"""
jflove-server Docker 镜像构建脚本

按 .claude/commands/devops.md 约定：
  - 后端发布产物为 docker 镜像（仅本地构建，用户自行 push 到公共仓库）
  - 镜像支持挂载数据库，也支持镜像内置空表结构 DB 启动
  - 构建前自检：jflove-prod.db 必须为空（仅有表结构）→ 防止业务数据泄漏

用法：
    python build.py                       # 构建并打 tag jflove-server:1.0.0 / latest
    python build.py --no-cache            # 不使用 docker 缓存
    python build.py --tag 1.0.0-rc1       # 自定义 tag

输出：
    构建产物 tag：jflove-server:<version>、jflove-server:latest
    可选：build/jflove-server-<version>.tar （docker save 离线包，用 --save 触发）
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

VERSION = "1.4.1"
IMAGE_NAME = "jflove-server"

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
PROD_DB_SOURCE = PROJECT_ROOT / "jflove-db" / "jflove-prod.db"
DOCKER_BUILD_CTX_DB_DIR = ROOT / "db"
BUILD_DIR = ROOT / "build"

# 版本号定义位置（发布时必须全部一致）：
#   - src/main.py        FastAPI(version="...")      —— 运行时 /docs 显示
#   - build.py           VERSION = "..."              —— 本脚本（镜像 tag 默认值）
#   - Dockerfile         LABEL version="..."          —— 镜像元数据
VERSION_FILES = [
    (ROOT / "src" / "main.py", r'version="([^"]+)"'),
    (ROOT / "build.py", r'^VERSION = "([^"]+)"', re.MULTILINE),
    (ROOT / "Dockerfile", r'LABEL version="([^"]+)"'),
]


def _read_version(path: Path, pattern: str, flags: int = 0) -> str | None:
    """读取指定文件中当前版本号，未匹配返回 None"""
    text = path.read_text(encoding="utf-8")
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


def assert_version_consistent() -> None:
    """发布阻塞项：校验全部版本号位置与 build.py VERSION 一致，不一致直接失败"""
    for path, pattern, *flags in VERSION_FILES:
        flags = flags[0] if flags else 0
        found = _read_version(path, pattern, flags)
        rel = path.relative_to(ROOT)
        if found is None:
            fail(f"{rel} 中未找到版本号定义，无法校验")
        if found != VERSION:
            fail(
                f"版本不一致：{rel} = {found}，build.py VERSION = {VERSION}\n"
                f"请用 `python build.py --version {VERSION}` 一键同步，或手动统一后再构建。"
            )
    log(f"版本一致性校验通过：全部版本号 = v{VERSION}")


def sync_version(new_version: str) -> None:
    """把新版本号同步写入全部版本号位置（含 build.py 自身）"""
    if not re.fullmatch(r"\d+\.\d+\.\d+", new_version):
        fail(f"非法版本号：{new_version}（要求形如 x.y.z）")
    # build.py 自身
    self_path = ROOT / "build.py"
    self_text = self_path.read_text(encoding="utf-8")
    self_text = re.sub(r'^VERSION = "[^"]+"', f'VERSION = "{new_version}"', self_text, count=1, flags=re.MULTILINE)
    self_path.write_text(self_text, encoding="utf-8")
    # src/main.py
    main_path = ROOT / "src" / "main.py"
    main_text = main_path.read_text(encoding="utf-8")
    main_text = re.sub(r'version="[^"]+"', f'version="{new_version}"', main_text, count=1)
    main_path.write_text(main_text, encoding="utf-8")
    # Dockerfile
    docker_path = ROOT / "Dockerfile"
    docker_text = docker_path.read_text(encoding="utf-8")
    docker_text = re.sub(r'LABEL version="[^"]+"', f'LABEL version="{new_version}"', docker_text, count=1)
    docker_path.write_text(docker_text, encoding="utf-8")
    log(f"版本号已同步：v{new_version}（main.py / build.py / Dockerfile）")


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[build][FATAL] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def assert_prod_db_empty() -> None:
    """安全宪法：发布前必须确认 prod DB 不含业务数据"""
    if not PROD_DB_SOURCE.exists():
        fail(f"找不到生产数据库：{PROD_DB_SOURCE}")

    conn = sqlite3.connect(str(PROD_DB_SOURCE))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall() if r[0] != "sqlite_sequence"]
        if not tables:
            fail("prod DB 没有任何业务表，构建终止")
        offending = []
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            cnt = cur.fetchone()[0]
            if cnt > 0:
                offending.append((t, cnt))
        if offending:
            fail(f"prod DB 包含业务数据，禁止打入镜像：{offending}")
        log(f"prod DB 表结构校验通过，{len(tables)} 张表全部为 0 行")
    finally:
        conn.close()


def stage_prod_db() -> None:
    """把 prod DB 拷贝到 docker build context（jflove-server/db/）"""
    DOCKER_BUILD_CTX_DB_DIR.mkdir(parents=True, exist_ok=True)
    target = DOCKER_BUILD_CTX_DB_DIR / "jflove-prod.db"
    shutil.copyfile(PROD_DB_SOURCE, target)
    log(f"已暂存空 prod DB 到 {target.relative_to(ROOT)}")


def cleanup_stage() -> None:
    """构建完成后清理临时 db/ 目录，避免污染源码树"""
    if DOCKER_BUILD_CTX_DB_DIR.exists():
        shutil.rmtree(DOCKER_BUILD_CTX_DB_DIR)
        log("已清理构建上下文中的 db/ 临时目录")


def docker_build(tag: str, no_cache: bool) -> None:
    cmd = ["docker", "build", "-t", f"{IMAGE_NAME}:{tag}", "-t", f"{IMAGE_NAME}:latest"]
    if no_cache:
        cmd.append("--no-cache")
    cmd.append(".")
    log(f"执行: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        fail(f"docker build 失败，退出码 {proc.returncode}")
    log(f"镜像构建成功：{IMAGE_NAME}:{tag} / {IMAGE_NAME}:latest")


def docker_save(tag: str) -> Path:
    """docker save 成 tar 文件，便于离线分发"""
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    tar_path = BUILD_DIR / f"{IMAGE_NAME}-{tag}.tar"
    cmd = ["docker", "save", "-o", str(tar_path), f"{IMAGE_NAME}:{tag}"]
    log(f"执行: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        fail(f"docker save 失败，退出码 {proc.returncode}")
    log(f"镜像已导出: {tar_path}")
    return tar_path


def file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=VERSION, help="版本号（默认 build.py VERSION；指定新值会自动同步全部版本号位置）")
    parser.add_argument("--tag", default=None, help="镜像 tag（默认与版本号一致）")
    parser.add_argument("--no-cache", action="store_true", help="docker build 不使用缓存")
    parser.add_argument("--save", action="store_true", help="构建后 docker save 成 tar 文件")
    args = parser.parse_args()

    if args.version != VERSION:
        sync_version(args.version)      # 显式指定新版本号 → 自动同步全部位置
    else:
        assert_version_consistent()     # 未指定 → 校验全部位置一致（不一致中止）
    tag = args.tag or args.version

    log(f"=== jflove-server v{tag} 构建开始 ===")
    assert_prod_db_empty()
    stage_prod_db()
    try:
        docker_build(tag, args.no_cache)
        if args.save:
            tar = docker_save(tag)
            log(f"SHA256: {file_sha256(tar)}")
    finally:
        cleanup_stage()
    log(f"=== 构建完成。运行示例：docker run -d -p 8989:8989 -v /your/data:/data --restart=always {IMAGE_NAME}:{tag} ===")


if __name__ == "__main__":
    main()

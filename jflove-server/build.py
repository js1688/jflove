"""
jflove-server Docker 镜像构建脚本

按 .claude/skills/devops/SKILL.md 约定：
  - 后端发布产物为 docker 镜像（本地或 GitHub Actions 均可构建）
  - 版本号单一来源：仓库根 version.json（本脚本不再硬编码版本号）
  - 镜像支持挂载数据库，也支持镜像内置空表结构 DB 启动
  - 构建前自检：jflove-prod.db 必须为空（仅有表结构）→ 防止业务数据泄漏

用法：
    python build.py                       # 构建并打 tag jflove-server:<version> / latest
    python build.py --no-cache            # 不使用 docker 缓存
    python build.py --tag 1.0.0-rc1       # 自定义 tag（默认与 version.json 版本一致）

版本号管理：
    版本号只读仓库根 version.json。改版本请用 `python scripts/sync_version.py`，
    本脚本构建前校验模块内版本号（main.py / Dockerfile）与 version.json 一致。

输出：
    构建产物 tag：jflove-server:<version>、jflove-server:latest
    可选：build/jflove-server-<version>.tar （docker save 离线包，用 --save 触发）
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

# 引入仓库根的 scripts/sync_version.py，复用「版本号单一来源」逻辑
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import sync_version  # noqa: E402

IMAGE_NAME = "jflove-server"

PROD_DB_SOURCE = PROJECT_ROOT / "jflove-db" / "jflove-prod.db"
DOCKER_BUILD_CTX_DB_DIR = ROOT / "db"
BUILD_DIR = ROOT / "build"

#: 允许出现在生产库里的**初始化数据**（AGENTS.md §5.1：prod 库"不同步业务数据
#: （初始化数据除外）"）。
#:
#: `init_db()` 首次运行会幂等写入 media_repair_* 两个默认配置键（值均为 0 = 关闭）。
#: 它们不是业务数据，而是让服务端有一份默认配置；若不放行，只要启动过一次服务端，
#: 生产库就不再是"全 0 行"，打包会被误拦。
#:
#: ⚠ 白名单必须**精确到键名 + 默认值**，且只对 config 表生效：这样任何真实的
#: 业务数据（用户、磁盘、权限、会话、修复任务，或管理员改过的配置值）仍会被拦下，
#: 数据泄漏防线不降低。
_INITIAL_CONFIG_ROWS: dict[str, str] = {
    "media_repair_enabled": "0",
    "media_repair_allow_transcode": "0",
}


def assert_version_consistent() -> None:
    """发布阻塞项：校验模块内版本号与 version.json 一致，不一致直接失败"""
    issues = sync_version.check_consistency("server")
    if issues:
        fail(
            "版本不一致：\n"
            + "\n".join(f"  - {i}" for i in issues)
            + f"\n请先运行 `python scripts/sync_version.py` 同步到 v{sync_version.load_version()}。"
        )
    log(f"版本一致性校验通过：全部版本号 = v{sync_version.load_version()}")


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[build][FATAL] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def assert_prod_db_empty() -> None:
    """
    安全宪法：发布前必须确认 prod DB 不含**业务数据**。

    放行条件（AGENTS.md §5.1）：`config` 表里仅存在 `init_db()` 写入的默认配置键
    （见 `_INITIAL_CONFIG_ROWS`，值必须等于默认值）。其余任何表非空、或 config
    出现白名单外的键/被改过的值，都视为业务数据泄漏并中止构建。
    """
    if not PROD_DB_SOURCE.exists():
        fail(f"找不到生产数据库：{PROD_DB_SOURCE}")

    conn = sqlite3.connect(str(PROD_DB_SOURCE))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall() if r[0] != "sqlite_sequence"]
        if not tables:
            fail("prod DB 没有任何业务表，构建终止")

        offending: list[tuple[str, int]] = []
        initial_rows: list[str] = []

        for t in tables:
            if t == "config":
                continue  # config 单独按白名单校验
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            cnt = cur.fetchone()[0]
            if cnt > 0:
                offending.append((t, cnt))

        # config 表：逐行核对是否为"默认初始化键 + 默认值"
        if "config" in tables:
            cur.execute("SELECT key, value FROM config WHERE deleted_at IS NULL")
            for key, value in cur.fetchall():
                expected = _INITIAL_CONFIG_ROWS.get(key)
                if expected is not None and str(value) == expected:
                    initial_rows.append(f"{key}={value}")
                else:
                    offending.append(("config", f"{key}={value}"))

        if offending:
            fail(
                "prod DB 包含业务数据，禁止打入镜像："
                f"{offending}\n"
                "  提示：仅允许 config 表存在 init_db() 写入的默认键"
                f"（{_INITIAL_CONFIG_ROWS}）；"
                "其余表必须 0 行。请清理生产库后重试。"
            )

        extra = f"，初始化配置 {initial_rows}" if initial_rows else ""
        log(f"prod DB 表结构校验通过，{len(tables)} 张表均无业务数据{extra}")
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
    parser.add_argument("--tag", default=None, help="镜像 tag（默认与 version.json 版本一致）")
    parser.add_argument("--no-cache", action="store_true", help="docker build 不使用缓存")
    parser.add_argument("--save", action="store_true", help="构建后 docker save 成 tar 文件")
    args = parser.parse_args()

    assert_version_consistent()  # 校验模块内版本号 == version.json
    version = sync_version.load_version()
    tag = args.tag or version

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
    log(
        "=== 构建完成。运行示例："
        f"docker run -d -p 8989:8989 -v /your/data:/data --restart=always "
        f"{IMAGE_NAME}:{tag} ==="
    )


if __name__ == "__main__":
    main()

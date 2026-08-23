"""
jflove-web Docker 镜像构建脚本

按 .claude/skills/devops/SKILL.md 约定：
  - Web 端发布产物为 docker 镜像（仅本地构建，用户自行 push 到镜像仓库）
  - Docker 多阶段构建：Stage 1 node build（npm ci + npm run build）→ Stage 2 nginx serve
  - 构建前自检：package-lock.json 必须存在（保证 npm ci 可复现）

用法：
    python build.py                       # 构建并打 tag jflove-web:1.3.0
    python build.py --no-cache            # 不使用 docker 缓存
    python build.py --tag 1.3.0-rc1       # 自定义 tag
    python build.py --save                # 构建后 docker save 成 tar 离线包

输出：
    构建产物 tag：jflove-web:<version>
    可选：build/jflove-web-<version>.tar （docker save 离线包，用 --save 触发）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

VERSION = "1.4.1"
IMAGE_NAME = "jflove-web"

ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
PACKAGE_LOCK = ROOT / "package-lock.json"
PACKAGE_JSON = ROOT / "package.json"
CONSTANTS_TS = ROOT / "src" / "config" / "constants.ts"
DOCKERFILE = ROOT / "Dockerfile"
NGINX_CONF = ROOT / "nginx.conf"

# 版本号定义位置（发布时必须全部一致）：
#   - package.json                "version": "..."      —— npm 元数据
#   - src/config/constants.ts     APP_VERSION = '...'    —— 运行时设置页「关于」显示
#   - build.py                    VERSION = "..."        —— 本脚本（镜像 tag 默认值）
VERSION_FILES = [
    (PACKAGE_JSON, r'"version"\s*:\s*"([^"]+)"'),
    (CONSTANTS_TS, r"APP_VERSION\s*=\s*'([^']+)'"),
    (ROOT / "build.py", r'^VERSION = "([^"]+)"', re.MULTILINE),
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
    # package.json
    pkg = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    pkg["version"] = new_version
    PACKAGE_JSON.write_text(
        json.dumps(pkg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # src/config/constants.ts 的 APP_VERSION
    constants_text = CONSTANTS_TS.read_text(encoding="utf-8")
    constants_text = re.sub(
        r"APP_VERSION\s*=\s*'[^']+'", f"APP_VERSION = '{new_version}'", constants_text, count=1
    )
    CONSTANTS_TS.write_text(constants_text, encoding="utf-8")
    # build.py 自身
    self_path = ROOT / "build.py"
    self_text = self_path.read_text(encoding="utf-8")
    self_text = re.sub(
        r'^VERSION = "[^"]+"', f'VERSION = "{new_version}"', self_text, count=1, flags=re.MULTILINE
    )
    self_path.write_text(self_text, encoding="utf-8")
    log(f"版本号已同步：v{new_version}（package.json / constants.ts / build.py）")


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[build][FATAL] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def assert_build_prereqs() -> None:
    """构建前置自检：保证 Docker 多阶段构建可复现"""
    if not PACKAGE_LOCK.exists():
        fail("缺少 package-lock.json，请先执行 `npm install` 生成 lock 文件")
    if not DOCKERFILE.exists():
        fail(f"缺少 Dockerfile：{DOCKERFILE}")
    if not NGINX_CONF.exists():
        fail(f"缺少 nginx.conf：{NGINX_CONF}")
    log("构建前置自检通过（package-lock.json / Dockerfile / nginx.conf 就绪）")


def docker_build(tag: str, no_cache: bool) -> None:
    """本地构建 docker 镜像（不推送，由用户自行 push 到镜像仓库）"""
    cmd = ["docker", "build", "-t", f"{IMAGE_NAME}:{tag}"]
    if no_cache:
        cmd.append("--no-cache")
    cmd.append(".")
    log(f"执行: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        fail(f"docker build 失败，退出码 {proc.returncode}")
    log(f"镜像构建成功：{IMAGE_NAME}:{tag}")


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
    parser.add_argument("--version", default=VERSION, help="版本号（默认 build.py VERSION；指定新值会自动同步 package.json / constants.ts）")
    parser.add_argument("--tag", default=None, help="镜像 tag（默认与版本号一致）")
    parser.add_argument("--no-cache", action="store_true", help="docker build 不使用缓存")
    parser.add_argument("--save", action="store_true", help="构建后 docker save 成 tar 文件")
    args = parser.parse_args()

    if args.version != VERSION:
        sync_version(args.version)      # 显式指定新版本号 → 自动同步全部位置
    else:
        assert_version_consistent()     # 未指定 → 校验全部位置一致（不一致中止）
    tag = args.tag or args.version

    log(f"=== jflove-web v{tag} 构建开始 ===")
    assert_build_prereqs()
    docker_build(tag, args.no_cache)
    if args.save:
        tar = docker_save(tag)
        log(f"SHA256: {file_sha256(tar)}")
    log(f"=== 构建完成。运行示例：docker run -p 8080:80 {IMAGE_NAME}:{tag} ===")


if __name__ == "__main__":
    main()

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
import subprocess
import sys
from pathlib import Path

VERSION = "1.3.0"
IMAGE_NAME = "jflove-web"

ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
PACKAGE_LOCK = ROOT / "package-lock.json"
DOCKERFILE = ROOT / "Dockerfile"
NGINX_CONF = ROOT / "nginx.conf"


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
    parser.add_argument("--tag", default=VERSION, help="镜像 tag（默认与 VERSION 一致）")
    parser.add_argument("--no-cache", action="store_true", help="docker build 不使用缓存")
    parser.add_argument("--save", action="store_true", help="构建后 docker save 成 tar 文件")
    args = parser.parse_args()

    log(f"=== jflove-web v{args.tag} 构建开始 ===")
    assert_build_prereqs()
    docker_build(args.tag, args.no_cache)
    if args.save:
        tar = docker_save(args.tag)
        log(f"SHA256: {file_sha256(tar)}")
    log(f"=== 构建完成。运行示例：docker run -p 8080:80 {IMAGE_NAME}:{args.tag} ===")


if __name__ == "__main__":
    main()

"""
jflove-desktop pyinstaller 构建脚本

按 .claude/skills/devops/SKILL.md 约定：
  - 跨平台桌面端使用 pyinstaller 打包
  - 产物输出到 jflove-desktop/build/
  - 安装包不带任何源 session_key（运行时随密钥交换动态生成）
  - 版本号单一来源：仓库根 version.json（本脚本不再硬编码版本号）

平台支持：
  - Linux:   生成 ELF 单文件 build/dist/JFLove
  - Windows: 在 Windows 主机上跑同一脚本生成 build/dist/JFLove.exe（带 icon.ico）
  - macOS:   在 macOS 主机上跑同一脚本生成 build/dist/JFLove.app（建议手动 codesign）

用法：
    python build.py             # 默认 onefile + windowed
    python build.py --clean     # 构建前先 rm -rf build/

版本号管理：
    版本号只读仓库根 version.json。改版本请用 `python scripts/sync_version.py`，
    本脚本构建前校验模块内版本号（settings.py APP_VERSION）与 version.json 一致。
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

# 引入仓库根的 scripts/sync_version.py，复用「版本号单一来源」逻辑
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import sync_version  # noqa: E402

APP_NAME = "JFLove"

SRC_MAIN = ROOT / "src" / "main.py"
IMAGES_DIR = ROOT / "images"
BUILD_DIR = ROOT / "build"
DIST_DIR = BUILD_DIR / "dist"
WORK_DIR = BUILD_DIR / "pyinstaller_work"
SPEC_DIR = BUILD_DIR / "spec"


def assert_version_consistent() -> None:
    """发布阻塞项：校验模块内版本号与 version.json 一致，不一致直接失败"""
    issues = sync_version.check_consistency("desktop")
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


def assert_no_secrets() -> None:
    """安全宪法：发布安装包不允许携带任何源 session_key / 写死密钥"""
    forbidden_substrings = [
        "session_key = b\"",
        "SESSION_KEY = b\"",
    ]
    for py in (ROOT / "src").rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for needle in forbidden_substrings:
            if needle in text:
                fail(f"{py.relative_to(ROOT)} 中发现疑似硬编码 session_key")
    log("源代码扫描：未发现硬编码 session_key [OK]")


def file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_pyinstaller(clean: bool) -> None:
    if clean and BUILD_DIR.exists():
        log(f"清理 {BUILD_DIR}")
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    sep = ";" if platform.system() == "Windows" else ":"
    icon_path = (IMAGES_DIR / "icon.ico") if platform.system() == "Windows" else (IMAGES_DIR / "icon.png")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--distpath", str(DIST_DIR),
        "--workpath", str(WORK_DIR),
        "--specpath", str(SPEC_DIR),
        "--add-data", f"{IMAGES_DIR}{sep}images",
        "--icon", str(icon_path),
        # 防 PySide6 漏掉子模块
        "--collect-submodules", "PySide6",
        "--collect-submodules", "qfluentwidgets",
        "--collect-data", "qfluentwidgets",
        str(SRC_MAIN),
    ]
    log("执行 pyinstaller: " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        fail(f"pyinstaller 失败，退出码 {proc.returncode}")


def report_artifacts() -> None:
    if not DIST_DIR.exists():
        fail("构建产物目录不存在")
    log("=== 产物清单 ===")
    for p in sorted(DIST_DIR.rglob("*")):
        if p.is_file():
            size_mb = p.stat().st_size / (1024 * 1024)
            log(f"  {p.relative_to(ROOT)}  {size_mb:.1f} MB  sha256={file_sha256(p)[:16]}...")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true", help="构建前清空 build/")
    args = parser.parse_args()

    assert_version_consistent()  # 校验模块内版本号 == version.json
    version = sync_version.load_version()

    log(f"=== jflove-desktop v{version} 构建开始（平台={platform.system()} {platform.machine()}）===")
    assert_no_secrets()
    run_pyinstaller(args.clean)
    report_artifacts()
    log("=== 构建完成 ===")


if __name__ == "__main__":
    main()

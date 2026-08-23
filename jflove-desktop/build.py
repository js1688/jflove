"""
jflove-desktop pyinstaller 构建脚本

按 .claude/commands/devops.md 约定：
  - 跨平台桌面端使用 pyinstaller 打包
  - 产物输出到 jflove-desktop/build/
  - 安装包不带任何源 session_key（运行时随密钥交换动态生成）

平台支持：
  - Linux:   生成 ELF 单文件 build/dist/JFLove
  - Windows: 在 Windows 主机上跑同一脚本生成 build/dist/JFLove.exe（带 icon.ico）
  - macOS:   在 macOS 主机上跑同一脚本生成 build/dist/JFLove.app（建议手动 codesign）

用法：
    python build.py             # 默认 onefile + windowed
    python build.py --clean     # 构建前先 rm -rf build/
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

VERSION = "1.4.1"
APP_NAME = "JFLove"

ROOT = Path(__file__).resolve().parent
SRC_MAIN = ROOT / "src" / "main.py"
IMAGES_DIR = ROOT / "images"
BUILD_DIR = ROOT / "build"
DIST_DIR = BUILD_DIR / "dist"
WORK_DIR = BUILD_DIR / "pyinstaller_work"
SPEC_DIR = BUILD_DIR / "spec"

# 版本号定义位置（发布时必须全部一致）：
#   - src/config/settings.py   APP_VERSION = "..."   —— 运行时窗口标题/关于对话框显示
#   - build.py                 VERSION = "..."       —— 本脚本
VERSION_FILES = [
    (ROOT / "src" / "config" / "settings.py", r'APP_VERSION = "([^"]+)"'),
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
    # src/config/settings.py 的 APP_VERSION
    settings_path = ROOT / "src" / "config" / "settings.py"
    settings_text = settings_path.read_text(encoding="utf-8")
    settings_text = re.sub(
        r'APP_VERSION = "[^"]+"', f'APP_VERSION = "{new_version}"', settings_text, count=1
    )
    settings_path.write_text(settings_text, encoding="utf-8")
    # build.py 自身
    self_path = ROOT / "build.py"
    self_text = self_path.read_text(encoding="utf-8")
    self_text = re.sub(
        r'^VERSION = "[^"]+"', f'VERSION = "{new_version}"', self_text, count=1, flags=re.MULTILINE
    )
    self_path.write_text(self_text, encoding="utf-8")
    log(f"版本号已同步：v{new_version}（settings.py APP_VERSION / build.py VERSION）")


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
    parser.add_argument("--version", default=VERSION, help="版本号（默认 build.py VERSION；指定新值会自动同步 settings.py APP_VERSION）")
    parser.add_argument("--clean", action="store_true", help="构建前清空 build/")
    args = parser.parse_args()

    if args.version != VERSION:
        sync_version(args.version)      # 显式指定新版本号 → 自动同步全部位置
    else:
        assert_version_consistent()     # 未指定 → 校验全部位置一致（不一致中止）
    version = args.version

    log(f"=== jflove-desktop v{version} 构建开始（平台={platform.system()} {platform.machine()}）===")
    assert_no_secrets()
    run_pyinstaller(args.clean)
    report_artifacts()
    log("=== 构建完成 ===")


if __name__ == "__main__":
    main()

"""
jflove-desktop PyInstaller 构建脚本

按 .claude/skills/devops/SKILL.md 约定：
  - 跨平台桌面端使用 pyinstaller 打包
  - 产物输出到 jflove-desktop/build/
  - 安装包不带任何源 session_key（运行时随密钥交换动态生成）
  - 版本号单一来源：仓库根 version.json（本脚本不再硬编码版本号）

产物形态（**标准 = 安装包**；用户 2026-07-14 定：任何触发路径都以安装包为准）：
  - Windows: JFLove-<ver>-win64-setup.exe（Inno Setup 安装包；需 iscc，缺失则**降级**跳过并给指引）
             + JFLove-<ver>-win64-portable.zip（便携绿色版）
             + build/dist/JFLove/（onedir 目录，安装包的载荷）
  - Linux:   jflove-desktop-<ver>-1.fc<N>.x86_64.rpm（**默认开**；需 rpmbuild，缺失则降级跳过）
             + build/dist/JFLove/（onedir 目录，见 packaging/linux/build_rpm.sh）
  - macOS:   在 macOS 主机上跑同一脚本生成 .app（建议手动 codesign）
  - --mode onefile 仅为**兼容保留的显式选项**：单体文件 build/dist/JFLove[.exe]，
    **不是交付形态**（启动慢、且无法配安装包）

为什么用 onedir：
  onefile 每次启动都要把数百 MB 的 bundle（PySide6 + Qt）解压到 %TEMP%\\_MEIxxxx，
  启动慢的主因就是这个解压；onedir 直接映射文件、不解压，启动显著更快。
  代价是产物由「1 个文件」变成「1 个目录」，因此配安装包落地为「正常安装模式」。

用法：
    python build.py                     # 标准形态：Windows 出安装包+zip；Linux 出 RPM
    python build.py --no-installer      # 不出 Windows 安装包
    python build.py --no-zip            # 不出便携 zip
    python build.py --no-rpm            # 不出 RPM（Linux）
    python build.py --installer         # 显式要求安装包（onedir+Windows 默认已开）
    python build.py --zip               # 显式要求便携 zip
    python build.py --rpm               # 显式要求 RPM（Linux+onedir 默认已开）
    python build.py --rpm --fedora 43   # 指定 Fedora 基线版本（默认 43）
    python build.py --mode onefile      # 旧行为：单体文件（**不推荐**，仅兼容用）
    python build.py --clean             # 构建前清空 build/

版本号管理：
    版本号只读仓库根 version.json。改版本请用 `python scripts/sync_version.py`，
    本脚本构建前校验模块内版本号（settings.py APP_VERSION）与 version.json 一致。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

# 引入仓库根的 scripts/sync_version.py，复用「版本号单一来源」逻辑
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import sync_version  # noqa: E402

APP_NAME = "JFLove"

SRC_MAIN = ROOT / "src" / "main.py"
IMAGES_DIR = ROOT / "images"
#: 前端工程（v1.5.0 起桌面端界面由它构建产出）
UI_DIR = ROOT / "ui"
#: 前端产物：打包时放进解包目录的 `webui/`，与 web_shell.resolve_web_root() 约定一致
UI_DIST_DIR = UI_DIR / "dist"
BUILD_DIR = ROOT / "build"
DIST_DIR = BUILD_DIR / "dist"
WORK_DIR = BUILD_DIR / "pyinstaller_work"
SPEC_DIR = BUILD_DIR / "spec"

#: 打包资产（安装包脚本 / Linux RPM 全套）
PACKAGING_DIR = ROOT / "packaging"
WINDOWS_PACKAGING_DIR = PACKAGING_DIR / "windows"
LINUX_PACKAGING_DIR = PACKAGING_DIR / "linux"
INSTALLER_SCRIPT = WINDOWS_PACKAGING_DIR / "jflove.iss"
RPM_SCRIPT = LINUX_PACKAGING_DIR / "build_rpm.sh"

#: RPM 默认 Fedora 基线：在「仍受支持」里取较旧的一个 → glibc 要求更低、兼容面更宽，
#: 同时在更新的 Fedora 上也能正常安装。
DEFAULT_FEDORA = 43


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def fail(msg: str, code: int = 1) -> None:
    print(f"[build][FATAL] {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


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


# ── 产物定位 ─────────────────────────────────────────────────────
def _exe_name(name: str | None = None) -> str:
    """主程序文件名（Windows 带 .exe，其余平台无后缀）"""
    return (name or APP_NAME) + (".exe" if platform.system() == "Windows" else "")


def _dist_suffix() -> str:
    """产物文件名里的平台标识"""
    system = platform.system()
    return {"Windows": "win64", "Linux": "linux64", "Darwin": "macos"}.get(
        system, system.lower() or "unknown"
    )


def _onedir_path(name: str | None = None) -> Path:
    return DIST_DIR / (name or APP_NAME)


def _onefile_path(name: str | None = None) -> Path:
    return DIST_DIR / _exe_name(name)


def _rel(path: Path) -> str:
    """尽量打印相对仓库根的路径（便于阅读）；不在仓库内时退回绝对路径"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _is_locked(path: Path) -> bool:
    """目标文件是否被占用（Windows 上运行中的程序会锁住自身文件）"""
    if not path.exists():
        return False
    try:
        with open(path, "r+b"):
            return False
    except OSError:
        return True


def prepare_output_name(mode: str) -> None:
    """
    目标产物被运行中的程序占用时，整体改名输出（JFLove → JFLove-new）。

    - onefile：占用的是 dist/JFLove.exe
    - onedir： 占用的是 dist/JFLove/JFLove.exe —— 目录内文件被锁住时 PyInstaller
               无法清空该目录，因此这里改成**整个目录**改名

    用户往往正开着旧版本做验收，不该被迫先关掉才能打包。
    """
    global APP_NAME  # noqa: PLW0603 - 构建脚本，简单直接

    target = _onefile_path() if mode == "onefile" else _onedir_path() / _exe_name()
    if not _is_locked(target):
        return
    alt = f"{APP_NAME}-new"
    log(f"[warn] {_rel(target)} 正被运行中的程序占用，"
        f"产物改名输出为 {alt}{'（整目录）' if mode == 'onedir' else ''}")
    log("[warn] 关闭正在运行的应用后，可用同名产物替换使用")
    APP_NAME = alt


# ── Inno Setup 探测 ──────────────────────────────────────────────
def find_iscc() -> Path | None:
    """
    探测 Inno Setup 编译器 iscc.exe（真实探测，不写死假设）。

    查找顺序：
      1. 环境变量 ISCC / INNO_SETUP_ISCC（显式指定，便于自定义安装路径）
      2. PATH 上的 iscc / ISCC.exe
      3. 常见安装目录：Program Files / Program Files (x86) / 用户级安装目录

    :return: iscc 的完整路径；未安装返回 None（调用方负责降级，不视为失败）
    """
    env = os.environ.get("ISCC") or os.environ.get("INNO_SETUP_ISCC")
    if env and Path(env).is_file():
        return Path(env)

    for name in ("iscc", "ISCC", "iscc.exe", "ISCC.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)

    roots: list[Path] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(var)
        if value:
            roots.append(Path(value))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs")

    for root in roots:
        for sub in ("Inno Setup 6", "Inno Setup 5", "Inno Setup"):
            cand = root / sub / "ISCC.exe"
            if cand.is_file():
                return cand
    return None


def find_chinese_isl(iscc: Path) -> Path | None:
    """
    探测 Inno Setup 的简体中文语言文件（ChineseSimplified.isl）。

    Inno Setup 官方发行包**不含**中文语言文件（属社区翻译），所以中文化要
    「有则用、无则退回英文并提示」，而不是让整个构建失败。
    """
    cand = iscc.parent / "Languages" / "ChineseSimplified.isl"
    return cand if cand.is_file() else None


# ── PyInstaller ──────────────────────────────────────────────────


def ensure_mermaid_bundle() -> None:
    """
    确保前端工程里有 Mermaid 离线包。

    **为什么必须显式处理**：前端 `ui/src/utils/mermaid/loader.ts` 会
    **构建期 import** `public/vendor/mermaid.version.json`，所以缺了这两个文件
    `vite build` 会直接失败（全新 clone / CI 上就是这样）。

    离线包由仓库根 `scripts/build_mermaid_bundle.mjs` 产出到
    `jflove-web/public/vendor/`，**不被 git 跟踪**（三端共用同一份产物）。
    这里在缺失时从 web 端那份复制过来；两边都没有就响亮失败并给出命令。
    """
    vendor_dir = UI_DIR / "public" / "vendor"
    required = ("mermaid.bundle.js", "mermaid.version.json")
    if all((vendor_dir / name).exists() for name in required):
        return

    web_vendor = ROOT.parent / "jflove-web" / "public" / "vendor"
    missing = [n for n in required if not (web_vendor / n).exists()]
    if missing:
        fail(
            "缺少 Mermaid 离线包，且 web 端也没有可复制的产物："
            f"{', '.join(missing)}\n"
            "  请先执行：node scripts/build_mermaid_bundle.mjs（仓库根）"
        )

    vendor_dir.mkdir(parents=True, exist_ok=True)
    for name in required:
        shutil.copy2(web_vendor / name, vendor_dir / name)
    log(f"已从 web 端复制 Mermaid 离线包 → {vendor_dir}")


def build_frontend() -> None:
    """
    构建前端产物（`ui/dist`）。

    v1.5.0 起桌面端界面是 Web UI，**打包前必须先构建前端**，
    否则打出来的安装包会白屏（`resolve_web_root()` 找不到 index.html）。

    用 `npm ci`（有 lockfile，可复现）；lockfile 缺失时退回 `npm install`。
    找不到 npm 时直接失败并给出可执行的提示，而不是产出一个坏包。
    """
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        fail("找不到 npm：请先安装 Node.js（前端产物是界面本体，缺它打出来的包会白屏）")

    if not (UI_DIR / "package.json").exists():
        fail(f"前端工程不存在：{UI_DIR}")

    ensure_mermaid_bundle()

    log("构建前端（npm ci + build）…")
    install_cmd = ["ci"] if (UI_DIR / "package-lock.json").exists() else ["install"]
    for cmd_args in ([npm, *install_cmd], [npm, "run", "build"]):
        proc = subprocess.run(cmd_args, cwd=str(UI_DIR), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", shell=False)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-1500:]
            fail(f"前端构建失败（{' '.join(cmd_args[1:])}）：\n{tail}")

    index = UI_DIST_DIR / "index.html"
    if not index.exists():
        fail(f"前端构建后仍找不到 {index}（请检查 ui/vite.config.ts）")
    log(f"前端产物就绪：{index}")


def run_pyinstaller(clean: bool, mode: str) -> None:
    if clean and BUILD_DIR.exists():
        log(f"清理 {BUILD_DIR}")
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    sep = ";" if platform.system() == "Windows" else ":"
    icon_name = "icon.ico" if platform.system() == "Windows" else "icon.png"
    icon_path = IMAGES_DIR / icon_name

    prepare_output_name(mode)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir" if mode == "onedir" else "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--distpath", str(DIST_DIR),
        "--workpath", str(WORK_DIR),
        "--specpath", str(SPEC_DIR),
        "--add-data", f"{IMAGES_DIR}{sep}images",
        "--icon", str(icon_path),
        # 防 PySide6 漏掉子模块（Qt 的二进制与资源一并收集）
        "--collect-submodules", "PySide6",
        "--collect-binaries", "PySide6",
        "--collect-data", "PySide6",
        # v1.5.0：界面改为 Web UI，必须把前端产物打进包 —— 否则打包后白屏
        # （web_shell.resolve_web_root() 在冻结态从 `<解包目录>/webui` 取首页）
        "--add-data", f"{UI_DIST_DIR}{sep}webui",
        str(SRC_MAIN),
    ]
    log(f"执行 pyinstaller（{mode}）: " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        fail(f"pyinstaller 失败，退出码 {proc.returncode}")


# ── 便携 zip ─────────────────────────────────────────────────────
def _zip_add_dir(zf: zipfile.ZipFile, src: Path, arc_root: str) -> int:
    count = 0
    for p in sorted(src.rglob("*")):
        if p.is_file():
            zf.write(p, arcname=f"{arc_root}/{p.relative_to(src).as_posix()}")
            count += 1
    return count


def build_zip(mode: str, version: str) -> Path | None:
    """便携 zip（绿色免安装版）：onedir 打包整个目录，onefile 打包单体文件"""
    out = DIST_DIR / f"{APP_NAME}-{version}-{_dist_suffix()}-portable.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    log(f"生成便携 zip：{out.name}（压缩中，大包较慢，请稍候…）")
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            if mode == "onedir":
                src = _onedir_path()
                if not src.is_dir():
                    fail(f"未找到 onedir 产物目录：{src.relative_to(ROOT)}")
                count = _zip_add_dir(zf, src, APP_NAME)
            else:
                src_exe = _onefile_path()
                if not src_exe.is_file():
                    fail(f"未找到 onefile 产物：{src_exe.relative_to(ROOT)}")
                zf.write(src_exe, arcname=src_exe.name)
                count = 1
    except OSError as exc:
        fail(f"便携 zip 生成失败：{exc}")
    size_mb = out.stat().st_size / (1024 * 1024)
    log(f"便携 zip 完成：{count} 个文件 → {size_mb:.1f} MB")
    return out


# ── Windows 安装包（Inno Setup）──────────────────────────────────
def build_installer(version: str, mode: str) -> Path | None:
    """
    编译 Inno Setup 安装包（仅 Windows + onedir）。

    探测不到 iscc 时**不失败**：打印明确指引并跳过，便携 zip 照常产出
    （按计划 §0 的降级口径：装载不到 Inno Setup 也能出可用的绿色包）。
    版本号一律用 /DAppVersion 传入，.iss 内不硬编码版本号。
    """
    if platform.system() != "Windows":
        log(f"[skip] Inno Setup 安装包仅在 Windows 构建（当前平台={platform.system()}）")
        return None
    if mode != "onedir":
        log("[skip] 安装包只针对 onedir 产物（onefile 本身即单体绿色文件）")
        return None

    src = _onedir_path()
    if not src.is_dir():
        fail(f"未找到 onedir 产物目录：{src.relative_to(ROOT)}")
    if not INSTALLER_SCRIPT.is_file():
        fail(f"缺少安装包脚本：{INSTALLER_SCRIPT.relative_to(ROOT)}")

    iscc = find_iscc()
    if iscc is None:
        log("[warn] **************************************************************")
        log("[warn] 未检测到 Inno Setup 编译器 iscc ⇒ **本次没有产出安装包**")
        log("[warn] 安装包是桌面端的**标准交付形态**（用户 2026-07-14 定），"
            "当前只剩 onedir 目录 + 便携 zip —— 交付/验收前请补齐：")
        log("[warn]   ① 装 Inno Setup 6 → https://jrsoftware.org/isdl.php（默认路径即可；"
            "自定义路径可设环境变量 ISCC）")
        log("[warn]   ② 重跑：python build.py（Windows 上安装包默认就开）")
        log("[warn] **************************************************************")
        return None

    isl = find_chinese_isl(iscc)
    if isl is None:
        log("[warn] 未找到 Languages\\ChineseSimplified.isl —— Inno Setup 官方包不含简体中文"
            "语言文件（属社区翻译），本次安装向导使用英文界面")
        log("       把 ChineseSimplified.isl 放进 "
            f"{Path(iscc).parent / 'Languages'} 即可自动切中文界面")

    base = f"{APP_NAME}-{version}-win64-setup"
    out = DIST_DIR / f"{base}.exe"
    cmd = [
        str(iscc), "/Qp",
        f"/DAppVersion={version}",
        f"/DAppName={APP_NAME}",
        f"/DAppExeName={_exe_name()}",
        f"/DSourceDir={src}",
        f"/DOutputDir={DIST_DIR}",
        f"/DOutputBaseFilename={base}",
        f"/DIconFile={IMAGES_DIR / 'icon.ico'}",
        f"/DLicenseFile={PROJECT_ROOT / 'LICENSE'}",
        f"/DHasChineseIsl={1 if isl else 0}",
        str(INSTALLER_SCRIPT),
    ]
    log("执行 iscc: " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=WINDOWS_PACKAGING_DIR)
    if proc.returncode != 0:
        fail(f"iscc 编译失败，退出码 {proc.returncode}")
    if not out.is_file():
        fail(f"iscc 返回 0，但未找到预期产物 {out.relative_to(ROOT)}")
    log(f"安装包已生成：{out.relative_to(ROOT)}")
    return out


# ── Linux RPM（本机不可交叉编译）────────────────────────────────
def assert_rpm_available() -> None:
    """
    RPM 只能在 Linux 上构建：PyInstaller 不能交叉编译，ELF / RPM 必须在 Linux 生成。

    非 Linux 平台**绝不静默跳过**：打印两条正路并以非 0 退出。
    """
    if platform.system() == "Linux":
        return
    fail(
        f"--rpm 只在 Linux 主机上可用（当前平台={platform.system()} {platform.machine()}）。\n"
        "  PyInstaller 不能交叉编译：ELF / RPM 必须在 Linux 上生成。两条正路：\n"
        "    1) 在 Fedora 机器上跑：sh jflove-desktop/packaging/linux/build_rpm.sh"
        " [--fedora 43] [--container]\n"
        "    2) 交给 CI（GitHub Actions 的 Fedora 容器 job）构建\n"
        "  本次不产出任何 Linux 产物，也不做静默跳过。",
        code=2,
    )


def run_rpm(fedora: int, clean: bool) -> int:
    """Linux 上委托给 packaging/linux/build_rpm.sh（onedir 载荷 + rpmbuild）"""
    if not RPM_SCRIPT.is_file():
        fail(f"缺少 RPM 构建脚本：{RPM_SCRIPT.relative_to(ROOT)}")
    cmd = ["sh", str(RPM_SCRIPT), "--fedora", str(fedora)]
    if clean:
        cmd.append("--clean")
    log("执行 RPM 构建: " + " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT).returncode


# ── 产物清单 ─────────────────────────────────────────────────────
def report_artifacts(mode: str) -> None:
    if not DIST_DIR.exists():
        fail("构建产物目录不存在")
    log("=== 产物清单 ===")
    if mode == "onedir":
        src = _onedir_path()
        if src.is_dir():
            files = [p for p in src.rglob("*") if p.is_file()]
            total_mb = sum(p.stat().st_size for p in files) / (1024 * 1024)
            log(f"  {src.relative_to(ROOT)}{os.sep}  （onedir 目录：{len(files)} 个文件，"
                f"共 {total_mb:.1f} MB）")
            main_exe = src / _exe_name()
            if main_exe.is_file():
                size_mb = main_exe.stat().st_size / (1024 * 1024)
                log(f"    主程序 {main_exe.name}  {size_mb:.1f} MB  "
                    f"sha256={file_sha256(main_exe)[:16]}...")
    for p in sorted(DIST_DIR.iterdir()):
        if not p.is_file():
            continue
        size_mb = p.stat().st_size / (1024 * 1024)
        log(f"  {p.relative_to(ROOT)}  {size_mb:.1f} MB  sha256={file_sha256(p)[:16]}...")


def main() -> None:
    parser = argparse.ArgumentParser(description="jflove-desktop 打包脚本（默认 onedir + 安装包）")
    parser.add_argument("--mode", choices=("onedir", "onefile"), default="onedir",
                        help="产物形态：onedir（默认，启动快、配安装包）/ onefile（兼容旧单体文件）")
    parser.add_argument("--installer", action=argparse.BooleanOptionalAction, default=None,
                        help="生成 Windows 安装包（onedir+Windows 默认开；iscc 缺失则降级跳过）")
    parser.add_argument("--zip", action=argparse.BooleanOptionalAction, default=None,
                        help="生成便携 zip（onedir+Windows 默认开）")
    parser.add_argument("--rpm", action=argparse.BooleanOptionalAction, default=None,
                        help="生成 Fedora RPM（Linux + onedir 默认开；非 Linux 显式要求则报错退出；"
                             "缺 rpmbuild 时降级跳过并给指引）")
    parser.add_argument("--fedora", type=int, default=DEFAULT_FEDORA,
                        help=f"RPM 的 Fedora 基线版本号（默认 {DEFAULT_FEDORA}）")
    parser.add_argument("--clean", action="store_true", help="构建前清空 build/")
    args = parser.parse_args()

    system = platform.system()
    is_windows = system == "Windows"
    is_linux = system == "Linux"

    # ── 标准交付形态（用户 2026-07-14 定：**任何触发路径都以安装包为准**）──
    #   Windows → Inno Setup 安装包（+ 便携 zip）；Linux/Fedora → RPM。
    #   两者都在 onedir 形态下**默认开启**，不需要调用方记参数；缺工具链时**降级并打印指引**，
    #   不把一次正常构建变成失败（与 iscc 缺失时的处理对称）。
    default_extras = args.mode == "onedir" and is_windows
    installer = args.installer if args.installer is not None else default_extras
    make_zip = args.zip if args.zip is not None else default_extras

    # --rpm：Linux + onedir 默认开；非 Linux 只有**显式**要求才报错退出（绝不静默跳过）
    want_rpm = args.rpm if args.rpm is not None else (args.mode == "onedir" and is_linux)
    if want_rpm and not is_linux:
        assert_rpm_available()  # 打印两条正路并以非 0 退出
    if want_rpm and shutil.which("rpmbuild") is None:
        log("[warn] 未检测到 rpmbuild —— RPM 是 Linux 端**标准交付形态**，本次降级跳过")
        log("       装好后重跑：sudo dnf install -y rpm-build && python build.py")
        want_rpm = False

    assert_version_consistent()  # 校验模块内版本号 == version.json
    version = sync_version.load_version()

    log(f"=== jflove-desktop v{version} 构建开始"
        f"（平台={system} {platform.machine()}，形态={args.mode}）===")
    assert_no_secrets()

    if want_rpm:
        # Linux：RPM 是交付形态；onedir 载荷由 build_rpm.sh 内部再调本脚本产出
        rc = run_rpm(args.fedora, args.clean)
        if rc != 0:
            fail(f"RPM 构建失败，退出码 {rc}")
        report_artifacts(args.mode)
        log("=== 构建完成 ===")
        return

    build_frontend()
    run_pyinstaller(args.clean, args.mode)
    if make_zip:
        build_zip(args.mode, version)
    if installer:
        build_installer(version, args.mode)
    report_artifacts(args.mode)
    log("=== 构建完成 ===")


if __name__ == "__main__":
    main()

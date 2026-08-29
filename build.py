"""
JFLove 统一打包入口（顶层编排器）

职责：读版本 → 自动同步 → 选模块 → 逐模块环境检查 → 按需切 venv → 依次打包 → 汇总。
与各模块 build.py 不同，本脚本是编排器，负责调度各模块构建。

用法：
    python build.py                          # 交互式选择模块（多选）
    python build.py -m server,desktop        # 参数指定模块
    python build.py -m all                   # 全部模块
    python build.py --no-sync                # 跳过版本号自动同步
    python build.py --skip-env-check         # 跳过环境检查强行构建

流程：
    1. 读 version.json（唯一版本真相）
    2. 自动同步版本号到全部 6 处（调 scripts/sync_version.py）
    3. 确定要打包的模块（交互多选 或 -m 参数）
    4. 逐模块环境检查：满足→入队；不满足→打印原因并跳过
    5. 依次打包（desktop 自动切到模块 venv）
    6. 汇总：成功 N / 跳过 M / 失败 K
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 引入 scripts/sync_version.py（版本号单一来源）
sys.path.insert(0, str(ROOT / "scripts"))
import sync_version  # noqa: E402


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[build][FATAL] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# ── 环境检查 ─────────────────────────────────────────────────────
def _has_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _venv_python(module_dir: str) -> Path | None:
    """按 AGENTS.md 约定定位模块 venv python（venv-win / venv-linux）"""
    if platform.system() == "Windows":
        p = ROOT / module_dir / "venv-win" / "Scripts" / "python.exe"
    else:
        p = ROOT / module_dir / "venv-linux" / "bin" / "python"
    return p if p.exists() else None


def check_server() -> tuple[bool, str]:
    """server 构建只需 docker（build.py 只调 docker build，标准库即可）"""
    if not _has_cmd("docker"):
        return False, "未检测到 docker（server 用 docker build 构建镜像）"
    return True, "docker 可用"


def check_desktop() -> tuple[bool, str]:
    """desktop 需要模块 venv（内含 PyInstaller / PySide6）"""
    py = _venv_python("jflove-desktop")
    if py is None:
        return False, "未找到 jflove-desktop 的 venv（venv-win / venv-linux）"
    proc = subprocess.run([str(py), "-c", "import PyInstaller"], capture_output=True)
    if proc.returncode != 0:
        return False, f"{py.relative_to(ROOT)} 中未安装 PyInstaller"
    return True, f"venv 就绪（{py.relative_to(ROOT)}）"


def check_web() -> tuple[bool, str]:
    """web 构建需 docker + package-lock.json"""
    if not _has_cmd("docker"):
        return False, "未检测到 docker（web 用 docker build 构建镜像）"
    if not (ROOT / "jflove-web" / "package-lock.json").exists():
        return False, "缺少 jflove-web/package-lock.json（先 npm install）"
    return True, "docker 可用 + package-lock.json 就绪"


def _flutter_exe() -> str | None:
    """定位 flutter：优先 FLUTTER_HOME（PATH 未更新也能用），回退 PATH"""
    fh = os.environ.get("FLUTTER_HOME")
    if fh:
        for name in ("flutter.bat", "flutter"):  # Windows 优先 .bat
            c = Path(fh) / "bin" / name
            if c.exists():
                return str(c)
    return shutil.which("flutter")


def check_app() -> tuple[bool, str]:
    """app 构建需 flutter（Android SDK 由 flutter doctor 兜底）"""
    exe = _flutter_exe()
    if exe is None:
        return False, "未检测到 flutter（请确认 FLUTTER_HOME 已设置或 bin 已加入 PATH）"
    return True, f"flutter 可用（{exe}）"


# ── 构建执行 ─────────────────────────────────────────────────────
def build_server() -> int:
    # server build.py 只调 docker + 标准库，用当前 python 即可
    return subprocess.run([sys.executable, "build.py"], cwd=ROOT / "jflove-server").returncode


def build_desktop() -> int:
    # desktop 必须用模块 venv（内含 PyInstaller）
    py = _venv_python("jflove-desktop")
    if py is None:
        log("desktop venv 丢失，无法构建")
        return 1
    return subprocess.run([str(py), "build.py"], cwd=ROOT / "jflove-desktop").returncode


def build_web() -> int:
    return subprocess.run([sys.executable, "build.py"], cwd=ROOT / "jflove-web").returncode


def build_app() -> int:
    # debug + release 两个 APK
    exe = _flutter_exe()
    if exe is None:
        log("未检测到 flutter，无法构建 app")
        return 1
    for args in (["build", "apk", "--debug"], ["build", "apk", "--release"]):
        if platform.system() == "Windows":
            # flutter 在 Windows 上是 .bat，需经 cmd.exe 执行
            rc = subprocess.run(["cmd", "/c", exe, *args], cwd=ROOT / "jflove-app").returncode
        else:
            rc = subprocess.run([exe, *args], cwd=ROOT / "jflove-app").returncode
        if rc != 0:
            return rc
    return 0


# ── 模块表 ───────────────────────────────────────────────────────
MODULES: dict[str, dict] = {
    "server": {"label": "jflove-server（Docker 镜像）", "check": check_server, "build": build_server},
    "desktop": {"label": "jflove-desktop（PyInstaller）", "check": check_desktop, "build": build_desktop},
    "web": {"label": "jflove-web（Docker 镜像）", "check": check_web, "build": build_web},
    "app": {"label": "jflove-app（APK debug+release）", "check": check_app, "build": build_app},
}


def parse_modules_arg(raw: str) -> list[str]:
    """解析 -m 参数：all / server,web（逗号分隔）"""
    keys = list(MODULES.keys())
    if raw.strip().lower() == "all":
        return keys
    selected = []
    for part in raw.split(","):
        k = part.strip()
        if k in MODULES and k not in selected:
            selected.append(k)
    if not selected:
        fail(f"未识别的模块：{raw}（可选：{', '.join(keys)} / all）")
    return selected


def prompt_modules() -> list[str]:
    """交互式选择模块（多选）"""
    keys = list(MODULES.keys())
    print("可打包模块：")
    for i, key in enumerate(keys, 1):
        print(f"  {i}. {key:<8} {MODULES[key]['label']}")
    try:
        raw = input("请输入要打包的模块编号（多选用逗号分隔，如 1,3；all=全部；回车=全部）：").strip()
    except EOFError:
        raw = ""
    if not raw or raw.lower() == "all":
        return keys
    selected = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(keys):
            k = keys[int(part) - 1]
            if k not in selected:
                selected.append(k)
        elif part in MODULES and part not in selected:
            selected.append(part)
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="JFLove 统一打包入口")
    parser.add_argument("-m", "--modules", default=None,
                        help="模块，逗号分隔：server,desktop,web,app（all=全部）")
    parser.add_argument("--no-sync", action="store_true", help="跳过版本号自动同步")
    parser.add_argument("--skip-env-check", action="store_true", help="跳过环境检查强行构建")
    args = parser.parse_args()

    # 1. 读版本 + 同步
    version = sync_version.load_version()
    log(f"=== JFLove 统一打包 v{version} ===")
    if args.no_sync:
        log("已跳过版本号同步（--no-sync）")
    else:
        changed = sync_version.sync_all()
        if changed:
            log(f"已自动同步版本号到 {len(changed)} 处：{', '.join(changed)}")
        else:
            log("版本号已一致，无需同步")

    # 2. 选模块
    if args.modules is not None:
        selected = parse_modules_arg(args.modules)
    else:
        selected = prompt_modules()
    if not selected:
        fail("未选择任何模块")

    # 3. 环境检查
    ok_list: list[str] = []
    skip_list: list[tuple[str, str]] = []
    for key in selected:
        m = MODULES[key]
        if args.skip_env_check:
            log(f"[env] {key}: 跳过环境检查（--skip-env-check）")
            ok_list.append(key)
            continue
        ok, reason = m["check"]()
        if ok:
            log(f"[env] {key}: OK —— {reason}")
            ok_list.append(key)
        else:
            log(f"[skip] {key}: 环境不满足 —— {reason}")
            skip_list.append((key, reason))

    if not ok_list:
        log("没有可打包的模块（全部环境不满足），退出")
        return

    # 4. 依次打包
    log(f"\n开始打包 {len(ok_list)} 个模块：{', '.join(ok_list)}")
    results: dict[str, int] = {}
    for key in ok_list:
        log(f"\n===== 打包 {key} =====")
        rc = MODULES[key]["build"]()
        results[key] = rc
        log(f"[{'ok' if rc == 0 else 'fail'}] {key} {'打包成功' if rc == 0 else f'打包失败（退出码 {rc}）'}")

    # 5. 汇总
    log("\n===== 汇总 =====")
    for key in selected:
        if key in [k for k, _ in skip_list]:
            reason = next(r for k, r in skip_list if k == key)
            log(f"  [skip] {key:<8} {MODULES[key]['label']} —— 环境不满足：{reason}")
        elif results.get(key) == 0:
            log(f"  [ok]   {key:<8} 成功")
        else:
            log(f"  [fail] {key:<8} 失败（退出码 {results.get(key)}）")

    if any(rc != 0 for rc in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()

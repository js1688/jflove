"""
跨平台中文输入法（IME）配置

各平台的 Qt 输入法工作机制：

- Linux Wayland
    Qt 6 内置 `wayland` IM 模块，直接通过 `text-input` 协议与 IM frontend
    对话。fcitx5、ibus 等主流 IM 在 Wayland 下都通过对应 frontend
    （fcitx5 的 `libwayland.so` / ibus 内置）实现该协议。**唯一可靠的输入法路径**。

- Linux X11（含 XWayland 的 xcb 平台）
    通过 `QT_IM_MODULE` 环境变量选择 IM 桥接插件 (fcitx / ibus)。
    PySide6 venv 自带 `libibusplatforminputcontextplugin.so`，**不带 fcitx 插件**；
    系统 `/usr/lib64/qt6/plugins/.../libfcitx5platforminputcontextplugin.so`
    **加载不了**（它依赖 `Qt_6.x_PRIVATE_API`，PySide6 的 Qt 不导出私有符号）。
    因此 X11 下实际可用的只有 ibus（且必须 ibus-daemon 在跑）。

⚠ **平台与 QT_IM_MODULE 必须匹配**：`QT_QPA_PLATFORM=xcb` 时 `QT_IM_MODULE=wayland`
毫无作用（反之亦然）。v1.7.0 之前的缺陷正是：main.py 强制 xcb，而本模块看到
`WAYLAND_DISPLAY` 就设成 `wayland` ⇒ 两者互斥 ⇒ 完全无法输入中文。

- Windows
    系统级 IME（PinYin / Microsoft IME / 各种第三方），Qt 自动桥接，无需任何
    环境变量配置。PyInstaller 打包后开箱即用。

- macOS
    系统级 IME（小企鹅 / 搜狗 / 自带），Qt 自动桥接，无需配置。本项目暂未
    在 macOS 上验证，但代码路径已预留。

策略：**先定平台，再按平台选 IM 模块**（见 `configure_linux_qt_platform`）；
不强行覆盖用户**已经显式可用**的设置。

调用要求：必须在 `QApplication` 创建之前调用，否则 Qt 已经读完环境变量：
  1. `configure_linux_qt_platform()` —— 决定 `QT_QPA_PLATFORM`
  2. `setup_input_method()` —— 按该平台设置 `QT_IM_MODULE`
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# 仅记录到 logger，不抛异常 —— 输入法配置是辅助功能，失败也要让程序起来
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _pyside_im_plugins() -> set[str]:
    """
    返回 PySide6 自带的 IM 插件名称集合（如 {"ibus", "compose"}）。

    用于判断 `QT_IM_MODULE=xxx` 是否能在当前 venv 中实际加载到。
    """
    try:
        import PySide6
    except ImportError:  # pragma: no cover
        return set()

    plugin_dir = (
        Path(PySide6.__file__).resolve().parent
        / "Qt" / "plugins" / "platforminputcontexts"
    )
    if not plugin_dir.is_dir():
        return set()

    names: set[str] = set()
    for f in plugin_dir.iterdir():
        # libibusplatforminputcontextplugin.so   → "ibus"
        # ibusplatforminputcontextplugin.dll     → "ibus"
        # libibusplatforminputcontextplugin.dylib→ "ibus"
        stem = f.stem
        if stem.startswith("lib"):
            stem = stem[3:]
        suffix = "platforminputcontextplugin"
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
        if stem:
            names.add(stem)
    return names


def _is_running(name: str) -> bool:
    """通过 pgrep 判断给定进程是否正在运行（Linux）"""
    if not shutil.which("pgrep"):
        return False
    try:
        return subprocess.run(
            ["pgrep", "-x", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).returncode == 0
    except Exception:
        return False


#: Wayland 输入法前端（fcitx5 的 wayland 插件）常见安装位置
_FCITX_WAYLAND_PLUGINS = (
    "/usr/lib64/fcitx5/libwayland.so",
    "/usr/lib/x86_64-linux-gnu/fcitx5/libwayland.so",
    "/usr/lib/fcitx5/libwayland.so",
)

#: 用户可用来覆盖平台选择的环境变量（xcb / wayland）
PLATFORM_OVERRIDE_ENV = "JFLOVE_QPA_PLATFORM"


def _is_wayland_session() -> bool:
    """当前是否是 Wayland 会话（以会话为准，不看 QT_QPA_PLATFORM）"""
    return bool(
        os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
    )


def _has_fcitx_wayland_frontend() -> bool:
    """fcitx5 是否具备 Wayland 前端（缺了它 Wayland 下同样没有输入法）"""
    return any(os.path.exists(p) for p in _FCITX_WAYLAND_PLUGINS)


def wayland_ime_available() -> bool:
    """
    当前会话是否存在可用的 Wayland 输入法前端。

    :returns: True 表示切换到 Wayland 平台后中文输入可用
    """
    if not _is_wayland_session():
        return False
    if _is_running("fcitx5") or _is_running("fcitx"):
        return _has_fcitx_wayland_frontend()
    if _is_running("ibus-daemon"):
        return True
    return False


def configure_linux_qt_platform() -> str | None:
    """
    选择 Linux 下的 Qt 平台插件（必须在 QApplication 创建之前调用）。

    规则：
      1. 用户已显式设置 `QT_QPA_PLATFORM` ⇒ 尊重，不动；
      2. `JFLOVE_QPA_PLATFORM` 覆盖（用于遇到 Wayland 显示问题时回退 `xcb`）；
      3. Wayland 会话且存在 Wayland 输入法前端 ⇒ `wayland`
         （只有这条路能拿到中文输入；xcb 下 PySide6 没有可用的 fcitx 插件）；
      4. 其余 ⇒ `xcb`（XWayland 下更稳）。

    :returns: 最终生效的 `QT_QPA_PLATFORM`（非 Linux 返回 None 表示不干预）
    """
    if not sys.platform.startswith("linux"):
        return None

    current = os.environ.get("QT_QPA_PLATFORM", "").strip()
    if current:
        logger.info("Qt 平台：尊重已设置 QT_QPA_PLATFORM=%s", current)
        return current

    override = os.environ.get(PLATFORM_OVERRIDE_ENV, "").strip()
    if override:
        os.environ["QT_QPA_PLATFORM"] = override
        logger.info("Qt 平台：按 %s=%s 覆盖", PLATFORM_OVERRIDE_ENV, override)
        return override

    if _is_wayland_session() and wayland_ime_available():
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        logger.info(
            "Qt 平台：Wayland 会话且检测到 Wayland 输入法前端，使用 wayland"
            "（中文输入依赖 text-input 协议）",
        )
        return "wayland"

    os.environ["QT_QPA_PLATFORM"] = "xcb"
    logger.info("Qt 平台：使用 xcb（未检测到可用的 Wayland 输入法前端）")
    return "xcb"


def setup_input_method() -> str | None:
    """
    根据当前平台与运行环境配置 Qt IME 环境变量。

    必须在 QApplication 构造之前调用。

    :returns: 最终生效的 `QT_IM_MODULE` 值（None 表示未做改动 / 无需设置）
    """
    # ── Windows / macOS：系统级 IME，无需任何配置 ──
    if not sys.platform.startswith("linux"):
        logger.info(
            "输入法配置：%s 平台使用系统原生 IME（无需 Qt 环境变量）",
            sys.platform,
        )
        return os.environ.get("QT_IM_MODULE")

    # ── Linux 平台 ──
    current = os.environ.get("QT_IM_MODULE", "").strip()
    available_plugins = _pyside_im_plugins()
    # **以 Qt 实际平台为准**：Wayland 会话 + xcb 平台时，wayland IM 模块无效
    platform = os.environ.get("QT_QPA_PLATFORM", "").strip().lower()
    is_wayland_platform = platform.startswith("wayland")

    if is_wayland_platform:
        # Wayland 平台：只能用内置 wayland IM 模块（走 text-input 协议）
        os.environ["QT_IM_MODULE"] = "wayland"
        if current and current != "wayland":
            logger.info(
                "输入法配置：平台=wayland，QT_IM_MODULE 由 %s 切换为 'wayland'"
                "（其他模块在 wayland 平台下不生效）",
                current,
            )
        else:
            logger.info("输入法配置：平台=wayland，使用内置 wayland IM 模块")
        return "wayland"

    # 非 Wayland（xcb 等）：只有 PySide6 自带的 platforminputcontexts 插件可用
    if current and current in available_plugins:
        logger.info(
            "输入法配置：尊重已设置 QT_IM_MODULE=%s（PySide6 中可用）",
            current,
        )
        return current

    if "ibus" in available_plugins and _is_running("ibus-daemon"):
        os.environ["QT_IM_MODULE"] = "ibus"
        logger.info(
            "输入法配置：平台=%s，检测到 ibus-daemon，QT_IM_MODULE=ibus",
            platform or "xcb",
        )
        return "ibus"

    # 实在没合适的 → 警告，但保留用户设置（至少 Compose key 可工作）
    logger.warning(
        "输入法配置：当前平台=%r 下 QT_IM_MODULE=%r 不可用；"
        "PySide6 仅自带 IM 插件 %s（系统 fcitx5 Qt 插件因 Qt 私有 API 不匹配无法加载）。"
        "建议：1) 使用 Wayland 会话并保持 fcitx5 运行；"
        "2) 或安装 ibus 并使用中文输入法",
        platform or "xcb", current, sorted(available_plugins),
    )
    return current or None

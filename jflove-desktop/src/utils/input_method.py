"""
跨平台中文输入法（IME）配置

各平台的 Qt 输入法工作机制：

- Linux Wayland
    Qt 6 内置 `wayland` IM 模块，直接通过 `text-input-v3` 协议与 IM frontend
    对话。fcitx5、ibus 等主流 IM 在 Wayland 下都通过对应 frontend
    （fcitx5-wayland-frontend / ibus-wayland）实现该协议。**最稳定方案**。

- Linux X11
    通过 `QT_IM_MODULE` 环境变量选择 IM 桥接插件 (fcitx / ibus)。
    PySide6 venv 自带的 Qt 在 plugins/platforminputcontexts/ 下携带
    `libibusplatforminputcontextplugin.so`，但**不带 fcitx 插件**。因此：
      - X11 + IBus → 自动可用
      - X11 + Fcitx5 → 需要系统级 fcitx5-qt6 插件，或退而用 ibus
      - X11 + Fcitx5 但无 fcitx 插件 → 自动回退到 ibus（如果 ibus daemon 在跑）

- Windows
    系统级 IME（PinYin / Microsoft IME / 各种第三方），Qt 自动桥接，无需任何
    环境变量配置。PyInstaller 打包后开箱即用。

- macOS
    系统级 IME（小企鹅 / 搜狗 / 自带），Qt 自动桥接，无需配置。本项目暂未
    在 macOS 上验证，但代码路径已预留。

策略：不强行覆盖用户**已经显式可用**的设置；只在用户的设置在当前 PySide6
中不可用（最常见：QT_IM_MODULE=fcitx 但 PySide6 没带 fcitx 插件）时才介入。

调用要求：必须在 `QApplication` 创建之前调用，否则 Qt 已经读完环境变量。
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
    is_wayland = bool(
        os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("XDG_SESSION_TYPE") == "wayland"
    )

    # 1) 当前设置是否可用？
    #    - "wayland" 是 Qt 6 wayland 平台插件内置（不在 plugins/ 下），始终可用
    #    - 其他名字必须在 PySide6 自带的 platforminputcontexts/ 中找到对应 .so
    is_current_usable = (
        (current == "wayland" and is_wayland)
        or (current and current in available_plugins)
    )
    if is_current_usable:
        logger.info(
            "输入法配置：尊重已设置 QT_IM_MODULE=%s（PySide6 中可用）",
            current,
        )
        return current

    # 2) 当前设置不可用 → 自动选一个能用的
    if is_wayland:
        # Wayland 首选：Qt 6 内置的 wayland IM 模块（走 text-input-v3 协议）
        # 前提：fcitx5 用户需安装 fcitx5-wayland-frontend；ibus 用户 1.5.30+
        os.environ["QT_IM_MODULE"] = "wayland"
        if current and current != "wayland":
            logger.info(
                "输入法配置：QT_IM_MODULE=%s 在 PySide6 中不可用，"
                "Wayland 下切换为 'wayland'（Qt 6 内置，走 text-input-v3 协议）",
                current,
            )
        else:
            logger.info("输入法配置：Wayland 默认使用 'wayland' IM 模块")
        return "wayland"

    # X11 fallback：PySide6 自带 ibus 插件
    if "ibus" in available_plugins and _is_running("ibus-daemon"):
        os.environ["QT_IM_MODULE"] = "ibus"
        logger.info(
            "输入法配置：X11 下检测到 ibus-daemon，QT_IM_MODULE=ibus",
        )
        return "ibus"

    # 实在没合适的 → 警告，但保留用户设置（至少 Compose key 可工作）
    logger.warning(
        "输入法配置：当前 QT_IM_MODULE=%r 在 PySide6 venv 中不可用；"
        "PySide6 仅自带 IM 插件 %s。"
        "建议：1) Wayland 下使用 fcitx5-wayland-frontend；"
        "2) X11 下安装系统 fcitx5-qt6 包并设置 QT_PLUGIN_PATH；"
        "3) 或改用 IBus（ibus-setup → 安装中文输入法）",
        current, sorted(available_plugins),
    )
    return current or None

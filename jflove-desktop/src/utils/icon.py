"""
应用图标加载工具

集中管理 JFLove 桌面应用的窗口图标 / 任务栏图标 / 系统托盘图标 / 通知图标。

使用方式：
    from src.utils.icon import get_app_icon
    self.setWindowIcon(get_app_icon())

跨平台说明：
  - Linux：使用 icon.png（X11 / Wayland 任务栏与托盘均能显示）
  - Windows：将 icon.ico 注册到同一 QIcon，任务栏可获得多分辨率图标
  - macOS：使用 icon.png（Dock 自动从 QApplication.setWindowIcon 同步）
"""

from __future__ import annotations

import os
from functools import lru_cache

from PySide6.QtGui import QIcon

from src.config.settings import ICON_PNG_PATH, ICON_ICO_PATH
from src.utils.logger import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_app_icon() -> QIcon:
    """
    获取应用图标。

    将 icon.png 与 icon.ico 注册到同一个 QIcon 中，由 Qt 在不同场景
    （任务栏、托盘、对话框）自动选择最合适的尺寸。

    使用 lru_cache 缓存：QIcon 对象可在多个窗口共享，避免重复 I/O。

    :returns: QIcon 实例。若两个图标文件都缺失则返回空 QIcon（不会抛错）
    """
    icon = QIcon()
    if os.path.exists(ICON_PNG_PATH):
        icon.addFile(ICON_PNG_PATH)
    else:
        logger.warning("应用图标 PNG 不存在: %s", ICON_PNG_PATH)
    if os.path.exists(ICON_ICO_PATH):
        icon.addFile(ICON_ICO_PATH)
    return icon

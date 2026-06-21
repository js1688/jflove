"""
桌面端测试 pytest 共享 fixture。

关键职责：
  1. 把 jflove-desktop 根目录加入 sys.path，使 src.* 可被 import
  2. 设置 QT_QPA_PLATFORM=offscreen 支持 headless 环境运行 Qt 测试
  3. 提供 session 级 QApplication 单例 fixture
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# PySide6 headless 运行（无显示器时必须设置，有显示器时不影响）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 把 jflove-desktop 根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """
    创建 QApplication 全局单例。

    Qt 组件（QThread、信号等）必须有 QApplication 实例才能正常工作。
    session 级 fixture 保证整个测试进程只创建一个 QApplication。
    """
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

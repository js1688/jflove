"""
v1.7.0 Linux 输入法（IME）配置单测

回归的缺陷：`main.py` 强制 `QT_QPA_PLATFORM=xcb`，而本模块看到 `WAYLAND_DISPLAY`
就把 `QT_IM_MODULE` 设成 `wayland` —— 两者互斥，中文输入彻底失效。

判据：
  - 平台与 IM 模块必须匹配（wayland 平台 ⇒ wayland 模块；xcb 平台 ⇒ 插件模块）；
  - 用户显式设置 / `JFLOVE_QPA_PLATFORM` 覆盖优先；
  - xcb + fcitx5 且无可用插件时，**不得**再设成 `wayland`（这正是原缺陷）。
"""

from __future__ import annotations

import os

import pytest

from src.utils import input_method

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="Linux 输入法配置（Wayland/xcb/ibus/fcitx）仅在 Linux 有意义",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """隔离环境变量与进程探测，避免受本机真实状态影响"""
    for key in ("QT_QPA_PLATFORM", "QT_IM_MODULE", "WAYLAND_DISPLAY",
                "XDG_SESSION_TYPE", input_method.PLATFORM_OVERRIDE_ENV):
        monkeypatch.delenv(key, raising=False)


def _set_wayland(monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")


def test_wayland会话且有输入法前端时选择wayland(monkeypatch):
    _set_wayland(monkeypatch)
    monkeypatch.setattr(input_method, "wayland_ime_available", lambda: True)

    assert input_method.configure_linux_qt_platform() == "wayland"
    assert input_method.setup_input_method() == "wayland"


def test_wayland会话但无输入法前端时退回xcb(monkeypatch):
    _set_wayland(monkeypatch)
    monkeypatch.setattr(input_method, "wayland_ime_available", lambda: False)

    assert input_method.configure_linux_qt_platform() == "xcb"


def test_非wayland会话使用xcb(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert input_method.configure_linux_qt_platform() == "xcb"


def test_用户显式设置平台时不干预(monkeypatch):
    _set_wayland(monkeypatch)
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    assert input_method.configure_linux_qt_platform() == "xcb"


def test_平台覆盖变量优先于自动选择(monkeypatch):
    _set_wayland(monkeypatch)
    monkeypatch.setattr(input_method, "wayland_ime_available", lambda: True)
    monkeypatch.setenv(input_method.PLATFORM_OVERRIDE_ENV, "xcb")

    assert input_method.configure_linux_qt_platform() == "xcb"
    assert input_method.setup_input_method() != "wayland"


def test_xcb平台绝不再设wayland模块(monkeypatch):
    """原缺陷的回归判据：xcb 下把 QT_IM_MODULE 设成 wayland 等于没有输入法"""
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    monkeypatch.setenv("QT_IM_MODULE", "fcitx")
    monkeypatch.setattr(input_method, "_pyside_im_plugins", lambda: {"ibus", "compose"})
    monkeypatch.setattr(input_method, "_is_running", lambda name: False)

    result = input_method.setup_input_method()
    assert result != "wayland"


def test_xcb平台有ibus时回退ibus(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    monkeypatch.setenv("QT_IM_MODULE", "fcitx")
    monkeypatch.setattr(input_method, "_pyside_im_plugins", lambda: {"ibus", "compose"})
    monkeypatch.setattr(input_method, "_is_running", lambda name: name == "ibus-daemon")

    import os

    assert input_method.setup_input_method() == "ibus"
    assert os.environ["QT_IM_MODULE"] == "ibus"


def test_xcb平台插件可用时尊重用户设置(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    monkeypatch.setenv("QT_IM_MODULE", "ibus")
    monkeypatch.setattr(input_method, "_pyside_im_plugins", lambda: {"ibus"})

    assert input_method.setup_input_method() == "ibus"


def test_非linux平台不干预(monkeypatch):
    """非 Linux 平台直接返回 None，不写 QT_QPA_PLATFORM"""
    monkeypatch.setattr(input_method.sys, "platform", "win32")
    assert input_method.configure_linux_qt_platform() is None
    assert "QT_QPA_PLATFORM" not in input_method.os.environ

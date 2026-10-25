"""
v1.8.2 / F40 回归：媒体浮层**子控件化**（geometry 相对父控件 / 全屏 / 回退）

依据：

- 需求 `文档记录/需求文档/v1.8.2.md` §3.5（F40.1～F40.5）与 AC-289～293；
- 设计 `文档记录/设计文档/v1.8.2.md` §4.1（P2 原型实测：PySide6 6.11 + X11/Wayland
  下 native 子控件可见且随父窗移动；`QWindow.setParent()` 返回 false 不可用）。

本文件覆盖三件事（不需要真实媒体，也不需要 Chromium 合成）：

1. **默认子控件**：`MediaOverlay` 默认把画面挂在 `QWebEngineView`（父控件）上，
   `rect` 直接作为相对坐标 `setGeometry`，**不再 `mapToGlobal`**；主窗/视口移动时
   子控件的全局位置自动跟随（相对 geometry 不变）；
2. **全屏 = 铺满视口**：子控件模式不调用 `showFullScreen()`，而是
   `setGeometry(0, 0, view.width(), view.height())`；视口尺寸变化后重贴合；
3. **回退开关**：`JFLOVE_MEDIA_OVERLAY=window`（或显式 `mode=`）恢复 v1.8.1 的
   独立顶层窗口 + 全局坐标实现；`get_view()` 不可用时自动降级为顶层窗口（F40.2）。

回退实现的完整生命周期（托盘隐藏/恢复/全屏）仍由
`tests/test_v151_media_overlay_lifecycle.py` 守住（该文件显式选择 window 模式）。
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QWidget

from src.ui.media_overlay import MODE_CHILD, MODE_WINDOW, MediaOverlay
from src.ui.web_shell import WebShell

#: 测试用预留矩形（相对视口，CSS 像素）
_RECT = {"x": 10, "y": 20, "w": 300, "h": 200, "visible": True}


@pytest.fixture(autouse=True)
def _reset_mode_env(monkeypatch):
    """默认清掉回退开关，避免宿主环境变量影响用例；用例内部可自行 setenv"""
    monkeypatch.delenv("JFLOVE_MEDIA_OVERLAY", raising=False)


def _make_child_overlay(rect: dict | None = None) -> tuple[MediaOverlay, QWidget]:
    """构造一个子控件模式浮层 + 它的"视口"（顶层 QWidget，模拟 QWebEngineView）"""
    view = QWidget()
    view.setGeometry(0, 0, 800, 600)
    view.show()
    overlay = MediaOverlay(get_view=lambda: view, publish_event=None, mode=MODE_CHILD)
    overlay._rect = dict(rect if rect is not None else _RECT)
    return overlay, view


def _cleanup(overlay: MediaOverlay) -> None:
    overlay.window.hide()
    view = overlay._get_view()
    if isinstance(view, QWidget):
        view.hide()


class Test默认子控件:
    """F40.1：默认结构 = QWebEngineView 的 native 子控件"""

    def test_默认模式为子控件(self, qapp):
        """未设置环境变量时默认 child，且画面窗口的 parent 就是视口"""
        overlay, view = _make_child_overlay()
        try:
            assert overlay.mode == MODE_CHILD
            assert overlay.window.parent() is view, "浮层不是视口的子控件"
            assert overlay.window.testAttribute(Qt.WidgetAttribute.WA_NativeWindow), (
                "子控件必须是 native window，否则会被 QtWebEngine 的合成层盖住（P2 原型）"
            )
        finally:
            _cleanup(overlay)

    def test_几何使用父控件相对坐标(self, qapp):
        """`rect` 就是相对视口的坐标：直接 setGeometry，不经过 mapToGlobal"""
        overlay, view = _make_child_overlay()
        try:
            overlay._apply_geometry()

            assert overlay.window.geometry().getRect() == (10, 20, 300, 200), (
                f"子控件几何应等于上报矩形，实际 {overlay.window.geometry().getRect()}"
            )
            # 全局位置 = 视口全局原点 + 相对坐标（由父子关系推导，未做任何换算）
            expected = view.mapToGlobal(QPoint(10, 20))
            actual = overlay.window.mapToGlobal(QPoint(0, 0))
            assert (actual.x(), actual.y()) == (expected.x(), expected.y())
        finally:
            _cleanup(overlay)

    def test_主窗移动时子控件自动跟随(self, qapp):
        """拖动主窗（= 移动视口）：相对几何不变，全局位置自动跟随（AC-289 的结构前提）"""
        overlay, view = _make_child_overlay()
        try:
            overlay._apply_geometry()
            before = overlay.window.mapToGlobal(QPoint(0, 0))

            view.move(200, 120)  # 主窗被拖动 200/120
            qapp.processEvents()

            after = overlay.window.mapToGlobal(QPoint(0, 0))
            assert (after.x() - before.x(), after.y() - before.y()) == (200, 120), (
                f"子控件未随父窗移动：{before} → {after}"
            )
            # **关键**：跟随不靠外壳重算几何，相对坐标保持原值
            assert overlay.window.geometry().getRect() == (10, 20, 300, 200)
        finally:
            _cleanup(overlay)

    def test_父窗缩放不改变相对几何(self, qapp):
        """父窗缩放后相对几何仍是上报矩形（页面自己会重新上报新矩形）"""
        overlay, view = _make_child_overlay()
        try:
            overlay._apply_geometry()
            view.resize(1200, 900)
            qapp.processEvents()

            overlay.refresh_geometry()

            assert overlay.window.geometry().getRect() == (10, 20, 300, 200)
        finally:
            _cleanup(overlay)


class Test子控件全屏:
    """F40.1 / AC-290：全屏 = 铺满视口（不再 showFullScreen）"""

    def test_全屏铺满视口且不调用showFullScreen(self, qapp, monkeypatch):
        overlay, _view = _make_child_overlay()
        calls: list[int] = []
        monkeypatch.setattr(overlay.window, "showFullScreen", lambda: calls.append(1))
        try:
            overlay.setFullScreen(True)
            qapp.processEvents()

            assert calls == [], "子控件模式不得调用 showFullScreen()（会脱离父窗）"
            assert overlay.isFullScreen() is True
            assert overlay.window.geometry().getRect() == (0, 0, 800, 600), (
                f"全屏应铺满视口，实际 {overlay.window.geometry().getRect()}"
            )
        finally:
            _cleanup(overlay)

    def test_全屏下视口缩放后重贴合(self, qapp):
        """全屏铺满口径应跟随视口尺寸（窗口缩放/进入全屏后 view 尺寸变化）"""
        overlay, view = _make_child_overlay()
        try:
            overlay.setFullScreen(True)
            view.resize(1024, 768)
            qapp.processEvents()

            overlay.refresh_geometry()

            assert overlay.window.geometry().getRect() == (0, 0, 1024, 768), (
                f"全屏几何未跟随视口：{overlay.window.geometry().getRect()}"
            )
        finally:
            _cleanup(overlay)

    def test_退出全屏恢复上报矩形(self, qapp):
        overlay, _view = _make_child_overlay()
        try:
            overlay.setFullScreen(True)
            overlay.setFullScreen(False)
            qapp.processEvents()

            assert overlay.isFullScreen() is False
            assert overlay.window.geometry().getRect() == (10, 20, 300, 200), (
                f"退出全屏未回到页面矩形：{overlay.window.geometry().getRect()}"
            )
        finally:
            _cleanup(overlay)

    def test_关窗期间只记录全屏意图(self, qapp, monkeypatch):
        """主窗隐藏（托盘）期间 setFullScreen 不显示，恢复时才铺满（M-5 同源不变式）"""
        overlay, _view = _make_child_overlay()
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        try:
            overlay._kind = "video"
            overlay.on_shell_hidden()
            before = overlay.window.geometry().getRect()

            overlay.setFullScreen(True)
            qapp.processEvents()

            assert overlay._fullscreen is True, "应记录全屏意图"
            assert overlay.window.geometry().getRect() == before, "隐藏期间不得改几何"

            overlay.refresh_geometry()  # 主窗恢复（WebShell.showEvent）
            qapp.processEvents()

            assert overlay._shell_hidden is False, "恢复成功后应清关窗态标志"
            assert overlay.window.geometry().getRect() == (0, 0, 800, 600)
        finally:
            _cleanup(overlay)


class Test子控件生命周期:
    """`visible=false` / 托盘隐藏恢复：子控件模式仍遵守 AC-12 不变式"""

    def test_渲染层声明不可见时隐藏(self, qapp):
        overlay, _view = _make_child_overlay()
        try:
            overlay.setVisible(True)
            qapp.processEvents()
            assert overlay.window.isVisible() is True

            overlay.setVisible(False)
            qapp.processEvents()

            assert overlay.window.isVisible() is False
        finally:
            _cleanup(overlay)

    def test_托盘恢复后重新显示(self, qapp, monkeypatch):
        overlay, _view = _make_child_overlay()
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        try:
            overlay._kind = "video"
            overlay.setVisible(True)
            qapp.processEvents()
            overlay.on_shell_hidden()
            assert overlay.window.isVisible() is False

            overlay.refresh_geometry()
            qapp.processEvents()

            assert overlay.window.isVisible() is True, "托盘恢复后子控件没有重新显示"
            assert overlay._shell_hidden is False
        finally:
            _cleanup(overlay)

    def test_close_清空状态(self, qapp, monkeypatch):
        """冒烟：子控件模式下 `close()` 清空状态并隐藏（不依赖真实媒体）"""
        overlay, _view = _make_child_overlay()
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        try:
            overlay.setVisible(True)
            overlay._kind = "video"
            overlay._fullscreen = True
            overlay.close()

            assert overlay._kind == ""
            assert overlay._rect == {}
            assert overlay._fullscreen is False
            assert overlay.window.isVisible() is False
        finally:
            _cleanup(overlay)

    def test_state_报告当前模式(self, qapp):
        overlay, _view = _make_child_overlay()
        try:
            payload = json.loads(overlay.state())
            assert payload["mode"] == MODE_CHILD
        finally:
            _cleanup(overlay)


class Test回退开关:
    """F40.2：`JFLOVE_MEDIA_OVERLAY=window` 回退 v1.8.1 顶层窗口实现"""

    def test_环境变量回退顶层窗口(self, qapp, monkeypatch):
        monkeypatch.setenv("JFLOVE_MEDIA_OVERLAY", "window")
        view = QWidget()
        overlay = MediaOverlay(get_view=lambda: view, publish_event=None)
        try:
            assert overlay.mode == MODE_WINDOW
            assert overlay.window.parent() is None, "回退实现必须是独立顶层窗口"
            assert overlay.window.isWindow() is True
        finally:
            overlay.window.hide()

    def test_环境变量大小写与空白容错(self, qapp, monkeypatch):
        monkeypatch.setenv("JFLOVE_MEDIA_OVERLAY", "  WiNdOw ")
        view = QWidget()
        overlay = MediaOverlay(get_view=lambda: view, publish_event=None)
        try:
            assert overlay.mode == MODE_WINDOW
            assert overlay.window.parent() is None
        finally:
            overlay.window.hide()

    def test_未知取值回落子控件(self, qapp, monkeypatch):
        monkeypatch.setenv("JFLOVE_MEDIA_OVERLAY", "top")
        view = QWidget()
        overlay = MediaOverlay(get_view=lambda: view, publish_event=None)
        try:
            assert overlay.mode == MODE_CHILD
        finally:
            _cleanup(overlay)

    def test_显式模式优先于环境变量(self, qapp, monkeypatch):
        monkeypatch.setenv("JFLOVE_MEDIA_OVERLAY", "window")
        view = QWidget()
        overlay = MediaOverlay(get_view=lambda: view, publish_event=None, mode=MODE_CHILD)
        try:
            assert overlay.mode == MODE_CHILD
            assert overlay.window.parent() is view
        finally:
            _cleanup(overlay)

    def test_视口不可用时自动回退(self, qapp):
        """`get_view()` 返回 None（页面尚未就绪）：自动降级为顶层窗口，不抛异常（F40.2）"""
        overlay = MediaOverlay(get_view=lambda: None, publish_event=None)
        try:
            assert overlay.mode == MODE_WINDOW, "取不到视口时必须自动回退，不得崩溃"
            assert overlay.window.parent() is None
        finally:
            overlay.window.hide()


class Test外壳视口转发:
    """设计 §4.1.2：`web_shell` 新增对 view resize 的转发"""

    class _StubOverlay:
        def __init__(self) -> None:
            self.refreshes = 0

        def refresh_geometry(self) -> None:
            self.refreshes += 1

        def on_shell_hidden(self) -> None:
            pass

    def test_attach_overlay_幂等安装视口过滤器(self, qapp):
        shell = WebShell(objects={}, frameless=True)
        overlay = self._StubOverlay()
        shell.attach_overlay(overlay)
        shell.attach_overlay(overlay)  # 重复挂载不应重复安装过滤器
        try:
            assert shell._overlay_view_filter_installed is True
        finally:
            shell.hide()

    def test_视口尺寸变化触发重贴合(self, qapp):
        """直接改 view 尺寸（页面客户区变化）也要重贴合，而非只依赖主窗 resizeEvent"""
        shell = WebShell(objects={}, frameless=True)
        overlay = self._StubOverlay()
        shell.attach_overlay(overlay)
        shell.show()
        qapp.processEvents()
        overlay.refreshes = 0  # 清掉 showEvent 产生的那一次
        try:
            shell.view.resize(500, 400)
            qapp.processEvents()

            assert overlay.refreshes >= 1, "view resize 没有转发到浮层重贴合"
        finally:
            shell.hide()

    def test_视口过滤器不吞事件(self, qapp):
        """eventFilter 只旁观：返回值必须是 False（不得拦截 view 的事件）"""
        shell = WebShell(objects={}, frameless=True)
        try:
            event = QResizeEvent(QSize(100, 100), QSize(100, 100))
            assert shell.eventFilter(shell.view, event) is False
        finally:
            shell.hide()

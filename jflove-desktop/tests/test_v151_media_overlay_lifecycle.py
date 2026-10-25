"""
v1.5.1 回归：桌面端原生媒体浮层的**生命周期与几何同步**（issue #3）

覆盖设计文档 §5 与 v1.5.1 Phase 5 审查处置（M-3 / M-5 / §10.1 不变式）的行为：

1. `MediaOverlay.refresh_geometry()`：全屏态与 `_rect` 为空时**不改变**几何；
   正常情况下**复用** `_apply_geometry()` 重新贴合（不新写第二套坐标计算）；
2. `MediaOverlay.on_shell_hidden()`：隐藏浮层窗口**并**强制重绘，
   且**不**停播放、**不**清 `_rect`、**不**清 `_kind`（关窗 = 后台继续播放）；
3. 关窗态恢复（`_shell_hidden`）：非全屏按 `_rect` 恢复、全屏按 `showFullScreen()`
   恢复；**没有媒体源不恢复**（M-3：否则弹全屏黑窗）；
   **恢复没真的发生就不清标志**（M-5：否则渲染层去重后恢复永不发生）；
4. §10.1 不变式：主窗隐藏期间除 `open()` 外不得把浮层摆出来
   （`setVisible(true)` / `setFullScreen(true)` 只记意图）；
5. `WebShell`：`hide_to_tray()` 会联动 overlay；拖动/缩放的连续事件
   经 `_schedule_overlay_refresh()` **合并成一次** `refresh_geometry()`；
   主窗不可见时不排队（避免隐藏期间被重新摆出来）。

测试手法：`_get_view` 传一个普通 `QWidget`（浮层内部只用 `mapToGlobal`），
`_force_repaint` 用 monkeypatch 计数（避免依赖真实 Chromium 重绘）。
本文件**不**依赖真实媒体、不依赖时间 `sleep`。

**本文件覆盖的是回退实现**：v1.8.2（F40）把默认实现改成 `QWebEngineView` 的
native 子控件（见 `tests/test_v182_media_overlay_child.py`），v1.8.1 的独立顶层
窗口实现保留为 `JFLOVE_MEDIA_OVERLAY=window` 回退路径。因此这里用 autouse
fixture 显式选择 `window` 模式，**原有断言一条不改**，继续守住回退路径的
托盘隐藏/恢复/全屏语义。

**未覆盖（无法在无头环境稳定验证）**：真实 OS 窗口拖动时的整屏像素表现
（画面是否随主窗移动、关窗后是否残留旧像素）—— 见
`文档记录/桌面端开发记录/v1.5.1.md` 的「遗留问题」。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from src.ui.media_overlay import MediaOverlay
from src.ui.web_shell import WebShell


@pytest.fixture(autouse=True)
def _legacy_window_mode(monkeypatch):
    """本文件的用例针对**保留的回退实现**（顶层窗口 + 全局坐标），显式选择 window 模式"""
    monkeypatch.setenv("JFLOVE_MEDIA_OVERLAY", "window")


#: 测试用预留矩形（相对视口，CSS 像素）
_RECT = {"x": 10, "y": 20, "w": 320, "h": 180, "visible": True}


def _make_overlay(rect: dict | None = None) -> tuple[MediaOverlay, QWidget]:
    """构造一个浮层 + 它的"视口"（普通 QWidget，只提供 mapToGlobal）"""
    view = QWidget()
    view.setGeometry(200, 150, 800, 600)
    overlay = MediaOverlay(get_view=lambda: view, publish_event=None)
    overlay._rect = dict(rect if rect is not None else _RECT)
    return overlay, view


class Test刷新几何:
    """`refresh_geometry()`：全屏/空矩形不改变几何，正常态复用 `_apply_geometry()`"""

    def test_全屏态不改变几何(self, qapp):
        """全屏浮层必须留在全屏：refresh_geometry 不得把它拉回小矩形"""
        overlay, _ = _make_overlay()
        # 模拟"已进入全屏"后的屏幕铺满尺寸
        overlay._fullscreen = True
        overlay.window.setGeometry(0, 0, 1920, 1080)
        before = overlay.window.geometry()

        overlay.refresh_geometry()

        assert overlay.window.geometry() == before, (
            f"全屏态几何被改写：{before} → {overlay.window.geometry()}"
        )

    def test_全屏态不调用_apply_geometry(self, qapp, monkeypatch):
        """全屏态应**直接返回**（连坐标换算都不做）"""
        overlay, _ = _make_overlay()
        overlay._fullscreen = True
        calls: list[int] = []
        monkeypatch.setattr(overlay, "_apply_geometry", lambda: calls.append(1))

        overlay.refresh_geometry()

        assert calls == [], "全屏态不应触发 _apply_geometry"

    def test_矩形为空不改变几何(self, qapp):
        """还没有预留矩形时没有换算基准：几何保持不变"""
        overlay, _ = _make_overlay()
        overlay._rect = {}
        overlay.window.setGeometry(11, 22, 33, 44)
        before = overlay.window.geometry()

        overlay.refresh_geometry()

        assert overlay.window.geometry() == before, (
            f"空矩形时几何被改写：{before} → {overlay.window.geometry()}"
        )

    def test_渲染层声明不可见时不改变几何(self, qapp):
        """`visible=false` 表示滚出视口/不应显示：重贴合不得把浮层又摆出来"""
        overlay, _ = _make_overlay(rect={**_RECT, "visible": False})
        overlay.window.setGeometry(11, 22, 33, 44)
        before = overlay.window.geometry()

        overlay.refresh_geometry()

        assert overlay.window.geometry() == before, (
            f"visible=false 时几何被改写：{before} → {overlay.window.geometry()}"
        )

    def test_正常态复用_apply_geometry_重新贴合(self, qapp, monkeypatch):
        """正常态：refresh_geometry 只是"外部触发的一次 _apply_geometry()"（不新写坐标计算）"""
        overlay, _ = _make_overlay()
        overlay.window.show()
        qapp.processEvents()
        try:
            calls: list[int] = []
            real_apply = overlay._apply_geometry

            def _spy() -> None:
                calls.append(1)
                real_apply()

            monkeypatch.setattr(overlay, "_apply_geometry", _spy)

            overlay.refresh_geometry()

            assert calls == [1], f"应复用 _apply_geometry() 恰好一次，实际 {calls}"
        finally:
            overlay.window.hide()


class Test主窗隐藏联动:
    """`on_shell_hidden()`：隐藏浮层 + 强制重绘，但不打断播放状态"""

    def test_隐藏浮层窗口并强制重绘(self, qapp, monkeypatch):
        """关窗（最小化到托盘）后浮层必须隐藏，且必须请求一次重绘（LESSONS L40）"""
        overlay, _ = _make_overlay()
        overlay.window.show()
        qapp.processEvents()
        assert overlay.window.isVisible(), "前置条件：浮层应处于显示态"

        repaints: list[int] = []
        monkeypatch.setattr(overlay, "_force_repaint", lambda: repaints.append(1))

        overlay.on_shell_hidden()

        assert overlay.window.isVisible() is False, "浮层窗口没有隐藏（屏幕上会残留画面）"
        assert repaints == [1], f"隐藏后必须强制重绘一次，实际 {repaints}"

    def test_不清空矩形与媒体类型(self, qapp, monkeypatch):
        """关窗 = 后台运行：保留 `_rect`/`_kind`，从托盘恢复时可直接贴合"""
        overlay, _ = _make_overlay()
        overlay._kind = "video"
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)

        overlay.on_shell_hidden()

        assert overlay._rect == _RECT, f"_rect 被清空：{overlay._rect}"
        assert overlay._kind == "video", f"_kind 被清空：{overlay._kind!r}"

    def test_不停止播放(self, qapp, monkeypatch):
        """关窗不得 `player.stop()`（托盘语义 = 音频继续播放）"""
        overlay, _ = _make_overlay()
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)

        stopped: list[int] = []
        monkeypatch.setattr(overlay.player, "stop", lambda: stopped.append(1))

        overlay.on_shell_hidden()

        assert stopped == [], "on_shell_hidden() 不应停止播放"


class Test关窗态恢复:
    """`_shell_hidden` 恢复分支：能恢复就恢复，不能恢复就**保持标志**（M-3 / M-5）"""

    def test_恢复窗口后几何事件把浮层重新显示(self, qapp, monkeypatch):
        """关窗→恢复（非全屏）：`_rect` 还在，几何事件到来时浮层必须重新出现"""
        overlay, _ = _make_overlay()
        overlay._kind = "video"
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        overlay.window.show()
        qapp.processEvents()

        overlay.on_shell_hidden()
        assert overlay.window.isVisible() is False, "前置条件：浮层应已隐藏"

        overlay.refresh_geometry()  # 由 WebShell.showEvent 触发
        qapp.processEvents()

        assert overlay.window.isVisible(), "恢复窗口后浮层没有重新显示"
        assert overlay._shell_hidden is False, "恢复成功后应清 _shell_hidden"

    def test_全屏态关窗后恢复仍以全屏方式显示(self, qapp, monkeypatch):
        """
        全屏 + 关窗 → 恢复：浮层必须**重新可见且仍是全屏方式**。

        回归的缺陷：若把全屏早退排在最前（本项目最初的实现就是这样），
        "全屏看视频 → 关窗到托盘 → 恢复窗口"会在全屏早退处直接 `return`，
        浮层**永久不再显示**（只剩音频在响）。
        """
        overlay, _ = _make_overlay()
        overlay._kind = "video"
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        fullscreen_calls: list[int] = []
        real_fullscreen = overlay.window.showFullScreen

        def _spy_fullscreen() -> None:
            fullscreen_calls.append(1)
            real_fullscreen()  # 仍走真实实现，否则窗口不会真的可见

        monkeypatch.setattr(overlay.window, "showFullScreen", _spy_fullscreen)
        overlay.window.show()
        qapp.processEvents()
        overlay._fullscreen = True

        overlay.on_shell_hidden()
        assert overlay.window.isVisible() is False, "前置条件：浮层应已隐藏"

        overlay.refresh_geometry()  # 由 WebShell.showEvent 触发
        qapp.processEvents()

        assert fullscreen_calls == [1], (
            f"全屏恢复必须走 showFullScreen()，实际调用 {fullscreen_calls}"
        )
        assert overlay.window.isVisible(), "全屏态从托盘恢复后浮层没有重新显示"
        assert overlay._shell_hidden is False, "恢复成功后应清 _shell_hidden"
        assert overlay._fullscreen is True, "恢复不应把全屏状态改掉"

    def test_全屏态关窗恢复时不降级为小矩形(self, qapp, monkeypatch):
        """全屏恢复分支不得调用 `_apply_geometry()`（那会把全屏浮层缩回 `_rect`）"""
        overlay, _ = _make_overlay()
        overlay._kind = "video"
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        monkeypatch.setattr(overlay.window, "showFullScreen", lambda: None)
        calls: list[int] = []
        monkeypatch.setattr(overlay, "_apply_geometry", lambda: calls.append(1))
        overlay._fullscreen = True
        overlay._shell_hidden = True

        overlay.refresh_geometry()

        assert calls == [], "全屏恢复不应按 _rect 重新贴合（会降级成小矩形）"

    def test_无媒体源时关窗恢复不显示_M3(self, qapp, monkeypatch):
        """
        M-3：预览已被 `close()`（`_kind` 为空）时，恢复窗口**不得**把浮层摆出来。

        回归的缺陷：陈旧的 `_fullscreen=True` + 已清空的媒体源，
        恢复时会 `showFullScreen()` 弹一个**没有媒体源的全屏黑窗**，违背 AC-12。
        """
        overlay, _ = _make_overlay()
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        fullscreen_calls: list[int] = []
        monkeypatch.setattr(
            overlay.window, "showFullScreen", lambda: fullscreen_calls.append(1)
        )
        # 前置：先证明"操作前浮层是可见的"（否则 `isVisible() is False` 可能空转通过，R5-T8）
        overlay.window.show()
        qapp.processEvents()
        assert overlay.window.isVisible() is True, "前置条件：浮层操作前应可见"

        overlay.on_shell_hidden()
        overlay._fullscreen = True
        overlay._kind = ""
        assert overlay.window.isVisible() is False, "前置条件：关窗后浮层应不可见"

        overlay.refresh_geometry()
        qapp.processEvents()

        assert overlay.window.isVisible() is False, "无媒体源时不得显示浮层（全屏黑窗）"
        assert fullscreen_calls == [], f"不得调用 showFullScreen()，实际 {fullscreen_calls}"

    def test_close_复位全屏标志_M3(self, qapp, monkeypatch):
        """M-3：`close()` 必须复位 `_fullscreen`（否则留下陈旧全屏意图）"""
        overlay, _ = _make_overlay()
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        overlay._fullscreen = True

        overlay.close()

        assert overlay._fullscreen is False, "close() 未复位 _fullscreen"
        assert overlay._kind == "", "close() 未清 _kind"
        assert overlay._rect == {}, "close() 未清 _rect"

    def test_恢复未发生时不消费关窗态标志_M5(self, qapp, monkeypatch):
        """
        M-5：`_rect` 为空导致**恢复没发生**时，`_shell_hidden` 必须**保持为真**。

        回归的缺陷：提前把标志清成 False，而渲染层因 `MediaStage.tsx` 的 `lastKey`
        去重不会重新上报 `rect`，恢复就**永久不会发生**（只剩音频）。
        """
        overlay, _ = _make_overlay()
        overlay._kind = "video"
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        overlay._shell_hidden = True
        overlay._rect = {}

        overlay.refresh_geometry()
        qapp.processEvents()

        assert overlay.window.isVisible() is False, "无预留矩形时不应显示浮层"
        assert overlay._shell_hidden is True, (
            "恢复没真的发生就不该消费 _shell_hidden（否则恢复永久不发生，M-5）"
        )

    def test_恢复未发生后下一次有效上报能恢复_M5(self, qapp, monkeypatch):
        """M-5 的收尾：标志保留后，**随后一次带有效 rect 的几何事件**必须把浮层恢复出来"""
        overlay, _ = _make_overlay()
        overlay._kind = "video"
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        overlay.window.show()
        qapp.processEvents()

        overlay.on_shell_hidden()
        overlay._rect = {}  # 托盘期间渲染层清空/未上报 rect
        overlay.refresh_geometry()
        assert overlay.window.isVisible() is False
        assert overlay._shell_hidden is True, "前置条件：标志应保留"

        # 渲染层随后上报了有效矩形 → 几何事件再次到来
        overlay._rect = dict(_RECT)
        overlay.refresh_geometry()
        qapp.processEvents()

        assert overlay.window.isVisible(), "有效上报后浮层仍未恢复"
        assert overlay._shell_hidden is False, "恢复成功后应清 _shell_hidden"

    def test_渲染层声明不可见时保持关窗态标志_M5(self, qapp, monkeypatch):
        """`visible=false` 导致恢复被推迟时同样不得消费标志"""
        overlay, _ = _make_overlay(rect={**_RECT, "visible": False})
        overlay._kind = "video"
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        overlay._shell_hidden = True

        overlay.refresh_geometry()

        assert overlay.window.isVisible() is False
        assert overlay._shell_hidden is True, "恢复被推迟时不应消费 _shell_hidden"


class Test隐藏期间不变式:
    """§10.1 不变式：主窗隐藏期间，除 `open()` 外不得把浮层摆出来"""

    def test_setVisible_true_在关窗态不显示(self, qapp, monkeypatch):
        """隐藏期间收到迟到的 `setVisible(true)`：只记意图，不显示浮层"""
        overlay, _ = _make_overlay()
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        overlay._shell_hidden = True

        overlay.setVisible(True)
        qapp.processEvents()

        assert overlay.window.isVisible() is False, "隐藏期间 setVisible(true) 把浮层摆了出来"
        assert overlay._visible is True, "仍应记录渲染层的可见意图"
        assert overlay._shell_hidden is True, "不应消费关窗态标志"

    def test_setFullScreen_true_在关窗态不显示(self, qapp, monkeypatch):
        """隐藏期间收到 `setFullScreen(true)`：记全屏意图但不铺满屏幕"""
        overlay, _ = _make_overlay()
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        fullscreen_calls: list[int] = []
        monkeypatch.setattr(
            overlay.window, "showFullScreen", lambda: fullscreen_calls.append(1)
        )
        # 前置：先证明"操作前浮层是可见的"（否则 `isVisible() is False` 可能空转通过，R5-T8）
        overlay.window.show()
        qapp.processEvents()
        assert overlay.window.isVisible() is True, "前置条件：浮层操作前应可见"

        overlay.on_shell_hidden()  # 关窗到托盘
        assert overlay.window.isVisible() is False, "前置条件：关窗后浮层应不可见"

        overlay.setFullScreen(True)
        qapp.processEvents()

        assert fullscreen_calls == [], "隐藏期间不得 showFullScreen()"
        assert overlay.window.isVisible() is False, "隐藏期间不得把浮层摆出来"
        assert overlay._fullscreen is True, "仍应记录全屏意图（主窗回来时还原）"

    def test_关窗态记录的全屏意图在恢复时生效(self, qapp, monkeypatch):
        """主窗回来后，隐藏期间记录的"全屏意图"应按全屏方式还原"""
        overlay, _ = _make_overlay()
        overlay._kind = "video"
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        fullscreen_calls: list[int] = []
        real_fullscreen = overlay.window.showFullScreen

        def _spy_fullscreen() -> None:
            fullscreen_calls.append(1)
            real_fullscreen()

        monkeypatch.setattr(overlay.window, "showFullScreen", _spy_fullscreen)
        overlay._shell_hidden = True

        overlay.setFullScreen(True)  # 隐藏期间只记意图
        assert fullscreen_calls == [], "前置条件：隐藏期间不应显示"

        overlay.refresh_geometry()  # 主窗恢复
        qapp.processEvents()

        assert fullscreen_calls == [1], "恢复时应按记录的全屏意图 showFullScreen()"
        assert overlay.window.isVisible()

    def test_setVisible_false_在关窗态仍然隐藏(self, qapp, monkeypatch):
        """`setVisible(false)` 方向与不变式一致：照常隐藏并强制重绘"""
        overlay, _ = _make_overlay()
        repaints: list[int] = []
        monkeypatch.setattr(overlay, "_force_repaint", lambda: repaints.append(1))
        overlay.window.show()
        qapp.processEvents()
        overlay._shell_hidden = True

        overlay.setVisible(False)
        qapp.processEvents()

        assert overlay.window.isVisible() is False
        assert repaints == [1], f"隐藏时应强制重绘一次，实际 {repaints}"

    def test_open_是显式例外会清关窗态(self, qapp, monkeypatch):
        """`open()` 是唯一例外：用户主动打开新预览 → 清 `_shell_hidden` 并显示"""
        overlay, _ = _make_overlay()
        monkeypatch.setattr(overlay, "_force_repaint", lambda: None)
        overlay._shell_hidden = True

        result = overlay.open('{"kind": "image", "local_path": ""}')

        assert "图片解码失败" in result, f"无 local_path 的图片应解码失败，实际 {result}"
        assert overlay._shell_hidden is False, "open() 应清 _shell_hidden（显式例外）"


class Test外壳联动:
    """`WebShell`：几何事件驱动重贴合、隐藏主窗联动隐藏浮层"""

    class _StubOverlay:
        """鸭子类型替身：只记录 `refresh_geometry` / `on_shell_hidden` 的调用"""

        def __init__(self) -> None:
            self.refreshes = 0
            self.hidden = 0

        def refresh_geometry(self) -> None:
            self.refreshes += 1

        def on_shell_hidden(self) -> None:
            self.hidden += 1

    class _StubTray:
        """托盘替身（只被 `hide_to_tray` 调 `notify_minimized`）"""

        def __init__(self) -> None:
            self.notified = 0

        def notify_minimized(self) -> None:
            self.notified += 1

    def test_hide_to_tray_联动隐藏浮层(self, qapp):
        """hide_to_tray() 在隐藏主窗之前，先让浮层一起隐藏（原有托盘提示行为不变）"""
        shell = WebShell(objects={}, frameless=True)
        overlay = self._StubOverlay()
        tray = self._StubTray()
        shell.attach_overlay(overlay)
        shell.tray = tray
        shell.show()
        qapp.processEvents()
        show_refreshes = overlay.refreshes
        overlay.refreshes = 0  # 只关心 hide_to_tray 这一步的刷新次数
        try:
            shell.hide_to_tray()

            assert overlay.hidden == 1, "hide_to_tray() 未联动 overlay.on_shell_hidden()"
            assert show_refreshes >= 1, (
                f"showEvent 应在显示时重贴合（至少一次），实际 {show_refreshes}"
            )
            assert overlay.refreshes == 0, (
                f"hide_to_tray() 不应再触发重贴合，实际 {overlay.refreshes}"
            )
            assert shell.isVisible() is False, "主窗未隐藏"
            assert tray.notified == 1, "原有托盘气泡提示行为被改变"
        finally:
            shell.hide()

    def test_未挂浮层时_hide_to_tray_不报错(self, qapp):
        """没挂浮层（或浮层为 None）时行为与改动前一致"""
        shell = WebShell(objects={}, frameless=True)
        tray = self._StubTray()
        shell.tray = tray

        shell.hide_to_tray()  # 不应抛异常

        assert tray.notified == 1

    def test_浮层刷新抛异常不影响主窗(self, qapp):
        """`_safe_overlay_refresh()` 吞掉浮层异常（几何同步是纯观感，不该打断主窗）"""

        class _BoomOverlay:
            def refresh_geometry(self) -> None:
                raise RuntimeError("浮层内部炸了")

            def on_shell_hidden(self) -> None:
                pass

        shell = WebShell(objects={}, frameless=True)
        shell.attach_overlay(_BoomOverlay())

        shell.show()  # showEvent → _safe_overlay_refresh()，不应把异常抛出来
        qapp.processEvents()
        shell.resize(1000, 700)
        qapp.processEvents()

        assert shell.isVisible() is True
        shell.hide()

    def test_同帧多次调度只触发一次刷新(self, qapp):
        """合并语义：一次拖动产生的连续 move/resize 事件只重贴合一次"""
        shell = WebShell(objects={}, frameless=True)
        overlay = self._StubOverlay()
        shell.attach_overlay(overlay)
        shell.show()
        qapp.processEvents()
        overlay.refreshes = 0  # 清掉 showEvent 产生的那次

        for _ in range(5):
            shell._schedule_overlay_refresh()

        assert overlay.refreshes == 0, "调度阶段不应同步调用 refresh_geometry（需延迟一帧）"

        qapp.processEvents()

        assert overlay.refreshes == 1, (
            f"同帧多次调度应合并为一次 refresh_geometry，实际 {overlay.refreshes}"
        )
        assert shell._overlay_refresh_pending is False, "pending 标志未复位"
        shell.hide()

    def test_连续两帧各触发一次(self, qapp):
        """反面：合并只发生在**同一帧**内，下一帧的新事件仍要重贴合"""
        shell = WebShell(objects={}, frameless=True)
        overlay = self._StubOverlay()
        shell.attach_overlay(overlay)
        shell.show()
        qapp.processEvents()
        overlay.refreshes = 0  # 清掉 showEvent 产生的那次

        shell._schedule_overlay_refresh()
        qapp.processEvents()
        shell._schedule_overlay_refresh()
        qapp.processEvents()

        assert overlay.refreshes == 2, f"两帧应各刷新一次，实际 {overlay.refreshes}"
        shell.hide()

    def test_未挂浮层时调度为空操作(self, qapp):
        """没有浮层时几何事件不应产生任何副作用"""
        shell = WebShell(objects={}, frameless=True)
        shell.show()
        qapp.processEvents()

        shell._schedule_overlay_refresh()
        qapp.processEvents()

        assert shell._overlay is None
        assert shell._overlay_refresh_pending is False

    def test_主窗不可见时几何事件不触发重贴合(self, qapp):
        """
        隐藏到托盘之后收到 move/resize：**不得**重贴合。

        回归的缺陷：`hide_to_tray()` 期间平台仍可能投递几何事件，若照常排队，
        回调会走到 `refresh_geometry()` 的"关窗态恢复"分支，把刚隐藏的浮层
        又摆回屏幕上 —— #3 的修复在真机上等于失效。
        """
        shell = WebShell(objects={}, frameless=True)
        overlay = self._StubOverlay()
        shell.attach_overlay(overlay)
        shell.show()
        qapp.processEvents()
        shell.hide_to_tray()
        qapp.processEvents()
        assert shell.isVisible() is False, "前置条件：主窗应已隐藏"
        overlay.refreshes = 0

        # 平台在隐藏过程中投递几何事件（直接调用 + 真实 resize 两条路径）
        shell._schedule_overlay_refresh()
        shell.resize(1000, 700)
        qapp.processEvents()

        assert overlay.refreshes == 0, (
            f"主窗不可见时不应重贴合，实际 {overlay.refreshes}"
        )
        assert shell._overlay_refresh_pending is False, "不可见时不应排队"

    def test_同一帧内多次调度只调用一次_主窗可见(self, qapp):
        """主窗可见时同一帧内连发多次 move/resize → 只调用 1 次 refresh_geometry()"""
        shell = WebShell(objects={}, frameless=True)
        overlay = self._StubOverlay()
        shell.attach_overlay(overlay)
        shell.show()
        qapp.processEvents()
        overlay.refreshes = 0

        shell.move(120, 90)
        shell.resize(1000, 700)
        shell.move(140, 110)
        shell.resize(1010, 710)
        shell._schedule_overlay_refresh()  # 显式再补一次，仍在同一帧内
        qapp.processEvents()

        assert overlay.refreshes == 1, (
            f"同一帧内多次事件应只重贴合一次，实际 {overlay.refreshes}"
        )

    def test_拖动主窗触发重贴合(self, qapp):
        """真实事件路径：moveEvent → _schedule_overlay_refresh → refresh_geometry"""
        shell = WebShell(objects={}, frameless=True)
        overlay = self._StubOverlay()
        shell.attach_overlay(overlay)
        shell.show()
        qapp.processEvents()
        overlay.refreshes = 0  # 清掉 showEvent 产生的那一次
        try:
            shell.move(320, 240)
            qapp.processEvents()

            assert shell.x() == 320 and shell.y() == 240, (
                f"主窗没有真的移动：({shell.x()}, {shell.y()})"
            )
            assert overlay.refreshes == 1, (
                f"移动主窗应触发一次重贴合，实际 {overlay.refreshes}"
            )
        finally:
            shell.hide()


def test_qapp_单例可复用(qapp):
    """冒烟：本文件的 Qt 用例复用 conftest 的 session 级 QApplication"""
    assert QApplication.instance() is qapp

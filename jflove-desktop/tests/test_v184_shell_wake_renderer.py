"""
v1.8.4 追加修复：最小化 → 恢复后「整个面板空白」

## 缺陷

Windows 上最小化窗口后，Chromium 把页面判为 occluded 并**停止出帧**；恢复时外壳
只做 `showNormal()`，合成层不会自己补一帧 ⇒ DOM 正常但整片空白（用户实测）。

## 修法（本文件守住的契约）

1. `showEvent()` / `restore_from_tray()` / `changeEvent()` 的**最小化 → 恢复边沿**
   都必须排队一次「唤醒渲染器」（`_wake_renderer`）；
2. 窗口仍最小化/不可见时**不得**执行（此刻动尺寸会把窗口改坏）；
3. 同一轮多次触发只执行一次（合帧），且恢复后的延迟复查只排一次；
4. 唤醒动作本身：页面生命周期拉回 Active + 1px 尺寸抖动 + 重绘 + 派发 JS resize；
5. `src/utils/webengine_flags.py` 的 flags 合并：Windows 注入关闭
   `CalculateNativeWinOcclusion`、保留用户既有参数、非 Windows 不动。

测试手法：用**替身 view / 替身 page** 记录调用，不启动真实 Chromium 渲染
（与 `test_v151_media_overlay_lifecycle.py` 同口径）。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize

from src.ui.web_shell import WebShell
from src.utils import webengine_flags


class _StubPage:
    """渲染页面替身：记录生命周期与 JS 调用"""

    class LifecycleState:  # 与 QWebEnginePage.LifecycleState 同名成员
        Active = "active"
        Frozen = "frozen"

    def __init__(self, state: str = "active") -> None:
        self._state = state
        self.lifecycle_calls: list[str] = []
        self.js_calls: list[str] = []

    def lifecycleState(self) -> str:  # noqa: N802 - 对齐 Qt 命名
        return self._state

    def setLifecycleState(self, state: str) -> None:  # noqa: N802
        self._state = state
        self.lifecycle_calls.append(state)

    def runJavaScript(self, _script: str) -> None:  # noqa: N802
        self.js_calls.append(_script)


class _StubView:
    """渲染 widget 替身：记录 resize / 重绘"""

    def __init__(self, page: _StubPage | None = None) -> None:
        self._page = page or _StubPage()
        self._size = QSize(1180, 760)
        self.resizes: list[tuple[int, int]] = []
        self.updates = 0
        self.repaints = 0

    def page(self) -> _StubPage:
        return self._page

    def focusProxy(self):  # noqa: N802 - Windows 上 showEvent 会走粘贴过滤器
        return None

    def size(self) -> QSize:  # noqa: N802
        return QSize(self._size)

    def resize(self, *args) -> None:  # noqa: N802
        if len(args) == 1:
            size = args[0]
            self._size = QSize(size)
        else:
            self._size = QSize(args[0], args[1])
        self.resizes.append((self._size.width(), self._size.height()))

    def update(self) -> None:  # noqa: N802
        self.updates += 1

    def repaint(self) -> None:  # noqa: N802
        self.repaints += 1


def _shell_with_stub(qapp, page: _StubPage | None = None):
    """造一个 WebShell + 替身 view（不加载真实页面）"""
    shell = WebShell(objects={}, frameless=True)
    view = _StubView(page)
    shell.view = view  # 覆盖真实 view：本文件只验证唤醒逻辑
    return shell, view


def _window_state_event() -> QEvent:
    """构造一个 WindowStateChange 事件（changeEvent 只认类型）"""
    return QEvent(QEvent.Type.WindowStateChange)


class TestWakeOnRestore:
    def test_最小化恢复边沿会排队唤醒(self, qapp):
        shell, view = _shell_with_stub(qapp)
        shell.show()
        qapp.processEvents()
        try:
            # 直接构造「上一轮处于最小化」的状态：changeEvent 只认边沿 + isMinimized()
            shell._was_minimized = True
            view.updates = 0
            view.resizes.clear()

            shell.changeEvent(_window_state_event())
            qapp.processEvents()

            assert view.updates >= 1, "恢复边沿未触发唤醒（重绘没有发生）"
            assert view.resizes, "未做 1px 尺寸抖动（合成层不会重建 surface）"
            assert shell._was_minimized is False, "边沿状态未更新（下次恢复会漏触发）"
        finally:
            shell.hide()

    def test_首次显示不做无意义唤醒(self, qapp):
        """首次 show 时页面还没加载，抖动尺寸没有意义（唤醒只服务恢复场景）"""
        shell, view = _shell_with_stub(qapp)
        view.updates = 0
        try:
            shell.show()
            qapp.processEvents()
            assert view.updates == 0, "首次显示不应唤醒（页面尚未加载）"
        finally:
            shell.hide()

    def test_再次显示会排队唤醒(self, qapp):
        shell, view = _shell_with_stub(qapp)
        try:
            shell.show()
            qapp.processEvents()
            shell.hide()
            qapp.processEvents()
            view.updates = 0
            shell.show()
            qapp.processEvents()
            assert view.updates >= 1, "从隐藏恢复时 showEvent 未触发唤醒"
        finally:
            shell.hide()

    def test_托盘恢复会唤醒(self, qapp):
        shell, view = _shell_with_stub(qapp)
        try:
            shell.show()
            qapp.processEvents()
            shell.hide_to_tray()
            qapp.processEvents()
            view.updates = 0
            shell.restore_from_tray()
            qapp.processEvents()
            assert view.updates >= 1, "restore_from_tray 未唤醒渲染器"
        finally:
            shell.hide()

    def test_最小化期间不执行唤醒(self, qapp):
        shell, view = _shell_with_stub(qapp)
        shell.show()
        qapp.processEvents()
        try:
            shell.showMinimized()
            qapp.processEvents()
            view.updates = 0
            view.resizes.clear()
            shell._do_wake_renderer("test")
            assert view.updates == 0, "窗口最小化时不应动尺寸/重绘"
            assert view.resizes == [], "窗口最小化时不应 resize（会改坏窗口尺寸）"
        finally:
            shell.hide()

    def test_同一轮多次触发只执行一次(self, qapp):
        shell, view = _shell_with_stub(qapp)
        shell.show()
        qapp.processEvents()
        try:
            view.updates = 0
            shell._schedule_wake_renderer("a")
            shell._schedule_wake_renderer("b")
            shell._schedule_wake_renderer("c")
            assert shell._wake_pending is True
            qapp.processEvents()
            assert view.updates == 1, f"合帧失效，重绘 {view.updates} 次"
        finally:
            shell.hide()


class TestWakeRendererActions:
    def test_页面被冻结时拉回_active(self, qapp):
        page = _StubPage(state="frozen")
        shell, view = _shell_with_stub(qapp, page)
        try:
            assert shell._wake_renderer() is True
            assert len(page.lifecycle_calls) == 1, "未把冻结的页面拉回 Active"
            assert page.lifecycle_calls[0] != "frozen"
            assert page.lifecycleState() != "frozen"
        finally:
            shell.hide()

    def test_1px_尺寸抖动后还原(self, qapp):
        shell, view = _shell_with_stub(qapp)
        try:
            shell._wake_renderer()
            assert view.resizes == [(1181, 761), (1180, 760)], (
                f"尺寸抖动不符合预期：{view.resizes}"
            )
        finally:
            shell.hide()

    def test_唤醒不引入_js_执行通道(self, qapp):
        """AC-87：外壳必须在 AST 层面零 `runJavaScript` 调用（能力面不扩张）"""
        import ast

        from tests import conftest as _conftest  # noqa: F401  (确保 sys.path 已就绪)
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent / "src" / "ui" / "web_shell.py"
        ).read_text(encoding="utf-8")
        called = {
            node.func.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "runJavaScript" not in called, "唤醒渲染器不得引入 JS 执行通道"

        page = _StubPage()
        shell, _view = _shell_with_stub(qapp, page)
        try:
            shell._wake_renderer()
            assert page.js_calls == [], "唤醒路径不应执行 JS（尺寸变化由 Chromium 自己派发）"
        finally:
            shell.hide()


class TestWebengineFlags:
    def test_windows_注入关闭遮挡节流(self):
        merged = webengine_flags.build_chromium_flags("", platform="win32")
        assert "CalculateNativeWinOcclusion" in merged
        assert merged.startswith("--disable-features=")

    def test_保留用户既有参数并合并到同一个开关(self):
        merged = webengine_flags.build_chromium_flags(
            "--disable-gpu --disable-features=Foo,Bar", platform="win32"
        )
        assert "--disable-gpu" in merged
        assert merged.count("--disable-features=") == 1, (
            "另写一个 --disable-features 会覆盖前面的特性开关"
        )
        assert "Foo,Bar" in merged and "CalculateNativeWinOcclusion" in merged

    def test_幂等_重复调用不重复追加(self):
        once = webengine_flags.build_chromium_flags("", platform="win32")
        twice = webengine_flags.build_chromium_flags(once, platform="win32")
        assert once == twice

    def test_非_windows_不改动(self):
        assert webengine_flags.build_chromium_flags("--foo", platform="linux") == "--foo"
        assert webengine_flags.build_chromium_flags(None, platform="darwin") == ""

    def test_空值容错(self):
        merged = webengine_flags.build_chromium_flags(None, platform="win32")
        assert merged == f"--disable-features={webengine_flags.OCCLUSION_FEATURE}"

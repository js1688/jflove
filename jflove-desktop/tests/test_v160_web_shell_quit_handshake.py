"""
v1.6.0 桌面端 Web 外壳：**退出握手 + 宿主默认动作处置 + 外部路径通知**

设计依据：`文档记录/设计文档/v1.6.0.md`
  - §6.4（829-843）退出握手完整流程（超时兜底 / `shell.quitRequested {timeoutMs}` /
    `editor.confirmQuit` / `editor.cancelQuit` / 绝不自动清空内容）
  - §6.5（844-859）窗口退出与单实例的交互顺序（✕ 仍是最小化到托盘）
  - §7.1（1021-1064）Python→渲染层事件表（`editor.openPaths` 的 payload
    `{paths, source:"cli"|"single_instance"}`、`shell.quitRequested`）与
    「本版本**不新增** `shell.*` 白名单方法」
  - §7.5（1094-1107）启动期事件不丢：队列 + 就绪后按序 flush + 一次延迟重推
  - §4.2.2 / §4.2.3（478 / 489-514）**R-5 实测结论**：Qt 6.11 没有
    `QWebEnginePage.actions()`；枚举 49 项 = 48 动作 + 1 计数哨兵 `WebActionCount`，
    对哨兵取 action 会崩（0xC0000005）⇒ 必须用白名单 `view.pageAction(...)`

覆盖点：超时兜底（含默认「取消」与选「退出」）、取消、确认、零脏直接退、幂等、
`notify_external_paths` 未就绪不丢（队列/去重/上限/按序 flush/一次重推）、
宿主默认动作处置**对不存在的名字不崩且绝不碰哨兵、绝不遍历 `actions()`**。

测试手法：`QT_QPA_PLATFORM=offscreen`（`conftest.py` 已设）；整个模块**复用同一个
`WebShell`**（Chromium 视图构造较重），每个用例前 `_reset()` 复位状态；发布器用假回调
（不依赖真实页面），原生兜底框用 `QMessageBox` 替身（不弹真框）。
草稿落盘用例把 `settings.SCRATCH_DIR` 指向 `tmp_path`，**绝不写真实用户目录**。
"""
from __future__ import annotations

import pytest
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QMessageBox

from src.config import settings
from src.services import scratch_service
from src.ui import web_shell as web_shell_module
from src.ui.web_shell import (
    HOST_ACTIONS_TO_DISABLE,
    OPEN_PATHS_QUEUE_LIMIT,
    QUIT_REQUEST_TIMEOUT_MS,
    QUIT_STATE_DIALOG,
    QUIT_STATE_IDLE,
    QUIT_STATE_PENDING,
    QUIT_STATE_QUITTING,
    SOURCE_CLI,
    SOURCE_SINGLE_INSTANCE,
    WebShell,
    disable_host_actions,
    resolve_host_actions,
)

LOGGER_NAME = "src.ui.web_shell"

#: `_editor_bridge` 的原始实现（autouse 夹具会把它换成"未接线"，需要时用它还原）
_REAL_EDITOR_BRIDGE = WebShell.__dict__["_editor_bridge"]


# ── 替身 / fixtures ──────────────────────────────────────────────────


class _Recorder:
    """假事件发布器：记录 `(topic, payload)` 序列"""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def __call__(self, topic: str, payload: object) -> None:
        self.events.append((topic, payload))

    @property
    def topics(self) -> list[str]:
        return [topic for topic, _payload in self.events]

    def payloads(self, topic: str) -> list[object]:
        return [payload for t, payload in self.events if t == topic]


class _StubTray:
    """托盘替身（只被 `hide_to_tray()` / `quit_application()` 调用）"""

    def __init__(self) -> None:
        self.notified = 0
        self.hidden = 0

    def notify_minimized(self) -> None:
        self.notified += 1

    def hide(self) -> None:
        self.hidden += 1


class _StubCloseEvent:
    """`closeEvent` 事件替身（只关心 accept / ignore）"""

    def __init__(self) -> None:
        self.accepted: bool | None = None

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.accepted = False


class _FakeAction:
    """`QAction` 替身：记录 `setShortcuts` / `setEnabled` 的调用序列"""

    def __init__(self, shortcut: str = "Ctrl+S") -> None:
        self.log: list[tuple[str, object]] = []
        self.shortcuts = [shortcut]
        self.enabled = True

    def setShortcuts(self, value) -> None:  # noqa: N802 - 对齐 Qt 命名
        self.log.append(("setShortcuts", value))
        self.shortcuts = list(value)

    def setEnabled(self, value) -> None:  # noqa: N802 - 对齐 Qt 命名
        self.log.append(("setEnabled", value))
        self.enabled = bool(value)


class _FakeView:
    """
    WebEngine 视图替身。

    `actions()` 故意抛异常：任何"遍历 view.actions() 取下标"的实现都会立刻失败
    （R-5 实测该路子在 Qt 6.11 上行不通，且哨兵会崩进程）。
    """

    def __init__(self, fail_on: tuple = ()) -> None:
        self.received: list[object] = []
        self.actions_made: list[_FakeAction] = []
        self._fail_on = fail_on

    def actions(self):
        raise AssertionError("不得遍历 view.actions()（R-5：长度为 0 且哨兵会崩）")

    def pageAction(self, action):  # noqa: N802 - 对齐 Qt 命名
        if action in self._fail_on:
            raise RuntimeError("该动作在本版本不可用")
        self.received.append(action)
        fake = _FakeAction()
        self.actions_made.append(fake)
        return fake


def _install_fake_messagebox(monkeypatch, click_text: str) -> list:
    """
    把 `web_shell.QMessageBox` 换成替身，并让 `exec()` 模拟"点了 `click_text` 那个按钮"。

    :returns: 创建的替身实例列表（供断言文案 / 默认按钮）
    """
    instances: list = []

    class _FakeMessageBox:
        Icon = QMessageBox.Icon
        ButtonRole = QMessageBox.ButtonRole

        def __init__(self, parent=None) -> None:
            self.parent = parent
            self.title = ""
            self.text = ""
            self.informative = ""
            self.icon = None
            self.buttons: list = []
            self.default = None
            self.escape = None
            instances.append(self)

        def setIcon(self, icon) -> None:  # noqa: N802
            self.icon = icon

        def setWindowTitle(self, title) -> None:  # noqa: N802
            self.title = title

        def setText(self, text) -> None:  # noqa: N802
            self.text = text

        def setInformativeText(self, text) -> None:  # noqa: N802
            self.informative = text

        def addButton(self, text, role):  # noqa: N802
            button = {"text": text, "role": role}
            self.buttons.append(button)
            return button

        def setDefaultButton(self, button) -> None:  # noqa: N802
            self.default = button

        def setEscapeButton(self, button) -> None:  # noqa: N802
            self.escape = button

        def exec(self) -> int:
            return 0

        def clickedButton(self):  # noqa: N802
            for button in self.buttons:
                if button["text"] == click_text:
                    return button
            return None

    monkeypatch.setattr(web_shell_module, "QMessageBox", _FakeMessageBox)
    return instances


@pytest.fixture(autouse=True)
def scratch_dir(tmp_path, monkeypatch):
    """草稿库根目录指向临时目录（退出标记落盘用例不碰真实用户目录）"""
    target = tmp_path / "scratch"
    monkeypatch.setattr(settings, "SCRATCH_DIR", str(target))
    return target


@pytest.fixture(autouse=True)
def isolated_editor_bridge(monkeypatch):
    """
    默认把 `_editor_bridge()` 打成 None（按"桥未接线"处理）。

    这样本文件只验证 `WebShell` 自身；与真实 `bridge/editor.py` 的对接由
    `tests/test_bridge_editor_v1_6_0.py` 覆盖，避免两个模块的内部状态互相干扰。
    """
    monkeypatch.setattr(WebShell, "_editor_bridge", staticmethod(lambda: None))


@pytest.fixture(scope="module")
def shell(qapp):
    """整个模块复用一个 `WebShell`（Chromium 视图构造较重）"""
    instance = WebShell(objects={}, frameless=True, publish_event=None)
    yield instance
    instance.hide()


@pytest.fixture
def coop(shell):
    """复位 `WebShell` 状态 + 装一个新的假发布器，返回 `(shell, recorder)`"""
    recorder = _Recorder()
    # 清掉上一个用例留下的**实例级**替身（`shell._save_exit_marks = ...` 这类赋值
    # 不会被 monkeypatch 自动还原，模块级复用的 shell 必须显式复位）
    for name in ("quit_application", "_save_exit_marks", "_fallback_confirm_quit"):
        shell.__dict__.pop(name, None)
    shell._publish_event = recorder
    shell._quitting = False
    shell._quit_state = QUIT_STATE_IDLE
    shell._dirty_count = 0
    shell._retain_preference = None
    shell._ui_loaded = False
    shell._renderer_ready = None
    shell._open_paths_queue.clear()
    shell._open_paths_queued.clear()
    shell._open_paths_retry_batches.clear()
    shell._open_paths_retry_pending = False
    if shell._quit_timer is not None:
        shell._quit_timer.stop()
    shell.tray = None
    return shell, recorder


def _no_quit_side_effects(shell) -> list:
    """
    把"真正退出"与"落盘"换成记录器（不碰 `QApplication.quit()`），
    但**保留状态机语义**（真实 `quit_application()` 会置 `_quitting` + `quitting`）。
    """
    calls: list = []

    def _quit() -> None:
        calls.append("quit")
        shell._quitting = True
        shell._quit_state = QUIT_STATE_QUITTING

    shell.quit_application = _quit
    shell._save_exit_marks = lambda **kwargs: calls.append(("marks", kwargs))
    return calls


# ── 退出握手：请求 / 幂等 / 超时兜底（§6.4） ──────────────────────────


class Test退出请求与幂等:
    """`request_quit()`：有脏推事件等回执；重复调用只触发一次"""

    def test_有脏时推quitRequested并启动定时器(self, coop):
        shell, recorder = coop
        shell.report_dirty(2)

        assert shell.request_quit() is True

        assert shell.quit_state == QUIT_STATE_PENDING
        assert shell.quit_pending is True
        assert shell.dirty_count == 2
        assert recorder.events == [
            ("shell.quitRequested", {"timeoutMs": QUIT_REQUEST_TIMEOUT_MS})
        ], "payload 必须是 {timeoutMs}（§7.1 事件 #2）"
        assert shell._quit_timer is not None
        assert shell._quit_timer.isActive() is True
        assert shell._quit_timer.interval() == QUIT_REQUEST_TIMEOUT_MS

    def test_重复请求只触发一次(self, coop):
        """幂等：第二次请求必须被忽略（不得双弹窗 / 双退出）"""
        shell, recorder = coop
        shell.report_dirty(1)

        assert shell.request_quit() is True
        assert shell.request_quit() is False
        assert shell.request_quit() is False

        assert recorder.topics == ["shell.quitRequested"], "重复请求又推了一次事件"

    def test_零脏直接退不弹确认框(self, coop):
        shell, recorder = coop
        calls = _no_quit_side_effects(shell)

        assert shell.request_quit() is True

        assert calls == [
            ("marks", {"clean_exit": True, "clear_drafts": False}),
            "quit",
        ], "零脏必须写 clean_exit=true 且不清草稿（偏好未知 ⇒ 保留）"
        assert recorder.events == [], "零脏不应推 shell.quitRequested"
        assert shell._quitting is True, "退出放行后 closeEvent 必须接受事件"
        assert shell.quit_state == QUIT_STATE_QUITTING

    def test_零脏且用户曾选不保留则清草稿(self, coop):
        shell, _recorder = coop
        calls = _no_quit_side_effects(shell)
        shell.set_retain_preference(False)

        shell.request_quit()

        assert calls[0] == ("marks", {"clean_exit": True, "clear_drafts": True})

    def test_无发布器时有脏直接走原生兜底(self, coop, monkeypatch):
        """桥不可用 ⇒ 渲染层不可能回执，必须直接弹原生兜底（不干等 5 s）"""
        shell, _recorder = coop
        shell._publish_event = None
        shell.report_dirty(1)
        calls = _no_quit_side_effects(shell)
        shown: list = []
        monkeypatch.setattr(shell, "_fallback_confirm_quit", lambda: shown.append(1))

        assert shell.request_quit() is True

        assert shown == [1], "无发布器时应直接走原生兜底"
        assert calls == [], "此时不应落盘 / 不应退出（等用户在兜底框选择）"


class Test超时兜底原生确认框:
    """5 s 无回执 ⇒ GUI 线程原生 `QMessageBox`：`退出 / 取消`，**默认「取消」**"""

    def test_超时触发兜底且默认按钮是取消(self, coop, monkeypatch):
        shell, _recorder = coop
        shell.report_dirty(1)
        shell.request_quit()
        calls = _no_quit_side_effects(shell)
        boxes = _install_fake_messagebox(monkeypatch, click_text="取消")

        shell._on_quit_timeout()  # 等价于定时器到期

        assert len(boxes) == 1, "超时应弹一次原生确认框"
        box = boxes[0]
        labels = [button["text"] for button in box.buttons]
        assert labels == ["退出", "取消"], f"按钮必须是 退出 / 取消，实际 {labels}"
        assert box.default["text"] == "取消", "默认按钮必须是「取消」"
        assert box.escape["text"] == "取消", "Esc 必须等价于「取消」"
        assert "未保存" in box.text and "草稿" in box.informative
        assert calls == [], "选「取消」不得落盘 / 不得退出"
        assert shell.quit_state == QUIT_STATE_IDLE
        assert shell._quitting is False, "取消后进程必须继续运行（AC-56）"
        assert shell._quit_timer.isActive() is False

    def test_超时兜底选退出则保留草稿并退出(self, coop, monkeypatch):
        shell, _recorder = coop
        shell.report_dirty(3)
        shell.request_quit()
        calls = _no_quit_side_effects(shell)
        _install_fake_messagebox(monkeypatch, click_text="退出")

        shell._on_quit_timeout()

        assert calls == [
            ("marks", {"clean_exit": False, "clear_drafts": False}),
            "quit",
        ], "兜底选「退出」⇒ clean_exit=false（保留草稿）+ 退出"

    def test_超时回调在非pending状态不弹框(self, coop, monkeypatch):
        """已取消 / 已退出后再收到超时回调必须什么都不做（幂等）"""
        shell, _recorder = coop
        boxes = _install_fake_messagebox(monkeypatch, click_text="取消")

        shell._on_quit_timeout()

        assert boxes == []
        assert shell.quit_state == QUIT_STATE_IDLE

    def test_弹框期间到达的回执被忽略(self, coop, monkeypatch):
        """状态机：`dialog` 期间渲染层回执一律忽略（由框内选择决定退出与否）"""
        shell, _recorder = coop
        shell._quit_state = QUIT_STATE_DIALOG
        calls = _no_quit_side_effects(shell)

        assert shell.confirm_quit(True) is False
        assert shell.cancel_quit() is False
        assert calls == []


class Test渲染层回执:
    """`confirm_quit(retain)` / `cancel_quit()`（§7.1 #11 / #12）"""

    def test_确认退出写干净标记并按retain清草稿(self, coop):
        shell, _recorder = coop
        shell.report_dirty(1)
        shell.request_quit()
        calls = _no_quit_side_effects(shell)

        assert shell.confirm_quit(False) is True

        assert calls == [
            ("marks", {"clean_exit": True, "clear_drafts": True}),
            "quit",
        ]
        assert shell._quitting is True

    def test_确认退出_保留时不第一次清草稿(self, coop):
        shell, _recorder = coop
        shell.report_dirty(1)
        shell.request_quit()
        calls = _no_quit_side_effects(shell)

        shell.confirm_quit(True)

        assert calls[0] == ("marks", {"clean_exit": True, "clear_drafts": False})

    def test_取消不退出且状态复位(self, coop):
        shell, recorder = coop
        shell.report_dirty(2)
        shell.request_quit()
        calls = _no_quit_side_effects(shell)

        assert shell.cancel_quit() is True

        assert shell.quit_state == QUIT_STATE_IDLE
        assert shell.quit_pending is False
        assert shell._quitting is False
        assert shell._quit_timer.isActive() is False
        assert calls == [], "取消路径不得落盘 / 不得退出（AC-56：内容不变）"
        assert shell.dirty_count == 2, "取消不得改动脏计数"
        assert recorder.topics == ["shell.quitRequested"], "取消不得再推事件"

    def test_未请求时的回执被忽略(self, coop):
        shell, _recorder = coop
        calls = _no_quit_side_effects(shell)

        assert shell.confirm_quit(True) is False
        assert shell.cancel_quit() is False
        assert calls == []

    def test_取消后可再次发起(self, coop):
        """状态机必须可复用（取消一次之后仍能正常退出）"""
        shell, recorder = coop
        shell.report_dirty(1)

        assert shell.request_quit() is True
        assert shell.cancel_quit() is True
        assert shell.request_quit() is True

        assert recorder.topics == ["shell.quitRequested", "shell.quitRequested"]

    def test_report_dirty过滤非法值(self, coop):
        shell, _recorder = coop
        shell.report_dirty(-5)
        assert shell.dirty_count == 0
        shell.report_dirty("不是数字")
        assert shell.dirty_count == 0
        shell.report_dirty(2.9)
        assert shell.dirty_count == 2


class Test退出标记落盘:
    """`_save_exit_marks()`：真实走 `scratch_service`（草稿目录已指向 tmp_path）"""

    @staticmethod
    def _pane(pane_id: str = "p1") -> dict:
        return {"id": pane_id, "title": "a.txt", "dirty": True}

    def test_确认退出写clean_exit并清草稿(self, coop):
        shell, _recorder = coop
        scratch_service.save_draft(self._pane(), "内容一")
        assert scratch_service.is_abnormal_exit() is True, "有草稿时索引应为 clean_exit=false"

        shell._save_exit_marks(clean_exit=True, clear_drafts=True)

        assert scratch_service.is_abnormal_exit() is False
        assert scratch_service.list_drafts()["entries"] == []

    def test_保留草稿时草稿仍在且标记为非干净退出(self, coop):
        shell, _recorder = coop
        scratch_service.save_draft(self._pane(), "内容一")

        shell._save_exit_marks(clean_exit=False, clear_drafts=False)

        assert scratch_service.is_abnormal_exit() is True, "保留草稿必须可被下次启动还原"
        assert [e["id"] for e in scratch_service.list_drafts()["entries"]] == ["p1"]

    def test_落盘失败只记日志不抛出(self, coop, monkeypatch):
        """退出流程不得被索引写失败卡死（红线：绝不清空内容）"""
        shell, _recorder = coop

        def _boom(*_args, **_kwargs):
            raise OSError("索引写失败")

        monkeypatch.setattr(scratch_service, "mark_clean_exit", _boom)

        shell._save_exit_marks(clean_exit=True, clear_drafts=False)  # 不应抛出


# ── 关闭语义（§6.5） ────────────────────────────────────────────────


class Test关闭语义:
    """`✕` 永远是最小化到托盘；只有退出放行后才真正关闭（AC-58）"""

    def test_退出握手进行中点击关闭按钮仍最小化到托盘(self, coop):
        shell, _recorder = coop
        tray = _StubTray()
        shell.tray = tray
        shell.report_dirty(1)
        shell.request_quit()
        event = _StubCloseEvent()

        shell.closeEvent(event)

        assert event.accepted is False, "✕ 在握手期间也必须是最小化到托盘"
        assert tray.notified == 1
        assert shell._quitting is False

    def test_退出放行后closeEvent接受(self, coop):
        shell, _recorder = coop
        shell._quitting = True
        event = _StubCloseEvent()

        shell.closeEvent(event)

        assert event.accepted is True


# ── 外部路径通知（§7.1 事件 #1 / §7.5） ──────────────────────────────


class Test外部路径通知:
    """`notify_external_paths()`：未就绪入队不丢，就绪后按序冲出"""

    def test_未就绪入队且不丢(self, coop):
        shell, recorder = coop

        assert shell.notify_external_paths(["C:/a.txt", "C:/b.txt"], SOURCE_CLI) == 0

        assert recorder.events == [], "未就绪不得直接推送（§7.5：入队）"
        assert shell._open_paths_queue == [(["C:/a.txt", "C:/b.txt"], SOURCE_CLI)]

    def test_就绪后按序冲出(self, coop):
        shell, recorder = coop
        shell.notify_external_paths(["C:/a.txt"], SOURCE_CLI)
        shell.notify_external_paths(["D:/b.txt"], SOURCE_SINGLE_INSTANCE)

        pushed = shell.set_renderer_ready(True)

        assert pushed == 2
        assert recorder.events == [
            ("editor.openPaths", {"paths": ["C:/a.txt"], "source": SOURCE_CLI}),
            (
                "editor.openPaths",
                {"paths": ["D:/b.txt"], "source": SOURCE_SINGLE_INSTANCE},
            ),
        ], "必须按到达顺序冲出（FIFO）"
        assert shell._open_paths_queue == []

    def test_未就绪时重复路径去重(self, coop):
        shell, recorder = coop
        shell.notify_external_paths(["C:/a.txt"], SOURCE_CLI)
        shell.notify_external_paths(["C:/a.txt", "C:/a.txt"], SOURCE_CLI)

        assert shell.flush_pending_open_paths() == 1
        assert recorder.payloads("editor.openPaths") == [
            {"paths": ["C:/a.txt"], "source": SOURCE_CLI}
        ]

    def test_队列上限截断(self, coop):
        shell, recorder = coop
        paths = [f"C:/f{i}.txt" for i in range(OPEN_PATHS_QUEUE_LIMIT + 16)]

        shell.notify_external_paths(paths, SOURCE_CLI)
        pushed = shell.flush_pending_open_paths()

        assert pushed == OPEN_PATHS_QUEUE_LIMIT
        payload = recorder.payloads("editor.openPaths")[0]
        assert len(payload["paths"]) == OPEN_PATHS_QUEUE_LIMIT

    def test_批内去重保持顺序(self, coop):
        shell, recorder = coop
        shell.set_renderer_ready(True)

        shell.notify_external_paths(["C:/b.txt", "C:/a.txt", "C:/b.txt"], SOURCE_CLI)

        assert recorder.payloads("editor.openPaths") == [
            {"paths": ["C:/b.txt", "C:/a.txt"], "source": SOURCE_CLI}
        ]

    def test_已就绪立即推送(self, coop):
        shell, recorder = coop
        shell.set_renderer_ready(True)

        assert shell.notify_external_paths(["C:/a.txt"], SOURCE_SINGLE_INSTANCE) == 1

        assert recorder.payloads("editor.openPaths") == [
            {"paths": ["C:/a.txt"], "source": SOURCE_SINGLE_INSTANCE}
        ]

    def test_无发布器时不丢待就绪后冲出(self, coop):
        """桥未注入时照样入队；注入发布器 + 就绪后必须一条不少地冲出"""
        shell, recorder = coop
        shell._publish_event = None

        assert shell.notify_external_paths(["C:/a.txt"], SOURCE_CLI) == 0
        assert shell.set_renderer_ready(True) == 0, "没有发布器时冲刷应为 0"

        shell._publish_event = recorder
        assert shell.flush_pending_open_paths() == 1
        assert recorder.payloads("editor.openPaths") == [
            {"paths": ["C:/a.txt"], "source": SOURCE_CLI}
        ]

    def test_未知来源按cli处理(self, coop):
        shell, recorder = coop
        shell.set_renderer_ready(True)

        shell.notify_external_paths(["C:/a.txt"], "未知来源")

        assert recorder.payloads("editor.openPaths")[0]["source"] == SOURCE_CLI

    def test_未显式回执时重推一次(self, coop):
        """§7.5 ③：`load_ui()` 之后页面还在加载的长尾 ⇒ 一次延迟重推"""
        shell, recorder = coop
        shell._ui_loaded = True  # 启发式就绪（未显式回执）

        shell.notify_external_paths(["C:/a.txt"], SOURCE_CLI)
        assert len(recorder.events) == 1
        assert shell._open_paths_retry_pending is True

        shell._on_open_paths_retry()

        assert len(recorder.events) == 2, "未收到显式就绪回执时必须重推一次"
        assert shell._open_paths_retry_pending is False

    def test_显式就绪后不再重推(self, coop):
        shell, recorder = coop
        shell.set_renderer_ready(True)

        shell.notify_external_paths(["C:/a.txt"], SOURCE_CLI)
        shell._on_open_paths_retry()

        assert len(recorder.events) == 1, "渲染层已回执就绪 ⇒ 不必重推"

    def test_set_renderer_ready_False回到未就绪(self, coop):
        shell, recorder = coop
        shell.set_renderer_ready(True)
        shell.set_renderer_ready(False)

        assert shell.notify_external_paths(["C:/a.txt"], SOURCE_CLI) == 0
        assert recorder.events == []
        assert shell.flush_pending_open_paths() == 1

    def test_未就绪日志不含路径(self, coop, caplog):
        """`AGENTS.md §9.4.13-14`：日志不得出现完整路径"""
        shell, _recorder = coop
        secret = "C:/非常机密的目录/绝密文件.txt"

        with caplog.at_level("DEBUG", logger=LOGGER_NAME):
            shell.notify_external_paths([secret], SOURCE_CLI)

        assert secret not in caplog.text
        assert "绝密文件" not in caplog.text


# ── 宿主默认动作处置（§4.2.2 / §4.2.3，R-5 实测结论） ────────────────


class Test宿主默认动作处置:
    """`view.pageAction(具名枚举成员)` 白名单；绝不遍历 `actions()`、绝不碰哨兵"""

    def test_页面级actions不存在(self):
        """R-5 实测：Qt 6.11 **没有** `QWebEnginePage.actions()`（伪代码不可执行）"""
        assert not hasattr(QWebEnginePage, "actions")

    def test_白名单按名解析并跳过本版本缺失项(self):
        resolved = resolve_host_actions()
        names = [name for name, _member in resolved]

        assert "SavePage" in names
        assert "ViewSource" in names
        assert "RequestClose" in names
        assert "InspectElement" in names
        assert "Print" not in names, "Qt 6.11 枚举里没有 Print（R-5），应跳过"
        assert "WebActionCount" not in names, "计数哨兵永远不进白名单"

    def test_哨兵绝不出现在解析结果里(self):
        sentinel = QWebEnginePage.WebAction.WebActionCount
        resolved = resolve_host_actions()

        assert sentinel not in [member for _name, member in resolved]
        assert len({member for _name, member in resolved}) == len(resolved), "不应重复"

    def test_假view调用序列(self, monkeypatch):
        """逐项 `setShortcuts([])` + `setEnabled(False)`（R-5 实测均有效）"""
        monkeypatch.setattr(
            web_shell_module,
            "HOST_ACTIONS_TO_DISABLE",
            ("SavePage", "Reload", "本版本不存在的动作"),
        )
        view = _FakeView()

        handled = disable_host_actions(view)

        assert handled == 2, "名字不存在的项必须跳过（不崩），其余照常处置"
        assert [action.log for action in view.actions_made] == [
            [("setShortcuts", []), ("setEnabled", False)],
            [("setShortcuts", []), ("setEnabled", False)],
        ]
        assert [action.enabled for action in view.actions_made] == [False, False]
        assert [action.shortcuts for action in view.actions_made] == [[], []]

    def test_不存在的动作不崩且不遍历actions(self, monkeypatch):
        """对哨兵 / 不存在名字取 action 必须被挡住（否则崩 0xC0000005）"""
        monkeypatch.setattr(
            web_shell_module,
            "HOST_ACTIONS_TO_DISABLE",
            ("WebActionCount", "根本没有这个动作"),
        )
        view = _FakeView()

        assert disable_host_actions(view) == 0
        assert view.received == [], "哨兵与不存在的名字都不得传给 pageAction"

    def test_单个动作不可用不影响其余(self, monkeypatch):
        fail_on = QWebEnginePage.WebAction.SavePage
        monkeypatch.setattr(
            web_shell_module,
            "HOST_ACTIONS_TO_DISABLE",
            ("SavePage", "Reload", "Stop"),
        )
        view = _FakeView(fail_on=(fail_on,))

        handled = disable_host_actions(view)

        assert handled == 2
        assert fail_on not in view.received

    def test_真实视图上处置成功且快捷键为空(self, shell):
        """真实 `QWebEngineView`：至少处理掉一个动作，且快捷键读回为空、已禁用"""
        handled = disable_host_actions(shell.view)

        assert handled >= 1
        save_page = shell.view.pageAction(QWebEnginePage.WebAction.SavePage)
        assert save_page.shortcuts() == []
        assert save_page.isEnabled() is False

    def test_构造WebShell时自动处置(self, shell):
        """`WebShell.__init__` 必须已经清理过宿主默认动作（AC-10 的落地前提）"""
        reload_action = shell.view.pageAction(QWebEnginePage.WebAction.Reload)
        assert reload_action.shortcuts() == []
        assert reload_action.isEnabled() is False

    def test_白名单不含哨兵名(self):
        assert "WebActionCount" not in HOST_ACTIONS_TO_DISABLE
        assert "NoWebAction" not in HOST_ACTIONS_TO_DISABLE


# ── 与 `bridge/editor.py` 的对接（§6.4 / §7.1 #13） ─────────────────


class _FakeEditorBridge:
    """`src.bridge.editor` 的替身：只提供退出握手相关的三个函数"""

    def __init__(self, dirty_count: int = 0) -> None:
        self.dirty_count = dirty_count
        self.pending_calls: list[bool] = []
        self.notify_calls: list[int] = []

    def get_quit_state(self) -> dict:
        return {"pending": bool(self.pending_calls and self.pending_calls[-1]),
                "dirtyCount": self.dirty_count}

    def set_quit_pending(self, pending: bool) -> None:
        self.pending_calls.append(bool(pending))

    def notify_quit_requested(self, timeout_ms: int) -> None:
        self.notify_calls.append(int(timeout_ms))


class Test与编辑器桥对接:
    """脏计数真相在 `bridge/editor.py`；`quit.pending` 由它读 `set_quit_pending`"""

    def test_脏计数取桥的值(self, coop, monkeypatch):
        shell, _recorder = coop
        bridge = _FakeEditorBridge(dirty_count=4)
        monkeypatch.setattr(WebShell, "_editor_bridge", staticmethod(lambda: bridge))

        assert shell._resolve_dirty_count() == 4

        assert shell.request_quit() is True
        assert shell.quit_state == QUIT_STATE_PENDING, "桥说有脏 ⇒ 必须走确认流程"

    def test_取两者较大值(self, coop, monkeypatch):
        """宁可多问一次，也不静默丢掉未保存内容"""
        shell, _recorder = coop
        shell.report_dirty(7)
        bridge = _FakeEditorBridge(dirty_count=2)
        monkeypatch.setattr(WebShell, "_editor_bridge", staticmethod(lambda: bridge))

        assert shell._resolve_dirty_count() == 7

    def test_请求时置位桥pending且只推一次事件(self, coop, monkeypatch):
        shell, recorder = coop
        bridge = _FakeEditorBridge(dirty_count=1)
        monkeypatch.setattr(WebShell, "_editor_bridge", staticmethod(lambda: bridge))

        shell.request_quit()

        assert bridge.pending_calls == [True], "必须让 quit.pending 反映本次握手"
        assert bridge.notify_calls == [], "不得再调桥的推送函数（会重复发事件）"
        assert recorder.topics == ["shell.quitRequested"]

    def test_取消后清桥pending(self, coop, monkeypatch):
        shell, _recorder = coop
        shell.report_dirty(1)
        bridge = _FakeEditorBridge(dirty_count=1)
        monkeypatch.setattr(WebShell, "_editor_bridge", staticmethod(lambda: bridge))
        shell.request_quit()

        shell.cancel_quit()

        assert bridge.pending_calls == [True, False]
        assert bridge.get_quit_state()["pending"] is False

    def test_无发布器时退回桥的推送函数(self, coop, monkeypatch):
        shell, _recorder = coop
        shell._publish_event = None
        shell.report_dirty(1)
        bridge = _FakeEditorBridge(dirty_count=1)
        monkeypatch.setattr(WebShell, "_editor_bridge", staticmethod(lambda: bridge))

        assert shell._notify_renderer_quit(1234) is True
        assert bridge.notify_calls == [1234]

    def test_桥不可用时不抛(self, coop, monkeypatch):
        shell, _recorder = coop
        monkeypatch.setattr(WebShell, "_editor_bridge", staticmethod(lambda: None))
        shell.report_dirty(1)

        # 不抛异常，且本地脏计数仍然生效
        assert shell._resolve_dirty_count() == 1
        assert shell._notify_renderer_quit(10) is True


class Test跨线程调度:
    """退出入口必须调度到 GUI 线程（`QApplication.quit()` 只能在 GUI 线程）"""

    def test_工作线程调用request_quit排队执行(self, coop, qapp):
        shell, recorder = coop
        shell.report_dirty(1)
        import threading

        result: list = []

        def _worker() -> None:
            result.append(shell.request_quit())

        thread = threading.Thread(target=_worker)
        thread.start()
        thread.join(timeout=5)

        assert result == [True], "工作线程调用应被接受（返回 True）"
        assert shell.quit_state == QUIT_STATE_IDLE, "工作线程里只 emit，不能直接改状态"
        assert recorder.events == [], "工作线程里不得直接推送（必须排队到 GUI 线程）"

        qapp.processEvents()  # 交付排队的槽调用

        assert shell.quit_state == QUIT_STATE_PENDING, "排队的请求应在 GUI 线程真正执行"
        assert recorder.topics == ["shell.quitRequested"]
        assert shell.cancel_quit() is True  # 收尾，避免影响后续用例

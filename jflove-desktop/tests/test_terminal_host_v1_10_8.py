"""
终端通道 / 本机会话的 v1.10.8 回归测试

钉住这轮的四个行为改变：

1. **输出合并**：16ms 内多次 `feed()` 只发一条二进制帧（TUI 每帧几十个 chunk，
   逐条发会把 GUI 线程拖垮）；
2. **退出通知**：会话结束必须给渲染层发 `exit` 控制帧（否则留一块静止黑屏）；
3. **终端类型**：宿主 `TERM=dumb` / 空值时，本机 shell 必须拿到 `xterm-256color`
   （v1.10.7 真机事故：dumb 下 TUI 无色、能力探测失真）；
4. **控制终端**：本机 PTY 子进程必须 `setsid + TIOCSCTTY`（否则 Ctrl+C 没有
   SIGINT —— 前台进程组不存在）。

通道部分用假 socket 替代 QWebSocket，只验证"发了什么"，不依赖真实网络。
"""

from __future__ import annotations

import json
import os

import pytest

from src.components import terminal_host as host_module
from src.services import local_terminal_service


class _FakeSocket:
    """记录 QWebSocket 的两个发送口（二进制 / 文本）"""

    def __init__(self) -> None:
        self.binary: list[bytes] = []
        self.texts: list[str] = []

    def sendBinaryMessage(self, payload) -> None:  # noqa: N802 - 对齐 Qt 命名
        self.binary.append(bytes(payload))

    def sendTextMessage(self, text: str) -> None:  # noqa: N802 - 对齐 Qt 命名
        self.texts.append(str(text))

    def close(self) -> None:  # noqa: N802 - 对齐 Qt 命名
        pass


class _FakeSession:
    """只实现通道用到的接口"""

    def __init__(self) -> None:
        self.cols = 120
        self.rows = 30
        self.written: list[bytes] = []
        self.sizes: list[tuple[int, int]] = []
        self.closed = False
        self.redraw = 0

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def resize(self, cols: int, rows: int) -> None:
        self.sizes.append((cols, rows))

    def close(self) -> None:
        self.closed = True

    def nudge_redraw(self) -> None:
        self.redraw += 1


@pytest.fixture()
def host(qapp):
    """真实 TerminalHost（监听 127.0.0.1 随机端口），用假 socket 收包"""
    instance = host_module.TerminalHost()
    yield instance
    instance.shutdown()


def _attach(host: host_module.TerminalHost, session_id: str) -> tuple[_FakeSession, _FakeSocket]:
    session = _FakeSession()
    token = host.register(session_id, session)
    sock = _FakeSocket()
    # 直接塞进 socket 表：跳过 WebSocket 握手，只验证发送内容
    host._sockets[session_id] = sock
    assert token
    return session, sock


def test_output_coalesced_into_single_frame(qapp, host) -> None:
    """16ms 窗口内的多次 feed 合并成一条二进制帧"""
    session, sock = _attach(host, "s1")
    host.feed("s1", b"abc")
    host.feed("s1", b"def")
    host.feed("s1", b"ghi")
    # 主动触发一次刷新（等价于定时器到点）
    host._flush_pending()
    assert sock.binary == [b"abcdefghi"], sock.binary
    session.closed = False


def test_output_targets_only_its_session(qapp, host) -> None:
    """多会话输出不能串台"""
    _, sock1 = _attach(host, "s1")
    _, sock2 = _attach(host, "s2")
    host.feed("s1", b"one")
    host.feed("s2", b"two")
    host._flush_pending()
    assert sock1.binary == [b"one"]
    assert sock2.binary == [b"two"]


def test_exit_frame_only_for_terminal_phases(qapp, host) -> None:
    """只有 closed / failed 才发 exit 帧；连接中的状态不该被当成退出"""
    session, sock = _attach(host, "s1")
    host.notify_status("s1", "connect", "正在连接")
    assert sock.texts == []
    host.notify_status("s1", "closed", "本机终端已退出")
    assert len(sock.texts) == 1
    payload = json.loads(sock.texts[0])
    assert payload["type"] == "exit"
    assert payload["message"] == "本机终端已退出"
    assert session.closed is False


def test_input_and_resize_and_redraw_messages(qapp, host) -> None:
    """上行消息：input 原样写入、resize 落到会话、redraw 转给 nudge_redraw"""
    session, _ = _attach(host, "s1")
    host._on_text("s1", json.dumps({"type": "input", "data": "ls\r"}))
    assert session.written == [b"ls\r"]
    host._on_text("s1", json.dumps({"type": "resize", "cols": 100, "rows": 40}))
    assert session.sizes == [(100, 40)]
    host._on_text("s1", json.dumps({"type": "redraw"}))
    assert session.redraw == 1
    # 坏消息不炸通道
    host._on_text("s1", "not-json")
    host._on_text("s1", json.dumps({"type": "unknown"}))
    assert session.written == [b"ls\r"]


def test_close_message_closes_session(qapp, host) -> None:
    """上行 close 会真正关闭会话并回收通道状态"""
    session, _ = _attach(host, "s1")
    host._on_text("s1", json.dumps({"type": "close"}))
    assert session.closed is True
    assert "s1" not in host._sessions
    assert "s1" not in host._sockets


def test_local_env_forces_usable_term(monkeypatch) -> None:
    """宿主 TERM=dumb / 空值 → 本机 shell 拿到 xterm-256color + 真彩色声明"""
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    session = local_terminal_service.LocalSession(None)
    env = session._env()
    assert env["TERM"] == local_terminal_service.DEFAULT_TERM
    assert env["COLORTERM"] == local_terminal_service.DEFAULT_COLORTERM
    assert env["TERM_PROGRAM"] == local_terminal_service.TERM_PROGRAM


def test_local_env_keeps_explicit_term(monkeypatch) -> None:
    """用户显式配置的 TERM 不被覆盖（尊重 profile 的自定义环境变量）"""
    monkeypatch.setenv("TERM", "screen-256color")
    session = local_terminal_service.LocalSession({"env_json": "{}"})
    assert session._env()["TERM"] == "screen-256color"


def test_local_env_profile_override_wins(monkeypatch) -> None:
    """profile 的 env_json 是最终否决权"""
    monkeypatch.setenv("TERM", "dumb")
    session = local_terminal_service.LocalSession(
        {"env_json": json.dumps({"TERM": "xterm-kitty"})}
    )
    assert session._env()["TERM"] == "xterm-kitty"


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY 才有的行为")
def test_local_session_creates_controlling_terminal(tmp_path, qapp) -> None:
    """子进程必须把 PTY 设成控制终端（Ctrl+C 才有 SIGINT）"""
    session = local_terminal_service.LocalSession(
        {"shell_path": "/bin/sh", "cwd": str(tmp_path)},
        cols=80,
        rows=24,
    )
    chunks: list[bytes] = []
    session.on_output = chunks.append
    session.open()
    try:
        # `stty` 在无控制终端时会报 "not a tty"；有控制终端时能读到尺寸并退出 0
        session.write(b"stty size > ctl.txt 2>&1; ps -o tpgid= -p $$ >> ctl.txt\n")
        import time

        for _ in range(50):
            if os.path.exists(os.path.join(str(tmp_path), "ctl.txt")):
                break
            time.sleep(0.1)
        content = open(os.path.join(str(tmp_path), "ctl.txt"), encoding="utf-8").read()
        rows, cols = content.split()[0:2]
        assert (int(rows), int(cols)) == (24, 80)
        # 有控制终端时 tpgid 是正数（前台进程组的 pgid），-1 表示没有
        tpgid = content.split()[-1]
        assert int(tpgid) > 0
    finally:
        session.close()


def test_nudge_redraw_exists_on_local_and_ssh() -> None:
    """"重绘"按钮依赖两个会话实现同一个方法名"""
    from src.services.ssh_session import SshSession

    assert callable(getattr(local_terminal_service.LocalSession, "nudge_redraw", None))
    assert callable(getattr(SshSession, "nudge_redraw", None))


def test_clipboard_bridge_runs_on_gui_thread() -> None:
    """QClipboard 不是线程安全的：两个剪贴板方法必须登记进 MAIN_THREAD_METHODS"""
    from src.bridge import remote as remote_bridge

    assert ("local_term", "clipboard_read") in remote_bridge.MAIN_THREAD_METHODS
    assert ("local_term", "clipboard_write") in remote_bridge.MAIN_THREAD_METHODS
    assert "clipboard_read" in remote_bridge.HANDLERS["local_term"]
    assert "clipboard_write" in remote_bridge.HANDLERS["local_term"]

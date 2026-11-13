"""
终端 WebSocket 通道与桥白名单（v1.10.0）

不启动完整应用，只钉住三件事：
1. `TerminalHost` 能在 127.0.0.1 起监听，并给每个会话发随机 token；
2. 桥的 `remote` / `local_term` 方法都登记在白名单里（漏登记 = 前端调用直接被拒）；
3. 凭据不会被 `remote.connections_get` 回传给渲染层。
"""

from __future__ import annotations

import pytest

from src.bridge import bridge as bridge_module
from src.bridge import remote as remote_bridge
from src.components.terminal_host import TerminalHost


class _DummySession:
    """最小会话替身（只记录调用）"""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.resized: list[tuple[int, int]] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def resize(self, cols: int, rows: int) -> None:
        self.resized.append((cols, rows))

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def host(qapp):
    """在 GUI 线程创建终端通道"""
    instance = TerminalHost()
    yield instance
    instance.shutdown()


def test_host_listens_on_localhost(host) -> None:
    """监听端口有效，且只在 127.0.0.1"""
    assert host.port > 0
    assert host._server is not None
    assert host._server.serverAddress().toString() in ("127.0.0.1", "localhost")


def test_register_returns_random_token(host) -> None:
    """每个会话一个 32 位 token，且彼此不同"""
    session = _DummySession()
    token_a = host.register("sid-a", session)
    token_b = host.register("sid-b", session)
    assert len(token_a) == 32
    assert token_a != token_b


def test_bridge_whitelist_has_remote_and_local_term() -> None:
    """桥白名单必须包含两个新服务及其方法"""
    assert "remote" in bridge_module.WHITELIST
    assert "local_term" in bridge_module.WHITELIST
    for method in (
        "connections_list",
        "connections_get",
        "connections_create",
        "connections_update",
        "connections_delete",
        "session_open",
        "session_close",
        "hostkey_confirm",
        "sftp_list",
        "sftp_download",
        "sftp_upload",
    ):
        assert method in bridge_module.WHITELIST["remote"], f"remote.{method} 未登记"
    for method in ("detect_shells", "profiles_list", "open"):
        assert method in bridge_module.WHITELIST["local_term"], f"local_term.{method} 未登记"


def test_connections_get_strips_secret(monkeypatch) -> None:
    """`remote.connections_get` 只回 has_* 与指纹，不带密码/私钥原文"""
    fake = {
        "id": 1,
        "name": "web-01",
        "host": "192.168.1.21",
        "port": 22,
        "username": "tanjun",
        "auth_type": "password",
        "password": "p@ss",
        "private_key": "-----BEGIN-----",
        "passphrase": "pp",
        "ssh_group_id": 0,
        "jump_connection_id": 0,
        "terminal_type": "xterm-256color",
        "charset": "UTF-8",
        "init_command": "cd /opt",
        "host_key_fingerprint": "",
        "has_password": True,
        "has_private_key": False,
    }
    monkeypatch.setattr(
        remote_bridge.remote_service, "get_connection", lambda _id: dict(fake)
    )
    result = remote_bridge.HANDLERS["remote"]["connections_get"]({"id": 1})
    connection = result["connection"]
    assert "password" not in connection
    assert "private_key" not in connection
    assert connection["has_password"] is True
    assert connection["init_command"] == "cd /opt"


def test_websocket_roundtrip(host, qapp) -> None:
    """真实 WebSocket 往返：下行字节到达客户端、上行 JSON 写入会话"""
    import json
    import time

    from PySide6.QtCore import QUrl
    from PySide6.QtWebSockets import QWebSocket

    session = _DummySession()
    token = host.register("sid-ws", session)

    received: list[bytes] = []
    client = QWebSocket()
    client.binaryMessageReceived.connect(lambda msg: received.append(bytes(msg)))
    client.open(QUrl(f"ws://127.0.0.1:{host.port}/term/sid-ws?token={token}"))

    for _ in range(100):
        qapp.processEvents()
        if client.isValid():
            break
        time.sleep(0.01)
    assert client.isValid(), "WebSocket 未连接成功"

    host.feed("sid-ws", b"hello-terminal")
    for _ in range(100):
        qapp.processEvents()
        if received:
            break
        time.sleep(0.01)
    assert received == [b"hello-terminal"]

    client.sendTextMessage(json.dumps({"type": "resize", "cols": 120, "rows": 40}))
    for _ in range(100):
        qapp.processEvents()
        if session.resized:
            break
        time.sleep(0.01)
    assert session.resized == [(120, 40)]

    client.sendTextMessage(json.dumps({"type": "input", "data": "ls\r"}))
    for _ in range(100):
        qapp.processEvents()
        if session.written:
            break
        time.sleep(0.01)
    assert session.written == [b"ls\r"]

    client.close()

"""
SFTP 取消语义：目录取消 vs 传输取消（v1.10.13）

- `sftp_abort`：只丢弃会话级 client（目录加载），**不能**误伤正在进行的上传/下载；
- `sftp_cancel`：取消传输，关闭会话级 + 全部传输专用 client。

性质：只测桥层字典/会话协作，不连网络。
"""

from __future__ import annotations

import pytest

from src.bridge import remote as remote_bridge


class _FakeClient:
    """记录 close 调用的假 SFTP client"""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    """记录 drop_sftp 调用的假会话"""

    def __init__(self) -> None:
        self.dropped = 0

    def drop_sftp(self) -> None:
        self.dropped += 1


@pytest.fixture()
def clean_bridge():
    """测试前后清空桥的会话/SFTP 字典，避免污染其它用例"""
    remote_bridge._sftp_clients.clear()
    remote_bridge._sftp_transfer_clients.clear()
    remote_bridge._sessions.clear()
    yield
    remote_bridge._sftp_clients.clear()
    remote_bridge._sftp_transfer_clients.clear()
    remote_bridge._sessions.clear()


def test_abort_only_drops_directory_client(clean_bridge) -> None:
    """取消目录加载：关会话级 client，但传输专用 client 必须保持可用"""
    session = _FakeSession()
    cached = _FakeClient()
    transfer = _FakeClient()
    remote_bridge._sessions["s1"] = session
    remote_bridge._sftp_clients["s1"] = cached
    remote_bridge._sftp_transfer_clients["s1"] = {transfer}

    result = remote_bridge._h_sftp_abort({"session_id": "s1"})

    assert result == {"ok": True}
    assert cached.closed is True
    assert transfer.closed is False
    assert transfer in remote_bridge._sftp_transfer_clients["s1"]
    assert session.dropped == 1


def test_cancel_closes_directory_and_transfer_clients(clean_bridge) -> None:
    """取消传输：会话级 + 全部传输专用 client 都关闭"""
    session = _FakeSession()
    cached = _FakeClient()
    transfer_a = _FakeClient()
    transfer_b = _FakeClient()
    remote_bridge._sessions["s1"] = session
    remote_bridge._sftp_clients["s1"] = cached
    remote_bridge._sftp_transfer_clients["s1"] = {transfer_a, transfer_b}

    result = remote_bridge._h_sftp_cancel({"session_id": "s1"})

    assert result == {"ok": True}
    assert cached.closed is True
    assert transfer_a.closed is True
    assert transfer_b.closed is True
    assert "s1" not in remote_bridge._sftp_transfer_clients
    assert session.dropped == 1

"""
SFTP 传输通道回退（v1.10.24）

用户实测（2026-11-27）：远程连接里点「下载」无反应。
根因：v1.10.13 起传输改为新开一条 SFTP subsystem 通道；部分服务器 / 堡垒机限制
单连接并发会话（OpenSSH `MaxSessions 1`），第二条通道被拒绝
（`ChannelException(2, 'Connect failed')`）；旧实现又在启动传输前同步取 client，
异常直接抛回桥，前端 `downloadOne` 没有 catch ⇒ 不建行、不报错 =「无反应」。

判据：
1. 独立通道失败 ⇒ 回退会话级 client 仍能完成传输，且**共享 client 不被关闭**；
2. 两条路都失败 ⇒ 发 `sftp_failed` 事件，带文件名与可读原因（前端能建失败行）；
3. 独立通道成功 ⇒ 传输结束关闭专用 client，会话级 client 不受影响。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from src.bridge import remote as remote_bridge
from src.services.ssh_session import SshError


class _FakeSftp:
    """最小 SFTP client：只实现 get / put / close，并记录调用"""

    def __init__(self, payload: bytes = b"hello-jflove") -> None:
        self.payload = payload
        self.closed = False
        self.get_calls: list[tuple[str, str]] = []
        self.put_calls: list[tuple[str, str]] = []

    def get(self, remote_path: str, local_path: str, callback=None) -> None:  # noqa: ANN001
        Path(local_path).write_bytes(self.payload)
        self.get_calls.append((remote_path, local_path))
        if callback is not None:
            callback(len(self.payload), len(self.payload))

    def put(self, local_path: str, remote_path: str, callback=None) -> None:  # noqa: ANN001
        self.put_calls.append((local_path, remote_path))
        if callback is not None:
            callback(1, 1)

    def listdir_attr(self, _path: str):
        raise AssertionError("等锁超时时不应真正调用 listdir")

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    """dedicated_fail=True 时模拟「服务器拒绝第二条通道」"""

    def __init__(self, dedicated_fail: bool, dedicated: _FakeSftp | None = None) -> None:
        self.dedicated_fail = dedicated_fail
        self.dedicated = dedicated or _FakeSftp()
        self.shared = _FakeSftp()
        self.released: list[_FakeSftp] = []

    def open_sftp(self, cached: bool = True) -> _FakeSftp:
        if cached:
            return self.shared
        if self.dedicated_fail:
            raise SshError("无法打开 SFTP 通道：ChannelException(2, 'Connect failed')")
        return self.dedicated

    def release_sftp(self, client: _FakeSftp) -> None:
        self.released.append(client)


@pytest.fixture()
def clean_state(monkeypatch):
    """清空桥的会话/client/锁字典，并捕获事件"""
    events: list[tuple[str, dict]] = []
    lock = threading.Lock()

    def fake_publish(topic: str, payload: dict) -> None:
        with lock:
            events.append((topic, payload))

    monkeypatch.setattr(remote_bridge, "_publish", fake_publish)
    monkeypatch.setattr(remote_bridge, "_sessions", {})
    monkeypatch.setattr(remote_bridge, "_sftp_clients", {})
    monkeypatch.setattr(remote_bridge, "_sftp_transfer_clients", {})
    monkeypatch.setattr(remote_bridge, "_sftp_shared_transfers", {})
    monkeypatch.setattr(remote_bridge, "_sftp_locks", {})
    yield events


def _wait_events(events: list[tuple[str, dict]], topic: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for name, payload in events:
            if name == topic:
                return payload
        time.sleep(0.01)
    raise AssertionError(f"未收到 {topic} 事件，实际：{events}")


def test_download_falls_back_to_shared_client(clean_state, tmp_path: Path) -> None:
    """独立通道被拒时回退会话级 client：文件仍要下载成功，共享 client 不关闭"""
    session = _FakeSession(dedicated_fail=True)
    remote_bridge._sessions["s1"] = session

    target = tmp_path / "out.bin"
    result = remote_bridge._h_sftp_download(
        {
            "session_id": "s1",
            "remote_path": "/remote/dir/out.bin",
            "local_path": str(target),
            "bytes_total": 12,
        }
    )
    assert result["transfer_id"]

    done = _wait_events(clean_state, "remote.sftp_done")
    assert done["name"] == "out.bin"
    assert done["direction"] == "download"
    assert target.read_bytes() == session.shared.payload
    # 共享 client 属于会话缓存，传输后**不得关闭**
    assert session.shared.closed is False
    assert session.dedicated.closed is False  # 根本没开出来


def test_download_failure_is_reported_with_name(clean_state, tmp_path: Path) -> None:
    """两条路都失败也必须发事件（带文件名），前端才能建失败行"""

    class _BrokenSession(_FakeSession):
        def open_sftp(self, cached: bool = True) -> _FakeSftp:
            raise SshError("无法打开 SFTP 通道：ChannelException(2, 'Connect failed')")

    remote_bridge._sessions["s1"] = _BrokenSession(dedicated_fail=True)
    remote_bridge._h_sftp_download(
        {
            "session_id": "s1",
            "remote_path": "/remote/dir/broken.txt",
            "local_path": str(tmp_path / "broken.txt"),
            "bytes_total": 7,
        }
    )
    failed = _wait_events(clean_state, "remote.sftp_failed")
    assert failed["name"] == "broken.txt"
    assert failed["direction"] == "download"
    assert "并发会话" in failed["message"]


def test_dedicated_client_closed_after_transfer(clean_state, tmp_path: Path) -> None:
    """独立通道可用时维持原语义：传输结束关闭专用 client，共享 client 不受影响"""
    session = _FakeSession(dedicated_fail=False)
    remote_bridge._sessions["s1"] = session

    target = tmp_path / "dedicated.bin"
    remote_bridge._h_sftp_download(
        {
            "session_id": "s1",
            "remote_path": "/remote/dedicated.bin",
            "local_path": str(target),
            "bytes_total": 12,
        }
    )
    _wait_events(clean_state, "remote.sftp_done")
    assert target.read_bytes() == session.dedicated.payload
    assert session.dedicated.closed is True
    assert session.shared.closed is False
    assert "s1" not in remote_bridge._sftp_transfer_clients


def test_upload_falls_back_to_shared_client(clean_state) -> None:
    """上传同样要回退：受限服务器上「下载能用、上传不能用」是同一根因"""
    session = _FakeSession(dedicated_fail=True)
    remote_bridge._sessions["s1"] = session

    remote_bridge._h_sftp_upload(
        {
            "session_id": "s1",
            "local_path": "/local/upload.txt",
            "remote_path": "/remote/upload.txt",
            "bytes_total": 1,
        }
    )
    done = _wait_events(clean_state, "remote.sftp_done")
    assert done["name"] == "upload.txt"
    assert done["direction"] == "upload"
    assert session.shared.put_calls == [("/local/upload.txt", "/remote/upload.txt")]
    assert session.shared.closed is False


def test_shared_transfer_holds_lock_until_finished(clean_state) -> None:
    """共享 client 传输期间必须持锁（paramiko 不支持并发同步请求）"""
    session = _FakeSession(dedicated_fail=True)
    remote_bridge._sessions["s1"] = session
    lock = remote_bridge._sftp_lock("s1")

    gate = threading.Event()
    entered = threading.Event()
    original_get = session.shared.get

    def slow_get(remote_path: str, local_path: str, callback=None) -> None:  # noqa: ANN001
        entered.set()
        gate.wait(1.0)
        original_get(remote_path, local_path, callback)

    session.shared.get = slow_get  # type: ignore[method-assign]
    remote_bridge._h_sftp_download(
        {
            "session_id": "s1",
            "remote_path": "/remote/slow.bin",
            "local_path": "/tmp/jf-slow.bin",
        }
    )
    assert entered.wait(1.0), "传输线程未进入 get（可能没走到共享 client 回退）"
    # 传输线程已拿到锁：主线程尝试获取应超时
    acquired = lock.acquire(timeout=0.2)
    if acquired:
        lock.release()
    gate.set()
    _wait_events(clean_state, "remote.sftp_done")
    assert acquired is False, "共享 client 传输期间必须持有 SFTP 锁"


def test_abort_does_not_close_shared_client_during_transfer(clean_state) -> None:
    """
    v1.10.13 语义：取消**目录加载**不能误伤传输。

    受限服务器上传输回退到会话级 client；若此时 `sftp_abort` 关闭该 client，
    正在下载的文件会直接失败。计数 > 0 时必须跳过关闭。
    """
    session = _FakeSession(dedicated_fail=True)
    remote_bridge._sessions["s1"] = session
    entered = threading.Event()
    gate = threading.Event()
    original_get = session.shared.get

    def slow_get(remote_path: str, local_path: str, callback=None) -> None:  # noqa: ANN001
        entered.set()
        gate.wait(1.0)
        original_get(remote_path, local_path, callback)

    session.shared.get = slow_get  # type: ignore[method-assign]
    remote_bridge._h_sftp_download(
        {
            "session_id": "s1",
            "remote_path": "/remote/abort.bin",
            "local_path": "/tmp/jf-abort.bin",
            "bytes_total": 12,
        }
    )
    assert entered.wait(1.0)
    result = remote_bridge._h_sftp_abort({"session_id": "s1"})
    assert result == {"ok": False}
    assert session.shared.closed is False

    gate.set()
    _wait_events(clean_state, "remote.sftp_done")
    # 传输结束后计数归零，abort 恢复正常语义（可丢弃会话级 client）
    assert remote_bridge._sftp_shared_transfers.get("s1") is None
    assert remote_bridge._h_sftp_abort({"session_id": "s1"}) == {"ok": True}
    assert session.shared.closed is True


def test_cancel_before_channel_opens_aborts(clean_state, tmp_path: Path) -> None:
    """取消发生在「独立通道还在打开」窗口：打开后必须发现代次变化并中止"""
    entered = threading.Event()
    release = threading.Event()

    class _BlockingSession(_FakeSession):
        def open_sftp(self, cached: bool = True) -> _FakeSftp:
            if cached:
                return self.shared
            entered.set()
            release.wait(1.0)
            return self.dedicated

    session = _BlockingSession(dedicated_fail=False)
    remote_bridge._sessions["s1"] = session
    remote_bridge._h_sftp_download(
        {
            "session_id": "s1",
            "remote_path": "/remote/cancel.bin",
            "local_path": str(tmp_path / "cancel.bin"),
            "bytes_total": 12,
        }
    )
    assert entered.wait(1.0), "未进入通道打开阶段"
    remote_bridge._h_sftp_cancel({"session_id": "s1"})
    release.set()

    failed = _wait_events(clean_state, "remote.sftp_failed")
    assert failed["message"] == "已取消传输"
    assert session.dedicated.get_calls == []
    assert session.dedicated.closed is True


def test_cancel_queued_shared_transfer_is_skipped(clean_state, tmp_path: Path) -> None:
    """批量在受限服务器上排队时，取消必须让后续传输在真正读写前中止"""
    session = _FakeSession(dedicated_fail=True)
    remote_bridge._sessions["s1"] = session
    entered = threading.Event()
    gate = threading.Event()
    original_get = session.shared.get

    def slow_get(remote_path: str, local_path: str, callback=None) -> None:  # noqa: ANN001
        entered.set()
        gate.wait(1.0)
        original_get(remote_path, local_path, callback)

    session.shared.get = slow_get  # type: ignore[method-assign]
    # 第一笔：进入共享 client 并持锁
    remote_bridge._h_sftp_download(
        {
            "session_id": "s1",
            "remote_path": "/remote/first.bin",
            "local_path": str(tmp_path / "first.bin"),
        }
    )
    assert entered.wait(1.0)
    # 第二笔：排在同一个锁后
    remote_bridge._h_sftp_download(
        {
            "session_id": "s1",
            "remote_path": "/remote/second.bin",
            "local_path": str(tmp_path / "second.bin"),
        }
    )
    # 取消：关闭 shared client + 推进代次
    remote_bridge._h_sftp_cancel({"session_id": "s1"})
    gate.set()

    messages = []
    deadline = time.time() + 3.0
    while time.time() < deadline and len(messages) < 2:
        for topic, payload in clean_state:
            if topic == "remote.sftp_failed":
                messages.append(payload)
        time.sleep(0.01)
    assert len(messages) == 2, messages
    assert all(item["message"] == "已取消传输" for item in messages), messages
    assert len(session.shared.get_calls) == 1  # 第二笔没有真正 get


def test_list_waits_for_lock_with_timeout(clean_state, monkeypatch, tmp_path: Path) -> None:
    """共享传输长期持锁时，目录加载必须有上限等待并给可读原因（不能永久卡住）"""
    monkeypatch.setattr(remote_bridge.ssh_session_module, "SFTP_OP_TIMEOUT", 0.2)
    session = _FakeSession(dedicated_fail=True)
    remote_bridge._sessions["s1"] = session
    entered = threading.Event()
    gate = threading.Event()
    original_get = session.shared.get

    def slow_get(remote_path: str, local_path: str, callback=None) -> None:  # noqa: ANN001
        entered.set()
        gate.wait(1.0)
        original_get(remote_path, local_path, callback)

    session.shared.get = slow_get  # type: ignore[method-assign]
    remote_bridge._h_sftp_download(
        {
            "session_id": "s1",
            "remote_path": "/remote/busy.bin",
            "local_path": str(tmp_path / "busy.bin"),
        }
    )
    assert entered.wait(1.0)
    with pytest.raises(RuntimeError, match="有文件正在传输"):
        remote_bridge._h_sftp_list({"session_id": "s1", "path": "."})
    gate.set()
    _wait_events(clean_state, "remote.sftp_done")

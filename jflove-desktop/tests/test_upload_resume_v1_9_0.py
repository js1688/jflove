"""
v1.9.0 桌面端上传断点续传 / 异步合并单测（需求 F46 / F47，设计 §3.3）

覆盖：
  1. `init` 携带 `client_type="desktop"`，分片 512KB；
  2. `received_chunks` 是续传点的**唯一权威来源**，本地记录仅兜底；
  3. `restart_required` 时从第 0 片重传；
  4. `complete` 携带 `async_mode=true`，轮询 `upload/progress`
     按 queued / merging / done / failed 收尾；
  5. 合并缺片自动补传一次；校验失败上报中文且**不 DELETE 会话**；
  6. 分片失败指数退避 5 次；网络失败保留会话与本地续传记录；
  7. 只有用户取消才清理服务端会话。

所有网络调用都用替身，不依赖真实服务端。
"""

from __future__ import annotations

import os

import pytest
import requests

from src.services import file_service
from src.utils.http_client import ApiError


@pytest.fixture()
def local_file(tmp_path):
    """3 个分片（2*512KB + 1KB），足够验证续传跳过逻辑"""
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * (2 * file_service.CHUNK_SIZE + 1024))
    return str(path)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """隔离哈希缓存与续传记录，避免用例互相影响"""
    monkeypatch.setattr(file_service, "_HASH_CACHE", {})
    monkeypatch.setattr(
        file_service, "_RESUME_FILE", str(tmp_path / "upload_resume.json"),
    )


def _install_backend(
    monkeypatch,
    init_resp,
    chunk_impl=None,
    complete_impl=None,
    progress_impl=None,
    delete_impl=None,
) -> dict:
    """
    安装后端替身，返回调用记录。

    :param init_resp: dict 或 callable(data)
    :param chunk_impl: callable(data)（默认成功）
    :param complete_impl: callable(data)（默认 done + path）
    :param progress_impl: callable(调用序号)（默认 done）
    :param delete_impl: callable(endpoint)
    """
    calls: dict = {
        "init": [],
        "chunk": [],
        "complete": [],
        "progress": 0,
        "delete": [],
    }

    def fake_post(endpoint, data=None, timeout=30):
        if endpoint.endswith("/upload/init"):
            calls["init"].append(data or {})
            return init_resp(data) if callable(init_resp) else init_resp
        if endpoint.endswith("/upload/chunk"):
            calls["chunk"].append(data["chunk_index"])
            if chunk_impl is not None:
                return chunk_impl(data)
            return {}
        if endpoint.endswith("/upload/complete"):
            calls["complete"].append(data or {})
            if complete_impl is not None:
                return complete_impl(data)
            return {"state": "done", "path": "/remote/big.bin"}
        if endpoint.endswith("/upload/progress"):
            calls["progress"] += 1
            if progress_impl is not None:
                return progress_impl(calls["progress"])
            return {"state": "done", "path": "/remote/big.bin"}
        raise AssertionError(f"未预期的接口：{endpoint}")

    def fake_delete(endpoint, data=None, timeout=30):
        calls["delete"].append(endpoint)
        if delete_impl is not None:
            return delete_impl(endpoint)
        return {}

    monkeypatch.setattr(file_service.http_client, "post", fake_post)
    monkeypatch.setattr(file_service.http_client, "delete", fake_delete)
    return calls


def _resume_key(local_file: str) -> str:
    """计算测试文件的续传键"""
    return file_service._resume_key(
        1, "",
        os.path.basename(local_file),
        os.path.getsize(local_file),
        os.path.getmtime(local_file),
    )


def test_init携带client_type且分片为512KB(monkeypatch, local_file):
    """init 必带 client_type；total_chunks 按 512KB 分片计算"""
    calls = _install_backend(monkeypatch, {"upload_id": "u1", "received_chunks": []})

    file_service.upload_file(1, "", local_file)

    payload = calls["init"][0]
    assert payload["client_type"] == "desktop"
    assert payload["total_chunks"] == 3
    assert file_service.CHUNK_SIZE == 512 * 1024
    assert calls["complete"][0]["async_mode"] is True


def test_服务端received_chunks是续传点唯一来源(monkeypatch, local_file):
    """本地记录说 0/1 已传，但服务端说只收到 0 ⇒ 只补 1、2，不重发 0"""
    file_service._resume_update(_resume_key(local_file), "u-old", {0, 1})
    calls = _install_backend(
        monkeypatch,
        {"upload_id": "u1", "received_chunks": [0], "resumed": True},
    )

    file_service.upload_file(1, "", local_file)

    assert calls["chunk"] == [1, 2]
    assert file_service._resume_get(_resume_key(local_file)) is None


def test_旧服务端无received_chunks时本地记录兜底(monkeypatch, local_file):
    """兼容旧服务端：init 只返回 upload_id 时使用本地已发送记录"""
    file_service._resume_update(_resume_key(local_file), "u-local", {0})
    calls = _install_backend(monkeypatch, {"upload_id": "u-local"})

    file_service.upload_file(1, "", local_file)

    assert calls["chunk"] == [1, 2]


def test_restart_required时从第0片重传(monkeypatch, local_file):
    """上一次会话已失败：即使服务端返回 received_chunks，也必须从 0 开始"""
    calls = _install_backend(
        monkeypatch,
        {
            "upload_id": "u-new",
            "received_chunks": [0, 1],
            "restart_required": True,
        },
    )

    file_service.upload_file(1, "", local_file)

    assert calls["chunk"] == [0, 1, 2]


def test_异步complete轮询queued_merging_done(monkeypatch, local_file):
    """complete 立即返回 merging，随后 queued → merging → done 收尾"""
    size = os.path.getsize(local_file)
    reported: list[tuple[int, int, str]] = []

    def progress(n: int) -> dict:
        if n == 1:
            return {"state": "queued", "poll_interval_s": 0.01}
        if n == 2:
            return {
                "state": "merging", "processed": 2, "file_size": size,
                "poll_interval_s": 0.01,
            }
        return {"state": "done", "path": "/remote/big.bin"}

    calls = _install_backend(
        monkeypatch,
        {"upload_id": "u-async", "received_chunks": []},
        complete_impl=lambda _data: {
            "state": "merging", "upload_id": "u-async", "poll_interval_s": 0.01,
        },
        progress_impl=progress,
    )

    result = file_service.upload_file(
        1, "", local_file,
        progress_callback=lambda d, t, p: reported.append((d, t, p)),
    )

    assert result == "/remote/big.bin"
    assert calls["progress"] >= 3
    finalizing = [item for item in reported if item[2] == "finalizing"]
    assert finalizing[0] == (0, size, "finalizing")
    assert any(done == 2 and total == size for done, total, _ in finalizing)


def test_合并失败上报中文且不删除会话(monkeypatch, local_file):
    """hash_mismatch ⇒ 抛 ApiError 中文原因；服务端会话/分片保留供重试"""
    calls = _install_backend(
        monkeypatch,
        {"upload_id": "u-fail", "received_chunks": []},
        complete_impl=lambda _data: {
            "state": "failed", "error": "hash_mismatch",
        },
    )

    with pytest.raises(ApiError) as excinfo:
        file_service.upload_file(1, "", local_file)

    assert excinfo.value.status_code == 409
    assert "校验" in excinfo.value.detail
    assert calls["delete"] == [], "合并失败不得清理服务端会话"
    assert file_service._resume_get(_resume_key(local_file)) is not None


def test_合并缺片自动补传一次(monkeypatch, local_file):
    """progress 报 missing_chunks=[1] ⇒ 只补第 1 片后再次 complete"""
    complete_states = iter([
        {"state": "merging", "poll_interval_s": 0.01},
        {"state": "done", "path": "/remote/big.bin"},
    ])

    calls = _install_backend(
        monkeypatch,
        {"upload_id": "u-missing", "received_chunks": []},
        complete_impl=lambda _data: next(complete_states),
        progress_impl=lambda _n: {
            "state": "failed", "error": "missing_chunks", "missing_chunks": [1],
        },
    )

    assert file_service.upload_file(1, "", local_file) == "/remote/big.bin"
    # 第一轮全传 + 第二轮只补第 1 片（不再重复发送 0/2）
    assert calls["chunk"] == [0, 1, 2, 1]
    assert calls["delete"] == []


def test_分片失败指数退避5次(monkeypatch, local_file):
    """同一分片连续瞬态失败：共尝试 5 次，退避 1/2/4/8 秒"""
    sleeps: list[float] = []
    monkeypatch.setattr(file_service.time, "sleep", lambda s: sleeps.append(s))
    calls: dict = {"chunk": 0}

    def chunk_impl(data):
        calls["chunk"] += 1
        raise requests.exceptions.ConnectionError("网络抖动")

    _install_backend(
        monkeypatch,
        {"upload_id": "u-retry", "received_chunks": []},
        chunk_impl=chunk_impl,
    )

    with pytest.raises(requests.exceptions.ConnectionError):
        file_service.upload_file(1, "", local_file)

    assert calls["chunk"] == file_service._CHUNK_ATTEMPTS
    assert sleeps == [1, 2, 4, 8]


def test_网络失败不删除会话且保留本地续传记录(monkeypatch, local_file):
    """网络失败：不 DELETE 服务端会话，upload_resume.json 保留 upload_id"""
    calls: dict = {"chunk": 0}

    def chunk_impl(_data):
        calls["chunk"] += 1
        raise requests.exceptions.ConnectionError("断网")

    backend = _install_backend(
        monkeypatch,
        {"upload_id": "u-net", "received_chunks": []},
        chunk_impl=chunk_impl,
    )
    monkeypatch.setattr(file_service.time, "sleep", lambda _s: None)

    with pytest.raises(requests.exceptions.ConnectionError):
        file_service.upload_file(1, "", local_file)

    assert backend["delete"] == []
    resume = file_service._resume_get(_resume_key(local_file))
    assert resume is not None and resume["upload_id"] == "u-net"


def test_用户取消才删除服务端会话(monkeypatch, local_file):
    """用户取消 ⇒ DELETE 会话 + 清本地记录（与网络失败语义区分）"""
    state = {"sent": False}

    def chunk_impl(_data):
        state["sent"] = True
        return {}

    backend = _install_backend(
        monkeypatch,
        {"upload_id": "u-cancel", "received_chunks": []},
        chunk_impl=chunk_impl,
    )

    with pytest.raises(ValueError):
        file_service.upload_file(
            1, "", local_file,
            cancelled_flag=lambda: state["sent"],
        )

    assert backend["delete"] == ["/api/v1/files/upload/u-cancel"]
    assert file_service._resume_get(_resume_key(local_file)) is None


def test_服务端判定已存在时直接完成(monkeypatch, local_file):
    """init 返回 already_exists ⇒ 不传分片、不合并，直接返回已存在路径"""
    calls = _install_backend(
        monkeypatch,
        {
            "already_exists": True,
            "already_path": "/remote/big.bin",
        },
    )

    assert file_service.upload_file(1, "", local_file) == "/remote/big.bin"
    assert calls["chunk"] == []
    assert calls["complete"] == []

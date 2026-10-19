"""
v1.7.0 大文件上传健壮性单测（「传一晚上没成功」的回归防线）

覆盖四类真实故障：
  1. 服务端合并慢 → `complete` 超过默认 30s 超时（旧实现据此判定失败并整文件重传）；
  2. 合并请求中断 → 不盲目重发，改为轮询远端核验；
  3. 单个分片瞬态失败 → 只重发该分片；
  4. 应用被强杀后重启 → 复用 upload_id 与已发送分片，不从头再来。
"""

from __future__ import annotations

import os
import time

import pytest
import requests

from src.services import file_service
from src.utils.http_client import ApiError


@pytest.fixture()
def local_file(tmp_path):
    """1MB 小文件（分片数 1，测试足够；逻辑与 12GB 相同）"""
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * (file_service.CHUNK_SIZE + 1024))
    return str(path)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """隔离哈希缓存与续传记录，避免用例互相影响"""
    monkeypatch.setattr(file_service, "_HASH_CACHE", {})
    monkeypatch.setattr(
        file_service, "_RESUME_FILE", str(tmp_path / "upload_resume.json"),
    )


def test_合并超时不再整文件重传(monkeypatch, local_file):
    """complete 超过 30s 时：应等待服务端完成并核验，而不是判失败"""
    posts: list[str] = []
    timeouts: list[object] = []

    def fake_post(endpoint, data=None, timeout=30):
        posts.append(endpoint)
        if endpoint.endswith("/upload/init"):
            return {"upload_id": "u1"}
        if endpoint.endswith("/upload/chunk"):
            return {}
        if endpoint.endswith("/upload/complete"):
            timeouts.append(timeout)
            # 模拟「服务端已合并完成，但响应因超时丢失」
            monkeypatch.setattr(
                file_service, "_remote_size",
                lambda disk_id, rel, name: os.path.getsize(local_file),
            )
            raise requests.exceptions.ReadTimeout("read timed out")
        raise AssertionError(endpoint)

    monkeypatch.setattr(file_service.http_client, "post", fake_post)
    monkeypatch.setattr(file_service.http_client, "delete", lambda *a, **k: {})
    monkeypatch.setattr(file_service, "_FINALIZE_VERIFY_TIMEOUT", 5.0)

    result = file_service.upload_file(1, "", local_file)

    assert result.endswith("big.bin")
    # 不再重传分片：init 只发生一次、chunk 只走一轮
    assert posts.count("/api/v1/files/upload/init") == 1
    # 合并请求使用了放大的读取超时（> 默认 30s）
    assert timeouts and isinstance(timeouts[0], tuple) and timeouts[0][1] > 30


def test_分片瞬态失败只重发该分片(monkeypatch, local_file):
    """第一次 chunk 请求网络故障，重试后成功；不得重新 init / 从头再来"""
    calls = {"chunk": 0, "init": 0}

    def fake_post(endpoint, data=None, timeout=30):
        if endpoint.endswith("/upload/init"):
            calls["init"] += 1
            return {"upload_id": "u2"}
        if endpoint.endswith("/upload/chunk"):
            calls["chunk"] += 1
            if calls["chunk"] == 1:
                raise requests.exceptions.ConnectionError("网络抖动")
            return {}
        return {"path": "/remote/big.bin"}

    monkeypatch.setattr(file_service.http_client, "post", fake_post)
    monkeypatch.setattr(file_service.http_client, "delete", lambda *a, **k: {})
    monkeypatch.setattr(file_service.time, "sleep", lambda *_: None)

    assert file_service.upload_file(1, "", local_file) == "/remote/big.bin"
    assert calls["init"] == 1


def test_会话失效时重新初始化并完成(monkeypatch, local_file):
    """服务端重启导致 upload_id 失效 ⇒ 自动重新 init 并完成（不静默卡死）"""
    calls = {"init": 0, "chunk": 0}

    def fake_post(endpoint, data=None, timeout=30):
        if endpoint.endswith("/upload/init"):
            calls["init"] += 1
            return {"upload_id": f"u{calls['init']}"}
        if endpoint.endswith("/upload/chunk"):
            calls["chunk"] += 1
            # 第一次会话（u1）的所有分片都报「会话不存在」
            if data and data.get("upload_id") == "u1":
                raise ApiError(400, "上传会话不存在或已过期")
            return {}
        return {"path": "/remote/big.bin"}

    monkeypatch.setattr(file_service.http_client, "post", fake_post)
    monkeypatch.setattr(file_service.http_client, "delete", lambda *a, **k: {})

    assert file_service.upload_file(1, "", local_file) == "/remote/big.bin"
    assert calls["init"] == 2


def test_强杀后重启复用已发送分片(monkeypatch, local_file):
    """续传记录存在时，跳过已发送的分片"""
    size = os.path.getsize(local_file)
    mtime = os.path.getmtime(local_file)
    key = file_service._resume_key(1, "", os.path.basename(local_file), size, mtime)
    file_service._resume_update(key, "u-resume", {0})

    sent_indexes: list[int] = []

    def fake_post(endpoint, data=None, timeout=30):
        if endpoint.endswith("/upload/init"):
            raise AssertionError("不应重新初始化")
        if endpoint.endswith("/upload/chunk"):
            sent_indexes.append(data["chunk_index"])
            return {}
        return {"path": "/remote/big.bin"}

    monkeypatch.setattr(file_service.http_client, "post", fake_post)
    monkeypatch.setattr(file_service.http_client, "delete", lambda *a, **k: {})

    assert file_service.upload_file(1, "", local_file) == "/remote/big.bin"
    assert 0 not in sent_indexes, "已发送的分片不应重发"
    assert sent_indexes == [1]
    assert file_service._resume_get(key) is None, "完成后必须清理续传记录"


def test_哈希结果在文件未变时复用(monkeypatch, local_file):
    """重试不重算 SHA256：同一文件第二次调用应命中缓存"""
    file_service._HASH_CACHE.clear()
    first = file_service._cached_file_hash(local_file, os.path.getsize(local_file),
                                           os.path.getmtime(local_file), None, None)
    second = file_service._cached_file_hash(local_file, os.path.getsize(local_file),
                                            os.path.getmtime(local_file), None, None)
    assert first == second
    assert len(file_service._HASH_CACHE) == 1


def test_文件变化后哈希重算(local_file):
    """文件内容变化（size/mtime 变）后不得复用旧哈希"""
    file_service._HASH_CACHE.clear()
    size, mtime = os.path.getsize(local_file), os.path.getmtime(local_file)
    old = file_service._cached_file_hash(local_file, size, mtime, None, None)

    with open(local_file, "ab") as f:
        f.write(b"changed")
    new = file_service._cached_file_hash(local_file, os.path.getsize(local_file),
                                         os.path.getmtime(local_file) + 1, None, None)
    assert old != new


def test_合并期间轮询服务端进度并回报(monkeypatch, local_file):
    """complete 是长请求：期间必须把服务端合并百分比回报给上层（界面才不会以为卡死）"""
    reported: list[tuple[int, int, str]] = []
    progress_state = {"n": 0}

    def fake_post(endpoint, data=None, timeout=30):
        if endpoint.endswith("/upload/init"):
            return {"upload_id": "u-prog"}
        if endpoint.endswith("/upload/chunk"):
            return {}
        if endpoint.endswith("/upload/progress"):
            progress_state["n"] += 1
            # 第一次 30%，第二次 80%
            return {"state": "merging",
                    "processed": 3 if progress_state["n"] == 1 else 8,
                    "file_size": 10}
        if endpoint.endswith("/upload/complete"):
            time.sleep(2.2)  # 足够让主线程轮询两轮
            return {"path": "/remote/big.bin"}
        return {}

    monkeypatch.setattr(file_service.http_client, "post", fake_post)
    monkeypatch.setattr(file_service.http_client, "delete", lambda *a, **k: {})

    file_service.upload_file(
        1, "", local_file,
        progress_callback=lambda d, t, p: reported.append((d, t, p)),
    )

    finalizing = [(d, t) for d, t, p in reported if p == "finalizing"]
    assert finalizing[0][0] == 0, "合并开始时应回报 0%"
    assert any(d in (3, 8) for d, _ in finalizing), "合并中应回报服务端进度"


def test_旧服务端没有进度接口时不影响上传(monkeypatch, local_file):
    """兼容旧服务端：进度接口 404 时静默跳过，上传照常成功"""
    def fake_post(endpoint, data=None, timeout=30):
        if endpoint.endswith("/upload/init"):
            return {"upload_id": "u-old"}
        if endpoint.endswith("/upload/chunk"):
            return {}
        if endpoint.endswith("/upload/progress"):
            raise ApiError(404, "Not Found")
        return {"path": "/remote/big.bin"}

    monkeypatch.setattr(file_service.http_client, "post", fake_post)
    monkeypatch.setattr(file_service.http_client, "delete", lambda *a, **k: {})

    assert file_service.upload_file(1, "", local_file) == "/remote/big.bin"


def test_服务端已完成但客户端崩溃_重启后不重复上传(monkeypatch, local_file):
    """
    关键防重复：上次中断时服务端其实已经合并完成（响应没送达）。
    重启后必须先核对远端（size + mtime），命中则直接收尾，不能再传一遍。
    """
    size = os.path.getsize(local_file)
    mtime = os.path.getmtime(local_file)
    key = file_service._resume_key(1, "", os.path.basename(local_file), size, mtime)
    file_service._resume_update(key, "u-done", {0})

    monkeypatch.setattr(
        file_service, "_remote_stat",
        lambda disk, rel, name: (size, mtime),
    )

    def fake_post(endpoint, data=None, timeout=30):
        raise AssertionError(f"不应再发起任何上传请求：{endpoint}")

    monkeypatch.setattr(file_service.http_client, "post", fake_post)

    assert file_service.upload_file(1, "", local_file).endswith("big.bin")
    assert file_service._resume_get(key) is None, "命中后应清理续传记录"


def test_远端大小相同但mtime不同仍需上传(monkeypatch, local_file):
    """只有 size+mtime 同时吻合才算已存在，避免误跳过真正变化的文件"""
    size = os.path.getsize(local_file)
    mtime = os.path.getmtime(local_file)
    key = file_service._resume_key(1, "", os.path.basename(local_file), size, mtime)
    file_service._resume_update(key, "u-todo", {0})

    monkeypatch.setattr(
        file_service, "_remote_stat",
        lambda disk, rel, name: (size, mtime + 600),
    )

    sent: list[int] = []

    def fake_post(endpoint, data=None, timeout=30):
        if endpoint.endswith("/upload/init"):
            return {"upload_id": "u-reup"}
        if endpoint.endswith("/upload/chunk"):
            sent.append(data["chunk_index"])
            return {}
        return {"path": "/remote/big.bin"}

    monkeypatch.setattr(file_service.http_client, "post", fake_post)
    monkeypatch.setattr(file_service.http_client, "delete", lambda *a, **k: {})

    file_service.upload_file(1, "", local_file)
    # 不是「跳过」，而是继续传未完成的分片（第 1 片）
    assert sent == [1]

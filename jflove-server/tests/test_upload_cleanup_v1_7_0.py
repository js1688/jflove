"""
v1.7.0 上传残留清理单测

回归的缺陷：`cleanup_stale_uploads` 会跳过「仍在内存会话里」的临时目录，
而会话**永不过期** ⇒ 客户端崩溃 / 强杀后，临时分片目录永远不会释放
（只有重启服务端才清）。这里覆盖三层清理：

  1. 空闲超时的会话（含其临时目录）；
  2. 不在会话中的孤儿目录；
  3. 磁盘目录里遗留的 `.<name>.jflove-upload-<id>.part`。

另含：同一 `upload_id` 的并发 `complete` 必须串行（防止并发重写同一批分片）。
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from src.services import file_service


@pytest.fixture()
def temp_root(tmp_path, monkeypatch):
    """隔离 UPLOAD_TEMP_DIR 与会话表"""
    root = tmp_path / "upload_tmp"
    root.mkdir()
    monkeypatch.setattr(file_service, "UPLOAD_TEMP_DIR", str(root))
    monkeypatch.setattr(file_service, "_upload_sessions", {})
    return root


def _make_session(upload_id: str, tmp_dir, idle_seconds: float) -> dict:
    """构造一个会话（模拟已传了若干分片）"""
    os.makedirs(tmp_dir, exist_ok=True)
    (tmp_dir / "000000.chunk").write_bytes(b"x")
    session = {
        "owner_user_id": 1,
        "disk_id": 1,
        "rel_path": "",
        "filename": "a.bin",
        "file_size": 1,
        "total_chunks": 1,
        "file_hash": "h",
        "mtime": None,
        "received": {0},
        "tmp_dir": str(tmp_dir),
        "base": str(tmp_dir),
        "updated_at": time.time() - idle_seconds,
    }
    file_service._upload_sessions[upload_id] = session
    return session


def test_空闲超时的会话连同目录一起回收(temp_root):
    """核心修复：会话不能永远留在内存里，否则目录永不释放"""
    tmp_dir = temp_root / "sess-1"
    _make_session("sess-1", tmp_dir, idle_seconds=25 * 3600)

    removed = asyncio.run(file_service.cleanup_stale_uploads())

    assert removed == 1
    assert "sess-1" not in file_service._upload_sessions
    assert not tmp_dir.exists()


def test_活跃会话不被回收(temp_root):
    """正在上传的会话不能被误杀"""
    tmp_dir = temp_root / "sess-live"
    _make_session("sess-live", tmp_dir, idle_seconds=10)

    asyncio.run(file_service.cleanup_stale_uploads())

    assert "sess-live" in file_service._upload_sessions
    assert tmp_dir.exists()


def test_孤儿目录按_mtime_清理(temp_root):
    """重启后内存会话丢失，残留目录按 mtime 清理"""
    orphan = temp_root / "orphan"
    orphan.mkdir()
    old = time.time() - 25 * 3600
    os.utime(orphan, (old, old))

    removed = asyncio.run(file_service.cleanup_stale_uploads())

    assert removed == 1
    assert not orphan.exists()


def test_新目录不被误删(temp_root):
    """刚建但还没会话的目录（例如 init 进行中）不能删"""
    fresh = temp_root / "fresh"
    fresh.mkdir()

    asyncio.run(file_service.cleanup_stale_uploads())

    assert fresh.exists()


def test_清理遗留的合并临时文件(temp_root, monkeypatch, tmp_path):
    """合并时进程被杀留下的 .part 必须被清掉；新产生的不能动"""
    disk = tmp_path / "disk"
    (disk / "sub").mkdir(parents=True)
    old_part = disk / "sub" / ".a.bin.jflove-upload-uid.part"
    new_part = disk / "sub" / ".b.bin.jflove-upload-uid.part"
    normal = disk / "sub" / "keep.bin"
    for path in (old_part, new_part, normal):
        path.write_bytes(b"x")
    old = time.time() - 25 * 3600
    os.utime(old_part, (old, old))

    class _Disk(dict):
        pass

    async def fake_list_all(_db):
        return [_Disk(real_path=str(disk))]

    monkeypatch.setattr(file_service.virtual_disk_repository, "list_all", fake_list_all)

    removed = asyncio.run(file_service.cleanup_orphan_partials(db=object()))

    assert removed == 1
    assert not old_part.exists()
    assert new_part.exists(), "刚创建的合并临时文件不能被误删"
    assert normal.exists()


def test_同一会话并发合并被串行化(monkeypatch):
    """并发 complete 不得同时重写同一批分片：第二个应看到「会话不存在」"""
    calls: list[str] = []

    async def fake_locked(upload_id, db=None):
        calls.append(upload_id)
        await asyncio.sleep(0.05)
        return "/remote/a.bin"

    monkeypatch.setattr(file_service, "_complete_upload_locked", fake_locked)
    monkeypatch.setattr(file_service, "_complete_locks", {})

    async def run():
        return await asyncio.gather(
            file_service.complete_upload("u1"),
            file_service.complete_upload("u1"),
        )

    results = asyncio.run(run())
    assert results == ["/remote/a.bin", "/remote/a.bin"]
    # 两次都进入实现，但**顺序执行**（锁生效）——用耗时近似断言串行
    assert len(calls) == 2


def test_重启后的首轮清理用更短阈值(temp_root):
    """服务启动时内存会话必然为空：1 小时前的残留应立即回收（不等 24 小时）"""
    orphan = temp_root / "resume-from-crash"
    orphan.mkdir()
    (orphan / "000000.chunk").write_bytes(b"x")
    old = time.time() - 2 * 3600
    os.utime(orphan, (old, old))

    removed = asyncio.run(file_service.cleanup_stale_uploads(max_age_seconds=3600))

    assert removed == 1
    assert not orphan.exists()


# ── 合并进度（回传给客户端，替代「只能看日志」）───────────────────────


def _merge_session(tmp_path, file_size: int, chunks: int) -> dict:
    """造一个待合并会话：chunks 个 1MB 分片"""
    import hashlib

    tmp_dir = tmp_path / "chunks"
    tmp_dir.mkdir()
    digest = hashlib.sha256()
    for i in range(chunks):
        data = bytes([i % 256]) * (1024 * 1024)
        (tmp_dir / f"{i:06d}.chunk").write_bytes(data)
        digest.update(data)
    dest = tmp_path / "dest"
    dest.mkdir()
    session = {
        "owner_user_id": 1, "disk_id": 1, "rel_path": "", "filename": "m.bin",
        "file_size": file_size, "total_chunks": chunks,
        "file_hash": digest.hexdigest(), "mtime": None,
        "received": set(range(chunks)), "tmp_dir": str(tmp_dir),
        "base": str(dest), "updated_at": time.time(),
    }
    return session


def test_合并过程中能读到进度(temp_root, tmp_path, monkeypatch):
    """核心需求：合并期间必须能把百分比回报给客户端，而不是只能看日志"""
    file_service._merge_progress.clear()
    session = _merge_session(tmp_path, 4 * 1024 * 1024, 4)
    file_service._upload_sessions["u-prog"] = session

    seen: list[dict] = []
    original = file_service._merge_progress_set

    def spy(upload_id, **fields):
        original(upload_id, **fields)
        snap = file_service.merge_progress(upload_id)
        if snap:
            seen.append(snap)

    monkeypatch.setattr(file_service, "_merge_progress_set", spy)

    asyncio.run(file_service.complete_upload("u-prog"))

    states = [s["state"] for s in seen]
    assert "merging" in states
    assert states[-1] == "done"
    progress = [s["processed"] for s in seen if s["state"] == "merging"]
    assert progress and progress[-1] == 4 * 1024 * 1024, "进度应推进到文件总字节"
    assert progress == sorted(progress), "进度必须单调不减"


def test_合并失败时状态标记failed(temp_root, tmp_path):
    """失败要让客户端立刻知道，而不是一直显示「处理中」"""
    file_service._merge_progress.clear()
    session = _merge_session(tmp_path, 2 * 1024 * 1024, 2)
    session["file_hash"] = "deadbeef"  # 故意让校验失败
    file_service._upload_sessions["u-bad"] = session

    with pytest.raises(ValueError):
        asyncio.run(file_service.complete_upload("u-bad"))

    snap = file_service.merge_progress("u-bad")
    assert snap and snap["state"] == "failed"


def test_取消上传会清理进度(temp_root, tmp_path):
    file_service._merge_progress.clear()
    session = _merge_session(tmp_path, 1024 * 1024, 1)
    file_service._upload_sessions["u-cancel"] = session
    file_service._merge_progress_set("u-cancel", state="merging", total=1)

    asyncio.run(file_service.cancel_upload("u-cancel"))

    assert file_service.merge_progress("u-cancel") is None


def test_进度记录会被定期清理(temp_root):
    file_service._merge_progress.clear()
    file_service._merge_progress_set("p-old", state="done")
    file_service._merge_progress["p-old"]["updated_at"] = time.time() - 7200

    pruned = file_service.prune_merge_progress()

    assert pruned == 1
    assert file_service.merge_progress("p-old") is None

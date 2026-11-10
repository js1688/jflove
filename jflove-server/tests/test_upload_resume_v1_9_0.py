"""
v1.9.0 上传断点续传 / 异步合并 / 下载 Range / 隐藏项 / 清理 回归用例

覆盖需求：AC-321~AC-337、AC-340~AC-347
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time

import aiosqlite
import pytest

from src.config.settings import DB_PATH
from src.services import config_service, file_service, upload_cleanup_service
from tests.conftest import decrypt_response, encrypted_request


@pytest.fixture(autouse=True)
def _reset_upload_sessions():
    """每个用例从干净内存会话开始（避免上一用例占用会话上限/指纹）。"""
    file_service._upload_sessions.clear()
    file_service._session_fingerprints.clear()
    yield


def _init(client, session, env, filename="resume.bin", content=b"hello-world",
          total_chunks=1, client_type="desktop"):
    resp = encrypted_request(
        client, session, "POST", "/api/v1/files/upload/init",
        {
            "disk_id": env["disk_id"],
            "path": "alice_notes",
            "filename": filename,
            "file_size": len(content),
            "total_chunks": total_chunks,
            "file_hash": hashlib.sha256(content).hexdigest(),
            "client_type": client_type,
        },
    )
    return resp, (decrypt_response(session, resp) if resp.status_code == 200 else None)


def _chunk(client, session, upload_id, index, data):
    return encrypted_request(
        client, session, "POST", "/api/v1/files/upload/chunk",
        {
            "upload_id": upload_id,
            "chunk_index": index,
            "chunk_data": base64.b64encode(data).decode(),
        },
    )


def _wait_progress(client, session, upload_id, timeout=8.0):
    """轮询 progress 直到 done/failed（同步 TestClient）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = encrypted_request(
            client, session, "POST", "/api/v1/files/upload/progress",
            {"upload_id": upload_id},
        )
        assert resp.status_code == 200, resp.text
        last = decrypt_response(session, resp)
        if last.get("state") in ("done", "failed"):
            return last
        time.sleep(0.2)
    return last


def test_init_指纹复用与已收分片(client, env):
    """同一内容在保障期内重复 init：返回同一 upload_id + 已收分片。"""
    content = b"x" * 3
    resp, info = _init(client, env["alice"], env, content=content, total_chunks=3)
    assert resp.status_code == 200, resp.text
    upload_id = info["upload_id"]
    assert _chunk(client, env["alice"], upload_id, 0, content[:1]).status_code == 200
    time.sleep(0.2)

    resp2, info2 = _init(client, env["alice"], env, content=content, total_chunks=3)
    assert resp2.status_code == 200, resp2.text
    assert info2["upload_id"] == upload_id
    assert info2["resumed"] is True
    assert 0 in info2["received_chunks"]


def test_status_缺失分片(client, env):
    """status 以磁盘实际分片为准返回已收/缺失。"""
    content = b"abc"
    resp, info = _init(client, env["alice"], env, content=content, total_chunks=3)
    assert resp.status_code == 200, resp.text
    uid = info["upload_id"]
    assert _chunk(client, env["alice"], uid, 1, b"b").status_code == 200
    resp = encrypted_request(
        client, env["alice"], "POST", "/api/v1/files/upload/status",
        {"upload_id": uid},
    )
    assert resp.status_code == 200, resp.text
    data = decrypt_response(env["alice"], resp)
    assert data["exists"] is True
    assert data["received_chunks"] == [1]
    assert data["missing_chunks"] == [0, 2]


def test_分片大小与索引校验(client, env):
    """超过 1MB 的分片 / 负索引必须 400。"""
    content = b"z" * 10
    resp, info = _init(client, env["alice"], env, content=content, total_chunks=2)
    assert resp.status_code == 200
    uid = info["upload_id"]
    too_big = base64.b64encode(b"a" * (2 * 1024 * 1024)).decode()
    resp = encrypted_request(
        client, env["alice"], "POST", "/api/v1/files/upload/chunk",
        {"upload_id": uid, "chunk_index": 0, "chunk_data": too_big},
    )
    assert resp.status_code == 400, resp.text
    resp = _chunk(client, env["alice"], uid, -1, b"a")
    assert resp.status_code == 400, resp.text


def test_异步complete_进度done并落盘(client, env):
    """complete 立即受理，progress 轮询至 done；文件内容正确。"""
    content = b"0123456789"
    parts = [content[i:i + 4] for i in range(0, len(content), 4)]
    resp, info = _init(client, env["alice"], env, filename="async_ok.bin",
                       content=content, total_chunks=len(parts))
    assert resp.status_code == 200
    uid = info["upload_id"]
    for i, part in enumerate(parts):
        assert _chunk(client, env["alice"], uid, i, part).status_code == 200

    resp = encrypted_request(
        client, env["alice"], "POST", "/api/v1/files/upload/complete",
        {"upload_id": uid, "async_mode": True},
    )
    assert resp.status_code == 200, resp.text
    snapshot = decrypt_response(env["alice"], resp)
    assert snapshot["state"] in ("queued", "merging")

    final = _wait_progress(client, env["alice"], uid)
    assert final and final["state"] == "done", final
    target = env["disk_root"] / "alice_notes" / "async_ok.bin"
    assert target.read_bytes() == content


def test_异步complete_缺片failed(client, env):
    """缺片时 progress 返回 failed + missing_chunks。"""
    content = b"abcd"
    resp, info = _init(client, env["alice"], env, filename="async_missing.bin",
                       content=content, total_chunks=2)
    assert resp.status_code == 200
    uid = info["upload_id"]
    assert _chunk(client, env["alice"], uid, 0, b"ab").status_code == 200
    resp = encrypted_request(
        client, env["alice"], "POST", "/api/v1/files/upload/complete",
        {"upload_id": uid, "async_mode": True},
    )
    assert resp.status_code == 200, resp.text
    final = _wait_progress(client, env["alice"], uid)
    assert final and final["state"] == "failed", final
    assert final.get("missing_chunks") == [1]


def test_会话目录丢失后重新开始(client, env):
    """分片目录被清理/丢失：init 重新建会话且不报错。"""
    content = b"lost"
    resp, info = _init(client, env["alice"], env, filename="lost.bin",
                       content=content, total_chunks=1)
    assert resp.status_code == 200
    uid = info["upload_id"]
    import shutil
    shutil.rmtree(file_service._upload_sessions[uid]["tmp_dir"], ignore_errors=True)
    file_service._upload_sessions.pop(uid, None)

    resp2, info2 = _init(client, env["alice"], env, filename="lost.bin",
                         content=content, total_chunks=1)
    assert resp2.status_code == 200, resp2.text
    assert info2["upload_id"] != uid
    assert info2["restart_required"] is True


def test_服务重启后恢复会话(client, env):
    """session.json + 分片在磁盘时，restore 能恢复并续传。"""
    content = b"restore-me"
    resp, info = _init(client, env["alice"], env, filename="restore.bin",
                       content=content, total_chunks=1)
    assert resp.status_code == 200
    uid = info["upload_id"]
    assert _chunk(client, env["alice"], uid, 0, content).status_code == 200
    session = file_service._upload_sessions.get(uid)
    assert session and os.path.isfile(os.path.join(session["tmp_dir"], "session.json"))

    # 模拟服务重启：清空内存后从磁盘恢复
    file_service._upload_sessions.clear()
    file_service._session_fingerprints.clear()

    async def _restore():
        db = await aiosqlite.connect(DB_PATH)
        db.row_factory = aiosqlite.Row
        try:
            await file_service.restore_upload_sessions(db)
        finally:
            await db.close()

    asyncio.run(_restore())
    assert uid in file_service._upload_sessions
    restored = file_service.upload_status(uid, env["alice"].user_id)
    assert restored and restored["received_chunks"] == [0]


def test_下载Range响应头(client, env):
    """GET /download 支持 range_start：响应头给出偏移/总大小/版本。"""
    # 先建一个文件（走过渡同步 complete，等价于 complete 接口的兼容路径）
    content = b"0123456789"
    resp, info = _init(client, env["alice"], env, filename="range.bin",
                       content=content, total_chunks=1)
    assert resp.status_code == 200
    uid = info["upload_id"]
    assert _chunk(client, env["alice"], uid, 0, content).status_code == 200
    resp = encrypted_request(
        client, env["alice"], "POST", "/api/v1/files/upload/complete",
        {"upload_id": uid},
    )
    assert resp.status_code == 200, resp.text

    # GET 下载：加密参数走 query（Web 兼容通道）
    resp = encrypted_request(
        client, env["alice"], "GET", "/api/v1/files/download",
        {"disk_id": env["disk_id"], "path": "alice_notes/range.bin", "range_start": 4},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Stream-Offset") == "4"
    assert resp.headers.get("X-Stream-Total") == "10"
    assert resp.headers.get("X-File-Version")
    # 越界 → 416
    resp = encrypted_request(
        client, env["alice"], "GET", "/api/v1/files/download",
        {"disk_id": env["disk_id"], "path": "alice_notes/range.bin", "range_start": 11},
    )
    assert resp.status_code == 416


def test_隐藏项不出现在列表(client, env):
    """上传 `.hidden` 后 list 不返回；已知路径仍可下载。"""
    content = b"hidden"
    resp, info = _init(client, env["alice"], env, filename=".hidden",
                       content=content, total_chunks=1)
    assert resp.status_code == 200
    uid = info["upload_id"]
    assert _chunk(client, env["alice"], uid, 0, content).status_code == 200
    resp = encrypted_request(
        client, env["alice"], "POST", "/api/v1/files/upload/complete",
        {"upload_id": uid},
    )
    assert resp.status_code == 200, resp.text
    (env["disk_root"] / "alice_notes" / ".hidden").exists()

    resp = encrypted_request(
        client, env["alice"], "GET", "/api/v1/files/list",
        {"disk_id": env["disk_id"], "path": "alice_notes"},
    )
    files = decrypt_response(env["alice"], resp)["files"]
    assert all(not item["name"].startswith(".") for item in files)


def test_清理只删程序自产(tmp_path, monkeypatch, client, env):
    """清理：过期 UUID 目录删除；正常文件/非 UUID 目录保留。"""
    root = file_service.UPLOAD_TEMP_DIR
    monkeypatch.setattr(file_service, "UPLOAD_TEMP_DIR", str(root))
    uid = "11111111-2222-3333-4444-555555555555"
    session_dir = os.path.join(root, uid)
    os.makedirs(session_dir, exist_ok=True)
    (open(os.path.join(session_dir, "000000.chunk"), "wb").write(b"x"))
    with open(os.path.join(session_dir, "session.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"owner_user_id": 1, "client_type": "web",
             "updated_at": time.time() - 2 * 3600},
            f,
        )
    os.utime(os.path.join(session_dir, "000000.chunk"), (time.time() - 2 * 3600,) * 2)

    keep_dir = os.path.join(root, "normal-dir")
    os.makedirs(keep_dir, exist_ok=True)
    with open(os.path.join(keep_dir, "keep.txt"), "wb") as f:
        f.write(b"keep")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")

    options = upload_cleanup_service.UploadCleanupOptions(
        resume_ttl_hours=1,
        cleanup_enabled=True,
        cleanup_interval_minutes=60,
        cleanup_client_types=frozenset({"web"}),
        orphan_ttl_hours=1,
        max_sessions_per_user=8,
        merge_concurrency=1,
        chunk_concurrency=2,
    )

    async def _run():
        db = await aiosqlite.connect(DB_PATH)
        db.row_factory = aiosqlite.Row
        try:
            return await file_service.cleanup_temp_files(db, options)
        finally:
            await db.close()

    stats = asyncio.run(_run())
    assert stats["removed_dirs"] >= 1
    assert not os.path.isdir(session_dir)
    assert os.path.isdir(keep_dir) and os.path.isfile(os.path.join(keep_dir, "keep.txt"))
    assert outside.is_file()


def test_清理配置校验():
    """配置校验：范围/类型白名单。"""
    with pytest.raises(ValueError):
        upload_cleanup_service.validate_config_value("upload_resume_ttl_hours", "0")
    with pytest.raises(ValueError):
        upload_cleanup_service.validate_config_value("upload_cleanup_client_types", "app,fake")
    assert upload_cleanup_service.validate_config_value(
        "upload_cleanup_client_types", "web,app,web",
    ) == "app,web"


# ── v1.9.1：清理配置精简（只保留 3 个键，其余行为固定） ─────────────

def test_v1_9_1_遗留配置键不再影响行为(env):
    """写旧键不影响行为：孤儿期=期限、客户端类型=全部、并发/会话上限固定默认。"""
    async def _run():
        db = await aiosqlite.connect(DB_PATH)
        db.row_factory = aiosqlite.Row
        try:
            for key, value in {
                "upload_resume_ttl_hours": "48",
                "upload_orphan_ttl_hours": "1",
                "upload_cleanup_client_types": "web",
                "upload_max_sessions_per_user": "3",
                "upload_merge_concurrency": "0",
                "upload_chunk_concurrency": "8",
            }.items():
                await config_service.update(db, key, value)
            options = await upload_cleanup_service.get_options(db)
            # 还原会影响到其它用例的键
            await config_service.update(db, "upload_resume_ttl_hours", "24")
            await config_service.update(db, "upload_cleanup_enabled", "1")
            return options
        finally:
            await db.close()

    options = asyncio.run(_run())
    assert options.resume_ttl_hours == 48
    assert options.orphan_ttl_hours == 48          # 与期限统一
    assert options.cleanup_client_types == frozenset()  # 空 = 全部
    assert options.max_sessions_per_user == 8
    assert options.merge_concurrency == 1
    assert options.chunk_concurrency == 2


def test_v1_9_1_关闭自动清理时手动清理仍执行(env):
    """总开关只管自动清理：run_auto_cleanup 跳过，run_cleanup（手动）照常执行。"""
    async def _run():
        db = await aiosqlite.connect(DB_PATH)
        db.row_factory = aiosqlite.Row
        try:
            await config_service.update(db, "upload_cleanup_enabled", "0")
            auto = await upload_cleanup_service.run_auto_cleanup(db)
            manual = await upload_cleanup_service.run_cleanup(db)
            await config_service.update(db, "upload_cleanup_enabled", "1")
            return auto, manual
        finally:
            await db.close()

    auto, manual = asyncio.run(_run())
    assert auto["enabled"] is False
    assert auto["removed_sessions"] == 0
    assert manual["enabled"] is True
    assert "removed_sessions" in manual


def test_v1_9_1_手动清理仍要求管理员(client, env):
    """安全：手动清理入口仍要求管理员（普通用户 403）。"""
    resp = encrypted_request(
        client, env["alice"], "POST", "/api/v1/config/cleanup", {},
    )
    assert resp.status_code == 403

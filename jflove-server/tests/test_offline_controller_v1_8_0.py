"""
离线下载接口单测（v1.8.0 / S6）

覆盖 `src/controllers/offline_download_controller.py`：
  - 加密信封（POST body / GET query）、明文白名单不扩、401；
  - D9/D10：meta 只含自己、他人任务 404、设置不能越权；
  - 10 个接口的主路径与错误码；
  - 管理员配置键校验（AC-228）。

为避免真实出网，测试把队列入队函数替换为空操作；分析/S4 下载行为已由各自单测覆盖。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import src.models.database as database
from src.repositories import offline_download_repository as repo
from src.services import config_service, offline_download_service as service
from src.utils.crypto import encrypt as srv_encrypt
from tests.conftest import decrypt_response, encrypted_request


@asynccontextmanager
async def _db():
    db = await aiosqlite.connect(database.DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


def encrypted_get(client, session, path: str, payload: dict | None = None):
    """GET 请求用 query 加密信封（S-16：浏览器 GET 无 body）。"""
    data = {"token": session.token, **(payload or {})}
    envelope = srv_encrypt(
        session.session_key, json.dumps(data, ensure_ascii=False).encode()
    )
    return client.get(
        path,
        params={"nonce": envelope["nonce"], "ciphertext": envelope["ciphertext"]},
        headers={"X-Session-ID": session.session_id},
    )


async def make_ready_task(db, user_id: int, username: str = "u") -> tuple[int, int]:
    """造 ready 任务 + 1 个视频项，返回 (task_id, item_id)。"""
    task_id = await repo.insert_task(
        db,
        user_id=user_id,
        username=username,
        source_url="https://example.com/page",
        source_domain="example.com",
        page_title="Demo",
        status="ready",
    )
    await repo.replace_items(
        db,
        task_id,
        [
            {
                "kind": "video",
                "display_name": "240P",
                "ext": "mp4",
                "source_url": "https://cdn.example.com/v.mp4",
                "protocol": "http",
                "est_size": 1000,
            }
        ],
    )
    await repo.set_analysis_result(
        db,
        task_id,
        level="l2",
        expire_at="2999-01-01T00:00:00+00:00",
        item_total=1,
        page_title="Demo",
    )
    item = (await repo.list_items(db, task_id))[0]
    return task_id, int(item["id"])


@pytest.fixture()
def offline_api(env, client, monkeypatch) -> dict:
    """启用离线下载、清理任务表、准备 alice/bob 目录，并阻断真实队列执行。"""
    config_service.invalidate_cache()

    async def setup():
        async with _db() as db:
            await config_service.update(db, "offline_download_enabled", "1")
            await config_service.update(db, "offline_download_deep_analyze", "1")
            await config_service.update(db, "offline_download_max_concurrent", "2")
            await config_service.update(db, "offline_download_max_file_size_mb", "4096")
            await config_service.update(db, "offline_download_max_items", "50")
            await config_service.update(db, "offline_download_max_images", "200")
            await db.execute("DELETE FROM offline_download_items")
            await db.execute("DELETE FROM offline_download_tasks")
            await db.execute("DELETE FROM offline_download_settings")
            await db.commit()
            await repo.upsert_setting(db, env["alice_id"], env["disk_id"], "alice_dl")
            await repo.upsert_setting(db, env["bob_id"], env["disk_id"], "bob_dl")
            (Path(env["disk_root"]) / "alice_dl").mkdir(exist_ok=True)
            (Path(env["disk_root"]) / "bob_dl").mkdir(exist_ok=True)

    asyncio.run(setup())
    monkeypatch.setattr(service, "_enqueue_analysis", lambda task_id: None)
    monkeypatch.setattr(service, "_enqueue_download", lambda task_id: None)
    monkeypatch.setattr(service.BROWSER_MANAGER, "available", AsyncMock(return_value=False))
    return env


def test_meta_get_uses_query_envelope(offline_api, client) -> None:
    """GET /meta 只返回自己的配置与可写磁盘；响应为加密信封。"""
    resp = encrypted_get(client, offline_api["alice"], "/api/v1/offline/meta")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"nonce", "ciphertext"}
    meta = decrypt_response(offline_api["alice"], resp)
    assert meta["enabled"] is True
    assert meta["configured"] is True
    assert meta["config"]["rel_path"] == "alice_dl"
    assert meta["writable_disks"][0]["disk_id"] == offline_api["disk_id"]


def test_meta_requires_token(offline_api, client) -> None:
    """无 token ⇒ 401（错误体也加密）。"""
    envelope = srv_encrypt(offline_api["alice"].session_key, b"{}")
    resp = client.get(
        "/api/v1/offline/meta",
        params={"nonce": envelope["nonce"], "ciphertext": envelope["ciphertext"]},
        headers={"X-Session-ID": offline_api["alice"].session_id},
    )
    assert resp.status_code == 401


def test_task_list_and_detail_are_isolated(offline_api, client) -> None:
    """D10：A 的任务 B 列表看不到、详情 404。"""
    async def setup():
        async with _db() as db:
            return await make_ready_task(db, offline_api["alice_id"], "alice")

    task_id, _ = asyncio.run(setup())
    alice_resp = encrypted_get(client, offline_api["alice"], "/api/v1/offline/tasks")
    bob_resp = encrypted_get(client, offline_api["bob"], "/api/v1/offline/tasks")
    assert alice_resp.status_code == 200
    assert decrypt_response(offline_api["alice"], alice_resp)["total"] == 1
    assert decrypt_response(offline_api["bob"], bob_resp)["total"] == 0

    detail = encrypted_get(
        client, offline_api["bob"], "/api/v1/offline/detail", {"task_id": task_id}
    )
    assert detail.status_code == 404
    assert decrypt_response(offline_api["bob"], detail)["detail"] == "任务不存在"


def test_settings_saves_only_own_path(offline_api, client) -> None:
    """保存自己的目录成功；非法路径 400；无写权限用户 403。"""
    resp = encrypted_request(
        client,
        offline_api["alice"],
        "POST",
        "/api/v1/offline/settings",
        {"disk_id": offline_api["disk_id"], "rel_path": "alice_dl/sub", "create": True},
    )
    assert resp.status_code == 200, resp.text
    saved = decrypt_response(offline_api["alice"], resp)
    assert saved["configured"] is True
    assert saved["rel_path"] == "alice_dl/sub"

    bad = encrypted_request(
        client,
        offline_api["alice"],
        "POST",
        "/api/v1/offline/settings",
        {"disk_id": offline_api["disk_id"], "rel_path": "../evil"},
    )
    assert bad.status_code == 400


def test_analyze_rejects_disabled_ssrf_and_invalid(offline_api, client) -> None:
    """总开关关闭 / 内网地址 / 空网址都得到中文错误。"""
    async def disable():
        async with _db() as db:
            await config_service.update(db, "offline_download_enabled", "0")

    asyncio.run(disable())
    disabled = encrypted_request(
        client,
        offline_api["alice"],
        "POST",
        "/api/v1/offline/analyze",
        {"url": "https://example.com/page"},
    )
    assert disabled.status_code == 400
    assert decrypt_response(offline_api["alice"], disabled)["detail"] == "离线下载功能已关闭"

    async def enable():
        async with _db() as db:
            await config_service.update(db, "offline_download_enabled", "1")

    asyncio.run(enable())
    ssrf = encrypted_request(
        client,
        offline_api["alice"],
        "POST",
        "/api/v1/offline/analyze",
        {"url": "http://127.0.0.1/x"},
    )
    assert ssrf.status_code == 400
    assert decrypt_response(offline_api["alice"], ssrf)["detail"] == "该地址不允许访问"

    empty = encrypted_request(
        client, offline_api["alice"], "POST", "/api/v1/offline/analyze", {"url": ""}
    )
    assert empty.status_code == 400


def test_analyze_no_writable_disk_400(offline_api, client, no_perm_session) -> None:
    """无可写磁盘 ⇒ analyze 400（设计 §2.2.1；settings 才是 403）。"""
    resp = encrypted_request(
        client,
        no_perm_session,
        "POST",
        "/api/v1/offline/analyze",
        {"url": "https://example.com/page"},
    )
    assert resp.status_code == 400
    assert (
        decrypt_response(no_perm_session, resp)["detail"]
        == "你没有可写入的磁盘，无法使用离线下载"
    )


def test_select_cancel_retry_delete_roundtrip(offline_api, client, monkeypatch) -> None:
    """select → cancel → retry → delete-record 主链路。"""
    async def setup():
        async with _db() as db:
            return await make_ready_task(db, offline_api["alice_id"], "alice")

    task_id, item_id = asyncio.run(setup())
    selected = encrypted_request(
        client,
        offline_api["alice"],
        "POST",
        "/api/v1/offline/select",
        {
            "task_id": task_id,
            "item_ids": [item_id],
            "disk_id": offline_api["disk_id"],
            "target_path": "alice_dl",
        },
    )
    assert selected.status_code == 200, selected.text
    assert decrypt_response(offline_api["alice"], selected)["status"] == "downloading"

    canceled = encrypted_request(
        client, offline_api["alice"], "POST", "/api/v1/offline/cancel", {"task_id": task_id}
    )
    assert canceled.status_code == 200
    assert decrypt_response(offline_api["alice"], canceled)["status"] == "canceled"

    retried = encrypted_request(
        client, offline_api["alice"], "POST", "/api/v1/offline/retry", {"task_id": task_id}
    )
    assert retried.status_code == 200
    assert decrypt_response(offline_api["alice"], retried)["status"] == "downloading"

    deleted = encrypted_request(
        client,
        offline_api["alice"],
        "POST",
        "/api/v1/offline/delete-record",
        {"task_id": task_id},
    )
    assert deleted.status_code == 200
    assert decrypt_response(offline_api["alice"], deleted)["deleted"] is True


def test_bob_cannot_operate_alice_task(offline_api, client) -> None:
    """他人 task_id 的 cancel/select/delete 一律 404（不泄露存在性，AC-244）。"""
    async def setup():
        async with _db() as db:
            return await make_ready_task(db, offline_api["alice_id"], "alice")

    task_id, item_id = asyncio.run(setup())
    for path, payload in (
        ("/api/v1/offline/cancel", {"task_id": task_id}),
        ("/api/v1/offline/delete-record", {"task_id": task_id}),
        (
            "/api/v1/offline/select",
            {"task_id": task_id, "item_ids": [item_id]},
        ),
    ):
        resp = encrypted_request(client, offline_api["bob"], "POST", path, payload)
        assert resp.status_code == 404, path


def test_admin_config_validation_and_defaults(offline_api, client) -> None:
    """管理员配置：GET 含默认值；非法并发 400；合法值立即生效（AC-228）。"""
    get_resp = encrypted_get(client, offline_api["admin"], "/api/v1/config")
    assert get_resp.status_code == 200
    config = decrypt_response(offline_api["admin"], get_resp)["config"]
    assert config["offline_download_max_concurrent"] == "2"

    bad = encrypted_request(
        client,
        offline_api["admin"],
        "PUT",
        "/api/v1/config",
        {"key": "offline_download_max_concurrent", "value": "9"},
    )
    assert bad.status_code == 400
    assert "1~4" in decrypt_response(offline_api["admin"], bad)["detail"]

    good = encrypted_request(
        client,
        offline_api["admin"],
        "PUT",
        "/api/v1/config",
        {"key": "offline_download_max_concurrent", "value": "3"},
    )
    assert good.status_code == 200

    async def policy():
        async with _db() as db:
            return await service.get_policy(db)

    assert asyncio.run(policy())["max_concurrent"] == 3


def test_admin_config_get_default_enabled(offline_api, client) -> None:
    """全新部署（未写键）⇒ GET /config 返回总开关默认 "1"（AC-247）。"""
    async def reset():
        async with _db() as db:
            await db.execute(
                "DELETE FROM config WHERE key = ?", ("offline_download_enabled",)
            )
            await db.commit()
        config_service.invalidate_cache()

    asyncio.run(reset())
    resp = encrypted_get(client, offline_api["admin"], "/api/v1/config")
    assert resp.status_code == 200
    config = decrypt_response(offline_api["admin"], resp)["config"]
    assert config["offline_download_enabled"] == "1"


def test_response_headers_do_not_leak_filename(offline_api, client) -> None:
    """响应头不出现文件名 / 路径（§9.4）。"""
    resp = encrypted_get(client, offline_api["alice"], "/api/v1/offline/tasks")
    assert "content-disposition" not in {key.lower() for key in resp.headers}

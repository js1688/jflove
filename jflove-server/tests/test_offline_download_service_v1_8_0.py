"""
离线下载任务编排单测（v1.8.0 / S5）

覆盖 `src/services/offline_download_service.py`：
  - 全局策略读取/夹边界、账号级目录（D9）校验；
  - 任务创建前置（总开关 / 无可写磁盘 / 未配置 / SSRF / 队列满）；
  - 记录隔离（D10：列表/详情只对自己的任务）；
  - select 校验（磁盘余量、体积上限、路径合法性）；
  - 取消 / 删除记录 / 图片分页 / 分析落库 / 下载编排。

真实数据库用 conftest 的临时库；不启动 worker 队列表（直接调用内部执行函数）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import src.models.database as database
from src.repositories import offline_download_repository as repo
from src.services import config_service, offline_download_service as service
from src.services.media_analyzer_service import AnalysisResult, AnalyzedItem
from src.services.media_fetcher_service import FetchOutcome


async def _users(db):
    """取 alice / bob 的用户行。"""
    from src.repositories import user_repository

    alice = await user_repository.find_by_username(db, "alice")
    bob = await user_repository.find_by_username(db, "bob")
    return dict(alice), dict(bob)


@asynccontextmanager
async def _db():
    """独立数据库连接（row_factory=Row）。"""
    db = await aiosqlite.connect(database.DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture()
def offline_env(env, monkeypatch):
    """打开离线下载 + 清理上组用例残留 + 禁用真实浏览器探测。"""
    config_service.invalidate_cache()

    async def setup():
        async with _db() as db:
            await config_service.update(db, "offline_download_enabled", "1")
            await db.execute("DELETE FROM offline_download_items")
            await db.execute("DELETE FROM offline_download_tasks")
            await db.execute("DELETE FROM offline_download_settings")
            await db.commit()
            alice, bob = await _users(db)
            alice_id = alice["id"]
            bob_id = bob["id"]
            await repo.upsert_setting(db, alice_id, env["disk_id"], "alice_dl")
            await repo.upsert_setting(db, bob_id, env["disk_id"], "bob_dl")
            (Path(env["disk_root"]) / "alice_dl").mkdir(exist_ok=True)
            (Path(env["disk_root"]) / "bob_dl").mkdir(exist_ok=True)
        return alice_id, bob_id

    alice_id, bob_id = asyncio.run(setup())
    monkeypatch.setattr(
        service.BROWSER_MANAGER, "available", AsyncMock(return_value=False)
    )
    yield {
        **env,
        "alice_id": alice_id,
        "bob_id": bob_id,
        "alice_user": {"id": alice_id, "role": "user", "username": "alice"},
        "bob_user": {"id": bob_id, "role": "user", "username": "bob"},
    }


async def make_ready_task(
    db,
    user_id: int,
    *,
    url: str = "https://example.com/page",
    items: list[dict] | None = None,
) -> int:
    """直接造一个 `ready` 任务 + 清单项。"""
    task_id = await repo.insert_task(
        db,
        user_id=user_id,
        username="u",
        source_url=url,
        source_domain="example.com",
        page_title="Demo",
        status="ready",
    )
    rows = items or [
        {
            "kind": "video",
            "display_name": "240P",
            "ext": "mp4",
            "source_url": "https://cdn.example.com/v.mp4",
            "protocol": "http",
            "est_size": 1000,
        }
    ]
    await repo.replace_items(db, task_id, rows)
    await repo.set_analysis_result(
        db,
        task_id,
        level="l2",
        expire_at="2999-01-01T00:00:00+00:00",
        item_total=len(rows),
        page_title="Demo",
    )
    return task_id


def test_get_policy_defaults_and_clamps(env) -> None:
    """策略默认值 + 非法配置夹到硬边界。"""
    config_service.invalidate_cache()

    async def run():
        async with _db() as db:
            await config_service.update(db, "offline_download_enabled", "1")
            await config_service.update(db, "offline_download_max_concurrent", "99")
            return await service.get_policy(db)

    policy = asyncio.run(run())
    assert policy["enabled"] is True
    assert policy["max_concurrent"] == 4
    assert policy["max_items"] == 50
    assert policy["max_images"] == 200


def test_meta_reports_no_writable_disk(env, no_perm_session) -> None:
    """没有可写磁盘 ⇒ can_use=false + no_writable_disk（AC-214）。"""
    config_service.invalidate_cache()

    async def run():
        async with _db() as db:
            user = {
                "id": no_perm_session.user_id,
                "role": "user",
                "username": no_perm_session.username,
            }
            return await service.get_meta(db, user)

    meta = asyncio.run(run())
    assert meta["can_use"] is False
    assert meta["unusable_reason"] == "no_writable_disk"
    assert meta["writable_disks"] == []


def test_meta_account_isolation(offline_env) -> None:
    """A 的默认目录只属于 A；B 看不到（AC-212/240）。"""
    async def run():
        async with _db() as db:
            alice = await service.get_meta(db, offline_env["alice_user"])
            bob = await service.get_meta(db, offline_env["bob_user"])
            return alice, bob

    alice, bob = asyncio.run(run())
    assert alice["config"]["rel_path"] == "alice_dl"
    assert alice["configured"] is True
    assert bob["config"]["rel_path"] == "bob_dl"
    assert bob["configured"] is True


def test_save_setting_rejects_invalid_path(offline_env) -> None:
    """路径含 `..` / 隐藏目录 ⇒ 拒绝且不写库（AC-235）。"""
    async def run(path: str):
        async with _db() as db:
            return await service.save_setting(
                db, offline_env["alice_user"], offline_env["disk_id"], path
            )

    for bad in ("../evil", ".jflove-download/x", "/etc"):
        with pytest.raises(service._TaskError):
            asyncio.run(run(bad))


def test_create_task_preconditions(offline_env) -> None:
    """总开关 / 未配置 / SSRF 三类前置拒绝。"""
    async def run():
        async with _db() as db:
            await config_service.update(db, "offline_download_enabled", "0")
            with pytest.raises(service._TaskError) as disabled:
                await service.create_task(db, offline_env["alice_user"], "https://example.com/x")
            await config_service.update(db, "offline_download_enabled", "1")
            await repo.clear_setting(db, offline_env["alice_id"])
            with pytest.raises(service._TaskError) as unconfigured:
                await service.create_task(db, offline_env["alice_user"], "https://example.com/x")
            return disabled.value.code, unconfigured.value.code

    disabled_code, unconfigured_code = asyncio.run(run())
    assert disabled_code == "disabled"
    assert unconfigured_code == "target_not_configured"

    async def ssrf():
        async with _db() as db:
            await repo.upsert_setting(
                db, offline_env["alice_id"], offline_env["disk_id"], "alice_dl"
            )
            with pytest.raises(service._TaskError) as excinfo:
                await service.create_task(db, offline_env["alice_user"], "http://127.0.0.1/x")
            return excinfo.value.code

    assert asyncio.run(ssrf()) == "ssrf_blocked"


def test_task_list_detail_are_isolated(offline_env) -> None:
    """D10：A 建的任务 B 列表 0 命中、详情 404。"""
    async def run():
        async with _db() as db:
            alice = offline_env["alice_user"]
            bob = offline_env["bob_user"]
            task_id = await make_ready_task(db, alice["id"])
            alice_list = await service.list_tasks(db, alice)
            bob_list = await service.list_tasks(db, bob)
            with pytest.raises(service._TaskError) as excinfo:
                await service.get_detail(db, bob, task_id)
            return alice_list, bob_list, excinfo.value.code

    alice_list, bob_list, code = asyncio.run(run())
    assert alice_list["total"] == 1
    assert bob_list["total"] == 0
    assert code == "not_found"


def test_select_downloading_and_target_persisted(offline_env) -> None:
    """勾选成功后任务转 downloading、目标目录写入任务。"""
    async def run():
        async with _db() as db:
            user = offline_env["alice_user"]
            task_id = await make_ready_task(db, user["id"])
            items = await repo.list_items(db, task_id)
            result = await service.select_items(
                db,
                user,
                task_id,
                [int(items[0]["id"])],
                offline_env["disk_id"],
                "alice_dl",
            )
            row = await repo.find_task(db, task_id, user["id"])
            return result, dict(row)

    result, row = asyncio.run(run())
    assert result["status"] == "downloading"
    assert result["selected"] == 1
    assert row["status"] == "downloading"
    assert row["target_rel_path"] == "alice_dl"


def test_select_rejects_too_large_and_disk_full(offline_env, monkeypatch) -> None:
    """体积上限与磁盘余量在入队前拦住（AC-221/222）。"""
    async def setup():
        async with _db() as db:
            task_id = await make_ready_task(
                db,
                offline_env["alice_id"],
                items=[
                    {
                        "kind": "video",
                        "display_name": "4K",
                        "ext": "mp4",
                        "source_url": "https://cdn.example.com/v.mp4",
                        "protocol": "http",
                        "est_size": 10**12,
                    }
                ],
            )
            item = (await repo.list_items(db, task_id))[0]
            return task_id, int(item["id"])

    task_id, item_id = asyncio.run(setup())

    async def run():
        async with _db() as db:
            with pytest.raises(service._TaskError) as excinfo:
                await service.select_items(
                    db,
                    offline_env["alice_user"],
                    task_id,
                    [item_id],
                    offline_env["disk_id"],
                    "alice_dl",
                )
            return excinfo.value.code

    assert asyncio.run(run()) == "too_large"


def test_cancel_and_delete_record(offline_env) -> None:
    """取消置 canceled；删除记录只软删（任务从列表消失）。"""
    async def run():
        async with _db() as db:
            user = offline_env["alice_user"]
            task_id = await make_ready_task(db, user["id"])
            canceled = await service.cancel_task(db, user, task_id)
            deleted = await service.delete_record(db, user, task_id)
            listing = await service.list_tasks(db, user)
            return canceled, deleted, listing

    canceled, deleted, listing = asyncio.run(run())
    assert canceled["status"] == "canceled"
    assert deleted["deleted"] is True
    assert listing["total"] == 0


def test_store_analysis_and_run_analysis(offline_env, monkeypatch) -> None:
    """分析 worker：queued → ready，清单落库。"""
    class FakeAnalyzer:
        def __init__(self, *args, **kwargs):
            pass

        async def analyze(self, url, **kwargs):
            return AnalysisResult(
                page_title="Demo",
                items=[
                    AnalyzedItem(
                        kind="video",
                        display_name="240P",
                        ext="mp4",
                        source_url="https://cdn.example.com/v.mp4",
                        protocol="http",
                        est_size=100,
                    )
                ],
                level="l2",
            )

    monkeypatch.setattr(service, "MediaAnalyzerService", FakeAnalyzer)

    async def run():
        async with _db() as db:
            task_id = await repo.insert_task(
                db,
                user_id=offline_env["alice_id"],
                username="alice",
                source_url="https://example.com/page",
                source_domain="example.com",
                status="queued",
            )
            await service._run_analysis(db, task_id)
            row = await repo.find_task(db, task_id, offline_env["alice_id"])
            items = await repo.list_items(db, task_id)
            return dict(row), items

    row, items = asyncio.run(run())
    assert row["status"] == "ready"
    assert row["page_title"] == "Demo"
    assert len(items) == 1


def test_run_download_success(offline_env, monkeypatch) -> None:
    """下载 worker：成功置 success 并回填产物名。"""
    class FakeFetcher:
        def __init__(self, *args, **kwargs):
            pass

        async def fetch_item(self, target_dir, task_id, item, **kwargs):
            return FetchOutcome(
                output_name="Demo_240P.mp4",
                output_path=str(Path(target_dir) / "Demo_240P.mp4"),
                bytes_written=100,
            )

        async def fetch_images(self, *args, **kwargs):
            return []

    class FakeOutbound:
        async def aclose(self):
            pass

    monkeypatch.setattr(service, "MediaFetcherService", FakeFetcher)
    monkeypatch.setattr(service, "OutboundClient", FakeOutbound)

    async def run():
        async with _db() as db:
            user = offline_env["alice_user"]
            task_id = await make_ready_task(db, user["id"])
            item = (await repo.list_items(db, task_id))[0]
            await service.select_items(
                db,
                user,
                task_id,
                [int(item["id"])],
                offline_env["disk_id"],
                "alice_dl",
            )
            await service._run_download(db, task_id)
            row = await repo.find_task(db, task_id, user["id"])
            items = await repo.list_items(db, task_id)
            return dict(row), [dict(entry) for entry in items]

    row, items = asyncio.run(run())
    assert row["status"] == "success"
    assert items[0]["status"] == "success"
    assert items[0]["output_name"] == "Demo_240P.mp4"


def test_next_page_appends_images(offline_env, monkeypatch) -> None:
    """图片分页追加：ready 任务 + 新图片落库、页码自增。"""
    class FakeAnalyzer:
        def __init__(self, *args, **kwargs):
            pass

        async def analyze(self, url, **kwargs):
            return AnalysisResult(
                page_title="Gallery",
                items=[
                    AnalyzedItem(
                        kind="image",
                        display_name="100×100",
                        ext="jpg",
                        source_url=f"https://cdn.example.com/{index}.jpg",
                    )
                    for index in (2, 3)
                ],
                level="l2",
                next_page_url="",
            )

    monkeypatch.setattr(service, "MediaAnalyzerService", FakeAnalyzer)

    async def run():
        async with _db() as db:
            user = offline_env["alice_user"]
            task_id = await make_ready_task(
                db,
                user["id"],
                items=[
                    {
                        "kind": "image",
                        "display_name": "100×100",
                        "ext": "jpg",
                        "source_url": "https://cdn.example.com/1.jpg",
                    }
                ],
            )
            result = await service.next_page(db, user, task_id)
            items = await repo.list_items(db, task_id)
            row = await repo.find_task(db, task_id, user["id"])
            return result, items, dict(row)

    result, items, row = asyncio.run(run())
    assert result["added"] == 2
    assert len(items) == 3
    assert row["image_page_index"] == 2

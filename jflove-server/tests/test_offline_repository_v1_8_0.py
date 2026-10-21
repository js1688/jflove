"""
离线下载数据访问层单测（v1.8.0）

重点不是"能写进去"，而是**两条硬约束别写漏**：

1. **记录按账号隔离（D10）**：`list_tasks` / `find_task` / `soft_delete` /
   `find_reusable_analysis` / `expire_ready_tasks` 都必须带 `user_id`。
   这里用"B 查不到 A 的任务"来钉住，而不是只测 happy path。
2. **账号级目录唯一（D9）**：`upsert_setting` 反复保存只能产生**一行**活跃配置；
   `clear_setting` 之后再保存能成功（部分唯一索引 + 软删除的配合）。

另含状态机侧面的回归：`mark_interrupted` 不能碰 `ready`（否则用户分析完回来
发现清单被"重启"打死）。
"""

from __future__ import annotations

import asyncio
import sqlite3

import aiosqlite
import pytest

from src.models import database
from src.repositories import offline_download_repository as repo


@pytest.fixture()
def db_path(tmp_path):
    """建一个只含 v1.8.0 三张表的临时库（不跑完整 init_db，避免无关表/迁移干扰）。"""
    path = tmp_path / "offline.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(database._CREATE_OFFLINE_TASKS)
    conn.execute(database._CREATE_OFFLINE_ITEMS)
    conn.execute(database._CREATE_OFFLINE_SETTINGS)
    for idx in database._INDEXES:
        if "offline" in idx:
            conn.execute(idx)
    conn.commit()
    conn.close()
    return path


def _run(coro):
    """同步测试里跑协程（与既有测试一致，不引入 pytest-asyncio）。"""
    return asyncio.run(coro)


def test_record_isolation_between_accounts(db_path) -> None:
    """A 建的任务，B 列表 0 命中、按 ID 也查不到（他人任务按不存在处理）。"""

    async def scenario():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            task_a = await repo.insert_task(
                db,
                user_id=1,
                username="alice",
                source_url="https://a.example.com/v/1",
                source_domain="a.example.com",
            )
            rows_b, total_b = await repo.list_tasks(db, user_id=2)
            got_b = await repo.find_task(db, task_a, user_id=2)
            rows_a, total_a = await repo.list_tasks(db, user_id=1)
            got_a = await repo.find_task(db, task_a, user_id=1)
            return task_a, rows_b, total_b, got_b, rows_a, total_a, got_a

    task_a, rows_b, total_b, got_b, rows_a, total_a, got_a = _run(scenario())
    assert task_a > 0
    assert total_b == 0 and rows_b == []
    assert got_b is None, "他人任务必须按不存在返回（不能返回行再判断归属）"
    assert total_a == 1 and rows_a[0]["id"] == task_a
    assert got_a is not None


def test_soft_delete_is_scoped_to_owner(db_path) -> None:
    """B 删不掉 A 的任务（越权删除必须是 0 行，而不是删了再报错）。"""

    async def scenario():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            task_a = await repo.insert_task(
                db, user_id=1, username="alice",
                source_url="https://a.example.com/v/2", source_domain="a.example.com",
            )
            deleted_by_b = await repo.soft_delete(db, task_a, user_id=2)
            still_there = await repo.find_task(db, task_a, user_id=1)
            deleted_by_a = await repo.soft_delete(db, task_a, user_id=1)
            after = await repo.find_task(db, task_a, user_id=1)
            return deleted_by_b, still_there, deleted_by_a, after

    deleted_by_b, still_there, deleted_by_a, after = _run(scenario())
    assert deleted_by_b is False
    assert still_there is not None
    assert deleted_by_a is True
    assert after is None


def test_settings_are_single_row_per_user(db_path) -> None:
    """反复保存只产生一行活跃配置；清除后可再保存（软删除 + 部分唯一索引）。"""

    async def scenario():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await repo.upsert_setting(db, 1, 10, "download")
            await repo.upsert_setting(db, 1, 11, "media/inbox")
            row = await repo.get_setting(db, 1)
            async with db.execute(
                "SELECT COUNT(*) AS c FROM offline_download_settings"
                " WHERE user_id = 1 AND deleted_at IS NULL"
            ) as cur:
                active = (await cur.fetchone())["c"]
            cleared = await repo.clear_setting(db, 1)
            none_after = await repo.get_setting(db, 1)
            await repo.upsert_setting(db, 1, 10, "again")
            again = await repo.get_setting(db, 1)
            return row, active, cleared, none_after, again

    row, active, cleared, none_after, again = _run(scenario())
    assert active == 1
    assert (row["default_disk_id"], row["default_rel_path"]) == (11, "media/inbox")
    assert cleared is True
    assert none_after is None
    assert again is not None and again["default_rel_path"] == "again"


def test_settings_isolated_between_accounts(db_path) -> None:
    """A/B 各配各的，互相看不到（这是 D9 的隐私底线）。"""

    async def scenario():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await repo.upsert_setting(db, 1, 10, "a-dir")
            await repo.upsert_setting(db, 2, 20, "b-dir")
            a = await repo.get_setting(db, 1)
            b = await repo.get_setting(db, 2)
            return a, b

    a, b = _run(scenario())
    assert (a["default_disk_id"], a["default_rel_path"]) == (10, "a-dir")
    assert (b["default_disk_id"], b["default_rel_path"]) == (20, "b-dir")


def test_items_replace_and_selection(db_path) -> None:
    """清单覆盖写入 + 勾选只影响本任务；未选中的项不进入下载列表。"""

    async def scenario():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            task = await repo.insert_task(
                db, user_id=1, username="alice",
                source_url="https://a.example.com/v/3", source_domain="a.example.com",
            )
            await repo.replace_items(db, task, [
                {"kind": "video", "display_name": "1080P", "source_url": "https://m/1.m3u8",
                 "protocol": "m3u8", "est_size": 100, "height": 1080},
                {"kind": "video", "display_name": "720P", "source_url": "https://m/2.m3u8",
                 "protocol": "m3u8", "est_size": 50, "height": 720},
                {"kind": "image", "display_name": "封面", "source_url": "https://m/c.jpg",
                 "ext": "jpg", "est_size": 10},
            ])
            items = await repo.list_items(db, task)
            chosen = [items[0]["id"], items[2]["id"]]
            selected = await repo.select_items(db, task, chosen)
            picked = await repo.list_selected_items(db, task)
            # 覆盖写：重分析后清单被替换，不会残留旧项
            await repo.replace_items(db, task, [
                {"kind": "video", "display_name": "480P", "source_url": "https://m/3.m3u8"},
            ])
            after = await repo.list_items(db, task)
            return items, selected, picked, after

    items, selected, picked, after = _run(scenario())
    assert len(items) == 3
    assert selected == 2
    assert sorted(row["display_name"] for row in picked) == ["1080P", "封面"]
    assert len(after) == 1 and after[0]["display_name"] == "480P"


def test_restart_does_not_kill_ready_tasks(db_path) -> None:
    """重启兜底只处理"正在跑"的状态；`ready`（等用户勾选）必须保留。"""

    async def scenario():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            ready = await repo.insert_task(
                db, user_id=1, username="alice",
                source_url="https://a.example.com/v/4", source_domain="a.example.com",
                status="ready",
            )
            running = await repo.insert_task(
                db, user_id=1, username="alice",
                source_url="https://a.example.com/v/5", source_domain="a.example.com",
                status="downloading",
            )
            affected = await repo.mark_interrupted(db)
            ready_row = await repo.find_task(db, ready, user_id=1)
            running_row = await repo.find_task(db, running, user_id=1)
            return affected, ready_row, running_row

    affected, ready_row, running_row = _run(scenario())
    assert affected == 1
    assert ready_row["status"] == "ready"
    assert running_row["status"] == "failed"
    assert running_row["error_code"] == "restart_interrupted"


def test_expire_ready_is_scoped_and_marks_url_expired(db_path) -> None:
    """过期标记只影响自己账号的 ready 任务，且错误码固定为 `url_expired`。"""

    async def scenario():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            mine = await repo.insert_task(
                db, user_id=1, username="alice",
                source_url="https://a.example.com/v/6", source_domain="a.example.com",
                status="ready",
            )
            others = await repo.insert_task(
                db, user_id=2, username="bob",
                source_url="https://a.example.com/v/7", source_domain="a.example.com",
                status="ready",
            )
            await repo.set_analysis_result(
                db, mine, level="l2", expire_at="2000-01-01T00:00:00+00:00", item_total=1,
            )
            await repo.set_analysis_result(
                db, others, level="l2", expire_at="2000-01-01T00:00:00+00:00", item_total=1,
            )
            affected = await repo.expire_ready_tasks(
                db, user_id=1, now_iso="2999-01-01T00:00:00+00:00",
            )
            mine_row = await repo.find_task(db, mine, user_id=1)
            others_row = await repo.find_task(db, others, user_id=2)
            return affected, mine_row, others_row

    affected, mine_row, others_row = _run(scenario())
    assert affected == 1
    assert mine_row["status"] == "failed" and mine_row["error_code"] == "url_expired"
    assert others_row["status"] == "ready", "不能把别人账号的清单标记过期"


def test_analysis_reuse_only_within_own_account(db_path) -> None:
    """同址复用只在**自己的**任务里找（否则等于把别人的分析结果抄过来）。"""

    async def scenario():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            url = "https://a.example.com/v/8"
            await repo.insert_task(
                db, user_id=2, username="bob", source_url=url,
                source_domain="a.example.com", status="ready",
            )
            hit_for_a = await repo.find_reusable_analysis(
                db, user_id=1, source_url=url, since_iso="2000-01-01T00:00:00+00:00",
            )
            hit_for_b = await repo.find_reusable_analysis(
                db, user_id=2, source_url=url, since_iso="2000-01-01T00:00:00+00:00",
            )
            return hit_for_a, hit_for_b

    hit_for_a, hit_for_b = _run(scenario())
    assert hit_for_a is None
    assert hit_for_b is not None


def test_update_item_rejects_unknown_fields(db_path) -> None:
    """item 更新走白名单：出现未知字段必须报错，而不是悄悄拼进 SQL。"""

    async def scenario():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            task = await repo.insert_task(
                db, user_id=1, username="alice",
                source_url="https://a.example.com/v/9", source_domain="a.example.com",
            )
            await repo.replace_items(db, task, [
                {"kind": "video", "display_name": "1080P", "source_url": "https://m/9.m3u8"},
            ])
            items = await repo.list_items(db, task)
            item_id = items[0]["id"]
            await repo.update_item(db, item_id, status="success", bytes_done=42)
            ok = await repo.list_items(db, task)
            with pytest.raises(ValueError):
                await repo.update_item(db, item_id, task_id=999)
            return ok

    ok = _run(scenario())
    assert ok[0]["status"] == "success"
    assert ok[0]["bytes_done"] == 42
    assert ok[0]["task_id"] != 999

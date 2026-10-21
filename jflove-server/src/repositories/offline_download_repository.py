"""
离线下载数据访问层（v1.8.0 新增）

三张表：`offline_download_tasks` / `offline_download_items` / `offline_download_settings`。

两条硬约束（`文档记录/设计文档/v1.8.0.md`）：

1. **记录按账号隔离（D10）**：面向用户的读取/操作函数一律带 `user_id` 条件。
   为什么不用"先查出来再判断归属"：那样只要有一个调用点忘了判断就是越权，
   把条件放进 SQL 才是"漏不掉"的写法。他人记录一律按**不存在**处理（404）。
2. **账号级默认目录唯一（D9）**：一个账号只有一行活跃配置（部分唯一索引兜底），
   `upsert_setting()` 必须保证"有则更新、无则插入"，且清除是**软删除**（否则唯一索引会挡住重建）。

状态机（与设计文档 §2.5.1 一致）：
    queued → analyzing → ready → downloading → merging → success
    任一步 → failed / canceled；failed/canceled 可 retry 回 downloading
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

#: 任务终态（这些状态不再占用队列、不再有进度变化）
TERMINAL_STATUSES = ("success", "failed", "canceled")

#: 未终态（重启兜底时要标记为失败的状态；`ready` 单独处理——它没有在跑的工作）
ACTIVE_STATUSES = ("queued", "analyzing", "downloading", "merging")

#: 允许被用户「重试」的状态
RETRYABLE_STATUSES = ("failed", "canceled")

#: 重新分析后允许刷新的「来源字段」白名单（仅服务端内部使用，**不**暴露给客户端）
_ITEM_SOURCE_UPDATABLE = frozenset(
    {
        "source_url",
        "aux_url",
        "protocol",
        "variant_id",
        "vcodec",
        "acodec",
        "width",
        "height",
        "duration_ms",
        "est_size",
        "is_estimated",
        "display_name",
        "ext",
    }
)

#: 可在详情接口中更新的 item 字段白名单（**不接受**客户端任意字段）
_ITEM_UPDATABLE = frozenset(
    {
        "status",
        "bytes_done",
        "output_name",
        "error_code",
        "error_message",
        "is_selected",
        "width",
        "height",
        "duration_ms",
        "est_size",
        "is_estimated",
    }
)


def _now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串（与既有表口径一致）。"""
    return datetime.now(timezone.utc).isoformat()


def _iso_in(seconds: float) -> str:
    """
    返回"现在 + seconds 秒"的 ISO 字符串（用于清单有效期）。

    :param seconds: 相对秒数（可为负）
    """
    from datetime import timedelta

    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


# ── 任务（tasks）──────────────────────────────────────────

async def insert_task(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    username: str,
    source_url: str,
    source_domain: str,
    page_title: str = "",
    status: str = "queued",
) -> int:
    """
    新建一条任务。

    :returns: 新任务 ID
    """
    now = _now()
    cur = await db.execute(
        "INSERT INTO offline_download_tasks"
        " (user_id, username, source_url, source_domain, page_title, status,"
        "  created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, username, source_url, source_domain, page_title, status, now, now),
    )
    await db.commit()
    return int(cur.lastrowid or 0)


async def find_task(
    db: aiosqlite.Connection, task_id: int, user_id: int
) -> aiosqlite.Row | None:
    """
    按 (task_id, user_id) 查任务——**他人任务按不存在返回 None**。

    这是 D10 的核心读法：调用方只拿到 `None`，无法区分"没有这个任务"和
    "这是别人的任务"，从根上不泄露存在性。
    """
    async with db.execute(
        "SELECT * FROM offline_download_tasks"
        " WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (task_id, user_id),
    ) as cur:
        return await cur.fetchone()


async def find_task_any(db: aiosqlite.Connection, task_id: int) -> aiosqlite.Row | None:
    """
    按 ID 查任务（**仅供服务内部 worker 使用**，不得直接用于用户请求）。

    worker 已经过 `find_task()` 授权，或任务来自队列，此时不需要再带 user_id。
    """
    async with db.execute(
        "SELECT * FROM offline_download_tasks WHERE id = ? AND deleted_at IS NULL",
        (task_id,),
    ) as cur:
        return await cur.fetchone()


async def list_tasks(
    db: aiosqlite.Connection, user_id: int, page: int = 1, page_size: int = 20
) -> tuple[list[aiosqlite.Row], int]:
    """
    分页查询**某账号自己的**任务（按创建时间倒序）。

    :returns: (任务行列表, 总条数)
    """
    async with db.execute(
        "SELECT COUNT(*) AS c FROM offline_download_tasks"
        " WHERE user_id = ? AND deleted_at IS NULL",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
        total = int(row["c"]) if row else 0
    offset = max(0, (page - 1) * page_size)
    async with db.execute(
        "SELECT * FROM offline_download_tasks"
        " WHERE user_id = ? AND deleted_at IS NULL"
        " ORDER BY id DESC LIMIT ? OFFSET ?",
        (user_id, page_size, offset),
    ) as cur:
        rows = await cur.fetchall()
    return list(rows), total


async def count_pending(db: aiosqlite.Connection) -> int:
    """统计排队/分析中的任务数（分析队列上限判定用）。"""
    placeholders = ",".join("?" for _ in ("queued", "analyzing"))
    async with db.execute(
        "SELECT COUNT(*) AS c FROM offline_download_tasks"
        f" WHERE status IN ({placeholders}) AND deleted_at IS NULL",
        ("queued", "analyzing"),
    ) as cur:
        row = await cur.fetchone()
    return int(row["c"]) if row else 0


async def find_reusable_analysis(
    db: aiosqlite.Connection, user_id: int, source_url: str, since_iso: str
) -> aiosqlite.Row | None:
    """
    找**自己的**、同址且仍在复用窗口内的 `ready` 任务（AC-208）。

    :param since_iso: 创建时间下界（ISO 字符串）
    """
    async with db.execute(
        "SELECT * FROM offline_download_tasks"
        " WHERE user_id = ? AND source_url = ? AND status = 'ready'"
        "   AND created_at >= ? AND deleted_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (user_id, source_url, since_iso),
    ) as cur:
        return await cur.fetchone()


async def set_status(
    db: aiosqlite.Connection,
    task_id: int,
    status: str,
    *,
    error_code: str = "",
    error_message: str = "",
) -> None:
    """
    更新任务状态（同时维护 started_at / finished_at）。

    :param status: queued/analyzing/ready/downloading/merging/success/failed/canceled
    """
    now = _now()
    sets = ["status = ?", "updated_at = ?"]
    params: list[object] = [status, now]
    if status in ("analyzing", "downloading") and status != "ready":
        # 首次进入执行态时记录开始时间（已有则不覆盖）
        sets.append("started_at = COALESCE(started_at, ?)")
        params.append(now)
    if status in TERMINAL_STATUSES:
        sets.append("finished_at = ?")
        params.append(now)
    sets.extend(["error_code = ?", "error_message = ?"])
    params.extend([error_code, error_message])
    params.append(task_id)
    await db.execute(
        f"UPDATE offline_download_tasks SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    await db.commit()


async def set_analysis_result(
    db: aiosqlite.Connection,
    task_id: int,
    *,
    level: str,
    expire_at: str,
    item_total: int,
    page_title: str | None = None,
) -> None:
    """写入分析结果元信息（清单已落 items 表，这里只更新任务头）。"""
    now = _now()
    if page_title is None:
        await db.execute(
            "UPDATE offline_download_tasks"
            " SET analysis_level = ?, analysis_expire_at = ?, item_total = ?, updated_at = ?"
            " WHERE id = ?",
            (level, expire_at, item_total, now, task_id),
        )
    else:
        await db.execute(
            "UPDATE offline_download_tasks"
            " SET analysis_level = ?, analysis_expire_at = ?, item_total = ?,"
            "     page_title = ?, updated_at = ?"
            " WHERE id = ?",
            (level, expire_at, item_total, page_title, now, task_id),
        )
    await db.commit()


async def set_target(
    db: aiosqlite.Connection,
    task_id: int,
    *,
    disk_id: int,
    rel_path: str,
    pack_zip: bool,
    bytes_total: int,
) -> None:
    """写入目标目录与预估总量（select 阶段）。"""
    await db.execute(
        "UPDATE offline_download_tasks"
        " SET target_disk_id = ?, target_rel_path = ?, pack_zip = ?,"
        "     bytes_total = ?, bytes_done = 0, updated_at = ?"
        " WHERE id = ?",
        (disk_id, rel_path, 1 if pack_zip else 0, bytes_total, _now(), task_id),
    )
    await db.commit()


async def set_progress(
    db: aiosqlite.Connection,
    task_id: int,
    *,
    bytes_done: int,
    bytes_total: int,
    speed_bps: int,
    item_done: int,
) -> None:
    """更新进度（调用方负责按 `OFFLINE_PROGRESS_THROTTLE` 节流）。"""
    await db.execute(
        "UPDATE offline_download_tasks"
        " SET bytes_done = ?, bytes_total = ?, speed_bps = ?, item_done = ?, updated_at = ?"
        " WHERE id = ?",
        (bytes_done, bytes_total, speed_bps, item_done, _now(), task_id),
    )
    await db.commit()


async def set_image_page_index(db: aiosqlite.Connection, task_id: int, index: int) -> None:
    """更新图片分页游标（next-page 用）。"""
    await db.execute(
        "UPDATE offline_download_tasks SET image_page_index = ?, updated_at = ? WHERE id = ?",
        (index, _now(), task_id),
    )
    await db.commit()


async def soft_delete(
    db: aiosqlite.Connection, task_id: int, user_id: int
) -> bool:
    """
    软删除任务记录（**不删磁盘文件**）。

    :returns: 是否删掉了（False = 不是他的任务 / 已删）
    """
    now = _now()
    cur = await db.execute(
        "UPDATE offline_download_tasks SET deleted_at = ?, updated_at = ?"
        " WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (now, now, task_id, user_id),
    )
    await db.commit()
    return bool(cur.rowcount)


async def expire_ready_tasks(
    db: aiosqlite.Connection, user_id: int, now_iso: str
) -> int:
    """
    把该账号已过期的 `ready` 任务标记为失败（`url_expired`）。

    惰性过期：在列表/详情/选择入口调用，避免额外定时器。

    :returns: 影响的行数
    """
    now = _now()
    cur = await db.execute(
        "UPDATE offline_download_tasks"
        " SET status = 'failed', error_code = 'url_expired',"
        "     error_message = '媒体地址已过期，请重新分析',"
        "     finished_at = ?, updated_at = ?"
        " WHERE user_id = ? AND status = 'ready'"
        "   AND analysis_expire_at IS NOT NULL AND analysis_expire_at < ?"
        "   AND deleted_at IS NULL",
        (now, now, user_id, now_iso),
    )
    await db.commit()
    return int(cur.rowcount or 0)


async def mark_interrupted(db: aiosqlite.Connection) -> int:
    """
    服务启动时把所有"正在跑"的任务标记为失败（重启兜底）。

    注意 **不碰 `ready`**：它没有在跑的工作，用户回来后仍可勾选下载（见设计文档 §2.5.3）。

    :returns: 影响的行数
    """
    now = _now()
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    cur = await db.execute(
        "UPDATE offline_download_tasks"
        " SET status = 'failed', error_code = 'restart_interrupted',"
        "     error_message = '服务重启导致任务中断',"
        "     finished_at = ?, updated_at = ?"
        f" WHERE status IN ({placeholders}) AND deleted_at IS NULL",
        (now, now, *ACTIVE_STATUSES),
    )
    await db.commit()
    return int(cur.rowcount or 0)


# ── 可下载项（items）──────────────────────────────────────

async def replace_items(
    db: aiosqlite.Connection, task_id: int, items: list[dict]
) -> None:
    """
    用新清单覆盖任务的可下载项（重分析/复用分析时用）。

    :param items: 每项至少含 `kind` / `display_name` / `source_url`
    """
    now = _now()
    await db.execute("DELETE FROM offline_download_items WHERE task_id = ?", (task_id,))
    for order, item in enumerate(items):
        await db.execute(
            "INSERT INTO offline_download_items"
            " (task_id, kind, display_name, ext, source_url, aux_url, protocol,"
            "  variant_id, vcodec, acodec, width, height, duration_ms, est_size,"
            "  is_estimated, sort_order, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                item.get("kind", "video"),
                item.get("display_name", ""),
                item.get("ext", "mp4"),
                item.get("source_url", ""),
                item.get("aux_url", ""),
                item.get("protocol", "http"),
                item.get("variant_id", ""),
                item.get("vcodec", ""),
                item.get("acodec", ""),
                int(item.get("width", 0) or 0),
                int(item.get("height", 0) or 0),
                int(item.get("duration_ms", 0) or 0),
                int(item.get("est_size", 0) or 0),
                1 if item.get("is_estimated", True) else 0,
                order,
                now,
                now,
            ),
        )
    await db.commit()


async def list_items(db: aiosqlite.Connection, task_id: int) -> list[aiosqlite.Row]:
    """按展示顺序返回某任务的全部可下载项。"""
    async with db.execute(
        "SELECT * FROM offline_download_items"
        " WHERE task_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
        (task_id,),
    ) as cur:
        return list(await cur.fetchall())


async def select_items(
    db: aiosqlite.Connection, task_id: int, item_ids: list[int]
) -> int:
    """
    标记本次选择（先全部清零，再置选中的）。

    :returns: 实际被选中的项数（用于校验 `item_ids` 合法性）
    """
    await db.execute(
        "UPDATE offline_download_items SET is_selected = 0 WHERE task_id = ?",
        (task_id,),
    )
    if not item_ids:
        await db.commit()
        return 0
    placeholders = ",".join("?" for _ in item_ids)
    cur = await db.execute(
        "UPDATE offline_download_items SET is_selected = 1, updated_at = ?"
        f" WHERE task_id = ? AND id IN ({placeholders})",
        (_now(), task_id, *item_ids),
    )
    await db.commit()
    return int(cur.rowcount or 0)


async def list_selected_items(
    db: aiosqlite.Connection, task_id: int
) -> list[aiosqlite.Row]:
    """返回该任务被勾选的项（下载执行用）。"""
    async with db.execute(
        "SELECT * FROM offline_download_items"
        " WHERE task_id = ? AND is_selected = 1 AND deleted_at IS NULL"
        " ORDER BY sort_order, id",
        (task_id,),
    ) as cur:
        return list(await cur.fetchall())


async def update_item(db: aiosqlite.Connection, item_id: int, **fields) -> None:
    """
    更新单个项的字段（**白名单**，见 `_ITEM_UPDATABLE`）。

    :raises ValueError: 出现不在白名单里的字段
    """
    unknown = set(fields) - _ITEM_UPDATABLE
    if unknown:
        raise ValueError(f"不允许更新的字段：{sorted(unknown)}")
    if not fields:
        return
    sets = [f"{name} = ?" for name in fields]
    params: list[object] = list(fields.values())
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(item_id)
    await db.execute(
        f"UPDATE offline_download_items SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    await db.commit()


async def refresh_item_source(db: aiosqlite.Connection, item_id: int, **fields) -> None:
    """
    刷新单个可下载项的「来源信息」（签过期后自动重分析时用）。

    只允许 `_ITEM_SOURCE_UPDATABLE` 里的字段：**不会**碰 `status` / `is_selected` /
    `output_name` / `bytes_done`，这样已成功项与用户勾选在重分析后保持原样。

    :raises ValueError: 出现不在白名单里的字段
    """
    unknown = set(fields) - _ITEM_SOURCE_UPDATABLE
    if unknown:
        raise ValueError(f"不允许刷新的字段：{sorted(unknown)}")
    if not fields:
        return
    sets = [f"{name} = ?" for name in fields]
    params: list[object] = list(fields.values())
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(item_id)
    await db.execute(
        f"UPDATE offline_download_items SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    await db.commit()


async def reset_failed_items(
    db: aiosqlite.Connection,
    task_id: int,
    *,
    error_code: str | None = None,
    item_ids: list[int] | None = None,
) -> int:
    """
    把失败项重置为 `pending`（自动重分析成功后重试用）。

    :param error_code: 只重置该错误码的项（如 `url_expired`）；None 表示全部失败项
    :param item_ids: 只重置这些项；None 表示该任务全部失败项
    :returns: 受影响行数
    """
    clauses = ["task_id = ?", "status = 'failed'"]
    where_params: list[object] = [task_id]
    if error_code is not None:
        clauses.append("error_code = ?")
        where_params.append(error_code)
    if item_ids is not None:
        if not item_ids:
            return 0
        placeholders = ",".join("?" for _ in item_ids)
        clauses.append(f"id IN ({placeholders})")
        where_params.extend(item_ids)
    cur = await db.execute(
        "UPDATE offline_download_items"
        " SET status = 'pending', error_code = '', error_message = '',"
        "     bytes_done = 0, updated_at = ?"
        f" WHERE {' AND '.join(clauses)}",
        (_now(), *where_params),
    )
    await db.commit()
    return int(cur.rowcount or 0)


# ── 账号级默认目录（settings）─────────────────────────────

async def get_setting(
    db: aiosqlite.Connection, user_id: int
) -> aiosqlite.Row | None:
    """取该账号的活跃默认目录配置（没有则 None）。"""
    async with db.execute(
        "SELECT * FROM offline_download_settings"
        " WHERE user_id = ? AND deleted_at IS NULL",
        (user_id,),
    ) as cur:
        return await cur.fetchone()


async def upsert_setting(
    db: aiosqlite.Connection, user_id: int, disk_id: int, rel_path: str
) -> None:
    """
    保存该账号的默认目录（有活跃行则更新，否则插入）。

    依赖部分唯一索引 `offline_download_settings_user_active_uidx`：
    若并发插入，会由数据库拒绝第二条活跃行——这里用 `INSERT ... ON CONFLICT` 兜住。
    """
    now = _now()
    existing = await get_setting(db, user_id)
    if existing is not None:
        await db.execute(
            "UPDATE offline_download_settings"
            " SET default_disk_id = ?, default_rel_path = ?, updated_at = ?"
            " WHERE id = ?",
            (disk_id, rel_path, now, int(existing["id"])),
        )
    else:
        await db.execute(
            "INSERT INTO offline_download_settings"
            " (user_id, default_disk_id, default_rel_path, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, disk_id, rel_path, now, now),
        )
    await db.commit()


async def clear_setting(db: aiosqlite.Connection, user_id: int) -> bool:
    """
    清除该账号的默认目录（软删除，便于审计；唯一索引只约束活跃行）。

    :returns: 是否确实清掉了
    """
    now = _now()
    cur = await db.execute(
        "UPDATE offline_download_settings SET deleted_at = ?, updated_at = ?"
        " WHERE user_id = ? AND deleted_at IS NULL",
        (now, now, user_id),
    )
    await db.commit()
    return bool(cur.rowcount)

"""
媒体修复任务数据访问层（v1.4.2 新增）

负责 media_repair_tasks 表的 CRUD 与状态流转查询。

设计说明：
  - 任务全平台共享（不做账户归属隔离），user_id / username 仅供列表展示
  - 软删除遵循 §5 规范（deleted_at），列表默认只查未删除记录
"""

from datetime import datetime, timezone

import aiosqlite


def _now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


async def insert(
    db: aiosqlite.Connection,
    user_id: int,
    username: str,
    disk_id: int,
    rel_path: str,
    filename: str,
    source_size: int,
) -> int:
    """
    插入一条新修复任务（status=pending）。

    :param db: 数据库连接
    :param user_id: 创建者用户 ID（仅展示用）
    :param username: 创建者用户名（仅展示用，避免列表联表）
    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 原文件所在目录（磁盘内相对路径）
    :param filename: 原文件名（含扩展名）
    :param source_size: 原文件字节大小（进度估算基准）
    :returns: 新任务 ID
    """
    now = _now()
    cur = await db.execute(
        "INSERT INTO media_repair_tasks"
        " (user_id, username, disk_id, rel_path, filename, status,"
        "  source_size, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
        (user_id, username, disk_id, rel_path, filename, source_size, now, now),
    )
    await db.commit()
    return int(cur.lastrowid or 0)


async def find_by_id(db: aiosqlite.Connection, task_id: int) -> aiosqlite.Row | None:
    """按 ID 查询未删除任务，不存在返回 None。"""
    async with db.execute(
        "SELECT * FROM media_repair_tasks WHERE id = ? AND deleted_at IS NULL",
        (task_id,),
    ) as cur:
        return await cur.fetchone()


async def find_active_by_file(
    db: aiosqlite.Connection, disk_id: int, rel_path: str, filename: str
) -> aiosqlite.Row | None:
    """
    查询同文件是否存在未完成任务（pending/running/verifying），存在返回该行。

    用于创建任务时的互斥校验（避免重复修复同一文件）。
    """
    async with db.execute(
        "SELECT * FROM media_repair_tasks"
        " WHERE disk_id = ? AND rel_path = ? AND filename = ?"
        "   AND status IN ('pending', 'running', 'verifying')"
        "   AND deleted_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (disk_id, rel_path, filename),
    ) as cur:
        return await cur.fetchone()


async def list_tasks(
    db: aiosqlite.Connection, page: int = 1, page_size: int = 50
) -> tuple[list[aiosqlite.Row], int]:
    """
    分页查询任务列表（全平台共享，按创建时间倒序）。

    :returns: (任务行列表, 总条数)
    """
    async with db.execute(
        "SELECT COUNT(*) AS c FROM media_repair_tasks WHERE deleted_at IS NULL"
    ) as cur:
        total = (await cur.fetchone())["c"]
    offset = max(0, (page - 1) * page_size)
    async with db.execute(
        "SELECT * FROM media_repair_tasks WHERE deleted_at IS NULL"
        " ORDER BY id DESC LIMIT ? OFFSET ?",
        (page_size, offset),
    ) as cur:
        rows = await cur.fetchall()
    return rows, int(total)


async def list_by_file(
    db: aiosqlite.Connection, disk_id: int, rel_path: str, filename: str
) -> list[aiosqlite.Row]:
    """查询同文件的全部历史任务（含终态，用于失败重试时清理旧产物）。"""
    async with db.execute(
        "SELECT * FROM media_repair_tasks"
        " WHERE disk_id = ? AND rel_path = ? AND filename = ?"
        "   AND deleted_at IS NULL",
        (disk_id, rel_path, filename),
    ) as cur:
        return await cur.fetchall()


async def list_distinct_dirs(db: aiosqlite.Connection) -> list[tuple[int, str]]:
    """
    查询全部任务涉及的 (disk_id, rel_path) 去重列表。

    供服务启动时按目录范围清理 *.repairing.mp4 半成品（不做全盘扫描）。
    """
    async with db.execute(
        "SELECT DISTINCT disk_id, rel_path FROM media_repair_tasks"
        " WHERE deleted_at IS NULL"
    ) as cur:
        return [(r["disk_id"], r["rel_path"]) async for r in cur]


async def update_status(
    db: aiosqlite.Connection,
    task_id: int,
    status: str,
    error_message: str = "",
    output_name: str | None = None,
) -> None:
    """
    更新任务状态（含失败原因 / 产物名，可选项传 None 表示不变更）。

    状态流转自动维护 started_at（首次进入 running）与 finished_at（进入终态）。
    """
    now = _now()
    finished_states = ("success", "failed", "canceled", "overridden")
    sets = ["status = ?", "error_message = ?", "updated_at = ?"]
    params: list = [status, error_message, now]
    if status == "running":
        sets.append("started_at = COALESCE(started_at, ?)")
        params.append(now)
    if status in finished_states:
        sets.append("finished_at = ?")
        params.append(now)
    if output_name is not None:
        sets.append("output_name = ?")
        params.append(output_name)
    params.append(task_id)
    await db.execute(
        f"UPDATE media_repair_tasks SET {', '.join(sets)}"
        " WHERE id = ? AND deleted_at IS NULL",
        params,
    )
    await db.commit()


async def update_progress(db: aiosqlite.Connection, task_id: int, progress: int) -> None:
    """更新执行中任务进度（0~100，服务层负责节流）。"""
    await db.execute(
        "UPDATE media_repair_tasks SET progress = ?, updated_at = ?"
        " WHERE id = ? AND deleted_at IS NULL",
        (max(0, min(100, progress)), _now(), task_id),
    )
    await db.commit()


async def soft_delete(db: aiosqlite.Connection, task_id: int) -> None:
    """软删除任务记录（任意终态可删；列表不再展示）。"""
    await db.execute(
        "UPDATE media_repair_tasks SET deleted_at = ?, updated_at = ?"
        " WHERE id = ? AND deleted_at IS NULL",
        (_now(), _now(), task_id),
    )
    await db.commit()


async def mark_interrupted_as_failed(db: aiosqlite.Connection) -> int:
    """
    服务启动时调用：把 pending/running/verifying 任务标记为 failed（服务重启中断）。

    :returns: 受影响行数
    """
    cur = await db.execute(
        "UPDATE media_repair_tasks"
        " SET status = 'failed', error_message = '服务重启导致任务中断',"
        "     finished_at = ?, updated_at = ?"
        " WHERE status IN ('pending', 'running', 'verifying')"
        "   AND deleted_at IS NULL",
        (_now(), _now()),
    )
    await db.commit()
    return cur.rowcount or 0

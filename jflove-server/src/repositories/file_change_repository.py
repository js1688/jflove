"""
文件变更日志仓储层（v1.7.0）

职责：
  - 向 `file_changes` 表追加变更记录（append-only）
  - 按 `disk_id` + 子目录前缀 + `since_rev` 查询增量
  - 提供最大 / 最小 rev 与过期清理

设计要点：
  - `id` 即全局单调递增的 `rev`（AUTOINCREMENT，删除不回收号）
  - 同路径允许多条历史记录，**不建唯一索引**（软删除与唯一性冲突的规避）
  - 前缀匹配用 `substr` 而非 `LIKE`：大小写敏感、边界精确，避免 `photos` 误匹配 `photos2`
"""

from datetime import datetime, timezone

import aiosqlite

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _now() -> str:
    """返回当前 UTC 时间（ISO 8601 字符串），与库内其它表口径一致"""
    return datetime.now(timezone.utc).isoformat()


async def insert_many(
    db: aiosqlite.Connection,
    disk_id: int,
    rows: list[dict],
) -> int:
    """
    批量追加变更记录。

    :param db: 数据库连接
    :param disk_id: 虚拟磁盘 ID
    :param rows: 每项含 rel_path / op / is_dir / size / mtime / content_hash
    :returns: 实际写入条数（rows 为空时返回 0）
    """
    if not rows:
        return 0
    now = _now()
    payload = [
        (
            disk_id,
            r.get("rel_path", ""),
            r.get("op", "upsert"),
            1 if r.get("is_dir") else 0,
            int(r.get("size") or 0),
            float(r.get("mtime") or 0),
            r.get("content_hash", "") or "",
            now,
            now,
        )
        for r in rows
    ]
    await db.executemany(
        "INSERT INTO file_changes"
        " (disk_id, rel_path, op, is_dir, size, mtime, content_hash,"
        "  created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        payload,
    )
    await db.commit()
    return len(payload)


async def list_since(
    db: aiosqlite.Connection,
    disk_id: int,
    prefix: str,
    since_rev: int,
    limit: int,
) -> list[dict]:
    """
    查询某磁盘、某子目录范围内、rev 大于 since_rev 的变更。

    :param db: 数据库连接
    :param disk_id: 虚拟磁盘 ID
    :param prefix: 子目录前缀（空串 = 磁盘根）
    :param since_rev: 上次拿到的最大 rev（0 = 从头）
    :param limit: 最多返回条数
    :returns: 按 rev 升序的变更列表
    """
    prefix = (prefix or "").strip().strip("/")
    params: list = [disk_id, int(since_rev)]
    where = "disk_id = ? AND id > ?"
    if prefix:
        # 精确边界：要么就是该目录本身，要么以 `前缀/` 开头
        where += " AND (rel_path = ? OR substr(rel_path, 1, ?) = ?)"
        params.extend([prefix, len(prefix) + 1, prefix + "/"])
    params.append(int(limit))
    async with db.execute(
        "SELECT id, rel_path, op, is_dir, size, mtime, content_hash"
        f" FROM file_changes WHERE {where} ORDER BY id ASC LIMIT ?",
        params,
    ) as cur:
        return [dict(r) async for r in cur]


async def latest_rev(db: aiosqlite.Connection) -> int:
    """
    返回全局最大 rev（无记录时为 0）。

    用全局最大值而非按磁盘：客户端只需一个整数游标即可判定"是否还有更多"。
    """
    async with db.execute("SELECT MAX(id) FROM file_changes") as cur:
        row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


async def min_rev(db: aiosqlite.Connection) -> int:
    """返回全局最小 rev（无记录时为 0），供 `reset_required` 判定"""
    async with db.execute("SELECT MIN(id) FROM file_changes") as cur:
        row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


async def cleanup_old(db: aiosqlite.Connection, keep_rows: int) -> int:
    """
    只保留最近 `keep_rows` 条记录（防表无限增长）。

    注意：清理会让老的 `since_rev` 失效，调用方需以 `min_rev` 判定
    `reset_required`，让客户端回退到全量对账。

    :param db: 数据库连接
    :param keep_rows: 保留条数（<= 0 时不做清理）
    :returns: 删除条数
    """
    if keep_rows <= 0:
        return 0
    async with db.execute("SELECT MAX(id) FROM file_changes") as cur:
        row = await cur.fetchone()
    max_id = int(row[0] or 0) if row else 0
    threshold = max_id - keep_rows
    if threshold <= 0:
        return 0
    cur = await db.execute("DELETE FROM file_changes WHERE id <= ?", (threshold,))
    await db.commit()
    deleted = cur.rowcount or 0
    if deleted:
        logger.info("变更日志清理：删除 %s 条（保留最近 %s 条）", deleted, keep_rows)
    return deleted

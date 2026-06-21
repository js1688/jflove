"""
系统配置数据访问层

负责 config 表的 key-value 读写操作，采用 upsert 策略。
常用配置项：notes_disk_id（笔记目录所在虚拟磁盘 ID）等。
"""

from datetime import datetime, timezone
import aiosqlite


def _now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


async def get(db: aiosqlite.Connection, key: str) -> str | None:
    """
    按 key 查询配置值。

    :param db: 数据库连接
    :param key: 配置键名
    :returns: 配置值字符串，不存在则返回 None
    """
    async with db.execute(
        "SELECT value FROM config WHERE key = ? AND deleted_at IS NULL", (key,)
    ) as cur:
        row = await cur.fetchone()
        return row["value"] if row else None


async def set(db: aiosqlite.Connection, key: str, value: str) -> None:
    """
    设置配置项（upsert）：存在则更新，不存在则插入。

    :param db: 数据库连接
    :param key: 配置键名
    :param value: 配置值
    """
    now = _now()
    async with db.execute(
        "SELECT id FROM config WHERE key = ? AND deleted_at IS NULL", (key,)
    ) as cur:
        existing = await cur.fetchone()

    if existing:
        await db.execute(
            "UPDATE config SET value = ?, updated_at = ? WHERE key = ? AND deleted_at IS NULL",
            (value, now, key),
        )
    else:
        await db.execute(
            "INSERT INTO config (key, value, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (key, value, now, now),
        )
    await db.commit()


async def get_all(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    """
    查询所有有效配置项，按 key 字母序排列。

    :param db: 数据库连接
    :returns: 配置行列表，每行含 key 和 value
    """
    async with db.execute(
        "SELECT key, value FROM config WHERE deleted_at IS NULL ORDER BY key"
    ) as cur:
        return await cur.fetchall()

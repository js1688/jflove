"""
权限数据访问层

负责 user_permissions（磁盘权限）表的操作。
权限采用 upsert 策略：已存在则更新，不存在则插入。

笔记权限已于 v1.x 移除（每个用户独立配置笔记目录，全员均可使用笔记功能）。
"""

from datetime import datetime, timezone
import aiosqlite


def _now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


async def get_disk_permissions(
    db: aiosqlite.Connection, user_id: int
) -> list[aiosqlite.Row]:
    """
    查询指定用户的所有有效磁盘权限记录。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :returns: 权限行列表（每条对应一个虚拟磁盘）
    """
    async with db.execute(
        "SELECT * FROM user_permissions WHERE user_id = ? AND deleted_at IS NULL",
        (user_id,),
    ) as cur:
        return await cur.fetchall()


async def get_disk_permission(
    db: aiosqlite.Connection, user_id: int, disk_id: int
) -> aiosqlite.Row | None:
    """
    查询指定用户对指定虚拟磁盘的权限记录。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param disk_id: 虚拟磁盘 ID
    :returns: 权限行数据，不存在则返回 None
    """
    async with db.execute(
        "SELECT * FROM user_permissions"
        " WHERE user_id = ? AND virtual_disk_id = ? AND deleted_at IS NULL",
        (user_id, disk_id),
    ) as cur:
        return await cur.fetchone()


async def set_disk_permission(
    db: aiosqlite.Connection,
    user_id: int,
    disk_id: int,
    can_read: bool,
    can_write: bool,
    can_delete: bool,
) -> None:
    """
    设置用户对虚拟磁盘的操作权限（upsert）。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param disk_id: 虚拟磁盘 ID
    :param can_read: 是否允许读取（浏览目录、下载）
    :param can_write: 是否允许写入（上传、新建目录）
    :param can_delete: 是否允许删除
    """
    now = _now()
    existing = await get_disk_permission(db, user_id, disk_id)
    if existing:
        await db.execute(
            "UPDATE user_permissions SET can_read=?, can_write=?, can_delete=?, updated_at=?"
            " WHERE user_id=? AND virtual_disk_id=? AND deleted_at IS NULL",
            (1 if can_read else 0, 1 if can_write else 0, 1 if can_delete else 0,
             now, user_id, disk_id),
        )
    else:
        await db.execute(
            "INSERT INTO user_permissions"
            " (user_id, virtual_disk_id, can_read, can_write, can_delete, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, disk_id,
             1 if can_read else 0, 1 if can_write else 0, 1 if can_delete else 0,
             now, now),
        )
    await db.commit()


async def delete_disk_permission(
    db: aiosqlite.Connection, user_id: int, disk_id: int
) -> None:
    """
    软删除用户对指定虚拟磁盘的权限记录。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param disk_id: 虚拟磁盘 ID
    """
    await db.execute(
        "UPDATE user_permissions SET deleted_at=?, updated_at=?"
        " WHERE user_id=? AND virtual_disk_id=? AND deleted_at IS NULL",
        (_now(), _now(), user_id, disk_id),
    )
    await db.commit()

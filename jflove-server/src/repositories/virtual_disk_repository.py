"""
虚拟磁盘数据访问层

负责 virtual_disks 表的 CRUD 操作，支持软删除。
虚拟磁盘是服务端真实磁盘目录的逻辑映射，name 为展示名称，real_path 为实际路径。
"""

from datetime import datetime, timezone
import aiosqlite


def _now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


async def list_all(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    """
    查询所有未删除的虚拟磁盘，按创建时间升序排列。

    :param db: 数据库连接
    :returns: 虚拟磁盘行列表
    """
    async with db.execute(
        "SELECT * FROM virtual_disks WHERE deleted_at IS NULL ORDER BY created_at"
    ) as cur:
        return await cur.fetchall()


async def find_by_id(db: aiosqlite.Connection, disk_id: int) -> aiosqlite.Row | None:
    """
    按主键查询未删除的虚拟磁盘。

    :param db: 数据库连接
    :param disk_id: 虚拟磁盘 ID
    :returns: 磁盘行数据，不存在则返回 None
    """
    async with db.execute(
        "SELECT * FROM virtual_disks WHERE id = ? AND deleted_at IS NULL", (disk_id,)
    ) as cur:
        return await cur.fetchone()


async def create(
    db: aiosqlite.Connection, name: str, real_path: str, created_by: int
) -> int:
    """
    创建虚拟磁盘记录。

    :param db: 数据库连接
    :param name: 虚拟磁盘展示名称，如"文档库"
    :param real_path: 服务端真实磁盘目录绝对路径
    :param created_by: 创建者用户 ID（仅限管理员）
    :returns: 新记录的自增主键 ID
    """
    now = _now()
    async with db.execute(
        "INSERT INTO virtual_disks (name, real_path, created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (name, real_path, created_by, now, now),
    ) as cur:
        await db.commit()
        return cur.lastrowid


async def update(
    db: aiosqlite.Connection, disk_id: int, name: str, real_path: str
) -> None:
    """
    更新虚拟磁盘的名称和真实路径。

    :param db: 数据库连接
    :param disk_id: 目标虚拟磁盘 ID
    :param name: 新展示名称
    :param real_path: 新真实路径
    """
    await db.execute(
        "UPDATE virtual_disks SET name = ?, real_path = ?, updated_at = ? WHERE id = ?",
        (name, real_path, _now(), disk_id),
    )
    await db.commit()


async def soft_delete(db: aiosqlite.Connection, disk_id: int) -> None:
    """
    软删除虚拟磁盘记录，设置 deleted_at 时间戳。

    :param db: 数据库连接
    :param disk_id: 目标虚拟磁盘 ID
    """
    await db.execute(
        "UPDATE virtual_disks SET deleted_at = ?, updated_at = ? WHERE id = ?",
        (_now(), _now(), disk_id),
    )
    await db.commit()

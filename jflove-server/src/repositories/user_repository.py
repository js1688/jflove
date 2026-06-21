"""
用户数据访问层

负责 users 表的所有 CRUD 操作，包含软删除支持。
"""

from datetime import datetime, timezone
import aiosqlite


def _now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


async def find_by_username(db: aiosqlite.Connection, username: str) -> aiosqlite.Row | None:
    """
    按用户名查询未删除用户。

    :param db: 数据库连接
    :param username: 用户名
    :returns: 用户行数据，不存在则返回 None
    """
    async with db.execute(
        "SELECT * FROM users WHERE username = ? AND deleted_at IS NULL", (username,)
    ) as cur:
        return await cur.fetchone()


async def find_by_id(db: aiosqlite.Connection, user_id: int) -> aiosqlite.Row | None:
    """
    按主键查询未删除用户。

    :param db: 数据库连接
    :param user_id: 用户 ID
    :returns: 用户行数据，不存在则返回 None
    """
    async with db.execute(
        "SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)
    ) as cur:
        return await cur.fetchone()


async def count_admins(db: aiosqlite.Connection) -> int:
    """
    统计未删除的管理员数量，用于判断是否已初始化管理员账号。

    :param db: 数据库连接
    :returns: 管理员数量
    """
    async with db.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND deleted_at IS NULL"
    ) as cur:
        row = await cur.fetchone()
        return row[0]


async def create(
    db: aiosqlite.Connection,
    username: str,
    password_hash: str,
    role: str = "user",
) -> int:
    """
    创建新用户。

    :param db: 数据库连接
    :param username: 用户名（唯一）
    :param password_hash: bcrypt 密码哈希
    :param role: 角色，admin 或 user，默认 user
    :returns: 新用户的自增主键 ID
    """
    now = _now()
    async with db.execute(
        "INSERT INTO users (username, password_hash, role, enabled, created_at, updated_at)"
        " VALUES (?, ?, ?, 1, ?, ?)",
        (username, password_hash, role, now, now),
    ) as cur:
        await db.commit()
        return cur.lastrowid


async def list_all(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    """
    查询所有未删除用户，按创建时间升序排列。

    :param db: 数据库连接
    :returns: 用户行列表
    """
    async with db.execute(
        "SELECT * FROM users WHERE deleted_at IS NULL ORDER BY created_at"
    ) as cur:
        return await cur.fetchall()


async def update_password(db: aiosqlite.Connection, user_id: int, password_hash: str) -> None:
    """
    更新用户密码哈希。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param password_hash: 新的 bcrypt 密码哈希
    """
    await db.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (password_hash, _now(), user_id),
    )
    await db.commit()


async def update_enabled(db: aiosqlite.Connection, user_id: int, enabled: bool) -> None:
    """
    更新用户启用/禁用状态。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param enabled: True 启用，False 禁用
    """
    await db.execute(
        "UPDATE users SET enabled = ?, updated_at = ? WHERE id = ?",
        (1 if enabled else 0, _now(), user_id),
    )
    await db.commit()


async def soft_delete(db: aiosqlite.Connection, user_id: int) -> None:
    """
    软删除用户，设置 deleted_at 时间戳。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    """
    await db.execute(
        "UPDATE users SET deleted_at = ?, updated_at = ? WHERE id = ?",
        (_now(), _now(), user_id),
    )
    await db.commit()


async def update_notes_disk_id(
    db: aiosqlite.Connection, user_id: int, disk_id: int | None
) -> None:
    """
    更新用户的笔记存储磁盘配置（仅磁盘 ID，保持 path 不变）。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param disk_id: 虚拟磁盘 ID，None 表示清除配置
    """
    await db.execute(
        "UPDATE users SET notes_disk_id = ?, updated_at = ? WHERE id = ?",
        (disk_id, _now(), user_id),
    )
    await db.commit()


async def update_notes_config(
    db: aiosqlite.Connection,
    user_id: int,
    disk_id: int | None,
    notes_path: str = "",
) -> None:
    """
    更新用户的笔记存储磁盘 ID 和子目录路径。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param disk_id: 虚拟磁盘 ID，None 表示清除配置
    :param notes_path: 磁盘内的子目录相对路径，空字符串表示磁盘根目录
    """
    await db.execute(
        "UPDATE users SET notes_disk_id = ?, notes_path = ?, updated_at = ? WHERE id = ?",
        (disk_id, notes_path.strip("/") if notes_path else "", _now(), user_id),
    )
    await db.commit()

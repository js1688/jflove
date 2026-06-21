"""
会话数据访问层

负责 sessions 表的 CRUD 操作。
注意：会话密钥（session_key）不存入数据库，仅在内存中维护，
此表仅用于记录会话元数据和 JWT 令牌哈希（用于吊销校验）。
"""

from datetime import datetime, timezone
import aiosqlite


def _now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


async def create(
    db: aiosqlite.Connection,
    user_id: int,
    session_id: str,
    jwt_token_hash: str,
    expires_at: str,
) -> None:
    """
    创建新会话记录。

    :param db: 数据库连接
    :param user_id: 关联用户 ID
    :param session_id: 会话唯一标识（UUID），与内存中的 session_key 对应
    :param jwt_token_hash: JWT 令牌的 SHA256 哈希，用于令牌吊销校验
    :param expires_at: 会话过期时间（ISO 格式）
    """
    now = _now()
    await db.execute(
        "INSERT INTO sessions "
        "(user_id, session_id, jwt_token_hash, expires_at, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, session_id, jwt_token_hash, expires_at, now, now),
    )
    await db.commit()


async def find_by_session_id(
    db: aiosqlite.Connection, session_id: str
) -> aiosqlite.Row | None:
    """
    按会话 ID 查询未删除的会话记录。

    :param db: 数据库连接
    :param session_id: 会话 UUID
    :returns: 会话行数据，不存在则返回 None
    """
    async with db.execute(
        "SELECT * FROM sessions WHERE session_id = ? AND deleted_at IS NULL",
        (session_id,),
    ) as cur:
        return await cur.fetchone()


async def revoke(db: aiosqlite.Connection, session_id: str) -> None:
    """
    软删除指定会话（令牌吊销）。

    :param db: 数据库连接
    :param session_id: 要吊销的会话 UUID
    """
    await db.execute(
        "UPDATE sessions SET deleted_at = ?, updated_at = ? WHERE session_id = ?",
        (_now(), _now(), session_id),
    )
    await db.commit()


async def revoke_by_user(db: aiosqlite.Connection, user_id: int) -> None:
    """
    批量吊销指定用户的所有有效会话（用于强制下线）。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    """
    await db.execute(
        "UPDATE sessions SET deleted_at = ?, updated_at = ?"
        " WHERE user_id = ? AND deleted_at IS NULL",
        (_now(), _now(), user_id),
    )
    await db.commit()

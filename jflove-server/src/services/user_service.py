"""
用户管理业务逻辑层

处理普通用户的创建、密码修改、启用/禁用、删除操作。
管理员账号受保护，不可被删除或禁用。
"""

import bcrypt
import aiosqlite

from src.repositories import user_repository
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def list_users(db: aiosqlite.Connection) -> list[dict]:
    """
    获取所有用户列表（不含密码哈希）。

    :param db: 数据库连接
    :returns: 用户信息列表，每项含 id、username、role、enabled、created_at
    """
    rows = await user_repository.list_all(db)
    return [
        {
            "id": r["id"],
            "username": r["username"],
            "role": r["role"],
            "enabled": bool(r["enabled"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


async def create_user(db: aiosqlite.Connection, username: str, password: str) -> int:
    """
    创建普通用户账号，密码使用 bcrypt 哈希存储。

    用户名唯一性口径（v1.5.0）：**只对活跃账号唯一**。
    `users.username` 是普通索引，另有部分唯一索引
    `users_username_active_uidx ... WHERE deleted_at IS NULL` 兜底。
    因此被软删除的历史行不占用用户名 —— 删掉账号后可以用同名重建，
    这是用户反馈「账号删除后添加相同账号会报错」的修复点。

    :param db: 数据库连接
    :param username: 用户名（活跃账号内唯一）
    :param password: 明文密码
    :returns: 新用户的主键 ID
    :raises ValueError: 用户名已存在（仅指活跃账号）
    """
    # 只对**活跃**账号查重：`users.username` 已改为普通索引 +
    # 「活跃行唯一」的部分唯一索引（v1.5.0），被软删除的历史行不再占用用户名，
    # 管理员删了账号可以用同名重建。
    existing = await user_repository.find_by_username(db, username)
    if existing:
        # 文案面向界面展示：明确指出是"已经被占用的活跃账号"，
        # 而不是数据库术语（前端会把它原样弹给用户）。
        raise ValueError(f"用户名「{username}」已存在，请换一个")
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user_id = await user_repository.create(db, username, password_hash, role="user")
    logger.info("普通用户已创建: %s", username)
    return user_id


async def update_password(
    db: aiosqlite.Connection, user_id: int, new_password: str
) -> None:
    """
    修改指定用户的密码。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param new_password: 新明文密码，内部重新 bcrypt 哈希
    :raises ValueError: 用户不存在
    """
    user = await user_repository.find_by_id(db, user_id)
    if not user:
        raise ValueError("用户不存在")
    password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    await user_repository.update_password(db, user_id, password_hash)


async def set_enabled(
    db: aiosqlite.Connection, user_id: int, enabled: bool
) -> None:
    """
    启用或禁用用户账号（管理员账号不可操作）。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param enabled: True 启用，False 禁用
    :raises ValueError: 用户不存在或目标为管理员账号
    """
    user = await user_repository.find_by_id(db, user_id)
    if not user:
        raise ValueError("用户不存在")
    if user["role"] == "admin":
        raise ValueError("不能禁用管理员账号")
    await user_repository.update_enabled(db, user_id, enabled)


async def delete_user(db: aiosqlite.Connection, user_id: int) -> None:
    """
    软删除用户账号（管理员账号不可删除）。

    保留软删除以遵守 AGENTS.md §5.2「必备字段 deleted_at（软删除）」。
    副作用：`users.username` 是全局唯一约束、不区分是否删除，所以该用户名会
    被历史行占用、无法再次创建 —— 这一点由 `create_user` 给出明确提示。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :raises ValueError: 用户不存在或目标为管理员账号
    """
    user = await user_repository.find_by_id(db, user_id)
    if not user:
        raise ValueError("用户不存在")
    if user["role"] == "admin":
        raise ValueError("不能删除管理员账号")
    await user_repository.soft_delete(db, user_id)
    logger.info("用户已删除: id=%d", user_id)

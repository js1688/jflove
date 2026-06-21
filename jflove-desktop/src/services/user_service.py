"""
用户管理服务模块（管理员专用）

封装用户的创建、查询、密码修改、启用/禁用、删除操作。
"""

from src.utils.http_client import http_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


def list_users() -> list[dict]:
    """
    获取系统中所有用户列表（不含密码哈希）。

    :returns: 用户列表，每项含 id、username、role、enabled、created_at
    :raises ApiError: 无管理员权限
    """
    resp = http_client.get("/api/v1/users")
    return resp.get("users", [])


def create_user(username: str, password: str) -> int:
    """
    创建普通用户账号。

    :param username: 用户名（系统唯一）
    :param password: 初始密码
    :returns: 新用户主键 ID
    :raises ApiError: 用户名已存在或无管理员权限
    """
    resp = http_client.post("/api/v1/users", {
        "username": username,
        "password": password,
    })
    logger.info("用户已创建: %s", username)
    return resp.get("id")


def delete_user(user_id: int) -> None:
    """
    软删除指定用户账号（管理员账号不可删除）。

    :param user_id: 目标用户 ID
    :raises ApiError: 目标为管理员或无权限
    """
    http_client.delete(f"/api/v1/users/{user_id}")
    logger.info("用户已删除: id=%d", user_id)


def update_password(user_id: int, new_password: str) -> None:
    """
    修改指定用户的密码。

    :param user_id: 目标用户 ID
    :param new_password: 新密码
    :raises ApiError: 用户不存在或无权限
    """
    http_client.put(f"/api/v1/users/{user_id}/password", {
        "password": new_password,
    })
    logger.info("用户密码已更新: id=%d", user_id)


def set_enabled(user_id: int, enabled: bool) -> None:
    """
    启用或禁用用户账号（管理员账号不可操作）。

    :param user_id: 目标用户 ID
    :param enabled: True 启用，False 禁用
    :raises ApiError: 目标为管理员或无权限
    """
    http_client.put(f"/api/v1/users/{user_id}/enabled", {
        "enabled": enabled,
    })
    logger.info("用户状态已更新: id=%d, enabled=%s", user_id, enabled)

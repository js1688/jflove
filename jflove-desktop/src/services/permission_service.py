"""
权限管理服务模块（管理员专用）

封装用户磁盘权限的查询与配置操作。

笔记权限已于 v1.x 移除：所有登录用户均可使用笔记功能，
笔记目录由用户在「设置」页面独立配置，互不影响。
"""

from src.utils.http_client import http_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_user_disk_permissions(user_id: int) -> list[dict]:
    """
    获取指定用户对所有虚拟磁盘的权限配置。

    :param user_id: 目标用户 ID
    :returns: 权限列表，每项含 virtual_disk_id、can_read、can_write、can_delete
    :raises ApiError: 无管理员权限
    """
    resp = http_client.get(f"/api/v1/permissions/users/{user_id}/disks")
    return resp.get("permissions", [])


def set_disk_permission(
    user_id: int,
    disk_id: int,
    can_read: bool,
    can_write: bool,
    can_delete: bool,
) -> None:
    """
    设置用户对指定虚拟磁盘的操作权限（已有配置则覆盖）。

    :param user_id: 目标用户 ID
    :param disk_id: 目标虚拟磁盘 ID
    :param can_read: 是否允许读取
    :param can_write: 是否允许写入
    :param can_delete: 是否允许删除
    :raises ApiError: 用户或磁盘不存在，或无管理员权限
    """
    http_client.post(f"/api/v1/permissions/users/{user_id}/disks/{disk_id}", {
        "can_read": can_read,
        "can_write": can_write,
        "can_delete": can_delete,
    })
    logger.info("磁盘权限已设置: user=%d, disk=%d", user_id, disk_id)


def delete_disk_permission(user_id: int, disk_id: int) -> None:
    """
    删除用户对指定虚拟磁盘的权限配置。

    :param user_id: 目标用户 ID
    :param disk_id: 目标虚拟磁盘 ID
    :raises ApiError: 无管理员权限
    """
    http_client.delete(f"/api/v1/permissions/users/{user_id}/disks/{disk_id}")
    logger.info("磁盘权限已删除: user=%d, disk=%d", user_id, disk_id)

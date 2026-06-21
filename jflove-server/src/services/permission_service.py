"""
权限业务逻辑层

处理用户对虚拟磁盘的权限设置与校验。
管理员默认拥有全部磁盘权限，普通用户须由管理员显式授权。

笔记权限已于 v1.x 移除：每个用户独立配置自己的笔记目录
（users.notes_disk_id / notes_path），所有登录用户均可使用笔记功能。
"""

import aiosqlite

from src.repositories import permission_repository, user_repository, virtual_disk_repository
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def get_user_disk_permissions(
    db: aiosqlite.Connection, user_id: int
) -> list[dict]:
    """
    获取指定用户的所有磁盘权限配置。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :returns: 权限列表，每项含 virtual_disk_id、can_read、can_write、can_delete
    """
    rows = await permission_repository.get_disk_permissions(db, user_id)
    return [
        {
            "virtual_disk_id": r["virtual_disk_id"],
            "can_read": bool(r["can_read"]),
            "can_write": bool(r["can_write"]),
            "can_delete": bool(r["can_delete"]),
        }
        for r in rows
    ]


async def set_user_disk_permission(
    db: aiosqlite.Connection,
    user_id: int,
    disk_id: int,
    can_read: bool,
    can_write: bool,
    can_delete: bool,
) -> None:
    """
    设置用户对指定虚拟磁盘的操作权限，操作前校验用户和磁盘是否存在。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param disk_id: 目标虚拟磁盘 ID
    :param can_read: 是否允许读取（浏览目录、下载文件）
    :param can_write: 是否允许写入（上传、新建目录）
    :param can_delete: 是否允许删除文件/目录
    :raises ValueError: 用户或磁盘不存在
    """
    user = await user_repository.find_by_id(db, user_id)
    if not user:
        raise ValueError("用户不存在")
    disk = await virtual_disk_repository.find_by_id(db, disk_id)
    if not disk:
        raise ValueError("虚拟磁盘不存在")
    await permission_repository.set_disk_permission(
        db, user_id, disk_id, can_read, can_write, can_delete
    )


async def delete_user_disk_permission(
    db: aiosqlite.Connection, user_id: int, disk_id: int
) -> None:
    """
    删除用户对指定虚拟磁盘的权限配置。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param disk_id: 目标虚拟磁盘 ID
    """
    await permission_repository.delete_disk_permission(db, user_id, disk_id)


async def check_disk_permission(
    db: aiosqlite.Connection,
    user_id: int,
    disk_id: int,
    action: str,
) -> bool:
    """
    校验用户是否拥有对指定磁盘的某项操作权限。

    :param db: 数据库连接
    :param user_id: 目标用户 ID
    :param disk_id: 虚拟磁盘 ID
    :param action: 操作类型，取值为 'read'、'write' 或 'delete'
    :returns: True 表示有权限，False 表示无权限
    """
    perm = await permission_repository.get_disk_permission(db, user_id, disk_id)
    if not perm:
        return False
    return bool(perm[f"can_{action}"])

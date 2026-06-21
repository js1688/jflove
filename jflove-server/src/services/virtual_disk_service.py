"""
虚拟磁盘业务逻辑层

处理虚拟磁盘的增删改查，并在创建/更新时校验服务端真实路径是否存在。
"""

import os
import aiosqlite

from src.repositories import virtual_disk_repository
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def list_disks(db: aiosqlite.Connection) -> list[dict]:
    """
    获取所有虚拟磁盘列表。

    :param db: 数据库连接
    :returns: 磁盘信息列表，每项含 id、name、real_path、created_by、created_at
    """
    rows = await virtual_disk_repository.list_all(db)
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "real_path": r["real_path"],
            "created_by": r["created_by"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


async def create_disk(
    db: aiosqlite.Connection, name: str, real_path: str, created_by: int
) -> int:
    """
    创建虚拟磁盘，创建前校验服务端目录是否存在。

    :param db: 数据库连接
    :param name: 虚拟磁盘展示名称
    :param real_path: 服务端真实磁盘目录绝对路径
    :param created_by: 操作者用户 ID（仅管理员）
    :returns: 新虚拟磁盘的主键 ID
    :raises ValueError: 指定路径不存在或不是目录
    """
    if not os.path.isdir(real_path):
        raise ValueError(f"服务端路径不存在或不是目录: {real_path}")
    disk_id = await virtual_disk_repository.create(db, name, real_path, created_by)
    logger.info("虚拟磁盘已创建: name=%s, path=%s", name, real_path)
    return disk_id


async def update_disk(
    db: aiosqlite.Connection, disk_id: int, name: str, real_path: str
) -> None:
    """
    更新虚拟磁盘名称和路径，更新前校验新路径是否存在。

    :param db: 数据库连接
    :param disk_id: 目标虚拟磁盘 ID
    :param name: 新展示名称
    :param real_path: 新服务端真实路径
    :raises ValueError: 磁盘不存在或新路径不合法
    """
    disk = await virtual_disk_repository.find_by_id(db, disk_id)
    if not disk:
        raise ValueError("虚拟磁盘不存在")
    if not os.path.isdir(real_path):
        raise ValueError(f"服务端路径不存在或不是目录: {real_path}")
    await virtual_disk_repository.update(db, disk_id, name, real_path)


async def delete_disk(db: aiosqlite.Connection, disk_id: int) -> None:
    """
    软删除虚拟磁盘记录（不删除服务端真实文件）。

    :param db: 数据库连接
    :param disk_id: 目标虚拟磁盘 ID
    :raises ValueError: 磁盘不存在
    """
    disk = await virtual_disk_repository.find_by_id(db, disk_id)
    if not disk:
        raise ValueError("虚拟磁盘不存在")
    await virtual_disk_repository.soft_delete(db, disk_id)
    logger.info("虚拟磁盘已删除: id=%d", disk_id)

"""
虚拟磁盘服务模块（管理员专用）

封装虚拟磁盘的增删改查操作。
"""

from src.utils.http_client import http_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


def list_disks() -> list[dict]:
    """
    获取所有虚拟磁盘列表。

    :returns: 磁盘列表，每项含 id、name、real_path、created_by、created_at
    :raises ApiError: 无管理员权限
    """
    resp = http_client.get("/api/v1/virtual-disks")
    return resp.get("disks", [])


def create_disk(name: str, real_path: str) -> int:
    """
    注册新的虚拟磁盘，服务端目录须已存在。

    :param name: 磁盘展示名称
    :param real_path: 服务端真实目录绝对路径
    :returns: 新磁盘主键 ID
    :raises ApiError: 路径不存在或无管理员权限
    """
    resp = http_client.post("/api/v1/virtual-disks", {
        "name": name,
        "real_path": real_path,
    })
    logger.info("虚拟磁盘已创建: %s → %s", name, real_path)
    return resp.get("id")


def update_disk(disk_id: int, name: str, real_path: str) -> None:
    """
    更新虚拟磁盘名称和路径。

    :param disk_id: 目标磁盘 ID
    :param name: 新展示名称
    :param real_path: 新服务端真实路径
    :raises ApiError: 磁盘不存在或路径非法
    """
    http_client.put(f"/api/v1/virtual-disks/{disk_id}", {
        "name": name,
        "real_path": real_path,
    })
    logger.info("虚拟磁盘已更新: id=%d", disk_id)


def delete_disk(disk_id: int) -> None:
    """
    软删除虚拟磁盘（不删除服务端真实文件）。

    :param disk_id: 目标磁盘 ID
    :raises ApiError: 磁盘不存在或无权限
    """
    http_client.delete(f"/api/v1/virtual-disks/{disk_id}")
    logger.info("虚拟磁盘已删除: id=%d", disk_id)


def browse_dirs(disk_id: int, path: str = "") -> list[dict]:
    """
    列出指定虚拟磁盘内某路径下的所有子目录，用于笔记目录选择。

    :param disk_id: 虚拟磁盘 ID
    :param path: 相对路径（空字符串表示根目录）
    :returns: 子目录列表，每项含 name（目录名）和 path（相对磁盘根的路径）
    :raises ApiError: 磁盘不存在、路径不存在或无权限
    """
    resp = http_client.get(f"/api/v1/virtual-disks/{disk_id}/browse", {"path": path})
    return resp.get("dirs", [])

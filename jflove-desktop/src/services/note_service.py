"""
笔记服务模块

封装笔记目录下 .md 文件的增删改查和重命名操作。
"""

from src.utils.http_client import http_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


def list_notes() -> list[dict]:
    """
    获取笔记目录下所有 .md 文件列表（按文件名排序）。

    :returns: 笔记列表，每项含 filename、size、modified_at
    :raises ApiError: 无权限或笔记目录未配置
    """
    resp = http_client.get("/api/v1/notes/list")
    return resp.get("notes", [])


def read_note(filename: str) -> str:
    """
    读取指定笔记文件的文本内容。

    :param filename: 笔记文件名（须以 .md 结尾）
    :returns: 笔记文本内容（UTF-8）
    :raises ApiError: 无权限或文件不存在
    """
    resp = http_client.get("/api/v1/notes/read", {"filename": filename})
    return resp.get("content", "")


def write_note(filename: str, content: str) -> None:
    """
    写入（新建或覆盖）笔记文件内容。

    :param filename: 笔记文件名（须以 .md 结尾）
    :param content: 笔记文本内容
    :raises ApiError: 无权限
    """
    http_client.post("/api/v1/notes/write", {
        "filename": filename,
        "content": content,
    })
    logger.info("笔记已保存: %s", filename)


def delete_note(filename: str) -> None:
    """
    删除指定笔记文件（v1.1.4 更新 docstring：原 410 改为 404）。

    :param filename: 笔记文件名
    :raises ApiError: 无权限或文件不存在（404）
    """
    http_client.delete("/api/v1/notes", {"filename": filename})
    logger.info("笔记已删除: %s", filename)


def rename_note(old_name: str, new_name: str) -> None:
    """
    重命名笔记文件（v1.1.4 更新 docstring：原 410 改为 404）。

    :param old_name: 原文件名
    :param new_name: 新文件名（须以 .md 结尾）
    :raises ApiError: 无权限（403）/ 文件不存在（404）/ 文件名冲突（409）
    """
    http_client.post("/api/v1/notes/rename", {
        "old_name": old_name,
        "new_name": new_name,
    })
    logger.info("笔记已重命名: %s → %s", old_name, new_name)


def get_notes_disk() -> dict:
    """
    获取当前用户的笔记目录配置及可用磁盘列表。

    :returns: 含 disk_id（当前磁盘 ID 或 None）、path（子目录路径）和 disks 的字典
    :raises ApiError: 请求失败
    """
    return http_client.get("/api/v1/notes/disk-config")


def set_notes_disk(disk_id: int | None, path: str = "") -> None:
    """
    设置当前用户的笔记存储磁盘和子目录。

    :param disk_id: 目标磁盘 ID，None 表示清除配置
    :param path: 磁盘内子目录相对路径，空字符串表示磁盘根目录
    :raises ApiError: 请求失败
    """
    http_client.put("/api/v1/notes/disk-config", {"disk_id": disk_id, "path": path})
    logger.info("笔记目录配置已更新: disk_id=%s, path=%s", disk_id, path)

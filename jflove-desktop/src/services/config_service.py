"""
系统配置服务模块（管理员专用）

封装系统级配置项的读取和更新操作。
"""

from src.utils.http_client import http_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_all_config() -> list[dict]:
    """
    获取所有系统配置项。

    :returns: 配置列表，每项含 key、value
    :raises ApiError: 无管理员权限
    """
    resp = http_client.get("/api/v1/config")
    return resp.get("config", [])


def update_config(key: str, value: str) -> None:
    """
    更新指定配置项的值（键不存在时自动创建）。

    常用配置键：
      - notes_disk_id：笔记目录对应的虚拟磁盘 ID

    :param key: 配置项键名
    :param value: 配置项值
    :raises ApiError: key 为空或无管理员权限
    """
    http_client.put("/api/v1/config", {
        "key": key,
        "value": value,
    })
    logger.info("配置已更新: %s=%s", key, value)

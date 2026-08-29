"""
媒体修复服务（v1.4.2）

封装手动离线修复的任务管理接口（与后端 repair_controller 对应）：
  - create：创建修复任务（健康文件被服务端拒绝：400 无需修复）
  - list：分页任务列表（全平台共享，所有登录用户可见）
  - cancel / override / delete_artifact / delete_record

说明：
  - 全部走 http_client（加密信封），UI 层不得直接发 HTTP
  - 服务端对操作类接口统一校验磁盘写+删权限；只读账号可看列表但操作会 403
"""

from src.utils.http_client import http_client
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 任务终态集合（列表轮询判断用）
TERMINAL_STATUSES = ("success", "failed", "canceled", "overridden")


def create_task(disk_id: int, path: str, filename: str) -> dict:
    """
    创建媒体修复任务（文件管理右键 / 播放失败弹窗「立即修复」）。

    :param disk_id: 虚拟磁盘 ID
    :param path: 文件所在目录（磁盘内相对路径）
    :param filename: 文件名（含扩展名）
    :returns: {"task_id": int, "message": str}
    :raises ApiError: 403 无写+删权限；400 健康无需修复 / 无法修复 / 已有任务
    """
    resp = http_client.post("/api/v1/files/repair/create", {
        "disk_id": disk_id,
        "path": path,
        "filename": filename,
    })
    logger.info("媒体修复任务已创建: task_id=%s filename=%s", resp.get("task_id"), filename)
    return resp


def list_tasks(page: int = 1, page_size: int = 50) -> dict:
    """
    分页获取修复任务列表（全平台共享）。

    :returns: {"total": int, "tasks": list[dict]}，任务项字段见后端开发记录
    :raises ApiError: 服务端错误
    """
    return http_client.get("/api/v1/files/repair/tasks", {
        "page": page,
        "page_size": page_size,
    })


def cancel_task(task_id: int) -> None:
    """取消排队中/执行中的任务（执行中会终止 ffmpeg 并清理半成品）。"""
    http_client.post("/api/v1/files/repair/cancel", {"task_id": task_id})
    logger.info("修复任务已取消: task_id=%s", task_id)


def override_origin(task_id: int) -> None:
    """
    覆盖原文件（原损坏文件被直接删除、不留备份，调用前必须用户二次确认）。
    """
    http_client.post("/api/v1/files/repair/override", {"task_id": task_id})
    logger.info("修复产物已覆盖原文件: task_id=%s", task_id)


def delete_artifact(task_id: int) -> None:
    """删除修复成功但尚未覆盖的产物。"""
    http_client.post("/api/v1/files/repair/delete-artifact", {"task_id": task_id})
    logger.info("修复产物已删除: task_id=%s", task_id)


def delete_record(task_id: int) -> None:
    """删除终态任务记录（软删除，不影响磁盘产物）。"""
    http_client.post("/api/v1/files/repair/delete-record", {"task_id": task_id})
    logger.info("修复任务记录已删除: task_id=%s", task_id)

"""
同步业务逻辑层（v1.1.6 精简版）

v1.1.6 变更：
  - 移除全部 sync_configs CRUD 函数（配置改为客户端本地存储）
  - 移除 touch_synced（last_synced_at 改为客户端本地记录）
  - 保留 list_remote_snapshot，签名改为 (db, user_id, role, disk_id, remote_path)
  - 保留 _safe_join（路径越界防护，供本模块内部使用）

关键约束：
  - 不允许从文件系统删除文件——同步过程的"补全"操作由客户端执行上传/下载
"""

import os
from pathlib import Path

import aiosqlite

from src.repositories import virtual_disk_repository
from src.services.permission_service import check_disk_permission
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _safe_join(base: str, rel: str) -> str:
    """
    将相对路径 rel 限定在 base 内，防止目录遍历攻击。

    :param base: 根目录绝对路径
    :param rel: 相对路径
    :returns: 限定后的绝对路径
    :raises PermissionError: 路径越界
    """
    base_path = Path(base).resolve()
    target = (base_path / rel.lstrip("/")).resolve()
    if not str(target).startswith(str(base_path)):
        raise PermissionError("非法路径访问")
    return str(target)


async def list_remote_snapshot(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    remote_path: str = "",
) -> list[dict]:
    """
    递归扫描指定磁盘子目录，返回文件清单（不含目录）。

    扫描结果由客户端用作 diff 输入：与本地索引取差集，决定上传/下载。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 当前用户角色
    :param disk_id: 虚拟磁盘 ID
    :param remote_path: 磁盘内子目录相对路径（空串=根目录）
    :returns: 列表，每项含 path（相对 remote_path 的相对路径，使用 / 分隔）、
              size（字节）、modified_at（Unix 时间戳，浮点秒）
    :raises PermissionError: 无读权限
    :raises ValueError: 磁盘不存在
    """
    remote_path = (remote_path or "").strip().strip("/")

    # 1) 磁盘存在性检查
    disk = await virtual_disk_repository.find_by_id(db, disk_id)
    if not disk:
        raise ValueError("虚拟磁盘不存在")

    # 2) 路径越界防护（不要求目录已存在）
    _safe_join(disk["real_path"], remote_path)

    # 3) 权限校验（admin 免检）
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "read"):
            raise PermissionError("对目标磁盘没有读取权限")

    base = _safe_join(disk["real_path"], remote_path)
    files: list[dict] = []

    if not os.path.isdir(base):
        # 远端目录尚未存在 → 视为空快照
        return files

    base_len = len(base.rstrip(os.sep)) + 1
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                stat = os.stat(full)
            except OSError as e:
                logger.warning("快照扫描跳过：%s（%s）", full, e)
                continue
            rel = full[base_len:].replace(os.sep, "/")
            files.append({
                "path": rel,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
            })
    return files

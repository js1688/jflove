"""
虚拟磁盘业务逻辑层

处理虚拟磁盘的增删改查，并在创建/更新时校验服务端真实路径是否存在。
"""

import os
import string
import sys
import aiosqlite

from src.repositories import virtual_disk_repository
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 服务器目录浏览单次返回上限（防止超大目录拖垮界面；超限时 truncated=True）
SERVER_DIR_LIMIT = 2000


class ServerDirError(Exception):
    """服务器目录浏览的业务错误（带 HTTP 状态码，供 controller 直接映射）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _server_roots() -> list[dict]:
    """
    返回服务器目录浏览的常见起点（POSIX：/、/storage、/data、~；Windows：各盘符 + ~）。

    :returns: `[{"name": 展示名, "path": 绝对路径}]`，去重且只保留真实存在的目录
    """
    candidates: list[str] = []
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.isdir(root):
                candidates.append(root)
    else:
        candidates.extend(["/", "/storage", "/data"])
    home = os.path.expanduser("~")
    if home:
        candidates.append(home)

    roots: list[dict] = []
    seen: set[str] = set()
    for raw in candidates:
        normalized = os.path.normpath(raw)
        key = os.path.normcase(normalized)
        if key in seen or not os.path.isdir(normalized):
            continue
        seen.add(key)
        name = os.path.basename(normalized.rstrip("\\/")) or normalized
        roots.append({"name": name, "path": normalized})
    return roots


def list_server_dirs(path: str = "") -> dict:
    """
    列出服务器本机某个绝对路径下的子目录（只列目录，不读文件）。

    :param path: 绝对路径；空字符串表示使用 roots 第一项
    :returns: `path` / `parent` / `dirs` / `roots` / `truncated`
    :raises ServerDirError: 路径非法、目录不存在或无权限（带状态码）
    """
    roots = _server_roots()
    if not path:
        if not roots:
            raise ServerDirError("没有可访问的目录", status=404)
        path = roots[0]["path"]

    if not os.path.isabs(path):
        raise ServerDirError("路径必须是绝对路径", status=400)

    current = os.path.normpath(path)
    if not os.path.isdir(current):
        raise ServerDirError("目录不存在", status=404)

    entries: list[dict] = []
    truncated = False
    try:
        with os.scandir(current) as iterator:
            for entry in iterator:
                if entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                if len(entries) >= SERVER_DIR_LIMIT:
                    truncated = True
                    break
                entries.append(
                    {"name": entry.name, "path": os.path.join(current, entry.name)}
                )
    except PermissionError:
        raise ServerDirError("无权限访问该目录", status=403)

    entries.sort(key=lambda item: item["name"].casefold())
    parent = os.path.dirname(current)
    if not parent or os.path.normcase(parent) == os.path.normcase(current):
        parent = None

    return {
        "path": current,
        "parent": parent,
        "dirs": entries,
        "roots": roots,
        "truncated": truncated,
        # v1.8.2（C1）：下发目录上限，三端文案不再硬编码「2000」
        "limit": SERVER_DIR_LIMIT,
    }


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

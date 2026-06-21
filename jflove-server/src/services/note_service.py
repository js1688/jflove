"""
笔记业务逻辑层

处理笔记文件的增删改查和重命名操作。
笔记目录从用户配置 users.notes_disk_id / notes_path 动态读取，仅支持 .md 文件。

权限模型（v1.x 起）：所有登录用户均可使用笔记功能；
每个用户的笔记目录互不可见（_get_notes_base 仅返回当前用户自己的目录），
因此不再需要单独的"笔记权限"表来区分读/写/删。
"""

import os
import aiofiles
import aiosqlite
from pathlib import Path

from src.repositories import virtual_disk_repository, user_repository
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def _get_notes_base(db: aiosqlite.Connection, user_id: int) -> str:
    """
    获取指定用户的笔记目录真实路径。

    每个用户可独立配置笔记存储磁盘（users.notes_disk_id 字段）。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :returns: 笔记目录绝对路径
    :raises ValueError: 用户未配置 notes_disk_id 或对应磁盘不存在
    """
    user = await user_repository.find_by_id(db, user_id)
    if not user or not user["notes_disk_id"]:
        raise ValueError("笔记目录未配置，请在设置页面选择笔记存储磁盘")
    disk = await virtual_disk_repository.find_by_id(db, int(user["notes_disk_id"]))
    if not disk:
        raise ValueError("笔记虚拟磁盘不存在，请重新配置")
    base = disk["real_path"]
    sub = (user["notes_path"] or "").strip("/")
    if sub:
        full = os.path.normpath(os.path.join(base, sub))
        if not full.startswith(os.path.normpath(base)):
            raise ValueError("非法笔记路径")
        base = full
    if not os.path.isdir(base):
        raise ValueError(f"笔记目录不存在: {base}")
    return base


def _safe_md_path(base: str, filename: str) -> str:
    """
    生成安全的笔记文件绝对路径，防目录遍历攻击。

    仅允许访问 base 目录下的 .md 文件，文件名不含路径分隔符。

    :param base: 笔记目录绝对路径
    :param filename: 笔记文件名（如 "my-note.md"）
    :returns: 安全的绝对路径字符串
    :raises ValueError: 不是 .md 文件
    :raises PermissionError: 路径越界
    """
    if not filename.endswith(".md"):
        raise ValueError("仅支持 .md 文件")
    base_path = Path(base).resolve()
    target = (base_path / Path(filename).name).resolve()
    if not str(target).startswith(str(base_path)):
        raise PermissionError("非法路径访问")
    return str(target)


async def list_notes(db: aiosqlite.Connection, user_id: int, role: str) -> list[dict]:
    """
    获取当前用户笔记目录下所有 .md 文件列表（按文件名排序）。

    所有登录用户均可使用笔记功能；笔记目录由 users.notes_disk_id / notes_path
    指定，每个用户互不可见。role 参数保留以兼容现有调用，未来可移除。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色（保留参数，当前未使用）
    :returns: 笔记文件列表，每项含 filename、size、modified_at
    :raises ValueError: 笔记目录未配置
    """
    base = await _get_notes_base(db, user_id)
    result = []
    for entry in sorted(os.scandir(base), key=lambda e: e.name):
        if entry.is_file() and entry.name.endswith(".md"):
            result.append({
                "filename": entry.name,
                "size": entry.stat().st_size,
                "modified_at": entry.stat().st_mtime,
            })
    return result


async def read_note(
    db: aiosqlite.Connection, user_id: int, role: str, filename: str
) -> str:
    """
    读取当前用户笔记目录下的指定笔记文件（UTF-8 编码）。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色（保留参数，当前未使用）
    :param filename: 笔记文件名
    :returns: 文件文本内容
    :raises ValueError: 文件不存在
    """
    base = await _get_notes_base(db, user_id)
    path = _safe_md_path(base, filename)
    if not os.path.isfile(path):
        raise ValueError("笔记不存在")
    async with aiofiles.open(path, encoding="utf-8") as f:
        return await f.read()


async def write_note(
    db: aiosqlite.Connection, user_id: int, role: str, filename: str, content: str
) -> None:
    """
    在当前用户笔记目录下新建或覆盖笔记文件内容。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色（保留参数，当前未使用）
    :param filename: 笔记文件名（必须以 .md 结尾）
    :param content: 笔记文本内容
    """
    base = await _get_notes_base(db, user_id)
    path = _safe_md_path(base, filename)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)
    logger.info("笔记已保存: %s", filename)


async def delete_note(
    db: aiosqlite.Connection, user_id: int, role: str, filename: str
) -> None:
    """
    删除当前用户笔记目录下的指定笔记文件（物理删除）。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色（保留参数，当前未使用）
    :param filename: 笔记文件名
    :raises ValueError: 文件不存在
    """
    base = await _get_notes_base(db, user_id)
    path = _safe_md_path(base, filename)
    if not os.path.isfile(path):
        raise ValueError("笔记不存在")
    os.remove(path)
    logger.info("笔记已删除: %s", filename)


async def rename_note(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    old_name: str,
    new_name: str,
) -> None:
    """
    重命名当前用户笔记目录下的笔记文件（源文件和目标文件均须在该目录内）。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色（保留参数，当前未使用）
    :param old_name: 原文件名
    :param new_name: 新文件名
    :raises ValueError: 源文件不存在或目标文件名已占用
    """
    base = await _get_notes_base(db, user_id)
    old_path = _safe_md_path(base, old_name)
    new_path = _safe_md_path(base, new_name)
    if not os.path.isfile(old_path):
        raise ValueError("笔记不存在")
    if os.path.exists(new_path):
        raise ValueError("目标文件名已存在")
    os.rename(old_path, new_path)

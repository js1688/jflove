"""
文件管理业务逻辑层

处理虚拟磁盘上的文件/目录操作，包括：
  - 目录浏览
  - 分片上传（init → chunk → complete），支持断点续传
  - 文件下载
  - 文件/目录删除
  - 目录创建
  - 文件预览
  - 流式 Range 预览（v1.1.0 新增）

安全措施：所有路径操作均通过 _safe_join 限制在虚拟磁盘根目录内，防止目录遍历攻击。
"""

import mimetypes
import os
import uuid
import hashlib
import aiofiles
import aiosqlite
from pathlib import Path

from src.config.settings import REPAIR_DIR_NAME, UPLOAD_TEMP_DIR
from src.repositories import virtual_disk_repository, permission_repository
from src.services.permission_service import check_disk_permission
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 内存上传会话存储：upload_id -> 会话元数据字典
_upload_sessions: dict[str, dict] = {}


def _safe_join(base: str, rel: str) -> str:
    """
    安全路径拼接，防目录遍历攻击。

    将相对路径 rel 限定在 base 目录内，若解析后路径越出 base 则抛出异常。
    v1.4.2：同时拒绝任何包含修复隐藏目录（.jflove-repair）路径段的访问——
    客户端无法通过下载/删除/预览等接口直接触达修复产物（产物仅经
    /stream 的 repair_task_id 验证播放通道输出）。

    :param base: 根目录绝对路径
    :param rel: 相对路径（可含前置 /）
    :returns: 限定后的绝对路径字符串
    :raises PermissionError: 路径越界 / 含隐藏目录段
    """
    base_path = Path(base).resolve()
    target = (base_path / rel.lstrip("/")).resolve()
    if not str(target).startswith(str(base_path)):
        raise PermissionError("非法路径访问")
    # v1.4.2：隐藏目录段访问一律拒绝（下载/删除/重命名/移动等全部生效）
    rel_norm = rel.replace("\\", "/").lstrip("/")
    if any(part == REPAIR_DIR_NAME for part in rel_norm.split("/") if part):
        raise PermissionError("非法路径访问")
    return str(target)


async def _get_disk_path(db: aiosqlite.Connection, disk_id: int) -> str:
    """
    获取虚拟磁盘对应的服务端真实路径。

    :param db: 数据库连接
    :param disk_id: 虚拟磁盘 ID
    :returns: 真实路径绝对路径字符串
    :raises ValueError: 虚拟磁盘不存在
    """
    disk = await virtual_disk_repository.find_by_id(db, disk_id)
    if not disk:
        raise ValueError("虚拟磁盘不存在")
    return disk["real_path"]


async def list_accessible_disks(
    db: aiosqlite.Connection, user_id: int, role: str
) -> list[dict]:
    """
    获取当前用户可访问的虚拟磁盘列表。

    - 管理员：返回全部磁盘，且全部磁盘均有写权限
    - 普通用户：仅返回拥有读权限的磁盘，并标注 can_write / can_delete
      （v1.4.2 新增 can_delete：修复功能要求写+删权限并存，三端据此
      禁用/隐藏「修复损坏媒体」入口）

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色（admin / user）
    :returns: 磁盘列表，每项含 id、name、can_write、can_delete
    """
    all_disks = await virtual_disk_repository.list_all(db)
    if role == "admin":
        return [
            {"id": d["id"], "name": d["name"], "can_write": True, "can_delete": True}
            for d in all_disks
        ]
    perms = await permission_repository.get_disk_permissions(db, user_id)
    # sqlite3.Row 不支持 .get()，转为 dict 后再操作
    perm_map = {p["virtual_disk_id"]: dict(p) for p in perms}
    result = []
    for d in all_disks:
        p = perm_map.get(d["id"])
        if p and p.get("can_read"):
            result.append({
                "id": d["id"],
                "name": d["name"],
                "can_write": bool(p.get("can_write", False)),
                "can_delete": bool(p.get("can_delete", False)),
            })
    return result


async def list_files(
    db: aiosqlite.Connection, user_id: int, role: str, disk_id: int, rel_path: str = ""
) -> list[dict]:
    """
    列出指定虚拟磁盘目录下的文件和子目录（目录在前，文件在后，均按名称排序）。

    v1.4.2：过滤修复隐藏目录 .jflove-repair（服务端过滤，三端零改动不展示；
    修复产物经 /stream?repair_task_id 验证播放，不经文件列表）。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色（admin 跳过权限校验）
    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 相对于磁盘根目录的路径，默认为根目录
    :returns: 文件/目录列表，每项含 name、is_dir、size、modified_at
    :raises PermissionError: 无读取权限
    :raises ValueError: 路径不存在
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "read"):
            raise PermissionError("无读取权限")
    base = await _get_disk_path(db, disk_id)
    target = _safe_join(base, rel_path)
    if not os.path.isdir(target):
        raise ValueError("路径不存在或不是目录")

    result = []
    for entry in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name)):
        # v1.4.2：隐藏修复产物目录，不出现在文件列表
        if entry.name == REPAIR_DIR_NAME:
            continue
        result.append({
            "name": entry.name,
            "is_dir": entry.is_dir(),
            "size": entry.stat().st_size if entry.is_file() else 0,
            "modified_at": entry.stat().st_mtime,
        })
    return result


async def init_upload(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    rel_path: str,
    filename: str,
    file_size: int,
    total_chunks: int,
    file_hash: str,
    mtime: float | None = None,
) -> str:
    """
    初始化分片上传会话，返回 upload_id 供后续分片上传使用。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色
    :param disk_id: 目标虚拟磁盘 ID
    :param rel_path: 目标目录相对路径
    :param filename: 文件名
    :param file_size: 文件总大小（字节）
    :param total_chunks: 总分片数
    :param file_hash: 文件 SHA256 哈希，用于完整性校验
    :param mtime: 源文件的修改时间（Unix 时间戳，浮点秒）。若提供，
                  complete_upload 将通过 os.utime 还原文件 mtime，
                  避免目录同步时出现"上传后服务端时间被刷新→下次同步重新下载覆盖"的循环。
    :returns: upload_id（UUID 字符串）
    :raises PermissionError: 无写入权限
    :raises ValueError: 路径非法
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "write"):
            raise PermissionError("无写入权限")
    base = await _get_disk_path(db, disk_id)
    _safe_join(base, rel_path)  # 仅做路径合法性验证

    upload_id = str(uuid.uuid4())
    tmp_dir = os.path.join(UPLOAD_TEMP_DIR, upload_id)
    os.makedirs(tmp_dir, exist_ok=True)
    _upload_sessions[upload_id] = {
        "owner_user_id": user_id,  # 用于权限绑定，防止换 upload_id 操作他人会话
        "disk_id": disk_id,
        "rel_path": rel_path,
        "filename": filename,
        "file_size": file_size,
        "total_chunks": total_chunks,
        "file_hash": file_hash,
        "mtime": mtime,
        "received": set(),    # 已接收的分片索引集合
        "tmp_dir": tmp_dir,
        "base": base,
    }
    return upload_id


def is_upload_owned_by(upload_id: str, user_id: int) -> bool:
    """
    判断指定 upload_id 是否归属当前用户。

    用于 upload_chunk / complete_upload / cancel_upload 控制器层校验。
    防止"换 upload_id 绕过权限"——即便其他用户拿到 upload_id 也不能操作。

    :param upload_id: 上传会话 ID
    :param user_id: 当前用户 ID
    :returns: True 表示归属当前用户；不存在或归属他人均返回 False
    """
    session = _upload_sessions.get(upload_id)
    if session is None:
        return False
    return session.get("owner_user_id") == user_id


async def upload_chunk(upload_id: str, chunk_index: int, chunk_data: bytes) -> None:
    """
    上传单个文件分片，分片以索引命名存入临时目录。

    :param upload_id: 上传会话 ID
    :param chunk_index: 分片索引（从 0 开始）
    :param chunk_data: 分片字节数据
    :raises ValueError: 会话不存在或分片索引越界
    """
    session = _upload_sessions.get(upload_id)
    if not session:
        raise ValueError("上传会话不存在或已过期")
    if chunk_index >= session["total_chunks"]:
        raise ValueError("chunk_index 超出范围")

    chunk_path = os.path.join(session["tmp_dir"], f"{chunk_index:06d}.chunk")
    async with aiofiles.open(chunk_path, "wb") as f:
        await f.write(chunk_data)
    session["received"].add(chunk_index)


async def complete_upload(upload_id: str) -> str:
    """
    合并所有分片，校验 SHA256，写入目标路径，清理临时文件。

    :param upload_id: 上传会话 ID
    :returns: 服务端最终文件绝对路径
    :raises ValueError: 会话不存在、分片缺失或 SHA256 校验失败
    """
    session = _upload_sessions.get(upload_id)
    if not session:
        raise ValueError("上传会话不存在或已过期")

    total = session["total_chunks"]
    missing = set(range(total)) - session["received"]
    if missing:
        raise ValueError(f"缺少分片: {sorted(missing)[:5]}")

    dest_dir = _safe_join(session["base"], session["rel_path"])
    os.makedirs(dest_dir, exist_ok=True)
    dest_file = os.path.join(dest_dir, session["filename"])

    # 合并分片并计算 SHA256
    sha256 = hashlib.sha256()
    async with aiofiles.open(dest_file, "wb") as out:
        for i in range(total):
            chunk_path = os.path.join(session["tmp_dir"], f"{i:06d}.chunk")
            async with aiofiles.open(chunk_path, "rb") as f:
                data = await f.read()
            sha256.update(data)
            await out.write(data)

    # 校验文件完整性
    if sha256.hexdigest() != session["file_hash"]:
        os.remove(dest_file)
        raise ValueError("文件校验失败，hash 不匹配")

    # 还原源文件 mtime（若客户端提供）：保证两端 mtime 一致，
    # 避免目录同步时陷入"上传后远端时间变新→下次同步反向覆盖本地"的循环
    src_mtime = session.get("mtime")
    if src_mtime:
        try:
            os.utime(dest_file, (src_mtime, src_mtime))
        except OSError as e:
            logger.warning("无法还原文件 mtime（不影响上传结果）: %s -> %s", dest_file, e)

    import shutil
    shutil.rmtree(session["tmp_dir"], ignore_errors=True)
    del _upload_sessions[upload_id]
    logger.info("文件上传完成: %s", dest_file)
    return dest_file


async def cancel_upload(upload_id: str) -> None:
    """
    取消上传，清理临时分片目录。

    :param upload_id: 上传会话 ID
    """
    session = _upload_sessions.pop(upload_id, None)
    if session:
        import shutil
        shutil.rmtree(session["tmp_dir"], ignore_errors=True)


async def download_file(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    rel_path: str,
) -> str:
    """
    校验权限后返回文件绝对路径，由路由层使用 FileResponse 返回文件流。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色
    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 文件相对路径
    :returns: 文件绝对路径
    :raises PermissionError: 无读取权限
    :raises ValueError: 文件不存在
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "read"):
            raise PermissionError("无读取权限")
    base = await _get_disk_path(db, disk_id)
    target = _safe_join(base, rel_path)
    if not os.path.isfile(target):
        raise ValueError("文件不存在")
    return target


async def delete_file(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    rel_path: str,
) -> None:
    """
    删除文件或目录（目录使用递归删除）。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色
    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 文件或目录相对路径
    :raises PermissionError: 无删除权限
    :raises ValueError: 路径不存在
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "delete"):
            raise PermissionError("无删除权限")
    base = await _get_disk_path(db, disk_id)
    target = _safe_join(base, rel_path)
    if os.path.isfile(target):
        os.remove(target)
    elif os.path.isdir(target):
        import shutil
        shutil.rmtree(target)
    else:
        raise ValueError("路径不存在")
    logger.info("文件/目录已删除: %s", target)


async def make_dir(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    rel_path: str,
) -> None:
    """
    在指定虚拟磁盘下创建目录（支持多级创建）。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色
    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 要创建的目录相对路径
    :raises PermissionError: 无写入权限
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "write"):
            raise PermissionError("无写入权限")
    base = await _get_disk_path(db, disk_id)
    target = _safe_join(base, rel_path)
    os.makedirs(target, exist_ok=True)


async def get_preview(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    rel_path: str,
) -> str:
    """
    校验权限后返回文件绝对路径，由路由层使用 FileResponse 返回文件内容用于预览。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色
    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 文件相对路径
    :returns: 文件绝对路径
    :raises PermissionError: 无读取权限
    :raises ValueError: 文件不存在
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "read"):
            raise PermissionError("无读取权限")
    base = await _get_disk_path(db, disk_id)
    target = _safe_join(base, rel_path)
    if not os.path.isfile(target):
        raise ValueError("文件不存在")
    return target


async def rename_file(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    path: str,
    new_name: str,
) -> None:
    """
    重命名文件或目录（v1.1.3 新增）。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色
    :param disk_id: 虚拟磁盘 ID
    :param path: 目标文件/目录当前相对路径（含名称）
    :param new_name: 新名称（纯名称，不含路径分隔符）
    :raises PermissionError: 无写入权限
    :raises ValueError: 路径不存在 / 名称非法
    :raises FileNotFoundError: 路径不存在（404）
    :raises FileExistsError: 同目录下已存在同名项
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "write"):
            raise PermissionError("无写入权限")

    # 校验新名称合法性
    new_name = new_name.strip() if new_name else ""
    if not new_name:
        raise ValueError("名称不能为空")
    if "/" in new_name or "\\" in new_name:
        raise ValueError("名称包含非法字符")
    if new_name in (".", ".."):
        raise ValueError("名称包含非法字符")

    base = await _get_disk_path(db, disk_id)
    target = _safe_join(base, path)

    if not os.path.exists(target):
        raise FileNotFoundError("路径不存在")

    parent_dir = os.path.dirname(target)
    new_target = os.path.join(parent_dir, new_name)

    # 防御性验证：确认 new_target 也在磁盘 base 内（v1.1.4 补上 _safe_join 校验）
    _safe_join(base, os.path.relpath(new_target, base))

    # 名称未变则静默跳过
    if target == new_target:
        return

    if os.path.exists(new_target):
        raise FileExistsError("目标名称已存在")

    os.rename(target, new_target)
    logger.info("文件/目录已重命名: disk_id=%s", disk_id)


async def move_file(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    src_path: str,
    dst_dir_path: str,
) -> None:
    """
    移动文件或目录到目标目录（v1.1.3 新增）。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色
    :param disk_id: 虚拟磁盘 ID
    :param src_path: 被移动的文件/目录相对路径
    :param dst_dir_path: 目标目录的相对路径（空字符串表示根目录）
    :raises PermissionError: 无写入权限
    :raises FileNotFoundError: 路径不存在
    :raises ValueError: 路径非法 / 循环嵌套
    :raises FileExistsError: 目标位置已存在同名项
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "write"):
            raise PermissionError("无写入权限")

    base = await _get_disk_path(db, disk_id)
    src_abs = _safe_join(base, src_path)
    dst_dir_abs = _safe_join(base, dst_dir_path)

    if not os.path.exists(src_abs):
        raise FileNotFoundError("源路径不存在")
    if not os.path.isdir(dst_dir_abs):
        raise FileNotFoundError("目标目录不存在")

    # 源文件和目标在同一父目录：静默跳过
    src_parent = os.path.dirname(src_abs)
    if src_parent == dst_dir_abs:
        return

    # 防循环嵌套：不允许把目录移动到自身或其子目录
    if os.path.isdir(src_abs):
        if dst_dir_abs == src_abs or dst_dir_abs.startswith(src_abs + os.sep):
            raise ValueError("目标目录是源目录的子目录")

    name = os.path.basename(src_abs)
    new_abs = os.path.join(dst_dir_abs, name)

    if os.path.exists(new_abs):
        raise FileExistsError("目标位置已存在同名项")

    os.rename(src_abs, new_abs)
    logger.info("文件/目录已移动: disk_id=%s", disk_id)


async def get_stream_range(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    path: str,
    filename: str,
    range_start: int,
    range_end: int,
) -> tuple[str, int, int, int, str]:
    """
    校验权限，规范化 range 参数，返回流式预览所需的文件信息。

    供 v1.1.0 流式预览接口（GET /api/v1/files/stream）使用。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 用户角色（admin 跳过权限校验）
    :param disk_id: 虚拟磁盘 ID
    :param path: 文件所在目录（磁盘内相对路径）
    :param filename: 文件名
    :param range_start: 请求字节起点（0 = 开头；负数 = 从末尾倒数）
    :param range_end: 请求字节终点，不含（-1 = 文件结尾）
    :returns: (file_path, effective_start, effective_end,
              file_size, content_type)
    :raises PermissionError: 无读取权限
    :raises ValueError: 文件不存在
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "read"):
            raise PermissionError("无读取权限")

    base = await _get_disk_path(db, disk_id)
    # 将目录路径与文件名拼接后整体做安全校验，防目录遍历
    rel_full = os.path.join(path.lstrip("/"), filename)
    file_path = _safe_join(base, rel_full)

    if not os.path.isfile(file_path):
        raise ValueError("文件不存在")

    file_size = os.path.getsize(file_path)

    # 规范化 range：负值表示从文件末尾倒数
    eff_start = (
        range_start if range_start >= 0
        else max(0, file_size + range_start)
    )
    eff_end = range_end if range_end >= 0 else file_size
    eff_start = max(0, min(eff_start, file_size))
    eff_end = max(eff_start, min(eff_end, file_size))

    # 推断 MIME 类型；无法识别时返回二进制流类型
    content_type, _ = mimetypes.guess_type(filename)
    if not content_type:
        content_type = "application/octet-stream"

    return file_path, eff_start, eff_end, file_size, content_type

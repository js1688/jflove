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

import asyncio
import mimetypes
import os
import shutil
import time
import uuid
import hashlib
import aiofiles
import aiosqlite

from src.config.settings import REPAIR_DIR_NAME, UPLOAD_TEMP_DIR
from src.repositories import virtual_disk_repository, permission_repository
from src.services.permission_service import check_disk_permission
from src.utils.logger import get_logger
from src.utils.path_safety import safe_join as _safe_join_impl
from src.utils.path_safety import validate_filename

logger = get_logger(__name__)

# 内存上传会话存储：upload_id -> 会话元数据字典
_upload_sessions: dict[str, dict] = {}

# v1.6.0（BL-8）原子替换的重试参数：
#   Windows 上 os.replace 会因目标文件被其它进程**短暂**持有而失败
#   （杀软实时扫描、资源管理器缩略图/预览句柄、云盘同步进程等，
#   表现为 PermissionError WinError 5 / 32），这类占用是瞬时的，短重试即可成功。
#   最多尝试 5 次（含首次），每次间隔 0.1s ⇒ 最长等待 0.4s，对上传请求可忽略。
_REPLACE_MAX_ATTEMPTS = 5
_REPLACE_RETRY_INTERVAL_SEC = 0.1


def _remove_quietly(path: str) -> None:
    """
    静默删除文件（仅用于清理临时产物）。

    不存在 / 被占用 / 无权限等一律忽略——清理动作绝不允许掩盖真正的异常
    （临时文件本身已带 `.jflove-upload-*.part` 特征后缀，可被识别与审计）。

    :param path: 待删除文件路径
    """
    try:
        os.remove(path)
    except OSError:
        pass


def _replace_with_retry(src: str, dst: str) -> None:
    """
    原子替换目标文件（`os.replace`）并带短重试，全部失败才抛出最后一次异常。

    为什么必须是 `os.replace` 而不是"先删后写"：`os.replace` 在**同一文件系统**内
    是原子的（要么旧文件、要么新文件，不存在"半个文件"的中间态）；而"先删后写"
    会让原文件在一个时间窗口内彻底丢失，窗口内任何失败都是不可恢复的数据损失。
    为什么需要重试：Windows 上目标文件被其它进程短暂占用（杀软扫描 / 缩略图 /
    同步工具）时 `os.replace` 会抛 `PermissionError`，重试通常即可成功；不重试会
    让"覆盖同名文件"在没有任何真实错误的情况下失败。
    本函数含 `time.sleep`，调用方必须放到线程池执行（`asyncio.to_thread`），
    不得直接在事件循环里调用。

    :param src: 源文件（同目录内的临时文件）
    :param dst: 目标文件（可已存在，将被原子替换）
    :raises OSError: 重试 _REPLACE_MAX_ATTEMPTS 次后仍失败，抛出最后一次异常
    """
    last_error: OSError = OSError("os.replace 未执行")
    for attempt in range(_REPLACE_MAX_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except OSError as e:
            last_error = e
            if attempt + 1 < _REPLACE_MAX_ATTEMPTS:
                time.sleep(_REPLACE_RETRY_INTERVAL_SEC)
    raise last_error


def _safe_join(base: str, rel: str) -> str:
    """
    安全路径拼接，防目录遍历攻击。

    将相对路径 rel 限定在 base 目录内，若解析后路径越出 base 则抛出异常。
    v1.4.2：同时拒绝任何包含修复隐藏目录（.jflove-repair）路径段的访问——
    客户端无法通过下载/删除/预览等接口直接触达修复产物（产物仅经
    /stream 的 repair_task_id 验证播放通道输出）。

    v1.6.0（Phase 5 审查 S-1）：实现已下沉到 ``src.utils.path_safety.safe_join``。
    原实现用 ``str(target).startswith(str(base_path))`` 判定越界，会把
    ``base=D:\\disks\\alice`` + ``rel=../alice2/evil.txt`` 判为"在目录内"
    （同名前缀的兄弟目录），使下游全部路径操作失去防护。

    :param base: 根目录绝对路径
    :param rel: 相对路径（可含前置 /）
    :returns: 限定后的绝对路径字符串
    :raises PermissionError: 路径越界 / 含隐藏目录段
    """
    return _safe_join_impl(base, rel, forbidden_parts=(REPAIR_DIR_NAME,))


def _validate_filename(filename: str) -> str:
    """
    校验上传文件名，拒绝任何可能逃出目标目录的写法（v1.6.0 BL-10，安全）。

    背景：`init_upload` 曾把 `filename` 原样存入上传会话，`complete_upload` 再用
    `os.path.join(dest_dir, filename)` 拼目标路径 ⇒ `filename = "../../evil.txt"`
    可让写入**逃出虚拟磁盘目录**（需已认证且对该磁盘有写权限 ⇒ 认证后目录穿越）。
    BL-8 的原子写改造沿用了同一条拼接，因此本条独立修复并保留 `complete_upload`
    侧的第二层兜底。

    v1.6.0（Phase 5 审查 S-2）：实现已下沉到
    ``src.utils.path_safety.validate_filename``，本函数保留为兼容别名；
    同时 ``get_stream_range``（流式预览的文件名）与 ``rename_file``（新名称）
    也改为走同一份校验，不再只依赖 `_safe_join` 兜底。

    :param filename: 客户端提交的文件名
    :returns: 去空白后的合法文件名
    :raises ValueError: 文件名为空、含路径分隔符或为上级目录段
    """
    return validate_filename(filename)


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
    :raises ValueError: 路径非法 / 文件名非法（含路径分隔符或上级目录段，v1.6.0 BL-10）
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "write"):
            raise PermissionError("无写入权限")
    base = await _get_disk_path(db, disk_id)
    # v1.6.0（BL-10）：`filename` 必须在这里就被限定为**纯文件名**。
    # 会话里的 filename 会在 complete_upload 被拼进目标路径，一旦含分隔符即可穿越；
    # 校验放在权限判定之后，与下方 `_safe_join(base, rel_path)` 的位置保持一致
    # （无写权限者仍先得到 403，不因参数合法性改变越权可见性）。
    filename = _validate_filename(filename)
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
    合并所有分片，校验 SHA256，**原子**写入目标路径，清理临时文件。

    v1.6.0（BL-8）覆盖写原子化：合并与校验全部先落到**目标目录内**的临时文件
    （`.<filename>.jflove-upload-<upload_id>.part`），mtime 还原也作用在临时文件上，
    最后用 `os.replace` 一次性原子替换目标文件。因此任何失败路径（缺分片 / IO 异常 /
    hash 不匹配 / 进程中断）都**不会破坏已存在的原文件**——原文件要么保持旧内容，
    要么变成完整的新内容，不存在"半截内容"的中间态。

    :param upload_id: 上传会话 ID
    :returns: 服务端最终文件绝对路径
    :raises ValueError: 会话不存在、分片缺失、文件名非法（v1.6.0 BL-10 兜底）或 SHA256 校验失败
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
    # v1.6.0（BL-10）第二层兜底：`filename` 已在 init_upload 校验为纯文件名，
    # 这里再用 _safe_join 复核目标路径仍落在 dest_dir 内 —— 若会话被篡改或校验被绕过，
    # 越界会抛 PermissionError；统一转成 ValueError，保持 complete 接口
    # 「非法输入 ⇒ 400」的既有契约（控制器只捕获 ValueError）。
    try:
        dest_file = _safe_join(dest_dir, session["filename"])
    except PermissionError:
        raise ValueError("filename 非法：不能包含路径分隔符")

    # v1.6.0（BL-8）：临时文件**必须与目标文件同目录**。
    # os.replace 只在**同一文件系统**内才是原子操作；若把临时文件放在上传临时目录
    # （UPLOAD_TEMP_DIR，可能位于 /tmp 或另一个盘符），跨设备时 os.replace 会抛
    # OSError(EXDEV) 或退化为"复制 + 删除"的非原子实现，原子性即失效。
    # v1.6.0（BL-10）：名字取 `basename(dest_file)`，与 dest_file 共用同一份校验结果。
    tmp_file = os.path.join(
        dest_dir,
        f".{os.path.basename(dest_file)}.jflove-upload-{upload_id}.part",
    )
    try:
        # 合并分片并计算 SHA256：只写临时文件，此阶段**完全不触碰** dest_file
        sha256 = hashlib.sha256()
        async with aiofiles.open(tmp_file, "wb") as out:
            for i in range(total):
                chunk_path = os.path.join(session["tmp_dir"], f"{i:06d}.chunk")
                async with aiofiles.open(chunk_path, "rb") as f:
                    data = await f.read()
                sha256.update(data)
                await out.write(data)

        # 校验文件完整性：失败**只删临时文件**（由 finally 完成），绝不删除/截断 dest_file
        # （旧实现在此处 `os.remove(dest_file)`，hash 不匹配即丢失原文件）
        if sha256.hexdigest() != session["file_hash"]:
            raise ValueError("文件校验失败，hash 不匹配")

        # 还原源文件 mtime（若客户端提供）：保证两端 mtime 一致，
        # 避免目录同步时陷入"上传后远端时间变新→下次同步反向覆盖本地"的循环。
        # v1.6.0：mtime 先作用在**临时文件**上，再整体替换 ⇒ 目标文件对外一次成型
        # （内容与 mtime 同时可见，没有"内容已新、时间还是旧"的中间态）。
        src_mtime = session.get("mtime")
        if src_mtime:
            try:
                os.utime(tmp_file, (src_mtime, src_mtime))
            except OSError as e:
                # 日志只记异常类型，不回显完整路径（AGENTS.md §9.4.14）
                logger.warning("无法还原文件 mtime（不影响上传结果）: %s", type(e).__name__)

        # 原子替换：放到线程池执行（内部含 sleep 重试，不能阻塞事件循环）
        await asyncio.to_thread(_replace_with_retry, tmp_file, dest_file)
    finally:
        # 失败 / 异常路径：清理临时文件，不留半截 `.part` 残留；
        # 成功路径下 tmp_file 已被 os.replace 移走（该删除幂等）。
        _remove_quietly(tmp_file)

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
    # v1.6.0（Phase 5 审查 S-2）：新名称走与上传文件名同一份口径（NUL / 盘符前缀 /
    # 平台分隔符兜底），错误信息仍按"名称"措辞，避免暴露内部校验函数的措辞。
    try:
        new_name = validate_filename(new_name)
    except ValueError:
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
    # v1.6.0（Phase 5 审查 S-2）：`filename` 是接口契约里的**纯文件名**，必须过同一份
    # 文件名校验（否则含分隔符/上级段的名字只能靠 _safe_join 兜底，错误码会从 400
    # 漂成 403）。`path` 是目录相对路径，允许含合法子目录分量，只由 _safe_join 判定。
    filename = _validate_filename(filename)
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

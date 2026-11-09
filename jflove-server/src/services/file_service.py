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
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid
import hashlib
import aiofiles
import aiosqlite

from src.config.settings import (
    OFFLINE_DIR_NAME,
    REPAIR_DIR_NAME,
    TEMP_DIR_NAME,
    TEMP_OFFLINE_SUBDIR,
    UPLOAD_MAX_CHUNK_SIZE,
    UPLOAD_PROGRESS_TTL_SECONDS,
    UPLOAD_TEMP_DIR,
)
from src.repositories import virtual_disk_repository, permission_repository
from src.services import file_journal_service
from src.services.permission_service import check_disk_permission
from src.utils.logger import get_logger
from src.utils.path_safety import safe_join as _safe_join_impl
from src.utils.path_safety import validate_filename

logger = get_logger(__name__)

# 上传会话固定命名规则（v1.9.0）：
#   - 会话目录名必须是 UUID（磁盘上只允许程序自产目录）
#   - 分片文件名 000000.chunk
#   - 合并临时文件 .<name>.jflove-upload-<uuid>.part
_SESSION_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_CHUNK_FILE_RE = re.compile(r"^[0-9]{6}\.chunk$")
_UPLOAD_PART_RE = re.compile(r"^\.(.+?)\.jflove-upload-([0-9a-fA-F-]{36})\.part$")

# 内存上传会话存储：upload_id -> 会话元数据字典（启动时由磁盘 session.json 恢复）
_upload_sessions: dict[str, dict] = {}
#: 内容指纹索引：fingerprint -> upload_id（init 幂等复用）
_session_fingerprints: dict[str, str] = {}
#: 会话表锁（事件循环内串行化 init/恢复/删除）
_session_lock = asyncio.Lock()
#: 进度表锁（合并线程内更新进度，事件循环读取）
_merge_progress_lock = threading.Lock()

#: 会话元数据落盘间隔：每 N 片或每 30s 回写 updated_at（避免每片一次小 IO）
_SESSION_FLUSH_EVERY = 8
_SESSION_FLUSH_INTERVAL = 30.0

# 兼容旧引用（v1.7.0 测试/调用）：默认 24h；实际 TTL 由 config 单一来源驱动。
UPLOAD_SESSION_TTL_SECONDS = 24 * 3600

#: 合并（complete）期间的串行锁：同一 upload_id 只允许一个合并任务，
#: 防止客户端超时重发 / 多端同时 complete 时并发重写同一批临时分片。
_complete_locks: dict[str, asyncio.Lock] = {}
_complete_locks_guard = asyncio.Lock()

#: 合并进度（upload_id → 状态快照），供客户端轮询显示「服务端在做什么」。
_merge_progress: dict[str, dict] = {}
#: 后台合并任务（upload_id → asyncio.Task），同一会话不重复启动
_merge_tasks: dict[str, asyncio.Task] = {}

#: 合并进度更新的分片间隔：太频繁会拖慢合并，只在每 N 片更新一次
_MERGE_PROGRESS_STEP = 16

#: 全局并发限流（v1.9.0，弱服务端资源隔离）
_merge_limit = 1
_merge_semaphore = asyncio.Semaphore(1)
_chunk_limit = 2
_chunk_semaphore = asyncio.Semaphore(2)


def refresh_upload_limits(merge_concurrency: int, chunk_concurrency: int) -> None:
    """
    重建上传合并 / 分片上传的全局信号量（配置写入后调用）。

    :param merge_concurrency: 合并并发（0 = 暂停：新任务保持 queued）
    :param chunk_concurrency: 分片上传并发（至少 1）
    """
    global _merge_limit, _merge_semaphore, _chunk_limit, _chunk_semaphore
    _merge_limit = max(0, int(merge_concurrency))
    _chunk_limit = max(1, int(chunk_concurrency))
    _merge_semaphore = asyncio.Semaphore(max(1, _merge_limit))
    _chunk_semaphore = asyncio.Semaphore(_chunk_limit)
    logger.info(
        "上传限流已刷新: merge=%d chunk=%d", _merge_limit, _chunk_limit,
    )


def get_upload_limits() -> tuple[int, int]:
    """返回当前合并/分片并发上限（供控制器/设置页展示）。"""
    return _merge_limit, _chunk_limit


def merge_progress(upload_id: str) -> dict | None:
    """
    读取某次合并的实时进度。

    :param upload_id: 上传会话 ID
    :returns: 进度快照，或 None（该会话从未开始合并 / 记录已清理）
    """
    with _merge_progress_lock:
        item = _merge_progress.get(upload_id)
        return dict(item) if item else None


def _merge_progress_set(upload_id: str, owner_user_id: int | None = None, **fields) -> None:
    """更新进度快照（合并线程与事件循环共用；用线程锁保护）。"""
    with _merge_progress_lock:
        item = _merge_progress.get(upload_id)
        if item is None:
            item = {
                "upload_id": upload_id, "state": "queued", "processed": 0,
                "total": 0, "file_size": 0, "started_at": time.time(),
                "updated_at": time.time(), "queue_position": 0,
                "path": "", "error": "", "missing_chunks": [],
            }
            _merge_progress[upload_id] = item
        if owner_user_id is not None:
            item["owner_user_id"] = owner_user_id
        item.update(fields)
        item["updated_at"] = time.time()


def _merge_progress_drop(upload_id: str) -> None:
    """删除进度记录（完成 / 取消 / 回收时）"""
    with _merge_progress_lock:
        _merge_progress.pop(upload_id, None)


def is_progress_owned_by(upload_id: str, user_id: int) -> bool:
    """判断某次合并进度记录是否归属当前用户（会话删除后的迟到查询用）。"""
    info = merge_progress(upload_id)
    return bool(info and info.get("owner_user_id") == user_id)


def prune_merge_progress(max_age_seconds: int = UPLOAD_PROGRESS_TTL_SECONDS) -> int:
    """清理超过 TTL 未更新的进度记录，避免字典无限增长"""
    now = time.time()
    with _merge_progress_lock:
        stale = [uid for uid, item in _merge_progress.items()
                 if now - float(item.get("updated_at") or 0) > max_age_seconds]
        for uid in stale:
            _merge_progress.pop(uid, None)
    return len(stale)


# v1.6.0（BL-8）原子替换的重试参数：
#   Windows 上 os.replace 会因目标文件被其它进程**短暂**持有而失败
#   （杀软实时扫描、资源管理器缩略图/预览句柄、云盘同步进程等，
#   表现为 PermissionError WinError 5 / 32），这类占用是瞬时的，短重试即可成功。
#   最多尝试 5 次（含首次），每次间隔 0.1s ⇒ 最长等待 0.4s，对上传请求可忽略。
_REPLACE_MAX_ATTEMPTS = 5
_REPLACE_RETRY_INTERVAL_SEC = 0.1


def _rel_of(base: str, full: str) -> str:
    """
    把绝对路径转成"相对磁盘根、以 / 分隔"的路径（与变更日志口径一致）。

    :param base: 磁盘根绝对路径
    :param full: 目标绝对路径
    :returns: 形如 `photos/2026/a.jpg`
    """
    return os.path.relpath(full, base).replace(os.sep, "/")


def _collect_tree(
    full: str, base: str, *, skip_hidden: bool = True
) -> list[tuple[str, bool, int, float]]:
    """
    收集 `full`（文件或目录）及其全部子孙的元数据。

    用于 rename / move / delete 时一次性写入多条变更日志；v1.9.0 起
    对账扫描也复用本函数，默认跳过名称以 `.` 开头的隐藏项。

    :param full: 目标绝对路径
    :param base: 磁盘根绝对路径
    :param skip_hidden: 是否跳过 `.` 开头的子项（v1.9.0 隐藏项治理）
    :returns: 列表，每项 `(相对路径, 是否目录, 大小, mtime)`
    """
    result: list[tuple[str, bool, int, float]] = []
    try:
        st = os.stat(full)
    except OSError:
        return result
    is_dir = os.path.isdir(full)
    result.append((_rel_of(base, full), is_dir, 0 if is_dir else st.st_size, st.st_mtime))
    if not is_dir:
        return result
    for dirpath, dirnames, filenames in os.walk(full):
        if skip_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in dirnames:
            p = os.path.join(dirpath, name)
            try:
                s = os.stat(p)
            except OSError:
                continue
            result.append((_rel_of(base, p), True, 0, s.st_mtime))
        for name in filenames:
            if skip_hidden and name.startswith("."):
                continue
            p = os.path.join(dirpath, name)
            try:
                s = os.stat(p)
            except OSError:
                continue
            result.append((_rel_of(base, p), False, s.st_size, s.st_mtime))
    return result


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
    :raises PermissionError: 路径越界 / 含程序保留隐藏目录段
    """
    # v1.9.0：所有程序保留隐藏目录一律拒绝通过文件管理接口访问；
    # 用户自己的 `.` 文件仍允许精确路径访问（列表不展示，但已知路径可操作）。
    return _safe_join_impl(
        base,
        rel,
        forbidden_parts=(REPAIR_DIR_NAME, OFFLINE_DIR_NAME, TEMP_DIR_NAME),
    )


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
        # v1.9.0：所有名称以 `.` 开头的条目一律不出现在文件列表（服务端统一过滤）。
        # 覆盖修复产物、离线下载临时目录、统一临时根与用户隐藏文件。
        if entry.name.startswith("."):
            continue
        result.append({
            "name": entry.name,
            "is_dir": entry.is_dir(),
            "size": entry.stat().st_size if entry.is_file() else 0,
            "modified_at": entry.stat().st_mtime,
        })
    return result


def _normalize_rel_path(rel_path: str) -> str:
    """规范化目标目录相对路径（去前后 `/`，空串表示磁盘根）。"""
    return (rel_path or "").strip().strip("/")


def _fingerprint(
    user_id: int, disk_id: int, rel_path: str, filename: str,
    file_size: int, file_hash: str, total_chunks: int,
) -> str:
    """内容指纹：同一用户/磁盘/路径/文件名/大小/哈希/分片数 → 同一上传会话。

    v1.9.0：`total_chunks` 必须参与指纹 —— 不同客户端版本分片大小不同
    （1MB vs 512KB），复用旧会话会导致 chunk_index 越界。
    """
    joined = "|".join([
        str(user_id), str(disk_id), _normalize_rel_path(rel_path),
        filename, str(file_size), file_hash, str(total_chunks),
    ])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _scan_received_chunks(tmp_dir: str, total_chunks: int) -> set[int]:
    """
    以磁盘上实际存在的分片文件重建「已收分片」集合（v1.9.0 唯一口径）。

    :param tmp_dir: 会话临时目录
    :param total_chunks: 总分片数
    :returns: 实际存在且大小非零的分片索引集合
    """
    received: set[int] = set()
    if not os.path.isdir(tmp_dir):
        return received
    try:
        names = os.listdir(tmp_dir)
    except OSError:
        return received
    for name in names:
        if not _CHUNK_FILE_RE.match(name):
            continue
        index = int(name[:6])
        if index >= total_chunks:
            continue
        try:
            if os.path.getsize(os.path.join(tmp_dir, name)) > 0:
                received.add(index)
        except OSError:
            continue
    return received


def _session_expires_at(session: dict) -> float:
    """会话可续传截止时间（updated_at + TTL）。"""
    return float(session.get("updated_at") or 0) + int(
        session.get("ttl_seconds") or UPLOAD_SESSION_TTL_SECONDS
    )


def _persist_session(session: dict) -> None:
    """
    把会话元数据写入 `<tmp_dir>/session.json`（同步；调用方放线程池）。

    只持久化元数据，不写 received：已收分片以磁盘文件为准。
    """
    session["updated_at"] = time.time()
    data = {
        "upload_id": session.get("upload_id"),
        "owner_user_id": session.get("owner_user_id"),
        "client_type": session.get("client_type", "unknown"),
        "disk_id": session.get("disk_id"),
        "rel_path": session.get("rel_path", ""),
        "filename": session.get("filename"),
        "file_size": session.get("file_size"),
        "total_chunks": session.get("total_chunks"),
        "file_hash": session.get("file_hash"),
        "mtime": session.get("mtime"),
        "created_at": session.get("created_at"),
        "updated_at": session["updated_at"],
        "ttl_seconds": session.get("ttl_seconds", UPLOAD_SESSION_TTL_SECONDS),
        "state": session.get("state", "active"),
    }
    path = os.path.join(session["tmp_dir"], "session.json")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp_path, path)


def _load_session_from_disk(upload_id: str) -> dict | None:
    """
    读取磁盘上的 session.json 并重建内存会话（启动恢复用）。

    :param upload_id: 会话 UUID（目录名）
    :returns: 会话字典；文件缺失/损坏/字段非法返回 None
    """
    tmp_dir = os.path.join(UPLOAD_TEMP_DIR, upload_id)
    path = os.path.join(tmp_dir, "session.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        filename = _validate_filename(str(data.get("filename") or ""))
        disk_id = int(data.get("disk_id") or 0)
        owner_user_id = int(data.get("owner_user_id") or 0)
        file_size = int(data.get("file_size") or 0)
        total_chunks = int(data.get("total_chunks") or 0)
        if disk_id <= 0 or owner_user_id <= 0 or total_chunks <= 0:
            return None
        file_hash = str(data.get("file_hash") or "")
        if not file_hash:
            return None
        client_type = str(data.get("client_type") or "unknown")
        if client_type not in ("app", "desktop", "web", "unknown"):
            client_type = "unknown"
        rel_path = _normalize_rel_path(str(data.get("rel_path") or ""))
        mtime = data.get("mtime")
        return {
            "upload_id": upload_id,
            "owner_user_id": owner_user_id,
            "client_type": client_type,
            "disk_id": disk_id,
            "rel_path": rel_path,
            "filename": filename,
            "file_size": file_size,
            "total_chunks": total_chunks,
            "file_hash": file_hash,
            "mtime": float(mtime) if mtime else None,
            "received": _scan_received_chunks(tmp_dir, total_chunks),
            "tmp_dir": tmp_dir,
            "base": "",
            "created_at": float(data.get("created_at") or time.time()),
            "updated_at": float(data.get("updated_at") or time.time()),
            "ttl_seconds": int(data.get("ttl_seconds") or UPLOAD_SESSION_TTL_SECONDS),
            "state": str(data.get("state") or "active"),
            "fingerprint": _fingerprint(
                owner_user_id, disk_id, rel_path, filename, file_size,
                file_hash, total_chunks,
            ),
        }
    except (TypeError, ValueError):
        return None


def _register_session(session: dict) -> None:
    """登记会话到内存表与指纹索引。"""
    _upload_sessions[session["upload_id"]] = session
    _session_fingerprints[session["fingerprint"]] = session["upload_id"]


def _unregister_session(upload_id: str) -> dict | None:
    """从内存表与指纹索引移除会话（不删除磁盘目录）。"""
    session = _upload_sessions.pop(upload_id, None)
    if session:
        fingerprint = session.get("fingerprint")
        if fingerprint and _session_fingerprints.get(fingerprint) == upload_id:
            _session_fingerprints.pop(fingerprint, None)
    return session


async def restore_upload_sessions(db: aiosqlite.Connection) -> int:
    """
    启动时从磁盘恢复未过期上传会话（v1.9.0）。

    :param db: 数据库连接（用于按 disk_id 补回虚拟磁盘根路径）
    :returns: 恢复的会话数量
    """
    from src.services import upload_cleanup_service  # 局部导入，避免循环依赖

    if not os.path.isdir(UPLOAD_TEMP_DIR):
        return 0
    options = await upload_cleanup_service.get_options(db)
    now = time.time()
    restored = 0
    async with _session_lock:
        for name in os.listdir(UPLOAD_TEMP_DIR):
            if not _SESSION_UUID_RE.match(name):
                continue
            if not os.path.isdir(os.path.join(UPLOAD_TEMP_DIR, name)):
                continue
            session = _load_session_from_disk(name)
            if not session:
                continue
            if now - float(session.get("updated_at") or 0) >= options.resume_ttl_seconds:
                continue
            disk = await virtual_disk_repository.find_by_id(db, session["disk_id"])
            if not disk:
                continue
            session["base"] = disk["real_path"]
            session["ttl_seconds"] = options.resume_ttl_seconds
            _register_session(session)
            restored += 1
    if restored:
        logger.info("已恢复 %d 个未完成上传会话", restored)
    return restored


async def init_upload_ex(
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
    client_type: str = "unknown",
) -> dict:
    """
    初始化/复用分片上传会话（v1.9.0 指纹幂等）。

    :returns: 包含 upload_id / received_chunks / resumed / expires_at /
              already_exists / max_chunk_size / recommended_concurrency /
              restart_required 的字典
    :raises PermissionError: 无写入权限
    :raises ValueError: 路径/文件名非法、分片参数非法、未完成会话超限
    """
    from src.services import upload_cleanup_service  # 局部导入，避免循环依赖

    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "write"):
            raise PermissionError("无写入权限")
    base = await _get_disk_path(db, disk_id)
    filename = _validate_filename(filename)
    rel_path = _normalize_rel_path(rel_path)
    _safe_join(base, rel_path)  # 仅做路径合法性验证
    if file_size < 0 or total_chunks <= 0:
        raise ValueError("file_size/total_chunks 非法")
    if client_type not in ("app", "desktop", "web", "unknown"):
        client_type = "unknown"

    options = await upload_cleanup_service.get_options(db)
    fingerprint = _fingerprint(
        user_id, disk_id, rel_path, filename, file_size, file_hash, total_chunks,
    )
    now = time.time()

    async with _session_lock:
        # 1) 指纹命中：复用未过期且状态 active 的会话
        existing_id = _session_fingerprints.get(fingerprint)
        restart_required = False
        if existing_id:
            existing = _upload_sessions.get(existing_id)
            if existing and existing.get("state", "active") == "active":
                if now - float(existing.get("updated_at") or 0) < options.resume_ttl_seconds:
                    existing["received"] = _scan_received_chunks(
                        existing["tmp_dir"], existing["total_chunks"],
                    )
                    existing["ttl_seconds"] = options.resume_ttl_seconds
                    existing["updated_at"] = now
                    await asyncio.to_thread(_persist_session, existing)
                    return {
                        "upload_id": existing_id,
                        "received_chunks": sorted(existing["received"]),
                        "resumed": bool(existing["received"]),
                        "expires_at": _session_expires_at(existing),
                        "already_exists": False,
                        "already_path": "",
                        "max_chunk_size": UPLOAD_MAX_CHUNK_SIZE,
                        "recommended_concurrency": options.chunk_concurrency,
                        "restart_required": False,
                    }
                # 已过期：清掉旧会话目录，下面新建
                _unregister_session(existing_id)
                shutil.rmtree(existing.get("tmp_dir", ""), ignore_errors=True)
                restart_required = True
            else:
                # 状态为 failed / 无法复用：删掉旧目录，重新开始
                if existing:
                    _unregister_session(existing_id)
                    shutil.rmtree(existing.get("tmp_dir", ""), ignore_errors=True)
                else:
                    # 指纹索引残留（会话已被外部清理/删除）：移除脏索引
                    _session_fingerprints.pop(fingerprint, None)
                restart_required = True

        # 2) 每用户未完成会话上限（防单账号堆满临时分片）
        # 注：不在服务端自动判定「目标文件已存在」（同大小不代表同内容）；
        # 客户端在提交前用自己的远端索引/列表核验，避免误跳过。
        active_count = sum(
            1 for item in _upload_sessions.values()
            if item.get("owner_user_id") == user_id
            and item.get("state", "active") == "active"
        )
        if active_count >= options.max_sessions_per_user:
            raise ValueError("未完成的上传过多，请先完成或取消部分上传后重试")

        # 3) 新建会话
        upload_id = str(uuid.uuid4())
        tmp_dir = os.path.join(UPLOAD_TEMP_DIR, upload_id)
        os.makedirs(tmp_dir, exist_ok=True)
        session = {
            "upload_id": upload_id,
            "owner_user_id": user_id,
            "client_type": client_type,
            "disk_id": disk_id,
            "rel_path": rel_path,
            "filename": filename,
            "file_size": file_size,
            "total_chunks": total_chunks,
            "file_hash": file_hash,
            "mtime": mtime,
            "received": set(),
            "tmp_dir": tmp_dir,
            "base": base,
            "created_at": now,
            "updated_at": now,
            "ttl_seconds": options.resume_ttl_seconds,
            "state": "active",
            "fingerprint": fingerprint,
            "_persisted_at": now,
        }
        _register_session(session)
        await asyncio.to_thread(_persist_session, session)
        return {
            "upload_id": upload_id,
            "received_chunks": [],
            "resumed": False,
            "expires_at": _session_expires_at(session),
            "already_exists": False,
            "already_path": "",
            "max_chunk_size": UPLOAD_MAX_CHUNK_SIZE,
            "recommended_concurrency": options.chunk_concurrency,
            "restart_required": restart_required,
        }


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
    兼容旧调用：初始化分片上传会话并只返回 upload_id。

    新控制器请用 `init_upload_ex`（需要 received_chunks/expires_at 等字段）。
    """
    result = await init_upload_ex(
        db, user_id, role, disk_id, rel_path, filename, file_size,
        total_chunks, file_hash, mtime, client_type="unknown",
    )
    return str(result.get("upload_id") or "")


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


def upload_status(upload_id: str, user_id: int | None = None) -> dict | None:
    """
    查询上传会话状态（v1.9.0）。

    :param upload_id: 上传会话 ID
    :param user_id: 当前用户 ID；传入时校验归属（不匹配返回 None）
    :returns: 状态字典；会话不存在返回 None
    """
    session = _upload_sessions.get(upload_id)
    if session is None:
        return None
    if user_id is not None and session.get("owner_user_id") != user_id:
        return None
    total = int(session.get("total_chunks") or 0)
    session["received"] = _scan_received_chunks(session["tmp_dir"], total)
    received = sorted(session["received"])
    missing = sorted(set(range(total)) - session["received"])
    return {
        "exists": True,
        "state": session.get("state", "active"),
        "upload_id": upload_id,
        "received_chunks": received,
        "missing_chunks": missing,
        "total_chunks": total,
        "file_size": int(session.get("file_size") or 0),
        "expires_at": _session_expires_at(session),
        "expired": time.time() >= _session_expires_at(session),
        "client_type": session.get("client_type", "unknown"),
    }


async def upload_chunk(upload_id: str, chunk_index: int, chunk_data: bytes) -> None:
    """
    上传单个文件分片，分片以索引命名存入临时目录。

    :param upload_id: 上传会话 ID
    :param chunk_index: 分片索引（从 0 开始）
    :param chunk_data: 分片字节数据
    :raises ValueError: 会话不存在、分片索引越界、分片大小非法
    """
    session = _upload_sessions.get(upload_id)
    if not session:
        raise ValueError("上传会话不存在或已过期")
    total = int(session["total_chunks"])
    if chunk_index < 0 or chunk_index >= total:
        raise ValueError("chunk_index 超出范围")
    if not chunk_data or len(chunk_data) > UPLOAD_MAX_CHUNK_SIZE:
        raise ValueError("分片大小非法")

    chunk_path = os.path.join(session["tmp_dir"], f"{chunk_index:06d}.chunk")
    async with aiofiles.open(chunk_path, "wb") as f:
        await f.write(chunk_data)
    session["received"].add(chunk_index)
    now = time.time()
    session["updated_at"] = now
    # 每 N 片或隔 30s 回写一次 session.json（不每片写，避免弱服务端小 IO 放大）
    if (
        chunk_index % _SESSION_FLUSH_EVERY == 0
        or now - float(session.get("_persisted_at") or 0) >= _SESSION_FLUSH_INTERVAL
    ):
        await asyncio.to_thread(_persist_session, session)
        session["_persisted_at"] = now


class _MergeError(ValueError):
    """合并失败的可分类异常（供 progress 返回稳定 error 码）。"""

    def __init__(self, error: str, missing: list[int] | None = None, message: str = "") -> None:
        self.error = error
        self.missing = list(missing or [])
        super().__init__(message or error)


def _merge_chunks_worker(upload_id: str, session: dict, tmp_file: str) -> str:
    """
    合并工作线程：逐片读 → 写临时文件 → 写时计算 SHA256（v1.9.0 线程化）。

    不在事件循环线程执行，避免弱 CPU 上哈希/同步 IO 阻塞播放/下载等流式接口。

    :returns: 合并后文件的 SHA256 hex
    """
    sha256 = hashlib.sha256()
    processed = 0
    total = int(session["total_chunks"])
    with open(tmp_file, "wb") as out:
        for i in range(total):
            chunk_path = os.path.join(session["tmp_dir"], f"{i:06d}.chunk")
            with open(chunk_path, "rb") as handle:
                data = handle.read()
            sha256.update(data)
            out.write(data)
            processed += len(data)
            if i % _MERGE_PROGRESS_STEP == 0 or i == total - 1:
                _merge_progress_set(upload_id, state="merging", processed=processed)
    return sha256.hexdigest()


async def _get_complete_lock(upload_id: str) -> asyncio.Lock:
    """获取（或创建）同一 upload_id 的合并互斥锁。"""
    async with _complete_locks_guard:
        lock = _complete_locks.get(upload_id)
        if lock is None:
            lock = asyncio.Lock()
            _complete_locks[upload_id] = lock
    return lock


def _merge_queue_position() -> int:
    """当前排队等待合并的任务数（不含已完成）。"""
    return sum(1 for task in _merge_tasks.values() if not task.done())


def _merge_response(upload_id: str, info: dict | None) -> dict:
    """把进度快照转成 complete 的即时响应。"""
    item = info or {}
    return {
        "state": item.get("state", "queued"),
        "upload_id": upload_id,
        "poll_interval_s": 2.5,
        "queue_position": int(item.get("queue_position") or 0),
        "expires_at": item.get("expires_at") or 0.0,
        "path": item.get("path") or "",
        "error": item.get("error") or "",
        "missing_chunks": list(item.get("missing_chunks") or []),
    }


async def submit_merge(
    upload_id: str,
    db: aiosqlite.Connection | None = None,
) -> dict | None:
    """
    提交异步合并任务（v1.9.0）。

    :param upload_id: 上传会话 ID
    :param db: 数据库连接（写变更日志用）
    :returns: 即时状态字典；会话与进度记录都不存在返回 None
    """
    session = _upload_sessions.get(upload_id)
    async with _complete_locks_guard:
        task = _merge_tasks.get(upload_id)
        if session is not None:
            if task is not None and not task.done():
                return _merge_response(upload_id, merge_progress(upload_id))
            _merge_progress_set(
                upload_id,
                owner_user_id=session.get("owner_user_id"),
                state="queued",
                processed=0,
                total=int(session.get("total_chunks") or 0),
                file_size=int(session.get("file_size") or 0),
                started_at=time.time(),
                queue_position=_merge_queue_position(),
                path="",
                error="",
                missing_chunks=[],
            )
            _merge_tasks[upload_id] = asyncio.create_task(_merge_task(upload_id, db))
            return _merge_response(upload_id, merge_progress(upload_id))

    info = merge_progress(upload_id)
    if info is None:
        return None
    if session is None and info.get("state") == "done":
        info["expires_at"] = float(info.get("updated_at") or 0) + UPLOAD_PROGRESS_TTL_SECONDS
    return _merge_response(upload_id, info)


async def _merge_task(upload_id: str, db: aiosqlite.Connection | None = None) -> None:
    """后台合并任务：排队 → 合并 → 进度收尾（异常统一落 failed）。"""
    started = time.monotonic()
    try:
        # 合并并发配置为 0 时保持排队（管理员暂停），恢复后自动继续
        while _merge_limit <= 0:
            await asyncio.sleep(2)
        async with _merge_semaphore:
            _merge_progress_set(
                upload_id, state="merging", started_at=time.time(), queue_position=0,
            )
            lock = await _get_complete_lock(upload_id)
            try:
                async with lock:
                    await _complete_upload_locked(upload_id, db)
            finally:
                async with _complete_locks_guard:
                    if not lock.locked():
                        _complete_locks.pop(upload_id, None)
    except _MergeError as exc:
        _merge_progress_set(
            upload_id, state="failed", error=exc.error,
            missing_chunks=exc.missing,
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
    except asyncio.CancelledError:
        _merge_progress_set(
            upload_id, state="failed", error="cancelled",
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
        raise
    except Exception as exc:  # noqa: BLE001 - 后台任务必须收尾，不能静默悬挂
        logger.warning("合并任务失败: %s", type(exc).__name__)
        _merge_progress_set(
            upload_id, state="failed", error="io_error",
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
    finally:
        async with _complete_locks_guard:
            _merge_tasks.pop(upload_id, None)


async def complete_upload(
    upload_id: str,
    db: aiosqlite.Connection | None = None,
) -> str:
    """
    合并所有分片，校验 SHA256，**原子**写入目标路径，清理临时文件。

    保留为同步执行入口（供既有内部调用与单测）；HTTP 控制器使用
    `submit_merge` 异步受理 + 进度轮询，避免长请求超时。

    :param upload_id: 上传会话 ID
    :returns: 服务端最终文件绝对路径
    :raises ValueError: 会话不存在、分片缺失、SHA256 校验失败
    """
    lock = await _get_complete_lock(upload_id)
    try:
        async with lock:
            return await _complete_upload_locked(upload_id, db)
    finally:
        async with _complete_locks_guard:
            if not lock.locked():
                _complete_locks.pop(upload_id, None)


async def _complete_upload_locked(
    upload_id: str,
    db: aiosqlite.Connection | None = None,
) -> str:
    """
    `complete_upload` 的实际实现（已持同一 upload_id 的互斥锁）。

    v1.9.0：合并体在独立线程执行；进度通过 `progress` 接口可查；
    失败不再删除已存在目标文件（v1.6.0 原子写保护不变）。
    """
    session = _upload_sessions.get(upload_id)
    if not session:
        raise ValueError("上传会话不存在或已过期")

    total = int(session["total_chunks"])
    # 以磁盘实际分片为准（v1.9.0）：内存 received 可能因重启/清理与磁盘不一致
    session["received"] = _scan_received_chunks(session["tmp_dir"], total)
    missing = sorted(set(range(total)) - session["received"])
    if missing:
        _merge_progress_set(
            upload_id, owner_user_id=session.get("owner_user_id"),
            state="failed", error="missing_chunks",
            missing_chunks=missing[:200], total=total,
            file_size=session["file_size"],
        )
        raise _MergeError(
            "missing_chunks", missing=missing[:200],
            message=f"缺少分片: {missing[:5]}",
        )

    dest_dir = _safe_join(session["base"], session["rel_path"])
    os.makedirs(dest_dir, exist_ok=True)
    try:
        dest_file = _safe_join(dest_dir, session["filename"])
    except PermissionError:
        raise ValueError("filename 非法：不能包含路径分隔符")

    # 合并临时文件必须与目标文件同目录（同文件系统 os.replace 才是原子的）
    tmp_file = os.path.join(
        dest_dir,
        f".{os.path.basename(dest_file)}.jflove-upload-{upload_id}.part",
    )
    merge_started = time.monotonic()
    _merge_progress_set(
        upload_id, owner_user_id=session.get("owner_user_id"),
        state="merging", processed=0, total=total,
        file_size=session["file_size"], started_at=time.time(),
    )
    try:
        digest = await asyncio.to_thread(
            _merge_chunks_worker, upload_id, session, tmp_file,
        )
        if digest != session["file_hash"]:
            raise _MergeError("hash_mismatch", message="文件校验失败，hash 不匹配")

        src_mtime = session.get("mtime")
        if src_mtime:
            try:
                os.utime(tmp_file, (src_mtime, src_mtime))
            except OSError as e:
                logger.warning("无法还原文件 mtime（不影响上传结果）: %s", type(e).__name__)

        await asyncio.to_thread(_replace_with_retry, tmp_file, dest_file)
    except _MergeError as exc:
        _merge_progress_set(
            upload_id, state="failed", error=exc.error,
            missing_chunks=exc.missing,
            elapsed_seconds=round(time.monotonic() - merge_started, 1),
        )
        raise
    except BaseException:
        _merge_progress_set(
            upload_id, state="failed", error="io_error",
            elapsed_seconds=round(time.monotonic() - merge_started, 1),
        )
        raise
    finally:
        _remove_quietly(tmp_file)

    elapsed = time.monotonic() - merge_started
    _merge_progress_set(
        upload_id, state="done", processed=session["file_size"],
        path=dest_file, error="", missing_chunks=[],
        elapsed_seconds=round(elapsed, 1),
        expires_at=time.time() + UPLOAD_PROGRESS_TTL_SECONDS,
    )
    size_gb = max(session["file_size"], 0) / 1024 ** 3
    logger.info(
        "合并完成: 共 %.1f GB，耗时 %.1f 秒（%.1f 秒/GB，%d 片）",
        size_gb, elapsed,
        (elapsed / size_gb) if size_gb else 0.0, session["total_chunks"],
    )

    shutil.rmtree(session["tmp_dir"], ignore_errors=True)
    _unregister_session(upload_id)
    # v1.7.0：写入变更日志（失败不影响上传结果——文件已经落盘）
    if db is not None:
        try:
            st = os.stat(dest_file)
            await file_journal_service.record_upsert(
                db,
                session["disk_id"],
                _rel_of(session["base"], dest_file),
                is_dir=False,
                size=st.st_size,
                mtime=st.st_mtime,
                content_hash=session["file_hash"],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("写变更日志失败（不影响上传结果）: %s", type(e).__name__)
    logger.info("文件上传完成: task=%s disk=%s", upload_id, session["disk_id"])
    return dest_file


async def cancel_upload(upload_id: str) -> None:
    """
    取消上传，清理临时分片目录。

    :param upload_id: 上传会话 ID
    """
    session = _unregister_session(upload_id)
    _merge_progress_drop(upload_id)
    _merge_tasks.pop(upload_id, None)
    if session:
        shutil.rmtree(session.get("tmp_dir", ""), ignore_errors=True)


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


class RangeNotSatisfiableError(ValueError):
    """下载起始偏移越界（控制器映射为 416）。"""


async def get_download_range(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    rel_path: str,
    range_start: int = 0,
) -> tuple[str, int, int, str]:
    """
    校验权限并返回下载 Range 信息（v1.9.0 断点续传）。

    :param range_start: 起始字节偏移（0 = 从头）
    :returns: (文件绝对路径, 实际起始偏移, 文件大小, 版本标识 "size:int(mtime)")
    :raises PermissionError: 无读取权限
    :raises ValueError: 文件不存在
    :raises RangeNotSatisfiableError: 偏移越界
    """
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "read"):
            raise PermissionError("无读取权限")
    base = await _get_disk_path(db, disk_id)
    target = _safe_join(base, rel_path)
    if not os.path.isfile(target):
        raise ValueError("文件不存在")
    file_size = os.path.getsize(target)
    try:
        start = int(range_start)
    except (TypeError, ValueError) as exc:
        raise RangeNotSatisfiableError("起始偏移非法") from exc
    if start < 0 or start > file_size:
        raise RangeNotSatisfiableError("起始偏移越界")
    mtime = int(os.path.getmtime(target))
    return target, start, file_size, f"{file_size}:{mtime}"


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
    if not os.path.exists(target):
        raise ValueError("路径不存在")
    # v1.7.0：删除前先收集整棵子树，删除后统一写 delete 变更日志
    entries = _collect_tree(target, base)
    if os.path.isfile(target):
        os.remove(target)
    elif os.path.isdir(target):
        shutil.rmtree(target)
    else:
        raise ValueError("路径不存在")
    try:
        await file_journal_service.record_many(db, disk_id, [
            {"rel_path": rel, "op": "delete", "is_dir": is_dir}
            for rel, is_dir, _size, _mtime in entries
        ])
    except Exception as e:  # noqa: BLE001
        logger.warning("写变更日志失败（不影响删除结果）: %s", type(e).__name__)
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
    existed = os.path.isdir(target)
    os.makedirs(target, exist_ok=True)
    # v1.7.0：仅在真正新建目录时记录（make_dir 本身是幂等的）
    if not existed:
        try:
            st = os.stat(target)
            await file_journal_service.record_upsert(
                db,
                disk_id,
                _rel_of(base, target),
                is_dir=True,
                mtime=st.st_mtime,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("写变更日志失败（不影响建目录结果）: %s", type(e).__name__)


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

    # v1.7.0：旧路径整棵子树 delete + 新路径整棵子树 upsert
    old_entries = _collect_tree(target, base)
    os.rename(target, new_target)
    new_entries = _collect_tree(new_target, base)
    try:
        rows = [
            {"rel_path": rel, "op": "delete", "is_dir": is_dir}
            for rel, is_dir, _size, _mtime in old_entries
        ]
        rows.extend(
            {
                "rel_path": rel,
                "op": "upsert",
                "is_dir": is_dir,
                "size": size,
                "mtime": mtime,
            }
            for rel, is_dir, size, mtime in new_entries
        )
        await file_journal_service.record_many(db, disk_id, rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("写变更日志失败（不影响重命名结果）: %s", type(e).__name__)
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

    # v1.7.0：旧路径整棵子树 delete + 新路径整棵子树 upsert
    old_entries = _collect_tree(src_abs, base)
    os.rename(src_abs, new_abs)
    new_entries = _collect_tree(new_abs, base)
    try:
        rows = [
            {"rel_path": rel, "op": "delete", "is_dir": is_dir}
            for rel, is_dir, _size, _mtime in old_entries
        ]
        rows.extend(
            {
                "rel_path": rel,
                "op": "upsert",
                "is_dir": is_dir,
                "size": size,
                "mtime": mtime,
            }
            for rel, is_dir, size, mtime in new_entries
        )
        await file_journal_service.record_many(db, disk_id, rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("写变更日志失败（不影响移动结果）: %s", type(e).__name__)
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


# ── v1.7.0：挂载云盘配套能力 ───────────────────────


async def list_changes(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    prefix: str = "",
    since_rev: int = 0,
    limit: int = 500,
) -> dict:
    """
    拉取某磁盘、某子目录范围内的变更增量（带权限与越界校验）。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 当前用户角色
    :param disk_id: 虚拟磁盘 ID
    :param prefix: 子目录前缀（空串 = 磁盘根）
    :param since_rev: 上次拿到的最大 rev
    :param limit: 单次最多返回条数
    :returns: `{changes, latest_rev, has_more, reset_required}`
    :raises PermissionError: 无读取权限 / 路径越界
    :raises ValueError: 磁盘不存在
    """
    disk = await virtual_disk_repository.find_by_id(db, disk_id)
    if not disk:
        raise ValueError("虚拟磁盘不存在")
    clean_prefix = (prefix or "").strip().strip("/")
    _safe_join(disk["real_path"], clean_prefix)
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "read"):
            raise PermissionError("对目标磁盘没有读取权限")
    return await file_journal_service.list_changes(
        db, disk_id, clean_prefix, since_rev, limit,
    )


async def scan_tree(
    db: aiosqlite.Connection,
    user_id: int,
    role: str,
    disk_id: int,
    prefix: str = "",
    cursor: str = "",
    limit: int = 2000,
) -> dict:
    """
    全量对账扫描：只做 stat，不读文件内容。

    存在的意义：用户可能绕过 API 直接往虚拟磁盘目录里拷文件，
    这类"API 外变更"不会进变更日志，需要客户端定期用本接口对账补齐。

    :param db: 数据库连接
    :param user_id: 当前用户 ID
    :param role: 当前用户角色
    :param disk_id: 虚拟磁盘 ID
    :param prefix: 子目录前缀（空串 = 磁盘根）
    :param cursor: 上一页最后一个路径（空串 = 首页）
    :param limit: 每页条数（收敛到 [1, 5000]）
    :returns: `{entries, next_cursor, has_more, latest_rev}`
    :raises PermissionError: 无读取权限
    :raises ValueError: 磁盘不存在 / 路径越界
    """
    limit = max(1, min(int(limit or 2000), 5000))
    if role != "admin":
        if not await check_disk_permission(db, user_id, disk_id, "read"):
            raise PermissionError("无读取权限")

    base = await _get_disk_path(db, disk_id)
    clean_prefix = (prefix or "").strip().strip("/")
    root = _safe_join(base, clean_prefix)

    entries: list[dict] = []
    if os.path.isdir(root):
        for rel, is_dir, size, mtime in sorted(_collect_tree(root, base)):
            if cursor and rel <= cursor:
                continue
            entries.append({
                "path": rel,
                "is_dir": is_dir,
                "size": size,
                "mtime": mtime,
            })
            if len(entries) >= limit + 1:
                break

    has_more = len(entries) > limit
    page = entries[:limit]
    return {
        "entries": page,
        "next_cursor": page[-1]["path"] if page and has_more else "",
        "has_more": has_more,
        "latest_rev": await file_journal_service.get_latest_rev(db),
    }


def expire_stale_sessions(max_idle_seconds: int = UPLOAD_SESSION_TTL_SECONDS) -> int:
    """
    回收**空闲过久**的上传会话（内存 + 临时分片目录）。

    为什么必须有这一步：`cleanup_stale_uploads` 会跳过「仍在 `_upload_sessions`
    里」的目录。客户端崩溃 / 强杀 / 放弃上传后没人再来，会话却永远留在内存里，
    结果临时目录**永不释放**（只有重启服务端才清）。这里按最后活动时间回收。

    :param max_idle_seconds: 空闲多久算废弃（默认 24 小时）
    :returns: 回收的会话数量
    """
    now = time.time()
    stale = [
        upload_id for upload_id, session in _upload_sessions.items()
        if now - float(session.get("updated_at") or 0) >= max_idle_seconds
    ]
    for upload_id in stale:
        session = _upload_sessions.pop(upload_id, None)
        if not session:
            continue
        shutil.rmtree(session.get("tmp_dir", ""), ignore_errors=True)
        _merge_progress_drop(upload_id)
        logger.info("已回收废弃的上传会话（空闲超时）：%s", upload_id)
    return len(stale)


async def cleanup_stale_uploads(max_age_seconds: int = UPLOAD_SESSION_TTL_SECONDS) -> int:
    """
    清理过期的上传临时分片目录（v1.7.0，v1.7.0 补：会话空闲回收）。

    顺序：
      1. 先按**空闲时间**回收内存会话（连同其临时目录）——
         否则「会话还在内存里」会让第 2 步一律跳过，目录永不释放；
      2. 再清理既不在会话中、目录 mtime 又超过阈值的孤儿目录
         （服务端重启后内存会话丢失，这些都会变成孤儿）。

    :param max_age_seconds: 最大存活/空闲秒数（默认 24 小时）
    :returns: 删除的目录数量
    """
    removed = expire_stale_sessions(max_age_seconds)
    if not os.path.isdir(UPLOAD_TEMP_DIR):
        return removed

    now = time.time()
    for name in os.listdir(UPLOAD_TEMP_DIR):
        path = os.path.join(UPLOAD_TEMP_DIR, name)
        if not os.path.isdir(path) or name in _upload_sessions:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        if now - st.st_mtime < max_age_seconds:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    if removed:
        logger.info("已清理 %s 个过期上传临时目录/会话", removed)
    return removed


async def cleanup_orphan_partials(
    db: aiosqlite.Connection,
    max_age_seconds: int = UPLOAD_SESSION_TTL_SECONDS,
) -> int:
    """
    清理磁盘目录里遗留的合并临时文件（`.<名字>.jflove-upload-<id>.part`）。

    合并过程中进程被强杀 / 宕机时，`complete_upload` 的 `finally` 来不及执行，
    目标目录会留下一个隐藏的 `.part` 文件：既占空间，也会让用户看到「莫名其妙的隐藏文件」。

    :param db: 数据库连接（用于枚举虚拟磁盘根目录）
    :param max_age_seconds: mtime 超过该值的才算遗留（默认 24 小时）
    :returns: 删除的文件数量
    """
    now = time.time()
    removed = 0
    try:
        disks = await virtual_disk_repository.list_all(db)
    except Exception as e:  # noqa: BLE001 - 清理失败不应影响其它定时任务
        logger.warning("枚举虚拟磁盘失败，跳过残留合并文件清理：%s", type(e).__name__)
        return 0

    for disk in disks:
        base = disk["real_path"]
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                if not (name.startswith(".") and ".jflove-upload-" in name
                        and name.endswith(".part")):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    if now - os.stat(path).st_mtime < max_age_seconds:
                        continue
                    os.remove(path)
                    removed += 1
                except OSError:
                    continue
    if removed:
        logger.info("已清理 %s 个遗留的合并临时文件", removed)
    return removed


def _is_within_root(root: str, path: str) -> bool:
    """判断 path 的真实路径是否位于 root 内（防符号链接逃逸）。"""
    try:
        root_real = os.path.realpath(root)
        path_real = os.path.realpath(path)
        return os.path.commonpath([root_real, path_real]) == root_real
    except (OSError, ValueError):
        return False


def _read_session_meta(tmp_dir: str) -> dict | None:
    """读取会话目录内 session.json（损坏返回 None）。"""
    path = os.path.join(tmp_dir, "session.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _clean_task_subdirs(root: str, orphan_ttl: int, stats: dict, key: str) -> None:
    """
    清理 `<root>` 下按 task_id（纯数字）命名的临时子目录。

    只处理数字目录；不跟随符号链接；删除前校验真实路径仍在 root 内。
    用于离线下载旧 `.jflove-download` 与统一根 `offline/`。
    """
    if not os.path.isdir(root):
        return
    try:
        names = os.listdir(root)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not name.isdigit():
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path) or not _is_within_root(root, path):
            stats["skipped"] += 1
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if now - mtime < orphan_ttl:
            continue
        shutil.rmtree(path, ignore_errors=True)
        stats["removed_dirs"] += 1
        stats[key] = stats.get(key, 0) + 1


def _clean_upload_part_files(base: str, orphan_ttl: int, stats: dict) -> None:
    """
    清理磁盘内遗留的合并临时文件（严格命名 `.x.jflove-upload-<uuid>.part`）。

    不递归进入程序隐藏目录；只删 mtime 超过孤儿保留期的匹配文件。
    """
    now = time.time()
    base_real = os.path.realpath(base)
    for dirpath, dirnames, filenames in os.walk(base):
        # 不进入统一临时根/旧隐藏目录（它们由各自逻辑处理）
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if not _UPLOAD_PART_RE.match(name):
                continue
            path = os.path.join(dirpath, name)
            if not _is_within_root(base_real, path):
                stats["skipped"] += 1
                continue
            try:
                if now - os.path.getmtime(path) < orphan_ttl:
                    continue
                os.remove(path)
                stats["removed_parts"] += 1
            except OSError:
                continue


async def cleanup_temp_files(
    db: aiosqlite.Connection,
    options,
) -> dict:
    """
    统一临时文件清理（v1.9.0；管理员「立即清理」与定时任务共用）。

    :param db: 数据库连接
    :param options: `UploadCleanupOptions`（含 TTL / 客户端类型白名单 / 孤儿保留期）
    :returns: 统计字典（不含文件名/路径）
    """
    stats = {
        "removed_sessions": 0,
        "removed_dirs": 0,
        "removed_parts": 0,
        "skipped": 0,
    }
    now = time.time()
    ttl = int(getattr(options, "resume_ttl_seconds", UPLOAD_SESSION_TTL_SECONDS))
    orphan_ttl = int(getattr(options, "orphan_ttl_seconds", UPLOAD_SESSION_TTL_SECONDS))
    client_types = set(getattr(options, "cleanup_client_types", ()) or ())

    def _allowed(client_type: str) -> bool:
        return not client_types or client_type in client_types

    # 1) 内存会话过期回收（按客户端类型白名单）
    for upload_id, session in list(_upload_sessions.items()):
        client_type = str(session.get("client_type") or "unknown")
        if not _allowed(client_type):
            continue
        if now - float(session.get("updated_at") or 0) < ttl:
            continue
        _unregister_session(upload_id)
        shutil.rmtree(str(session.get("tmp_dir") or ""), ignore_errors=True)
        stats["removed_sessions"] += 1

    # 2) 上传临时根内的孤儿目录（服务重启后内存会话丢失 / 失败会话）
    if os.path.isdir(UPLOAD_TEMP_DIR):
        try:
            names = os.listdir(UPLOAD_TEMP_DIR)
        except OSError:
            names = []
        for name in names:
            if not _SESSION_UUID_RE.match(name):
                continue
            path = os.path.join(UPLOAD_TEMP_DIR, name)
            if name in _upload_sessions:
                continue
            if not os.path.isdir(path) or not _is_within_root(UPLOAD_TEMP_DIR, path):
                stats["skipped"] += 1
                continue
            meta = _read_session_meta(path)
            client_type = str((meta or {}).get("client_type") or "unknown")
            if not _allowed(client_type):
                continue
            updated_at = float((meta or {}).get("updated_at") or 0)
            if not updated_at:
                try:
                    updated_at = os.path.getmtime(path)
                except OSError:
                    continue
            if now - updated_at < ttl:
                continue
            # 只删「完全是程序自产」的目录：分片 / session.json / 其临时写文件
            try:
                entries = os.listdir(path)
            except OSError:
                continue
            unexpected = [
                entry for entry in entries
                if not (
                    _CHUNK_FILE_RE.match(entry)
                    or entry in ("session.json", "session.json.tmp")
                )
            ]
            if unexpected:
                stats["skipped"] += 1
                logger.warning("跳过含非预期文件的临时目录（不删除）")
                continue
            shutil.rmtree(path, ignore_errors=True)
            stats["removed_dirs"] += 1

    # 3) 磁盘级：旧 .jflove-download / 统一根 offline / .part 残留
    try:
        disks = await virtual_disk_repository.list_all(db)
    except Exception as exc:  # noqa: BLE001 - 清理失败不影响其它任务
        logger.warning("枚举虚拟磁盘失败，跳过磁盘临时清理: %s", type(exc).__name__)
        disks = []

    for disk in disks:
        try:
            base = os.path.realpath(str(disk["real_path"]))
        except (TypeError, OSError):
            continue
        if not os.path.isdir(base):
            continue
        # 3.1 旧离线下载目录（按任务子目录数字名 + 孤儿保留期）
        legacy_offline = os.path.join(base, OFFLINE_DIR_NAME)
        _clean_task_subdirs(legacy_offline, orphan_ttl, stats, "removed_legacy_offline")
        if os.path.isdir(legacy_offline) and not os.listdir(legacy_offline):
            try:
                os.rmdir(legacy_offline)
            except OSError:
                pass
        # 3.2 统一根离线下载子目录
        unified_offline = os.path.join(base, TEMP_DIR_NAME, TEMP_OFFLINE_SUBDIR)
        _clean_task_subdirs(unified_offline, orphan_ttl, stats, "removed_offline")
        # 3.3 合并临时文件（严格命名）
        _clean_upload_part_files(base, orphan_ttl, stats)

    prune_merge_progress(max_age_seconds=ttl)
    if any(stats.values()):
        logger.info(
            "临时文件清理完成: sessions=%d dirs=%d parts=%d skipped=%d",
            stats["removed_sessions"], stats["removed_dirs"],
            stats["removed_parts"], stats["skipped"],
        )
    return stats

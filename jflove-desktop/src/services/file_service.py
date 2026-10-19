"""
文件管理服务模块

封装虚拟磁盘上的文件/目录操作：
  - 目录浏览
  - 分片上传（SHA256 校验 + 断点续传 + 字节级进度 + 取消支持）
  - 文件下载（流式直接写入磁盘 + 字节级进度 + 取消支持）
  - 文件/目录删除
  - 目录创建
  - 文件预览
"""

import os
import json
import hashlib
import base64
import threading
import time
from typing import Callable, Generator

from src.utils.http_client import ApiError, http_client, is_transient_error
from src.utils.crypto import parse_stream_frame
from src.utils.session import session_manager
from src.config.settings import CHUNK_SIZE, LOCAL_STORAGE_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 进度回调签名: fn(已传输字节, 总字节数, 阶段标识)
# 阶段标识取值：
#   "hashing"    - 上传前的 SHA256 校验阶段
#   "uploading"  - 分片上传阶段
#   "finalizing" - 分片传完，等服务端合并 + 校验（大文件可能持续数分钟）
#   "retrying"   - 瞬态失败，正在重试（附带已完成字节数）
#   "downloading" - 下载阶段
ProgressCallback = Callable[[int, int, str], None]

#: 分片上传的最大尝试次数（网络抖动可安全重发同一分片）
_CHUNK_ATTEMPTS = 5
#: 合并请求的读取超时（秒）：服务端要重写全部分片并校验 SHA256
_FINALIZE_READ_TIMEOUT = 3600.0
#: 合并超时后，等待服务端完成并核验的最长时间（秒）
_FINALIZE_VERIFY_TIMEOUT = 900.0
#: 断点续传记录文件：应用重启后复用上次的 upload_id 与已发送分片
_RESUME_FILE = os.path.join(LOCAL_STORAGE_DIR, "upload_resume.json")
_RESUME_LOCK = threading.Lock()
#: 每传多少个分片落一次盘（避免每 1MB 都写 JSON）
_RESUME_FLUSH_EVERY = 32

#: SHA256 缓存（key = 路径 + 大小 + mtime）：重试时不必重新读一遍几十 GB
_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_HASH_CACHE_LOCK = threading.Lock()
_HASH_CACHE_MAX = 32


def list_accessible_disks() -> list[dict]:
    """
    获取当前用户可访问的虚拟磁盘列表。

    管理员返回全部磁盘，普通用户返回有读权限的磁盘。

    :returns: 磁盘列表，每项含 id、name
    """
    resp = http_client.get("/api/v1/files/disks")
    return resp.get("disks", [])


def list_files(disk_id: int, rel_path: str = "") -> list[dict]:
    """
    列出指定虚拟磁盘目录下的文件和子目录。

    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 相对于磁盘根目录的路径，默认为根目录
    :returns: 文件/目录列表，每项含 name、is_dir、size、modified_at
    :raises ApiError: 无权限或路径不存在
    """
    resp = http_client.get("/api/v1/files/list", {
        "disk_id": disk_id,
        "path": rel_path,
    })
    return resp.get("files", [])


def _resume_key(disk_id: int, rel_dir: str, filename: str, file_size: int,
                file_mtime: float | None) -> str:
    """断点续传的唯一键：同一目标 + 同一文件（size/mtime 未变）"""
    return "|".join([
        str(disk_id), rel_dir, filename, str(file_size),
        str(int((file_mtime or 0) * 1000)),
    ])


def _load_resume() -> dict:
    """读取断点续传记录（损坏 / 不存在时返回空表）"""
    try:
        with open(_RESUME_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_resume(data: dict) -> None:
    """原子写入断点续传记录（失败只告警，不影响上传）"""
    try:
        os.makedirs(os.path.dirname(_RESUME_FILE), exist_ok=True)
        tmp = _RESUME_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _RESUME_FILE)
    except OSError as e:
        logger.warning("保存断点续传记录失败：%s", type(e).__name__)


def _resume_get(key: str) -> dict | None:
    with _RESUME_LOCK:
        return _load_resume().get(key)


def _resume_update(key: str, upload_id: str, sent: set[int]) -> None:
    """记录已发送的分片索引（供下次启动续传），并清理 24 小时前的陈旧记录"""
    now = int(time.time())
    with _RESUME_LOCK:
        data = {
            k: v for k, v in _load_resume().items()
            if now - int(v.get("updated_at") or 0) < 24 * 3600
        }
        data[key] = {"upload_id": upload_id, "sent": sorted(sent), "updated_at": now}
        _save_resume(data)


def _resume_clear(key: str) -> None:
    with _RESUME_LOCK:
        data = _load_resume()
        if data.pop(key, None) is not None:
            _save_resume(data)


def _cached_file_hash(
    local_path: str,
    file_size: int,
    file_mtime: float | None,
    progress_callback: ProgressCallback | None,
    cancelled_flag: Callable[[], bool] | None,
) -> str:
    """
    计算文件 SHA256，按「路径 + 大小 + mtime」缓存。

    为什么需要缓存：上传失败重试时若重新读一遍 12GB，既慢又毫无意义；
    文件没变（size/mtime 相同）就复用上次结果。

    :returns: 十六进制 SHA256
    """
    key = (os.path.abspath(local_path), int(file_size), int((file_mtime or 0) * 1000))
    with _HASH_CACHE_LOCK:
        cached = _HASH_CACHE.get(key)
    if cached:
        logger.info("复用上次的 SHA256（文件未变化）：%s", os.path.basename(local_path))
        return cached

    sha256 = hashlib.sha256()
    done = 0
    with open(local_path, "rb") as f:
        while True:
            if cancelled_flag and cancelled_flag():
                raise ValueError("上传已取消")
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sha256.update(chunk)
            done += len(chunk)
            if progress_callback:
                progress_callback(done, file_size, "hashing")
    digest = sha256.hexdigest()

    with _HASH_CACHE_LOCK:
        if len(_HASH_CACHE) >= _HASH_CACHE_MAX:
            _HASH_CACHE.clear()
        _HASH_CACHE[key] = digest
    return digest


def _remote_stat(disk_id: int, rel_dir: str, filename: str) -> tuple[int, float] | None:
    """
    查询远端某文件的 (大小, mtime)（用于核验「是否其实已经上传成功」）。

    :returns: 存在则返回 `(size, mtime)`，不存在返回 None
    """
    try:
        entries = list_files(disk_id, rel_dir)
    except Exception as e:  # noqa: BLE001 - 核验失败按「未完成」处理
        logger.warning("核验远端文件失败：%s", type(e).__name__)
        return None
    for entry in entries:
        if entry.get("name") == filename and not entry.get("is_dir"):
            try:
                return int(entry.get("size") or 0), float(entry.get("modified_at") or 0)
            except (TypeError, ValueError):
                return None
    return None


def _remote_size(disk_id: int, rel_dir: str, filename: str) -> int | None:
    """远端文件大小（不存在返回 None）"""
    found = _remote_stat(disk_id, rel_dir, filename)
    return found[0] if found else None


def _already_uploaded(
    disk_id: int, rel_dir: str, filename: str, file_size: int, file_mtime: float | None,
) -> bool:
    """
    远端是否**已经是这个文件**（大小一致 + mtime 一致，±2 秒）。

    用途：避免「服务端其实已经合并完成、客户端却因为崩溃/超时又从头传一遍」。
    只有大小与 mtime 同时吻合才判定为已存在 —— 后者由服务端在合并时按客户端
    提供的 mtime 还原（`os.utime`），所以能作为「同一份内容」的可靠旁证。
    """
    found = _remote_stat(disk_id, rel_dir, filename)
    if found is None:
        return False
    size, mtime = found
    if size != file_size:
        return False
    if file_mtime and mtime and abs(mtime - file_mtime) > 2.0:
        return False
    return True


def _remote_path(rel_dir: str, filename: str) -> str:
    """相对目录 + 文件名 → 服务端相对路径"""
    return f"{rel_dir.rstrip('/')}/{filename}" if rel_dir else filename


def upload_file(
    disk_id: int,
    rel_path: str,
    local_path: str,
    progress_callback: ProgressCallback | None = None,
    cancelled_flag: Callable[[], bool] | None = None,
    preserve_mtime: bool = True,
) -> str:
    """
    分片上传本地文件到虚拟磁盘。

    流程：
      1. 边读边算 SHA256（"hashing" 阶段，按字节回调进度）
      2. init_upload 创建上传会话
      3. 重新打开文件按分片读取上传（"uploading" 阶段，按字节回调进度）
      4. complete_upload 触发服务端合并

    支持取消（通过 cancelled_flag 回调判断）。失败或取消时会向后端发出
    delete_upload 请求清理临时分片。

    :param disk_id: 目标虚拟磁盘 ID
    :param rel_path: 目标目录相对路径
    :param local_path: 本地文件绝对路径
    :param progress_callback: 进度回调 fn(已传输字节, 总字节, 阶段)
    :param cancelled_flag: 取消判断回调，返回 True 时中止
    :returns: 服务端最终文件绝对路径
    :raises ValueError: 取消上传
    :raises ApiError: 上传失败
    """
    filename = os.path.basename(local_path)
    file_size = os.path.getsize(local_path)
    file_mtime = os.path.getmtime(local_path) if preserve_mtime else None
    total_chunks = max(1, (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE) if file_size > 0 else 1

    # ── 阶段 1：SHA256 校验（流式；重试时走缓存，不重复读几十 GB）──
    file_hash = _cached_file_hash(
        local_path, file_size, file_mtime, progress_callback, cancelled_flag,
    )

    logger.info("开始上传文件: %s，大小=%d，分片数=%d", filename, file_size, total_chunks)

    # ── 阶段 2：初始化上传会话 ──
    init_payload = {
        "disk_id": disk_id,
        "path": rel_path,
        "filename": filename,
        "file_size": file_size,
        "total_chunks": total_chunks,
        "file_hash": file_hash,
    }
    if file_mtime is not None:
        # 传递源文件 mtime，让服务端在 complete 时还原；
        # 关键作用：避免目录同步陷入"上传后远端时间被刷新→下次同步反向覆盖"的循环
        init_payload["mtime"] = file_mtime

    resume_key = _resume_key(disk_id, rel_path, filename, file_size, file_mtime)
    resume = _resume_get(resume_key)
    resumed_sent: set[int] = set()
    upload_id = ""
    if resume:
        upload_id = str(resume.get("upload_id") or "")
        resumed_sent = {int(i) for i in (resume.get("sent") or [])}
        logger.info(
            "发现未完成的上传，尝试续传：%s（已传 %d/%d 片）",
            filename, len(resumed_sent), total_chunks,
        )

    # 上次中断时服务端可能**已经合并完成**（只是响应没送达 / 客户端被杀）：
    # 先核对远端，命中就直接收尾，绝不重复上传几十 GB。
    if resume and _already_uploaded(
        disk_id, rel_path, filename, file_size, file_mtime,
    ):
        logger.info("远端已存在同样大小的文件，跳过重复上传：%s", filename)
        _resume_clear(resume_key)
        if progress_callback:
            progress_callback(file_size, file_size, "uploading")
        return _remote_path(rel_path, filename)

    last_error: BaseException | None = None
    # 会话可能因服务端重启 / 24h 清理而失效：允许整轮重来一次（分片很小，代价可控）
    for attempt in range(2):
        try:
            if not upload_id:
                resp = http_client.post("/api/v1/files/upload/init", init_payload)
                upload_id = resp["upload_id"]
                resumed_sent = set()
            _send_chunks(
                upload_id, local_path, file_size, total_chunks,
                progress_callback, cancelled_flag,
                skip=resumed_sent, resume_key=resume_key,
            )
            result = _finalize(
                upload_id, disk_id, rel_path, filename, file_size,
                progress_callback, cancelled_flag,
            )
            _resume_clear(resume_key)
            return result
        except ValueError:
            _cancel_session(upload_id)
            _resume_clear(resume_key)
            raise
        except Exception as e:  # noqa: BLE001 - 分类后决定重试或上抛
            last_error = e
            if attempt == 0 and (_is_session_lost(e) or resumed_sent):
                # 续传失败（会话已不在服务端）⇒ 清记录、重新初始化、完整重传一次
                logger.warning(
                    "续传不可用（%s），重新初始化上传：%s", type(e).__name__, filename,
                )
                _cancel_session(upload_id)
                _resume_clear(resume_key)
                upload_id = ""
                resumed_sent = set()
                continue
            _cancel_session(upload_id)
            _resume_clear(resume_key)
            raise

    raise last_error if last_error else RuntimeError("上传失败")


def _is_session_lost(exc: BaseException) -> bool:
    """服务端是否明确表示「上传会话不存在 / 已过期」"""
    text = str(getattr(exc, "detail", exc))
    return "上传会话" in text or "upload_id" in text


def _cancel_session(upload_id: str) -> None:
    """尽力清理服务端临时分片（失败不影响调用方）"""
    if not upload_id:
        return
    try:
        http_client.delete(f"/api/v1/files/upload/{upload_id}")
    except Exception:  # noqa: BLE001
        pass


def _send_chunks(
    upload_id: str,
    local_path: str,
    file_size: int,
    total_chunks: int,
    progress_callback: ProgressCallback | None,
    cancelled_flag: Callable[[], bool] | None,
    skip: set[int] | None = None,
    resume_key: str = "",
) -> None:
    """
    逐个分片上传，**单个分片失败自动重试**。

    为什么按分片重试：整文件重传的代价是「重算哈希 + 重传几十 GB」，
    而分片重发只是 1MB —— 这是「传了一晚上没成功」的根因修复点之一。
    """
    retried = 0
    skip = skip or set()
    sent: set[int] = set(skip)
    with open(local_path, "rb") as f:
        for i in range(total_chunks):
            if cancelled_flag and cancelled_flag():
                raise ValueError("上传已取消")

            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            if i in skip:
                # 续传：该分片服务端已有，跳过发送（仍要读文件以保持偏移）
                if progress_callback:
                    progress_callback(min((i + 1) * CHUNK_SIZE, file_size),
                                      file_size, "uploading")
                continue
            chunk_b64 = base64.b64encode(chunk).decode()

            for attempt in range(_CHUNK_ATTEMPTS):
                try:
                    http_client.post("/api/v1/files/upload/chunk", {
                        "upload_id": upload_id,
                        "chunk_index": i,
                        "chunk_data": chunk_b64,
                    })
                    break
                except Exception as e:  # noqa: BLE001
                    if not is_transient_error(e) or attempt == _CHUNK_ATTEMPTS - 1:
                        raise
                    retried += 1
                    delay = min(2 ** attempt, 15)
                    logger.warning(
                        "分片 %d 上传失败（%s），%.0fs 后重试（第 %d 次）",
                        i, type(e).__name__, delay, attempt + 1,
                    )
                    if progress_callback:
                        done = min(i * CHUNK_SIZE, file_size)
                        progress_callback(done, file_size, "retrying")
                    if cancelled_flag and cancelled_flag():
                        raise ValueError("上传已取消") from e
                    time.sleep(delay)

            sent.add(i)
            if resume_key and len(sent) % _RESUME_FLUSH_EVERY == 0:
                _resume_update(resume_key, upload_id, sent)

            if progress_callback:
                done_bytes = min((i + 1) * CHUNK_SIZE, file_size)
                progress_callback(done_bytes, file_size, "uploading")

    if resume_key:
        _resume_update(resume_key, upload_id, sent)

    if retried:
        logger.info("本次上传共发生 %d 次分片重试", retried)


def _report_merge_progress(
    upload_id: str,
    file_size: int,
    progress_callback: ProgressCallback | None,
) -> None:
    """
    轮询服务端合并进度并回报给上层（界面据此显示百分比）。

    兼容旧服务端：接口不存在 / 网络异常时**静默跳过**，不影响上传本身。
    """
    if progress_callback is None:
        return
    try:
        info = http_client.post(
            "/api/v1/files/upload/progress",
            {"upload_id": upload_id},
            timeout=10,
        )
    except Exception:  # noqa: BLE001 - 旧服务端无此接口 / 断网：只影响进度显示
        return
    if info.get("state") not in ("merging", "done"):
        return
    processed = int(info.get("processed") or 0)
    total = int(info.get("file_size") or file_size or 0)
    if total <= 0:
        return
    progress_callback(min(processed, total), total, "finalizing")


def _finalize(
    upload_id: str,
    disk_id: int,
    rel_path: str,
    filename: str,
    file_size: int,
    progress_callback: ProgressCallback | None,
    cancelled_flag: Callable[[], bool] | None,
) -> str:
    """
    触发服务端合并分片并等待结果。

    两个关键点：
      1. **读取超时放到 1 小时**：合并要把全部分片重写一遍并算 SHA256，
         慢盘 / 并发上传时远超默认 30 秒；旧实现会在这里超时，进而整文件重传。
      2. **超时后不盲目重发**（服务端同一会话并发合并会破坏临时文件），
         而是轮询远端目录核验：文件出现且大小一致即视为成功。
    """
    if progress_callback:
        progress_callback(0, file_size, "finalizing")

    # complete 是**长请求**（服务端合并 + 校验），期间必须能拿到进度：
    # 真正发送放到子线程，本线程按秒轮询服务端进度并回报，界面才不会「停在处理中」。
    outcome: dict[str, object] = {}

    def _do_complete() -> None:
        try:
            outcome["result"] = http_client.post(
                "/api/v1/files/upload/complete",
                {"upload_id": upload_id},
                timeout=(30.0, _FINALIZE_READ_TIMEOUT),
            )
        except BaseException as e:  # noqa: BLE001 - 异常原样带回主线程分类
            outcome["error"] = e

    worker = threading.Thread(target=_do_complete, name="jflove-complete", daemon=True)
    worker.start()
    while True:
        worker.join(timeout=1.0)
        if not worker.is_alive():
            break
        if cancelled_flag and cancelled_flag():
            raise ValueError("上传已取消")
        _report_merge_progress(upload_id, file_size, progress_callback)

    error = outcome.get("error")
    if error is None:
        result = outcome.get("result") or {}
        logger.info("文件上传完成: %s", filename)
        return result.get("path", "")
    if not is_transient_error(error):
        raise error
    logger.warning(
        "合并请求中断（%s），等待服务端完成并核验：%s",
        type(error).__name__, filename,
    )

    deadline = time.monotonic() + _FINALIZE_VERIFY_TIMEOUT
    while time.monotonic() < deadline:
        if cancelled_flag and cancelled_flag():
            raise ValueError("上传已取消")
        size = _remote_size(disk_id, rel_path, filename)
        if size is not None and size == file_size:
            logger.info("合并已完成（核验通过）：%s", filename)
            return f"{rel_path.rstrip('/')}/{filename}" if rel_path else filename
        time.sleep(5)

    raise ApiError(504, "服务端合并超时，请稍后重试")


def download_file(
    disk_id: int,
    rel_path: str,
    local_save_path: str,
    progress_callback: ProgressCallback | None = None,
    cancelled_flag: Callable[[], bool] | None = None,
    restore_mtime: float | None = None,
) -> int:
    """
    从虚拟磁盘下载文件并以流式方式写入本地。

    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 文件相对路径
    :param local_save_path: 本地保存路径（含文件名）
    :param progress_callback: 进度回调 fn(已下载字节, 总字节, "downloading")
    :param cancelled_flag: 取消判断回调
    :param restore_mtime: 若提供，下载完成后调用 os.utime 把本地文件 mtime 还原到该值
                          （Unix 时间戳，浮点秒）。用于目录同步：保证两端 mtime 一致，
                          下次扫描就能正确识别"未变更"，避免重复下载/上传。
    :returns: 下载的字节数
    :raises ApiError: 无权限或文件不存在
    :raises ValueError: 用户取消下载
    """
    def _bridge_cb(done: int, total: int) -> None:
        # 加密流下 total=0（http_client 明文字节流不传总数），保留任务原有 file_size
        if progress_callback:
            progress_callback(done, total, "downloading")

    written = http_client.download_to_file(
        "/api/v1/files/download",
        data={"disk_id": disk_id, "path": rel_path},
        save_path=local_save_path,
        progress_callback=_bridge_cb,
        cancelled_flag=cancelled_flag,
    )
    if restore_mtime:
        try:
            os.utime(local_save_path, (restore_mtime, restore_mtime))
        except OSError as e:
            logger.warning("无法还原本地文件 mtime（不影响下载结果）: %s -> %s",
                           local_save_path, e)
    logger.info("文件下载完成: %s → %s（%d 字节）", rel_path, local_save_path, written)
    return written


def get_preview_bytes(disk_id: int, rel_path: str) -> bytes:
    """
    获取文件预览内容（原始字节）。

    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 文件相对路径
    :returns: 文件原始字节
    :raises ApiError: 无权限或文件不存在
    """
    return http_client.download_stream(
        "/api/v1/files/preview",
        data={"disk_id": disk_id, "path": rel_path},
    )


def delete_file(disk_id: int, rel_path: str) -> None:
    """
    删除指定文件或目录（目录递归删除）。

    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 文件或目录相对路径
    :raises ApiError: 无权限或路径不存在
    """
    http_client.delete("/api/v1/files", {
        "disk_id": disk_id,
        "path": rel_path,
    })
    logger.info("文件/目录已删除: disk=%d, path=%s", disk_id, rel_path)


def stream_range(
    disk_id: int,
    path: str,
    filename: str,
    range_start: int = 0,
    range_end: int = -1,
    repair_task_id: int = 0,
) -> tuple[dict, Generator[bytes, None, None]]:
    """
    流式拉取服务端文件的指定字节范围，逐帧解密后返回（v1.4.2 纯 byte 模式）。

    :param disk_id: 虚拟磁盘 ID
    :param path: 文件所在目录（磁盘内相对路径）
    :param filename: 文件名
    :param range_start: 字节起点（0=开头，负数=从末尾倒数）
    :param range_end: 字节终点不含（-1=文件结尾）
    :param repair_task_id: v1.4.2 修复产物验证播放（>0 时服务端流式返回该
        修复任务产物，忽略 disk_id/path/filename 的取值语义；仅 success 任务）
    :returns: (meta_dict, frame_iterator) 元数据字典 + 明文字节生成器
    :raises ValueError: 服务端返回错误帧，或流格式非法
    :raises ApiError: HTTP 层错误（415 含 [MEDIA_NEEDS_REPAIR] 时表示文件损坏）
    """
    payload = {
        "disk_id": disk_id,
        "path": path,
        "filename": filename,
        "range_start": range_start,
        "range_end": range_end,
    }
    if repair_task_id:
        payload["repair_task_id"] = repair_task_id
    resp = http_client.stream_request("GET", "/api/v1/files/stream", payload)

    # 读取第一帧（元数据帧）
    raw = resp.raw
    session_key = session_manager.session_key
    first_plaintext = parse_stream_frame(raw, session_key)
    if first_plaintext is None:
        resp.close()
        raise ValueError("流提前结束，未收到元数据帧")
    meta = json.loads(first_plaintext)
    if meta.get("type") == "error":
        resp.close()
        raise ValueError(meta.get("message", "服务端返回错误"))

    def _frame_iter() -> Generator[bytes, None, None]:
        """逐帧 yield 已解密的数据字节"""
        try:
            while True:
                plaintext = parse_stream_frame(raw, session_key)
                if plaintext is None:
                    break
                # 检查是否是 JSON 错误帧
                if len(plaintext) < 512:
                    try:
                        frame_json = json.loads(plaintext)
                        if frame_json.get("type") == "error":
                            msg = frame_json.get("message", "流传输中断")
                            raise ValueError(msg)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass  # 普通二进制数据帧
                yield plaintext
        except GeneratorExit:
            # seek / 关闭对话框时调用方关闭生成器，属于正常流程；
            # 不重新抛出，让 finally 完成清理后正常返回即可
            pass
        finally:
            resp.close()

    return meta, _frame_iter()


def make_dir(disk_id: int, rel_path: str) -> None:
    """
    在虚拟磁盘指定路径下创建目录。

    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 要创建的目录相对路径
    :raises ApiError: 无权限
    """
    http_client.post("/api/v1/files/mkdir", {
        "disk_id": disk_id,
        "path": rel_path,
    })
    logger.info("目录已创建: disk=%d, path=%s", disk_id, rel_path)


def rename_file(disk_id: int, path: str, new_name: str) -> None:
    """
    重命名文件或目录（v1.1.3 新增，v1.1.4 更新 docstring）。

    在当前目录内对文件或目录进行重命名，不改变所在目录位置。

    :param disk_id: 虚拟磁盘 ID
    :param path: 目标文件/目录当前相对路径（含名称）
    :param new_name: 新名称（纯名称，不含路径分隔符）
    :raises ApiError: 无权限（403）/ 名称非法（400）/ 目标不存在（404）/ 目标名称已存在（409）
    """
    http_client.post("/api/v1/files/rename", {
        "disk_id": disk_id,
        "path": path,
        "new_name": new_name,
    })
    logger.info("文件/目录已重命名: disk=%d", disk_id)


def move_file(disk_id: int, src_path: str, dst_dir_path: str) -> None:
    """
    移动文件或目录到同磁盘的另一目录（v1.1.3 新增，v1.1.4 更新 docstring）。

    :param disk_id: 虚拟磁盘 ID
    :param src_path: 被移动的文件/目录相对路径
    :param dst_dir_path: 目标目录的相对路径（空字符串表示磁盘根目录）
    :raises ApiError: 无权限（403）/ 源不存在（404）/ 目标目录不存在（404）/ 循环嵌套（400）/ 目标同名（409）
    """
    http_client.post("/api/v1/files/move", {
        "disk_id": disk_id,
        "src_path": src_path,
        "dst_dir_path": dst_dir_path,
    })
    logger.info("文件/目录已移动: disk=%d", disk_id)

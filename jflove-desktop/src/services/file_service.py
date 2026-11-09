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

from src.utils.http_client import (
    ApiError,
    clear_part_files,
    http_client,
    is_transient_error,
    load_part_meta,
    part_paths,
)
from src.utils.crypto import parse_stream_frame
from src.utils.session import session_manager
from src.config.settings import (
    CHUNK_SIZE,
    DOWNLOAD_PART_TTL_SECONDS,
    LOCAL_STORAGE_DIR,
)
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
#: 客户端类型标识（v1.9.0：服务端据此识别新客户端，缺失即 409 提示升级）
_CLIENT_TYPE = "desktop"
#: 合并请求的读取超时（秒）：异步受理本身毫秒级返回，超大超时仅作为慢网兜底
_FINALIZE_READ_TIMEOUT = 3600.0
#: 合并超时后，等待服务端完成并核验的最长时间（秒）
_FINALIZE_VERIFY_TIMEOUT = 900.0
#: 服务端合并进度轮询间隔（秒，设计 §3.1）与服务端建议值的合法区间
_MERGE_POLL_INTERVAL = 2.5
_MERGE_POLL_TIMEOUT = 3600.0
#: 断点续传记录文件：应用重启后复用上次的 upload_id 与已发送分片
_RESUME_FILE = os.path.join(LOCAL_STORAGE_DIR, "upload_resume.json")
_RESUME_LOCK = threading.Lock()
#: 每传多少个分片落一次盘（避免每 1MB 都写 JSON）
_RESUME_FLUSH_EVERY = 32
#: 合并失败原因 → 可展示的中文（服务端返回的 error 是枚举值）
_MERGE_ERROR_TEXT = {
    "missing_chunks": "服务端合并失败：分片不完整",
    "hash_mismatch": "服务端校验失败（文件哈希不一致），请重新上传",
    "io_error": "服务端写入失败（磁盘空间或权限问题），请稍后重试",
}

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


class _MergeFailed(Exception):
    """
    服务端合并失败（v1.9.0）。

    携带 `missing_chunks` 时上层可补传这些分片后再次 complete；无缺失片
    （如 hash_mismatch / io_error）则由用户重试（下次 init 会拿到 restart_required）。
    """

    def __init__(self, detail: str, missing_chunks: list[int] | None = None):
        super().__init__(detail)
        self.detail = detail
        self.missing_chunks = missing_chunks or []


def _received_chunks(resp: dict, total_chunks: int) -> set[int]:
    """从 init 响应解析已收分片集合（过滤非法索引）"""
    raw = resp.get("received_chunks")
    if not isinstance(raw, list):
        return set()
    out: set[int] = set()
    for item in raw:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= index < total_chunks:
            out.add(index)
    return out


def upload_file(
    disk_id: int,
    rel_path: str,
    local_path: str,
    progress_callback: ProgressCallback | None = None,
    cancelled_flag: Callable[[], bool] | None = None,
    preserve_mtime: bool = True,
) -> str:
    """
    分片上传本地文件到虚拟磁盘（v1.9.0 断点续传 + 异步合并）。

    流程：
      1. 边读边算 SHA256（"hashing" 阶段，按字节回调进度）
      2. `init`（携带 `client_type="desktop"`）——服务端按内容指纹幂等复用会话，
         响应里的 `received_chunks` 是**已收分片的唯一权威来源**；本地
         `storage/upload_resume.json` 只在旧服务端未返回该字段时作为兜底
      3. 只补传缺失分片（"uploading"），单片失败按指数退避重试
      4. `complete` 携带 `async_mode=true` 立即受理，随后每 2.5s 轮询
         `upload/progress`，按 `done` / `failed` / `queued` 收尾

    失败语义（F46.5 / F47.4）：
      - 网络失败 / 合并失败：**不主动 DELETE 服务端会话**，保留已传分片供续传
      - 只有用户取消（`cancelled_flag=True`）才清理服务端会话与本地续传记录
      - 合并缺片时先补传缺失分片再合并一次；校验失败/IO 失败保留会话供用户重试

    :param disk_id: 目标虚拟磁盘 ID
    :param rel_path: 目标目录相对路径
    :param local_path: 本地文件绝对路径
    :param progress_callback: 进度回调 fn(已传输字节, 总字节, 阶段)
    :param cancelled_flag: 取消判断回调，返回 True 时中止
    :param preserve_mtime: 是否把源文件 mtime 传给服务端并在合并时还原
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

    logger.info(
        "开始上传文件: %s，大小=%d，分片=%d（%d KB/片）",
        filename, file_size, total_chunks, CHUNK_SIZE // 1024,
    )

    # ── 阶段 2：初始化上传会话（client_type 为 v1.9.0 必传字段）──
    init_payload = {
        "disk_id": disk_id,
        "path": rel_path,
        "filename": filename,
        "file_size": file_size,
        "total_chunks": total_chunks,
        "file_hash": file_hash,
        "client_type": _CLIENT_TYPE,
    }
    if file_mtime is not None:
        # 传递源文件 mtime，让服务端在 complete 时还原；
        # 关键作用：避免目录同步陷入"上传后远端时间被刷新→下次同步反向覆盖"的循环
        init_payload["mtime"] = file_mtime

    resume_key = _resume_key(disk_id, rel_path, filename, file_size, file_mtime)
    resume = _resume_get(resume_key)
    local_sent: set[int] = set()
    if resume:
        local_sent = {int(i) for i in (resume.get("sent") or [])}
        logger.info(
            "发现未完成的上传记录，将由服务端状态决定续传点：%s（本地记录 %d/%d 片）",
            filename, len(local_sent), total_chunks,
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

    upload_id = ""
    received: set[int] = set()
    reinit_used = False
    missing_repaired = False
    skip_send_once = False

    while True:
        try:
            if not upload_id:
                resp = http_client.post("/api/v1/files/upload/init", init_payload)
                if resp.get("already_exists"):
                    # 服务端判定目标文件已完整存在：不创建会话、不重传
                    path = str(resp.get("already_path") or _remote_path(rel_path, filename))
                    _resume_clear(resume_key)
                    if progress_callback:
                        progress_callback(file_size, file_size, "uploading")
                    logger.info("服务端判定目标文件已存在，跳过上传：%s", filename)
                    return path
                upload_id = str(resp.get("upload_id") or "")
                if not upload_id:
                    raise ApiError(502, "上传初始化失败：服务端未返回 upload_id")

                if resp.get("restart_required"):
                    # 上一次会话已失败：服务端新建了会话，必须从第 0 片开始
                    received = set()
                    logger.info("服务端要求重新开始，从第 0 片上传：%s", filename)
                elif isinstance(resp.get("received_chunks"), list):
                    received = _received_chunks(resp, total_chunks)
                elif local_sent:
                    # 兜底：旧服务端没有 received_chunks 字段时用本地记录
                    received = set(local_sent)

                expires_at = resp.get("expires_at")
                if received:
                    logger.info(
                        "续传：%s 已收 %d/%d 片，跳过已传分片（可续传至 %s）",
                        filename, len(received), total_chunks, expires_at or "服务端未返回",
                    )
                else:
                    logger.info("从第 0 片开始上传：%s", filename)

            skip = set() if missing_repaired else received
            if skip_send_once:
                # 缺片补传已在本轮完成，直接重新触发合并
                skip_send_once = False
            else:
                _send_chunks(
                    upload_id, local_path, file_size, total_chunks,
                    progress_callback, cancelled_flag,
                    skip=skip, resume_key=resume_key,
                )
            result = _finalize(
                upload_id, disk_id, rel_path, filename, file_size,
                progress_callback, cancelled_flag,
            )
            _resume_clear(resume_key)
            return result
        except ValueError:
            # 只有用户明确取消才清理服务端会话与本地续传记录
            _cancel_session(upload_id)
            _resume_clear(resume_key)
            raise
        except _MergeFailed as e:
            if e.missing_chunks and not missing_repaired:
                # 缺片：补传缺失分片后再合并一次（F47.4）
                missing_repaired = True
                logger.warning(
                    "服务端合并缺片 %s，补传后重试合并：%s",
                    e.missing_chunks[:10], filename,
                )
                try:
                    _send_chunks(
                        upload_id, local_path, file_size, total_chunks,
                        progress_callback, cancelled_flag,
                        skip=received, resume_key=resume_key,
                        only=set(e.missing_chunks),
                    )
                except ValueError:
                    _cancel_session(upload_id)
                    _resume_clear(resume_key)
                    raise
                except Exception as send_error:  # noqa: BLE001
                    # 补片失败：保留会话（下次重试仍可续传）
                    logger.warning("补齐缺失分片失败：%s", type(send_error).__name__)
                    raise ApiError(409, e.detail) from send_error
                skip_send_once = True
                continue
            # 校验失败 / IO 失败：会话与分片保留，由用户下次重试（init 会给出 restart_required）
            raise ApiError(409, e.detail) from e
        except Exception as e:  # noqa: BLE001 - 分类后决定重试或上抛
            if _is_session_lost(e) and not reinit_used and not missing_repaired:
                # 服务端重启 / 会话过期：无法续传，重新 init 并从第 0 片传一次
                logger.warning(
                    "上传会话已失效（%s），重新初始化：%s", type(e).__name__, filename,
                )
                _resume_clear(resume_key)
                upload_id = ""
                received = set()
                reinit_used = True
                continue
            # 网络失败 / 合并失败：不 DELETE 服务端会话，保留续传记录
            logger.warning(
                "上传中断（%s），保留服务端会话与续传记录：%s",
                type(e).__name__, filename,
            )
            raise


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
    only: set[int] | None = None,
) -> None:
    """
    逐个分片上传，**单个分片失败自动重试**（指数退避，最多 `_CHUNK_ATTEMPTS` 次）。

    为什么按分片重试：整文件重传的代价是「重算哈希 + 重传几十 GB」，
    而分片重发只是 512KB —— 这是「传了一晚上没成功」的根因修复点之一。

    :param skip: 服务端已有 / 本地记录已发送的分片索引（不发送）
    :param only: 只补传这些分片（合并缺片修复用）；None 表示按 skip 规则全量扫描
    """
    retried = 0
    skip = skip or set()
    sent: set[int] = set(skip)
    try:
        with open(local_path, "rb") as f:
            for i in range(total_chunks):
                if cancelled_flag and cancelled_flag():
                    raise ValueError("上传已取消")

                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                if only is not None and i not in only:
                    # 读一帧以保持文件偏移，但本次不发送（只补缺失分片）
                    continue
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
    finally:
        # 无论成功 / 中断都落一次盘：下次启动可据此兜底（服务端 received_chunks 优先）
        if resume_key and upload_id:
            _resume_update(resume_key, upload_id, sent)

    if retried:
        logger.info("本次上传共发生 %d 次分片重试", retried)


def _query_merge_progress(upload_id: str) -> dict:
    """
    查询服务端合并进度。

    网络异常 / 旧服务端无该接口时返回空表（只影响进度展示与收尾判断，
    不把一次查询失败当成上传失败）。
    """
    try:
        info = http_client.post(
            "/api/v1/files/upload/progress",
            {"upload_id": upload_id},
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001 - 进度查询失败不应推倒上传
        logger.warning("查询合并进度失败：%s", type(e).__name__)
        return {}
    return info if isinstance(info, dict) else {}


def _merge_missing_chunks(info: dict) -> list[int]:
    """解析进度/失败响应里的缺失分片索引"""
    raw = info.get("missing_chunks")
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _merge_error_detail(error: str, missing: list[int]) -> str:
    """把服务端合并失败枚举翻译成可展示的中文（不回显路径/文件名）"""
    detail = _MERGE_ERROR_TEXT.get(error.strip(), error.strip() or "服务端合并失败")
    if missing:
        preview = "、".join(str(i) for i in missing[:10])
        detail += f"（缺失分片：{preview}）"
    return detail


def _raise_merge_failed(info: dict) -> None:
    """把一次 failed 状态转换成带缺失片信息的内部异常"""
    missing = _merge_missing_chunks(info)
    raise _MergeFailed(
        _merge_error_detail(str(info.get("error") or ""), missing),
        missing,
    )


def _report_merge_state(
    info: dict,
    file_size: int,
    progress_callback: ProgressCallback | None,
) -> None:
    """把 queued / merging 状态映射为界面的 finalizing 进度"""
    if progress_callback is None:
        return
    state = str(info.get("state") or "")
    if state == "queued":
        # 排队中：没有可用的字节进度，但要让界面知道「服务端已受理、在排队」
        progress_callback(0, file_size, "finalizing")
        return
    if state != "merging":
        return
    total = int(info.get("file_size") or 0) or file_size
    processed = int(info.get("processed") or 0)
    if total > 0:
        progress_callback(min(processed, total), total, "finalizing")


def _verify_remote_completion(
    disk_id: int,
    rel_path: str,
    filename: str,
    file_size: int,
) -> str | None:
    """
    核验「服务端其实已经合并完成」（合并超时 / 响应丢失时的兜底）。

    :returns: 已完整落盘则返回远端路径，否则 None
    """
    size = _remote_size(disk_id, rel_path, filename)
    if size is None or size != file_size:
        return None
    logger.info("合并已完成（远端大小核验通过）：%s", filename)
    return _remote_path(rel_path, filename)


def _wait_remote_completion(
    disk_id: int,
    rel_path: str,
    filename: str,
    file_size: int,
    cancelled_flag: Callable[[], bool] | None,
) -> str | None:
    """在 `_FINALIZE_VERIFY_TIMEOUT` 内等待远端文件出现且大小一致"""
    deadline = time.monotonic() + _FINALIZE_VERIFY_TIMEOUT
    while time.monotonic() < deadline:
        if cancelled_flag and cancelled_flag():
            raise ValueError("上传已取消")
        path = _verify_remote_completion(disk_id, rel_path, filename, file_size)
        if path is not None:
            return path
        time.sleep(5)
    return None


def _poll_interval(result: dict) -> float:
    """服务端建议的轮询间隔（默认 2.5s，限制在 0.05~30s 防止被异常值拖死）"""
    try:
        interval = float(result.get("poll_interval_s"))
    except (TypeError, ValueError):
        return _MERGE_POLL_INTERVAL
    if 0.05 <= interval <= 30.0:
        return interval
    return _MERGE_POLL_INTERVAL


def _poll_until_merged(
    upload_id: str,
    disk_id: int,
    rel_path: str,
    filename: str,
    file_size: int,
    result: dict,
    progress_callback: ProgressCallback | None,
    cancelled_flag: Callable[[], bool] | None,
) -> str:
    """
    轮询 `/files/upload/progress` 直到 done / failed / 超时（设计 §3.3）。

    服务端合并是后台线程，complete 立即返回 `merging` / `queued`；
    客户端每 2.5 秒（或服务端建议值）轮询一次，done 收尾、failed 带原因上报。
    """
    interval = _poll_interval(result)
    deadline = time.monotonic() + _MERGE_POLL_TIMEOUT
    query_failures = 0
    while time.monotonic() < deadline:
        if cancelled_flag and cancelled_flag():
            raise ValueError("上传已取消")
        info = _query_merge_progress(upload_id)
        if not info:
            query_failures += 1
            if query_failures >= 3:
                # 连续查不到进度：先核验远端，避免「其实已完成却被判失败」
                path = _wait_remote_completion(
                    disk_id, rel_path, filename, file_size, cancelled_flag,
                )
                if path is not None:
                    return path
                raise ApiError(502, "无法查询服务端合并进度，请稍后重试")
            time.sleep(interval)
            continue
        query_failures = 0
        state = str(info.get("state") or "unknown")
        if state == "done":
            logger.info("服务端合并完成：%s", filename)
            return str(info.get("path") or "") or _remote_path(rel_path, filename)
        if state == "failed":
            _raise_merge_failed(info)
        if state == "unknown":
            path = _verify_remote_completion(disk_id, rel_path, filename, file_size)
            if path is not None:
                return path
            raise _MergeFailed("服务端上传会话已丢失，请重新上传")
        _report_merge_state(info, file_size, progress_callback)
        time.sleep(interval)

    path = _verify_remote_completion(disk_id, rel_path, filename, file_size)
    if path is not None:
        return path
    raise ApiError(504, "服务端合并超时，请稍后重试")


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
    触发服务端**异步**合并并等待结果（v1.9.0）。

    关键点：
      1. `complete` 带 `async_mode=true`：服务端立即受理并返回 merging/queued，
         请求不再因「读 3GB 分片 + SHA256 + 原子替换」而超时（F47 / D27）；
      2. 等待受理响应期间仍轮询进度，界面不会停在「处理中」；
      3. 响应丢失 / 超时：**不盲目重发**（同一会话并发合并会破坏临时文件），
         改为轮询 progress 并核验远端大小，保住「其实已合并完成」的场景。
    """
    if progress_callback:
        progress_callback(0, file_size, "finalizing")

    outcome: dict[str, object] = {}

    def _do_complete() -> None:
        try:
            outcome["result"] = http_client.post(
                "/api/v1/files/upload/complete",
                {"upload_id": upload_id, "async_mode": True},
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
        info = _query_merge_progress(upload_id)
        if str(info.get("state") or "") == "failed":
            _raise_merge_failed(info)
        _report_merge_state(info, file_size, progress_callback)

    error = outcome.get("error")
    if error is not None:
        if not is_transient_error(error):
            raise error
        logger.warning(
            "合并请求中断（%s），查询进度并核验远端：%s",
            type(error).__name__, filename,
        )
        # 受理请求可能已送达服务端：先查一次进度，再进入远端核验兜底
        info = _query_merge_progress(upload_id)
        state = str(info.get("state") or "")
        if state == "done":
            return str(info.get("path") or "") or _remote_path(rel_path, filename)
        if state == "failed":
            _raise_merge_failed(info)
        _report_merge_state(info, file_size, progress_callback)
        path = _wait_remote_completion(
            disk_id, rel_path, filename, file_size, cancelled_flag,
        )
        if path is not None:
            return path
        raise ApiError(504, "服务端合并超时，请稍后重试")

    result = outcome.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    state = str(result.get("state") or "")
    if state == "failed":
        _raise_merge_failed(result)
    if result.get("path") and state in ("", "done"):
        # 兼容旧服务端 / 已完成响应：直接拿到最终路径
        logger.info("文件上传完成: %s", filename)
        return str(result["path"])
    return _poll_until_merged(
        upload_id, disk_id, rel_path, filename, file_size, result,
        progress_callback, cancelled_flag,
    )


def _apply_restore_mtime(path: str, restore_mtime: float | None) -> None:
    """下载完成后还原 mtime（目录同步用；失败只告警，不影响下载结果）"""
    if not restore_mtime:
        return
    try:
        os.utime(path, (restore_mtime, restore_mtime))
    except OSError as e:
        logger.warning("无法还原本地文件 mtime（不影响下载结果）：%s", e)


def _part_is_stale(meta: dict, part_path: str) -> bool:
    """
    本地 part 是否超过保留期（F48.5 / D33：7 天）。

    优先用 meta 里的 `updated_at`；若缺失则退回 part 文件的 mtime。
    """
    updated_at = 0.0
    try:
        updated_at = float(meta.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated_at = 0.0
    if updated_at <= 0:
        try:
            updated_at = os.path.getmtime(part_path)
        except OSError:
            return True
    return (time.time() - updated_at) > DOWNLOAD_PART_TTL_SECONDS


def download_file(
    disk_id: int,
    rel_path: str,
    local_save_path: str,
    progress_callback: ProgressCallback | None = None,
    cancelled_flag: Callable[[], bool] | None = None,
    restore_mtime: float | None = None,
) -> int:
    """
    从虚拟磁盘下载文件并以流式方式写入本地（v1.9.0 支持断点续传）。

    续传流程（F48.2 / 设计 §3.1）：
      1. 读目标文件旁的 `<name>.part` 与 `<name>.jflove-part.json`
         （远端 size / version / 已写偏移）；
      2. 远端未变且 part 未超 7 天 → 以 part 大小为 `range_start` 续传；
      3. 远端版本变化 / part 超期 / 缺少 meta → 删除 part 从头下载；
      4. 完成且大小一致后 `os.replace` 原子改名；失败保留 part，用户取消删除。

    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 文件相对路径
    :param local_save_path: 本地保存路径（含文件名）
    :param progress_callback: 进度回调 fn(已下载字节含续传起点, 总字节, "downloading")
    :param cancelled_flag: 取消判断回调
    :param restore_mtime: 若提供，下载完成后调用 os.utime 把本地文件 mtime 还原到该值
                          （Unix 时间戳，浮点秒）。用于目录同步：保证两端 mtime 一致，
                          下次扫描就能正确识别"未变更"，避免重复下载/上传。
    :returns: 下载完成后的文件总字节数（含续传已写部分）
    :raises ApiError: 无权限或文件不存在
    :raises ValueError: 用户取消下载
    """
    def _bridge_cb(done: int, total: int) -> None:
        # total 来自响应头 X-Stream-Total；续传时 done 含 part 已有的起点字节
        if progress_callback:
            progress_callback(done, total, "downloading")

    part_path, meta_path = part_paths(local_save_path)
    meta = load_part_meta(meta_path)
    meta_size = 0
    if meta:
        try:
            meta_size = int(meta.get("size") or 0)
        except (TypeError, ValueError):
            meta_size = 0

    # 上次其实已下载完成、但进程在 os.replace 后、清 meta 前被杀：直接收尾
    if meta and meta_size > 0 and os.path.exists(local_save_path):
        try:
            target_size = os.path.getsize(local_save_path)
        except OSError:
            target_size = -1
        if target_size == meta_size:
            clear_part_files(part_path, meta_path)
            _apply_restore_mtime(local_save_path, restore_mtime)
            if progress_callback:
                progress_callback(meta_size, meta_size, "downloading")
            logger.info("本地目标文件已完整存在，跳过重复下载（%d 字节）", meta_size)
            return meta_size

    range_start = 0
    expected_meta: dict | None = None
    try:
        part_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    except OSError:
        part_size = 0

    if meta and _part_is_stale(meta, part_path):
        # 超过 7 天：part 失效，删除从头下载（F48.5）
        logger.info("本地下载 part 已超过保留期，删除并重新下载")
        clear_part_files(part_path, meta_path)
        meta = None
        part_size = 0

    if not meta or meta_size <= 0:
        # 没有可信 meta 时无法校验远端版本：一律删除 part 从头下载，避免拼接坏文件
        if os.path.exists(part_path):
            clear_part_files(part_path, meta_path)
        part_size = 0
    elif part_size > meta_size:
        logger.warning("本地下载 part 大于远端文件，删除并重新下载")
        clear_part_files(part_path, meta_path)
        part_size = 0

    # part 已完整（上次在改名之前中断）时不直接改名：仍带 range_start=part_size
    # 请求一次以拿到 X-File-Version，确保远端文件没有在中断期间被同大小替换
    # （AC-336）；服务端会返回 0 字节流，客户端校验后 os.replace。
    if part_size > 0:
        range_start = part_size
        expected_meta = meta
        logger.info("检测到未完成的下载，从第 %d 字节继续", range_start)

    written = http_client.download_to_file(
        "/api/v1/files/download",
        data={"disk_id": disk_id, "path": rel_path},
        save_path=local_save_path,
        progress_callback=_bridge_cb,
        cancelled_flag=cancelled_flag,
        range_start=range_start,
        expected_meta=expected_meta,
        meta_path=meta_path,
    )
    _apply_restore_mtime(local_save_path, restore_mtime)
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

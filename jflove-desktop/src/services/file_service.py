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
from typing import Callable, Generator

from src.utils.http_client import http_client
from src.utils.crypto import parse_stream_frame
from src.utils.session import session_manager
from src.config.settings import CHUNK_SIZE
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 进度回调签名: fn(已传输字节, 总字节数, 阶段标识)
# 阶段标识取值：
#   "hashing"   - 上传前的 SHA256 校验阶段
#   "uploading" - 分片上传阶段
#   "downloading" - 下载阶段
ProgressCallback = Callable[[int, int, str], None]


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

    # ── 阶段 1：SHA256 校验（流式，不缓存全部内容）──
    sha256 = hashlib.sha256()
    bytes_read = 0
    with open(local_path, "rb") as f:
        while True:
            if cancelled_flag and cancelled_flag():
                raise ValueError("上传已取消")
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sha256.update(chunk)
            bytes_read += len(chunk)
            if progress_callback:
                progress_callback(bytes_read, file_size, "hashing")
    file_hash = sha256.hexdigest()

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
    resp = http_client.post("/api/v1/files/upload/init", init_payload)
    upload_id = resp["upload_id"]

    try:
        # ── 阶段 3：分片上传（每片单独从磁盘读取，避免大文件占用内存） ──
        with open(local_path, "rb") as f:
            for i in range(total_chunks):
                if cancelled_flag and cancelled_flag():
                    try:
                        http_client.delete(f"/api/v1/files/upload/{upload_id}")
                    except Exception:
                        pass
                    raise ValueError("上传已取消")

                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                chunk_b64 = base64.b64encode(chunk).decode()
                http_client.post("/api/v1/files/upload/chunk", {
                    "upload_id": upload_id,
                    "chunk_index": i,
                    "chunk_data": chunk_b64,
                })

                if progress_callback:
                    done_bytes = min((i + 1) * CHUNK_SIZE, file_size)
                    progress_callback(done_bytes, file_size, "uploading")

        # ── 阶段 4：合并分片 ──
        result = http_client.post("/api/v1/files/upload/complete", {
            "upload_id": upload_id,
        })
        logger.info("文件上传完成: %s", filename)
        return result.get("path", "")

    except ValueError:
        # 用户取消，前面已清理临时分片
        raise
    except Exception as e:
        # 上传失败时尝试取消，清理临时分片
        try:
            http_client.delete(f"/api/v1/files/upload/{upload_id}")
        except Exception:
            pass
        raise e


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

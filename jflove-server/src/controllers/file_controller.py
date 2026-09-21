"""
文件管理控制器

处理虚拟磁盘上的文件/目录操作，包括：
  - 目录浏览
  - 分片上传（init → chunk → complete），支持断点续传和取消
  - 文件下载
  - 文件/目录删除
  - 目录创建
  - 文件预览
  - 流式 Range 预览（v1.1.0 新增）

所有接口均使用 ChaCha20-Poly1305 加密传输，须携带 X-Session-ID 请求头。
"""

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
import aiosqlite

from src.config.settings import REPAIR_DIR_NAME
from src.models.database import get_db
from src.repositories import repair_task_repository, virtual_disk_repository
from src.services import file_service, auth_service
from src.utils import media_probe
from src.utils.middleware import decrypt_request_body, encrypt_response
from src.utils.crypto import encrypt_stream_chunk, STREAM_PLAINTEXT_CHUNK_SIZE
from src.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/files", tags=["文件管理"])
logger = get_logger(__name__)


def _to_int(value, default: int) -> int:
    """数值容错：非法/缺失输入返回默认值，防止异常输入触发 500（M1 修复）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float) -> float:
    """数值容错：非法/缺失输入返回默认值，防止异常输入触发 500（M1 修复）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _get_user(request: Request, db: aiosqlite.Connection, body: dict) -> dict:
    """
    从加密请求体中提取 JWT 令牌并解析当前用户。

    安全说明：仅从已解密的请求体读取 token，**不再支持** Authorization 请求头
    兜底——避免企业 MITM 代理观察到明文 Bearer token。

    :param request: FastAPI 请求对象（保留用于后续扩展）
    :param db: 数据库连接
    :param body: 已解密的请求体字典
    :returns: 当前用户信息字典
    :raises HTTPException: 令牌无效或过期时返回 401
    """
    token = body.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="缺少认证令牌")
    try:
        return await auth_service.get_current_user(db, token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get(
    "/disks",
    summary="获取可访问磁盘列表",
    description=(
        "获取当前用户可访问的虚拟磁盘列表。\n\n"
        "管理员返回全部磁盘；普通用户仅返回拥有读权限的磁盘。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n\n"
        "响应体（加密后）字段：\n"
        "- `disks`：磁盘列表，每项含 `id`、`name`、`can_write`、`can_delete`\n"
        "  （v1.4.2 新增 `can_delete`：修复功能要求写+删权限并存，三端据此\n"
        "  禁用/隐藏「修复损坏媒体」入口）"
    ),
)
async def list_accessible_disks(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """获取当前用户可访问的磁盘列表"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disks = await file_service.list_accessible_disks(db, user["id"], user["role"])
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"disks": disks})


@router.get(
    "/list",
    summary="浏览目录",
    description=(
        "列出指定虚拟磁盘目录下的文件和子目录（目录在前，文件在后，均按名称排序）。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：虚拟磁盘 ID\n"
        "- `path`：相对路径（默认为根目录 `\"\"`）\n\n"
        "响应体（加密后）字段：\n"
        "- `files`：文件/目录列表，每项含 `name`、`is_dir`、`size`、`modified_at`"
    ),
)
async def list_files(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """列出目录内容"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = int(body.get("disk_id", 0))
    rel_path = body.get("path", "")
    try:
        files = await file_service.list_files(db, user["id"], user["role"], disk_id, rel_path)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"files": files})


@router.post(
    "/upload/init",
    summary="初始化分片上传",
    description=(
        "创建分片上传会话，返回 `upload_id` 供后续分片上传使用。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：目标虚拟磁盘 ID\n"
        "- `path`：目标目录相对路径\n"
        "- `filename`：文件名\n"
        "- `file_size`：文件总大小（字节）\n"
        "- `total_chunks`：总分片数\n"
        "- `file_hash`：文件 SHA256 哈希值（用于完整性校验）\n"
        "- `mtime`：可选，源文件的修改时间（Unix 时间戳，浮点秒）。"
        "若提供，服务端会在合并完成后还原文件 mtime，避免目录同步时反复"
        "重传同一文件\n\n"
        "响应体（加密后）字段：\n"
        "- `upload_id`：上传会话 ID（UUID）"
    ),
)
async def init_upload(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """初始化分片上传，返回 upload_id"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = int(body.get("disk_id", 0))
    rel_path = body.get("path", "")
    filename = body.get("filename", "").strip()
    file_size = int(body.get("file_size", 0))
    total_chunks = int(body.get("total_chunks", 1))
    file_hash = body.get("file_hash", "")
    mtime_raw = body.get("mtime")
    mtime = float(mtime_raw) if mtime_raw not in (None, "", 0) else None
    if not filename or not file_hash:
        raise HTTPException(status_code=400, detail="filename 和 file_hash 不能为空")
    try:
        upload_id = await file_service.init_upload(
            db, user["id"], user["role"], disk_id, rel_path,
            filename, file_size, total_chunks, file_hash, mtime,
        )
    except (PermissionError, ValueError) as e:
        status = 403 if isinstance(e, PermissionError) else 400
        raise HTTPException(status_code=status, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"upload_id": upload_id})


@router.post(
    "/upload/chunk",
    summary="上传单个分片",
    description=(
        "上传文件的某一个分片数据，可乱序上传，支持断点续传（已上传分片重传会覆盖）。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `upload_id`：上传会话 ID\n"
        "- `chunk_index`：分片索引（从 0 开始）\n"
        "- `chunk_data`：分片数据（Base64 编码）"
    ),
)
async def upload_chunk(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """上传单个文件分片（验证 upload_id 归属当前用户）"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    upload_id = body.get("upload_id", "")
    chunk_index = int(body.get("chunk_index", 0))
    import base64
    chunk_data = base64.b64decode(body.get("chunk_data", ""))
    # 防权限绕过：upload_id 必须由当前用户创建（非法者即便拿到 upload_id 也不能写入）
    if not file_service.is_upload_owned_by(upload_id, user["id"]):
        raise HTTPException(status_code=403, detail="无权操作该上传会话")
    try:
        await file_service.upload_chunk(upload_id, chunk_index, chunk_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": f"分片 {chunk_index} 已接收"})


@router.post(
    "/upload/complete",
    summary="完成分片上传",
    description=(
        "合并所有已上传分片，校验 SHA256 哈希，写入目标路径，清理临时文件。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `upload_id`：上传会话 ID\n\n"
        "响应体（加密后）字段：\n"
        "- `message`：操作结果描述\n"
        "- `path`：服务端最终文件绝对路径"
    ),
)
async def complete_upload(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """合并分片并校验 SHA256，完成上传（验证 upload_id 归属当前用户）"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    upload_id = body.get("upload_id", "")
    if not file_service.is_upload_owned_by(upload_id, user["id"]):
        raise HTTPException(status_code=403, detail="无权操作该上传会话")
    try:
        dest = await file_service.complete_upload(upload_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "上传完成", "path": dest})


@router.delete(
    "/upload/{upload_id}",
    summary="取消上传",
    description=(
        "取消指定上传会话，清理服务端临时分片文件。\n\n"
        "路径参数：\n"
        "- `upload_id`：上传会话 ID\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌"
    ),
)
async def cancel_upload(
    upload_id: str, request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """取消分片上传（验证 upload_id 归属当前用户后才允许清理）"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    # 路径中的 upload_id 必须属于当前用户，防止换 ID 取消别人的上传
    if not file_service.is_upload_owned_by(upload_id, user["id"]):
        raise HTTPException(status_code=403, detail="无权操作该上传会话")
    await file_service.cancel_upload(upload_id)
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "上传已取消"})


def _build_encrypted_file_stream(file_path: str, session_key: bytes):
    """
    生成器：分块读取文件 → 每块独立 ChaCha20-Poly1305 加密 → 输出帧字节。

    供 download / preview 共用。明文按 STREAM_PLAINTEXT_CHUNK_SIZE 切片，
    每片用 encrypt_stream_chunk 加密为 [4B len][12B nonce][密文+16B tag] 帧。
    """
    def _gen():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(STREAM_PLAINTEXT_CHUNK_SIZE)
                if not chunk:
                    break
                yield encrypt_stream_chunk(session_key, chunk)
    return _gen()


@router.get(
    "/download",
    summary="下载文件（端到端加密流）",
    description=(
        "校验读取权限后，把文件按 64KB 明文切片，每片独立 ChaCha20-Poly1305 加密\n"
        "后以 [4B 长度][12B nonce][密文+认证标签] 帧格式流式返回。\n\n"
        "**响应 Content-Type 为 application/octet-stream，整个响应体均为密文**，\n"
        "公司 MITM 代理即便能解密 HTTPS，仍无法看到任何文件内容/文件名。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：虚拟磁盘 ID\n"
        "- `path`：文件相对路径"
    ),
)
async def download_file(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """下载指定文件，响应为加密文件流"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = int(body.get("disk_id", 0))
    rel_path = body.get("path", "")
    try:
        file_path = await file_service.download_file(
            db, user["id"], user["role"], disk_id, rel_path
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    session_id = request.headers.get("X-Session-ID", "")
    session_key = auth_service.get_session_key(session_id)
    if not session_key:
        raise HTTPException(status_code=401, detail="会话已失效")

    # 注意：不在 Content-Disposition 中放 filename，避免 MITM 看到文件名
    # 客户端从已加密的请求 path 字段自己决定保存名
    return StreamingResponse(
        _build_encrypted_file_stream(file_path, session_key),
        media_type="application/octet-stream",
        headers={"X-Encrypted-Stream": "v1"},
    )


@router.post(
    "/rename",
    summary="重命名文件或目录（v1.1.3）",
    description=(
        "在当前目录内对文件或目录进行重命名，不改变其所在目录位置。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：虚拟磁盘 ID\n"
        "- `path`：目标文件/目录当前相对路径（含名称，例如 `\"docs/old.txt\"`）\n"
        "- `new_name`：新名称（纯名称，不含路径分隔符）\n\n"
        "响应体（加密后）字段：\n"
        "- `message`：操作结果描述\n\n"
        "安全说明：服务端对 new_name 做合法性校验（非空、不含路径分隔符、"
        "不为 `.` 或 `..`），并检查目标名称是否已存在（409）。"
    ),
)
async def rename_file(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """重命名文件或目录，同目录内同名时返回 409"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = int(body.get("disk_id", 0))
    path = body.get("path", "")
    new_name = body.get("new_name", "")
    try:
        await file_service.rename_file(db, user["id"], user["role"], disk_id, path, new_name)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "已重命名"})


@router.post(
    "/move",
    summary="移动文件或目录（v1.1.3）",
    description=(
        "将文件或目录移动到同一磁盘内的另一个目录。服务端原地执行 OS rename，\n"
        "不产生文件内容重复读写，大文件也可即时完成。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：虚拟磁盘 ID\n"
        "- `src_path`：被移动的文件/目录相对路径（例如 `\"docs/file.txt\"`）\n"
        "- `dst_dir_path`：目标目录的相对路径（空字符串 `\"\"` 表示磁盘根目录）\n\n"
        "响应体（加密后）字段：\n"
        "- `message`：操作结果描述\n\n"
        "安全说明：服务端防止路径穿越（_safe_join）、防循环嵌套（目标不能是源目录的子目录）、"
        "检查目标位置同名冲突（409）。"
    ),
)
async def move_file(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """移动文件或目录，目标位置同名时返回 409，循环嵌套时返回 400"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = int(body.get("disk_id", 0))
    src_path = body.get("src_path", "")
    dst_dir_path = body.get("dst_dir_path", "")
    try:
        await file_service.move_file(
            db, user["id"], user["role"], disk_id, src_path, dst_dir_path
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "已移动"})


@router.delete(
    "",
    summary="删除文件或目录",
    description=(
        "删除指定路径的文件或目录（目录递归删除）。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：虚拟磁盘 ID\n"
        "- `path`：文件或目录相对路径"
    ),
)
async def delete_file(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """删除文件或目录"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = int(body.get("disk_id", 0))
    rel_path = body.get("path", "")
    try:
        await file_service.delete_file(db, user["id"], user["role"], disk_id, rel_path)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "已删除"})


@router.post(
    "/mkdir",
    summary="创建目录",
    description=(
        "在指定虚拟磁盘下创建目录，支持多级创建（等同于 `mkdir -p`）。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：虚拟磁盘 ID\n"
        "- `path`：要创建的目录相对路径"
    ),
)
async def make_dir(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """创建目录（支持多级路径）"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = int(body.get("disk_id", 0))
    rel_path = body.get("path", "")
    try:
        await file_service.make_dir(db, user["id"], user["role"], disk_id, rel_path)
    except (PermissionError, ValueError) as e:
        status = 403 if isinstance(e, PermissionError) else 400
        raise HTTPException(status_code=status, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "目录已创建"})


@router.get(
    "/preview",
    summary="预览文件（端到端加密流）",
    description=(
        "校验读取权限后，把文件以与 /download 完全相同的加密流格式返回。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：虚拟磁盘 ID\n"
        "- `path`：文件相对路径"
    ),
)
async def preview_file(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """预览文件，响应为加密文件流（与下载共用同一加密格式）"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = int(body.get("disk_id", 0))
    rel_path = body.get("path", "")
    try:
        file_path = await file_service.get_preview(
            db, user["id"], user["role"], disk_id, rel_path
        )
    except (PermissionError, ValueError) as e:
        status = 403 if isinstance(e, PermissionError) else 404
        raise HTTPException(status_code=status, detail=str(e))

    session_id = request.headers.get("X-Session-ID", "")
    session_key = auth_service.get_session_key(session_id)
    if not session_key:
        raise HTTPException(status_code=401, detail="会话已失效")

    return StreamingResponse(
        _build_encrypted_file_stream(file_path, session_key),
        media_type="application/octet-stream",
        headers={"X-Encrypted-Stream": "v1"},
    )


def _build_range_stream_generator(
    file_path: str,
    session_key: bytes,
    file_size: int,
    eff_start: int,
    eff_end: int,
    content_type: str,
):
    """
    v2 流式分帧生成器（供 stream_file 路由使用）。

    帧格式与 v1 相同（[4B len][12B nonce][密文+tag]），在此基础上：
      - 帧 0：元数据帧，明文为 JSON（type/file_size/range_start/range_end/content_type）
      - 帧 1..N：数据帧，每帧最大 64KB 原始文件字节
    若文件在传输途中被删除，发送错误帧后结束生成器。
    """
    # 帧 0：元数据
    # v1.4.2：stream_mode 恒为 "byte"（实时修复流已移除；字段保留供三端统一判断）
    meta = json.dumps(
        {
            "type": "meta",
            "stream_mode": "byte",
            "file_size": file_size,
            "range_start": eff_start,
            "range_end": eff_end,
            "content_type": content_type,
        },
        ensure_ascii=False,
    ).encode()
    yield encrypt_stream_chunk(session_key, meta)

    # 帧 1..N：文件数据
    remaining = eff_end - eff_start
    try:
        with open(file_path, "rb") as f:
            f.seek(eff_start)
            while remaining > 0:
                chunk_size = min(STREAM_PLAINTEXT_CHUNK_SIZE, remaining)
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield encrypt_stream_chunk(session_key, chunk)
                remaining -= len(chunk)
    except FileNotFoundError:
        # 文件在传输途中被删除，发送错误帧通知客户端
        err = json.dumps(
            {
                "type": "error",
                "code": "FILE_NOT_FOUND",
                "message": "文件不存在或已被删除",
            },
            ensure_ascii=False,
        ).encode()
        yield encrypt_stream_chunk(session_key, err)


@router.get(
    "/stream",
    summary="流式 Range 预览（v1.1.0，端到端加密；v1.4.2 移除实时修复）",
    description=(
        "校验读取权限后，按请求的字节范围（range_start / range_end）流式返回\n"
        "加密帧序列，供客户端边下边播，**不写入本地磁盘**。\n\n"
        "响应协议版本 `X-Encrypted-Stream: v2`，帧格式与 v1 相同，\n"
        "但增加了第 0 帧（元数据帧，含 file_size / content_type 等）。\n\n"
        "v1.4.2 行为：\n"
        "- 健康文件：原文件字节 range 直读（meta 帧 stream_mode=byte）\n"
        "- 携带 `repair_task_id`：流式返回该修复任务产物（验证播放，仅 success 任务）\n"
        "- 播放门禁（按客户端能力区分）：\n"
        "  - `mse_only=1`（Web 端）：仅可流式格式放行（fMP4/WebM/简单音频），\n"
        "    其余 415，detail 以 `[MEDIA_NEEDS_REPAIR]` 开头，客户端应弹「立即修复」\n"
        "  - 未声明（桌面/移动端）：仅真损坏文件 415 + `[MEDIA_NEEDS_REPAIR]`\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：虚拟磁盘 ID\n"
        "- `path`：文件所在目录（磁盘内相对路径）\n"
        "- `filename`：文件名\n"
        "- `range_start`：字节起点（0=开头；负数=从末尾倒数）\n"
        "- `range_end`：字节终点，不含（-1=文件结尾）\n"
        "- `repair_task_id`：（v1.4.2，可选）修复任务 ID，验证播放产物用\n"
        "- `mse_only`：（v1.4.2 hotfix，可选）1=仅返回 Web MSE 可流式格式"
    ),
)
async def stream_file(
    request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """
    流式 Range 预览，响应为 v2 加密帧序列（v1.4.2：仅 byte 模式）。

    v1.4.2 架构变更：移除 time 实时修复流（播放路径零 ffmpeg）。
      - 健康文件：原文件字节 range 直读 + 加密帧输出（不变）
      - 损坏/非流式文件：415 + [MEDIA_NEEDS_REPAIR] 错误码，客户端弹窗引导修复
      - 修复产物验证播放：repair_task_id 指向 success 任务的产物，
        产物为 faststart MP4（健康），走同一加密帧管线
    """
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = int(body.get("disk_id", 0))
    path = body.get("path", "")
    filename = body.get("filename", "").strip()
    range_start = _to_int(body.get("range_start", 0), 0)
    range_end = _to_int(body.get("range_end", -1), -1)
    # v1.4.2：修复产物验证播放（repair_center「验证播放」按钮）。
    # 携带 repair_task_id 时流式返回该任务产物（隐藏目录内，绕过健康判定）。
    repair_task_id = _to_int(body.get("repair_task_id", 0), 0)
    # v1.4.2 hotfix：mse_only=1 表示 Web 端（只接受 MSE 可流式格式）。
    # 桌面/移动端不传，维持原生可播格式放行。
    mse_only = _to_int(body.get("mse_only", 0), 0)

    if not filename:
        raise HTTPException(status_code=400, detail="filename 不能为空")

    session_id = request.headers.get("X-Session-ID", "")
    session_key = auth_service.get_session_key(session_id)
    if not session_key:
        raise HTTPException(status_code=401, detail="会话已失效")

    # ── v1.4.2：修复产物验证播放分支 ──
    if repair_task_id:
        artifact_path = await _resolve_repair_artifact(
            db, user, repair_task_id,
        )
        return _stream_artifact(
            artifact_path, session_key, range_start, range_end,
        )

    try:
        file_path, eff_start, eff_end, file_size, content_type = (
            await file_service.get_stream_range(
                db, user["id"], user["role"], disk_id,
                path, filename, range_start, range_end,
            )
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # ── v1.4.2 hotfix：播放门禁——区分客户端能力 ──
    # 探测结果 60s TTL 缓存，健康文件零重复探测。
    #  - mse_only=1（Web 端）：只接受可流式格式（fMP4/WebM/简单音频），
    #    其余（TS/MKV/AVI/普通 MP4/faststart/损坏）一律 415 引导修复。
    #  - 未声明（桌面/移动端）：仅拒「真损坏」（探测致命错/无媒体流），
    #    MKV/AVI/moov 尾部等原生可播格式放行。
    # 415 detail 携带 [MEDIA_NEEDS_REPAIR] 错误码前缀，客户端据此弹修复引导。
    probe = await media_probe.probe_media(file_path)
    if mse_only:
        if not media_probe.is_web_streamable(filename, probe, file_path):
            logger.info("流式预览拒绝(Web不可流式): disk_id=%s", disk_id)
            raise HTTPException(
                status_code=415,
                detail="[MEDIA_NEEDS_REPAIR] 该文件无法在线边下边播，请先修复",
            )
    elif media_probe.is_broken_media(filename, probe, file_path):
        logger.info("流式预览拒绝(需修复): disk_id=%s", disk_id)
        raise HTTPException(
            status_code=415,
            detail="[MEDIA_NEEDS_REPAIR] 该文件已损坏，无法在线播放，请先修复",
        )

    # 日志仅记录 ID / 大小，不记录路径和文件名，防 MITM 日志泄露
    logger.info(
        "流式预览: disk_id=%s size=%s range=[%s,%s]",
        disk_id, file_size, eff_start, eff_end,
    )

    return StreamingResponse(
        _build_range_stream_generator(
            file_path, session_key, file_size,
            eff_start, eff_end, content_type,
        ),
        media_type="application/octet-stream",
        headers={"X-Encrypted-Stream": "v2"},
    )


async def _resolve_repair_artifact(
    db: aiosqlite.Connection, user: dict, task_id: int
) -> str:
    """
    解析修复任务产物绝对路径（v1.4.2 验证播放用）。

    任务必须处于 success 状态且产物存在；路径由服务端任务记录推导
    （磁盘根 + rel_path + 隐藏目录 + output_name），不接受客户端传路径。
    校验当前用户对任务所属磁盘的读权限（S-1 修复：任务列表全平台共享，
    但产物内容仍受磁盘读权限约束，防止换 task_id 越权拉取内容）。

    :raises HTTPException: 任务无效（400）/ 产物不存在（404）/ 无读权限（403）
    """
    task = await repair_task_repository.find_by_id(db, task_id)
    if not task or task["status"] != "success" or not task["output_name"]:
        raise HTTPException(status_code=400, detail="任务不存在或不可播放")
    if user["role"] != "admin":
        from src.services.permission_service import check_disk_permission

        if not await check_disk_permission(db, user["id"], task["disk_id"], "read"):
            raise HTTPException(status_code=403, detail="无读取权限")
    disk = await virtual_disk_repository.find_by_id(db, task["disk_id"])
    if not disk:
        raise HTTPException(status_code=404, detail="虚拟磁盘不存在")
    from src.services.file_service import _safe_join

    repair_dir = os.path.join(
        _safe_join(disk["real_path"], task["rel_path"]), REPAIR_DIR_NAME
    )
    artifact = os.path.join(repair_dir, task["output_name"])
    if not os.path.isfile(artifact):
        raise HTTPException(status_code=404, detail="修复产物不存在")
    return artifact


def _stream_artifact(
    artifact_path: str, session_key: bytes, range_start: int, range_end: int
) -> StreamingResponse:
    """修复产物加密帧流（与正常文件同一 v2 帧管线，支持字节 range）。"""
    file_size = os.path.getsize(artifact_path)
    eff_start = max(0, min(range_start, file_size))
    eff_end = range_end if range_end >= 0 else file_size
    eff_end = max(eff_start, min(eff_end, file_size))
    logger.info("修复产物验证播放: size=%s range=[%s,%s]", file_size, eff_start, eff_end)
    return StreamingResponse(
        _build_range_stream_generator(
            artifact_path, session_key, file_size,
            eff_start, eff_end, "video/mp4",
        ),
        media_type="application/octet-stream",
        headers={"X-Encrypted-Stream": "v2"},
    )

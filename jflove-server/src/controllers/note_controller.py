"""
笔记控制器

处理笔记文件的增删改查和重命名操作。
笔记目录由系统配置 notes_disk_id 动态指定，仅支持 .md 格式文件。

所有接口均使用 ChaCha20-Poly1305 加密传输，须携带 X-Session-ID 请求头。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
import aiosqlite

from src.models.database import get_db
from src.services import note_service, auth_service
from src.repositories import user_repository, virtual_disk_repository
from src.utils.middleware import decrypt_request_body, encrypt_response

router = APIRouter(prefix="/api/v1/notes", tags=["笔记"])


async def _get_user(request: Request, db: aiosqlite.Connection, body: dict) -> dict:
    """
    从请求体或 Authorization 头中提取 JWT 令牌并解析当前用户。

    :param request: FastAPI 请求对象
    :param db: 数据库连接
    :param body: 已解密的请求体字典
    :returns: 当前用户信息字典
    :raises HTTPException: 令牌无效或过期时返回 401
    """
    token = body.get("token", "")
    try:
        return await auth_service.get_current_user(db, token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get(
    "/list",
    summary="获取笔记列表",
    description=(
        "列出笔记目录下所有 `.md` 文件，按文件名排序。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n\n"
        "响应体（加密后）字段：\n"
        "- `notes`：笔记列表，每项含 `filename`、`size`、`modified_at`"
    ),
)
async def list_notes(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """列出所有笔记文件"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    try:
        notes = await note_service.list_notes(db, user["id"], user["role"])
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"notes": notes})


@router.get(
    "/read",
    summary="读取笔记内容",
    description=(
        "读取指定笔记文件的文本内容（UTF-8 编码）。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `filename`：笔记文件名（须以 `.md` 结尾）\n\n"
        "响应体（加密后）字段：\n"
        "- `filename`：文件名\n"
        "- `content`：笔记文本内容"
    ),
)
async def read_note(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """读取笔记文件内容"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    filename = body.get("filename", "")
    try:
        content = await note_service.read_note(db, user["id"], user["role"], filename)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"filename": filename, "content": content})


@router.post(
    "/write",
    summary="写入笔记内容",
    description=(
        "新建或覆盖指定笔记文件的内容。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `filename`：笔记文件名（须以 `.md` 结尾）\n"
        "- `content`：笔记文本内容"
    ),
)
async def write_note(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """新建或覆盖笔记文件内容"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    filename = body.get("filename", "")
    content = body.get("content", "")
    try:
        await note_service.write_note(db, user["id"], user["role"], filename, content)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "笔记已保存"})


@router.delete(
    "",
    summary="删除笔记",
    description=(
        "物理删除指定笔记文件。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `filename`：笔记文件名（须以 `.md` 结尾）"
    ),
)
async def delete_note(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """删除笔记文件"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    filename = body.get("filename", "")
    try:
        await note_service.delete_note(db, user["id"], user["role"], filename)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "笔记已删除"})


@router.post(
    "/rename",
    summary="重命名笔记",
    description=(
        "将笔记文件重命名为新文件名（源文件和目标文件均须在笔记目录内）。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `old_name`：原文件名\n"
        "- `new_name`：新文件名（须以 `.md` 结尾）"
    ),
)
async def rename_note(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """重命名笔记文件"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    old_name = body.get("old_name", "")
    new_name = body.get("new_name", "")
    try:
        await note_service.rename_note(db, user["id"], user["role"], old_name, new_name)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "笔记已重命名"})


@router.get(
    "/disk-config",
    summary="获取当前用户的笔记磁盘配置",
    description=(
        "返回当前用户配置的笔记存储磁盘 ID 及可选磁盘列表。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n\n"
        "响应体（加密后）字段：\n"
        "- `disk_id`：当前配置的磁盘 ID（未配置时为 null）\n"
        "- `disks`：可选磁盘列表，每项含 `id`、`name`"
    ),
)
async def get_disk_config(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """获取当前用户的笔记磁盘配置及可用磁盘列表"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disks = await virtual_disk_repository.list_all(db)
    disk_list = [{"id": d["id"], "name": d["name"]} for d in disks]
    full_user = await user_repository.find_by_id(db, user["id"])
    current_disk_id = (
        full_user["notes_disk_id"] if full_user and full_user["notes_disk_id"] else None
    )
    current_path = (full_user["notes_path"] or "") if full_user else ""
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {
        "disk_id": current_disk_id,
        "path": current_path,
        "disks": disk_list,
    })


@router.put(
    "/disk-config",
    summary="设置当前用户的笔记磁盘",
    description=(
        "将当前用户的笔记存储磁盘更新为指定磁盘。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：目标磁盘 ID（传 null 表示清除配置）"
    ),
)
async def set_disk_config(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """更新当前用户的笔记磁盘及子目录配置"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = body.get("disk_id")
    notes_path = (body.get("path") or "").strip("/")
    if disk_id is not None:
        disk = await virtual_disk_repository.find_by_id(db, int(disk_id))
        if not disk:
            raise HTTPException(status_code=404, detail="磁盘不存在")
        # 校验子目录存在性
        if notes_path:
            import os
            full = os.path.normpath(os.path.join(disk["real_path"], notes_path))
            if not full.startswith(os.path.normpath(disk["real_path"])):
                raise HTTPException(status_code=400, detail="非法路径")
            if not os.path.isdir(full):
                raise HTTPException(status_code=400, detail=f"目录不存在: {notes_path}")
    await user_repository.update_notes_config(db, user["id"], disk_id, notes_path)
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "笔记目录配置已更新"})

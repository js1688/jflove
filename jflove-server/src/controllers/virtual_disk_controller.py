"""
虚拟磁盘控制器

处理虚拟磁盘的增删改查操作。
所有接口均需要管理员权限，使用 ChaCha20-Poly1305 加密传输。
"""

import os

from fastapi import APIRouter, Depends, HTTPException, Request
import aiosqlite

from src.models.database import get_db
from src.services import virtual_disk_service, auth_service
from src.services.permission_service import check_disk_permission
from src.repositories import virtual_disk_repository
from src.utils.middleware import decrypt_request_body, encrypt_response

router = APIRouter(prefix="/api/v1/virtual-disks", tags=["虚拟磁盘"])


async def _require_admin(request: Request, db: aiosqlite.Connection) -> tuple:
    """
    校验请求方是否为管理员，并返回用户信息和已解密的请求体。

    :param request: FastAPI 请求对象
    :param db: 数据库连接
    :returns: (用户信息字典, 解密后的请求体字典)
    :raises HTTPException: 令牌无效返回 401，非管理员返回 403
    """
    body = await decrypt_request_body(request)
    token = body.get("token", "")
    try:
        user = await auth_service.get_current_user(db, token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    return user, body


@router.get(
    "",
    summary="获取虚拟磁盘列表",
    description=(
        "获取系统中所有虚拟磁盘的信息。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n\n"
        "响应体（加密后）字段：\n"
        "- `disks`：磁盘列表，每项含 `id`、`name`、`real_path`、`created_by`、`created_at`"
    ),
)
async def list_disks(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """列出所有虚拟磁盘（管理员权限）"""
    _, _ = await _require_admin(request, db)
    disks = await virtual_disk_service.list_disks(db)
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"disks": disks})


@router.post(
    "",
    summary="创建虚拟磁盘",
    description=(
        "注册一个新的虚拟磁盘，服务端指定目录路径须已存在。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n"
        "- `name`：磁盘展示名称\n"
        "- `real_path`：服务端真实目录绝对路径\n\n"
        "响应体（加密后）字段：\n"
        "- `id`：新磁盘主键 ID\n"
        "- `message`：操作结果描述"
    ),
)
async def create_disk(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """创建虚拟磁盘（管理员权限）"""
    user, body = await _require_admin(request, db)
    name = body.get("name", "").strip()
    real_path = body.get("real_path", "").strip()
    if not name or not real_path:
        raise HTTPException(status_code=400, detail="name 和 real_path 不能为空")
    try:
        disk_id = await virtual_disk_service.create_disk(db, name, real_path, user["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"id": disk_id, "message": "虚拟磁盘添加成功"})


@router.put(
    "/{disk_id}",
    summary="更新虚拟磁盘",
    description=(
        "修改虚拟磁盘的展示名称和服务端路径，新路径须已存在。\n\n"
        "路径参数：\n"
        "- `disk_id`：目标磁盘 ID\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n"
        "- `name`：新展示名称\n"
        "- `real_path`：新服务端真实路径"
    ),
)
async def update_disk(
    disk_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """更新虚拟磁盘信息（管理员权限）"""
    _, body = await _require_admin(request, db)
    name = body.get("name", "").strip()
    real_path = body.get("real_path", "").strip()
    if not name or not real_path:
        raise HTTPException(status_code=400, detail="name 和 real_path 不能为空")
    try:
        await virtual_disk_service.update_disk(db, disk_id, name, real_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "虚拟磁盘已更新"})


@router.delete(
    "/{disk_id}",
    summary="删除虚拟磁盘",
    description=(
        "软删除虚拟磁盘记录，不删除服务端真实文件。\n\n"
        "路径参数：\n"
        "- `disk_id`：目标磁盘 ID\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌"
    ),
)
async def delete_disk(
    disk_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """软删除虚拟磁盘（管理员权限）"""
    _, _ = await _require_admin(request, db)
    try:
        await virtual_disk_service.delete_disk(db, disk_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "虚拟磁盘已删除"})


@router.get(
    "/{disk_id}/browse",
    summary="浏览磁盘内目录",
    description=(
        "列出指定虚拟磁盘内某路径下的所有子目录，用于笔记目录选择。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `path`：相对路径（空字符串表示根目录）\n\n"
        "响应体（加密后）字段：\n"
        "- `dirs`：子目录列表，每项含 `name`、`path`（相对磁盘根的路径）"
    ),
)
async def browse_disk_dirs(
    disk_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """列出磁盘内子目录（仅对该磁盘有读权限的用户可调用）"""
    body = await decrypt_request_body(request)
    token = body.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="缺少认证令牌")
    try:
        user = await auth_service.get_current_user(db, token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    disk = await virtual_disk_repository.find_by_id(db, disk_id)
    if not disk:
        raise HTTPException(status_code=404, detail="磁盘不存在")

    # 防权限绕过：换 disk_id 时必须重新校验该磁盘的读权限（管理员豁免）
    if user["role"] != "admin":
        if not await check_disk_permission(db, user["id"], disk_id, "read"):
            raise HTTPException(status_code=403, detail="无权浏览该磁盘")

    rel_path = (body.get("path") or "").strip("/")
    base = os.path.normpath(disk["real_path"])
    if rel_path:
        target = os.path.normpath(os.path.join(base, rel_path))
        if not target.startswith(base):
            raise HTTPException(status_code=400, detail="非法路径")
    else:
        target = base

    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail="路径不存在")

    dirs = []
    try:
        for entry in sorted(os.scandir(target), key=lambda e: e.name):
            if entry.is_dir() and not entry.name.startswith("."):
                sub_rel = os.path.relpath(entry.path, base).replace("\\", "/")
                dirs.append({"name": entry.name, "path": sub_rel})
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权限访问该目录")

    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"dirs": dirs, "current_path": rel_path})

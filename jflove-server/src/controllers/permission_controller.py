"""
权限管理控制器

处理用户对虚拟磁盘和笔记目录的权限配置操作。
所有接口均需要管理员权限，使用 ChaCha20-Poly1305 加密传输。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
import aiosqlite

from src.models.database import get_db
from src.services import permission_service, auth_service
from src.utils.middleware import decrypt_request_body, encrypt_response

router = APIRouter(prefix="/api/v1/permissions", tags=["权限管理"])


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
    "/users/{user_id}/disks",
    summary="获取用户磁盘权限列表",
    description=(
        "查询指定用户对所有虚拟磁盘的权限配置。\n\n"
        "路径参数：\n"
        "- `user_id`：目标用户 ID\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n\n"
        "响应体（加密后）字段：\n"
        "- `permissions`：权限列表，每项含 `virtual_disk_id`、`can_read`、`can_write`、`can_delete`"
    ),
)
async def get_disk_permissions(
    user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """获取用户对所有磁盘的权限配置（管理员权限）"""
    _, _ = await _require_admin(request, db)
    perms = await permission_service.get_user_disk_permissions(db, user_id)
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"permissions": perms})


@router.post(
    "/users/{user_id}/disks/{disk_id}",
    summary="设置用户磁盘权限",
    description=(
        "为指定用户配置对某个虚拟磁盘的读/写/删除权限（已有配置则覆盖）。\n\n"
        "路径参数：\n"
        "- `user_id`：目标用户 ID\n"
        "- `disk_id`：目标虚拟磁盘 ID\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n"
        "- `can_read`：是否允许读取（布尔值）\n"
        "- `can_write`：是否允许写入（布尔值）\n"
        "- `can_delete`：是否允许删除（布尔值）"
    ),
)
async def set_disk_permission(
    user_id: int,
    disk_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """设置用户对指定磁盘的权限（管理员权限）"""
    _, body = await _require_admin(request, db)
    can_read = bool(body.get("can_read", False))
    can_write = bool(body.get("can_write", False))
    can_delete = bool(body.get("can_delete", False))
    try:
        await permission_service.set_user_disk_permission(
            db, user_id, disk_id, can_read, can_write, can_delete
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "权限配置已更新"})


@router.delete(
    "/users/{user_id}/disks/{disk_id}",
    summary="删除用户磁盘权限",
    description=(
        "删除指定用户对某个虚拟磁盘的权限配置，操作后用户将失去该磁盘的所有权限。\n\n"
        "路径参数：\n"
        "- `user_id`：目标用户 ID\n"
        "- `disk_id`：目标虚拟磁盘 ID\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌"
    ),
)
async def delete_disk_permission(
    user_id: int,
    disk_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """删除用户对指定磁盘的权限配置（管理员权限）"""
    _, _ = await _require_admin(request, db)
    await permission_service.delete_user_disk_permission(db, user_id, disk_id)
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "权限已删除"})


# 注：v1.x 移除"设置笔记权限"接口（POST /users/{user_id}/notes）。
# 所有登录用户均可使用笔记功能；笔记目录由 users.notes_disk_id / notes_path
# 字段独立配置，互不可见。如需限制某用户使用笔记，可通过禁用该账号实现。

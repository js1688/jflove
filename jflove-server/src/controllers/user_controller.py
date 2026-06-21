"""
用户管理控制器

处理普通用户的创建、密码修改、启用/禁用、删除操作。
所有接口均需要管理员权限，使用 ChaCha20-Poly1305 加密传输。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
import aiosqlite

from src.models.database import get_db
from src.services import user_service, auth_service
from src.utils.middleware import decrypt_request_body, encrypt_response

router = APIRouter(prefix="/api/v1/users", tags=["用户管理"])


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
    summary="获取用户列表",
    description=(
        "获取系统中所有用户的信息（不含密码哈希）。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n\n"
        "响应体（加密后）字段：\n"
        "- `users`：用户列表，每项含 `id`、`username`、`role`、`enabled`、`created_at`"
    ),
)
async def list_users(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """列出所有用户（管理员权限）"""
    _, _ = await _require_admin(request, db)
    users = await user_service.list_users(db)
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"users": users})


@router.post(
    "",
    summary="创建普通用户",
    description=(
        "创建一个新的普通用户账号，密码使用 bcrypt 哈希存储。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n"
        "- `username`：用户名（系统唯一）\n"
        "- `password`：初始密码\n\n"
        "响应体（加密后）字段：\n"
        "- `id`：新用户的主键 ID\n"
        "- `message`：操作结果描述"
    ),
)
async def create_user(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """创建普通用户（管理员权限）"""
    _, body = await _require_admin(request, db)
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    try:
        user_id = await user_service.create_user(db, username, password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"id": user_id, "message": "用户添加成功"})


@router.delete(
    "/{user_id}",
    summary="删除用户",
    description=(
        "软删除指定用户账号（管理员账号不可删除）。\n\n"
        "路径参数：\n"
        "- `user_id`：目标用户 ID\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌"
    ),
)
async def delete_user(
    user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """软删除用户（管理员权限，不可删除管理员）"""
    _, _ = await _require_admin(request, db)
    try:
        await user_service.delete_user(db, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "用户已删除"})


@router.put(
    "/{user_id}/password",
    summary="修改用户密码",
    description=(
        "重置指定用户的密码，新密码使用 bcrypt 重新哈希存储。\n\n"
        "路径参数：\n"
        "- `user_id`：目标用户 ID\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n"
        "- `password`：新密码"
    ),
)
async def update_password(
    user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """修改用户密码（管理员权限）"""
    _, body = await _require_admin(request, db)
    new_password = body.get("password", "")
    if not new_password:
        raise HTTPException(status_code=400, detail="密码不能为空")
    try:
        await user_service.update_password(db, user_id, new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "密码已更新"})


@router.put(
    "/{user_id}/enabled",
    summary="启用或禁用用户",
    description=(
        "切换指定用户账号的启用状态（管理员账号不可操作）。\n\n"
        "路径参数：\n"
        "- `user_id`：目标用户 ID\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n"
        "- `enabled`：`true` 启用，`false` 禁用"
    ),
)
async def set_enabled(
    user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """启用或禁用用户（管理员权限，不可操作管理员）"""
    _, body = await _require_admin(request, db)
    enabled = body.get("enabled", True)
    try:
        await user_service.set_enabled(db, user_id, bool(enabled))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "状态已更新"})

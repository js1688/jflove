"""
系统配置控制器

处理系统级配置项的读取和更新操作（如 notes_disk_id 等）。
所有接口均需要管理员权限，使用 ChaCha20-Poly1305 加密传输。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
import aiosqlite

from src.models.database import get_db
from src.services import config_service, auth_service
from src.utils.middleware import decrypt_request_body, encrypt_response

router = APIRouter(prefix="/api/v1/config", tags=["系统配置"])


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
    summary="获取所有系统配置",
    description=(
        "获取系统中所有配置项的键值对列表。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n\n"
        "响应体（加密后）字段：\n"
        "- `config`：配置项列表，每项含 `key`、`value`"
    ),
)
async def get_config(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """获取全部系统配置项（管理员权限）"""
    _, _ = await _require_admin(request, db)
    config = await config_service.get_all(db)
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"config": config})


@router.put(
    "",
    summary="更新系统配置项",
    description=(
        "新增或更新指定配置项的值（键不存在时自动创建）。\n\n"
        "常用配置键：\n"
        "- `notes_disk_id`：笔记目录对应的虚拟磁盘 ID\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：管理员 JWT 令牌\n"
        "- `key`：配置项键名\n"
        "- `value`：配置项值"
    ),
)
async def update_config(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """更新系统配置项（管理员权限）"""
    _, body = await _require_admin(request, db)
    key = body.get("key", "").strip()
    value = body.get("value", "")
    if not key:
        raise HTTPException(status_code=400, detail="key 不能为空")
    await config_service.update(db, key, value)
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "配置已更新"})

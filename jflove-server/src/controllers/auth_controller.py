"""
认证控制器

处理密钥交换、管理员初始化、登录、令牌刷新等认证相关接口。
密钥交换接口为唯一明文接口，其余接口均使用 ChaCha20-Poly1305 加密传输。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import aiosqlite

from src.models.database import get_db
from src.services import auth_service
from src.utils.middleware import decrypt_request_body, encrypt_response
from src.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])
logger = get_logger(__name__)


class KeyExchangeRequest(BaseModel):
    """密钥交换请求体"""
    client_public_key: str   # 客户端 X25519 公钥（Base64 编码）
    refresh: bool = False    # 是否为密钥刷新（传 true 时须携带旧 X-Session-ID）


@router.post(
    "/key-exchange",
    summary="密钥交换",
    description=(
        "客户端与服务端进行 X25519 ECDH 密钥交换，派生 ChaCha20-Poly1305 会话密钥。\n\n"
        "- 首次交换：不携带 `X-Session-ID`，`refresh` 传 `false`。\n"
        "- 密钥刷新：携带旧 `X-Session-ID`，`refresh` 传 `true`。\n"
        "- **此接口为唯一明文接口，请求/响应均不加密。**"
    ),
)
async def key_exchange(body: KeyExchangeRequest, request: Request):
    """密钥交换（唯一不加密的接口）"""
    old_session_id = request.headers.get("X-Session-ID")
    if body.refresh and old_session_id:
        result = await auth_service.refresh_key_exchange(
            old_session_id, body.client_public_key
        )
    else:
        result = await auth_service.key_exchange(body.client_public_key)
    return JSONResponse(content=result)


@router.post(
    "/init-admin",
    summary="初始化管理员账号",
    description=(
        "系统首次使用时创建管理员账号。若管理员已存在则返回 400。\n\n"
        "请求体（加密后）字段：\n"
        "- `username`：管理员用户名\n"
        "- `password`：管理员密码"
    ),
)
async def init_admin(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """初始化管理员账号，系统中仅允许创建一次"""
    body = await decrypt_request_body(request)
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    try:
        await auth_service.init_admin(db, username, password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "管理员账号创建成功"})


@router.get(
    "/admin-exists",
    summary="检查管理员是否已存在",
    description=(
        "客户端启动时调用，判断是否需要引导用户完成初始化流程。\n\n"
        "**此接口为明文接口，不含敏感信息。**"
    ),
)
async def admin_exists(db: aiosqlite.Connection = Depends(get_db)):
    """客户端启动时检查是否存在管理员（明文，不含敏感信息）"""
    exists = await auth_service.check_admin_exists(db)
    return JSONResponse(content={"exists": exists})


@router.post(
    "/login",
    summary="用户登录",
    description=(
        "使用用户名和密码进行登录，验证通过后返回 JWT 令牌。\n\n"
        "请求体（加密后）字段：\n"
        "- `username`：用户名\n"
        "- `password`：密码\n"
        "- `requested_ttl_seconds`（v1.1.2 新增，可选）：客户端请求的 JWT 有效期（秒）。"
        "服务端会 clamp 到 [JWT_EXPIRE_MIN_SECONDS, JWT_EXPIRE_MAX_SECONDS] 范围内；"
        "缺失或非法值则使用默认值 JWT_EXPIRE_DEFAULT_SECONDS（1 小时）。\n\n"
        "响应体（加密后）字段：\n"
        "- `token`：JWT 访问令牌\n"
        "- `expires_in`：本次签发的实际有效期（秒，clamp 后的真实值）"
    ),
)
async def login(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """登录并颁发 JWT 令牌"""
    body = await decrypt_request_body(request)
    username = body.get("username", "").strip()
    password = body.get("password", "")
    session_id = request.headers.get("X-Session-ID", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    # v1.1.2：可选的客户端请求 TTL（秒）。容错策略：非法/缺失一律视为 None，
    # 由 service 层走默认值；不向客户端报错，避免被误用导致登录失败。
    raw_ttl = body.get("requested_ttl_seconds")
    requested_ttl: int | None = None
    if raw_ttl is not None:
        try:
            requested_ttl = int(raw_ttl)
        except (TypeError, ValueError):
            requested_ttl = None
    try:
        result = await auth_service.login(
            db, username, password, session_id, requested_ttl,
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return encrypt_response(session_id, result)


@router.post(
    "/refresh",
    summary="刷新 JWT 令牌",
    description=(
        "使用有效的旧令牌换取新令牌，延长登录有效期。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：当前有效的 JWT 令牌\n\n"
        "响应体（加密后）字段：\n"
        "- `token`：新 JWT 令牌\n"
        "- `expires_in`：新令牌有效期（秒）"
    ),
)
async def refresh_token(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """刷新 JWT 令牌，续期登录状态"""
    body = await decrypt_request_body(request)
    token = body.get("token", "")
    session_id = request.headers.get("X-Session-ID", "")
    if not token:
        raise HTTPException(status_code=400, detail="缺少令牌")
    try:
        result = await auth_service.refresh_token(db, token, session_id)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return encrypt_response(session_id, result)

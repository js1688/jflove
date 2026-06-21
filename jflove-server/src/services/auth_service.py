"""
认证业务逻辑层

处理密钥交换、用户登录、JWT 令牌签发与刷新。

会话密钥安全说明：
  - 会话密钥（session_key）仅存于内存字典，不落库。
  - 服务重启后所有客户端须重新执行密钥交换。
  - 私钥（X25519）用完即丢，不持久化。
"""

import uuid
import hashlib
from datetime import datetime, timezone, timedelta

import bcrypt
import aiosqlite

from src.config.settings import SESSION_EXPIRE_HOURS
from src.repositories import user_repository, session_repository
from src.utils.crypto import generate_x25519_keypair, derive_session_key
from src.utils.jwt_utils import create_token, verify_token
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 内存会话存储：session_id -> session_key（32 字节）
_session_store: dict[str, bytes] = {}


async def check_admin_exists(db: aiosqlite.Connection) -> bool:
    """
    检查系统中是否已存在管理员账号。

    :param db: 数据库连接
    :returns: True 表示已有管理员，False 表示尚未初始化
    """
    return await user_repository.count_admins(db) > 0


async def init_admin(
    db: aiosqlite.Connection, username: str, password: str
) -> None:
    """
    初始化管理员账号（仅允许创建一次）。

    :param db: 数据库连接
    :param username: 管理员用户名
    :param password: 明文密码，内部使用 bcrypt 哈希存储
    :raises ValueError: 管理员账号已存在
    """
    if await check_admin_exists(db):
        raise ValueError("管理员账号已存在")
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    await user_repository.create(db, username, password_hash, role="admin")
    logger.info("管理员账号已创建: %s", username)


async def key_exchange(client_public_key: str) -> dict:
    """
    执行 X25519 密钥交换，派生会话密钥并存入内存。

    流程：
      1. 服务端生成临时 X25519 公私钥对
      2. 通过 ECDH 计算共享密钥
      3. 经 HKDF-SHA256 派生 32 字节会话密钥
      4. 生成 session_id，将 session_id -> session_key 存入内存
      5. 返回服务端公钥和 session_id

    :param client_public_key: 客户端 X25519 公钥（Base64 编码）
    :returns: {"session_id": str, "server_public_key": str}
    """
    private_key, server_public_key = generate_x25519_keypair()
    session_id = str(uuid.uuid4())
    session_key = derive_session_key(private_key, client_public_key)
    # 私钥使用完毕，存储会话密钥后私钥对象自然销毁
    _session_store[session_id] = session_key
    logger.info("密钥交换完成，session_id=%s", session_id)
    return {"session_id": session_id, "server_public_key": server_public_key}


async def refresh_key_exchange(
    old_session_id: str, client_public_key: str
) -> dict:
    """
    刷新会话密钥：废弃旧会话密钥，重新执行密钥交换。

    :param old_session_id: 旧会话 ID
    :param client_public_key: 客户端新的 X25519 公钥（Base64 编码）
    :returns: {"session_id": str, "server_public_key": str}
    """
    if old_session_id in _session_store:
        del _session_store[old_session_id]
    return await key_exchange(client_public_key)


def get_session_key(session_id: str) -> bytes | None:
    """
    从内存中获取会话密钥。

    :param session_id: 会话 UUID
    :returns: 32 字节会话密钥，不存在则返回 None
    """
    return _session_store.get(session_id)


def remove_session_key(session_id: str) -> None:
    """
    从内存中移除会话密钥（用于登出或强制失效）。

    :param session_id: 会话 UUID
    """
    _session_store.pop(session_id, None)


async def login(
    db: aiosqlite.Connection,
    username: str,
    password: str,
    session_id: str,
    requested_ttl_seconds: int | None = None,
) -> dict:
    """
    用户登录：验证用户名密码，签发 JWT 令牌，记录会话。

    v1.1.2：新增 requested_ttl_seconds，让客户端可以请求自定义 TTL；
    实际生效值由 create_token 内部 clamp 到 [JWT_EXPIRE_MIN, JWT_EXPIRE_MAX]。

    :param db: 数据库连接
    :param username: 用户名
    :param password: 明文密码
    :param session_id: 当前密钥交换会话 ID，用于关联会话记录
    :param requested_ttl_seconds: 客户端请求的 JWT 有效期秒数；None 表示走默认值
    :returns: {"token": str, "role": str, "username": str, "user_id": int, "expires_in": int}
    :raises ValueError: 用户名或密码错误、账号被禁用
    """
    user = await user_repository.find_by_username(db, username)
    if not user:
        raise ValueError("用户名或密码错误")
    if not user["enabled"]:
        raise ValueError("账号已被禁用")
    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        raise ValueError("用户名或密码错误")

    token, effective_ttl = create_token(
        user["id"], user["username"], user["role"],
        ttl_seconds=requested_ttl_seconds,
    )
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    # sessions 表的 expires_at 仍按"会话过期"语义（24h），与 JWT 过期是两回事；保持不动
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=SESSION_EXPIRE_HOURS)
    ).isoformat()

    await session_repository.revoke(db, session_id)
    await session_repository.create(
        db, user["id"], session_id, token_hash, expires_at
    )
    logger.info("用户登录成功: %s（TTL=%ds）", username, effective_ttl)
    return {
        "token": token,
        "role": user["role"],
        "username": user["username"],
        "user_id": user["id"],
        "expires_in": effective_ttl,
    }


async def refresh_token(db: aiosqlite.Connection, token: str, session_id: str) -> dict:
    """
    刷新 JWT 令牌：验证旧令牌后签发新令牌，旧会话记录同时吊销。

    :param db: 数据库连接
    :param token: 待刷新的旧 JWT 令牌
    :param session_id: 当前会话 ID
    :returns: {"token": str}（新令牌）
    :raises ValueError: 令牌无效或用户不存在
    """
    try:
        payload = verify_token(token)
    except Exception:
        raise ValueError("令牌无效或已过期")

    user_id = int(payload["sub"])
    user = await user_repository.find_by_id(db, user_id)
    if not user or not user["enabled"]:
        raise ValueError("用户不存在或已被禁用")

    await session_repository.revoke(db, session_id)
    # refresh 仍走默认 TTL（本期不做"按用户偏好刷新"，避免扩大改动范围）
    new_token, _ = create_token(user["id"], user["username"], user["role"])
    token_hash = hashlib.sha256(new_token.encode()).hexdigest()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=SESSION_EXPIRE_HOURS)
    ).isoformat()
    await session_repository.create(db, user["id"], session_id, token_hash, expires_at)
    return {"token": new_token}


async def get_current_user(db: aiosqlite.Connection, token: str) -> dict:
    """
    从 JWT 令牌中解析并验证当前用户信息。

    :param db: 数据库连接
    :param token: JWT 令牌字符串
    :returns: {"id": int, "username": str, "role": str}
    :raises ValueError: 令牌无效或用户不存在/已禁用
    """
    try:
        payload = verify_token(token)
    except Exception:
        raise ValueError("令牌无效或已过期")
    user_id = int(payload["sub"])
    user = await user_repository.find_by_id(db, user_id)
    if not user or not user["enabled"]:
        raise ValueError("用户不存在或已被禁用")
    return {"id": user["id"], "username": user["username"], "role": user["role"]}

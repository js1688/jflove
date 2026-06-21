"""
JWT 工具模块

服务启动时动态生成 EC P-256 密钥对，使用 ES256 算法签发和验证 JWT 令牌。
注意：密钥对仅存于内存，服务重启后所有已签发令牌失效。
"""

import jwt
from datetime import datetime, timezone, timedelta
from cryptography.hazmat.primitives.asymmetric.ec import (
    generate_private_key, SECP256R1
)
from cryptography.hazmat.primitives import serialization
from src.config.settings import (
    JWT_ALGORITHM,
    JWT_EXPIRE_DEFAULT_SECONDS,
    JWT_EXPIRE_MAX_SECONDS,
    JWT_EXPIRE_MIN_SECONDS,
)

# 服务启动时生成一次性 EC 密钥对（内存存储）
_ec_private_key = generate_private_key(SECP256R1())
_ec_public_key = _ec_private_key.public_key()

_PRIVATE_PEM = _ec_private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_PUBLIC_PEM = _ec_public_key.public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
)


def create_token(
    user_id: int,
    username: str,
    role: str,
    ttl_seconds: int | None = None,
) -> tuple[str, int]:
    """
    签发 JWT 令牌（v1.1.2：支持自定义 TTL，返回实际 TTL）。

    :param user_id: 用户 ID，写入 sub 字段
    :param username: 用户名，写入 username 字段
    :param role: 用户角色（admin / user），写入 role 字段
    :param ttl_seconds: 客户端请求的有效期秒数：
                        - None：使用默认值 JWT_EXPIRE_DEFAULT_SECONDS（3600）
                        - 给定：clamp 到 [JWT_EXPIRE_MIN_SECONDS, JWT_EXPIRE_MAX_SECONDS]
    :returns: (token, 实际使用的 TTL 秒数)。
              返回实际 TTL 让调用方（auth_service）能用同一个值算 sessions 表的 expires_at，
              避免两边各自算导致毫秒级偏差。
    """
    if ttl_seconds is None:
        effective = JWT_EXPIRE_DEFAULT_SECONDS
    else:
        effective = max(
            JWT_EXPIRE_MIN_SECONDS,
            min(int(ttl_seconds), JWT_EXPIRE_MAX_SECONDS),
        )
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=effective),
    }
    token = jwt.encode(payload, _PRIVATE_PEM, algorithm=JWT_ALGORITHM)
    return token, effective


def verify_token(token: str) -> dict:
    """
    验证并解码 JWT 令牌。

    :param token: JWT 字符串
    :returns: 解码后的 payload 字典
    :raises: jwt.ExpiredSignatureError 令牌过期
    :raises: jwt.InvalidTokenError 令牌无效
    """
    return jwt.decode(token, _PUBLIC_PEM, algorithms=[JWT_ALGORITHM])

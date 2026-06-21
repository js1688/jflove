"""
请求/响应加密中间件

提供统一的请求体解密和响应体加密能力。
除 /api/v1/auth/key-exchange 和 /health 外，所有接口均须通过此模块处理。
"""

import json
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from src.services.auth_service import get_session_key
from src.utils.crypto import decrypt, encrypt


async def decrypt_request_body(request: Request) -> dict:
    """
    从加密请求体中解密出原始 JSON 字典。

    请求头须携带 X-Session-ID，Body 格式为：
    {"nonce": "<Base64>", "ciphertext": "<Base64>"}

    :param request: FastAPI Request 对象
    :returns: 解密后的原始请求字典
    :raises HTTPException 401: 缺少 Session ID 或会话已失效
    :raises HTTPException 400: 解密失败（密钥不匹配或数据损坏）
    """
    session_id = request.headers.get("X-Session-ID")
    if not session_id:
        raise HTTPException(status_code=401, detail="缺少 X-Session-ID 请求头")
    session_key = get_session_key(session_id)
    if not session_key:
        raise HTTPException(status_code=401, detail="会话不存在或已过期，请重新交换密钥")

    raw = await request.body()
    if not raw:
        return {}
    try:
        envelope = json.loads(raw)
        plaintext = decrypt(session_key, envelope["nonce"], envelope["ciphertext"])
        return json.loads(plaintext)
    except Exception:
        raise HTTPException(status_code=400, detail="请求解密失败")


def encrypt_response(session_id: str, data: dict) -> JSONResponse:
    """
    将响应数据加密后返回给客户端。

    响应格式：{"nonce": "<Base64>", "ciphertext": "<Base64>"}

    :param session_id: 当前会话 ID，用于获取会话密钥
    :param data: 待加密的原始响应字典
    :returns: 加密后的 JSONResponse
    :raises HTTPException 401: 会话已失效
    """
    session_key = get_session_key(session_id)
    if not session_key:
        raise HTTPException(status_code=401, detail="会话已失效")
    plaintext = json.dumps(data, ensure_ascii=False).encode()
    return JSONResponse(content=encrypt(session_key, plaintext))

"""
客户端加密工具模块

与后端保持一致的加密实现：
  - X25519 ECDH 临时密钥对生成
  - HKDF-SHA256 会话密钥派生（盐值 b"jflove-v1"）
  - ChaCha20-Poly1305 对称加解密（12 字节 nonce）

包括两套对称加密 API：
  - 整包 encrypt / decrypt：用于普通 JSON 请求/响应
  - 流式分帧 decrypt_stream_chunk + read_exact_stream：用于文件下载/预览
    的响应体边收边解密
"""

import os
import struct
import base64
import json

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from src.config.settings import SESSION_KEY_SALT

# 安全上限：单帧明文不超过 2 MB（防内存炸弹）。服务端默认 64KB 帧，留足余量
_MAX_FRAME_BODY_SIZE = 2 * 1024 * 1024


def generate_x25519_keypair() -> tuple:
    """
    生成 X25519 临时密钥对。

    :returns: (私钥对象 X25519PrivateKey, 公钥 Base64 字符串)
    """
    private_key = X25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return private_key, base64.b64encode(pub_bytes).decode()


def derive_session_key(private_key: X25519PrivateKey, peer_public_key_b64: str) -> bytes:
    """
    通过 ECDH 计算共享密钥，再经 HKDF-SHA256 派生 32 字节会话密钥。

    :param private_key: 本端 X25519 私钥对象
    :param peer_public_key_b64: 对端公钥 Base64 字符串
    :returns: 32 字节会话密钥
    """
    peer_bytes = base64.b64decode(peer_public_key_b64)
    peer_pub = X25519PublicKey.from_public_bytes(peer_bytes)
    shared_secret = private_key.exchange(peer_pub)

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SESSION_KEY_SALT,
        info=b"",
    )
    return hkdf.derive(shared_secret)


def encrypt(session_key: bytes, data: dict) -> dict:
    """
    将 dict 序列化后用 ChaCha20-Poly1305 加密。

    每次随机生成 12 字节 nonce，保证密文唯一性。

    :param session_key: 32 字节会话密钥
    :param data: 待加密的原始字典
    :returns: {"nonce": "<Base64>", "ciphertext": "<Base64>"}
    """
    plaintext = json.dumps(data).encode()
    nonce = os.urandom(12)
    chacha = ChaCha20Poly1305(session_key)
    ciphertext = chacha.encrypt(nonce, plaintext, None)
    return {
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }


def decrypt(session_key: bytes, nonce_b64: str, ciphertext_b64: str) -> dict:
    """
    ChaCha20-Poly1305 解密并反序列化为 dict。

    :param session_key: 32 字节会话密钥
    :param nonce_b64: nonce Base64 字符串
    :param ciphertext_b64: 密文 Base64 字符串（含认证标签）
    :returns: 解密后的原始字典
    :raises cryptography.exceptions.InvalidTag: 认证标签校验失败
    """
    nonce = base64.b64decode(nonce_b64)
    ciphertext = base64.b64decode(ciphertext_b64)
    chacha = ChaCha20Poly1305(session_key)
    plaintext = chacha.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext)


def decrypt_stream_chunk(session_key: bytes, frame_body: bytes) -> bytes:
    """
    流式分帧解密：把单帧帧体（不含 4 字节长度前缀）还原为明文字节。

    帧体布局：[12B nonce][N B 密文 + 16B 认证标签]

    :param session_key: 32 字节会话密钥
    :param frame_body: 已读取的帧体（不含 4B 长度前缀）
    :returns: 明文字节
    :raises cryptography.exceptions.InvalidTag: 认证失败（含被改/被截断）
    :raises ValueError: 帧体长度不合法
    """
    if len(frame_body) < 12 + 16:
        raise ValueError("加密帧体长度不足")
    nonce = frame_body[:12]
    ciphertext = frame_body[12:]
    chacha = ChaCha20Poly1305(session_key)
    return chacha.decrypt(nonce, ciphertext, None)


def read_exact_stream(raw, n: int) -> bytes:
    """
    从原始 IO 流中精确读取 n 字节，处理短读情况。

    :param raw: 任何 .read(int) -> bytes 的对象（如 requests resp.raw）
    :param n: 期望读取的字节数
    :returns: 实际读到的字节（可能小于 n，遇 EOF 时短返回）
    """
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = raw.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def parse_stream_frame(raw, session_key: bytes) -> bytes | None:
    """
    从原始流读取一个完整加密帧并解密。

    :param raw: 原始 IO 对象（如 requests resp.raw）
    :param session_key: 会话密钥
    :returns: 解密后的明文字节；若流已结束（无更多帧）返回 None
    :raises ValueError: 帧格式不合法（如长度异常、被截断、认证失败）
    """
    len_bytes = read_exact_stream(raw, 4)
    if not len_bytes:
        return None  # 正常 EOF
    if len(len_bytes) < 4:
        raise ValueError("加密流被截断（长度前缀不完整）")
    frame_len = struct.unpack(">I", len_bytes)[0]
    if frame_len > _MAX_FRAME_BODY_SIZE or frame_len < 12 + 16:
        raise ValueError(f"加密帧大小非法：{frame_len}")
    frame_body = read_exact_stream(raw, frame_len)
    if len(frame_body) < frame_len:
        raise ValueError("加密流被截断（帧体不完整）")
    return decrypt_stream_chunk(session_key, frame_body)

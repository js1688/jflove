"""
加密工具模块

提供 X25519 密钥对生成、ECDH+HKDF 会话密钥派生、
ChaCha20-Poly1305 对称加解密能力。

包括两套对称加密 API：
  - 整包加密（encrypt / decrypt）：用于普通 JSON 请求/响应
  - 流式分帧加密（encrypt_stream_chunk）：用于文件下载/预览的响应体，
    每片独立 nonce，配合客户端 decrypt_stream_chunk 实现端到端加密文件流
"""

import os
import struct
import base64
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from src.config.settings import SESSION_KEY_SALT

# 流式分帧大小（明文字节数）；密文帧 = 4B 长度 + 12B nonce + plaintext + 16B tag
STREAM_PLAINTEXT_CHUNK_SIZE = 64 * 1024


def generate_x25519_keypair() -> tuple[X25519PrivateKey, str]:
    """
    生成 X25519 临时密钥对。

    :returns: (私钥对象, 公钥 Base64 字符串)
    """
    private_key = X25519PrivateKey.generate()
    public_key_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return private_key, base64.b64encode(public_key_bytes).decode()


def derive_session_key(private_key: X25519PrivateKey, peer_public_key_b64: str) -> bytes:
    """
    通过 ECDH 计算共享密钥，再经 HKDF-SHA256 派生 32 字节会话密钥。

    :param private_key: 本端 X25519 私钥对象
    :param peer_public_key_b64: 对端公钥 Base64 字符串
    :returns: 32 字节会话密钥
    """
    peer_pub_bytes = base64.b64decode(peer_public_key_b64)
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    peer_pub = X25519PublicKey.from_public_bytes(peer_pub_bytes)
    shared_secret = private_key.exchange(peer_pub)

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SESSION_KEY_SALT,
        info=b"",
    )
    return hkdf.derive(shared_secret)


def encrypt(session_key: bytes, plaintext: bytes) -> dict:
    """
    使用 ChaCha20-Poly1305 加密明文。

    每次加密随机生成 12 字节 nonce，保证密文唯一性。

    :param session_key: 32 字节会话密钥
    :param plaintext: 待加密的原始字节
    :returns: {"nonce": "<Base64>", "ciphertext": "<Base64>"}
    """
    nonce = os.urandom(12)
    chacha = ChaCha20Poly1305(session_key)
    ciphertext = chacha.encrypt(nonce, plaintext, None)
    return {
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }


def decrypt(session_key: bytes, nonce_b64: str, ciphertext_b64: str) -> bytes:
    """
    使用 ChaCha20-Poly1305 解密密文，同时验证认证标签。

    :param session_key: 32 字节会话密钥
    :param nonce_b64: nonce Base64 字符串
    :param ciphertext_b64: 密文 Base64 字符串（含认证标签）
    :returns: 解密后的原始字节
    :raises: cryptography.exceptions.InvalidTag 若认证失败
    """
    nonce = base64.b64decode(nonce_b64)
    ciphertext = base64.b64decode(ciphertext_b64)
    chacha = ChaCha20Poly1305(session_key)
    return chacha.decrypt(nonce, ciphertext, None)


def encrypt_stream_chunk(session_key: bytes, plaintext: bytes) -> bytes:
    """
    流式分帧加密：把一片明文加密为一帧二进制，可直接写入 HTTP 流式响应。

    帧格式（大端字节序）：
        [4B 帧体长度 L] [12B nonce] [N B 密文 + 16B 认证标签]
    其中 L = 12 + N + 16 = 28 + 明文字节数

    每帧使用独立随机 nonce，ChaCha20-Poly1305 自带认证标签可防篡改与截断。

    :param session_key: 32 字节会话密钥（与请求/响应整包加密共用）
    :param plaintext: 明文字节（建议每帧 STREAM_PLAINTEXT_CHUNK_SIZE）
    :returns: 完整帧字节流，可直接 yield 给 StreamingResponse
    """
    nonce = os.urandom(12)
    chacha = ChaCha20Poly1305(session_key)
    ciphertext = chacha.encrypt(nonce, plaintext, None)
    body = nonce + ciphertext  # 12 + len(plaintext) + 16
    return struct.pack(">I", len(body)) + body

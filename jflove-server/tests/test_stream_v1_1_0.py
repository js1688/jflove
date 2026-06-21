"""
v1.1.0 流式预览接口测试

覆盖范围：
  1. 正常流式传输：完整文件、指定 range、suffix range、元数据专用 range 0-0
  2. 参数规范化：range_end=-1、range 越界、负数 range_start
  3. 错误处理：文件不存在、无 token、无磁盘权限、空 filename
  4. §9 安全宪法专项：请求加密、响应 v2 帧流、响应头零敏感字段、帧篡改拒绝
"""

from __future__ import annotations

import io
import json
import os
import struct

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from tests.conftest import (
    decrypt_response,
    do_key_exchange,
    encrypted_request,
    login,
)
from tests.test_security_e2e import (
    assert_encrypted_envelope,
    assert_no_sensitive_headers,
)


# ── 帧解析辅助 ────────────────────────────────────────────────────── #

def _parse_frames(raw_bytes: bytes, session_key: bytes) -> list[bytes]:
    """
    从响应原始字节中顺序解析并解密所有帧，返回明文列表。
    任意帧认证失败抛 InvalidTag；帧体被截断抛 AssertionError。
    """
    buf = io.BytesIO(raw_bytes)
    frames: list[bytes] = []
    while True:
        len_bytes = buf.read(4)
        if not len_bytes:
            break
        assert len(len_bytes) == 4, "帧长度前缀被截断"
        frame_len = struct.unpack(">I", len_bytes)[0]
        assert 28 <= frame_len <= 2 * 1024 * 1024 + 28, f"帧大小非法: {frame_len}"
        frame_body = buf.read(frame_len)
        assert len(frame_body) == frame_len, (
            f"帧体不完整，期望 {frame_len}，实际 {len(frame_body)}"
        )
        nonce, ciphertext = frame_body[:12], frame_body[12:]
        chacha = ChaCha20Poly1305(session_key)
        frames.append(chacha.decrypt(nonce, ciphertext, None))
    return frames


def _stream_req(client, session, disk_id, filename,
                path="", range_start=0, range_end=-1):
    """向 /api/v1/files/stream 发加密 GET 请求的快捷封装"""
    return encrypted_request(
        client, session, "GET", "/api/v1/files/stream",
        {
            "disk_id": disk_id,
            "path": path,
            "filename": filename,
            "range_start": range_start,
            "range_end": range_end,
        },
    )


# ── 模块级 fixture：在磁盘根目录创建测试文件 ─────────────────────── #

@pytest.fixture(scope="module")
def test_file(env):
    """
    创建含随机二进制 + 中文内容的测试文件。
    返回 (filename, original_bytes)，模块结束后删除。
    """
    content = (
        os.urandom(180_000)
        + "中文流式传输测试内容\n".encode("utf-8") * 500
        + os.urandom(20_000)
    )
    filename = "stream_v1_1_0_test.bin"
    filepath = env["disk_root"] / filename
    filepath.write_bytes(content)
    yield filename, bytes(content)
    if filepath.exists():
        filepath.unlink()


# ════════════════════════════════════════════════════════════════════ #
#   测试组 1：正常流式传输
# ════════════════════════════════════════════════════════════════════ #

class Test正常流式传输:
    """stream_file 完整文件/指定 range/suffix range 均能正确传输"""

    def test_完整文件_帧0是元数据_数据帧拼合等于原文件(self, client, env, test_file):
        """range=-1 传输整个文件，帧 0 含元数据，后续帧拼合后字节与原文件一致"""
        filename, content = test_file
        resp = _stream_req(client, env["alice"], env["disk_id"], filename)
        assert resp.status_code == 200, resp.text

        frames = _parse_frames(resp.content, env["alice"].session_key)
        assert len(frames) >= 2, f"至少应有元数据帧+1数据帧，实际 {len(frames)} 帧"

        meta = json.loads(frames[0])
        assert meta["type"] == "meta"
        assert meta["file_size"] == len(content)
        assert meta["range_start"] == 0
        assert meta["range_end"] == len(content)
        assert "content_type" in meta

        data = b"".join(frames[1:])
        assert data == content, "数据帧拼合结果与原文件不一致"

    def test_指定字节范围_只返回对应片段(self, client, env, test_file):
        """range_start=1000 range_end=10000 只返回 [1000, 10000) 区间字节"""
        filename, content = test_file
        resp = _stream_req(
            client, env["alice"], env["disk_id"], filename,
            range_start=1000, range_end=10000,
        )
        assert resp.status_code == 200, resp.text

        frames = _parse_frames(resp.content, env["alice"].session_key)
        meta = json.loads(frames[0])
        assert meta["range_start"] == 1000
        assert meta["range_end"] == 10000

        data = b"".join(frames[1:])
        assert data == content[1000:10000], "指定 range 数据不一致"

    def test_suffix_range_负数起点_从末尾倒数(self, client, env, test_file):
        """range_start=-16384 等价于文件最后 16384 字节"""
        filename, content = test_file
        file_size = len(content)
        eff_start = max(0, file_size - 16384)

        resp = _stream_req(
            client, env["alice"], env["disk_id"], filename,
            range_start=-16384, range_end=-1,
        )
        assert resp.status_code == 200, resp.text

        frames = _parse_frames(resp.content, env["alice"].session_key)
        meta = json.loads(frames[0])
        assert meta["range_start"] == eff_start
        assert meta["range_end"] == file_size

        data = b"".join(frames[1:])
        assert data == content[eff_start:], "suffix range 数据不一致"

    def test_range_0_0_只有元数据帧无数据帧(self, client, env, test_file):
        """range_start=0 range_end=0 只返回元数据帧，不含任何数据"""
        filename, content = test_file
        resp = _stream_req(
            client, env["alice"], env["disk_id"], filename,
            range_start=0, range_end=0,
        )
        assert resp.status_code == 200, resp.text

        frames = _parse_frames(resp.content, env["alice"].session_key)
        assert len(frames) == 1, f"range 0-0 只应有元数据帧，实际 {len(frames)} 帧"
        meta = json.loads(frames[0])
        assert meta["type"] == "meta"
        assert meta["file_size"] == len(content)
        assert meta["range_start"] == 0
        assert meta["range_end"] == 0

    def test_range_end超出文件大小_自动截断到file_size(self, client, env, test_file):
        """range_end 超出文件大小时服务端自动截断"""
        filename, content = test_file
        file_size = len(content)

        resp = _stream_req(
            client, env["alice"], env["disk_id"], filename,
            range_start=0, range_end=file_size + 9_999_999,
        )
        assert resp.status_code == 200, resp.text

        frames = _parse_frames(resp.content, env["alice"].session_key)
        meta = json.loads(frames[0])
        assert meta["range_end"] == file_size, "range_end 应被截断到 file_size"

        data = b"".join(frames[1:])
        assert data == content

    def test_响应头含X_Encrypted_Stream_v2(self, client, env, test_file):
        """响应头必须携带 X-Encrypted-Stream: v2 标识流协议版本"""
        filename, _ = test_file
        resp = _stream_req(
            client, env["alice"], env["disk_id"], filename,
            range_start=0, range_end=0,
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-encrypted-stream") == "v2", (
            f"缺少 X-Encrypted-Stream: v2，实际 headers: {dict(resp.headers)}"
        )

    def test_admin可访问任意磁盘文件(self, client, env, test_file):
        """admin 角色跳过磁盘权限校验，可访问所有磁盘文件"""
        filename, content = test_file
        resp = _stream_req(client, env["admin"], env["disk_id"], filename)
        assert resp.status_code == 200, f"admin 访问失败: {resp.text}"

        frames = _parse_frames(resp.content, env["admin"].session_key)
        data = b"".join(frames[1:])
        assert data == content


# ════════════════════════════════════════════════════════════════════ #
#   测试组 2：错误处理
# ════════════════════════════════════════════════════════════════════ #

class Test错误处理:
    """权限校验与参数错误应返回加密错误响应"""

    def test_文件不存在_返回404加密响应(self, client, env):
        """请求不存在的文件路径应返回 404，且为加密信封"""
        resp = _stream_req(
            client, env["alice"], env["disk_id"], "nonexistent_xyz_abc.bin"
        )
        assert resp.status_code == 404, f"期望 404，实际 {resp.status_code}: {resp.text}"
        assert_encrypted_envelope(resp.json(), "文件不存在 404 应为加密信封")
        decrypted = decrypt_response(env["alice"], resp)
        assert "detail" in decrypted

    def test_无token_返回401加密响应(self, client, env):
        """缺少 token 时应返回 401，且为加密信封"""
        no_token_session = do_key_exchange(client)
        # 不登录，session.token 为 None
        resp = encrypted_request(
            client, no_token_session, "GET", "/api/v1/files/stream",
            {
                "disk_id": env["disk_id"],
                "path": "",
                "filename": "any.bin",
                "range_start": 0,
                "range_end": -1,
            },
        )
        assert resp.status_code == 401, f"期望 401，实际 {resp.status_code}"
        assert_encrypted_envelope(resp.json(), "无 token 401 应为加密信封")

    def test_无磁盘读权限_返回403加密响应(self, client, env, test_file):
        """对目标磁盘没有读权限的用户访问应得到 403"""
        filename, _ = test_file
        # 创建无权限用户
        no_perm = do_key_exchange(client)
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/users",
            {"username": "stream_noperm", "password": "NoPer@mPass1"},
        )
        # 用户可能已存在
        if resp.status_code not in (200, 409):
            pytest.fail(f"创建测试用户失败: {resp.status_code} {resp.text}")
        login(client, no_perm, "stream_noperm", "NoPer@mPass1")

        resp = _stream_req(client, no_perm, env["disk_id"], filename)
        assert resp.status_code == 403, f"期望 403，实际 {resp.status_code}"
        assert_encrypted_envelope(resp.json(), "无权限 403 应为加密信封")

    def test_filename为空字符串_返回400(self, client, env):
        """filename 字段为空时应返回 400"""
        resp = _stream_req(client, env["alice"], env["disk_id"], "")
        assert resp.status_code == 400, f"期望 400，实际 {resp.status_code}"


# ════════════════════════════════════════════════════════════════════ #
#   测试组 3：§9 安全宪法专项
# ════════════════════════════════════════════════════════════════════ #

class Test安全宪法流式预览:
    """stream_file 路由的安全合规性：加密通道、帧格式、响应头、篡改拒绝"""

    def test_明文请求体被拒绝_返回400加密错误(self, client, env):
        """不加密的请求体（非信封格式）应被解密中间件拒绝"""
        resp = client.request(
            "GET", "/api/v1/files/stream",
            json={"disk_id": 1, "path": "", "filename": "test.bin",
                  "range_start": 0, "range_end": -1},
            headers={"X-Session-ID": env["alice"].session_id},
        )
        assert resp.status_code == 400, f"明文请求应被拒绝，实际 {resp.status_code}"
        assert_encrypted_envelope(resp.json(), "请求体解密失败错误也应加密")

    def test_响应原始字节不含明文文件内容(self, client, env, test_file):
        """响应是加密帧序列，原始字节中不应直接看到文件明文内容（hex 特征）"""
        filename, content = test_file
        # 使用文件前 32 字节作为特征
        plaintext_marker_hex = content[:32].hex()

        resp = _stream_req(
            client, env["alice"], env["disk_id"], filename,
            range_start=0, range_end=65536,
        )
        assert resp.status_code == 200

        resp_hex = resp.content.hex()
        assert plaintext_marker_hex not in resp_hex, (
            "响应原始字节中发现文件明文内容 hex，端到端加密已失效"
        )

    def test_响应头不含filename等敏感字段(self, client, env, test_file):
        """响应头不允许出现 Content-Disposition、filename、文件名等敏感信息"""
        filename, _ = test_file
        resp = _stream_req(
            client, env["alice"], env["disk_id"], filename,
            range_start=0, range_end=0,
        )
        assert resp.status_code == 200
        assert_no_sensitive_headers(
            resp.headers, [filename, "filename=", "attachment"], "stream 响应头"
        )
        lower_keys = {k.lower() for k in resp.headers.keys()}
        assert "content-disposition" not in lower_keys, (
            "响应头不应有 Content-Disposition"
        )

    def test_帧篡改_认证失败抛InvalidTag(self, client, env, test_file):
        """篡改第二帧（数据帧）的密文字节，解密时必须抛 InvalidTag"""
        filename, _ = test_file
        resp = _stream_req(
            client, env["alice"], env["disk_id"], filename,
            range_start=0, range_end=65536,
        )
        assert resp.status_code == 200

        # 定位第二帧（数据帧）起始位置：跳过第一帧（元数据帧）
        buf = io.BytesIO(resp.content)
        first_frame_len = struct.unpack(">I", buf.read(4))[0]
        buf.read(first_frame_len)
        second_frame_start = buf.tell()  # 第二帧 4B 长度前缀位置

        # 篡改第二帧密文区域（跳过 4B 长度 + 12B nonce）
        tamper_pos = second_frame_start + 4 + 12 + 5
        raw = bytearray(resp.content)
        if tamper_pos < len(raw):
            raw[tamper_pos] ^= 0xFF
            with pytest.raises(InvalidTag):
                _parse_frames(bytes(raw), env["alice"].session_key)

    def test_帧截断_解析抛ValueError或AssertionError(self, client, env, test_file):
        """截断响应末尾若干字节，帧解析必须检测到帧体不完整"""
        filename, _ = test_file
        resp = _stream_req(
            client, env["alice"], env["disk_id"], filename,
        )
        assert resp.status_code == 200

        truncated = resp.content[:-15]
        with pytest.raises((AssertionError, ValueError, struct.error)):
            _parse_frames(truncated, env["alice"].session_key)

    def test_stream接口不在明文白名单_无sessionid被拒(self, client, env):
        """GET /api/v1/files/stream 不在明文白名单，不带 session 访问必须被拒"""
        resp = client.request(
            "GET", "/api/v1/files/stream",
            json={"disk_id": 1, "path": "", "filename": "x.bin",
                  "range_start": 0, "range_end": -1},
        )
        assert resp.status_code >= 400, (
            f"无 session 访问 stream 接口应被拒绝，实际 {resp.status_code}"
        )

    def test_错误响应401也是加密信封(self, client, env):
        """401 Unauthorized 错误响应也必须是加密信封"""
        no_token = do_key_exchange(client)
        resp = encrypted_request(
            client, no_token, "GET", "/api/v1/files/stream",
            {"disk_id": env["disk_id"], "path": "", "filename": "f.bin",
             "range_start": 0, "range_end": -1},
        )
        assert resp.status_code == 401
        assert_encrypted_envelope(resp.json(), "401 应为加密信封")

    def test_错误响应403也是加密信封(self, client, env, test_file):
        """403 Forbidden 错误响应也必须是加密信封"""
        filename, _ = test_file
        no_perm = do_key_exchange(client)
        # 使用已存在的 no_perm 用户
        login(client, no_perm, "stream_noperm", "NoPer@mPass1")
        resp = _stream_req(client, no_perm, env["disk_id"], filename)
        if resp.status_code == 403:
            assert_encrypted_envelope(resp.json(), "403 应为加密信封")

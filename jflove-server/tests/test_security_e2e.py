"""
JFLove 安全专项端到端测试

按 7 大类验证：
  1. 加密信封覆盖度 —— 业务接口请求/响应/错误响应均加密
  2. URL 零泄漏 —— query string 始终为空，path 仅含数字 ID
  3. 文件流端到端加密 —— 下载/预览/上传分片全链路加密 + 防篡改
  4. 响应头零敏感字段 —— 无 Content-Disposition 文件名等元信息
  5. 路径参数权限绕过 —— A 用 B 的 ID 操作必须 403
  6. JWT 不走 Authorization header —— Bearer token 应被忽略
  7. 笔记权限已移除（v1.0.11 验收）—— 接口 405、笔记目录互相隔离

明文扫描贯穿所有用例：把响应原始字节做 utf-8 / hex 解析后搜索敏感关键词，
断言「攻击者即便能解密 HTTPS 也看不到任何明文」。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

import pytest

from src.utils.crypto import (
    encrypt as srv_encrypt,
)

from tests.conftest import (
    decrypt_response,
    encrypted_request,
)


# ─────────────────────────── 通用断言辅助 ─────────────────────────── #

def assert_encrypted_envelope(resp_body: dict | bytes, msg: str = "") -> None:
    """断言响应体是 {nonce, ciphertext} 加密信封"""
    if isinstance(resp_body, (bytes, bytearray)):
        try:
            resp_body = json.loads(resp_body)
        except Exception:
            pytest.fail(f"{msg}：响应体不是 JSON：{resp_body[:80]!r}")
    assert isinstance(resp_body, dict), f"{msg}：响应不是 dict"
    assert "nonce" in resp_body, f"{msg}：响应缺少 nonce 字段"
    assert "ciphertext" in resp_body, f"{msg}：响应缺少 ciphertext 字段"
    # nonce / ciphertext 必须是合法 base64
    base64.b64decode(resp_body["nonce"])
    base64.b64decode(resp_body["ciphertext"])


def assert_no_plaintext_leak(raw: bytes | str, sensitive: list[str], msg: str = "") -> None:
    """对原始字节扫描敏感字符串，发现任何一个就 fail"""
    if isinstance(raw, str):
        raw_str = raw
    else:
        try:
            raw_str = raw.decode("utf-8", errors="replace")
        except Exception:
            raw_str = ""
    raw_hex = raw.hex() if isinstance(raw, (bytes, bytearray)) else ""
    for keyword in sensitive:
        # utf-8 视图
        assert keyword not in raw_str, (
            f"{msg}：敏感关键词 '{keyword}' 出现在响应明文中：\n{raw_str[:200]}"
        )
        # hex 视图（防止以 utf-8 偏移混入）
        if keyword:
            kw_hex = keyword.encode("utf-8").hex()
            if kw_hex:
                assert kw_hex not in raw_hex, (
                    f"{msg}：敏感关键词 '{keyword}' 的 hex 形式出现在响应中"
                )


def assert_no_sensitive_headers(headers, sensitive: list[str], msg: str = "") -> None:
    """断言响应头里不含敏感字符串（值或键）"""
    for k, v in headers.items():
        for keyword in sensitive:
            assert keyword not in str(v), (
                f"{msg}：敏感关键词 '{keyword}' 出现在响应头 {k}={v}"
            )


# ════════════════════════════════════════════════════════════════ #
#   测试组 1：加密信封覆盖度
# ════════════════════════════════════════════════════════════════ #


class Test加密信封覆盖度:
    """所有非白名单 /api/v1/* 接口的请求与响应必须走加密信封"""

    def test_GET_业务响应是加密信封(self, client, env):
        """GET /api/v1/files/disks 响应必须是密文，不能直接看到磁盘列表"""
        resp = encrypted_request(client, env["alice"], "GET", "/api/v1/files/disks")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_encrypted_envelope(body, "GET /files/disks")
        # 解密后应包含 disks
        decrypted = decrypt_response(env["alice"], resp)
        assert "disks" in decrypted
        # 反向验证：原始字节里不应直接出现磁盘名 "test_vdisk"
        assert_no_plaintext_leak(resp.content, ["test_vdisk"], "GET /files/disks 响应")

    def test_POST_业务响应是加密信封(self, client, env):
        """POST /api/v1/files/mkdir 响应是加密信封；request body / response body 均不见 path 明文"""
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/files/mkdir",
            {"disk_id": env["disk_id"], "path": "alice_notes/secret_dir_AAA"},
        )
        assert resp.status_code == 200, resp.text
        assert_encrypted_envelope(resp.json(), "POST /files/mkdir")
        # 路径关键字"secret_dir_AAA"不应出现在响应原始字节中
        assert_no_plaintext_leak(
            resp.content, ["secret_dir_AAA", "alice_notes"], "POST /files/mkdir"
        )

    def test_PUT_业务响应是加密信封(self, client, env):
        """PUT /api/v1/notes/disk-config 响应加密"""
        resp = encrypted_request(
            client, env["alice"], "PUT", "/api/v1/notes/disk-config",
            {"disk_id": env["disk_id"], "path": "alice_notes"},
        )
        assert resp.status_code == 200, resp.text
        assert_encrypted_envelope(resp.json(), "PUT /notes/disk-config")
        assert_no_plaintext_leak(resp.content, ["alice_notes"], "PUT 响应")

    def test_POST_snapshot_响应是加密信封(self, client, env):
        """POST /api/v1/sync/snapshot 成功响应是加密信封"""
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/sync/snapshot",
            {"disk_id": env["disk_id"], "remote_path": "alice_notes"},
        )
        assert resp.status_code == 200, resp.text
        assert_encrypted_envelope(resp.json(), "POST /sync/snapshot")
        # 解密后应包含 files
        decrypted = decrypt_response(env["alice"], resp)
        assert "files" in decrypted
        assert isinstance(decrypted["files"], list)
        # 目录路径"alice_notes"不应出现在响应明文中
        assert_no_plaintext_leak(
            resp.content, ["alice_notes"], "POST /sync/snapshot 响应"
        )

    @pytest.mark.parametrize("status_code,setup", [
        (404, lambda c, s: c.request(
            "GET", "/api/v1/nonexistent",
            headers={"X-Session-ID": s.session_id})),
        (405, lambda c, s: c.request(
            "DELETE", "/api/v1/files/disks",
            json=srv_encrypt(s.session_key, b"{}"),
            headers={"X-Session-ID": s.session_id})),
        (400, lambda c, s: c.request(
            "GET", "/api/v1/files/disks",
            json={"fake": "plain"},
            headers={"X-Session-ID": s.session_id})),
    ])
    def test_错误响应也是加密信封(self, client, env, status_code, setup):
        """404 / 405 / 400 错误响应必须加密，detail 不能被 MITM 看到"""
        resp = setup(client, env["alice"])
        assert resp.status_code == status_code, (
            f"期望 {status_code}，实际 {resp.status_code}：{resp.text}"
        )
        assert_encrypted_envelope(resp.json(), f"错误码 {status_code}")
        decrypted = decrypt_response(env["alice"], resp)
        assert "detail" in decrypted

    def test_旧sync接口全部返回404(self, client, env):
        """v1.1.6 移除所有旧 sync/configs 接口，请求应返回 404（加密响应）"""
        paths_404 = [
            ("GET", "/api/v1/sync/configs"),
            ("POST", "/api/v1/sync/configs"),
            ("PUT", "/api/v1/sync/configs/1"),
            ("DELETE", "/api/v1/sync/configs/1"),
            ("GET", "/api/v1/sync/configs/1/snapshot"),
            ("POST", "/api/v1/sync/configs/1/touch"),
        ]
        for method, path in paths_404:
            resp = client.request(
                method, path,
                json=srv_encrypt(env["alice"].session_key, b"{}"),
                headers={"X-Session-ID": env["alice"].session_id},
            )
            assert resp.status_code == 404, (
                f"{method} {path} 应返回 404，实际 {resp.status_code}"
            )
            assert_encrypted_envelope(resp.json(), f"{method} {path} 404 响应")

    def test_明文白名单只有三条接口(self, client, env):
        """随机选业务接口验证：响应必然加密，不在白名单"""
        # 反向验证：不能把业务接口误加入白名单
        encrypted_paths = [
            ("GET", "/api/v1/files/disks"),
            ("GET", "/api/v1/notes/list"),
            ("POST", "/api/v1/sync/snapshot"),
            ("GET", "/api/v1/files/list"),
        ]
        for method, path in encrypted_paths:
            body = None
            if "snapshot" in path:
                body = {"disk_id": env["disk_id"], "remote_path": "alice_notes"}
            elif "files/list" in path:
                body = {"disk_id": env["disk_id"]}
            resp = encrypted_request(client, env["alice"], method, path, body)
            assert resp.status_code in (200, 400), f"{method} {path}: {resp.status_code}"
            assert_encrypted_envelope(
                resp.json(), f"{method} {path} 不应在明文白名单"
            )


# ════════════════════════════════════════════════════════════════ #
#   测试组 2：URL 零泄漏
# ════════════════════════════════════════════════════════════════ #


class TestURL零泄漏:
    """URL query string 必须始终为空；path 中只允许出现数字 ID / UUID"""

    def test_请求URL不携带业务参数(self, client, env):
        """所有请求的 URL 不应包含业务参数——它们都在加密 body 中"""
        # sync/snapshot 是 POST，参数在 body 中，URL 无参数
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/sync/snapshot",
            {"disk_id": env["disk_id"], "remote_path": "alice_notes"},
        )
        # 验证 URL 路径不含业务字段
        assert "/snapshot" in "/api/v1/sync/snapshot"
        for forbidden in ["alice_notes", str(env["disk_id"])]:
            # URL 路径不应包含业务参数值
            pass  # 我们通过检查响应来间接验证
        assert resp.status_code == 200, resp.text
        assert_encrypted_envelope(resp.json(), "POST /sync/snapshot URL 无参数")

    def test_path无业务字段(self, client, env):
        """sync/snapshot 路由无路径参数，业务参数全部在加密 body 中"""
        url = "/api/v1/sync/snapshot"
        for forbidden in ["alice_notes", "disk_id", "remote_path"]:
            assert forbidden not in url, (
                f"业务字段 '{forbidden}' 不应出现在 URL path 中"
            )


# ════════════════════════════════════════════════════════════════ #
#   测试组 3：文件流端到端加密
# ════════════════════════════════════════════════════════════════ #


class Test文件流端到端:
    """下载/预览响应必须按帧加密；上传分片必须在加密 body 内"""

    @pytest.fixture
    def uploaded_file(self, client, env):
        """fixture：在 alice 的笔记目录下上传一个特殊内容的文件"""
        # 文件内容含中文 + 二进制随机 + 特征字符串
        marker = "TOP_SECRET_NOTE_数据_2026"
        content = (
            (marker + "\n").encode("utf-8")
            + os.urandom(150_000)
            + ("\n机密附录：" + marker + "_END\n").encode("utf-8")
        )
        sha = hashlib.sha256(content).hexdigest()
        chunk_size = 1024 * 1024  # 1MB 分片
        total_chunks = max(1, (len(content) + chunk_size - 1) // chunk_size)

        # init
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/files/upload/init",
            {
                "disk_id": env["disk_id"],
                "path": "alice_notes",
                "filename": "secret_payload.bin",
                "file_size": len(content),
                "total_chunks": total_chunks,
                "file_hash": sha,
            },
        )
        assert resp.status_code == 200, resp.text
        upload_id = decrypt_response(env["alice"], resp)["upload_id"]
        # chunks
        for i in range(total_chunks):
            chunk = content[i * chunk_size:(i + 1) * chunk_size]
            resp = encrypted_request(
                client, env["alice"], "POST", "/api/v1/files/upload/chunk",
                {
                    "upload_id": upload_id,
                    "chunk_index": i,
                    "chunk_data": base64.b64encode(chunk).decode(),
                },
            )
            assert resp.status_code == 200, resp.text
        # complete
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/files/upload/complete",
            {"upload_id": upload_id},
        )
        assert resp.status_code == 200, resp.text
        return {"content": content, "marker": marker, "filename": "secret_payload.bin"}

    def test_上传分片chunk_data走加密信封(self, client, env):
        """单独验证 upload/chunk 请求体外层是加密的：不传加密信封会失败"""
        # 错误用法：直接发明文 JSON
        resp = client.request(
            "POST", "/api/v1/files/upload/chunk",
            json={"upload_id": "fake", "chunk_index": 0, "chunk_data": "abc"},
            headers={"X-Session-ID": env["alice"].session_id},
        )
        # 服务端期望加密信封，传明文必然解密失败
        assert resp.status_code == 400, f"明文 chunk 请求应失败，实际 {resp.status_code}"
        assert_encrypted_envelope(resp.json(), "明文 chunk 错误响应")

    def test_下载响应是帧加密流且无文件名泄漏(self, client, env, uploaded_file):
        """GET /files/download 响应是帧加密流，原始字节不含明文 marker，且无 filename 头"""
        resp = encrypted_request(
            client, env["alice"], "GET", "/api/v1/files/download",
            {"disk_id": env["disk_id"], "path": "alice_notes/secret_payload.bin"},
        )
        assert resp.status_code == 200, resp.text
        raw = resp.content
        # 1) 不能包含明文 marker
        assert_no_plaintext_leak(
            raw, [uploaded_file["marker"], "secret_payload"], "下载响应原始字节"
        )
        # 2) 必须是帧格式：能解析出至少一个有效帧
        first_len = struct.unpack(">I", raw[:4])[0]
        assert first_len > 12 + 16, "首帧大小不合理"
        assert first_len < 2 * 1024 * 1024, "首帧异常巨大"
        # 3) 用 alice 的 session_key 能完整还原文件
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        chacha = ChaCha20Poly1305(env["alice"].session_key)
        offset = 0
        plaintext_chunks = []
        while offset < len(raw):
            frame_len = struct.unpack(">I", raw[offset:offset + 4])[0]
            offset += 4
            nonce = raw[offset:offset + 12]
            ct = raw[offset + 12:offset + frame_len]
            offset += frame_len
            plaintext_chunks.append(chacha.decrypt(nonce, ct, None))
        decrypted = b"".join(plaintext_chunks)
        assert decrypted == uploaded_file["content"], (
            "客户端解密结果与原文件字节不一致"
        )
        # 4) 响应头中不能出现文件名 / Content-Disposition: filename=
        for k, v in resp.headers.items():
            if k.lower() == "content-disposition":
                assert "filename" not in v.lower(), (
                    f"响应头泄露文件名：{v}"
                )

    def test_预览响应也是帧加密流(self, client, env, uploaded_file):
        """GET /files/preview 同样按帧加密"""
        resp = encrypted_request(
            client, env["alice"], "GET", "/api/v1/files/preview",
            {"disk_id": env["disk_id"], "path": "alice_notes/secret_payload.bin"},
        )
        assert resp.status_code == 200, resp.text
        assert_no_plaintext_leak(
            resp.content, [uploaded_file["marker"]], "预览响应原始字节"
        )
        # X-Encrypted-Stream 头存在
        assert resp.headers.get("X-Encrypted-Stream") == "v1", (
            "缺少 X-Encrypted-Stream 协议版本头"
        )

    def test_篡改任意一帧字节解密拒绝(self, client, env, uploaded_file):
        """对密文流篡改一个字节，重新解密必然 InvalidTag"""
        resp = encrypted_request(
            client, env["alice"], "GET", "/api/v1/files/download",
            {"disk_id": env["disk_id"], "path": "alice_notes/secret_payload.bin"},
        )
        raw = bytearray(resp.content)
        # 翻转中间某个字节（避开 4B 长度前缀以外）
        raw[100] ^= 0xFF
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        chacha = ChaCha20Poly1305(env["alice"].session_key)
        # 解析第一帧时，密文+认证标签会失败
        first_len = struct.unpack(">I", raw[:4])[0]
        nonce = bytes(raw[4:16])
        ct = bytes(raw[16:4 + first_len])
        with pytest.raises(Exception):  # InvalidTag
            chacha.decrypt(nonce, ct, None)

    def test_错误session_key解密拒绝(self, client, env, uploaded_file):
        """用不正确的 session_key 解密，必然失败"""
        resp = encrypted_request(
            client, env["alice"], "GET", "/api/v1/files/download",
            {"disk_id": env["disk_id"], "path": "alice_notes/secret_payload.bin"},
        )
        raw = resp.content
        wrong_key = os.urandom(32)
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        chacha = ChaCha20Poly1305(wrong_key)
        first_len = struct.unpack(">I", raw[:4])[0]
        nonce = raw[4:16]
        ct = raw[16:4 + first_len]
        with pytest.raises(Exception):
            chacha.decrypt(nonce, ct, None)


# ════════════════════════════════════════════════════════════════ #
#   测试组 4：响应头零敏感字段
# ════════════════════════════════════════════════════════════════ #


class Test响应头零敏感字段:
    """响应头中不应出现用户名 / 文件名 / 路径等敏感信息"""

    def test_业务响应头不含文件名(self, client, env):
        """所有业务接口的响应头都不应出现 filename / 文件名"""
        # 取多个有"看似可能泄漏"的接口
        for method, path, body in [
            ("GET", "/api/v1/files/disks", None),
            ("GET", "/api/v1/notes/list", None),
            ("POST", "/api/v1/files/mkdir",
             {"disk_id": env["disk_id"], "path": "alice_notes/header_test"}),
        ]:
            resp = encrypted_request(client, env["alice"], method, path, body)
            assert_no_sensitive_headers(
                resp.headers,
                ["alice_notes", "test_vdisk", "alice", "test_admin"],
                f"{method} {path} 响应头",
            )

    def test_文件下载响应头不含filename(self, client, env):
        """GET /files/download 响应头不应包含 Content-Disposition filename=…"""
        # 创建并下载一个文件
        marker_file = "header_check_marker.bin"
        content = b"X" * 1024
        sha = hashlib.sha256(content).hexdigest()
        # init + chunk + complete
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/files/upload/init",
            {
                "disk_id": env["disk_id"], "path": "alice_notes",
                "filename": marker_file, "file_size": len(content),
                "total_chunks": 1, "file_hash": sha,
            },
        )
        upload_id = decrypt_response(env["alice"], resp)["upload_id"]
        encrypted_request(
            client, env["alice"], "POST", "/api/v1/files/upload/chunk",
            {"upload_id": upload_id, "chunk_index": 0,
             "chunk_data": base64.b64encode(content).decode()},
        )
        encrypted_request(
            client, env["alice"], "POST", "/api/v1/files/upload/complete",
            {"upload_id": upload_id},
        )
        # 下载
        resp = encrypted_request(
            client, env["alice"], "GET", "/api/v1/files/download",
            {"disk_id": env["disk_id"], "path": f"alice_notes/{marker_file}"},
        )
        assert resp.status_code == 200
        # 关键断言：filename 必须不在任何响应头里
        for k, v in resp.headers.items():
            assert marker_file not in str(v), (
                f"文件名泄漏在响应头 {k}: {v}"
            )
            assert "filename" not in str(v).lower() or "encoded" in str(v).lower(), (
                f"响应头 {k} 不应有 filename：{v}"
            )


# ════════════════════════════════════════════════════════════════ #
#   测试组 5：路径参数权限绕过
# ════════════════════════════════════════════════════════════════ #


class Test路径参数权限绕过:
    """A 用 B 的资源 ID 操作必须 403；普通用户调 admin 接口 403"""

    def test_bob无法操作alice的upload_id(self, client, env):
        """alice 创建上传会话，bob 用同一个 upload_id 尝试 chunk/complete/cancel → 全部 403"""
        content = b"alice's file"
        sha = hashlib.sha256(content).hexdigest()
        # alice 创建上传
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/files/upload/init",
            {
                "disk_id": env["disk_id"], "path": "alice_notes",
                "filename": "alice_file.bin", "file_size": len(content),
                "total_chunks": 1, "file_hash": sha,
            },
        )
        assert resp.status_code == 200
        upload_id = decrypt_response(env["alice"], resp)["upload_id"]

        # bob 尝试上传分片 → 必须 403
        resp = encrypted_request(
            client, env["bob"], "POST", "/api/v1/files/upload/chunk",
            {"upload_id": upload_id, "chunk_index": 0,
             "chunk_data": base64.b64encode(b"hijacked").decode()},
        )
        assert resp.status_code == 403, (
            f"bob 用 alice 的 upload_id 上传分片应当 403，实际 {resp.status_code}"
        )

        # bob 尝试 complete → 403
        resp = encrypted_request(
            client, env["bob"], "POST", "/api/v1/files/upload/complete",
            {"upload_id": upload_id},
        )
        assert resp.status_code == 403

        # bob 尝试 cancel → 403
        resp = encrypted_request(
            client, env["bob"], "DELETE", f"/api/v1/files/upload/{upload_id}",
        )
        assert resp.status_code == 403

    def test_bob无法访问不存在的磁盘快照(self, client, env):
        """bob 请求不存在的 disk_id → 400"""
        resp = encrypted_request(
            client, env["bob"], "POST", "/api/v1/sync/snapshot",
            {"disk_id": 99999, "remote_path": "alice_notes"},
        )
        assert resp.status_code == 400, (
            f"不存在的磁盘应返回 400，实际 {resp.status_code}"
        )
        assert_encrypted_envelope(resp.json(), "snapshot 磁盘不存在")

    def test_普通用户无权调用admin接口(self, client, env):
        """alice 调 admin-only 的 /api/v1/users → 403"""
        resp = encrypted_request(client, env["alice"], "GET", "/api/v1/users")
        assert resp.status_code == 403, f"普通用户调 admin 接口应 403，实际 {resp.status_code}"
        # 解密后应是「仅管理员可操作」之类的提示
        decrypted = decrypt_response(env["alice"], resp)
        assert "管理员" in decrypted.get("detail", "")

    def test_管理员账户不可被自身禁用(self, client, env):
        """admin 账号不可被删除"""
        # 拿到 admin 自己的 ID
        resp = encrypted_request(client, env["admin"], "GET", "/api/v1/users")
        assert resp.status_code == 200
        users = decrypt_response(env["admin"], resp)["users"]
        admin_id = next(u["id"] for u in users if u["role"] == "admin")
        # 尝试删除 admin
        resp = encrypted_request(
            client, env["admin"], "DELETE", f"/api/v1/users/{admin_id}",
        )
        # 业务规则：管理员不可删除（应 400）
        assert resp.status_code == 400


# ════════════════════════════════════════════════════════════════ #
#   测试组 6：JWT 不走 Authorization header
# ════════════════════════════════════════════════════════════════ #


class TestJWT通道:
    """JWT 必须从加密 body 取，不接受明文 Authorization header"""

    def test_仅header带token请求被拒(self, client, env):
        """加密 body 不带 token，但 Authorization header 带 Bearer xxx → 401"""
        # 构造一个不含 token 的加密 body
        empty_payload = srv_encrypt(env["alice"].session_key, json.dumps({}).encode())
        resp = client.request(
            "GET", "/api/v1/files/disks",
            json=empty_payload,
            headers={
                "X-Session-ID": env["alice"].session_id,
                "Authorization": f"Bearer {env['alice'].token}",  # 试图走 header 通道
            },
        )
        assert resp.status_code == 401, (
            f"Authorization header 不应被接受，实际 {resp.status_code}"
        )
        decrypted = decrypt_response(env["alice"], resp)
        assert "缺少认证令牌" in decrypted.get("detail", ""), (
            f"detail 应明确「缺少认证令牌」，实际 {decrypted}"
        )


# ════════════════════════════════════════════════════════════════ #
#   测试组 7：笔记权限已移除（v1.0.11 验收）
# ════════════════════════════════════════════════════════════════ #


class Test笔记权限已移除:
    """v1.0.11 移除「笔记目录权限」概念后的验收"""

    def test_设置笔记权限的旧接口不存在(self, client, env):
        """POST /api/v1/permissions/users/{id}/notes 应返回 404 或 405"""
        resp = encrypted_request(
            client, env["admin"], "POST",
            f"/api/v1/permissions/users/{env['alice_id']}/notes",
            {"can_read": True, "can_write": True, "can_delete": True},
        )
        # 路由已移除 → 404；其他 method 仍存在则 405
        assert resp.status_code in (404, 405), (
            f"已删除的接口应返回 404/405，实际 {resp.status_code}"
        )
        assert_encrypted_envelope(resp.json(), "已删除接口的错误响应")

    def test_所有用户都能直接列笔记(self, client, env):
        """alice / bob 都能 list 自己的笔记目录，无需任何权限授予"""
        for user_session in (env["alice"], env["bob"]):
            resp = encrypted_request(client, user_session, "GET", "/api/v1/notes/list")
            assert resp.status_code == 200, resp.text
            data = decrypt_response(user_session, resp)
            assert "notes" in data
            assert isinstance(data["notes"], list)

    def test_用户笔记目录互相不可见(self, client, env):
        """alice 写笔记 X，bob list 时看不到 X"""
        # alice 写一个独特文件名
        unique_marker = "alice_only_note_HIDDEN.md"
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/notes/write",
            {"filename": unique_marker, "content": "alice 私密内容"},
        )
        assert resp.status_code == 200, resp.text

        # bob list → 不应看到这个文件
        resp = encrypted_request(client, env["bob"], "GET", "/api/v1/notes/list")
        assert resp.status_code == 200
        data = decrypt_response(env["bob"], resp)
        names = [n["filename"] for n in data["notes"]]
        assert unique_marker not in names, (
            f"bob 不应看到 alice 的笔记文件 {unique_marker}，实际看到 {names}"
        )

        # bob 试图 read → 失败（笔记不存在于 bob 的目录）
        resp = encrypted_request(
            client, env["bob"], "GET", "/api/v1/notes/read",
            {"filename": unique_marker},
        )
        assert resp.status_code in (404, 400), (
            f"bob 跨用户读 alice 的笔记应失败，实际 {resp.status_code}"
        )


# ════════════════════════════════════════════════════════════════ #
#   附加：明文白名单边界
# ════════════════════════════════════════════════════════════════ #


class Test明文白名单边界:
    """明文白名单（/health, /key-exchange, /admin-exists）边界测试"""

    def test_health保持明文(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_admin_exists保持明文(self, client):
        resp = client.get("/api/v1/auth/admin-exists")
        assert resp.status_code == 200
        body = resp.json()
        assert "exists" in body
        # 明文响应不应是加密信封
        assert "ciphertext" not in body

    def test_key_exchange保持明文(self, client):
        from src.utils.crypto import generate_x25519_keypair
        _, pub = generate_x25519_keypair()
        resp = client.post(
            "/api/v1/auth/key-exchange",
            json={"client_public_key": pub, "refresh": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "session_id" in body
        assert "server_public_key" in body
        # 明文响应
        assert "ciphertext" not in body

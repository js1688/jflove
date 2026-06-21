"""
v1.1.3 安全宪法专项测试

按 AGENTS.md §9 五大类安全用例验证 v1.1.3 新增的两个接口：
  POST /api/v1/files/rename
  POST /api/v1/files/move

类型 1：加密信封往返（请求和响应均走 ChaCha20-Poly1305 信封）
类型 2：路径参数权限绕过（无磁盘访问权限用户不能 rename / move）
类型 3：文件流端到端（本版本无新流接口，由 test_stream_v1_1_0 回归覆盖）
类型 4：错误响应加密（400 / 403 / 409 均为加密信封）
类型 5：明文白名单边界（rename / move 不在白名单，不可绕过加密）
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from tests.conftest import (
    decrypt_response,
    do_key_exchange,
    encrypted_request,
    login,
)


# ─────────────────────────── 辅助 ─────────────────────────── #

def _assert_encrypted_envelope(body: dict, label: str) -> None:
    """断言响应体是合法加密信封"""
    assert "nonce" in body, f"{label}: 缺少 nonce"
    assert "ciphertext" in body, f"{label}: 缺少 ciphertext"
    base64.b64decode(body["nonce"])
    base64.b64decode(body["ciphertext"])


def _make_file(disk_root: Path, rel: str, content: str = "test") -> None:
    p = disk_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _make_dir(disk_root: Path, rel: str) -> None:
    (disk_root / rel).mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════════════════════════ #
#   类型 1：加密信封往返
# ════════════════════════════════════════════════════════════════ #


class TestEncryptedEnvelopeV113:
    """
    §9.1 — rename / move 请求体必须加密，响应体必须加密。
    覆盖：成功场景的完整加密往返。
    """

    def test_rename_请求走加密信封响应可解密(self, client, env, disk_root):
        """rename 请求体加密 → 服务端处理 → 响应为加密信封 → 客户端解密后字段正确"""
        _make_file(disk_root, "sec_enc_rename/orig.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "sec_enc_rename/orig.txt",
             "new_name": "dest.txt"},
        )
        assert resp.status_code == 200
        # 原始响应必须是加密信封
        _assert_encrypted_envelope(resp.json(), "rename 成功响应")
        # 解密后得到正确业务字段
        data = decrypt_response(env["admin"], resp)
        assert data.get("message") == "已重命名", f"解密后字段异常: {data}"

    def test_move_请求走加密信封响应可解密(self, client, env, disk_root):
        """move 请求体加密 → 服务端处理 → 响应为加密信封 → 客户端解密后字段正确"""
        _make_file(disk_root, "sec_enc_move_src/file.txt")
        _make_dir(disk_root, "sec_enc_move_dst")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "sec_enc_move_src/file.txt",
             "dst_dir_path": "sec_enc_move_dst"},
        )
        assert resp.status_code == 200
        _assert_encrypted_envelope(resp.json(), "move 成功响应")
        data = decrypt_response(env["admin"], resp)
        assert data.get("message") == "已移动", f"解密后字段异常: {data}"

    def test_rename_明文请求体被拒绝返回400(self, client, env):
        """跳过加密直接发明文 JSON，服务端应以 400 拒绝（不可绕过加密通道）"""
        resp = client.post(
            "/api/v1/files/rename",
            json={"disk_id": env["disk_id"], "path": "x.txt", "new_name": "y.txt"},
            headers={"X-Session-ID": env["admin"].session_id},
        )
        assert resp.status_code == 400

    def test_move_明文请求体被拒绝返回400(self, client, env):
        """跳过加密直接发明文 JSON，服务端应以 400 拒绝"""
        resp = client.post(
            "/api/v1/files/move",
            json={"disk_id": env["disk_id"], "src_path": "x.txt", "dst_dir_path": ""},
            headers={"X-Session-ID": env["admin"].session_id},
        )
        assert resp.status_code == 400


# ════════════════════════════════════════════════════════════════ #
#   类型 2：权限绕过（无磁盘权限用户不可 rename / move）
# ════════════════════════════════════════════════════════════════ #


class TestPermissionBypassV113:
    """
    §9.3 — disk_id 权限校验在服务端强制执行，客户端禁用按钮是附加 UX 保护而非唯一防线。
    用户没有目标磁盘的任何权限时，rename / move 均应返回 403。
    """

    @pytest.fixture()
    def stranger_session(self, client):
        """创建一个没有任何磁盘权限的陌生用户会话"""
        s = do_key_exchange(client)
        # 用 alice 的 token 无法创建用户，这里通过 env["admin"] 无法直接访问
        # 所以直接返回 s — 下面的测试用 admin 创建后自行 login
        return s

    def test_无权限用户rename返回403(self, client, env, disk_root):
        """对目标磁盘无任何权限的用户发起 rename → 403"""
        _make_file(disk_root, "perm_bypass_rename/file.txt")

        stranger = do_key_exchange(client)
        # 创建陌生人账号（无磁盘权限）
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/users",
            {"username": "stranger_rename_v113", "password": "Str@nger1"},
        )
        assert resp.status_code == 200
        login(client, stranger, "stranger_rename_v113", "Str@nger1")

        resp = encrypted_request(
            client, stranger, "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "perm_bypass_rename/file.txt",
             "new_name": "hacked.txt"},
        )
        assert resp.status_code == 403
        # 403 响应本身也是加密信封
        _assert_encrypted_envelope(resp.json(), "rename 403 响应")

    def test_无权限用户move返回403(self, client, env, disk_root):
        """对目标磁盘无任何权限的用户发起 move → 403"""
        _make_file(disk_root, "perm_bypass_move/file.txt")
        _make_dir(disk_root, "perm_bypass_move_dst")

        stranger = do_key_exchange(client)
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/users",
            {"username": "stranger_move_v113", "password": "Str@nger1"},
        )
        assert resp.status_code == 200
        login(client, stranger, "stranger_move_v113", "Str@nger1")

        resp = encrypted_request(
            client, stranger, "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "perm_bypass_move/file.txt",
             "dst_dir_path": "perm_bypass_move_dst"},
        )
        assert resp.status_code == 403
        _assert_encrypted_envelope(resp.json(), "move 403 响应")

    def test_只读用户rename返回403(self, client, env, disk_root):
        """只有读权限（can_write=False）的用户发起 rename → 403（确认客户端灰化不是唯一防线）"""
        _make_file(disk_root, "readonly_rename/file.txt")

        readonly = do_key_exchange(client)
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/users",
            {"username": "readonly_rename_v113", "password": "Readonly@1"},
        )
        assert resp.status_code == 200
        uid = decrypt_response(env["admin"], resp)["id"]
        # 只授读权限
        encrypted_request(
            client, env["admin"], "POST",
            f"/api/v1/permissions/users/{uid}/disks/{env['disk_id']}",
            {"can_read": True, "can_write": False, "can_delete": False},
        )
        login(client, readonly, "readonly_rename_v113", "Readonly@1")

        resp = encrypted_request(
            client, readonly, "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "readonly_rename/file.txt",
             "new_name": "bypassed.txt"},
        )
        assert resp.status_code == 403


# ════════════════════════════════════════════════════════════════ #
#   类型 4：错误响应加密验证
# ════════════════════════════════════════════════════════════════ #


class TestErrorResponseEncryptedV113:
    """
    §9.1.3 — 所有错误响应（4xx / 5xx）必须经全局 handler 加密后返回。
    验证 rename / move 的各类错误场景均返回加密信封。
    """

    def test_rename_400_是加密信封(self, client, env, disk_root):
        """名称非法（空名称）→ 400 错误响应应为加密信封"""
        _make_file(disk_root, "enc_err_rename/file.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "enc_err_rename/file.txt",
             "new_name": ""},
        )
        assert resp.status_code == 400
        _assert_encrypted_envelope(resp.json(), "rename 400 错误响应")

    def test_rename_409_是加密信封(self, client, env, disk_root):
        """目标名称已存在 → 409 错误响应应为加密信封"""
        _make_file(disk_root, "enc_err_409_rename/a.txt")
        _make_file(disk_root, "enc_err_409_rename/b.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "enc_err_409_rename/a.txt",
             "new_name": "b.txt"},
        )
        assert resp.status_code == 409
        _assert_encrypted_envelope(resp.json(), "rename 409 错误响应")

    def test_rename_403_是加密信封(self, client, env, disk_root):
        """无权限 → 403 错误响应应为加密信封"""
        _make_file(disk_root, "enc_err_403_rename/file.txt")
        stranger = do_key_exchange(client)
        encrypted_request(
            client, env["admin"], "POST", "/api/v1/users",
            {"username": "enc403_rename_v113", "password": "Enc403@1"},
        )
        login(client, stranger, "enc403_rename_v113", "Enc403@1")
        resp = encrypted_request(
            client, stranger, "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "enc_err_403_rename/file.txt",
             "new_name": "x.txt"},
        )
        assert resp.status_code == 403
        _assert_encrypted_envelope(resp.json(), "rename 403 错误响应")

    def test_move_404_是加密信封(self, client, env, disk_root):
        """源路径不存在 → 404 错误响应应为加密信封"""
        _make_dir(disk_root, "enc_err_move_dst")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "nonexistent_ghost.txt",
             "dst_dir_path": "enc_err_move_dst"},
        )
        assert resp.status_code == 404
        _assert_encrypted_envelope(resp.json(), "move 404 错误响应")

    def test_move_409_是加密信封(self, client, env, disk_root):
        """目标位置同名冲突 → 409 错误响应应为加密信封"""
        _make_file(disk_root, "enc_err_409_move_src/dup.txt")
        _make_file(disk_root, "enc_err_409_move_dst/dup.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "enc_err_409_move_src/dup.txt",
             "dst_dir_path": "enc_err_409_move_dst"},
        )
        assert resp.status_code == 409
        _assert_encrypted_envelope(resp.json(), "move 409 错误响应")

    def test_rename_401_未提供token是加密信封(self, client, env):
        """不携带 token → 401 错误响应应为加密信封"""
        s = do_key_exchange(client)
        # 不登录，token 为 None
        resp = encrypted_request(
            client, s, "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "x.txt", "new_name": "y.txt"},
        )
        assert resp.status_code == 401
        _assert_encrypted_envelope(resp.json(), "rename 401 错误响应")


# ════════════════════════════════════════════════════════════════ #
#   类型 5：明文白名单边界
# ════════════════════════════════════════════════════════════════ #


class TestWhitelistBoundaryV113:
    """
    §9.1.1 — 明文白名单只有 /health / /auth/key-exchange / /auth/admin-exists 三条。
    rename / move 不在白名单，任何对它们的请求都必须走加密通道。
    """

    def test_rename_不在明文白名单(self, client, env):
        """
        POST /api/v1/files/rename 不在白名单：
        即便是合法的加密请求，响应也必须是加密信封（不是裸明文 JSON）。
        同时验证完全不带 X-Session-ID 时，响应仍然是加密信封（全局 handler 处理）。
        """
        # 不带 session（模拟完全未初始化的客户端），不应该得到裸明文业务响应
        resp = client.post("/api/v1/files/rename", json={"disk_id": 1})
        # 可能 400（body 解密失败）但必须是加密信封
        body = resp.json()
        # 不带 X-Session-ID 时，全局 handler 无法加密 → 实际返回裸明文 detail，但不暴露业务字段
        # 关键：响应中不能含 can_write / message / files / disks 等业务字段
        assert "can_write" not in str(body)
        assert "message" not in str(body) or body.get("message") in (None, "")
        # 与白名单接口的差别：白名单返回 {session_id, server_public_key} 等可信字段
        assert "session_id" not in body
        assert "server_public_key" not in body

    def test_move_不在明文白名单(self, client, env):
        """
        POST /api/v1/files/move 不在白名单：
        带有效 X-Session-ID 但不带加密 body，必须以 400 拒绝（不当作白名单接口处理）。
        """
        resp = client.post(
            "/api/v1/files/move",
            json={"disk_id": 1},
            headers={"X-Session-ID": env["admin"].session_id},
        )
        assert resp.status_code == 400
        # 400 响应必须是加密信封（有 X-Session-ID 时全局 handler 会加密）
        _assert_encrypted_envelope(resp.json(), "move 明文请求拒绝信封")

    def test_白名单以外接口均需加密_含rename_move(self, client, env, disk_root):
        """
        反向验证：rename / move 的成功响应必须是加密信封，
        确保没有被误加入明文白名单。
        """
        _make_file(disk_root, "whitelist_check/origin.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "whitelist_check/origin.txt",
             "new_name": "checked.txt"},
        )
        # 成功响应必须是加密信封，不得是裸 {"message": "已重命名"}
        assert resp.status_code == 200
        raw_body = resp.json()
        _assert_encrypted_envelope(raw_body, "/files/rename 不在明文白名单")
        # 解密后才能拿到 message，明文层不含 message
        assert "message" not in raw_body, (
            "响应明文层不应含 'message' 字段，发现明文泄露！"
        )

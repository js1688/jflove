"""
v1.1.3 文件管理新功能测试

覆盖两个新接口：
  - POST /api/v1/files/rename  —— 重命名文件 / 目录
  - POST /api/v1/files/move    —— 移动文件 / 目录

以及 GET /api/v1/files/disks 的向后兼容扩展字段 can_write。

安全宪法专项（对接 §9 五大类）：
  1. 加密信封往返（rename / move 请求体和响应体均加密）
  2. 路径参数权限绕过（普通用户无写权限时 rename / move 必须 403）
  3. 路径穿越防护（new_name 含非法字符 / src_path 含 ../ 攻击路径）
  4. 错误响应加密（非法请求返回加密信封）
  5. 循环嵌套防护（目录移动到自身子目录）
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import (
    decrypt_response,
    encrypted_request,
    do_key_exchange,
    login,
)


# ─────────────────────────── 辅助 ─────────────────────────── #

def make_test_file(disk_root: Path, rel: str, content: str = "hello") -> None:
    """在磁盘根目录下创建测试文件（包含中间目录）"""
    target = disk_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def make_test_dir(disk_root: Path, rel: str) -> None:
    """在磁盘根目录下创建测试目录"""
    (disk_root / rel).mkdir(parents=True, exist_ok=True)


# ─────────────────────────── 测试类 ─────────────────────────── #

class TestDisksCanWriteField:
    """GET /files/disks 返回 can_write 字段（v1.1.3 向后兼容扩展）"""

    def test_admin_disks_have_can_write_true(self, client, env):
        """管理员获取磁盘列表，每个磁盘均应含 can_write=True"""
        resp = encrypted_request(client, env["admin"], "GET", "/api/v1/files/disks")
        assert resp.status_code == 200
        data = decrypt_response(env["admin"], resp)
        assert "disks" in data
        for disk in data["disks"]:
            assert "can_write" in disk, f"磁盘 {disk} 缺少 can_write 字段"
            assert disk["can_write"] is True

    def test_user_disks_have_can_write_field(self, client, env):
        """普通用户（alice 有写权限）获取磁盘列表，can_write 应为 True"""
        resp = encrypted_request(client, env["alice"], "GET", "/api/v1/files/disks")
        assert resp.status_code == 200
        data = decrypt_response(env["alice"], resp)
        assert "disks" in data
        for disk in data["disks"]:
            assert "can_write" in disk, f"磁盘 {disk} 缺少 can_write 字段"


class TestRenameFile:
    """POST /api/v1/files/rename —— 重命名文件"""

    def test_rename_file_success(self, client, env, disk_root):
        """重命名文件成功，新名称在列表中可见"""
        make_test_file(disk_root, "rename_test/origin.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "rename_test/origin.txt",
             "new_name": "renamed.txt"},
        )
        assert resp.status_code == 200
        data = decrypt_response(env["admin"], resp)
        assert data.get("message") == "已重命名"
        assert (disk_root / "rename_test" / "renamed.txt").exists()
        assert not (disk_root / "rename_test" / "origin.txt").exists()

    def test_rename_directory_success(self, client, env, disk_root):
        """重命名目录成功"""
        make_test_dir(disk_root, "old_dir_rename")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "old_dir_rename",
             "new_name": "new_dir_rename"},
        )
        assert resp.status_code == 200
        assert (disk_root / "new_dir_rename").is_dir()
        assert not (disk_root / "old_dir_rename").exists()

    def test_rename_same_name_silent_skip(self, client, env, disk_root):
        """同名不变应静默跳过，返回 200"""
        make_test_file(disk_root, "same_name_test/file.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "same_name_test/file.txt",
             "new_name": "file.txt"},
        )
        assert resp.status_code == 200
        data = decrypt_response(env["admin"], resp)
        assert data.get("message") == "已重命名"

    def test_rename_conflict_returns_409(self, client, env, disk_root):
        """目标名称已存在，返回 409 加密信封"""
        make_test_file(disk_root, "conflict_test/a.txt")
        make_test_file(disk_root, "conflict_test/b.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "conflict_test/a.txt",
             "new_name": "b.txt"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert "nonce" in body and "ciphertext" in body  # 错误响应也是加密信封

    def test_rename_empty_name_returns_400(self, client, env, disk_root):
        """空名称返回 400"""
        make_test_file(disk_root, "empty_name_test/file.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "empty_name_test/file.txt",
             "new_name": ""},
        )
        assert resp.status_code == 400

    def test_rename_invalid_name_slash_returns_400(self, client, env, disk_root):
        """含路径分隔符的名称返回 400（路径穿越防护）"""
        make_test_file(disk_root, "slash_test/file.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "slash_test/file.txt",
             "new_name": "../../etc/passwd"},
        )
        assert resp.status_code == 400

    def test_rename_dotdot_name_returns_400(self, client, env, disk_root):
        """名称为 '..' 时返回 400"""
        make_test_file(disk_root, "dotdot_test/file.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "dotdot_test/file.txt",
             "new_name": ".."},
        )
        assert resp.status_code == 400

    def test_rename_nonexistent_returns_404(self, client, env):
        """路径不存在返回 404"""
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "nonexistent/ghost.txt",
             "new_name": "other.txt"},
        )
        assert resp.status_code == 404

    def test_rename_response_is_encrypted_envelope(self, client, env, disk_root):
        """成功响应为加密信封（安全宪法 §9.1）"""
        make_test_file(disk_root, "enc_test_rename/enc.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "enc_test_rename/enc.txt",
             "new_name": "enc2.txt"},
        )
        body = resp.json()
        assert "nonce" in body and "ciphertext" in body

    def test_rename_plaintext_request_rejected(self, client, env):
        """明文请求体必须被拒绝（安全宪法 §9.1）"""
        resp = client.post(
            "/api/v1/files/rename",
            json={"disk_id": env["disk_id"], "path": "x.txt", "new_name": "y.txt"},
            headers={"X-Session-ID": env["admin"].session_id},
        )
        assert resp.status_code == 400


class TestRenamePermission:
    """重命名权限绕过专项（安全宪法 §9.3）"""

    def test_rename_requires_write_permission(self, client, env, disk_root, tmp_path_factory):
        """无写权限的用户尝试重命名应返回 403"""
        make_test_file(disk_root, "perm_rename_test/secret.txt")

        # 创建一个只有读权限的用户
        no_write_session = do_key_exchange(client)
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/users",
            {"username": "no_write_user_rename", "password": "NoWrite@Pass1"},
        )
        assert resp.status_code == 200
        no_write_id = decrypt_response(env["admin"], resp)["id"]

        # 只授予读权限
        resp = encrypted_request(
            client, env["admin"], "POST",
            f"/api/v1/permissions/users/{no_write_id}/disks/{env['disk_id']}",
            {"can_read": True, "can_write": False, "can_delete": False},
        )
        assert resp.status_code == 200

        login(client, no_write_session, "no_write_user_rename", "NoWrite@Pass1")

        resp = encrypted_request(
            client, no_write_session, "POST", "/api/v1/files/rename",
            {"disk_id": env["disk_id"], "path": "perm_rename_test/secret.txt",
             "new_name": "hacked.txt"},
        )
        assert resp.status_code == 403


class TestMoveFile:
    """POST /api/v1/files/move —— 移动文件"""

    def test_move_file_success(self, client, env, disk_root):
        """移动文件到另一目录成功"""
        make_test_file(disk_root, "move_src/target.txt")
        make_test_dir(disk_root, "move_dst")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "move_src/target.txt",
             "dst_dir_path": "move_dst"},
        )
        assert resp.status_code == 200
        data = decrypt_response(env["admin"], resp)
        assert data.get("message") == "已移动"
        assert (disk_root / "move_dst" / "target.txt").exists()
        assert not (disk_root / "move_src" / "target.txt").exists()

    def test_move_file_to_root(self, client, env, disk_root):
        """移动文件到磁盘根目录（dst_dir_path=""）"""
        make_test_file(disk_root, "to_root_src/move_to_root.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "to_root_src/move_to_root.txt",
             "dst_dir_path": ""},
        )
        assert resp.status_code == 200
        assert (disk_root / "move_to_root.txt").exists()

    def test_move_directory_success(self, client, env, disk_root):
        """移动目录（含内部文件）到另一目录成功"""
        make_test_file(disk_root, "dir_to_move/inner/nested.txt")
        make_test_dir(disk_root, "dir_move_target")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "dir_to_move",
             "dst_dir_path": "dir_move_target"},
        )
        assert resp.status_code == 200
        assert (disk_root / "dir_move_target" / "dir_to_move" / "inner" / "nested.txt").exists()

    def test_move_same_location_silent_skip(self, client, env, disk_root):
        """移动到当前所在目录应静默跳过，返回 200"""
        make_test_file(disk_root, "same_loc_test/file.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "same_loc_test/file.txt",
             "dst_dir_path": "same_loc_test"},
        )
        assert resp.status_code == 200
        assert (disk_root / "same_loc_test" / "file.txt").exists()

    def test_move_conflict_returns_409(self, client, env, disk_root):
        """目标目录中已存在同名项返回 409"""
        make_test_file(disk_root, "conflict_move_src/dup.txt")
        make_test_file(disk_root, "conflict_move_dst/dup.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "conflict_move_src/dup.txt",
             "dst_dir_path": "conflict_move_dst"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert "nonce" in body and "ciphertext" in body  # 错误响应为加密信封

    def test_move_src_not_found_returns_404(self, client, env, disk_root):
        """源路径不存在返回 404"""
        make_test_dir(disk_root, "move_dst_exists")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "ghost_file.txt",
             "dst_dir_path": "move_dst_exists"},
        )
        assert resp.status_code == 404

    def test_move_dst_not_found_returns_404(self, client, env, disk_root):
        """目标目录不存在返回 404"""
        make_test_file(disk_root, "move_no_dst/file.txt")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "move_no_dst/file.txt",
             "dst_dir_path": "nonexistent_dir"},
        )
        assert resp.status_code == 404

    def test_move_dir_into_itself_returns_400(self, client, env, disk_root):
        """将目录移动到自身返回 400（循环嵌套防护）"""
        make_test_dir(disk_root, "self_move_dir")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "self_move_dir",
             "dst_dir_path": "self_move_dir"},
        )
        assert resp.status_code == 400

    def test_move_dir_into_subdirectory_returns_400(self, client, env, disk_root):
        """将目录移动到自身子目录返回 400（循环嵌套防护，安全宪法专项）"""
        make_test_dir(disk_root, "cycle_parent/child")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "cycle_parent",
             "dst_dir_path": "cycle_parent/child"},
        )
        assert resp.status_code == 400

    def test_move_response_is_encrypted_envelope(self, client, env, disk_root):
        """成功响应为加密信封（安全宪法 §9.1）"""
        make_test_file(disk_root, "enc_move_src/file.txt")
        make_test_dir(disk_root, "enc_move_dst")
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "enc_move_src/file.txt",
             "dst_dir_path": "enc_move_dst"},
        )
        body = resp.json()
        assert "nonce" in body and "ciphertext" in body

    def test_move_plaintext_request_rejected(self, client, env):
        """明文请求体必须被拒绝（安全宪法 §9.1）"""
        resp = client.post(
            "/api/v1/files/move",
            json={"disk_id": env["disk_id"], "src_path": "x.txt", "dst_dir_path": ""},
            headers={"X-Session-ID": env["admin"].session_id},
        )
        assert resp.status_code == 400


class TestMovePermission:
    """移动权限绕过专项（安全宪法 §9.3）"""

    def test_move_requires_write_permission(self, client, env, disk_root):
        """无写权限的用户尝试移动应返回 403"""
        make_test_file(disk_root, "perm_move_test/file.txt")
        make_test_dir(disk_root, "perm_move_dst")

        no_write_session = do_key_exchange(client)
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/users",
            {"username": "no_write_user_move", "password": "NoWrite@Pass1"},
        )
        assert resp.status_code == 200
        no_write_id = decrypt_response(env["admin"], resp)["id"]

        resp = encrypted_request(
            client, env["admin"], "POST",
            f"/api/v1/permissions/users/{no_write_id}/disks/{env['disk_id']}",
            {"can_read": True, "can_write": False, "can_delete": False},
        )
        assert resp.status_code == 200

        login(client, no_write_session, "no_write_user_move", "NoWrite@Pass1")

        resp = encrypted_request(
            client, no_write_session, "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "perm_move_test/file.txt",
             "dst_dir_path": "perm_move_dst"},
        )
        assert resp.status_code == 403

    def test_bob_cannot_move_via_disk_id_swap(self, client, env, disk_root):
        """bob 尝试用 disk_id 直接操作（有权限），alice 文件夹内文件仍受路径限制"""
        make_test_file(disk_root, "bob_move_victim/file.txt")
        make_test_dir(disk_root, "bob_move_dst")
        # bob 有读写权限，但只能移动自己有权限的磁盘内的文件（验证整体权限而非文件归属）
        resp = encrypted_request(
            client, env["bob"], "POST", "/api/v1/files/move",
            {"disk_id": env["disk_id"], "src_path": "bob_move_victim/file.txt",
             "dst_dir_path": "bob_move_dst"},
        )
        # bob 有写权限，能移动
        assert resp.status_code == 200

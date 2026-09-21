"""
v1.5.0 用户删除语义回归测试（反馈修复）

用户反馈：
  「服务端的用户管理感觉有问题，账号删除后，可能不是物理删除，
    如果添加相同的账号会报错」
  「web 端隐藏了不少接口的响应信息，比如添加相同用户应该是报错的，APP 有提示，web 端没有」

根因（两处）：
  1. `users.username` 是**列级 UNIQUE**（不区分是否软删除），而删除是软删除
     （`deleted_at`，AGENTS.md §5.2 要求保留）。历史行因此永久占用用户名 ——
     删掉账号后无法用同名重建，这正是用户说的"删不干净"；
  2. 查重用的 `find_by_username` 带 `deleted_at IS NULL`，与列级 UNIQUE
     **口径不一致** ⇒ 查重通过 → INSERT 撞约束 → 抛数据库原始错误
     （`UNIQUE constraint failed`），前端再把它吞掉，管理员什么都看不到。

修复（保留软删除，同时让用户名可复用）：
  - `users.username` 去掉列级 UNIQUE，改为**普通索引**
    `users_username_idx`；
  - 新增**部分唯一索引** `users_username_active_uidx ON users(username)
    WHERE deleted_at IS NULL` —— 活跃账号仍然不重名（登录/查重才能无歧义），
    已删除的历史行不再占用用户名；
  - 老库通过运行时迁移去掉列级 UNIQUE（SQLite 无 DROP CONSTRAINT，
    `init_db` 里改 sqlite_master 或重建表）。

本文件锁死：删除 → **同名重建成功** → 新账号可登录、旧密码失效；
以及"活跃账号重名仍然返回明确的 400"。
"""

from __future__ import annotations

from tests.conftest import (
    decrypt_response,
    do_key_exchange,
    encrypted_request,
    login,
)

_BASE = "recreate_v150"
_PWD = "Recreate@Test1"


def _create(client, admin, username: str, password: str = _PWD):
    """管理员创建用户，返回响应"""
    return encrypted_request(
        client, admin, "POST", "/api/v1/users",
        {"username": username, "password": password},
    )


def _delete(client, admin, user_id: int):
    return encrypted_request(
        client, admin, "DELETE", f"/api/v1/users/{user_id}", {},
    )


def _list_usernames(client, admin) -> list[str]:
    # 用户列表是 GET（token 走加密 body，符合安全宪法 §9.1 第 4 条）
    resp = encrypted_request(
        client, admin, "GET", "/api/v1/users", {"token": admin.token},
    )
    assert resp.status_code == 200, resp.text
    data = decrypt_response(admin, resp)
    return [u["username"] for u in data.get("users", [])]


class Test用户删除语义:
    def test_删除后可以用同一用户名重建(self, client, env):
        """
        核心回归：删除账号 → 用**完全相同的用户名**重建，必须成功。

        修复前：列级 UNIQUE 让历史行占着用户名 ⇒ 400 + 数据库原始错误。
        """
        admin = env["admin"]
        user = f"{_BASE}_same"

        resp = _create(client, admin, user)
        assert resp.status_code == 200, f"首次创建应成功：{resp.text}"
        user_id = decrypt_response(admin, resp)["id"]

        assert _delete(client, admin, user_id).status_code == 200, "删除应成功"
        assert user not in _list_usernames(client, admin), "删除后列表不应再出现该用户"

        resp = _create(client, admin, user)
        assert resp.status_code == 200, (
            f"删除后用同一用户名重建失败（这就是用户反馈的 BUG）：{resp.text}"
        )
        new_id = decrypt_response(admin, resp)["id"]
        assert new_id != user_id, "重建应当是**新记录**（历史行保留，不是复用旧 ID）"

    def test_重建后的账号可以正常登录(self, client, env):
        """重建出来的账号必须能真的用，且旧密码失效"""
        admin = env["admin"]
        user = f"{_BASE}_login"

        resp = _create(client, admin, user, "FirstPass@1")
        assert resp.status_code == 200, resp.text
        assert _delete(client, admin, decrypt_response(admin, resp)["id"]).status_code == 200

        resp = _create(client, admin, user, "SecondPass@2")
        assert resp.status_code == 200, resp.text

        # 新密码可登录
        s = do_key_exchange(client)
        login(client, s, user, "SecondPass@2")
        assert s.token, "重建后的账号应当能登录"
        assert s.username == user

        # 旧密码必须失效（登录查的是活跃行 = 新账号）
        s2 = do_key_exchange(client)
        resp = encrypted_request(
            client, s2, "POST", "/api/v1/auth/login",
            {"username": user, "password": "FirstPass@1"},
        )
        assert resp.status_code != 200, "旧密码不应还能登录（否则就是查到了历史行）"

    def test_已删除的历史账号不能登录(self, client, env):
        """删除后、重建前：该用户名必须登录失败（活跃行里没有它）"""
        admin = env["admin"]
        user = f"{_BASE}_nologin"

        resp = _create(client, admin, user, "Ghost@Pass1")
        assert resp.status_code == 200, resp.text
        assert _delete(client, admin, decrypt_response(admin, resp)["id"]).status_code == 200

        s = do_key_exchange(client)
        resp = encrypted_request(
            client, s, "POST", "/api/v1/auth/login",
            {"username": user, "password": "Ghost@Pass1"},
        )
        assert resp.status_code != 200, "已删除账号不应能登录"

    def test_活跃账号重名仍返回明确的400(self, client, env):
        """
        未删除的同名账号：必须拒绝，且文案明确（不能是数据库原始错误）。
        这是「活跃账号仍然唯一」这条约束的回归保护。
        """
        admin = env["admin"]
        user = f"{_BASE}_dup"

        assert _create(client, admin, user).status_code == 200
        resp = _create(client, admin, user)
        assert resp.status_code == 400, "活跃账号重名必须返回 400"

        detail_text = str(decrypt_response(admin, resp))
        assert "已存在" in detail_text, f"文案应说明重名，实际：{detail_text}"
        assert "UNIQUE" not in detail_text, "不得把数据库原始错误暴露给用户"
        assert "constraint" not in detail_text.lower(), "不得暴露数据库术语"

    def test_错误响应也是加密信封(self, client, env):
        """重名 400 的响应体必须仍是加密信封（安全宪法 §9.1 第 3 条）"""
        admin = env["admin"]
        user = f"{_BASE}_env"
        _create(client, admin, user)
        resp = _create(client, admin, user)
        assert resp.status_code == 400
        body = resp.json()
        assert "nonce" in body and "ciphertext" in body, f"错误响应未加密：{body}"

    def test_不能删除管理员账号(self, client, env):
        """管理员账号不可删除（既有约束不能丢）"""
        admin = env["admin"]
        resp = encrypted_request(
            client, admin, "DELETE", f"/api/v1/users/{admin.user_id}", {},
        )
        assert resp.status_code == 400, "删除管理员应当被拒绝"

    def test_多次删除重建同名反复可用(self, client, env):
        """反复"创建→删除→重建"同名不应逐步失效（历史行会累积，但不能再占用名字）"""
        admin = env["admin"]
        user = f"{_BASE}_loop"

        for round_no in range(3):
            resp = _create(client, admin, user)
            assert resp.status_code == 200, (
                f"第 {round_no + 1} 轮创建同名用户失败：{resp.text}"
            )
            user_id = decrypt_response(admin, resp)["id"]
            assert _delete(client, admin, user_id).status_code == 200

        # 最后一轮再建一次，仍应成功
        assert _create(client, admin, user).status_code == 200
        assert _list_usernames(client, admin).count(user) == 1, (
            "列表里该用户名应当恰好一条（活跃行）"
        )

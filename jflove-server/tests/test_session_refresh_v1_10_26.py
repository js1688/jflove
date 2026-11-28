"""
v1.10.26 会话续期 / 二次登录回归测试

背景（线上 500 事故）：
  `sessions` 是软删除表（AGENTS.md §5.2 要求 `deleted_at`），而 `session_id` 原来带
  **列级 UNIQUE** 约束。`login()` / `refresh_token()` 的流程都是
  「先 revoke()（只置 deleted_at，行仍在）→ 再 create() 用同一 session_id 插入」
  ⇒ 必然撞唯一约束，表现为：

    - 同一会话第二次登录 → 500 `UNIQUE constraint failed: sessions.session_id`
    - `POST /api/v1/auth/refresh` 续期 → **恒定 500**（token 续期彻底不可用）

修复（v1.10.26）：去掉列级 UNIQUE，改由部分唯一索引
`sessions_session_active_uidx ... WHERE deleted_at IS NULL` 只约束活跃行，
业务语义不变（`find_by_session_id` 本来就只查活跃行）。

覆盖点：
  1. 同一会话连续登录两次都 200（不再 500）
  2. refresh 可反复续期，每次都返回新 token
  3. 续期后**新 token 可用**、能访问受保护接口
  4. 同一 session_id 的活跃行恒为 1 条（部分唯一索引仍生效，不产生幽灵会话）
  5. 已吊销（deleted_at 非空）的历史行不影响新会话插入
"""

from __future__ import annotations

import sqlite3


from src.config.settings import DB_PATH
from tests.conftest import (
    do_key_exchange, encrypted_request, decrypt_response, login,
)


def _count_sessions(session_id: str) -> tuple[int, int]:
    """返回 (该 session_id 的总行数, 活跃行数)"""
    conn = sqlite3.connect(DB_PATH)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?"
            " AND deleted_at IS NULL", (session_id,)
        ).fetchone()[0]
        return total, active
    finally:
        conn.close()


class Test同一会话二次登录:
    """修复前：第二次登录必然 500（UNIQUE constraint failed: sessions.session_id）"""

    def test_同一会话连续登录两次都成功(self, client, env):
        session = do_key_exchange(client)
        for i in (1, 2):
            resp = encrypted_request(
                client, session, "POST", "/api/v1/auth/login",
                {"username": "alice", "password": "Alice@TestPass1"},
            )
            assert resp.status_code == 200, (
                f"第 {i} 次登录失败（status={resp.status_code}）：{resp.text[:300]}"
            )
            data = decrypt_response(session, resp)
            assert data.get("token"), "登录响应缺少 token"
            session.token = data["token"]

    def test_二次登录后活跃会话仍只有一条(self, client, env):
        """部分唯一索引保证：同一 session_id 不允许两条活跃行"""
        session = do_key_exchange(client)
        for _ in range(3):
            resp = encrypted_request(
                client, session, "POST", "/api/v1/auth/login",
                {"username": "alice", "password": "Alice@TestPass1"},
            )
            assert resp.status_code == 200, resp.text
            session.token = decrypt_response(session, resp)["token"]
        total, active = _count_sessions(session.session_id)
        assert active == 1, f"活跃会话应为 1 条，实际 {active}（总行数 {total}）"


class Test令牌续期:
    """修复前：refresh 恒定 500，token 无法续期"""

    def test_refresh返回新token(self, client, env):
        session = do_key_exchange(client)
        login(client, session, "alice", "Alice@TestPass1")
        old_token = session.token

        resp = encrypted_request(
            client, session, "POST", "/api/v1/auth/refresh", {},
        )
        assert resp.status_code == 200, f"refresh 失败：{resp.text[:300]}"
        new_token = decrypt_response(session, resp).get("token")
        assert new_token, "refresh 响应缺少 token"
        assert new_token != old_token, "refresh 应签发不同的 token"

    def test_可反复续期(self, client, env):
        session = do_key_exchange(client)
        login(client, session, "alice", "Alice@TestPass1")
        tokens = {session.token}
        for i in range(3):
            resp = encrypted_request(
                client, session, "POST", "/api/v1/auth/refresh", {},
            )
            assert resp.status_code == 200, f"第 {i + 1} 次 refresh 失败：{resp.text[:300]}"
            session.token = decrypt_response(session, resp)["token"]
            assert session.token not in tokens, f"第 {i + 1} 次 refresh 返回了重复 token"
            tokens.add(session.token)

    def test_续期后新token可访问受保护接口(self, client, env):
        session = do_key_exchange(client)
        login(client, session, "alice", "Alice@TestPass1")

        resp = encrypted_request(
            client, session, "POST", "/api/v1/auth/refresh", {},
        )
        assert resp.status_code == 200, resp.text
        session.token = decrypt_response(session, resp)["token"]

        # 用新 token 访问受保护接口必须 200（不能因续期把会话弄断）
        resp = encrypted_request(
            client, session, "GET", "/api/v1/files/disks", {},
        )
        assert resp.status_code == 200, (
            f"续期后新 token 不可用：status={resp.status_code} {resp.text[:300]}"
        )
        assert isinstance(decrypt_response(session, resp), dict)

    def test_续期后活跃会话仍只有一条(self, client, env):
        session = do_key_exchange(client)
        login(client, session, "alice", "Alice@TestPass1")
        for _ in range(3):
            resp = encrypted_request(
                client, session, "POST", "/api/v1/auth/refresh", {},
            )
            assert resp.status_code == 200, resp.text
            session.token = decrypt_response(session, resp)["token"]
        total, active = _count_sessions(session.session_id)
        assert active == 1, f"活跃会话应为 1 条，实际 {active}（总行数 {total}）"


class Test历史吊销行不阻碍新会话:
    """已吊销的历史行（deleted_at 非空）不应阻止新会话插入同一 session_id"""

    def test_吊销后再登录可成功(self, client, env):
        session = do_key_exchange(client)
        login(client, session, "alice", "Alice@TestPass1")
        # 主动触发一次吊销（refresh 内部先 revoke 再 create）
        resp = encrypted_request(
            client, session, "POST", "/api/v1/auth/refresh", {},
        )
        assert resp.status_code == 200, resp.text
        session.token = decrypt_response(session, resp)["token"]

        # 再登录一次，走的还是「revoke → create」同一 session_id 路径
        resp = encrypted_request(
            client, session, "POST", "/api/v1/auth/login",
            {"username": "alice", "password": "Alice@TestPass1"},
        )
        assert resp.status_code == 200, resp.text
        total, active = _count_sessions(session.session_id)
        assert total >= 2, "历史吊销行应保留（软删除语义）"
        assert active == 1, f"活跃行应恒为 1，实际 {active}"

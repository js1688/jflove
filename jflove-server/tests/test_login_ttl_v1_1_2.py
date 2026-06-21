"""
v1.1.4 登录 TTL 单元测试（继承自 v1.1.2，上限改为按天选项）

覆盖点：
  1. 客户端不传 requested_ttl_seconds → 服务端按默认 1 天（86400s）签发
  2. 客户端传 86400 / 604800 / 2592000 等按天选项值 → JWT exp 与请求一致（误差 ≤ 30s）
  3. 客户端传超过 2592000（30 天）的值 → 服务端 clamp 到 2592000，不报错
  4. 客户端传 0 / 负数 → 服务端 clamp 到下限 60，不报错
  5. 客户端传字符串数字 "86400" → 服务端正确解析
  6. 客户端传非数字字符串 → 服务端视为 None 走默认值，不报错
  7. 响应 expires_in 字段反映实际签发的 TTL
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from tests.conftest import (
    do_key_exchange, encrypted_request, decrypt_response,
)


def _decode_jwt_exp(token: str) -> float:
    """不验证签名地解码 JWT exp 字段（仅测试使用）"""
    parts = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    return float(payload["exp"])


def _do_login(client, session, username: str, password: str,
              requested_ttl_seconds=None):
    """执行加密登录，返回解密后的响应字典"""
    body = {"username": username, "password": password}
    if requested_ttl_seconds is not None:
        body["requested_ttl_seconds"] = requested_ttl_seconds
    resp = encrypted_request(
        client, session, "POST", "/api/v1/auth/login", body,
    )
    assert resp.status_code == 200, resp.text
    return decrypt_response(session, resp)


@pytest.fixture
def fresh_session(client):
    """每个用例独立的 key-exchange session（不复用 env 的 alice，因为 login 会吊销旧 session）"""
    return do_key_exchange(client)


class Test默认TTL向后兼容:
    """客户端不传 requested_ttl_seconds 时，服务端按默认 1 天（86400s）签发"""

    def test_不传ttl字段_签发1天JWT(self, client, env, fresh_session):
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1")
        exp = _decode_jwt_exp(data["token"])
        # 默认 86400s，允许 ±30s 误差
        assert abs(exp - (time.time() + 86400)) < 30
        assert data["expires_in"] == 86400


class Test合法TTL真生效:
    """客户端传合法值时，服务端按请求签发"""

    @pytest.mark.parametrize("ttl", [86400, 604800, 2592000])
    def test_合法TTL值按请求签发(self, client, env, fresh_session, ttl):
        """按天选项对应：1 天(86400)、7 天(604800)、30 天(2592000)"""
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds=ttl)
        exp = _decode_jwt_exp(data["token"])
        assert abs(exp - (time.time() + ttl)) < 30
        assert data["expires_in"] == ttl


class TestTTL边界clamp:
    """超界 / 非法值的 clamp 行为"""

    def test_超过上限被clamp到2592000(self, client, env, fresh_session):
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds=99999999)
        assert data["expires_in"] == 2592000
        exp = _decode_jwt_exp(data["token"])
        assert abs(exp - (time.time() + 2592000)) < 30

    def test_零值被clamp到下限60(self, client, env, fresh_session):
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds=0)
        assert data["expires_in"] == 60

    def test_负数被clamp到下限60(self, client, env, fresh_session):
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds=-100)
        assert data["expires_in"] == 60

    def test_刚好上限2592000正常(self, client, env, fresh_session):
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds=2592000)
        assert data["expires_in"] == 2592000

    def test_刚好下限60正常(self, client, env, fresh_session):
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds=60)
        assert data["expires_in"] == 60


class TestTTL类型容错:
    """非整数类型的容错处理"""

    def test_字符串数字被正确解析(self, client, env, fresh_session):
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds="86400")
        assert data["expires_in"] == 86400

    def test_非数字字符串视为None走默认(self, client, env, fresh_session):
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds="abc")
        assert data["expires_in"] == 86400

    def test_None显式传也走默认(self, client, env, fresh_session):
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds=None)
        assert data["expires_in"] == 86400


class Test响应字段:

    def test_响应包含expires_in字段(self, client, env, fresh_session):
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds=3600)
        assert "expires_in" in data
        assert isinstance(data["expires_in"], int)

    def test_expires_in与JWT_exp一致(self, client, env, fresh_session):
        """expires_in 应等于 JWT exp - iat 的差值，至少近似一致"""
        data = _do_login(client, fresh_session, "alice", "Alice@TestPass1",
                         requested_ttl_seconds=86400)
        # 服务端用同一个 effective_ttl 同时算 JWT 和响应字段
        exp = _decode_jwt_exp(data["token"])
        assert data["expires_in"] == 86400
        # JWT exp 应与 now + expires_in 接近
        assert abs(exp - (time.time() + data["expires_in"])) < 30


class TestTTL不破坏安全宪法:
    """v1.1.2 改动不应影响 §9 任何条款"""

    def test_login请求仍走加密信封(self, client, env, fresh_session):
        # 直接发明文请求应失败
        plaintext_body = {
            "username": "alice", "password": "alice123",
            "requested_ttl_seconds": 3600,
        }
        resp = client.post(
            "/api/v1/auth/login",
            json=plaintext_body,
            headers={"X-Session-ID": fresh_session.session_id},
        )
        # 服务端 decrypt_request_body 拒绝明文 → 400
        assert resp.status_code == 400

    def test_登录响应是加密信封(self, client, env, fresh_session):
        body = {"username": "alice", "password": "alice123",
                "requested_ttl_seconds": 1800}
        resp = encrypted_request(
            client, fresh_session, "POST", "/api/v1/auth/login", body,
        )
        # 响应必须是加密信封
        raw = resp.json()
        assert "nonce" in raw and "ciphertext" in raw

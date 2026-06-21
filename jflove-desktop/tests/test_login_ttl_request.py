"""
v1.1.2 桌面端 login() 注入 requested_ttl_seconds 单元测试

覆盖点：
  1. login() 传入 local_max_seconds → 请求 body 包含 requested_ttl_seconds
  2. login() 不传 local_max_seconds → 请求 body 不含 requested_ttl_seconds（向后兼容）
  3. local_max_seconds 是 int 类型上传
"""

from __future__ import annotations

from unittest.mock import patch

from src.services import auth_service
from src.utils.session import session_manager


def _fake_login_response() -> dict:
    """构造一个最小可用的登录响应字典（绕过实际网络）"""
    # 一个测试用 JWT（exp = 9999999999），仅供 _decode_token_exp 解码 exp 字段
    fake_token = (
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzI1NiJ9."
        # payload base64url：{"sub":"1","username":"alice","role":"user","exp":9999999999}
        "eyJzdWIiOiIxIiwidXNlcm5hbWUiOiJhbGljZSIsInJvbGUiOiJ1c2VyIiwiZXhwIjo5OTk5OTk5OTk5fQ."
        "fakefakefakesignaturefakefakefakefakefakefakefakefakefakefakefakefakefakefakefakefake"
    )
    return {
        "token": fake_token,
        "username": "alice",
        "role": "user",
        "user_id": 1,
        "expires_in": 7200,
    }


class TestLoginInjectsTtl:
    """login() 在向 http_client.post 传 payload 时是否正确注入 requested_ttl_seconds"""

    def setup_method(self) -> None:
        session_manager.clear()

    def teardown_method(self) -> None:
        session_manager.clear()

    def test_local_max_seconds传入时_payload包含requested_ttl_seconds(self):
        with patch("src.services.auth_service.http_client") as mock_client:
            mock_client.post.return_value = _fake_login_response()
            with patch("src.services.auth_service._save_session"):
                auth_service.login("alice", "pwd", local_max_seconds=7200)
        # 验证 http_client.post 被调用，payload 含字段
        assert mock_client.post.call_count == 1
        args, kwargs = mock_client.post.call_args
        endpoint = args[0]
        payload = args[1]
        assert endpoint == "/api/v1/auth/login"
        assert payload["username"] == "alice"
        assert payload["password"] == "pwd"
        assert payload["requested_ttl_seconds"] == 7200
        assert isinstance(payload["requested_ttl_seconds"], int)

    def test_local_max_seconds为None_payload不含requested_ttl_seconds(self):
        with patch("src.services.auth_service.http_client") as mock_client:
            mock_client.post.return_value = _fake_login_response()
            with patch("src.services.auth_service._save_session"):
                auth_service.login("alice", "pwd", local_max_seconds=None)
        args, kwargs = mock_client.post.call_args
        payload = args[1]
        assert "requested_ttl_seconds" not in payload

    def test_local_max_seconds默认值不传_payload不含字段(self):
        """不传 local_max_seconds 参数（向后兼容 v1.1.1 之前调用）"""
        with patch("src.services.auth_service.http_client") as mock_client:
            mock_client.post.return_value = _fake_login_response()
            with patch("src.services.auth_service._save_session"):
                auth_service.login("alice", "pwd")
        args, kwargs = mock_client.post.call_args
        payload = args[1]
        assert "requested_ttl_seconds" not in payload

    def test_local_max_seconds字符串数字会被强转(self):
        """容错：UI 通过 setData 拿到的字符串数字也能被正确转 int 上传"""
        with patch("src.services.auth_service.http_client") as mock_client:
            mock_client.post.return_value = _fake_login_response()
            with patch("src.services.auth_service._save_session"):
                auth_service.login("alice", "pwd", local_max_seconds="3600")
        args, kwargs = mock_client.post.call_args
        payload = args[1]
        assert payload["requested_ttl_seconds"] == 3600
        assert isinstance(payload["requested_ttl_seconds"], int)

    def test_login后session_manager同步更新本地上限(self):
        with patch("src.services.auth_service.http_client") as mock_client:
            mock_client.post.return_value = _fake_login_response()
            with patch("src.services.auth_service._save_session"):
                auth_service.login("alice", "pwd", local_max_seconds=7200)
        assert session_manager.local_session_max_seconds == 7200

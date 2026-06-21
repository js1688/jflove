"""
v1.1.1 HTTP 客户端 ECDH 静默续约单元测试

覆盖点：
  1. ECDH 类 401（detail 含特定串）触发自动续约 + 重发，最终成功
  2. JWT 类 401（detail 是"令牌无效或已过期"）不触发续约，直接抛 ApiError(401)
  3. 续约只发生 1 次：首发 401 → 续约 → 重发仍 401 → 抛 ApiError，不再续约
  4. 多线程并发 401：单飞机制保证 resync_session() 仅被调用一次
  5. _is_ecdh_session_error 字符串识别正确
  6. JWT 类 detail 不会被误识别为 ECDH 类
"""

from __future__ import annotations

import json
import threading
from unittest.mock import patch, MagicMock

import pytest

from src.utils.http_client import (
    HttpClient, ApiError, _is_ecdh_session_error,
)


def _build_encrypted_envelope_response(status: int, detail: str = "") -> MagicMock:
    """
    构造一个 mock 的 requests.Response，行为：
      - status_code = status
      - 含 X-Encrypted-Stream 等无关；
      - .json() 返回普通 dict（非加密信封）以便 _extract_error_detail 走"明文 detail"分支。
    """
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"detail": detail}
    resp.text = json.dumps({"detail": detail})
    return resp


def _build_success_response(payload: dict) -> MagicMock:
    """构造一个 200 响应，body 是普通 dict（避开加密信封解密路径，简化测试）"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.text = json.dumps(payload)
    return resp


class TestEcdhDetailIdentification:
    """ECDH 类 401 detail 识别"""

    def test_会话不存在或已过期被识别(self) -> None:
        assert _is_ecdh_session_error(
            "会话不存在或已过期，请重新交换密钥"
        )

    def test_会话已失效被识别(self) -> None:
        assert _is_ecdh_session_error("会话已失效")

    def test_缺少SessionID被识别(self) -> None:
        assert _is_ecdh_session_error("缺少 X-Session-ID 请求头")

    def test_JWT类detail不被识别(self) -> None:
        assert not _is_ecdh_session_error("令牌无效或已过期")
        assert not _is_ecdh_session_error("缺少认证令牌")

    def test_空字符串不被识别(self) -> None:
        assert not _is_ecdh_session_error("")


class TestSendWithAutoResync:
    """_send_with_auto_resync 行为验证"""

    def setup_method(self) -> None:
        # 每个用例使用独立 HttpClient 实例 + 重置类级单飞状态
        HttpClient._ecdh_resync_inflight = False
        self.client = HttpClient()

    def test_首发200不触发续约(self) -> None:
        send_fn = MagicMock(return_value=_build_success_response({"ok": True}))
        with patch.object(self.client, "_try_resync_ecdh") as mock_resync:
            resp = self.client._send_with_auto_resync(send_fn)
            assert resp.status_code == 200
            assert send_fn.call_count == 1
            mock_resync.assert_not_called()

    def test_ECDH类401触发续约后重发成功(self) -> None:
        first = _build_encrypted_envelope_response(
            401, "会话不存在或已过期，请重新交换密钥"
        )
        second = _build_success_response({"ok": True})
        send_fn = MagicMock(side_effect=[first, second])
        with patch.object(self.client, "_try_resync_ecdh", return_value=True) as mock_resync:
            resp = self.client._send_with_auto_resync(send_fn)
            assert resp.status_code == 200
            assert send_fn.call_count == 2
            mock_resync.assert_called_once()

    def test_JWT类401不触发续约_直接返回401(self) -> None:
        resp401 = _build_encrypted_envelope_response(401, "令牌无效或已过期")
        send_fn = MagicMock(return_value=resp401)
        with patch.object(self.client, "_try_resync_ecdh") as mock_resync:
            resp = self.client._send_with_auto_resync(send_fn)
            assert resp.status_code == 401
            assert send_fn.call_count == 1
            mock_resync.assert_not_called()

    def test_续约只发生一次_第二次仍401直接返回(self) -> None:
        # 首发 401 ECDH → 续约 → 重发仍 401 → 不再续约
        first = _build_encrypted_envelope_response(
            401, "会话不存在或已过期，请重新交换密钥"
        )
        second = _build_encrypted_envelope_response(
            401, "会话不存在或已过期，请重新交换密钥"
        )
        send_fn = MagicMock(side_effect=[first, second])
        with patch.object(self.client, "_try_resync_ecdh", return_value=True) as mock_resync:
            resp = self.client._send_with_auto_resync(send_fn)
            assert resp.status_code == 401
            assert send_fn.call_count == 2  # 只重发 1 次
            mock_resync.assert_called_once()  # 只续约 1 次

    def test_续约失败时仍重发一次(self) -> None:
        # 设计文档 §6.2：续约失败时直接重发让上层拿到真实失败
        first = _build_encrypted_envelope_response(
            401, "会话不存在或已过期，请重新交换密钥"
        )
        second = _build_encrypted_envelope_response(401, "会话不存在或已过期")
        send_fn = MagicMock(side_effect=[first, second])
        with patch.object(self.client, "_try_resync_ecdh", return_value=False):
            resp = self.client._send_with_auto_resync(send_fn)
            assert resp.status_code == 401
            assert send_fn.call_count == 2


class TestParseResponse401:
    """_parse_response 在 401 时抛 ApiError"""

    def setup_method(self) -> None:
        self.client = HttpClient()

    def test_401抛出ApiError并携带detail(self) -> None:
        resp = _build_encrypted_envelope_response(401, "令牌无效或已过期")
        with pytest.raises(ApiError) as exc_info:
            self.client._parse_response(resp)
        assert exc_info.value.status_code == 401
        assert "令牌" in exc_info.value.detail


class TestSingleFlightResync:
    """多线程并发触发 ECDH 401 时，resync_session 仅被调用一次（单飞）"""

    def setup_method(self) -> None:
        HttpClient._ecdh_resync_inflight = False
        self.client = HttpClient()

    def test_多线程并发只续约一次(self) -> None:
        """启动 5 个线程同时调用 _try_resync_ecdh，验证 resync_session 只被调用 1 次"""
        call_count = {"n": 0}
        call_lock = threading.Lock()
        # 模拟续约耗时，让其他线程有机会进入等待分支
        resync_started = threading.Event()
        resync_finish = threading.Event()

        def fake_resync() -> None:
            with call_lock:
                call_count["n"] += 1
                resync_started.set()
            # 等待 0.2s 内主线程让其他线程入队
            resync_finish.wait(timeout=2.0)

        results = []
        results_lock = threading.Lock()

        def worker() -> None:
            ok = self.client._try_resync_ecdh()
            with results_lock:
                results.append(ok)

        with patch("src.services.auth_service.resync_session", side_effect=fake_resync):
            threads = [threading.Thread(target=worker) for _ in range(5)]
            # 先启动一个线程，让它先进入"持有者"分支
            threads[0].start()
            # 等持有者真正开始 resync 才启动其他线程
            assert resync_started.wait(timeout=2.0)
            for t in threads[1:]:
                t.start()
            # 让持有者完成
            resync_finish.set()
            for t in threads:
                t.join(timeout=5.0)

        assert call_count["n"] == 1  # 仅一次实际续约
        assert len(results) == 5
        assert all(r is True for r in results)  # 所有线程都收到"成功"

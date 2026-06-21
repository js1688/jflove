"""
v1.1.1 会话过期判定单元测试

覆盖点：
  1. SessionManager.effective_expire_at() 在不同字段组合下的计算口径
  2. local_session_max_seconds 比 JWT exp 更短时，应当取本地上限
  3. JWT exp 更短时，取 JWT exp
  4. 字段缺失时返回 0
"""

from __future__ import annotations

import time

import pytest

from src.utils.session import SessionManager


@pytest.fixture
def sm() -> SessionManager:
    """每个用例使用全新的 SessionManager 状态（单例需手动 clear）"""
    instance = SessionManager()
    instance.clear()
    return instance


class TestEffectiveExpireAt:
    """SessionManager.effective_expire_at 行为验证"""

    def test_无任何字段时返回0(self, sm: SessionManager) -> None:
        sm.local_session_max_seconds = 0
        assert sm.effective_expire_at() == 0.0

    def test_仅JWT_exp时返回JWT_exp(self, sm: SessionManager) -> None:
        sm.token_expires_at = 9_000_000_000.0
        sm.local_session_max_seconds = 0  # 关闭本地上限
        assert sm.effective_expire_at() == 9_000_000_000.0

    def test_仅本地上限时返回上限值(self, sm: SessionManager) -> None:
        sm.token_expires_at = 0.0
        sm.key_exchange_time = 1_000_000.0
        sm.local_session_max_seconds = 3600
        assert sm.effective_expire_at() == 1_003_600.0

    def test_本地上限更短取本地(self, sm: SessionManager) -> None:
        # JWT 8 小时后过期，本地选 1 小时上限
        now = time.time()
        sm.token_expires_at = now + 8 * 3600
        sm.key_exchange_time = now
        sm.local_session_max_seconds = 3600
        result = sm.effective_expire_at()
        assert abs(result - (now + 3600)) < 1.0  # 本地上限胜出

    def test_JWT更短取JWT(self, sm: SessionManager) -> None:
        # JWT 30 分钟后过期，本地选 8 小时上限
        now = time.time()
        sm.token_expires_at = now + 1800
        sm.key_exchange_time = now
        sm.local_session_max_seconds = 28800
        result = sm.effective_expire_at()
        assert abs(result - (now + 1800)) < 1.0  # JWT 胜出

    def test_本地上限0表示禁用_只用JWT(self, sm: SessionManager) -> None:
        # local_session_max_seconds=0 表示不应用本地上限
        sm.token_expires_at = 9_000_000_000.0
        sm.key_exchange_time = 1_000_000.0
        sm.local_session_max_seconds = 0
        assert sm.effective_expire_at() == 9_000_000_000.0

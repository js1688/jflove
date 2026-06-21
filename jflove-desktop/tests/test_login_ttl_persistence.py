"""
v1.1.1 登录有效期持久化测试
v1.1.5 重构：改用 JSON 文件存储替代 QSettings

覆盖点：
  1. save / load_local_session_max_seconds 的 JSON 文件往返
  2. 不存在记录时返回默认值
  3. 损坏配置回退到默认值
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from src.config.settings import LOCAL_SESSION_TTL_DEFAULT
from src.services import auth_service


@pytest.fixture(autouse=True)
def _isolate_session_file(monkeypatch):
    """每个用例使用独立的临时 session.json 文件，避免串扰"""
    tmp_dir = tempfile.mkdtemp()
    tmp_file = os.path.join(tmp_dir, "session.json")
    monkeypatch.setattr("src.services.auth_service._SESSION_FILE", tmp_file)
    yield
    # 清理临时目录
    try:
        os.remove(tmp_file)
        os.rmdir(tmp_dir)
    except Exception:
        pass


class TestLocalSessionMaxSecondsPersistence:

    def test_未保存时load返回默认值(self) -> None:
        assert auth_service.load_local_session_max_seconds() == LOCAL_SESSION_TTL_DEFAULT

    def test_save后load能拿到原值(self) -> None:
        auth_service.save_local_session_max_seconds(7200)
        assert auth_service.load_local_session_max_seconds() == 7200

    def test_save后直接读文件验证值(self) -> None:
        auth_service.save_local_session_max_seconds(28800)
        # 直接读 JSON 文件验证类型
        with open(auth_service._SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["local_session_max_seconds"] == 28800
        assert isinstance(data["local_session_max_seconds"], int)

    def test_损坏值回退默认(self) -> None:
        # 直接写一个损坏的 JSON
        os.makedirs(os.path.dirname(auth_service._SESSION_FILE), exist_ok=True)
        with open(auth_service._SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({"local_session_max_seconds": "not-a-number"}, f)
        assert auth_service.load_local_session_max_seconds() == LOCAL_SESSION_TTL_DEFAULT

    def test_non_existent_file_returns_default(self) -> None:
        """文件不存在时返回默认值"""
        assert auth_service.load_local_session_max_seconds() == LOCAL_SESSION_TTL_DEFAULT

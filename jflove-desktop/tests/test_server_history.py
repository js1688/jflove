"""
v1.1.4 服务端地址历史持久化单元测试

覆盖点：
  1. record() 写入新地址并置顶
  2. record() 去重（相同地址不重复）
  3. list_history() / get_default() 行为
  4. delete() 删除指定地址
  5. 上限 10 条淘汰逻辑
  6. 空列表兜底返回 _FALLBACK_URL
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services import server_history_service


@pytest.fixture(autouse=True)
def _reset_history():
    """每个测试前后清空内存状态，避免串扰"""
    # 用空列表覆盖 _load_raw，模拟无磁盘数据
    with patch.object(server_history_service, "_load_raw", return_value=[]):
        yield


class TestServerHistoryRecord:

    def test_record_空列表写入并置顶(self):
        """首次 record 写入空列表，返回包含该地址的单元素列表"""
        with patch.object(server_history_service, "_load_raw", return_value=[]), \
             patch.object(server_history_service, "_save_raw") as mock_save:
            server_history_service.record("http://example.com:8989")
            mock_save.assert_called_once()
            saved = mock_save.call_args[0][0]
            assert saved == ["http://example.com:8989"]

    def test_record_去重_已有地址置顶(self):
        """已有列表包含该地址时，record 后移动到第一位"""
        existing = ["http://b.com", "http://a.com", "http://c.com"]
        with patch.object(server_history_service, "_load_raw", return_value=existing[:]), \
             patch.object(server_history_service, "_save_raw") as mock_save:
            server_history_service.record("http://a.com")
            saved = mock_save.call_args[0][0]
            assert saved[0] == "http://a.com"
            assert len(saved) == 3  # 总数不变

    def test_record_新地址置顶_旧顺序保持(self):
        """新增地址置顶，原有顺序不变"""
        existing = ["http://b.com", "http://a.com"]
        with patch.object(server_history_service, "_load_raw", return_value=existing[:]), \
             patch.object(server_history_service, "_save_raw") as mock_save:
            server_history_service.record("http://c.com")
            saved = mock_save.call_args[0][0]
            assert saved == ["http://c.com", "http://b.com", "http://a.com"]

    def test_record_空字符串忽略(self):
        """空 URL 不写入历史"""
        with patch.object(server_history_service, "_load_raw", return_value=[]), \
             patch.object(server_history_service, "_save_raw") as mock_save:
            server_history_service.record("")
            mock_save.assert_not_called()

    def test_record_去尾部斜杠规范化(self):
        """末尾斜杠被 strip 后与已有地址去重"""
        existing = ["http://example.com:8989"]
        with patch.object(server_history_service, "_load_raw", return_value=existing[:]), \
             patch.object(server_history_service, "_save_raw") as mock_save:
            server_history_service.record("http://example.com:8989/")
            saved = mock_save.call_args[0][0]
            # 规范化后去重，总数仍为 1
            assert len(saved) == 1
            assert saved[0] == "http://example.com:8989"

    def test_record_上限10条_超出淘汰最旧(self):
        """列表满 10 条后再写入，最旧的被淘汰"""
        existing = [f"http://host{i}.com" for i in range(10)]  # host0..host9
        with patch.object(server_history_service, "_load_raw", return_value=existing[:]), \
             patch.object(server_history_service, "_save_raw") as mock_save:
            server_history_service.record("http://new.com")
            saved = mock_save.call_args[0][0]
            assert len(saved) == 10
            assert saved[0] == "http://new.com"
            # new 置顶后截断 10 条：new + host0..host8，host9 被淘汰
            assert "http://host9.com" not in saved
            assert "http://host0.com" in saved


class TestServerHistoryList:

    def test_list_history_正常列表(self):
        """正常返回历史列表"""
        existing = ["http://a.com", "http://b.com"]
        with patch.object(server_history_service, "_load_raw", return_value=existing[:]):
            result = server_history_service.list_history()
            assert result == existing

    def test_list_history_空列表(self):
        """无历史时返回空列表"""
        with patch.object(server_history_service, "_load_raw", return_value=[]):
            result = server_history_service.list_history()
            assert result == []

    def test_get_default_有历史返回第一项(self):
        """历史非空时，get_default 返回第一项（最近使用的）"""
        existing = ["http://latest.com", "http://old.com"]
        with patch.object(server_history_service, "_load_raw", return_value=existing[:]):
            result = server_history_service.get_default()
            assert result == "http://latest.com"

    def test_get_default_无历史返回兜底地址(self):
        """历史为空时，get_default 返回 http://localhost:8989"""
        with patch.object(server_history_service, "_load_raw", return_value=[]):
            result = server_history_service.get_default()
            assert result == "http://localhost:8989"


class TestServerHistoryDelete:

    def test_delete_存在的地址(self):
        """删除列表中存在的地址"""
        existing = ["http://a.com", "http://b.com", "http://c.com"]
        with patch.object(server_history_service, "_load_raw", return_value=existing[:]), \
             patch.object(server_history_service, "_save_raw") as mock_save:
            server_history_service.delete("http://b.com")
            mock_save.assert_called_once()
            saved = mock_save.call_args[0][0]
            assert "http://b.com" not in saved
            assert len(saved) == 2

    def test_delete_不存在的地址静默忽略(self):
        """删除不存在的地址不写入磁盘"""
        existing = ["http://a.com", "http://b.com"]
        with patch.object(server_history_service, "_load_raw", return_value=existing[:]), \
             patch.object(server_history_service, "_save_raw") as mock_save:
            server_history_service.delete("http://nonexistent.com")
            mock_save.assert_not_called()

    def test_delete_空字符串忽略(self):
        """空字符串不触发删除"""
        with patch.object(server_history_service, "_load_raw", return_value=[]), \
             patch.object(server_history_service, "_save_raw") as mock_save:
            server_history_service.delete("")
            mock_save.assert_not_called()

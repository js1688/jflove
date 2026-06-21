"""
v1.1.1 登出互斥测试

覆盖点：
  1. MainWindow._on_logout_requested 多次调用只触发一次实际 logout + 信号 emit
  2. _logout_in_flight 在 _check_token_expiry 触发时与外部触发共享互斥
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from src.utils.session import session_manager


@pytest.fixture(autouse=True)
def _reset_session(qapp):
    session_manager.clear()
    yield
    session_manager.clear()


def _make_main_window():
    """构造一个最小可用的 MainWindow 实例（需要 QApplication）"""
    from src.ui.main_window import MainWindow
    # MainWindow.__init__ 会构建一堆页面，需要 session_manager 有用户名
    session_manager.username = "tester"
    session_manager.role = "user"
    session_manager.token = "fake.jwt.token"
    session_manager.token_expires_at = 9_000_000_000.0  # 远期，避免 _check_token_expiry 立即触发
    win = MainWindow(role="user")
    return win


class TestLogoutMutex:

    def test_重复触发on_logout_requested只生效一次(self):
        win = _make_main_window()
        try:
            slot = MagicMock()
            win.logout_requested.connect(slot)
            with patch("src.services.auth_service.logout") as mock_logout:
                win._on_logout_requested()
                win._on_logout_requested()
                win._on_logout_requested()
            assert mock_logout.call_count == 1
            assert slot.call_count == 1
            assert win._logout_in_flight is True
        finally:
            win.deleteLater()

    def test_首次触发后再次进入_check_token_expiry不再弹窗(self):
        """
        模拟 token 已过期场景下定时器多次触发。

        v1.1.4 行为变更：_check_token_expiry 中 MessageBox 后改用
        QTimer.singleShot(0, _perform_expired_logout) 延迟执行登出。
        因此第一次 _check_token_expiry 后 _logout_in_flight 已置位但
        logout_requested 信号尚未发射（需手动调用 _perform_expired_logout 模拟）。
        """
        win = _make_main_window()
        try:
            # 让 effective_expire_at 立即返回一个过去时间
            session_manager.token_expires_at = 1.0
            session_manager.local_session_max_seconds = 0  # 关闭本地上限干扰
            session_manager.key_exchange_time = 0.0
            slot = MagicMock()
            win.logout_requested.connect(slot)

            calls = {"box": 0}

            def fake_msgbox(*args, **kwargs):
                """伪造 MessageBox：构造时计数，exec 立即返回"""
                calls["box"] += 1
                inst = MagicMock()
                inst.exec = MagicMock(return_value=0)
                return inst

            with patch("src.ui.main_window.MessageBox", side_effect=fake_msgbox), \
                 patch("src.services.auth_service.logout"):
                win._check_token_expiry()
                win._check_token_expiry()
                win._check_token_expiry()
            assert calls["box"] == 1  # 弹窗只构造一次
            assert win._logout_in_flight is True  # 互斥标志已置位
            # v1.1.4: 登出已推迟到 QTimer.singleShot(0)，信号尚未发射
            # 手动调用 _perform_expired_logout 模拟定时器触发
            win._perform_expired_logout()
            assert slot.call_count == 1  # 信号只发射一次
        finally:
            win.deleteLater()

    def test_perform_expired_logout_emits_signal(self):
        """_perform_expired_logout 应调用 _do_logout → 发射 logout_requested 信号"""
        win = _make_main_window()
        try:
            slot = MagicMock()
            win.logout_requested.connect(slot)
            with patch("src.services.auth_service.logout"):
                win._perform_expired_logout()
            assert slot.call_count == 1
        finally:
            win.deleteLater()

    def test_closeEvent_在登出流程中接受关闭事件(self):
        """
        v1.1.4 新增：登出流程中的 closeEvent 应接受事件（真正销毁窗口），
        而非忽略并隐藏到托盘。
        """
        from PySide6.QtGui import QCloseEvent
        win = _make_main_window()
        try:
            win._logout_in_flight = True
            event = QCloseEvent()
            with patch.object(win._tray, "hide") as mock_hide, \
                 patch.object(win._tray, "deleteLater") as mock_delete:
                win.closeEvent(event)
            assert event.isAccepted()  # 必须接受事件
            mock_hide.assert_called_once()
            mock_delete.assert_called_once()
        finally:
            win.deleteLater()

    def test_closeEvent_在正常使用中忽略关闭事件(self):
        """
        v1.1.4 新增：正常使用中的 closeEvent 应忽略事件（隐藏到托盘），
        而非接受并销毁窗口。
        """
        from PySide6.QtGui import QCloseEvent
        win = _make_main_window()
        try:
            # 确保 _logout_in_flight 为 False
            assert win._logout_in_flight is False
            event = QCloseEvent()
            win.closeEvent(event)
            assert not event.isAccepted()  # 必须忽略事件
        finally:
            win.deleteLater()

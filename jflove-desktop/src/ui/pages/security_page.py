"""
安全状态页面

显示当前会话加密状态、密钥交换时间，提供手动刷新密钥功能。
"""

import time
import datetime

from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QTimer

from qfluentwidgets import (
    PrimaryPushButton, SubtitleLabel, BodyLabel, CardWidget,
    InfoBar, InfoBarPosition, FluentIcon as FIF,
)

from src.services import auth_service
from src.utils.session import session_manager
from src.utils.worker import Worker
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SecurityPage(QWidget):
    """安全状态与密钥管理页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("securityPage")
        self._worker = None
        self._setup_ui()

        # 每 30 秒刷新一次显示的时间信息
        self._refresh_timer = QTimer()
        self._refresh_timer.setInterval(30_000)
        self._refresh_timer.timeout.connect(self._update_status)
        self._refresh_timer.start()

    # ── UI 构建 ────────────────────────────────────────

    def _setup_ui(self) -> None:
        """构建页面 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignTop)

        layout.addWidget(SubtitleLabel("连接与安全"))

        # ── 会话状态卡片 ──
        session_card = CardWidget()
        session_layout = QVBoxLayout(session_card)
        session_layout.setSpacing(10)

        session_layout.addWidget(BodyLabel("会话状态"))

        self._session_status_label = BodyLabel("─")
        session_layout.addWidget(self._session_status_label)

        self._session_id_label = BodyLabel("─")
        session_layout.addWidget(self._session_id_label)

        self._key_time_label = BodyLabel("─")
        session_layout.addWidget(self._key_time_label)

        layout.addWidget(session_card)

        # ── 用户信息卡片 ──
        user_card = CardWidget()
        user_layout = QVBoxLayout(user_card)
        user_layout.setSpacing(10)

        user_layout.addWidget(BodyLabel("当前用户"))

        self._username_label = BodyLabel("─")
        user_layout.addWidget(self._username_label)

        self._role_label = BodyLabel("─")
        user_layout.addWidget(self._role_label)

        layout.addWidget(user_card)

        # ── 操作卡片 ──
        action_card = CardWidget()
        action_layout = QVBoxLayout(action_card)
        action_layout.setSpacing(10)

        action_layout.addWidget(BodyLabel("密钥管理"))

        hint = BodyLabel("重新交换会话密钥，确保本次连接安全。原会话密钥立即失效。")
        hint.setWordWrap(True)
        action_layout.addWidget(hint)

        self._refresh_key_btn = PrimaryPushButton(FIF.SYNC, "刷新会话密钥")
        self._refresh_key_btn.clicked.connect(self._on_refresh_key)
        action_layout.addWidget(self._refresh_key_btn)

        layout.addWidget(action_card)
        layout.addStretch()

        # 初始化显示
        self._update_status()

    # ── 状态更新 ───────────────────────────────────────

    def _update_status(self) -> None:
        """从 session_manager 读取当前状态并刷新 UI"""
        if session_manager.is_session_ready():
            # 截取 session_id 前 8 位显示
            short_id = session_manager.session_id[:8] + "..."
            self._session_status_label.setText("✅ 会话已加密（ChaCha20-Poly1305）")
            self._session_id_label.setText(f"Session ID：{short_id}")

            if session_manager.key_exchange_time > 0:
                ts = datetime.datetime.fromtimestamp(
                    session_manager.key_exchange_time
                ).strftime("%Y-%m-%d %H:%M:%S")
                elapsed = int(time.time() - session_manager.key_exchange_time)
                self._key_time_label.setText(
                    f"密钥交换时间：{ts}（已持续 {elapsed // 60} 分钟）"
                )
        else:
            self._session_status_label.setText("❌ 未建立加密会话")
            self._session_id_label.setText("Session ID：─")
            self._key_time_label.setText("密钥交换时间：─")

        if session_manager.is_logged_in():
            self._username_label.setText(f"用户名：{session_manager.username}")
            role_cn = "管理员" if session_manager.is_admin() else "普通用户"
            self._role_label.setText(f"角色：{role_cn}")
        else:
            self._username_label.setText("用户名：─")
            self._role_label.setText("角色：─")

    def _on_refresh_key(self) -> None:
        """刷新会话密钥"""
        self._refresh_key_btn.setEnabled(False)

        self._worker = Worker(auth_service.refresh_key_exchange)
        self._worker.finished.connect(self._on_key_refreshed)
        self._worker.error.connect(self._on_key_refresh_error)
        self._worker.start()

    def _on_key_refreshed(self, _) -> None:
        """密钥刷新成功"""
        self._refresh_key_btn.setEnabled(True)
        self._update_status()
        InfoBar.success("成功", "会话密钥已刷新，后续通信使用新密钥",
                        parent=self, duration=3000, position=InfoBarPosition.TOP_RIGHT)
        logger.info("会话密钥手动刷新成功")

    def _on_key_refresh_error(self, msg: str) -> None:
        """密钥刷新失败"""
        self._refresh_key_btn.setEnabled(True)
        InfoBar.error("刷新失败", f"密钥刷新失败：{msg}",
                      parent=self, duration=4000, position=InfoBarPosition.TOP_RIGHT)
        logger.error("会话密钥刷新失败: %s", msg)

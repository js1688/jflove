"""
主窗口

登录成功后展示的主界面，基于 FluentWindow 构建带侧边导航的窗口。

导航项目：
  - 文档管理（全角色）
  - 笔记管理（全角色）
  - 同步目录（全角色）
  - 传输任务（全角色）
  - 用户管理（管理员）
  - 磁盘管理（管理员）
  - 权限配置（管理员）
  - 安全状态（底部，全角色）
  - 设置（底部，全角色）
"""

import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon as FIF,
    MessageBox,
)

from src.ui.pages.file_page import FilePage
from src.ui.pages.note_page import NotePage
from src.ui.pages.repair_center_page import RepairCenterPage
from src.ui.pages.security_page import SecurityPage
from src.ui.pages.settings_page import SettingsPage
from src.ui.pages.sync_page import SyncPage
from src.ui.pages.transfer_page import TransferPage
from src.ui.pages.admin.user_page import UserPage
from src.ui.pages.admin.disk_page import DiskPage
from src.ui.pages.admin.permission_page import PermissionPage
from src.utils.icon import get_app_icon
from src.utils.session import session_manager
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MainWindow(FluentWindow):
    """
    主窗口。

    :param role: 当前登录用户角色（admin / user），决定导航项可见性
    :signal logout_requested: 用户请求退出登录
    """

    logout_requested = Signal()

    def __init__(self, role: str = "user"):
        super().__init__()
        self._role = role
        # v1.1.1：登出互斥标志，避免 token 失效时被多线程/多回调重复触发产生
        # 多个"登录已过期"对话框或多个登录窗口
        self._logout_in_flight: bool = False
        self._setup_pages()
        self._setup_navigation()
        self._setup_window()
        self._setup_tray()
        self._setup_token_check()
        self._load_initial_data()

    # ── 初始化 ────────────────────────────────────────

    def _setup_pages(self) -> None:
        """创建所有功能页面实例"""
        self.file_page = FilePage(self)
        self.note_page = NotePage(self)
        self.sync_page = SyncPage(self)
        self.transfer_page = TransferPage(self)
        # v1.4.2：修复中心（全平台共享任务列表，所有登录用户可见）
        self.repair_center_page = RepairCenterPage(self)
        self.security_page = SecurityPage(self)
        self.settings_page = SettingsPage(self)

        if self._role == "admin":
            self.user_page = UserPage(self)
            self.disk_page = DiskPage(self)
            self.permission_page = PermissionPage(self)

    def _setup_navigation(self) -> None:
        """根据角色配置侧边导航栏"""
        # ── 主功能区 ──
        self.addSubInterface(
            self.file_page, FIF.FOLDER, "文档管理",
            position=NavigationItemPosition.SCROLL,
        )
        self.addSubInterface(
            self.note_page, FIF.EDIT, "笔记管理",
            position=NavigationItemPosition.SCROLL,
        )
        self.addSubInterface(
            self.sync_page, FIF.SYNC, "同步目录",
            position=NavigationItemPosition.SCROLL,
        )
        self.addSubInterface(
            self.transfer_page, FIF.SEND, "传输任务",
            position=NavigationItemPosition.SCROLL,
        )
        self.addSubInterface(
            self.repair_center_page, FIF.ROBOT, "修复中心",
            position=NavigationItemPosition.SCROLL,
        )

        # ── 管理员专属 ──
        if self._role == "admin":
            self.navigationInterface.addSeparator(
                position=NavigationItemPosition.SCROLL
            )
            self.addSubInterface(
                self.user_page, FIF.PEOPLE, "用户管理",
                position=NavigationItemPosition.SCROLL,
            )
            self.addSubInterface(
                self.disk_page, FIF.CLOUD, "磁盘管理",
                position=NavigationItemPosition.SCROLL,
            )
            self.addSubInterface(
                self.permission_page, FIF.VPN, "权限配置",
                position=NavigationItemPosition.SCROLL,
            )

        # ── 底部固定项 ──
        self.addSubInterface(
            self.security_page, FIF.CERTIFICATE, "安全状态",
            position=NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            self.settings_page, FIF.SETTING, "设置",
            position=NavigationItemPosition.BOTTOM,
        )

    def _setup_window(self) -> None:
        """配置窗口基本属性"""
        username = session_manager.username
        role_cn = "管理员" if self._role == "admin" else "用户"
        self.setWindowTitle(f"JFLove  —  {username}（{role_cn}）")
        self.setWindowIcon(get_app_icon())
        self.resize(1100, 700)
        self.setMinimumSize(900, 600)

    def _setup_tray(self) -> None:
        """配置系统托盘图标，关闭窗口时最小化到托盘"""
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(get_app_icon())
        self._tray.setToolTip("JFLove")

        tray_menu = QMenu()
        show_action = tray_menu.addAction("显示窗口")
        show_action.triggered.connect(self._restore_window)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("退出")
        quit_action.triggered.connect(QApplication.quit)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _setup_token_check(self) -> None:
        """启动 token 过期检测定时器，每 60 秒检查一次"""
        self._token_timer = QTimer(self)
        self._token_timer.setInterval(60_000)
        self._token_timer.timeout.connect(self._check_token_expiry)
        self._token_timer.start()
        # 连接设置页退出登录信号
        self.settings_page.logout_requested.connect(self._on_logout_requested)

    def _check_token_expiry(self) -> None:
        """
        检查会话是否已到期；以"实际生效失效时间"为准（v1.1.1）：
            min(JWT exp, key_exchange_time + local_session_max_seconds)

        到期触发唯一一次登出流程；后续定时器 tick 与并发请求触发的
        登出请求都会被 _logout_in_flight 拦截。

        v1.1.4 修复：MessageBox 弹窗后使用 QTimer.singleShot(0, ...) 延迟执行登出，
        跳出定时器回调上下文。这是修复"双主窗口"Bug 的关键——在 QTimer.timeout 回调
        内直接销毁窗口会导致 Qt 无法正确处理 closeEvent 并留下残留实例。
        """
        if self._logout_in_flight:
            return
        effective = session_manager.effective_expire_at()
        if effective and time.time() >= effective:
            logger.info("会话到期（effective_expire_at=%.0f），自动退出登录", effective)
            self._token_timer.stop()
            # 先抢占互斥标志，再弹窗；MessageBox.exec() 期间事件循环仍在转，
            # 必须保证此期间任何"二次到期触发"都被拦截
            self._logout_in_flight = True
            box = MessageBox(
                "登录已过期",
                "您的登录凭证已过期，请重新登录。",
                self,
            )
            box.exec()
            # 延迟执行：跳出定时器回调上下文，确保 MessageBox 完全关闭后
            # 再触发登出流程，此时 Qt 可安全销毁 MainWindow
            QTimer.singleShot(0, self._perform_expired_logout)

    def _perform_expired_logout(self) -> None:
        """
        从过期定时器触发的登出（v1.1.4 新增）。

        与 _do_logout 功能相同，但被 QTimer.singleShot(0, ...) 延迟调用，
        确保不在 QTimer.timeout 回调链内销毁窗口，避免"双主窗口"Bug。
        """
        self._do_logout()

    def _on_logout_requested(self) -> None:
        """
        外部（设置页"退出登录"按钮 / 401 异常路径等）触发的登出。

        与 _check_token_expiry 共用 _logout_in_flight 互斥，确保整个进程内
        只会执行一次实际的登出动作 + 只发射一次 logout_requested 信号。
        """
        if self._logout_in_flight:
            return
        self._logout_in_flight = True
        self._token_timer.stop()
        self._do_logout()

    def _do_logout(self) -> None:
        """实际执行清理 + 通知 main.py 切换到登录窗的内部入口（必须在互斥下调用）"""
        from src.services import auth_service
        auth_service.logout()
        self.logout_requested.emit()

    def _load_initial_data(self) -> None:
        """主窗口展示后预加载各页面数据"""
        # 文档管理：先加载磁盘列表
        self.file_page.load_disks()
        # 笔记管理：加载笔记列表
        self.note_page.load_notes()
        # 同步目录：加载用户的同步配置
        self.sync_page.load_configs()
        # 设置页面：所有用户均加载笔记磁盘配置
        self.settings_page.load_system_config()
        # 管理员：预加载数据
        if self._role == "admin":
            self.user_page.load_users()
            self.disk_page.load_disks()
            self.permission_page.load_data()

    # ── 托盘事件 ──────────────────────────────────────

    def _restore_window(self) -> None:
        """从托盘恢复窗口"""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """双击托盘图标恢复窗口"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_window()

    # ── 窗口关闭 ──────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        """
        关闭事件处理（v1.1.4 修正）。

        - 登出流程中（_logout_in_flight == True）：接受事件，真正销毁窗口并清理托盘
        - 普通关闭（用户点击 X）：忽略事件，最小化到系统托盘
        """
        if self._logout_in_flight:
            # 登出流程：彻底关闭并销毁窗口，清理托盘图标
            self._tray.hide()
            self._tray.deleteLater()
            event.accept()
            return
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "JFLove",
            "程序已最小化到系统托盘，双击图标可恢复窗口。",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )
        logger.info("窗口已最小化到系统托盘")

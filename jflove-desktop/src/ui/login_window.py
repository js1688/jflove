"""
登录/初始化窗口

应用启动时的第一个窗口，处理：
  1. 用户输入服务端地址并连接（执行密钥交换）
  2. 若系统无管理员：显示管理员账号创建表单
  3. 若已有管理员：显示用户登录表单

连接成功后发射 login_success 信号，由 main.py 切换到主窗口。
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
)
from PySide6.QtCore import Qt, Signal

from qfluentwidgets import (
    LineEdit, PasswordLineEdit, PrimaryPushButton, PushButton,
    SubtitleLabel, BodyLabel, TitleLabel, InfoBar, InfoBarPosition,
    CardWidget, EditableComboBox, ComboBox, ToolButton, FluentIcon as FIF,
)

from src.config.settings import (
    APP_VERSION, LOCAL_SESSION_TTL_OPTIONS, LOCAL_SESSION_TTL_DEFAULT,
)
from src.services import auth_service, server_history_service
from src.utils.worker import Worker
from src.utils.icon import get_app_icon
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 页面索引常量
_PAGE_SERVER = 0     # 输入服务端地址
_PAGE_INIT_ADMIN = 1  # 创建管理员
_PAGE_LOGIN = 2       # 用户登录


class LoginWindow(QWidget):
    """
    登录/初始化窗口。

    :signal login_success: 登录成功，携带用户角色字符串（admin/user）
    """

    login_success = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("JFLove")
        self.setWindowIcon(get_app_icon())
        self.setMinimumSize(480, 520)
        self.setWindowFlags(Qt.Window)
        self._worker = None
        self._setup_ui()
        # 启动后尝试免登录恢复（延迟 100ms 待窗口渲染完成后执行）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self._try_auto_login)

    # ── UI 构建 ────────────────────────────────────────

    def _setup_ui(self) -> None:
        """构建整体布局"""
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        outer.setContentsMargins(60, 40, 60, 40)

        # Logo 区域
        logo_label = TitleLabel("JFLove")
        logo_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(logo_label)

        desc = BodyLabel("私有文档 & 笔记管理系统")
        desc.setAlignment(Qt.AlignCenter)
        outer.addWidget(desc)
        outer.addSpacing(30)

        # 卡片容器
        card = CardWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(0)

        # 分页容器
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_server_page())    # 0
        self._stack.addWidget(self._build_init_admin_page())  # 1
        self._stack.addWidget(self._build_login_page())      # 2
        card_layout.addWidget(self._stack)
        outer.addWidget(card)

        # 版本号
        outer.addSpacing(16)
        ver = BodyLabel(f"v{APP_VERSION}")
        ver.setAlignment(Qt.AlignCenter)
        outer.addWidget(ver)

    def _build_server_page(self) -> QWidget:
        """构建服务端地址输入页（可编辑下拉，历史地址来自本地缓存）"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        layout.addWidget(SubtitleLabel("连接服务端"))
        layout.addWidget(BodyLabel("请输入 JFLove 服务端地址（或从历史中选择）："))

        # 可编辑下拉：列出历史地址，默认值取上次成功连接的地址
        # v1.1.4：右侧添加删除按钮，允许用户从历史中移除不需要的地址
        url_row = QHBoxLayout()
        url_row.setSpacing(6)
        self._server_url_input = EditableComboBox()
        self._server_url_input.setPlaceholderText("http://localhost:8989")
        self._refresh_server_history()
        url_row.addWidget(self._server_url_input, 1)

        self._delete_history_btn = ToolButton(FIF.DELETE)
        self._delete_history_btn.setToolTip("从历史中删除当前地址")
        self._delete_history_btn.clicked.connect(self._on_delete_history)
        # 没有历史记录时禁用删除按钮
        self._delete_history_btn.setEnabled(
            self._server_url_input.count() > 0
        )
        url_row.addWidget(self._delete_history_btn)

        layout.addLayout(url_row)

        self._connect_btn = PrimaryPushButton("连接")
        self._connect_btn.clicked.connect(self._on_connect)
        layout.addWidget(self._connect_btn)

        layout.addStretch()
        return page

    def _refresh_server_history(self) -> None:
        """从本地缓存加载历史地址，刷新下拉项并把首项设为默认值"""
        history = server_history_service.list_history()
        default_url = server_history_service.get_default()
        # 重建下拉项，避免重复
        self._server_url_input.clear()
        if history:
            self._server_url_input.addItems(history)
        # 显示默认值（编辑框中可见的文字）
        self._server_url_input.setCurrentText(default_url)
        # 刷新删除按钮状态
        if hasattr(self, '_delete_history_btn'):
            self._delete_history_btn.setEnabled(self._server_url_input.count() > 0)

    def _on_delete_history(self) -> None:
        """删除当前下拉框中选中的历史地址（v1.1.4 新增）"""
        current_text = self._server_url_input.currentText().strip()
        if not current_text:
            return
        # 在历史记录中查找并删除
        history = server_history_service.list_history()
        if current_text not in history:
            InfoBar.warning("提示", "当前地址不在历史记录中", parent=self,
                            duration=2000, position=InfoBarPosition.TOP)
            return
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("删除历史地址")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(f"确定要从历史中删除「{current_text}」吗？"))
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)
        if dlg.exec():
            server_history_service.delete(current_text)
            self._refresh_server_history()
            InfoBar.success("已删除", f"已从历史中移除「{current_text}」", parent=self,
                            duration=2000, position=InfoBarPosition.TOP)
            logger.info("用户删除了历史地址: %s", current_text)

    def _build_init_admin_page(self) -> QWidget:
        """构建管理员初始化页"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        layout.addWidget(SubtitleLabel("创建管理员账号"))
        hint = BodyLabel("管理员账号是系统的唯一超级用户，仅限创建一个。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._admin_username_input = LineEdit()
        self._admin_username_input.setPlaceholderText("用户名（3-20 个字符）")
        layout.addWidget(self._admin_username_input)

        self._admin_password_input = PasswordLineEdit()
        self._admin_password_input.setPlaceholderText("密码（至少 8 个字符）")
        layout.addWidget(self._admin_password_input)

        self._admin_confirm_input = PasswordLineEdit()
        self._admin_confirm_input.setPlaceholderText("确认密码")
        layout.addWidget(self._admin_confirm_input)

        self._init_admin_btn = PrimaryPushButton("创建管理员")
        self._init_admin_btn.clicked.connect(self._on_init_admin)
        layout.addWidget(self._init_admin_btn)

        back_btn = PushButton("返回")
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(_PAGE_SERVER))
        layout.addWidget(back_btn)

        layout.addStretch()
        return page

    def _build_login_page(self) -> QWidget:
        """构建用户登录页"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        layout.addWidget(SubtitleLabel("登录 JFLove"))
        note = BodyLabel("所有通信均经过加密，连接后自动交换临时会话密钥。")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._login_username_input = LineEdit()
        self._login_username_input.setPlaceholderText("用户名")
        layout.addWidget(self._login_username_input)

        self._login_password_input = PasswordLineEdit()
        self._login_password_input.setPlaceholderText("密码")
        layout.addWidget(self._login_password_input)

        # v1.1.2 布局优化：登录有效期标签 + 下拉合并到同一行（HBox）
        ttl_row = QHBoxLayout()
        ttl_row.setSpacing(10)
        ttl_label = BodyLabel("登录有效期")
        ttl_label.setMinimumWidth(80)
        ttl_row.addWidget(ttl_label)
        self._ttl_combo = ComboBox()
        for seconds, label in LOCAL_SESSION_TTL_OPTIONS:
            self._ttl_combo.addItem(label, userData=seconds)
        # 还原上次选择
        last_seconds = auth_service.load_local_session_max_seconds()
        idx_to_select = 0
        for i, (seconds, _label) in enumerate(LOCAL_SESSION_TTL_OPTIONS):
            if seconds == last_seconds:
                idx_to_select = i
                break
        else:
            # 找不到匹配项（比如用户偏好被清空），回退默认
            for i, (seconds, _label) in enumerate(LOCAL_SESSION_TTL_OPTIONS):
                if seconds == LOCAL_SESSION_TTL_DEFAULT:
                    idx_to_select = i
                    break
        self._ttl_combo.setCurrentIndex(idx_to_select)
        # stretch=1：下拉占满标签右侧的剩余宽度
        ttl_row.addWidget(self._ttl_combo, 1)
        layout.addLayout(ttl_row)

        # v1.1.2 布局优化：返回 + 登录按钮合并到同一行
        # 返回为次按钮（左、固定窄宽）；登录为主按钮（右、stretch 占大头）
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        back_btn = PushButton("返回")
        back_btn.setFixedWidth(96)
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(_PAGE_SERVER))
        btn_row.addWidget(back_btn)
        self._login_btn = PrimaryPushButton("登录")
        self._login_btn.clicked.connect(self._on_login)
        # 回车键触发登录
        self._login_password_input.returnPressed.connect(self._on_login)
        btn_row.addWidget(self._login_btn, 1)
        layout.addLayout(btn_row)

        layout.addStretch()
        return page

    # ── 事件处理 ──────────────────────────────────────

    def _set_loading(self, loading: bool) -> None:
        """设置所有按钮为加载/正常状态"""
        self._connect_btn.setEnabled(not loading)
        self._init_admin_btn.setEnabled(not loading)
        self._login_btn.setEnabled(not loading)

    def _show_error(self, msg: str) -> None:
        """在窗口顶部显示错误通知"""
        InfoBar.error("错误", msg, parent=self,
                      duration=4000, position=InfoBarPosition.TOP)

    def _show_success(self, msg: str) -> None:
        """在窗口顶部显示成功通知"""
        InfoBar.success("成功", msg, parent=self,
                        duration=3000, position=InfoBarPosition.TOP)

    def _try_auto_login(self) -> None:
        """尝试从保存的 token 免登录恢复会话"""
        self._set_loading(True)

        def task():
            from src.services import auth_service
            return auth_service.try_restore_session()

        worker = Worker(task)
        worker.finished.connect(self._on_auto_login_done)
        worker.error.connect(lambda _: self._set_loading(False))
        worker.start()
        self._worker = worker

    def _on_auto_login_done(self, success: bool) -> None:
        """免登录恢复完成"""
        self._set_loading(False)
        if success:
            from src.utils.session import session_manager
            logger.info("免登录恢复成功，角色: %s", session_manager.role)
            self.login_success.emit(session_manager.role)

    def _on_connect(self) -> None:
        """点击"连接"：执行密钥交换，再检查管理员是否存在"""
        server_url = self._server_url_input.currentText().strip()
        if not server_url:
            self._show_error("请输入服务端地址")
            return
        # 若当前输入是历史中的地址，删除按钮应保持启用
        self._delete_history_btn.setEnabled(self._server_url_input.count() > 0)
        # 暂存到实例变量，连接成功后用于写入历史
        self._pending_server_url = server_url

        self._set_loading(True)

        def task():
            # 先做密钥交换，建立加密通道
            auth_service.do_key_exchange(server_url)
            # 再检查是否有管理员
            return auth_service.check_admin_exists()

        self._worker = Worker(task)
        self._worker.finished.connect(self._on_connect_done)
        self._worker.error.connect(self._on_connect_error)
        self._worker.start()

    def _on_connect_done(self, admin_exists: bool) -> None:
        """连接成功后写入历史并根据管理员状态跳转页面"""
        self._set_loading(False)
        # 密钥交换成功 → 把当前地址加入历史（去重置顶，最多 10 条）
        url = getattr(self, "_pending_server_url", "").strip()
        if url:
            server_history_service.record(url)
            self._refresh_server_history()
        if admin_exists:
            self._stack.setCurrentIndex(_PAGE_LOGIN)
        else:
            self._stack.setCurrentIndex(_PAGE_INIT_ADMIN)

    def _on_connect_error(self, msg: str) -> None:
        """连接失败处理"""
        self._set_loading(False)
        self._show_error(f"无法连接到服务端：{msg}")
        logger.error("连接服务端失败: %s", msg)

    def _on_init_admin(self) -> None:
        """点击"创建管理员"：校验输入后执行初始化"""
        username = self._admin_username_input.text().strip()
        password = self._admin_password_input.text()
        confirm = self._admin_confirm_input.text()

        if len(username) < 3:
            self._show_error("用户名长度至少 3 个字符")
            return
        if len(password) < 8:
            self._show_error("密码长度至少 8 个字符")
            return
        if password != confirm:
            self._show_error("两次密码输入不一致")
            return

        self._set_loading(True)

        def task():
            auth_service.init_admin(username, password)
            auth_service.login(username, password)

        self._worker = Worker(task)
        self._worker.finished.connect(lambda _: self._on_login_success())
        self._worker.error.connect(self._on_login_error)
        self._worker.start()

    def _on_login(self) -> None:
        """点击"登录"：执行登录"""
        username = self._login_username_input.text().strip()
        password = self._login_password_input.text()

        if not username or not password:
            self._show_error("用户名和密码不能为空")
            return

        # 读取并持久化用户选择的登录有效期上限
        local_max_seconds = self._ttl_combo.currentData() or LOCAL_SESSION_TTL_DEFAULT
        auth_service.save_local_session_max_seconds(local_max_seconds)

        self._set_loading(True)

        self._worker = Worker(
            auth_service.login, username, password, int(local_max_seconds),
        )
        self._worker.finished.connect(lambda _: self._on_login_success())
        self._worker.error.connect(self._on_login_error)
        self._worker.start()

    def _on_login_success(self) -> None:
        """登录成功，发射信号通知主入口"""
        from src.utils.session import session_manager
        self._set_loading(False)
        self._show_success("登录成功")
        logger.info("登录成功，角色: %s", session_manager.role)
        self.login_success.emit(session_manager.role)

    def _on_login_error(self, msg: str) -> None:
        """登录失败处理"""
        self._set_loading(False)
        self._show_error(f"登录失败：{msg}")
        logger.error("登录失败: %s", msg)

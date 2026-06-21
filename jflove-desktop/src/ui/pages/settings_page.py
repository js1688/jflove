"""
设置页面

管理客户端本地配置：
  - 服务端地址（可修改并重新连接）
  - 笔记目录（选择磁盘 + 在磁盘内选择具体子目录）
  - 账号操作（退出登录，返回登录页）
  - 关于信息

布局策略：
  - 外层使用 QScrollArea，避免内容超出窗口高度时被裁剪
  - 每个功能区独立成卡片（CardWidget），分块构建，边距一致
  - 笔记目录采用「步骤 1 / 步骤 2」分步引导，目录浏览区给足高度
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QAbstractItemView, QScrollArea, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from qfluentwidgets import (
    PrimaryPushButton, PushButton, SubtitleLabel, BodyLabel,
    CardWidget, InfoBar, InfoBarPosition, ComboBox, EditableComboBox,
    FluentIcon as FIF, ToolButton, StrongBodyLabel, CaptionLabel,
)

from src.config.settings import APP_VERSION
from src.services import (
    auth_service, note_service, disk_service, server_history_service,
)
from src.utils.session import session_manager
from src.utils.worker import Worker
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SettingsPage(QWidget):
    """系统设置页面

    :signal reconnect_success: 服务端地址重新连接成功后发出（携带新地址）
    :signal logout_requested: 用户请求退出登录，由主窗口统一处理
    """

    reconnect_success = Signal(str)
    logout_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._worker = None
        self._disks: list[dict] = []
        # 当前目录浏览路径栈，栈顶元素为当前正在浏览的相对路径
        self._path_stack: list[str] = [""]
        self._current_disk_id: int | None = None
        self._setup_ui()

    # ── UI 构建 ────────────────────────────────────────

    def _setup_ui(self) -> None:
        """构建整体 UI：外层 QScrollArea + 内层 VBox 卡片堆叠"""
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # 滚动容器：内容超出窗口高度时显示垂直滚动条
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        outer_layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 16, 20, 24)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignTop)

        layout.addWidget(SubtitleLabel("设置"))

        layout.addWidget(self._build_server_card())
        layout.addWidget(self._build_notes_card())
        layout.addWidget(self._build_logout_card())
        layout.addWidget(self._build_about_card())
        layout.addStretch()

    def _build_server_card(self) -> CardWidget:
        """构建「服务端配置」卡片（可编辑下拉，历史地址来自本地缓存）"""
        card = CardWidget()
        sl = QVBoxLayout(card)
        sl.setContentsMargins(20, 16, 20, 16)
        sl.setSpacing(10)

        sl.addWidget(StrongBodyLabel("服务端配置"))
        sl.addWidget(BodyLabel("服务端地址"))

        self._server_url_input = EditableComboBox()
        self._server_url_input.setPlaceholderText("http://localhost:8989")
        self._refresh_server_history()
        sl.addWidget(self._server_url_input)

        hint = CaptionLabel("修改服务端地址后将重新执行密钥交换，需要重新登录。")
        hint.setWordWrap(True)
        sl.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_server_btn = PrimaryPushButton("保存并重新连接")
        self._save_server_btn.setMinimumWidth(160)
        self._save_server_btn.clicked.connect(self._on_save_server)
        btn_row.addWidget(self._save_server_btn)
        sl.addLayout(btn_row)

        return card

    def _refresh_server_history(self) -> None:
        """从本地缓存加载历史地址，刷新下拉项；当前选中项保持为正在使用的地址"""
        history = server_history_service.list_history()
        # 当前正在用的地址优先显示，其次是历史第一项，再次是兜底默认值
        current = (
            (session_manager.server_url or "").strip()
            or server_history_service.get_default()
        )
        self._server_url_input.clear()
        if history:
            self._server_url_input.addItems(history)
        self._server_url_input.setCurrentText(current)

    def _build_notes_card(self) -> CardWidget:
        """构建「笔记目录配置」卡片：分步引导式 UI"""
        card = CardWidget()
        nl = QVBoxLayout(card)
        nl.setContentsMargins(20, 16, 20, 16)
        nl.setSpacing(12)

        nl.addWidget(StrongBodyLabel("笔记目录配置"))

        desc = BodyLabel(
            "选择用于存储 .md 笔记文件的具体目录。"
            "请先选择虚拟磁盘，再浏览并选择该磁盘内的子目录。"
        )
        desc.setWordWrap(True)
        nl.addWidget(desc)

        # 当前已配置路径（突出显示，便于一眼识别）
        current_row = QHBoxLayout()
        current_row.setSpacing(6)
        current_row.addWidget(BodyLabel("当前配置："))
        self._notes_current_label = StrongBodyLabel("未配置")
        self._notes_current_label.setWordWrap(True)
        current_row.addWidget(self._notes_current_label, stretch=1)
        nl.addLayout(current_row)

        nl.addSpacing(4)

        # ── 第 1 步：选择虚拟磁盘 ──
        nl.addWidget(BodyLabel("第 1 步：选择虚拟磁盘"))
        disk_row = QHBoxLayout()
        self._notes_disk_combo = ComboBox()
        self._notes_disk_combo.setPlaceholderText("加载中…")
        self._notes_disk_combo.setMinimumWidth(260)
        self._notes_disk_combo.setMinimumHeight(33)
        self._notes_disk_combo.currentIndexChanged.connect(self._on_notes_disk_changed)
        disk_row.addWidget(self._notes_disk_combo, stretch=1)
        nl.addLayout(disk_row)

        nl.addSpacing(4)

        # ── 第 2 步：浏览并选择具体目录 ──
        nl.addWidget(BodyLabel("第 2 步：浏览并选择具体目录"))

        # 路径面包屑 + 返回按钮
        browser_label_row = QHBoxLayout()
        browser_label_row.setSpacing(8)
        browser_label_row.addWidget(BodyLabel("当前路径："))
        self._notes_path_label = StrongBodyLabel("/")
        self._notes_path_label.setWordWrap(False)
        self._notes_path_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        browser_label_row.addWidget(self._notes_path_label, stretch=1)

        back_btn = ToolButton(FIF.RETURN)
        back_btn.setToolTip("返回上级目录")
        back_btn.clicked.connect(self._on_dir_back)
        browser_label_row.addWidget(back_btn)
        nl.addLayout(browser_label_row)

        # 目录列表（显著放大可视高度，避免拥挤）
        self._dir_list = QListWidget()
        self._dir_list.setMinimumHeight(220)
        self._dir_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._dir_list.itemDoubleClicked.connect(self._on_dir_enter)
        nl.addWidget(self._dir_list)

        hint = CaptionLabel("提示：双击文件夹进入子目录；点击右上角的返回图标可回到上一级。")
        hint.setWordWrap(True)
        nl.addWidget(hint)

        # 保存按钮（右对齐，宽度足够展示完整文字）
        save_row = QHBoxLayout()
        save_row.addStretch()
        self._save_notes_btn = PrimaryPushButton("使用当前目录")
        self._save_notes_btn.setMinimumWidth(180)
        self._save_notes_btn.clicked.connect(self._on_save_notes_dir)
        save_row.addWidget(self._save_notes_btn)
        nl.addLayout(save_row)

        return card

    def _build_logout_card(self) -> CardWidget:
        """构建「账号」卡片：显示当前用户并提供退出登录按钮"""
        card = CardWidget()
        ll = QVBoxLayout(card)
        ll.setContentsMargins(20, 16, 20, 16)
        ll.setSpacing(10)

        ll.addWidget(StrongBodyLabel("账号"))
        if session_manager.is_logged_in():
            ll.addWidget(BodyLabel(
                f"当前用户：{session_manager.username}（{session_manager.role}）"
            ))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        logout_btn = PushButton("退出登录")
        logout_btn.setMinimumWidth(120)
        logout_btn.clicked.connect(self._on_logout)
        btn_row.addWidget(logout_btn)
        ll.addLayout(btn_row)

        return card

    def _build_about_card(self) -> CardWidget:
        """构建「关于」卡片：展示版本和加密方案信息"""
        card = CardWidget()
        al = QVBoxLayout(card)
        al.setContentsMargins(20, 16, 20, 16)
        al.setSpacing(6)

        al.addWidget(StrongBodyLabel("关于 JFLove"))
        al.addWidget(BodyLabel(f"版本：{APP_VERSION}"))
        al.addWidget(BodyLabel("私有文档 & 笔记管理系统"))
        al.addWidget(CaptionLabel("加密方案：X25519 ECDH + ChaCha20-Poly1305 + ES256 JWT"))

        return card

    # ── 数据加载 ───────────────────────────────────────

    def load_system_config(self) -> None:
        """加载笔记目录配置（磁盘列表 + 当前选择）"""
        self._worker = Worker(note_service.get_notes_disk)
        self._worker.finished.connect(self._on_disk_config_loaded)
        self._worker.error.connect(lambda e: self._show_error(f"加载笔记目录配置失败：{e}"))
        self._worker.start()

    def _on_disk_config_loaded(self, result: dict) -> None:
        """
        填充磁盘下拉列表，并显示当前已配置路径。

        v1.1.4 修复：blockSignals 阻断 setCurrentIndex 触发 _on_notes_disk_changed，
        避免 _path_stack 被重置为根目录 + 异步 Worker 竞态导致目录列表显示错误。
        同时将目录浏览直接定位到已配置路径（而非其父级），使「当前路径」标签
        与「当前配置」展示保持一致。
        """
        self._disks = result.get("disks", [])
        current_disk_id = result.get("disk_id")
        current_path = result.get("path", "")

        self._notes_disk_combo.blockSignals(True)
        self._notes_disk_combo.clear()
        for d in self._disks:
            self._notes_disk_combo.addItem(d["name"], userData=d["id"])
        if current_disk_id is not None:
            for i, d in enumerate(self._disks):
                if d["id"] == current_disk_id:
                    self._notes_disk_combo.setCurrentIndex(i)
                    self._current_disk_id = current_disk_id
                    break
        self._notes_disk_combo.blockSignals(False)

        # 顶部「当前配置」展示
        if current_disk_id:
            disk_name = next((d["name"] for d in self._disks if d["id"] == current_disk_id), "?")
            sub = current_path if current_path else "（根目录）"
            self._notes_current_label.setText(f"{disk_name} / {sub}")
            # 目录浏览直接定位到已配置路径，使路径标签与当前配置一致
            self._path_stack = [current_path] if current_path else [""]
            self._refresh_dir_list()
        else:
            self._notes_current_label.setText("未配置")

    # ── 目录浏览 ───────────────────────────────────────

    def _on_notes_disk_changed(self, index: int) -> None:
        """切换磁盘后重置路径栈并刷新目录列表"""
        if index < 0 or index >= len(self._disks):
            self._dir_list.clear()
            self._current_disk_id = None
            return
        self._current_disk_id = self._disks[index]["id"]
        self._path_stack = [""]
        self._refresh_dir_list()

    def _refresh_dir_list(self) -> None:
        """刷新当前路径下的子目录列表"""
        if self._current_disk_id is None:
            return
        current_path = self._path_stack[-1]
        self._notes_path_label.setText("/" + current_path if current_path else "/")

        disk_id = self._current_disk_id

        def fetch():
            return disk_service.browse_dirs(disk_id, current_path)

        worker = Worker(fetch)
        worker.finished.connect(self._on_dirs_loaded)
        worker.error.connect(lambda e: self._show_error(f"加载目录失败：{e}"))
        worker.start()
        self._worker = worker

    def _on_dirs_loaded(self, dirs: list) -> None:
        """目录列表加载回调：渲染到 QListWidget，空目录显示占位"""
        self._dir_list.clear()
        if not dirs:
            placeholder = QListWidgetItem("（此目录下没有子文件夹）")
            placeholder.setFlags(Qt.NoItemFlags)
            self._dir_list.addItem(placeholder)
            return
        for d in dirs:
            item = QListWidgetItem(f"📁  {d['name']}")
            item.setData(Qt.UserRole, d["path"])
            self._dir_list.addItem(item)

    def _on_dir_enter(self, item: QListWidgetItem) -> None:
        """双击进入子目录"""
        path = item.data(Qt.UserRole)
        if not path:
            return
        self._path_stack.append(path)
        self._refresh_dir_list()

    def _on_dir_back(self) -> None:
        """返回上级目录"""
        if len(self._path_stack) > 1:
            self._path_stack.pop()
            self._refresh_dir_list()

    # ── 保存笔记目录 ───────────────────────────────────

    def _on_save_notes_dir(self) -> None:
        """将当前浏览位置保存为笔记目录"""
        if self._current_disk_id is None:
            self._show_error("请先选择虚拟磁盘")
            return
        current_path = self._path_stack[-1]
        disk_id = self._current_disk_id

        self._save_notes_btn.setEnabled(False)

        def save():
            note_service.set_notes_disk(disk_id, current_path)

        worker = Worker(save)
        worker.finished.connect(lambda _: self._on_notes_dir_saved(disk_id, current_path))
        worker.error.connect(lambda e: (
            self._save_notes_btn.setEnabled(True),
            self._show_error(f"保存失败：{e}"),
        ))
        worker.start()
        self._worker = worker

    def _on_notes_dir_saved(self, disk_id: int, path: str) -> None:
        """
        保存成功回调：更新当前配置展示与目录浏览视图。

        v1.1.4 修复：同步更新 _path_stack，确保目录浏览视图（路径标签 + 目录列表）
        与刚刚保存的配置一致。此前只更新了 _notes_current_label 导致展示不一致。
        """
        self._save_notes_btn.setEnabled(True)
        disk_name = next((d["name"] for d in self._disks if d["id"] == disk_id), "?")
        sub = path if path else "（根目录）"
        self._notes_current_label.setText(f"{disk_name} / {sub}")
        # 目录浏览视图与当前配置保持一致
        self._path_stack = [path] if path else [""]
        self._refresh_dir_list()
        InfoBar.success("已保存", f"笔记目录已设置为：{disk_name}/{path or ''}",
                        parent=self, duration=3000, position=InfoBarPosition.TOP_RIGHT)
        logger.info("笔记目录已更新: disk_id=%d, path=%s", disk_id, path)

    # ── 服务端地址 ─────────────────────────────────────

    def _on_save_server(self) -> None:
        """保存服务端地址并重新执行密钥交换"""
        url = self._server_url_input.currentText().strip()
        if not url:
            InfoBar.warning("提示", "服务端地址不能为空",
                            parent=self, duration=3000, position=InfoBarPosition.TOP_RIGHT)
            return
        self._save_server_btn.setEnabled(False)
        # 暂存到实例变量，重连成功后用于写入历史
        self._pending_server_url = url

        self._worker = Worker(auth_service.do_key_exchange, url)
        self._worker.finished.connect(self._on_reconnect_done)
        self._worker.error.connect(self._on_reconnect_error)
        self._worker.start()

    def _on_reconnect_done(self, _) -> None:
        """密钥交换成功回调：写入历史并刷新下拉"""
        self._save_server_btn.setEnabled(True)
        url = getattr(self, "_pending_server_url", "").strip()
        if url:
            server_history_service.record(url)
            self._refresh_server_history()
        InfoBar.success("成功", "服务端地址已更新，密钥交换完成，请重新登录",
                        parent=self, duration=4000, position=InfoBarPosition.TOP_RIGHT)
        logger.info("服务端地址已更新: %s", session_manager.server_url)

    def _on_reconnect_error(self, msg: str) -> None:
        """密钥交换失败回调"""
        self._save_server_btn.setEnabled(True)
        InfoBar.error("连接失败", f"无法连接到服务端：{msg}",
                      parent=self, duration=4000, position=InfoBarPosition.TOP_RIGHT)

    # ── 退出登录 ───────────────────────────────────────

    def _on_logout(self) -> None:
        """退出登录，发射信号由主窗口统一处理"""
        auth_service.logout()
        logger.info("用户手动退出登录")
        self.logout_requested.emit()

    # ── 辅助 ──────────────────────────────────────────

    def _show_error(self, msg: str) -> None:
        """统一错误提示"""
        InfoBar.error("错误", msg, parent=self, duration=4000,
                      position=InfoBarPosition.TOP_RIGHT)

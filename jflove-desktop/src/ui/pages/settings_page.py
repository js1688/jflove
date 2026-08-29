"""
设置页面

管理客户端本地配置：
  - 服务端地址（可修改并重新连接）
  - 笔记目录（通过对话框选择磁盘 + 子目录，紧凑设计）
  - 账号操作（退出登录，返回登录页）
  - 关于信息

布局策略：
  - 外层使用 QScrollArea，避免内容超出窗口高度时被裁剪
  - 每个功能区独立成卡片（CardWidget），分块构建，边距一致
  - 笔记目录采用弹出对话框浏览选择，节省设置页空间
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QAbstractItemView, QScrollArea, QFrame, QDialog,
)
from PySide6.QtCore import Qt, Signal

from qfluentwidgets import (
    PrimaryPushButton, PushButton, SubtitleLabel, BodyLabel,
    CardWidget, InfoBar, InfoBarPosition, ComboBox, EditableComboBox,
    FluentIcon as FIF, ToolButton, StrongBodyLabel, CaptionLabel,
    SwitchButton, LineEdit,
)

from src.config.settings import APP_VERSION
from src.services import (
    auth_service, note_service, disk_service, server_history_service,
    config_service,
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
        # v1.4.0：媒体修复开关仅管理员可见（服务端配置，三端共享）
        self._is_admin = session_manager.is_admin()
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
        # v1.4.0：媒体修复开关（仅管理员可见）
        if self._is_admin:
            layout.addWidget(self._build_media_repair_card())
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
        """构建「笔记目录配置」卡片：紧凑设计，点击按钮弹出对话框选择目录"""
        card = CardWidget()
        nl = QVBoxLayout(card)
        nl.setContentsMargins(20, 16, 20, 16)
        nl.setSpacing(10)

        nl.addWidget(StrongBodyLabel("笔记目录配置"))

        # 当前已配置路径
        current_row = QHBoxLayout()
        current_row.setSpacing(6)
        current_row.addWidget(BodyLabel("当前配置："))
        self._notes_current_label = StrongBodyLabel("未配置")
        self._notes_current_label.setWordWrap(True)
        current_row.addWidget(self._notes_current_label, stretch=1)
        nl.addLayout(current_row)

        # 配置按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._config_notes_btn = PushButton(FIF.FOLDER, "配置笔记目录…")
        self._config_notes_btn.setMinimumWidth(160)
        self._config_notes_btn.clicked.connect(self._on_open_notes_dir_dialog)
        btn_row.addWidget(self._config_notes_btn)
        nl.addLayout(btn_row)

        return card

    def _build_media_repair_card(self) -> CardWidget:
        """构建「离线媒体修复」卡片（v1.4.2，仅管理员可见）

        v1.4.2 起修复改为手动离线任务（修复中心），不再有实时修复总开关。
        此处仅保留离线修复队列的配置项：重编码子开关与并发数（存服务端
        config 表，三端共享，修改后立即生效）。
        """
        card = CardWidget()
        ml = QVBoxLayout(card)
        ml.setContentsMargins(20, 16, 20, 16)
        ml.setSpacing(10)

        ml.addWidget(StrongBodyLabel("离线媒体修复"))
        hint = CaptionLabel(
            "v1.4.2 起损坏媒体经「修复中心」手动离线修复（文件管理右键"
            "「修复损坏媒体」发起）。以下为修复队列配置：并发数 1~8 或留空"
            "按服务器 CPU 核数自动推导；重编码为无损修复失败时的降级手段。"
        )
        hint.setWordWrap(True)
        ml.addWidget(hint)

        # 重编码子开关（独立于并发数，默认关闭）
        transcode_row = QHBoxLayout()
        transcode_row.addWidget(BodyLabel("允许重编码降级"))
        transcode_row.addStretch()
        self._transcode_switch = SwitchButton()
        self._transcode_switch.setChecked(False)
        self._transcode_switch.checkedChanged.connect(self._on_transcode_toggled)
        transcode_row.addWidget(self._transcode_switch)
        ml.addLayout(transcode_row)

        # 并发数
        concurrent_row = QHBoxLayout()
        concurrent_row.addWidget(BodyLabel("修复并发数（1~8，留空自动）"))
        concurrent_row.addStretch()
        self._concurrent_input = LineEdit()
        self._concurrent_input.setPlaceholderText("自动")
        self._concurrent_input.setFixedWidth(80)
        concurrent_row.addWidget(self._concurrent_input)
        save_btn = PushButton("保存")
        save_btn.setMinimumWidth(80)
        save_btn.clicked.connect(self._on_save_concurrent)
        concurrent_row.addWidget(save_btn)
        ml.addLayout(concurrent_row)

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
        """加载系统配置：笔记目录 +（管理员）离线修复配置"""
        self._worker = Worker(note_service.get_notes_disk)
        self._worker.finished.connect(self._on_disk_config_loaded)
        self._worker.error.connect(lambda e: self._show_error(f"加载笔记目录配置失败：{e}"))
        self._worker.start()
        if self._is_admin:
            self._load_media_repair_config()

    def _on_disk_config_loaded(self, result: dict) -> None:
        """更新当前配置展示文字"""
        self._disks = result.get("disks", [])
        current_disk_id = result.get("disk_id")
        current_path = result.get("path", "")

        if current_disk_id:
            disk_name = next(
                (d["name"] for d in self._disks if d["id"] == current_disk_id), "?"
            )
            sub = current_path if current_path else "（根目录）"
            self._notes_current_label.setText(f"{disk_name} / {sub}")
        else:
            self._notes_current_label.setText("未配置")

    # ── 离线媒体修复配置（v1.4.2，仅管理员） ───────────

    def _load_media_repair_config(self) -> None:
        """异步加载媒体修复配置并更新开关状态（仅管理员）"""
        if not self._is_admin:
            return

        def load():
            return config_service.get_all_config()

        worker = Worker(load)
        worker.finished.connect(self._on_media_repair_config_loaded)
        worker.error.connect(lambda e: logger.warning("加载媒体修复配置失败: %s", e))
        worker.start()
        self._worker = worker

    def _on_media_repair_config_loaded(self, config) -> None:
        """离线修复配置加载回调：更新重编码/并发数状态（避免触发保存信号）"""
        cfg = config if isinstance(config, dict) else {
            c.get("key"): c.get("value", "")
            for c in config if isinstance(c, dict)
        }
        self._transcode_switch.blockSignals(True)
        self._transcode_switch.setChecked(cfg.get("media_repair_allow_transcode", "0") == "1")
        self._transcode_switch.blockSignals(False)
        self._concurrent_input.setText(cfg.get("media_repair_max_concurrent", ""))

    def _on_transcode_toggled(self, checked: bool) -> None:
        """重编码子开关变化：写服务端配置"""
        self._save_media_repair("media_repair_allow_transcode", "1" if checked else "0")

    def _on_save_concurrent(self) -> None:
        """保存并发数（1~8 或留空自动）"""
        raw = self._concurrent_input.text().strip()
        if raw == "":
            self._save_media_repair("media_repair_max_concurrent", "")
            return
        if raw.isdigit() and 1 <= int(raw) <= 8:
            self._save_media_repair("media_repair_max_concurrent", str(int(raw)))
        else:
            InfoBar.warning("提示", "并发数需为 1~8 的整数，或留空使用自动基线",
                            parent=self, duration=3000, position=InfoBarPosition.TOP_RIGHT)

    def _save_media_repair(self, key: str, value: str) -> None:
        """异步保存媒体修复配置（Worker 线程，避免阻塞 UI）"""
        def save():
            config_service.update_config(key, value)

        worker = Worker(save)
        worker.finished.connect(
            lambda _: InfoBar.success(
                "已保存", "配置已更新，立即生效",
                parent=self, duration=3000, position=InfoBarPosition.TOP_RIGHT,
            )
        )
        worker.error.connect(lambda e: self._show_error(f"保存失败：{e}"))
        worker.start()
        self._worker = worker

    # ── 弹出对话框配置笔记目录 ─────────────────────────

    def _on_open_notes_dir_dialog(self) -> None:
        """打开笔记目录选择对话框"""
        dlg = _NotesDirBrowserDialog(self._disks, parent=self)
        if self._disks:
            # 预填当前选中
            current_disk_text = self._notes_current_label.text()
            if current_disk_text and current_disk_text != "未配置":
                # 尝试从当前展示文字解析出 disk_id
                for d in self._disks:
                    if d["name"] in current_disk_text:
                        dlg.set_disk(d["id"])
                        break

        if dlg.exec() == QDialog.Accepted:
            disk_id = dlg.get_selected_disk_id()
            path = dlg.get_selected_path()
            if disk_id is None:
                return

            def save():
                note_service.set_notes_disk(disk_id, path)

            worker = Worker(save)
            worker.finished.connect(lambda _: self._on_notes_dir_saved(disk_id, path))
            worker.error.connect(lambda e: self._show_error(f"保存失败：{e}"))
            worker.start()
            self._worker = worker

    def _on_notes_dir_saved(self, disk_id: int, path: str) -> None:
        """保存成功回调：更新配置展示"""
        disk_name = next((d["name"] for d in self._disks if d["id"] == disk_id), "?")
        sub = path if path else "（根目录）"
        self._notes_current_label.setText(f"{disk_name} / {sub}")
        InfoBar.success(
            "已保存", f"笔记目录已设置为：{disk_name}/{path or ''}",
            parent=self, duration=3000, position=InfoBarPosition.TOP_RIGHT,
        )
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


# ── 笔记目录浏览选择对话框（v1.2.0 替代内联浏览器） ──


class _NotesDirBrowserDialog(QDialog):
    """
    笔记目录浏览选择对话框。

    用户通过此对话框选择虚拟磁盘 → 浏览子目录 → 确认选择。
    替代旧版内联目录浏览器，大幅节省设置页空间。
    """

    def __init__(self, disks: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置笔记目录")
        self.resize(520, 420)
        self._disks = disks
        self._disk_id: int | None = None
        self._path_stack: list[str] = [""]
        self._selected_path: str = ""
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        layout.addWidget(SubtitleLabel("选择笔记存储目录"))
        layout.addWidget(CaptionLabel("先选择虚拟磁盘，再浏览并选择该磁盘内的子目录。"))

        # 磁盘选择
        layout.addWidget(BodyLabel("虚拟磁盘"))
        self._disk_combo = ComboBox()
        self._disk_combo.setPlaceholderText("选择虚拟磁盘")
        for d in self._disks:
            self._disk_combo.addItem(d["name"], userData=d["id"])
        self._disk_combo.currentIndexChanged.connect(self._on_disk_changed)
        layout.addWidget(self._disk_combo)

        layout.addSpacing(4)

        # 路径浏览区
        browse_label = BodyLabel("浏览子目录")
        layout.addWidget(browse_label)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(BodyLabel("路径："))
        self._path_label = StrongBodyLabel("/")
        self._path_label.setWordWrap(False)
        path_row.addWidget(self._path_label, stretch=1)

        self._back_btn = ToolButton(FIF.RETURN)
        self._back_btn.setToolTip("返回上级目录")
        self._back_btn.clicked.connect(self._on_go_back)
        self._back_btn.setEnabled(False)
        path_row.addWidget(self._back_btn)

        root_btn = ToolButton(FIF.CANCEL_MEDIUM)
        root_btn.setToolTip("返回根目录")
        root_btn.clicked.connect(self._on_go_root)
        path_row.addWidget(root_btn)

        layout.addLayout(path_row)

        # 目录列表
        self._dir_list = QListWidget()
        self._dir_list.setMinimumHeight(180)
        self._dir_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._dir_list.itemDoubleClicked.connect(self._on_enter_dir)
        layout.addWidget(self._dir_list, stretch=1)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = PushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._select_btn = PrimaryPushButton("选择当前目录")
        self._select_btn.setMinimumWidth(160)
        self._select_btn.setEnabled(False)
        self._select_btn.clicked.connect(self._on_select)
        btn_row.addWidget(self._select_btn)
        layout.addLayout(btn_row)

    def set_disk(self, disk_id: int) -> None:
        """预填磁盘选中项"""
        for i, d in enumerate(self._disks):
            if d["id"] == disk_id:
                self._disk_combo.setCurrentIndex(i)
                break

    def _on_disk_changed(self, index: int) -> None:
        """切换磁盘后重置路径浏览"""
        if index < 0 or index >= len(self._disks):
            self._disk_id = None
            self._select_btn.setEnabled(False)
            self._dir_list.clear()
            return
        self._disk_id = self._disks[index]["id"]
        self._path_stack = [""]
        self._selected_path = ""
        self._select_btn.setEnabled(True)
        self._refresh_dirs()

    def _refresh_dirs(self) -> None:
        """刷新当前路径下的子目录列表"""
        if self._disk_id is None:
            return
        current_path = self._path_stack[-1]
        self._path_label.setText("/" + current_path if current_path else "/")
        self._back_btn.setEnabled(len(self._path_stack) > 1)

        def fetch():
            return disk_service.browse_dirs(self._disk_id, current_path)

        worker = Worker(fetch)
        worker.finished.connect(self._on_dirs_loaded)
        worker.error.connect(lambda e: self._show_dialog_error(f"加载目录失败：{e}"))
        worker.start()

    def _on_dirs_loaded(self, dirs: list) -> None:
        """目录列表加载回调"""
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

    def _on_enter_dir(self, item: QListWidgetItem) -> None:
        """双击进入子目录"""
        path = item.data(Qt.UserRole)
        if not path:
            return
        self._path_stack.append(path)
        self._refresh_dirs()

    def _on_go_back(self) -> None:
        """返回上级目录"""
        if len(self._path_stack) > 1:
            self._path_stack.pop()
            self._refresh_dirs()

    def _on_go_root(self) -> None:
        """返回根目录"""
        self._path_stack = [""]
        self._refresh_dirs()

    def _on_select(self) -> None:
        """确认选择当前路径"""
        self._selected_path = self._path_stack[-1]
        self.accept()

    def get_selected_disk_id(self) -> int | None:
        """获取用户选择的磁盘 ID"""
        return self._disk_id

    def get_selected_path(self) -> str:
        """获取用户选择的相对路径（空字符串表示根目录）"""
        return self._selected_path

    def _show_dialog_error(self, msg: str) -> None:
        InfoBar.error("错误", msg, parent=self,
                      duration=4000, position=InfoBarPosition.TOP)

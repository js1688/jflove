"""
文档管理页面

功能：
  - 磁盘选择下拉列表
  - 文件/目录树形浏览（双击进入目录）
  - 右键菜单（下载、预览、重命名、移动到…、删除）
  - 工具栏（上传文件、新建目录、返回上级、刷新）
  - 上传/下载提交至全局传输任务管理器（支持并行 + 任务面板查看进度）
  - 上传支持多选（一次性提交多个文件到任务队列）
  - v1.1.3 新增：重命名、移动到…（写权限控制）
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QFileDialog, QInputDialog, QAbstractItemView, QMenu, QDialog,
    QDialogButtonBox,
)
from PySide6.QtCore import Qt, QPoint

from qfluentwidgets import (
    ComboBox, PushButton, ToolButton, SubtitleLabel, BodyLabel,
    InfoBar, InfoBarPosition, MessageBox, FluentIcon as FIF,
    PrimaryPushButton,
)

from src.components.preview_dialog import PreviewDialog
from src.services import file_service
from src.utils.worker import Worker
from src.utils.transfer_manager import transfer_manager
from src.utils.session import session_manager
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MoveTargetDialog(QDialog):
    """
    移动目标目录选择弹窗（v1.1.3 新增）。

    展示当前磁盘的目录树（仅目录，不显示文件），用户选定后返回目标目录的相对路径。
    支持懒加载：点击展开时才请求子目录，避免一次性加载大量数据。
    """

    def __init__(self, disk_id: int, src_rel_path: str, parent=None):
        """
        :param disk_id: 当前磁盘 ID
        :param src_rel_path: 被移动项的相对路径（用于在树中禁用该项）
        :param parent: 父窗口
        """
        super().__init__(parent)
        self.setWindowTitle("选择目标目录")
        self.setMinimumSize(400, 480)
        self._disk_id = disk_id
        # 被移动的源路径（顶层目录名，用于识别后禁用）
        self._src_name = src_rel_path.split("/")[0] if src_rel_path else ""
        self._selected_path: str | None = None
        self._worker = None
        self._setup_ui()
        self._load_root()
        # 根节点加载完成后再连接，避免 addTopLevelItem 触发自动选中把按钮提前启用
        self._tree.currentItemChanged.connect(
            lambda cur, _: self._confirm_btn.setEnabled(cur is not None)
        )

    def _setup_ui(self) -> None:
        """构建弹窗 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        layout.addWidget(BodyLabel("选择要将文件/目录移动到的目标位置："))

        # 目录树（懒加载）
        self._tree = QTreeWidget()
        self._tree.setHeaderLabel("目录")
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        layout.addWidget(self._tree)

        # 按钮行
        btn_box = QDialogButtonBox()
        self._confirm_btn = PrimaryPushButton("确认移动")
        self._confirm_btn.setEnabled(False)   # 未选中时禁用
        self._cancel_btn = PushButton("取消")
        self._confirm_btn.clicked.connect(self._on_confirm)
        self._cancel_btn.clicked.connect(self.reject)
        btn_box.addButton(self._confirm_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.addButton(self._cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(btn_box)

    def _load_root(self) -> None:
        """加载根目录（/）"""
        self._tree.clear()
        root_item = QTreeWidgetItem(self._tree)
        root_item.setText(0, "/ （根目录）")
        root_item.setData(0, Qt.UserRole, "")          # 根目录路径为空字符串
        root_item.setData(0, Qt.UserRole + 1, False)   # 是否已加载子目录
        # 添加占位子项触发展开箭头
        QTreeWidgetItem(root_item)
        self._tree.addTopLevelItem(root_item)

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        """展开节点时懒加载子目录"""
        already_loaded = item.data(0, Qt.UserRole + 1)
        if already_loaded:
            return
        # 移除占位子项
        while item.childCount():
            item.removeChild(item.child(0))
        item.setData(0, Qt.UserRole + 1, True)

        dir_path = item.data(0, Qt.UserRole)
        self._worker = Worker(file_service.list_files, self._disk_id, dir_path)
        self._worker.finished.connect(
            lambda files, _item=item: self._on_subdir_loaded(_item, files)
        )
        self._worker.error.connect(lambda e: logger.warning("加载子目录失败: %s", e))
        self._worker.start()

    def _on_subdir_loaded(self, parent_item: QTreeWidgetItem, files: list) -> None:
        """子目录加载完成，添加到父节点"""
        parent_path = parent_item.data(0, Qt.UserRole)
        for f in files:
            if not f.get("is_dir"):
                continue
            child = QTreeWidgetItem(parent_item)
            child.setText(0, f["name"])
            child_path = (parent_path + "/" + f["name"]).lstrip("/")
            child.setData(0, Qt.UserRole, child_path)
            child.setData(0, Qt.UserRole + 1, False)
            # 添加占位子项（使展开箭头可见）
            QTreeWidgetItem(child)

    def _on_confirm(self) -> None:
        """确认移动"""
        selected = self._tree.currentItem()
        if selected is None:
            InfoBar.warning("提示", "请先选择一个目标目录", parent=self, duration=2000,
                            position=InfoBarPosition.TOP)
            return
        self._selected_path = selected.data(0, Qt.UserRole)
        self.accept()

    def getSelectedPath(self) -> str | None:
        """返回用户选定的目标目录相对路径（None 表示未选择或已取消）"""
        return self._selected_path


class FilePage(QWidget):
    """文档管理页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filePage")
        # 当前目录路径栈（用于返回上级）
        self._path_stack: list[str] = [""]
        # 当前选中的磁盘
        self._current_disk_id: int | None = None
        self._disks: list[dict] = []
        # v1.1.3：当前磁盘是否有写权限（控制重命名/移动/删除按钮可用性）
        self._can_write: bool = False
        self._worker = None
        self._setup_ui()

    # ── UI 构建 ────────────────────────────────────────

    def _setup_ui(self) -> None:
        """构建页面 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 页面标题
        layout.addWidget(SubtitleLabel("文档管理"))

        # 工具栏
        toolbar = QHBoxLayout()
        self._disk_combo = ComboBox()
        self._disk_combo.setPlaceholderText("选择虚拟磁盘")
        self._disk_combo.setMinimumWidth(160)
        self._disk_combo.currentIndexChanged.connect(self._on_disk_changed)
        toolbar.addWidget(self._disk_combo)

        toolbar.addSpacing(8)

        self._path_label = BodyLabel("/")
        self._path_label.setMinimumWidth(200)
        toolbar.addWidget(self._path_label)
        toolbar.addStretch()

        # 工具按钮
        self._upload_btn = PushButton(FIF.UP, "上传文件")
        self._upload_btn.clicked.connect(self._on_upload)
        toolbar.addWidget(self._upload_btn)

        self._mkdir_btn = PushButton(FIF.FOLDER_ADD, "新建目录")
        self._mkdir_btn.clicked.connect(self._on_mkdir)
        toolbar.addWidget(self._mkdir_btn)

        self._back_btn = ToolButton(FIF.RETURN)
        self._back_btn.setToolTip("返回上级")
        self._back_btn.clicked.connect(self._on_back)
        toolbar.addWidget(self._back_btn)

        self._refresh_btn = ToolButton(FIF.SYNC)
        self._refresh_btn.setToolTip("刷新")
        self._refresh_btn.clicked.connect(self._refresh)
        toolbar.addWidget(self._refresh_btn)

        layout.addLayout(toolbar)

        # 文件树
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["名称", "大小", "修改时间"])
        self._tree.setColumnWidth(0, 320)
        self._tree.setColumnWidth(1, 100)
        self._tree.setColumnWidth(2, 160)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree)

    # ── 数据加载 ───────────────────────────────────────

    def load_disks(self) -> None:
        """加载当前用户可访问的虚拟磁盘列表"""
        self._worker = Worker(file_service.list_accessible_disks)
        self._worker.finished.connect(self._on_disks_loaded)
        self._worker.error.connect(lambda e: self._show_error(f"加载磁盘失败：{e}"))
        self._worker.start()

    def _on_disks_loaded(self, disks: list) -> None:
        """磁盘列表加载完成"""
        self._disks = disks
        self._disk_combo.clear()
        for d in disks:
            self._disk_combo.addItem(d["name"], userData=d["id"])
        if disks:
            self._disk_combo.setCurrentIndex(0)

    def _on_disk_changed(self, index: int) -> None:
        """磁盘切换：重置路径并刷新文件列表"""
        if index < 0 or index >= len(self._disks):
            return
        self._current_disk_id = self._disks[index]["id"]
        # v1.1.3：管理员始终有写权限；普通用户读取磁盘信息中的 can_write
        role = session_manager.role or ""
        if role == "admin":
            self._can_write = True
        else:
            self._can_write = bool(self._disks[index].get("can_write", False))
        self._path_stack = [""]
        self._refresh()

    def _refresh(self) -> None:
        """刷新当前目录下的文件列表"""
        if self._current_disk_id is None:
            return
        current_path = self._path_stack[-1]
        self._path_label.setText("/" + current_path)

        self._worker = Worker(file_service.list_files, self._current_disk_id, current_path)
        self._worker.finished.connect(self._on_files_loaded)
        self._worker.error.connect(lambda e: self._show_error(f"加载目录失败：{e}"))
        self._worker.start()

    def _on_files_loaded(self, files: list) -> None:
        """文件列表加载完成，刷新树"""
        self._tree.clear()
        for f in files:
            item = QTreeWidgetItem()
            item.setText(0, f["name"])
            if f["is_dir"]:
                item.setIcon(0, self.style().standardIcon(
                    self.style().StandardPixmap.SP_DirIcon))
                item.setText(1, "")
            else:
                item.setIcon(0, self.style().standardIcon(
                    self.style().StandardPixmap.SP_FileIcon))
                item.setText(1, self._format_size(f["size"]))

            import datetime
            ts = datetime.datetime.fromtimestamp(f["modified_at"]).strftime("%Y-%m-%d %H:%M")
            item.setText(2, ts)
            # 存储元数据
            item.setData(0, Qt.UserRole, f)
            self._tree.addTopLevelItem(item)

        self._back_btn.setEnabled(len(self._path_stack) > 1)

    # ── 导航 ──────────────────────────────────────────

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """双击目录进入，双击文件触发预览"""
        meta = item.data(0, Qt.UserRole)
        if not meta:
            return
        if meta["is_dir"]:
            current = self._path_stack[-1]
            new_path = (current + "/" + meta["name"]).lstrip("/")
            self._path_stack.append(new_path)
            self._refresh()
        else:
            self._preview_file(meta["name"])

    def _on_back(self) -> None:
        """返回上级目录"""
        if len(self._path_stack) > 1:
            self._path_stack.pop()
            self._refresh()

    def _current_rel_path(self) -> str:
        """返回当前所在的相对路径"""
        return self._path_stack[-1]

    # ── 右键菜单 ───────────────────────────────────────

    def _on_context_menu(self, pos: QPoint) -> None:
        """
        文件树右键菜单（v1.1.3 扩展）。

        菜单顺序：
          文件：下载 / 预览 / --- / 重命名 / 移动到… / --- / 删除
          目录：重命名 / 移动到… / --- / 删除
        写权限控制：无写权限时，重命名 / 移动到… / 删除 均禁用。
        """
        item = self._tree.itemAt(pos)
        if not item:
            return
        meta = item.data(0, Qt.UserRole)
        if not meta:
            return

        menu = QMenu(self)
        download_action = preview_action = None

        # 下载 / 预览（仅文件）
        if not meta["is_dir"]:
            download_action = menu.addAction("下载")
            preview_action = menu.addAction("预览")
            menu.addSeparator()

        # 重命名 / 移动到…（写权限控制）
        rename_action = menu.addAction("重命名")
        move_action = menu.addAction("移动到…")
        rename_action.setEnabled(self._can_write)
        move_action.setEnabled(self._can_write)

        menu.addSeparator()

        # 删除（写权限控制）
        delete_action = menu.addAction("删除")
        delete_action.setEnabled(self._can_write)

        action = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if action is None:
            return

        if not meta["is_dir"] and action == download_action:
            self._download_file(meta["name"], meta.get("size", 0))
        elif not meta["is_dir"] and action == preview_action:
            self._preview_file(meta["name"])
        elif action == rename_action:
            self._rename_item(meta["name"], meta["is_dir"])
        elif action == move_action:
            self._move_item(meta["name"], meta["is_dir"])
        elif action == delete_action:
            self._delete_item(meta["name"], meta["is_dir"])

    # ── 文件操作 ───────────────────────────────────────

    def _on_upload(self) -> None:
        """选择一个或多个文件，提交到全局传输任务管理器"""
        if self._current_disk_id is None:
            self._show_error("请先选择虚拟磁盘")
            return
        local_paths, _ = QFileDialog.getOpenFileNames(self, "选择要上传的文件（可多选）")
        if not local_paths:
            return

        rel_dir = self._current_rel_path()
        for path in local_paths:
            transfer_manager.submit_upload(self._current_disk_id, rel_dir, path)

        self._show_success(
            f"已加入传输队列：{len(local_paths)} 个文件，可在「传输任务」页面查看进度"
        )

    def _download_file(self, filename: str, file_size: int = 0) -> None:
        """下载文件到本地，提交到全局传输任务管理器"""
        save_path, _ = QFileDialog.getSaveFileName(self, "保存文件", filename)
        if not save_path:
            return

        rel = (self._current_rel_path() + "/" + filename).lstrip("/")
        transfer_manager.submit_download(
            self._current_disk_id, rel, save_path,
            filename=filename, file_size=file_size,
        )
        self._show_success(f"{filename} 已加入下载队列，可在「传输任务」页面查看进度")

    def _preview_file(self, filename: str) -> None:
        """预览文件：图片 / SVG / Markdown / 文本 / 视频 / 音频 等"""
        rel = (self._current_rel_path() + "/" + filename).lstrip("/")
        dialog = PreviewDialog(self._current_disk_id, rel, filename, parent=self)
        dialog.exec()

    def _on_mkdir(self) -> None:
        """新建目录"""
        if self._current_disk_id is None:
            self._show_error("请先选择虚拟磁盘")
            return
        name, ok = QInputDialog.getText(self, "新建目录", "请输入目录名称：")
        if not ok or not name.strip():
            return

        rel = (self._current_rel_path() + "/" + name.strip()).lstrip("/")
        self._worker = Worker(file_service.make_dir, self._current_disk_id, rel)
        self._worker.finished.connect(lambda _: (self._refresh(), self._show_success("目录已创建")))
        self._worker.error.connect(lambda e: self._show_error(f"创建失败：{e}"))
        self._worker.start()

    def _rename_item(self, name: str, is_dir: bool) -> None:
        """
        重命名文件或目录（v1.1.3 新增）。

        弹出输入对话框，预填当前名称并全选，用户输入新名称后提交到后端。
        客户端做快速校验（空名称 / 含非法字符 / 未改变），服务端做最终校验。
        """
        kind = "目录" if is_dir else "文件"
        new_name, ok = QInputDialog.getText(
            self, "重命名", f"请输入{kind}的新名称：", text=name
        )
        if not ok:
            return

        new_name = new_name.strip()

        # 客户端快速校验
        if not new_name:
            self._show_error("名称不能为空")
            return
        if "/" in new_name or "\\" in new_name:
            self._show_error("名称不能包含路径分隔符（/ 或 \\）")
            return
        if new_name in (".", ".."):
            self._show_error("名称不能为 '.' 或 '..'")
            return
        if new_name == name:
            return  # 名称未变，静默跳过

        rel = (self._current_rel_path() + "/" + name).lstrip("/")
        self._worker = Worker(file_service.rename_file, self._current_disk_id, rel, new_name)
        self._worker.finished.connect(
            lambda _: (self._refresh(), self._show_success(f"已重命名为「{new_name}」"))
        )
        self._worker.error.connect(self._on_rename_or_move_error)
        self._worker.start()

    def _move_item(self, name: str, is_dir: bool) -> None:
        """
        移动文件或目录（v1.1.3 新增）。

        弹出目录选择弹窗（MoveTargetDialog），用户选定目标目录后提交到后端。
        """
        src_rel = (self._current_rel_path() + "/" + name).lstrip("/")
        dialog = MoveTargetDialog(self._current_disk_id, src_rel, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        dst_dir = dialog.getSelectedPath()
        if dst_dir is None:
            return

        # 客户端静默跳过：目标目录与当前目录相同
        if dst_dir == self._current_rel_path():
            return

        self._worker = Worker(
            file_service.move_file, self._current_disk_id, src_rel, dst_dir
        )
        self._worker.finished.connect(
            lambda _: (self._refresh(), self._show_success(f"「{name}」已移动"))
        )
        self._worker.error.connect(self._on_rename_or_move_error)
        self._worker.start()

    def _delete_item(self, name: str, is_dir: bool) -> None:
        """删除文件或目录"""
        kind = "目录" if is_dir else "文件"
        box = MessageBox("确认删除", f"确定要删除{kind}「{name}」吗？", self)
        if not box.exec():
            return

        rel = (self._current_rel_path() + "/" + name).lstrip("/")
        self._worker = Worker(file_service.delete_file, self._current_disk_id, rel)
        self._worker.finished.connect(lambda _: (self._refresh(), self._show_success("已删除")))
        self._worker.error.connect(lambda e: self._show_error(f"删除失败：{e}"))
        self._worker.start()

    # ── 辅助方法 ───────────────────────────────────────

    @staticmethod
    def _format_size(size: int) -> str:
        """将字节数格式化为易读字符串"""
        if size < 1024:
            return f"{size} B"
        if size < 1024 ** 2:
            return f"{size / 1024:.1f} KB"
        if size < 1024 ** 3:
            return f"{size / 1024 ** 2:.1f} MB"
        return f"{size / 1024 ** 3:.2f} GB"

    def _on_rename_or_move_error(self, msg: str) -> None:
        """
        重命名/移动操作失败时的统一错误处理（v1.1.4 新增）。

        根据错误类型显示对应的提示文案：
          - 404（目标资源不存在）：显示「目标资源不存在」
          - 其他：通用提示
        """
        if msg.startswith("[404]"):
            self._show_error("目标资源不存在")
            logger.warning("重命名/移动失败：目标资源不存在（%s）", msg)
        else:
            self._show_error(f"操作失败：{msg}")
            logger.warning("重命名/移动失败: %s", msg)

    def _show_error(self, msg: str) -> None:
        """显示错误通知"""
        InfoBar.error("错误", msg, parent=self, duration=4000,
                      position=InfoBarPosition.TOP_RIGHT)

    def _show_success(self, msg: str) -> None:
        """显示成功通知"""
        InfoBar.success("成功", msg, parent=self, duration=3000,
                        position=InfoBarPosition.TOP_RIGHT)

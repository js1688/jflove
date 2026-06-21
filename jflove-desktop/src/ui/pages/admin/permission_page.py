"""
权限配置页面（管理员专用）

功能：
  - 左侧：普通用户列表
  - 右侧：选中用户的磁盘权限表格（读/写/删 复选框）
  - 点击"保存权限"提交到服务端

注：v1.x 起移除"笔记目录权限"——所有登录用户均可使用笔记功能；
每个用户的笔记目录由自己在「设置」页面独立配置。
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget,
    QListWidgetItem, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QCheckBox, QHeaderView,
)
from PySide6.QtCore import Qt

from qfluentwidgets import (
    PrimaryPushButton, SubtitleLabel, BodyLabel, CardWidget,
    InfoBar, InfoBarPosition,
)

from src.services import user_service, disk_service, permission_service
from src.utils.worker import Worker
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PermissionPage(QWidget):
    """权限配置页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("permissionPage")
        self._users: list[dict] = []
        self._disks: list[dict] = []
        self._current_user: dict | None = None
        # disk_id → {"can_read": bool, "can_write": bool, "can_delete": bool}
        self._perm_cache: dict[int, dict] = {}
        self._worker = None
        self._setup_ui()

    # ── UI 构建 ────────────────────────────────────────

    def _setup_ui(self) -> None:
        """构建页面 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        layout.addWidget(SubtitleLabel("权限配置"))

        splitter = QSplitter(Qt.Horizontal)

        # ── 左侧：用户列表 ──
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(8)

        left_layout.addWidget(BodyLabel("选择用户"))
        self._user_list = QListWidget()
        self._user_list.currentItemChanged.connect(self._on_user_selected)
        left_layout.addWidget(self._user_list)

        refresh_btn = PrimaryPushButton("刷新列表")
        refresh_btn.clicked.connect(self.load_data)
        left_layout.addWidget(refresh_btn)

        left.setMinimumWidth(160)
        left.setMaximumWidth(240)
        splitter.addWidget(left)

        # ── 右侧：权限配置面板 ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        self._user_title = BodyLabel("请在左侧选择用户")
        right_layout.addWidget(self._user_title)

        # 磁盘权限表格
        disk_card = CardWidget()
        disk_card_layout = QVBoxLayout(disk_card)
        disk_card_layout.addWidget(BodyLabel("磁盘访问权限"))

        self._disk_table = QTableWidget()
        self._disk_table.setColumnCount(4)
        self._disk_table.setHorizontalHeaderLabels(["虚拟磁盘", "读取", "写入", "删除"])
        self._disk_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._disk_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._disk_table.verticalHeader().setVisible(False)
        disk_card_layout.addWidget(self._disk_table)
        right_layout.addWidget(disk_card)

        # 保存按钮
        self._save_btn = PrimaryPushButton("保存权限配置")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save_permissions)
        right_layout.addWidget(self._save_btn)
        right_layout.addStretch()

        splitter.addWidget(right)
        splitter.setSizes([200, 800])
        # stretch=1 让 splitter 吸收所有剩余垂直空间，否则 SubtitleLabel 与 splitter
        # 会按 sizePolicy 平分高度，导致顶部出现大片空白（与其它管理页表现不一致）
        layout.addWidget(splitter, 1)

    # ── 数据加载 ───────────────────────────────────────

    def load_data(self) -> None:
        """加载用户列表和磁盘列表"""
        self._worker = Worker(self._fetch_all_data)
        self._worker.finished.connect(self._on_data_loaded)
        self._worker.error.connect(lambda e: self._show_error(f"加载数据失败：{e}"))
        self._worker.start()

    @staticmethod
    def _fetch_all_data() -> tuple:
        """同步获取用户列表和磁盘列表（在后台线程执行）"""
        users = [u for u in user_service.list_users() if u["role"] != "admin"]
        disks = disk_service.list_disks()
        return users, disks

    def _on_data_loaded(self, data: tuple) -> None:
        """数据加载完成"""
        users, disks = data
        self._users = users
        self._disks = disks

        self._user_list.clear()
        for user in users:
            item = QListWidgetItem(user["username"])
            item.setData(Qt.UserRole, user)
            self._user_list.addItem(item)

    def _on_user_selected(self, current: QListWidgetItem, _) -> None:
        """选中用户，加载其权限配置"""
        if current is None:
            return
        self._current_user = current.data(Qt.UserRole)
        user_id = self._current_user["id"]
        self._user_title.setText(f"用户：{self._current_user['username']}")
        self._save_btn.setEnabled(True)

        self._worker = Worker(permission_service.get_user_disk_permissions, user_id)
        self._worker.finished.connect(self._on_perms_loaded)
        self._worker.error.connect(lambda e: self._show_error(f"加载权限失败：{e}"))
        self._worker.start()

    def _on_perms_loaded(self, perms: list) -> None:
        """权限列表加载完成，刷新磁盘权限表格"""
        # 将权限列表转换为 {disk_id: perm_dict} 格式
        self._perm_cache = {p["virtual_disk_id"]: p for p in perms}

        self._disk_table.setRowCount(0)
        for disk in self._disks:
            row = self._disk_table.rowCount()
            self._disk_table.insertRow(row)
            self._disk_table.setItem(row, 0, QTableWidgetItem(disk["name"]))

            perm = self._perm_cache.get(disk["id"], {})
            for col, key in enumerate(["can_read", "can_write", "can_delete"], start=1):
                cb = QCheckBox()
                cb.setChecked(bool(perm.get(key, False)))
                # 存储 disk_id 和权限 key
                cb.setProperty("disk_id", disk["id"])
                cb.setProperty("perm_key", key)
                container = QWidget()
                h = QHBoxLayout(container)
                h.setAlignment(Qt.AlignCenter)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(cb)
                self._disk_table.setCellWidget(row, col, container)

    # ── 保存权限 ───────────────────────────────────────

    def _on_save_permissions(self) -> None:
        """收集表格中的权限状态并批量提交到服务端"""
        if not self._current_user:
            return

        user_id = self._current_user["id"]

        def save_all():
            # 保存磁盘权限
            for row in range(self._disk_table.rowCount()):
                disk = self._disks[row]
                read_cb = self._disk_table.cellWidget(row, 1).findChild(QCheckBox)
                write_cb = self._disk_table.cellWidget(row, 2).findChild(QCheckBox)
                delete_cb = self._disk_table.cellWidget(row, 3).findChild(QCheckBox)

                can_read = read_cb.isChecked() if read_cb else False
                can_write = write_cb.isChecked() if write_cb else False
                can_delete = delete_cb.isChecked() if delete_cb else False

                if can_read or can_write or can_delete:
                    permission_service.set_disk_permission(
                        user_id, disk["id"], can_read, can_write, can_delete
                    )
                else:
                    # 所有权限取消时删除权限记录
                    try:
                        permission_service.delete_disk_permission(user_id, disk["id"])
                    except Exception:
                        pass

        self._worker = Worker(save_all)
        self._worker.finished.connect(lambda _: self._show_success("权限配置已保存"))
        self._worker.error.connect(lambda e: self._show_error(f"保存权限失败：{e}"))
        self._worker.start()

    # ── 辅助方法 ───────────────────────────────────────

    def _show_error(self, msg: str) -> None:
        InfoBar.error("错误", msg, parent=self, duration=4000,
                      position=InfoBarPosition.TOP_RIGHT)

    def _show_success(self, msg: str) -> None:
        InfoBar.success("成功", msg, parent=self, duration=3000,
                        position=InfoBarPosition.TOP_RIGHT)

"""
磁盘管理页面（管理员专用）

功能：
  - 虚拟磁盘列表（ID、名称、真实路径、创建时间）
  - 添加虚拟磁盘
  - 编辑虚拟磁盘
  - 删除虚拟磁盘
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QDialog, QFormLayout, QAbstractItemView, QHeaderView,
)
from PySide6.QtCore import Qt

from qfluentwidgets import (
    PushButton, LineEdit, SubtitleLabel, BodyLabel,
    InfoBar, InfoBarPosition, MessageBox, FluentIcon as FIF,
    PrimaryPushButton, ToolButton,
)

from src.services import disk_service
from src.utils.worker import Worker
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 表格列索引
_COL_ID = 0
_COL_NAME = 1
_COL_PATH = 2
_COL_CREATED_AT = 3
_COL_OPS = 4


class DiskPage(QWidget):
    """虚拟磁盘管理页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("diskPage")
        self._disks: list[dict] = []
        self._worker = None
        self._setup_ui()

    # ── UI 构建 ────────────────────────────────────────

    def _setup_ui(self) -> None:
        """构建页面 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        layout.addWidget(SubtitleLabel("磁盘管理"))

        hint = BodyLabel("虚拟磁盘是服务端真实目录的映射，用于文件管理功能。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 工具栏
        toolbar = QHBoxLayout()
        add_btn = PushButton(FIF.ADD, "添加虚拟磁盘")
        add_btn.clicked.connect(self._on_add_disk)
        toolbar.addWidget(add_btn)

        refresh_btn = PushButton(FIF.SYNC, "刷新")
        refresh_btn.clicked.connect(self.load_disks)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 磁盘列表表格
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["ID", "名称", "真实路径", "创建时间", "操作"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Fixed)
        self._table.setColumnWidth(4, 80)
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    # ── 数据加载 ───────────────────────────────────────

    def load_disks(self) -> None:
        """从服务端加载虚拟磁盘列表"""
        self._worker = Worker(disk_service.list_disks)
        self._worker.finished.connect(self._on_disks_loaded)
        self._worker.error.connect(lambda e: self._show_error(f"加载磁盘列表失败：{e}"))
        self._worker.start()

    def _on_disks_loaded(self, disks: list) -> None:
        """磁盘列表加载完成，刷新表格"""
        self._disks = disks
        self._table.setRowCount(0)
        for disk in disks:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, _COL_ID, QTableWidgetItem(str(disk["id"])))
            self._table.setItem(row, _COL_NAME, QTableWidgetItem(disk["name"]))
            self._table.setItem(row, _COL_PATH, QTableWidgetItem(disk["real_path"]))
            self._table.setItem(row, _COL_CREATED_AT,
                                QTableWidgetItem(str(disk.get("created_at", "─"))[:19]))

            # 操作按钮
            ops_widget = QWidget()
            ops_layout = QHBoxLayout(ops_widget)
            ops_layout.setContentsMargins(4, 0, 4, 0)
            ops_layout.setSpacing(2)
            ops_layout.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

            edit_btn = ToolButton(FIF.EDIT)
            edit_btn.setToolTip("编辑磁盘")
            edit_btn.clicked.connect(lambda _, d=disk: self._on_edit_disk(d))
            ops_layout.addWidget(edit_btn)

            del_btn = ToolButton(FIF.DELETE)
            del_btn.setToolTip("删除磁盘")
            del_btn.clicked.connect(lambda _, d=disk: self._on_delete_disk(d))
            ops_layout.addWidget(del_btn)

            self._table.setCellWidget(row, _COL_OPS, ops_widget)

    # ── 磁盘操作 ───────────────────────────────────────

    def _on_add_disk(self) -> None:
        """添加虚拟磁盘"""
        dialog = _DiskDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        name, real_path = dialog.get_values()
        self._worker = Worker(disk_service.create_disk, name, real_path)
        self._worker.finished.connect(
            lambda _: (self.load_disks(), self._show_success("虚拟磁盘已添加"))
        )
        self._worker.error.connect(lambda e: self._show_error(f"添加失败：{e}"))
        self._worker.start()

    def _on_edit_disk(self, disk: dict) -> None:
        """编辑虚拟磁盘"""
        dialog = _DiskDialog(self, disk["name"], disk["real_path"])
        if dialog.exec() != QDialog.Accepted:
            return
        name, real_path = dialog.get_values()
        self._worker = Worker(disk_service.update_disk, disk["id"], name, real_path)
        self._worker.finished.connect(
            lambda _: (self.load_disks(), self._show_success("虚拟磁盘已更新"))
        )
        self._worker.error.connect(lambda e: self._show_error(f"更新失败：{e}"))
        self._worker.start()

    def _on_delete_disk(self, disk: dict) -> None:
        """删除虚拟磁盘"""
        box = MessageBox(
            "确认删除",
            f"确定要删除虚拟磁盘「{disk['name']}」吗？\n删除后该磁盘的所有权限配置将同步清除。",
            self,
        )
        if not box.exec():
            return
        self._worker = Worker(disk_service.delete_disk, disk["id"])
        self._worker.finished.connect(
            lambda _: (self.load_disks(), self._show_success("虚拟磁盘已删除"))
        )
        self._worker.error.connect(lambda e: self._show_error(f"删除失败：{e}"))
        self._worker.start()

    # ── 辅助方法 ───────────────────────────────────────

    def _show_error(self, msg: str) -> None:
        InfoBar.error("错误", msg, parent=self, duration=4000,
                      position=InfoBarPosition.TOP_RIGHT)

    def _show_success(self, msg: str) -> None:
        InfoBar.success("成功", msg, parent=self, duration=3000,
                        position=InfoBarPosition.TOP_RIGHT)


class _DiskDialog(QDialog):
    """添加/编辑虚拟磁盘对话框"""

    def __init__(self, parent=None, name: str = "", real_path: str = ""):
        super().__init__(parent)
        is_edit = bool(name)
        self.setWindowTitle("编辑虚拟磁盘" if is_edit else "添加虚拟磁盘")
        self.setFixedSize(400, 210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        layout.addWidget(BodyLabel("请填写虚拟磁盘信息："))

        form = QFormLayout()
        self._name_input = LineEdit()
        self._name_input.setText(name)
        self._name_input.setPlaceholderText("如：文档库")
        form.addRow("磁盘名称：", self._name_input)

        self._path_input = LineEdit()
        self._path_input.setText(real_path)
        self._path_input.setPlaceholderText("服务端绝对路径，如 /data/docs")
        form.addRow("真实路径：", self._path_input)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        ok_btn = PrimaryPushButton("保存")
        ok_btn.clicked.connect(self._on_confirm)
        cancel_btn = PushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_confirm(self) -> None:
        """校验并提交"""
        if self._name_input.text().strip() and self._path_input.text().strip():
            self.accept()

    def get_values(self) -> tuple[str, str]:
        """
        获取表单中的磁盘名称和路径。

        :returns: (名称, 真实路径)
        """
        return self._name_input.text().strip(), self._path_input.text().strip()

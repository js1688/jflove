"""
用户管理页面（管理员专用）

功能：
  - 用户列表（ID、用户名、角色、启用状态、创建时间）
  - 添加普通用户
  - 删除用户
  - 修改密码
  - 启用/禁用用户
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QDialog, QFormLayout, QAbstractItemView, QHeaderView, QInputDialog,
)
from PySide6.QtCore import Qt

from qfluentwidgets import (
    PushButton, PasswordLineEdit, LineEdit, SubtitleLabel, BodyLabel,
    InfoBar, InfoBarPosition, MessageBox, FluentIcon as FIF,
    PrimaryPushButton, ToolButton,
)

from src.services import user_service
from src.utils.worker import Worker
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 表格列索引
_COL_ID = 0
_COL_USERNAME = 1
_COL_ROLE = 2
_COL_ENABLED = 3
_COL_CREATED_AT = 4


class UserPage(QWidget):
    """用户管理页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("userPage")
        self._users: list[dict] = []
        self._worker = None
        self._setup_ui()

    # ── UI 构建 ────────────────────────────────────────

    def _setup_ui(self) -> None:
        """构建页面 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        layout.addWidget(SubtitleLabel("用户管理"))

        # 工具栏
        toolbar = QHBoxLayout()
        add_btn = PushButton(FIF.ADD, "添加用户")
        add_btn.clicked.connect(self._on_add_user)
        toolbar.addWidget(add_btn)

        self._refresh_btn = PushButton(FIF.SYNC, "刷新")
        self._refresh_btn.clicked.connect(self.load_users)
        toolbar.addWidget(self._refresh_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 用户列表表格
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(["ID", "用户名", "角色", "状态", "创建时间", "操作"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.Fixed)
        self._table.setColumnWidth(5, 116)
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    # ── 数据加载 ───────────────────────────────────────

    def load_users(self) -> None:
        """从服务端加载用户列表"""
        self._worker = Worker(user_service.list_users)
        self._worker.finished.connect(self._on_users_loaded)
        self._worker.error.connect(lambda e: self._show_error(f"加载用户列表失败：{e}"))
        self._worker.start()

    def _on_users_loaded(self, users: list) -> None:
        """用户列表加载完成，刷新表格"""
        self._users = users
        self._table.setRowCount(0)
        for user in users:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, _COL_ID, QTableWidgetItem(str(user["id"])))
            self._table.setItem(row, _COL_USERNAME, QTableWidgetItem(user["username"]))
            role_cn = "管理员" if user["role"] == "admin" else "普通用户"
            self._table.setItem(row, _COL_ROLE, QTableWidgetItem(role_cn))
            enabled_cn = "✅ 启用" if user["enabled"] else "❌ 禁用"
            self._table.setItem(row, _COL_ENABLED, QTableWidgetItem(enabled_cn))
            self._table.setItem(row, _COL_CREATED_AT,
                                QTableWidgetItem(str(user.get("created_at", "─"))[:19]))

            # 操作按钮列（普通用户才显示）
            if user["role"] != "admin":
                ops_widget = QWidget()
                ops_layout = QHBoxLayout(ops_widget)
                ops_layout.setContentsMargins(4, 0, 4, 0)
                ops_layout.setSpacing(2)
                ops_layout.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

                pwd_btn = ToolButton(FIF.EDIT)
                pwd_btn.setToolTip("修改密码")
                pwd_btn.clicked.connect(lambda _, u=user: self._on_change_password(u))
                ops_layout.addWidget(pwd_btn)

                toggle_icon = FIF.CANCEL if user["enabled"] else FIF.ACCEPT
                toggle_tip = "禁用用户" if user["enabled"] else "启用用户"
                toggle_btn = ToolButton(toggle_icon)
                toggle_btn.setToolTip(toggle_tip)
                toggle_btn.clicked.connect(lambda _, u=user: self._on_toggle_enabled(u))
                ops_layout.addWidget(toggle_btn)

                del_btn = ToolButton(FIF.DELETE)
                del_btn.setToolTip("删除用户")
                del_btn.clicked.connect(lambda _, u=user: self._on_delete_user(u))
                ops_layout.addWidget(del_btn)

                self._table.setCellWidget(row, 5, ops_widget)

    # ── 用户操作 ───────────────────────────────────────

    def _on_add_user(self) -> None:
        """弹出对话框添加用户"""
        dialog = _AddUserDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        username, password = dialog.get_values()
        self._worker = Worker(user_service.create_user, username, password)
        self._worker.finished.connect(
            lambda _: (self.load_users(), self._show_success(f"用户 {username} 已创建"))
        )
        self._worker.error.connect(lambda e: self._show_error(f"创建用户失败：{e}"))
        self._worker.start()

    def _on_change_password(self, user: dict) -> None:
        """修改用户密码"""
        new_pwd, ok = QInputDialog.getText(
            self, "修改密码", f"请输入用户 {user['username']} 的新密码：",
        )
        if not ok or not new_pwd.strip():
            return
        self._worker = Worker(user_service.update_password, user["id"], new_pwd.strip())
        self._worker.finished.connect(lambda _: self._show_success("密码已更新"))
        self._worker.error.connect(lambda e: self._show_error(f"修改密码失败：{e}"))
        self._worker.start()

    def _on_toggle_enabled(self, user: dict) -> None:
        """切换用户启用状态"""
        new_state = not user["enabled"]
        action = "启用" if new_state else "禁用"
        self._worker = Worker(user_service.set_enabled, user["id"], new_state)
        self._worker.finished.connect(
            lambda _: (self.load_users(), self._show_success(f"已{action}用户 {user['username']}"))
        )
        self._worker.error.connect(lambda e: self._show_error(f"操作失败：{e}"))
        self._worker.start()

    def _on_delete_user(self, user: dict) -> None:
        """删除用户"""
        box = MessageBox("确认删除",
                         f"确定要删除用户「{user['username']}」吗？此操作不可恢复。",
                         self)
        if not box.exec():
            return
        self._worker = Worker(user_service.delete_user, user["id"])
        self._worker.finished.connect(
            lambda _: (self.load_users(), self._show_success("用户已删除"))
        )
        self._worker.error.connect(lambda e: self._show_error(f"删除用户失败：{e}"))
        self._worker.start()

    # ── 辅助方法 ───────────────────────────────────────

    def _show_error(self, msg: str) -> None:
        InfoBar.error("错误", msg, parent=self, duration=4000,
                      position=InfoBarPosition.TOP_RIGHT)

    def _show_success(self, msg: str) -> None:
        InfoBar.success("成功", msg, parent=self, duration=3000,
                        position=InfoBarPosition.TOP_RIGHT)


class _AddUserDialog(QDialog):
    """添加用户对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加用户")
        self.setFixedSize(340, 200)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        layout.addWidget(BodyLabel("创建普通用户账号"))

        form = QFormLayout()
        self._username_input = LineEdit()
        self._username_input.setPlaceholderText("用户名")
        form.addRow("用户名：", self._username_input)

        self._password_input = PasswordLineEdit()
        self._password_input.setPlaceholderText("密码（至少 8 个字符）")
        form.addRow("密码：", self._password_input)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        ok_btn = PrimaryPushButton("创建")
        ok_btn.clicked.connect(self._on_confirm)
        cancel_btn = PushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_confirm(self) -> None:
        """校验并提交"""
        if len(self._username_input.text().strip()) < 3:
            return
        if len(self._password_input.text()) < 8:
            return
        self.accept()

    def get_values(self) -> tuple[str, str]:
        """
        获取表单填写的用户名和密码。

        :returns: (用户名, 密码)
        """
        return self._username_input.text().strip(), self._password_input.text()

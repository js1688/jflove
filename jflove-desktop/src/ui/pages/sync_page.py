"""
同步目录管理页面

为当前用户管理"本地目录 ↔ 远端虚拟磁盘子目录"的同步配置：
  - 列表展示：配置名 / 本地目录 / 远端目标 / 自动同步开关 / 间隔 / 上次同步时间 / 状态 / 操作
  - 操作按钮：立即同步、编辑、删除
  - 顶部"新建配置"按钮：弹出对话框创建新配置
  - 同步引擎事件：实时反映 started / finished / error

**重要 UI 提示**：删除配置仅删除映射关系，不会删除任何实际文件；
本地手动删除文件后下一轮同步会从远端重新下载（删除远端文件须在文档管理页操作）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QFileDialog, QDialog, QFormLayout,
)
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PushButton, PrimaryPushButton, ToolButton,
    LineEdit, ComboBox, SpinBox, SwitchButton,
    InfoBar, InfoBarPosition, MessageBox, FluentIcon as FIF,
    CardWidget,
)

from src.services import sync_service, file_service
from src.utils.worker import Worker
from src.utils.sync_engine import sync_engine, SyncResult
from src.utils.icon import get_app_icon
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 时间格式化工具 ──────────────────────────────

def _format_time(iso_str: Optional[str]) -> str:
    """ISO 时间字符串转为 yyyy-MM-dd HH:MM 格式，空值显示 '从未'"""
    if not iso_str:
        return "从未"
    try:
        # 服务端返回 UTC ISO，转换为本地时间显示
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str


def _format_interval(seconds: int) -> str:
    """秒数 → 可读字符串"""
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    return f"{seconds // 3600} 小时"


# ── 创建/编辑对话框 ─────────────────────────────

class _SyncConfigDialog(QDialog):
    """新建 / 编辑同步配置的表单对话框"""

    def __init__(self, disks: list[dict], config: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self._disks = disks
        self._config = config  # None 表示新建
        self.setWindowTitle("编辑同步配置" if config else "新建同步配置")
        self.setWindowIcon(get_app_icon())
        self.resize(560, 480)
        self._result_data: Optional[dict] = None
        self._setup_ui()
        if config:
            self._load(config)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        layout.addWidget(SubtitleLabel("同步配置"))
        layout.addWidget(CaptionLabel(
            "提示：删除配置仅取消映射，不影响实际文件；"
            "本地误删文件后会在下一次同步从远端自动恢复。"
        ))

        form = QFormLayout()
        form.setSpacing(10)

        self._name_input = LineEdit()
        self._name_input.setPlaceholderText("起一个便于识别的名称，如：照片备份")
        form.addRow("配置名称", self._name_input)

        # 本地目录：行内放选择按钮
        local_row = QHBoxLayout()
        local_row.setSpacing(8)
        self._local_input = LineEdit()
        self._local_input.setPlaceholderText("点击右侧按钮选择本地目录")
        local_row.addWidget(self._local_input, stretch=1)
        browse_btn = ToolButton(FIF.FOLDER)
        browse_btn.setToolTip("选择本地目录")
        browse_btn.clicked.connect(self._on_browse_local)
        local_row.addWidget(browse_btn)
        local_holder = QWidget()
        local_holder.setLayout(local_row)
        form.addRow("本地目录", local_holder)

        # 远端磁盘
        self._disk_combo = ComboBox()
        self._disk_combo.setPlaceholderText("选择远端虚拟磁盘")
        for d in self._disks:
            self._disk_combo.addItem(d["name"], userData=d["id"])
        form.addRow("远端虚拟磁盘", self._disk_combo)

        self._remote_input = LineEdit()
        self._remote_input.setPlaceholderText("磁盘内子目录（留空表示根目录）")
        form.addRow("远端子目录", self._remote_input)

        # 自动同步
        self._auto_switch = SwitchButton()
        self._auto_switch.setOnText("开启")
        self._auto_switch.setOffText("关闭")
        form.addRow("自动同步", self._auto_switch)

        self._interval_spin = SpinBox()
        self._interval_spin.setRange(30, 86400)
        self._interval_spin.setSingleStep(30)
        self._interval_spin.setValue(300)
        self._interval_spin.setSuffix(" 秒")
        form.addRow("同步间隔", self._interval_spin)

        # 启用开关（编辑时可关闭整个配置）
        self._enabled_switch = SwitchButton()
        self._enabled_switch.setOnText("启用")
        self._enabled_switch.setOffText("禁用")
        self._enabled_switch.setChecked(True)
        form.addRow("启用配置", self._enabled_switch)

        layout.addLayout(form)
        layout.addStretch()

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = PushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = PrimaryPushButton("保存")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _on_browse_local(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本地同步目录")
        if path:
            self._local_input.setText(path)

    def _load(self, config: dict) -> None:
        """编辑模式下填充已有数据"""
        self._name_input.setText(config.get("name", ""))
        self._local_input.setText(config.get("local_path", ""))
        # 选中磁盘
        target_disk_id = config.get("disk_id")
        for i, d in enumerate(self._disks):
            if d["id"] == target_disk_id:
                self._disk_combo.setCurrentIndex(i)
                break
        self._remote_input.setText(config.get("remote_path", "") or "")
        self._auto_switch.setChecked(bool(config.get("auto_sync", False)))
        self._interval_spin.setValue(int(config.get("sync_interval", 300)))
        self._enabled_switch.setChecked(bool(config.get("enabled", True)))

    def _on_save(self) -> None:
        """收集表单数据，做基本校验后存入 _result_data 并 accept"""
        name = self._name_input.text().strip()
        local_path = self._local_input.text().strip()
        if not name:
            self._show_warning("请填写配置名称")
            return
        if not local_path:
            self._show_warning("请选择本地目录")
            return
        if self._disk_combo.currentIndex() < 0:
            self._show_warning("请选择远端虚拟磁盘")
            return
        disk_id = self._disk_combo.currentData()
        remote_path = self._remote_input.text().strip().strip("/")
        if self._interval_spin.value() < 30:
            self._show_warning("同步间隔不能小于 30 秒")
            return

        self._result_data = {
            "name": name,
            "local_path": local_path,
            "disk_id": int(disk_id),
            "remote_path": remote_path,
            "auto_sync": self._auto_switch.isChecked(),
            "sync_interval": self._interval_spin.value(),
            "enabled": self._enabled_switch.isChecked(),
        }
        self.accept()

    def _show_warning(self, msg: str) -> None:
        InfoBar.warning("提示", msg, parent=self,
                        duration=2500, position=InfoBarPosition.TOP)

    def get_result(self) -> Optional[dict]:
        return self._result_data


# ── 主页面 ─────────────────────────────────────

class SyncPage(QWidget):
    """同步目录管理页面"""

    # 表格列索引
    _COL_NAME = 0
    _COL_LOCAL = 1
    _COL_REMOTE = 2
    _COL_AUTO = 3
    _COL_INTERVAL = 4
    _COL_LAST = 5
    _COL_STATUS = 6
    _COL_ACTIONS = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("syncPage")
        self._configs: list[dict] = []
        self._disks: list[dict] = []
        self._row_status: dict[int, str] = {}  # config_id -> 临时状态文字
        self._worker: Optional[Worker] = None
        self._setup_ui()
        self._connect_engine()

    # ── UI 构建 ────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 标题 + 操作栏
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(SubtitleLabel("同步目录"))
        header.addStretch()
        refresh_btn = ToolButton(FIF.SYNC)
        refresh_btn.setToolTip("刷新配置列表")
        refresh_btn.clicked.connect(self.load_configs)
        header.addWidget(refresh_btn)
        new_btn = PrimaryPushButton(FIF.ADD, "新建配置")
        new_btn.clicked.connect(self._on_new_config)
        header.addWidget(new_btn)
        layout.addLayout(header)

        # 安全提示
        notice = CardWidget()
        nl = QVBoxLayout(notice)
        nl.setContentsMargins(16, 10, 16, 10)
        nl.setSpacing(4)
        nl.addWidget(StrongBodyLabel("同步规则"))
        nl.addWidget(BodyLabel(
            "本地有 / 远端无 → 上传；本地无 / 远端有 → 下载；"
            "两边都有则按修改时间取新者。"
        ))
        nl.addWidget(BodyLabel(
            "⚠ 任何方向都不会主动删除文件。本地误删后下一次同步会从远端自动补回；"
            "要删除远端文件，请前往「文档管理」页面手动删除。"
        ))
        layout.addWidget(notice)

        # 表格
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            "名称", "本地目录", "远端目标", "自动同步", "间隔",
            "上次同步", "状态", "操作",
        ])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(44)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(self._COL_NAME, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(self._COL_LOCAL, QHeaderView.Stretch)
        header_view.setSectionResizeMode(self._COL_REMOTE, QHeaderView.Stretch)
        header_view.setSectionResizeMode(self._COL_AUTO, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(self._COL_INTERVAL, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(self._COL_LAST, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(self._COL_STATUS, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(self._COL_ACTIONS, QHeaderView.Fixed)
        self._table.setColumnWidth(self._COL_ACTIONS, 130)
        layout.addWidget(self._table, stretch=1)

    # ── 信号连接 ───────────────────────────────────

    def _connect_engine(self) -> None:
        sync_engine.sync_started.connect(self._on_sync_started)
        sync_engine.sync_finished.connect(self._on_sync_finished)
        sync_engine.sync_error.connect(self._on_sync_error)

    # ── 数据加载 ───────────────────────────────────

    def load_configs(self) -> None:
        """异步加载配置列表 + 可访问磁盘列表，再渲染表格"""
        def task():
            return {
                "configs": sync_service.list_configs(),
                "disks": file_service.list_accessible_disks(),
            }
        self._worker = Worker(task)
        self._worker.finished.connect(self._on_data_loaded)
        self._worker.error.connect(lambda e: self._show_error(f"加载失败：{e}"))
        self._worker.start()

    def _on_data_loaded(self, data: dict) -> None:
        self._configs = data.get("configs", [])
        self._disks = data.get("disks", [])
        sync_engine.reload_configs(self._configs)
        self._refresh_table()

    def _disk_name(self, disk_id: int) -> str:
        """根据磁盘 ID 显示名称（找不到时返回占位）"""
        for d in self._disks:
            if d["id"] == disk_id:
                return d["name"]
        return f"磁盘#{disk_id}"

    def _refresh_table(self) -> None:
        """根据 self._configs 渲染整张表"""
        self._table.setRowCount(0)
        for cfg in self._configs:
            self._add_config_row(cfg)

    def _add_config_row(self, cfg: dict) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        cid = cfg["id"]

        self._table.setItem(row, self._COL_NAME, QTableWidgetItem(cfg.get("name", "")))
        local_item = QTableWidgetItem(cfg.get("local_path", ""))
        local_item.setToolTip(cfg.get("local_path", ""))
        self._table.setItem(row, self._COL_LOCAL, local_item)

        disk_name = self._disk_name(cfg.get("disk_id", 0))
        remote = cfg.get("remote_path") or ""
        remote_text = f"{disk_name} / {remote or '(根目录)'}"
        self._table.setItem(row, self._COL_REMOTE, QTableWidgetItem(remote_text))

        auto_text = "开" if cfg.get("auto_sync") and cfg.get("enabled") else "关"
        self._table.setItem(row, self._COL_AUTO, QTableWidgetItem(auto_text))

        interval_text = _format_interval(int(cfg.get("sync_interval", 300)))
        self._table.setItem(row, self._COL_INTERVAL, QTableWidgetItem(interval_text))

        last_text = _format_time(cfg.get("last_synced_at"))
        self._table.setItem(row, self._COL_LAST, QTableWidgetItem(last_text))

        status = self._row_status.get(cid, "空闲" if cfg.get("enabled") else "已禁用")
        self._table.setItem(row, self._COL_STATUS, QTableWidgetItem(status))

        # 操作按钮组
        actions = QWidget()
        h = QHBoxLayout(actions)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(4)
        sync_btn = ToolButton(FIF.SYNC)
        sync_btn.setToolTip("立即同步")
        sync_btn.clicked.connect(lambda _=False, c=cid: self._on_trigger_sync(c))
        h.addWidget(sync_btn)
        edit_btn = ToolButton(FIF.EDIT)
        edit_btn.setToolTip("编辑")
        edit_btn.clicked.connect(lambda _=False, c=cid: self._on_edit_config(c))
        h.addWidget(edit_btn)
        del_btn = ToolButton(FIF.DELETE)
        del_btn.setToolTip("删除（不会删除实际文件）")
        del_btn.clicked.connect(lambda _=False, c=cid: self._on_delete_config(c))
        h.addWidget(del_btn)
        h.addStretch()
        self._table.setCellWidget(row, self._COL_ACTIONS, actions)

    def _row_for(self, config_id: int) -> int:
        """返回某配置在表格中的行号，找不到返回 -1"""
        for row in range(self._table.rowCount()):
            item = self._table.item(row, self._COL_NAME)
            item_text = item.text() if item else ""
            cfg = next(
                (c for c in self._configs if c["name"] == item_text and c["id"] == config_id),
                None,
            )
            if cfg:
                return row
        # 兜底：通过映射查找
        for i, cfg in enumerate(self._configs):
            if cfg["id"] == config_id:
                return i
        return -1

    def _set_status(self, config_id: int, text: str) -> None:
        self._row_status[config_id] = text
        row = self._row_for(config_id)
        if row >= 0:
            self._table.setItem(row, self._COL_STATUS, QTableWidgetItem(text))

    # ── 操作处理 ──────────────────────────────────

    def _on_new_config(self) -> None:
        if not self._disks:
            self._show_error("尚无可访问的虚拟磁盘，请联系管理员分配权限")
            return
        dlg = _SyncConfigDialog(self._disks, parent=self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_result()
            if not data:
                return

            def task():
                return sync_service.create_config(
                    data["name"], data["local_path"], data["disk_id"],
                    data["remote_path"], data["auto_sync"], data["sync_interval"],
                )
            worker = Worker(task)
            worker.finished.connect(lambda _: (
                self._show_success("同步配置已创建"),
                self.load_configs(),
            ))
            worker.error.connect(lambda e: self._show_error(f"创建失败：{e}"))
            worker.start()
            self._worker = worker

    def _on_edit_config(self, config_id: int) -> None:
        cfg = next((c for c in self._configs if c["id"] == config_id), None)
        if not cfg:
            return
        dlg = _SyncConfigDialog(self._disks, config=cfg, parent=self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_result()
            if not data:
                return

            def task():
                sync_service.update_config(
                    config_id,
                    data["name"], data["local_path"], data["disk_id"],
                    data["remote_path"], data["auto_sync"], data["sync_interval"],
                    data["enabled"],
                )
            worker = Worker(task)
            worker.finished.connect(lambda _: (
                self._show_success("同步配置已更新"),
                self.load_configs(),
            ))
            worker.error.connect(lambda e: self._show_error(f"更新失败：{e}"))
            worker.start()
            self._worker = worker

    def _on_delete_config(self, config_id: int) -> None:
        cfg = next((c for c in self._configs if c["id"] == config_id), None)
        if not cfg:
            return
        box = MessageBox(
            "删除同步配置",
            f"确定要删除「{cfg.get('name', '')}」吗？\n\n"
            "提示：仅会删除映射关系，本地与远端的文件都不受影响。",
            self,
        )
        if not box.exec():
            return

        def task():
            sync_service.delete_config(config_id)
        worker = Worker(task)
        worker.finished.connect(lambda _: (
            self._show_success("同步配置已删除"),
            self.load_configs(),
        ))
        worker.error.connect(lambda e: self._show_error(f"删除失败：{e}"))
        worker.start()
        self._worker = worker

    def _on_trigger_sync(self, config_id: int) -> None:
        ok = sync_engine.trigger_sync(config_id)
        if not ok:
            self._show_warning("该配置已在同步中，请稍候")

    # ── 同步引擎事件 ──────────────────────────────

    def _on_sync_started(self, config_id: int) -> None:
        self._set_status(config_id, "正在同步…")

    def _on_sync_finished(self, result: SyncResult) -> None:
        self._set_status(
            result.config_id,
            f"完成 ↑{result.uploaded} ↓{result.downloaded} ⏭{result.skipped}",
        )
        # 重新拉取列表，刷新 last_synced_at
        self.load_configs()

    def _on_sync_error(self, config_id: int, msg: str) -> None:
        self._set_status(config_id, f"失败：{msg}")
        self._show_error(f"同步失败：{msg}")

    # ── 辅助 ──────────────────────────────────────

    def _show_error(self, msg: str) -> None:
        InfoBar.error("错误", msg, parent=self,
                      duration=4000, position=InfoBarPosition.TOP_RIGHT)

    def _show_success(self, msg: str) -> None:
        InfoBar.success("成功", msg, parent=self,
                        duration=3000, position=InfoBarPosition.TOP_RIGHT)

    def _show_warning(self, msg: str) -> None:
        InfoBar.warning("提示", msg, parent=self,
                        duration=2500, position=InfoBarPosition.TOP_RIGHT)

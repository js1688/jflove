"""
修复中心页面（v1.4.2 新增）

展示全平台共享的媒体修复任务列表，支持按状态执行操作：
  - 排队中/执行中：取消
  - 成功：验证播放（预览修复产物）、覆盖原文件（重点二次确认）、删除产物
  - 失败：重新修复（跳文件页重发起）、删除记录
  - 已取消/已覆盖：删除记录

权限说明：
  - 列表全平台共享（所有登录用户可见，含只读账号）
  - 操作统一校验磁盘写+删权限；无权限时按钮禁用，接口层仍会 403 兜底
  - 本页面通过磁盘列表（can_write + can_delete）判定操作可用性
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)

from qfluentwidgets import (
    SubtitleLabel, CaptionLabel, PushButton, PrimaryPushButton,
    FluentIcon as FIF, InfoBar, InfoBarPosition, MessageBox,
)

from src.services import file_service, repair_service
from src.components.preview_dialog import PreviewDialog
from src.utils.worker import Worker
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 任务状态中文映射（服务端状态 → 展示文案）
STATUS_TEXT = {
    "pending": "排队中",
    "running": "执行中",
    "verifying": "验证中",
    "success": "修复成功",
    "failed": "修复失败",
    "canceled": "已取消",
    "overridden": "已覆盖",
}
# 轮询间隔（秒，设计文档约定 2.5s）
POLL_INTERVAL_MS = 2500


def _format_size(size: int) -> str:
    """字节数格式化"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{size / 1024 ** 3:.2f} GB"


class RepairCenterPage(QWidget):
    """修复中心：任务列表 + 轮询 + 按状态操作"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("repairCenterPage")
        self._tasks: list[dict] = []
        self._can_repair_disks: dict[int, bool] = {}  # disk_id → 是否可操作
        self._worker: Optional[Worker] = None
        self._setup_ui()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.refresh)
        self._poll_timer.start()

    # ── UI 构建 ────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(SubtitleLabel("修复中心"))
        header.addStretch()
        self._refresh_btn = PushButton(FIF.SYNC, "刷新")
        self._refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self._refresh_btn)
        layout.addLayout(header)

        hint = CaptionLabel(
            "对损坏的视频/音频发起离线修复：修复产物验证播放满意后，"
            "可覆盖原文件（原文件将被直接删除、不可恢复）。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 任务表格：文件名 / 状态 / 进度 / 创建者 / 大小 / 错误 / 操作
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["文件名", "状态", "进度", "创建者", "源大小", "错误信息"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.Stretch
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._selected_changed)
        layout.addWidget(self._table, stretch=1)

        # 操作按钮行（针对选中任务，按状态启用）
        btn_row = QHBoxLayout()
        self._cancel_btn = PushButton(FIF.CANCEL, "取消")
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        self._verify_btn = PrimaryPushButton(FIF.PLAY, "验证播放")
        self._verify_btn.clicked.connect(self._on_verify)
        btn_row.addWidget(self._verify_btn)
        self._override_btn = PrimaryPushButton(FIF.COMPLETED, "覆盖原文件")
        self._override_btn.clicked.connect(self._on_override)
        btn_row.addWidget(self._override_btn)
        self._delete_artifact_btn = PushButton(FIF.DELETE, "删除产物")
        self._delete_artifact_btn.clicked.connect(self._on_delete_artifact)
        btn_row.addWidget(self._delete_artifact_btn)
        self._delete_record_btn = PushButton(FIF.DELETE, "删除记录")
        self._delete_record_btn.clicked.connect(self._on_delete_record)
        btn_row.addWidget(self._delete_record_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    # ── 数据加载 ────────────────────────────────────────

    def refresh(self) -> None:
        """异步拉取任务列表（轮询与手动刷新共用；避免重入）"""
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = Worker(repair_service.list_tasks, 1, 100)
        self._worker.finished.connect(self._on_tasks_loaded)
        self._worker.error.connect(self._on_load_error)
        self._worker.start()

    def _on_tasks_loaded(self, data: dict) -> None:
        self._tasks = data.get("tasks", [])
        # 磁盘操作权限（写+删并存才可操作；admin 由服务端放行）
        try:
            disks = file_service.list_accessible_disks()
            self._can_repair_disks = {
                d["id"]: bool(d.get("can_write")) and bool(d.get("can_delete"))
                for d in disks
            }
        except Exception:  # noqa: BLE001 - 权限查询失败时全部禁用，接口 403 兜底
            self._can_repair_disks = {}
        self._rebuild_table()

    def _on_load_error(self, msg: str) -> None:
        logger.error("修复任务列表加载失败：%s", msg)

    def _rebuild_table(self) -> None:
        """按最新任务列表重建表格"""
        self._table.setRowCount(len(self._tasks))
        for row, task in enumerate(self._tasks):
            status = task.get("status", "")
            items = [
                QTableWidgetItem(task.get("filename", "")),
                QTableWidgetItem(STATUS_TEXT.get(status, status)),
                QTableWidgetItem(
                    f"{task.get('progress', 0)}%"
                    if status in ("running", "verifying")
                    else "—"
                ),
                QTableWidgetItem(task.get("username", "")),
                QTableWidgetItem(_format_size(task.get("source_size", 0))),
                QTableWidgetItem(task.get("error_message", "")),
            ]
            for col, item in enumerate(items):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(row, col, item)
        self._update_buttons()

    def _selected_task(self) -> Optional[dict]:
        """当前选中行的任务字典；未选中返回 None"""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._tasks):
            return None
        return self._tasks[row]

    def _update_buttons(self) -> None:
        """按选中任务状态 + 磁盘权限刷新按钮可用性"""
        task = self._selected_task()
        if not task:
            for btn in (
                self._cancel_btn, self._verify_btn, self._override_btn,
                self._delete_artifact_btn, self._delete_record_btn,
            ):
                btn.setEnabled(False)
            return
        status = task.get("status", "")
        can_operate = self._can_repair_disks.get(task.get("disk_id", 0), False)
        self._cancel_btn.setEnabled(
            can_operate and status in ("pending", "running", "verifying")
        )
        self._verify_btn.setEnabled(status == "success")
        self._override_btn.setEnabled(can_operate and status == "success")
        self._delete_artifact_btn.setEnabled(can_operate and status == "success")
        terminal = status in ("failed", "canceled", "overridden")
        self._delete_record_btn.setEnabled(terminal)

    def _selected_changed(self) -> None:
        """选中行变化时刷新按钮状态"""
        self._update_buttons()

    # ── 操作处理 ────────────────────────────────────────

    def _run_op(self, fn, *args, success_msg: str) -> None:
        """异步执行修复操作，成功提示并刷新列表"""
        task = self._selected_task()
        if not task:
            return
        self._worker = Worker(fn, *args)
        self._worker.finished.connect(
            lambda _: self._op_done(success_msg)
        )
        self._worker.error.connect(
            lambda e: InfoBar.error(
                title="操作失败", content=str(e), orient=Qt.Horizontal,
                isClosable=True, position=InfoBarPosition.TOP, duration=5000,
                parent=self,
            )
        )
        self._worker.start()

    def _op_done(self, msg: str) -> None:
        InfoBar.success(
            title=msg, content="", orient=Qt.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=3000, parent=self,
        )
        self.refresh()

    def _on_cancel(self) -> None:
        task = self._selected_task()
        if task and self._confirm(
            "取消修复任务", f"确认取消「{task['filename']}」的修复任务？"
        ):
            self._run_op(
                repair_service.cancel_task, task["id"], success_msg="任务已取消"
            )

    def _on_verify(self) -> None:
        """验证播放：打开修复产物预览（走 /stream?repair_task_id 加密流）"""
        task = self._selected_task()
        if not task:
            return
        dialog = PreviewDialog(
            task["disk_id"], task.get("filename", ""),
            task.get("filename", ""),
            repair_task_id=task["id"],
            parent=self,
        )
        dialog.exec()

    def _on_override(self) -> None:
        """覆盖原文件：重点二次确认（原文件将被删除、不可恢复）"""
        task = self._selected_task()
        if not task:
            return
        box = MessageBox(
            "覆盖原文件（不可恢复）",
            f"即将用修复产物覆盖「{task['filename']}」。\n\n"
            "⚠ 原损坏文件将被直接删除、无法恢复！\n"
            "请确认已通过「验证播放」确认修复产物可用。",
            self.window(),
        )
        box.yesButton.setText("确认覆盖")
        box.cancelButton.setText("取消")
        if box.exec():
            self._run_op(
                repair_service.override_origin, task["id"],
                success_msg="已覆盖原文件",
            )

    def _on_delete_artifact(self) -> None:
        task = self._selected_task()
        if task and self._confirm(
            "删除修复产物", f"确认删除「{task['filename']}」的修复产物？"
        ):
            self._run_op(
                repair_service.delete_artifact, task["id"],
                success_msg="产物已删除",
            )

    def _on_delete_record(self) -> None:
        task = self._selected_task()
        if task and self._confirm(
            "删除任务记录", f"确认删除「{task['filename']}」的任务记录？"
        ):
            self._run_op(
                repair_service.delete_record, task["id"],
                success_msg="记录已删除",
            )

    def _confirm(self, title: str, text: str) -> bool:
        """通用确认弹窗，返回用户是否确认"""
        box = MessageBox(title, text, self.window())
        box.yesButton.setText("确认")
        box.cancelButton.setText("取消")
        return bool(box.exec())

    def showEvent(self, event) -> None:  # noqa: N802
        """页面显示时立即刷新任务列表"""
        super().showEvent(event)
        self.refresh()

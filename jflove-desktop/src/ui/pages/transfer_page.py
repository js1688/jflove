"""
传输任务管理页面

集中展示所有上传/下载任务的进度与状态。

功能：
  - 顶部统计区：总任务数、进行中数、已完成数
  - 中部任务列表（每行一个任务）：图标、文件名、进度条、状态、字节数、操作按钮
  - 顶部"清除已完成"按钮：移除所有已结束任务（成功/失败/取消）

实现要点：
  - 订阅 transfer_manager 的 task_added / task_updated / task_removed 信号
  - 每个任务对应一个 TransferTaskRow（CardWidget），通过 task_id 索引
  - 任务行内部使用 ProgressBar、PushButton 等 qfluentwidgets 组件
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt

from qfluentwidgets import (
    SubtitleLabel, BodyLabel, StrongBodyLabel, CaptionLabel,
    CardWidget, ProgressBar, PushButton, ToolButton,
    InfoBar, InfoBarPosition, FluentIcon as FIF,
)

from src.utils.transfer_manager import (
    transfer_manager, TransferTask, TaskKind, TaskStatus,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 辅助：人类可读的字节数 ─────────────────────
def _format_size(size: int) -> str:
    """将字节数格式化为易读字符串"""
    if size < 0:
        return "0 B"
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{size / 1024 ** 3:.2f} GB"


# ── 状态文本映射 ──────────────────────────────
_STATUS_LABEL = {
    TaskStatus.PENDING: "等待中",
    TaskStatus.HASHING: "校验中",
    TaskStatus.RUNNING: "传输中",
    TaskStatus.COMPLETED: "已完成",
    TaskStatus.FAILED: "失败",
    TaskStatus.CANCELLED: "已取消",
}


class TransferTaskRow(CardWidget):
    """
    单个传输任务的展示行。

    包含：上下行图标 + 文件名 + 进度条 + 状态/字节数 + 取消按钮。
    """

    def __init__(self, task: TransferTask, parent=None):
        super().__init__(parent)
        self._task_id = task.id
        self._setup_ui(task)
        self.refresh(task)

    def _setup_ui(self, task: TransferTask) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # 顶部：图标 + 文件名 + 状态 + 取消按钮
        top = QHBoxLayout()
        top.setSpacing(8)

        # 上传/下载方向图标
        kind_icon = FIF.UP if task.kind == TaskKind.UPLOAD else FIF.DOWN
        kind_btn = ToolButton(kind_icon)
        kind_btn.setEnabled(False)
        kind_btn.setFixedSize(28, 28)
        top.addWidget(kind_btn)

        self._filename_label = StrongBodyLabel(task.filename)
        self._filename_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._filename_label.setToolTip(task.local_path)
        top.addWidget(self._filename_label, stretch=1)

        self._status_label = BodyLabel("")
        top.addWidget(self._status_label)

        self._action_btn = ToolButton(FIF.CLOSE)
        self._action_btn.setToolTip("取消任务")
        self._action_btn.setFixedSize(28, 28)
        self._action_btn.clicked.connect(self._on_cancel)
        top.addWidget(self._action_btn)

        layout.addLayout(top)

        # 进度条
        self._progress_bar = ProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        # 底部：字节数 / 错误信息
        self._detail_label = CaptionLabel("")
        self._detail_label.setWordWrap(True)
        layout.addWidget(self._detail_label)

    def task_id(self) -> str:
        """返回此行对应的任务 ID"""
        return self._task_id

    def refresh(self, task: TransferTask) -> None:
        """根据最新任务状态刷新本行展示"""
        self._filename_label.setText(task.filename)
        self._status_label.setText(_STATUS_LABEL.get(task.status, task.status.value))
        self._progress_bar.setValue(task.percent)

        # 详情：传输字节 / 错误信息
        if task.status == TaskStatus.FAILED and task.error:
            self._detail_label.setText(f"错误：{task.error}")
        elif task.file_size > 0:
            self._detail_label.setText(
                f"{_format_size(task.transferred)} / {_format_size(task.file_size)}"
                f"（{task.percent}%）"
            )
        else:
            self._detail_label.setText("等待开始…")

        # 操作按钮：进行中显示取消，已结束变为不可点
        if task.status in (
            TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.HASHING,
        ):
            self._action_btn.setEnabled(True)
            self._action_btn.setToolTip("取消任务")
        else:
            self._action_btn.setEnabled(False)
            self._action_btn.setToolTip("任务已结束")

    def _on_cancel(self) -> None:
        """点击取消按钮"""
        transfer_manager.cancel(self._task_id)


class TransferPage(QWidget):
    """传输任务列表页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("transferPage")
        self._rows: dict[str, TransferTaskRow] = {}
        self._setup_ui()
        self._connect_signals()
        # 初始化加载已有任务
        for task in transfer_manager.tasks:
            self._on_task_added(task)
        self._update_summary()

    # ── UI 构建 ────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 标题 + 操作栏
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(SubtitleLabel("传输任务"))
        header.addStretch()

        self._summary_label = BodyLabel("")
        header.addWidget(self._summary_label)

        clear_btn = PushButton(FIF.DELETE, "清除已完成")
        clear_btn.clicked.connect(self._on_clear_finished)
        header.addWidget(clear_btn)

        layout.addLayout(header)

        # 任务列表（滚动容器）
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(scroll)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._list_container)

        # 空状态提示（默认显示，有任务后隐藏）
        self._empty_label = BodyLabel("暂无传输任务。可在「文档管理」页面发起上传或下载。")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._list_layout.addWidget(self._empty_label)

    def _connect_signals(self) -> None:
        """连接 transfer_manager 的任务变化信号"""
        transfer_manager.task_added.connect(self._on_task_added)
        transfer_manager.task_updated.connect(self._on_task_updated)
        transfer_manager.task_removed.connect(self._on_task_removed)

    # ── 信号回调 ───────────────────────────────────

    def _on_task_added(self, task: TransferTask) -> None:
        """新任务加入：创建行并插入到列表顶部"""
        row = TransferTaskRow(task, self._list_container)
        self._rows[task.id] = row
        # 新任务始终插到列表顶部，最新可见
        self._list_layout.insertWidget(0, row)
        self._empty_label.setVisible(False)
        self._update_summary()

    def _on_task_updated(self, task_id: str) -> None:
        """任务进度/状态变化：刷新对应行"""
        row = self._rows.get(task_id)
        if not row:
            return
        task = next((t for t in transfer_manager.tasks if t.id == task_id), None)
        if task:
            row.refresh(task)
        self._update_summary()

    def _on_task_removed(self, task_id: str) -> None:
        """任务被移除：从列表中删除对应行"""
        row = self._rows.pop(task_id, None)
        if row is not None:
            self._list_layout.removeWidget(row)
            row.deleteLater()
        if not self._rows:
            self._empty_label.setVisible(True)
        self._update_summary()

    # ── 辅助 ───────────────────────────────────────

    def _update_summary(self) -> None:
        """更新顶部任务概览"""
        stats = transfer_manager.stats()
        total = stats.get("total", 0)
        running = stats.get(TaskStatus.RUNNING.value, 0) + stats.get(TaskStatus.HASHING.value, 0)
        pending = stats.get(TaskStatus.PENDING.value, 0)
        completed = stats.get(TaskStatus.COMPLETED.value, 0)
        failed = stats.get(TaskStatus.FAILED.value, 0)
        cancelled = stats.get(TaskStatus.CANCELLED.value, 0)
        self._summary_label.setText(
            f"共 {total} 个 ｜ 进行中 {running} ｜ 等待 {pending} ｜ "
            f"完成 {completed} ｜ 失败 {failed} ｜ 取消 {cancelled}"
        )

    def _on_clear_finished(self) -> None:
        """清除已结束任务"""
        before = len(self._rows)
        transfer_manager.clear_finished()
        after = len(self._rows)
        InfoBar.success(
            "已清理",
            f"已清除 {before - after} 个已结束任务",
            parent=self, duration=2500,
            position=InfoBarPosition.TOP_RIGHT,
        )

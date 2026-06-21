"""
文件传输任务管理器（单例）

职责：
  - 维护上传/下载任务列表
  - 控制并发数（最大同时进行 MAX_CONCURRENT 个传输）
  - 调度等待中的任务
  - 通过 Qt 信号广播任务状态变化，供 UI 展示

使用方式：
    from src.utils.transfer_manager import transfer_manager, TaskKind, TaskStatus
    task_id = transfer_manager.submit_upload(disk_id, rel_dir, local_path)
    transfer_manager.task_added.connect(...)
    transfer_manager.task_updated.connect(...)
    transfer_manager.cancel(task_id)
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QObject, QThread, Signal

from src.services import file_service
from src.utils.logger import get_logger

logger = get_logger(__name__)


class TaskKind(str, Enum):
    """传输任务类型"""

    UPLOAD = "upload"
    DOWNLOAD = "download"


class TaskStatus(str, Enum):
    """
    传输任务状态。

    PENDING   等待调度（队列中，尚未开始）
    HASHING   仅上传：正在做 SHA256 校验
    RUNNING   传输中（正在上传或下载分片/字节）
    COMPLETED 成功完成
    FAILED    失败（含异常）
    CANCELLED 用户取消
    """

    PENDING = "pending"
    HASHING = "hashing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TransferTask:
    """传输任务数据对象，由 TransferManager 持有，UI 直接读取展示"""

    id: str
    kind: TaskKind
    filename: str           # 仅作为 UI 展示用的文件名
    disk_id: int
    rel_path: str           # 服务端相对路径（上传：父目录；下载：文件相对路径）
    local_path: str         # 本地完整路径（上传：源路径；下载：保存路径）
    file_size: int = 0      # 总字节数（下载时如未知响应头则可能为 0）
    transferred: int = 0    # 已传输字节数
    status: TaskStatus = TaskStatus.PENDING
    error: str = ""
    # 仅下载任务使用：服务端文件的 mtime（Unix 时间戳），写入本地后还原
    # 目的：让目录同步在下一轮扫描时识别"未变更"，避免重复下载
    remote_mtime: float = 0.0
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    @property
    def percent(self) -> int:
        """计算进度百分比（0-100），总大小为 0 时返回 0"""
        if self.file_size <= 0:
            return 0
        return min(100, int(self.transferred * 100 / self.file_size))


class _TransferWorker(QThread):
    """
    单个传输任务的工作线程。

    内部信号通过 TransferManager 进行中转，外部不应直接连接。
    """

    progress = Signal(str, int, int, str)  # task_id, transferred, total, phase
    completed = Signal(str)                 # task_id
    failed = Signal(str, str)               # task_id, error
    cancelled = Signal(str)                 # task_id

    def __init__(self, task: TransferTask):
        super().__init__()
        self._task = task
        self._cancelled = False

    def request_cancel(self) -> None:
        """请求取消（线程安全）"""
        self._cancelled = True

    # ── QThread 入口 ───────────────────────────────

    def run(self) -> None:
        try:
            if self._task.kind == TaskKind.UPLOAD:
                self._do_upload()
            else:
                self._do_download()
            if self._cancelled:
                self.cancelled.emit(self._task.id)
            else:
                self.completed.emit(self._task.id)
        except _CancelledError:
            self.cancelled.emit(self._task.id)
        except ValueError as e:
            # file_service 用 ValueError 表示用户取消
            if "取消" in str(e):
                self.cancelled.emit(self._task.id)
            else:
                logger.error("传输任务异常 %s: %s", self._task.id, e)
                self.failed.emit(self._task.id, str(e))
        except Exception as e:
            logger.error("传输任务异常 %s: %s", self._task.id, e)
            self.failed.emit(self._task.id, str(e))

    # ── 内部任务执行 ────────────────────────────────

    def _do_upload(self) -> None:
        """执行上传任务"""
        def progress_cb(done: int, total: int, phase: str) -> None:
            self.progress.emit(self._task.id, done, total, phase)

        def cancelled_flag() -> bool:
            return self._cancelled

        file_service.upload_file(
            self._task.disk_id,
            self._task.rel_path,
            self._task.local_path,
            progress_callback=progress_cb,
            cancelled_flag=cancelled_flag,
        )

    def _do_download(self) -> None:
        """执行下载任务"""
        def progress_cb(done: int, total: int, phase: str) -> None:
            self.progress.emit(self._task.id, done, total, phase)

        def cancelled_flag() -> bool:
            return self._cancelled

        file_service.download_file(
            self._task.disk_id,
            self._task.rel_path,
            self._task.local_path,
            progress_callback=progress_cb,
            cancelled_flag=cancelled_flag,
            restore_mtime=self._task.remote_mtime or None,
        )


class _CancelledError(Exception):
    """内部使用：标识取消引发的中止"""


class TransferManager(QObject):
    """
    传输任务管理器（全局单例）。

    :signal task_added(TransferTask): 新任务被加入
    :signal task_updated(str): 任务状态/进度变化（携带 task_id）
    :signal task_removed(str): 任务从列表中移除（清除已完成）
    """

    task_added = Signal(object)
    task_updated = Signal(str)
    task_removed = Signal(str)

    # 最大并发任务数（上传/下载共享此配额）
    MAX_CONCURRENT = 3

    def __init__(self):
        super().__init__()
        self._tasks: dict[str, TransferTask] = {}
        self._workers: dict[str, _TransferWorker] = {}
        self._task_order: list[str] = []  # 维持创建顺序，UI 按此顺序渲染

    # ── 公开接口 ───────────────────────────────────

    @property
    def tasks(self) -> list[TransferTask]:
        """按创建顺序返回所有任务的快照"""
        return [self._tasks[tid] for tid in self._task_order if tid in self._tasks]

    def submit_upload(self, disk_id: int, rel_dir: str, local_path: str) -> str:
        """
        提交上传任务。

        :param disk_id: 目标虚拟磁盘 ID
        :param rel_dir: 服务端目标目录相对路径
        :param local_path: 本地文件绝对路径
        :returns: 任务 ID
        """
        task = TransferTask(
            id=self._new_task_id(),
            kind=TaskKind.UPLOAD,
            filename=os.path.basename(local_path),
            disk_id=disk_id,
            rel_path=rel_dir,
            local_path=local_path,
            file_size=os.path.getsize(local_path) if os.path.exists(local_path) else 0,
        )
        self._add_task(task)
        return task.id

    def submit_download(
        self,
        disk_id: int,
        rel_path: str,
        local_save_path: str,
        filename: str = "",
        file_size: int = 0,
        remote_mtime: float = 0.0,
    ) -> str:
        """
        提交下载任务。

        :param disk_id: 虚拟磁盘 ID
        :param rel_path: 服务端文件相对路径
        :param local_save_path: 本地保存绝对路径
        :param filename: 显示用文件名（默认从 rel_path 提取）
        :param file_size: 已知文件大小（来自 list 接口，可省略）
        :param remote_mtime: 服务端 mtime（Unix 时间戳）。下载完成后会调用
                             os.utime 把本地文件 mtime 还原到该值，让目录同步
                             在下一轮扫描时识别"未变更"，避免反复下载
        :returns: 任务 ID
        """
        task = TransferTask(
            id=self._new_task_id(),
            kind=TaskKind.DOWNLOAD,
            filename=filename or os.path.basename(rel_path),
            disk_id=disk_id,
            rel_path=rel_path,
            local_path=local_save_path,
            file_size=file_size,
            remote_mtime=remote_mtime,
        )
        self._add_task(task)
        return task.id

    def cancel(self, task_id: str) -> None:
        """
        请求取消任务。

        - 进行中：通知 worker 在下一个分片/字节边界退出
        - 等待中：直接置为 CANCELLED
        """
        task = self._tasks.get(task_id)
        if not task:
            return
        worker = self._workers.get(task_id)
        if worker is not None:
            worker.request_cancel()
        else:
            task.status = TaskStatus.CANCELLED
            task.finished_at = time.time()
            self.task_updated.emit(task_id)
            self._try_dispatch()

    def clear_finished(self) -> None:
        """清除所有已结束（成功/失败/取消）的任务"""
        finished = [
            tid for tid, t in self._tasks.items()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        ]
        for tid in finished:
            self._tasks.pop(tid, None)
            if tid in self._task_order:
                self._task_order.remove(tid)
            self.task_removed.emit(tid)

    def stats(self) -> dict[str, int]:
        """统计各状态任务数量，便于 UI 显示概览"""
        result = {s.value: 0 for s in TaskStatus}
        for t in self._tasks.values():
            result[t.status.value] += 1
        result["total"] = len(self._tasks)
        return result

    # ── 内部实现 ───────────────────────────────────

    @staticmethod
    def _new_task_id() -> str:
        """生成短 ID（前 8 位 uuid）"""
        return uuid.uuid4().hex[:8]

    def _add_task(self, task: TransferTask) -> None:
        """将任务加入队列并尝试调度"""
        self._tasks[task.id] = task
        self._task_order.append(task.id)
        logger.info("传输任务已提交 %s [%s] %s", task.id, task.kind.value, task.filename)
        self.task_added.emit(task)
        self._try_dispatch()

    def _running_count(self) -> int:
        """当前进行中（含 hashing）的任务数"""
        return sum(
            1 for t in self._tasks.values()
            if t.status in (TaskStatus.RUNNING, TaskStatus.HASHING)
        )

    def _try_dispatch(self) -> None:
        """尝试调度等待中的任务，直到达到并发上限"""
        while self._running_count() < self.MAX_CONCURRENT:
            next_task = next(
                (self._tasks[tid] for tid in self._task_order
                 if tid in self._tasks and self._tasks[tid].status == TaskStatus.PENDING),
                None,
            )
            if not next_task:
                break
            self._start_task(next_task)

    def _start_task(self, task: TransferTask) -> None:
        """实际启动 worker 线程"""
        worker = _TransferWorker(task)
        worker.progress.connect(self._on_progress)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        self._workers[task.id] = worker
        # 启动前先置为 RUNNING，避免下次调度重复挑中
        task.status = TaskStatus.RUNNING
        self.task_updated.emit(task.id)
        worker.start()

    # ── worker 信号回调 ────────────────────────────

    def _on_progress(self, task_id: str, transferred: int, total: int, phase: str) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.transferred = transferred
        if total > 0:
            task.file_size = total
        if phase == "hashing":
            task.status = TaskStatus.HASHING
        elif phase in ("uploading", "downloading"):
            task.status = TaskStatus.RUNNING
        self.task_updated.emit(task_id)

    def _on_completed(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.COMPLETED
            task.transferred = task.file_size
            task.finished_at = time.time()
            self.task_updated.emit(task_id)
        self._cleanup_worker(task_id)
        self._try_dispatch()

    def _on_failed(self, task_id: str, error: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.error = error
            task.finished_at = time.time()
            self.task_updated.emit(task_id)
        self._cleanup_worker(task_id)
        self._try_dispatch()

    def _on_cancelled(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.CANCELLED
            task.finished_at = time.time()
            self.task_updated.emit(task_id)
        self._cleanup_worker(task_id)
        self._try_dispatch()

    def _cleanup_worker(self, task_id: str) -> None:
        """工作线程结束后清理引用"""
        worker = self._workers.pop(task_id, None)
        if worker is not None:
            worker.deleteLater()


# 全局单例（跨模块共享）
transfer_manager = TransferManager()

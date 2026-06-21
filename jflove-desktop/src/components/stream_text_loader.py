"""
文本流式加载器模块

StreamTextLoader 在独立 QThread 中调用 file_service.stream_range()，
逐帧解密并 emit chunk_ready 信号，UI 线程 append 到 QPlainTextEdit。
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from src.services import file_service
from src.utils.logger import get_logger

logger = get_logger(__name__)


class StreamTextLoader(QThread):
    """
    文本流式加载线程。

    Signals:
        meta_ready(dict)   — 收到服务端元数据帧时 emit（含 file_size / content_type）
        chunk_ready(bytes) — 每收到一块解密明文时 emit
        loaded()           — 加载正常结束（v1.1.1 改名：避免与 QThread 内置
                             finished 信号冲突，详见 v1.1.0 测试报告 BUG-1）
        error(str)         — 出错时 emit 错误描述
    """

    meta_ready = Signal(object)   # dict
    chunk_ready = Signal(object)  # bytes
    loaded = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._disk_id: int = 0
        self._path: str = ""
        self._filename: str = ""
        self._range_start: int = 0
        self._range_end: int = -1
        self._cancelled: bool = False

    def start_loading(
        self,
        disk_id: int,
        path: str,
        filename: str,
        range_start: int = 0,
        range_end: int = -1,
    ) -> None:
        """
        配置加载参数并启动线程。

        :param disk_id: 虚拟磁盘 ID
        :param path: 文件目录（磁盘相对路径）
        :param filename: 文件名
        :param range_start: 字节起点（0=开头）
        :param range_end: 字节终点不含（-1=文件结尾）
        """
        self._disk_id = disk_id
        self._path = path
        self._filename = filename
        self._range_start = range_start
        self._range_end = range_end
        self._cancelled = False
        self.start()

    def cancel(self) -> None:
        """
        请求取消加载，设置取消标志；当前帧读取完成后线程退出。
        """
        self._cancelled = True

    def run(self) -> None:
        """线程主体：调用 stream_range 并逐帧 emit"""
        try:
            meta, frame_iter = file_service.stream_range(
                self._disk_id,
                self._path,
                self._filename,
                self._range_start,
                self._range_end,
            )
            self.meta_ready.emit(meta)

            for chunk in frame_iter:
                if self._cancelled:
                    break
                self.chunk_ready.emit(chunk)

        except Exception as e:
            if not self._cancelled:
                logger.error("StreamTextLoader 加载失败: %s", e)
                self.error.emit(str(e))
            return

        self.loaded.emit()

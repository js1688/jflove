"""
异步任务工作线程模块

提供基于 QThread 的通用工作线程，避免在主线程中执行耗时操作（网络请求、文件 IO）
导致 UI 卡顿。

使用方式：
    worker = Worker(some_function, arg1, arg2, key=value)
    worker.finished.connect(on_success)
    worker.error.connect(on_error)
    worker.start()
"""

from PySide6.QtCore import QThread, Signal

# 持有所有运行中的 Worker 引用，防止线程运行期间被 GC 回收
_active_workers: set = set()


class Worker(QThread):
    """
    通用后台任务线程。

    :signal finished: 任务成功完成，携带返回值
    :signal error: 任务执行失败，携带异常消息字符串
    """

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, func, *args, **kwargs):
        """
        初始化工作线程。

        :param func: 要在后台执行的可调用对象
        :param args: 传给 func 的位置参数
        :param kwargs: 传给 func 的关键字参数
        """
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs
        _active_workers.add(self)
        self.finished.connect(lambda _: _active_workers.discard(self))
        self.error.connect(lambda _: _active_workers.discard(self))

    def run(self) -> None:
        """线程入口，执行任务并发射信号"""
        try:
            result = self._func(*self._args, **self._kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

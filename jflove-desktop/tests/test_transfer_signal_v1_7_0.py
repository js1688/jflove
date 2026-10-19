"""
v1.7.0 传输进度信号大文件回归（int32 溢出）

背景：`_TransferWorker.progress` 曾声明为 `Signal(str, int, int, str)`。
Qt 的 `int` 是 32 位，>2GB 文件的字节数（如 8GB = 8589934592）在 emit 时
直接抛 `OverflowError`（libshiboken），**进度事件全部丢失** —— 表现为
大文件传输期间界面不动、日志里刷满 OverflowError。

本用例锁死两件事：
  1. 超过 2^31 的字节数能正常 emit 并原样送达；
  2. 信号声明的参数类型不是 32 位（防止有人改回 `int`）。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtCore")

from src.utils.transfer_manager import TaskKind, TransferTask, _TransferWorker  # noqa: E402

BIG_TOTAL = 8 * 1024 ** 3      # 8GB，> 2^31
BIG_DONE = 6 * 1024 ** 3       # 6GB，> 2^31


def _make_task(tmp_path) -> TransferTask:
    """构造一个最小可用任务（不启动线程）"""
    return TransferTask(
        id="t-big",
        kind=TaskKind.UPLOAD,
        filename="big.bin",
        disk_id=1,
        rel_path="big.bin",
        local_path=str(tmp_path / "big.bin"),
    )


def test_progress_signal_accepts_over_2gb(tmp_path):
    """>2GB 的字节数必须能 emit 且数值不失真"""
    worker = _TransferWorker(_make_task(tmp_path))
    received: list[tuple[str, int, int, str]] = []
    worker.progress.connect(
        lambda tid, done, total, phase: received.append((tid, done, total, phase)),
    )

    worker.progress.emit("t-big", BIG_DONE, BIG_TOTAL, "uploading")

    assert received == [("t-big", BIG_DONE, BIG_TOTAL, "uploading")]


def test_progress_signal_declared_as_int64():
    """信号声明必须是 64 位，改回 `int` 即回归"""
    from src.utils import transfer_manager as tm

    source = open(tm.__file__, encoding="utf-8").read()
    assert 'progress = Signal(str, "qint64", "qint64", str)' in source

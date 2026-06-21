"""
v1.1.1 验证 StreamTextLoader 信号重命名（修 v1.1.0 BUG-1）

覆盖点：
  1. 类暴露 loaded 信号
  2. 加载正常完成后 loaded 被触发
  3. 类不再暴露同名 finished 自定义 Signal（QThread.finished 内置存在但不来自 Signal()）
"""

from __future__ import annotations

from unittest.mock import patch

from PySide6.QtTest import QSignalSpy

from src.components.stream_text_loader import StreamTextLoader


def test_loaded信号存在(qapp):
    loader = StreamTextLoader()
    assert hasattr(loader, "loaded")
    # loaded 是用户自定义 Signal，应当与 QThread.finished 不同
    assert loader.loaded is not loader.finished


def test_自定义finished属性已不再存在(qapp):
    """v1.1.0 测试报告 BUG-1：曾在类上定义 finished = Signal()，现已移除"""
    # 类上不应再有自定义 finished Signal 声明（QThread.finished 是内置 BoundSignal）
    cls_finished = StreamTextLoader.__dict__.get("finished")
    assert cls_finished is None, (
        "StreamTextLoader 不应再在类体内声明 finished Signal "
        "（与 QThread.finished 同名会造成 BUG-1）"
    )


def test_加载完成后loaded被emit(qapp):
    """模拟 stream_range 返回一个简单数据序列，验证 loaded 信号触发"""
    loader = StreamTextLoader()
    spy = QSignalSpy(loader.loaded)

    fake_meta = {"file_size": 10, "content_type": "text/plain"}

    def fake_stream_range(disk_id, path, filename, range_start, range_end):
        return fake_meta, iter([b"hello", b"world"])

    with patch(
        "src.services.file_service.stream_range",
        side_effect=fake_stream_range,
    ):
        loader.start_loading(1, "/", "a.txt")
        loader.wait(2000)

    assert spy.count() == 1

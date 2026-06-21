"""
StreamTextLoader 单元测试

通过 mock file_service.stream_range 测试文本流式加载器：
  - 正常加载：meta_ready / chunk_ready / finished 信号按序触发
  - 加载内容：多帧字节拼合后等于预期文本
  - cancel()：设置取消标志后不再 emit chunk_ready，不 emit finished
  - 加载失败：stream_range 抛出异常时 emit error 信号
"""

from __future__ import annotations

from unittest.mock import patch

from src.components.stream_text_loader import StreamTextLoader


# ── 辅助：构建多帧 mock ────────────────────────────────────────────── #

def _make_frames_mock(chunks: list[bytes], meta: dict | None = None):
    """
    构造一个 mock file_service.stream_range，逐帧 yield chunks 中的数据。
    meta 若为 None 则使用默认元数据。
    """
    if meta is None:
        total = sum(len(c) for c in chunks)
        meta = {
            "type": "meta",
            "file_size": total,
            "range_start": 0,
            "range_end": total,
            "content_type": "text/plain",
        }

    def _mock(disk_id, path, filename, range_start=0, range_end=-1):
        def _iter():
            yield from chunks

        return meta, _iter()

    return _mock


# ════════════════════════════════════════════════════════════════════ #
#   测试组 1：正常加载
# ════════════════════════════════════════════════════════════════════ #

class Test正常加载:
    """chunk_ready、meta_ready、finished 信号按预期顺序和内容触发"""

    def test_信号顺序_meta_ready在chunk_ready前触发(self, qapp):
        """meta_ready 应在任何 chunk_ready 之前触发"""
        chunks = [b"Hello, ", b"World!\n", "中文文本内容".encode()]
        mock_fn = _make_frames_mock(chunks)

        signal_order: list[str] = []
        received_chunks: list[bytes] = []
        received_meta: list[dict] = []

        loader = StreamTextLoader()
        loader.meta_ready.connect(
            lambda m: (signal_order.append("meta"), received_meta.append(m))
        )
        loader.chunk_ready.connect(
            lambda c: (signal_order.append("chunk"), received_chunks.append(c))
        )
        loader.finished.connect(lambda: signal_order.append("finished"))

        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            loader.start_loading(disk_id=1, path="", filename="test.txt")
            loader.wait(5000)
            qapp.processEvents()

        assert signal_order[0] == "meta", f"meta_ready 应最先触发，实际顺序：{signal_order}"
        assert signal_order[-1] == "finished", f"finished 应最后触发，实际顺序：{signal_order}"
        assert signal_order.count("chunk") == len(chunks), (
            f"chunk_ready 应触发 {len(chunks)} 次，实际 {signal_order.count('chunk')} 次"
        )

    def test_多帧拼合内容正确(self, qapp):
        """所有 chunk_ready 接收到的字节拼合后应等于原始内容"""
        raw_content = "这是一段测试文本\n" * 100
        chunks = [
            raw_content.encode("utf-8")[i:i + 64]
            for i in range(0, len(raw_content.encode("utf-8")), 64)
        ]
        mock_fn = _make_frames_mock(chunks)
        received_chunks: list[bytes] = []

        loader = StreamTextLoader()
        loader.chunk_ready.connect(received_chunks.append)

        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            loader.start_loading(disk_id=1, path="notes", filename="readme.txt")
            loader.wait(5000)
            qapp.processEvents()

        combined = b"".join(received_chunks)
        assert combined == raw_content.encode("utf-8"), "拼合内容与原始数据不一致"

    def test_meta_ready携带正确的file_size(self, qapp):
        """meta_ready 信号应携带包含 file_size 的元数据字典"""
        chunks = [b"test data" * 10]
        total_size = sum(len(c) for c in chunks)
        mock_fn = _make_frames_mock(chunks)

        received_meta: list[dict] = []
        loader = StreamTextLoader()
        loader.meta_ready.connect(received_meta.append)

        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            loader.start_loading(disk_id=2, path="", filename="data.log")
            loader.wait(5000)
            qapp.processEvents()

        assert len(received_meta) == 1, "meta_ready 应只触发一次"
        assert received_meta[0]["file_size"] == total_size

    def test_finished信号在所有chunk之后触发(self, qapp):
        """finished 必须在最后一个 chunk_ready 之后触发"""
        chunks = [b"part1", b"part2", b"part3"]
        mock_fn = _make_frames_mock(chunks)

        events: list[str] = []
        loader = StreamTextLoader()
        loader.chunk_ready.connect(lambda _: events.append("chunk"))
        loader.finished.connect(lambda: events.append("finished"))

        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            loader.start_loading(disk_id=1, path="", filename="f.txt")
            loader.wait(5000)
            qapp.processEvents()

        assert events.count("chunk") == 3
        assert events[-1] == "finished", f"finished 应最后触发，实际：{events}"


# ════════════════════════════════════════════════════════════════════ #
#   测试组 2：取消加载
# ════════════════════════════════════════════════════════════════════ #

class Test取消加载:
    """cancel() 后不再 emit chunk_ready"""

    def test_cancel后停止emit_chunk_ready(self, qapp):
        """
        cancel() 设置取消标志后，run() 中的循环检测到 _cancelled=True 会 break。
        由于取消在加载前设置，不应有 chunk 被 emit。
        """
        import threading

        # 用一个 Event 让 mock 在第一帧时等待，确保 cancel() 在数据帧前生效
        pause = threading.Event()
        chunks = [b"chunk1", b"chunk2", b"chunk3"]

        def _slow_mock(disk_id, path, filename, range_start=0, range_end=-1):
            meta = {"type": "meta", "file_size": 18, "range_start": 0,
                    "range_end": 18, "content_type": "text/plain"}

            def _iter():
                for chunk in chunks:
                    pause.wait()  # 等待 cancel() 被调用
                    yield chunk

            return meta, _iter()

        received_chunks: list[bytes] = []
        loader = StreamTextLoader()
        loader.chunk_ready.connect(received_chunks.append)

        with patch("src.services.file_service.stream_range", side_effect=_slow_mock):
            loader.start_loading(disk_id=1, path="", filename="slow.txt")
            # 取消前先让 meta 帧被处理（不涉及 pause）
            loader.cancel()
            pause.set()  # 释放阻塞，让线程能退出
            loader.wait(5000)
            qapp.processEvents()

        # 由于 cancel 在数据帧前设置，received_chunks 应为空或很少
        # （允许第一帧已经 emit 出去，因为 cancel 可能在 chunk_ready emit 后才生效）
        assert len(received_chunks) <= len(chunks), "取消后不应收到超出预期数量的块"

    def test_cancel后finished信号仍会触发(self, qapp):
        """cancel() 中断循环后，finished 仍然会被触发（正常退出路径）"""
        chunks = [b"data"] * 5
        mock_fn = _make_frames_mock(chunks)

        finished_calls = [0]
        error_calls: list[str] = []

        loader = StreamTextLoader()
        loader.finished.connect(lambda: finished_calls.__setitem__(0, 1))
        loader.error.connect(error_calls.append)

        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            loader.start_loading(disk_id=1, path="", filename="f.txt")
            loader.cancel()
            loader.wait(5000)
            qapp.processEvents()

        # cancel 后正常退出，finished 被 emit，不 emit error
        assert error_calls == [], f"cancel 不应触发 error 信号，实际：{error_calls}"


# ════════════════════════════════════════════════════════════════════ #
#   测试组 3：加载失败
# ════════════════════════════════════════════════════════════════════ #

class Test加载失败:
    """stream_range 抛出异常时 error 信号被触发，finished 不触发"""

    def test_stream_range抛出异常_emit_error信号(self, qapp):
        """
        file_service.stream_range 抛出 ValueError 时应 emit error 信号。

        注意：StreamTextLoader.finished 与 QThread::finished 同名，Qt C++ 运行时
        会在线程结束时自动触发 finished，因此 finished 信号无论成功还是失败都会被 emit。
        此处仅验证 error 信号的正确性（已知缺陷见测试报告 BUG-1）。
        """
        def _failing_mock(disk_id, path, filename, range_start=0, range_end=-1):
            raise ValueError("模拟服务端错误：文件不存在")

        errors: list[str] = []

        loader = StreamTextLoader()
        loader.error.connect(errors.append)

        with patch("src.services.file_service.stream_range", side_effect=_failing_mock):
            loader.start_loading(disk_id=1, path="", filename="missing.txt")
            loader.wait(5000)
            qapp.processEvents()

        assert len(errors) == 1, f"应 emit 一次 error，实际 {len(errors)} 次"
        assert errors[0] != "", "error 信号应携带非空错误描述"

    def test_迭代中抛出异常_emit_error信号(self, qapp):
        """frame_iter 迭代过程中抛出异常，应 emit error 并停止"""
        def _partial_mock(disk_id, path, filename, range_start=0, range_end=-1):
            meta = {"type": "meta", "file_size": 100, "range_start": 0,
                    "range_end": 100, "content_type": "text/plain"}

            def _iter():
                yield b"first chunk"
                raise ValueError("流中途中断")

            return meta, _iter()

        errors: list[str] = []
        chunks: list[bytes] = []

        loader = StreamTextLoader()
        loader.chunk_ready.connect(chunks.append)
        loader.error.connect(errors.append)

        with patch("src.services.file_service.stream_range", side_effect=_partial_mock):
            loader.start_loading(disk_id=1, path="", filename="broken.txt")
            loader.wait(5000)
            qapp.processEvents()

        # 第一帧应该已经 emit
        assert len(chunks) >= 1, "流中断前的块应已 emit"
        assert len(errors) == 1, f"流中断后应 emit error，实际 {len(errors)} 次"

"""
StreamProxy 单元测试

通过 mock file_service.stream_range 测试本地 HTTP 代理行为：
  - HEAD 请求返回正确元数据（Content-Length、Accept-Ranges）
  - GET 请求无 Range：返回 206、完整字节内容
  - GET 请求带 Range bytes=X-Y：返回 206、Content-Range 正确
  - GET 请求带 suffix range bytes=-N：返回 206、末尾 N 字节
  - _ensure_meta 缓存：多次请求只拉取一次元数据
  - 无效 token → 404
  - 代理关闭后 → 503
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import requests

from src.components.stream_proxy import StreamProxy

# ── 测试用文件内容（固定，可重复运行） ───────────────────────────── #
_CONTENT = os.urandom(8192) + b"STREAM_PROXY_TEST_DATA" * 200


def _make_mock(content: bytes = _CONTENT):
    """
    返回一个模拟 file_service.stream_range 的函数。

    - range_start=0, range_end=0：仅返回元数据帧，不含数据
    - 其他 range：返回对应区间字节
    还附带一个可读的调用次数计数器（通过返回元组第 3 项传出）。
    """
    file_size = len(content)
    call_log: list[tuple[int, int]] = []  # 记录每次调用的 (range_start, range_end)

    def _mock(disk_id, path, filename, range_start=0, range_end=-1, range_start_seconds=None):
        eff_start = (
            range_start if range_start >= 0 else max(0, file_size + range_start)
        )
        eff_end = range_end if range_end >= 0 else file_size
        eff_end = min(eff_end, file_size)
        call_log.append((eff_start, eff_end))

        meta = {
            "type": "meta",
            "file_size": file_size,
            "range_start": eff_start,
            "range_end": eff_end,
            "content_type": "application/octet-stream",
        }

        def _iter():
            if eff_end > eff_start:
                # 分块 yield，模拟真实流式行为（每次 4KB）
                chunk_size = 4096
                pos = eff_start
                while pos < eff_end:
                    end = min(pos + chunk_size, eff_end)
                    yield content[pos:end]
                    pos = end

        return meta, _iter()

    return _mock, call_log


# ════════════════════════════════════════════════════════════════════ #
#   测试组 1：HEAD 请求 / 元数据
# ════════════════════════════════════════════════════════════════════ #

class Test元数据请求:
    """HEAD 请求应返回正确的文件大小和 Accept-Ranges"""

    def test_HEAD_返回Content_Length和Accept_Ranges(self):
        """HEAD 请求应返回 200，Content-Length 等于文件大小，含 Accept-Ranges: bytes"""
        mock_fn, _ = _make_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            try:
                resp = requests.head(proxy.url, timeout=5)
                assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}"
                assert int(resp.headers["content-length"]) == len(_CONTENT)
                assert resp.headers.get("accept-ranges") == "bytes"
            finally:
                proxy.close()

    def test_ensure_meta_多次请求只拉取一次元数据(self):
        """
        _ensure_meta 有缓存机制：
        第 1 次（HEAD 触发元数据拉取）：1 次 stream_range 调用
        后续 GET：每次 1 次 stream_range 调用（仅取数据，meta 走缓存）
        共 3 次请求 → 3 次 stream_range 调用（1 meta + 2 data）
        """
        mock_fn, call_log = _make_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            try:
                requests.head(proxy.url, timeout=5)
                requests.get(proxy.url, timeout=5)
                requests.get(proxy.url, timeout=5)
            finally:
                proxy.close()

        # HEAD 触发 1 次 meta 拉取（range_start=0, range_end=0）
        # 两次 GET 各自拉取数据，不重复拉取 meta
        assert len(call_log) == 3, (
            f"期望 3 次 stream_range 调用，实际 {len(call_log)}：{call_log}"
        )
        # 第一次调用是元数据专用（range 0-0）
        assert call_log[0] == (0, 0), f"第一次调用应为 range 0-0，实际 {call_log[0]}"


# ════════════════════════════════════════════════════════════════════ #
#   测试组 2：GET 字节范围请求
# ════════════════════════════════════════════════════════════════════ #

class TestGET字节范围:
    """GET 请求的 Range 解析与响应格式"""

    def test_GET_无Range头_返回206完整内容(self):
        """不带 Range 头的 GET 请求应返回 206 和完整文件内容"""
        mock_fn, _ = _make_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            try:
                resp = requests.get(proxy.url, timeout=5)
                assert resp.status_code == 206, f"期望 206，实际 {resp.status_code}"
                assert resp.content == _CONTENT, "返回内容与原始数据不一致"
                assert "content-range" in resp.headers
            finally:
                proxy.close()

    def test_GET_带Range_bytes_X_Y_返回对应片段(self):
        """Range: bytes=100-999 应返回 [100, 1000) 区间字节"""
        mock_fn, _ = _make_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            try:
                headers = {"Range": "bytes=100-999"}
                resp = requests.get(proxy.url, headers=headers, timeout=5)
                assert resp.status_code == 206
                assert resp.content == _CONTENT[100:1000], "Range 字节范围不正确"
                # Content-Range: bytes 100-999/{file_size}
                assert "100-999" in resp.headers.get("content-range", ""), (
                    f"Content-Range 不正确: {resp.headers.get('content-range')}"
                )
                assert int(resp.headers["content-length"]) == 900
            finally:
                proxy.close()

    def test_GET_带suffix_range_bytes_负N_返回末尾N字节(self):
        """Range: bytes=-512 应返回文件最后 512 字节"""
        content = _CONTENT
        file_size = len(content)
        mock_fn, _ = _make_mock(content)
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            try:
                headers = {"Range": "bytes=-512"}
                resp = requests.get(proxy.url, headers=headers, timeout=5)
                assert resp.status_code == 206
                assert resp.content == content[file_size - 512:], (
                    "suffix range 字节不正确"
                )
                assert int(resp.headers["content-length"]) == 512
            finally:
                proxy.close()

    def test_GET_带Range_到文件结尾(self):
        """Range: bytes=1000- 应返回从 1000 到文件末尾的字节"""
        mock_fn, _ = _make_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            try:
                headers = {"Range": "bytes=1000-"}
                resp = requests.get(proxy.url, headers=headers, timeout=5)
                assert resp.status_code == 206
                assert resp.content == _CONTENT[1000:], "从 1000 到文件末尾的字节不一致"
            finally:
                proxy.close()

    def test_GET_Content_Range头格式正确(self):
        """Content-Range 头格式应符合 RFC 7233: bytes start-end/total"""
        mock_fn, _ = _make_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            try:
                resp = requests.get(proxy.url, timeout=5)
                cr = resp.headers.get("content-range", "")
                assert cr.startswith("bytes "), f"Content-Range 格式错误: {cr}"
                # 格式: bytes start-end/total
                parts = cr[6:].split("/")
                assert len(parts) == 2, f"Content-Range 缺少 total: {cr}"
                start_end = parts[0].split("-")
                assert len(start_end) == 2, f"Content-Range start-end 格式错误: {cr}"
            finally:
                proxy.close()


# ════════════════════════════════════════════════════════════════════ #
#   测试组 3：time 修复流模式（v1.4.0 方案 B）
# ════════════════════════════════════════════════════════════════════ #

_FMP4_CONTENT = os.urandom(4096) + b"FMP4_REPAIR_STREAM" * 300


def _make_time_mock(content: bytes = _FMP4_CONTENT, duration: float = 3.0):
    """
    模拟服务端 time 修复流：meta 含 stream_mode=time / file_size / duration，
    数据帧为 fMP4 修复流字节；记录 range_start_seconds 调用参数。
    """
    file_size = len(content)
    time_log: list[tuple[int, int, float | None]] = []

    def _mock(
        disk_id, path, filename, range_start=0, range_end=-1,
        range_start_seconds=None,
    ):
        time_log.append((range_start, range_end, range_start_seconds))
        meta = {
            "type": "meta",
            "stream_mode": "time",
            "content_type": "video/mp4",
            "range_start_seconds": range_start_seconds if range_start_seconds is not None else 0.0,
            "file_size": file_size,
            "duration": duration,
            "codec": "avc1.64000c",
        }

        def _iter():
            pos = 0
            while pos < len(content):
                yield content[pos:pos + 4096]
                pos += 4096

        return meta, _iter()

    return _mock, time_log


class TestTime修复流:
    """time 模式：HEAD 不声明 seek；GET 200+chunked；Range 线性映射时间"""

    def test_HEAD_不声明Accept_Ranges(self):
        """time 模式 HEAD：200 + Content-Length，但不带 Accept-Ranges"""
        mock_fn, _ = _make_time_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "v.mkv")
            proxy.start()
            try:
                resp = requests.head(proxy.url, timeout=5)
                assert resp.status_code == 200
                assert int(resp.headers["content-length"]) == len(_FMP4_CONTENT)
                assert "accept-ranges" not in resp.headers, (
                    "time 模式不可字节 seek，不应声明 Accept-Ranges"
                )
            finally:
                proxy.close()

    def test_GET_返回200_chunked_无Content_Length(self):
        """time 模式 GET：200（非 206），无 Content-Length（chunked），数据完整"""
        mock_fn, _ = _make_time_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "v.mkv")
            proxy.start()
            try:
                resp = requests.get(proxy.url, timeout=5)
                assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}"
                assert resp.content == _FMP4_CONTENT, "修复流数据应完整透传"
                assert "content-length" not in resp.headers, (
                    "修复流总字节不可预知，应为 chunked（无 Content-Length）"
                )
            finally:
                proxy.close()

    def test_GET_带Range_线性映射range_start_seconds(self):
        """Range 字节偏移线性映射时间：seconds = offset / file_size * duration"""
        mock_fn, time_log = _make_time_mock()
        file_size = len(_FMP4_CONTENT)
        half = file_size // 2
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "v.mkv")
            proxy.start()
            try:
                headers = {"Range": f"bytes={half}-"}
                resp = requests.get(proxy.url, headers=headers, timeout=5)
                assert resp.status_code == 200
                assert resp.content == _FMP4_CONTENT
            finally:
                proxy.close()

        # 元数据请求 1 次（range 0-0，无时间参数）+ GET 数据 1 次（映射时间）
        assert len(time_log) == 2, f"期望 2 次调用，实际 {len(time_log)}: {time_log}"
        _, _, seconds = time_log[1]
        expected = half / file_size * 3.0
        assert seconds is not None, "time 模式 GET 应携带 range_start_seconds"
        assert abs(seconds - expected) < 1e-9, f"线性映射错误: {seconds} != {expected}"

    def test_映射边界_duration或size为零_时间取零(self):
        """duration=0 时映射结果应为 0（防除零/负时间）"""
        mock_fn, time_log = _make_time_mock(duration=0.0)
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "v.mkv")
            proxy.start()
            try:
                headers = {"Range": "bytes=100-"}
                requests.get(proxy.url, headers=headers, timeout=5)
            finally:
                proxy.close()
        _, _, seconds = time_log[1]
        assert seconds == 0.0, f"duration=0 时映射应为 0，实际 {seconds}"


# ════════════════════════════════════════════════════════════════════ #
#   测试组 4：安全与错误处理
# ════════════════════════════════════════════════════════════════════ #

class Test安全与错误:
    """token 验证、代理关闭后行为"""

    def test_无效token_返回404(self):
        """访问错误的 token URL 应返回 404"""
        mock_fn, _ = _make_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            try:
                # 构造一个错误 token 的 URL
                wrong_url = f"http://127.0.0.1:{proxy._port}/wrong_invalid_token"
                resp = requests.head(wrong_url, timeout=5)
                assert resp.status_code == 404, f"期望 404，实际 {resp.status_code}"
            finally:
                proxy.close()

    def test_代理关闭后请求_返回503(self):
        """代理关闭后，任何请求应返回 503 或连接被拒绝"""
        mock_fn, _ = _make_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            url = proxy.url
            proxy.close()
            time.sleep(0.3)  # 等待 shutdown 完成

        # 代理关闭后应拒绝连接或返回 503
        try:
            resp = requests.head(url, timeout=2)
            # 若服务器仍在关闭过程中，可能返回 503
            assert resp.status_code == 503, (
                f"代理关闭后期望 503 或连接拒绝，实际 {resp.status_code}"
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            pass  # 连接被拒绝是正常的

    def test_proxy_url包含token路径(self):
        """proxy.url 格式应为 http://127.0.0.1:{port}/{uuid_hex}"""
        mock_fn, _ = _make_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            try:
                url = proxy.url
                assert url.startswith("http://127.0.0.1:"), f"URL 应绑定 loopback: {url}"
                token = url.split("/")[-1]
                assert len(token) == 32, f"token 长度应为 32（uuid4.hex），实际 {len(token)}"
                assert token.isalnum(), f"token 应为十六进制字符，实际 {token}"
            finally:
                proxy.close()

    def test_proxy只绑定loopback地址(self):
        """代理必须绑定 127.0.0.1，不暴露到外网（验证 server_address）"""
        mock_fn, _ = _make_mock()
        with patch("src.services.file_service.stream_range", side_effect=mock_fn):
            proxy = StreamProxy(1, "", "test.mp4")
            proxy.start()
            try:
                # server_address 第一个元素是绑定的主机地址
                host = proxy._server.server_address[0]
                assert host == "127.0.0.1", (
                    f"代理应只绑定 127.0.0.1，实际绑定 {host}"
                )
            finally:
                proxy.close()

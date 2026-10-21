"""
唯一出站客户端单测（v1.8.0 / S2）

覆盖 `src/utils/outbound_http.py` 的硬规则（设计 §1.4 / §9.1）：
  - 每个请求（含每一跳重定向）先过 `ssrf_guard`，内网目标**零实际连接**；
  - UA / Referer / `identity` 编码、超时映射、大小上限；
  - 断点续传（Range / 206 / 200 回退 / 416 已完成）；
  - 取消、进度、错误状态分类；
  - 日志不出现完整 URL / query；
  - 全仓只有本模块 import httpx（出站收敛不变量）。

单测用 `httpx.MockTransport` + 注入 resolver，**不访问外网、不依赖 DNS**。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import httpx
import pytest

from src.config.settings import OFFLINE_MAX_REDIRECTS, OFFLINE_USER_AGENT
from src.utils.outbound_http import (
    OutboundClient,
    OutboundError,
    OutboundStatusError,
    OutboundStream,
    safe_url_label,
)

#: 假公网 IP（resolver 注入用，不会真的连接）
PUBLIC_IP = "93.184.216.34"


async def public_resolver(host: str) -> list[str]:
    """把所有域名都解析到一个公网 IP（真正的"出网"由 MockTransport 拦截）。"""
    return [PUBLIC_IP]


def make_client(handler) -> OutboundClient:
    """构造注入了 MockTransport + 假解析器的客户端。"""
    return OutboundClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    )


def test_sends_default_ua_referer_and_identity_encoding() -> None:
    """统一 UA / Referer / `Accept-Encoding: identity`（保证 Range 与长度语义）。"""
    seen: dict[str, httpx.Headers] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        return httpx.Response(200, content=b"ok")

    async def run() -> str:
        async with make_client(handler) as client:
            response = await client.get_bytes(
                "https://pages.example.com/a",
                max_bytes=1024,
                referer="https://pages.example.com/list",
            )
            return response.content.decode()

    assert asyncio.run(run()) == "ok"
    assert seen["headers"]["user-agent"] == OFFLINE_USER_AGENT
    assert seen["headers"]["referer"] == "https://pages.example.com/list"
    assert seen["headers"]["accept-encoding"] == "identity"


def test_blocked_ip_literal_is_rejected_without_any_request() -> None:
    """`127.0.0.1` 必须在建连前被拒（transport 一次都不能被调用）。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"secret")

    async def run() -> None:
        async with make_client(handler) as client:
            await client.get_bytes("http://127.0.0.1:8989/api", max_bytes=1024)

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "ssrf_blocked"
    assert calls == []


def test_domain_resolving_to_private_ip_is_rejected_without_request() -> None:
    """域名解析到内网同样拒绝，且不发出任何请求。"""
    calls: list[str] = []

    async def private_resolver(host: str) -> list[str]:
        return ["10.0.0.5"]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"x")

    async def run() -> None:
        client = OutboundClient(
            transport=httpx.MockTransport(handler), resolver=private_resolver
        )
        async with client:
            await client.get_bytes("http://evil.example.com/a.mp4", max_bytes=1024)

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "ssrf_blocked"
    assert calls == []


def test_redirect_to_internal_is_blocked_after_one_hop() -> None:
    """302 → 内网：只允许发生第一跳，第二跳复检即拒（AC-234）。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    async def run() -> None:
        async with make_client(handler) as client:
            await client.get_bytes("https://pub.example.com/page", max_bytes=1024)

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "ssrf_blocked"
    assert len(calls) == 1


def test_too_many_redirects_is_rejected() -> None:
    """重定向超过 `OFFLINE_MAX_REDIRECTS` 跳必须停止。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "/next"})

    async def run() -> None:
        async with make_client(handler) as client:
            await client.get_bytes("https://loop.example.com/start", max_bytes=1024)

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "too_many_redirects"
    assert len(calls) == OFFLINE_MAX_REDIRECTS + 1


def test_relative_redirect_is_resolved() -> None:
    """相对 Location 必须按当前 URL 解析成绝对地址。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "/next"})
        return httpx.Response(200, content=b"page")

    async def run() -> None:
        async with make_client(handler) as client:
            await client.get_bytes("https://example.com/start", max_bytes=1024)

    asyncio.run(run())
    assert seen == ["https://example.com/start", "https://example.com/next"]


def test_content_length_over_limit_is_rejected_before_reading() -> None:
    """Content-Length 已知超限时直接拒绝（不读大响应）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4096)

    async def run() -> None:
        async with make_client(handler) as client:
            await client.get_bytes("https://big.example.com/a", max_bytes=100)

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "too_large"


def test_streaming_body_over_limit_is_aborted_without_known_length() -> None:
    """没有 Content-Length 时按流式累计拦截，不把超限内容读完。"""
    def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, content=b"y" * 500)
        del response.headers["content-length"]
        return response

    async def run() -> None:
        async with make_client(handler) as client:
            await client.get_bytes("https://big.example.com/b", max_bytes=100)

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "too_large"


def test_http_error_status_carries_status_headers_and_body() -> None:
    """4xx 要给出可分类的状态码与少量响应体（供防盗链/过期判断）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"denied")

    async def run() -> None:
        async with make_client(handler) as client:
            await client.get_bytes("https://hotlink.example.com/a.jpg", max_bytes=1024)

    with pytest.raises(OutboundStatusError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.status_code == 403
    assert excinfo.value.body == b"denied"


def test_read_timeout_is_mapped_to_stable_reason() -> None:
    """超时映射为 `net_timeout`（上层据此给中文「访问源站超时」）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async def run() -> None:
        async with make_client(handler) as client:
            await client.get_bytes("https://slow.example.com/a", max_bytes=1024)

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "net_timeout"


def test_connect_error_is_mapped_to_network_error() -> None:
    """连接失败映射为 `network_error`。"""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async def run() -> None:
        async with make_client(handler) as client:
            await client.get_bytes("https://down.example.com/a", max_bytes=1024)

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "network_error"


def test_invalid_scheme_is_rejected() -> None:
    """`file://` 一律拒绝（协议白名单）。"""
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应发出请求")

    async def run() -> None:
        async with make_client(handler) as client:
            await client.get_bytes("file:///etc/passwd", max_bytes=1024)

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "ssrf_blocked"
    assert excinfo.value.detail == "scheme_not_allowed"


def test_stream_body_read_error_is_mapped_to_network_error() -> None:
    """读 body 时连接被重置 ⇒ 稳定 network_error（不是裸 httpx.ReadError）。"""

    class ResetResponse:
        async def aiter_bytes(self):
            yield b"abc"
            raise httpx.ReadError("模拟连接重置")

        async def aclose(self) -> None:
            pass

    stream = OutboundStream(
        url="https://cdn.example.com/a.ts",
        status_code=200,
        headers={},
        response=ResetResponse(),  # type: ignore[arg-type]
        max_bytes=1024,
    )

    async def consume():
        return [chunk async for chunk in stream.iter_bytes()]

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(consume())
    assert excinfo.value.reason == "network_error"


def test_stream_to_file_writes_and_reports_progress(tmp_path: Path) -> None:
    """正常下载：字节落盘、进度累计、总长来自 Content-Length。"""
    dest = tmp_path / "media.bin"
    progress: list[tuple[int, int | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"hello world")

    async def run() -> int:
        async with make_client(handler) as client:
            result = await client.stream_to_file(
                "https://cdn.example.com/a.mp4",
                str(dest),
                max_bytes=1024,
                progress=lambda done, total: progress.append((done, total)),
            )
            return result.bytes_written

    assert asyncio.run(run()) == 11
    assert dest.read_bytes() == b"hello world"
    assert progress[-1] == (11, 11)


def test_stream_to_file_resumes_with_range(tmp_path: Path) -> None:
    """续传：已存在 5 字节 ⇒ 发 `Range: bytes=5-`，206 起点匹配则追加。"""
    dest = tmp_path / "media.bin"
    dest.write_bytes(b"hello")
    seen_range: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_range.append(request.headers.get("range", ""))
        return httpx.Response(
            206,
            headers={"content-range": "bytes 5-10/11"},
            content=b" world",
        )

    async def run():
        async with make_client(handler) as client:
            return await client.stream_to_file(
                "https://cdn.example.com/a.mp4",
                str(dest),
                max_bytes=1024,
                resume=True,
            )

    result = asyncio.run(run())
    assert seen_range == ["bytes=5-"]
    assert result.resumed_from == 5
    assert result.bytes_written == 11
    assert dest.read_bytes() == b"hello world"


def test_stream_to_file_restarts_when_server_ignores_range(tmp_path: Path) -> None:
    """源站返回 200（不支持 Range）⇒ 截断重下，不得追加成两倍。"""
    dest = tmp_path / "media.bin"
    dest.write_bytes(b"old-content")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"new")

    async def run():
        async with make_client(handler) as client:
            return await client.stream_to_file(
                "https://cdn.example.com/a.mp4",
                str(dest),
                max_bytes=1024,
                resume=True,
            )

    result = asyncio.run(run())
    assert result.resumed_from == 0
    assert dest.read_bytes() == b"new"


def test_stream_to_file_treats_416_as_already_complete(tmp_path: Path) -> None:
    """416 且总长等于已下字节 ⇒ 视为已完成（断点续传的终点）。"""
    dest = tmp_path / "media.bin"
    dest.write_bytes(b"12345")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(416, headers={"content-range": "bytes */5"})

    async def run():
        async with make_client(handler) as client:
            return await client.stream_to_file(
                "https://cdn.example.com/a.mp4",
                str(dest),
                max_bytes=1024,
                resume=True,
            )

    result = asyncio.run(run())
    assert result.already_complete is True
    assert result.bytes_written == 5
    assert dest.read_bytes() == b"12345"


def test_stream_to_file_rejects_content_range_mismatch(tmp_path: Path) -> None:
    """206 的起点与本地大小不一致时拒绝，避免写坏文件。"""
    dest = tmp_path / "media.bin"
    dest.write_bytes(b"hello")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(206, headers={"content-range": "bytes 3-10/11"}, content=b"x")

    async def run() -> None:
        async with make_client(handler) as client:
            await client.stream_to_file(
                "https://cdn.example.com/a.mp4",
                str(dest),
                max_bytes=1024,
                resume=True,
            )

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "range_mismatch"
    assert dest.read_bytes() == b"hello"


def test_stream_to_file_does_not_write_error_body(tmp_path: Path) -> None:
    """403 时不得把错误页写成产物（避免出现"下载成功但是 HTML"）。"""
    dest = tmp_path / "media.bin"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"<html>denied</html>")

    async def run() -> None:
        async with make_client(handler) as client:
            await client.stream_to_file(
                "https://hotlink.example.com/a.mp4", str(dest), max_bytes=1024
            )

    with pytest.raises(OutboundStatusError):
        asyncio.run(run())
    assert not dest.exists()


def test_cancel_event_stops_before_any_download(tmp_path: Path) -> None:
    """取消事件已置位时立即失败，不建文件。"""
    dest = tmp_path / "media.bin"
    cancel = asyncio.Event()
    cancel.set()

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("取消后不应发请求")

    async def run() -> None:
        async with make_client(handler) as client:
            await client.stream_to_file(
                "https://cdn.example.com/a.mp4",
                str(dest),
                max_bytes=1024,
                cancel_event=cancel,
            )

    with pytest.raises(OutboundError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.reason == "canceled"
    assert not dest.exists()


def test_log_label_does_not_leak_query_or_full_url(caplog: pytest.LogCaptureFixture) -> None:
    """日志只允许出现域名 + 哈希，query 里的签名不得落日志（AC-235）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    url = "https://v.example.com/play?signature=SuperSecret&token=abc"

    async def run() -> None:
        caplog.set_level(logging.INFO)
        async with make_client(handler) as client:
            await client.get_bytes(url, max_bytes=1024)

    asyncio.run(run())
    assert "SuperSecret" not in caplog.text
    assert "signature" not in caplog.text
    assert "v.example.com#" in caplog.text
    assert safe_url_label(url).startswith("v.example.com#")


def test_only_outbound_http_imports_httpx() -> None:
    """
    出站收敛不变量（设计 §1.4）：`src/` 下只允许 `outbound_http.py` 直接 import httpx。

    否则任何模块都能绕过 SSRF 策略与大小上限，等于把唯一出口变成摆设。
    """
    src_root = Path(__file__).resolve().parent.parent / "src"
    pattern = re.compile(r"^\s*(?:import\s+httpx|from\s+httpx)", re.MULTILINE)
    offenders = [
        str(path.relative_to(src_root))
        for path in src_root.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == ["utils/outbound_http.py"]

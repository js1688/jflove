"""
唯一出站 HTTP 客户端（v1.8.0 新增）

设计依据：`文档记录/设计文档/v1.8.0.md` §1.4 / §9.1。
本版服务端首次具备"主动出网"能力，**全仓只允许本模块 import httpx**，
其余模块（分析器 / 下载器 / 策略代理）一律通过这里取数据，保证：

  - 每个请求（含**每一跳重定向**）都先过 `ssrf_guard.validate_url()`；
  - 响应体有硬上限，超限立即中断，不会把服务端拖爆；
  - 统一 UA / Referer / 超时，避免各处散写请求导致策略漂移；
  - 日志只记"域名 + URL 哈希"，不记 query（可能含签名，见设计 §9.2）。

稳定错误码（`OutboundError.reason`，上层据此映射中文，不要把英文异常抛给用户）：
  `ssrf_blocked` / `invalid_url` / `net_timeout` / `network_error`
  / `too_many_redirects` / `too_large` / `range_mismatch` / `canceled` / `http_status`

文件流不在这里落正式文件名：调用方按设计 §2.4.1 先写 `.jflove-download/` 临时目录，
成功后自行 `os.replace()`。本模块只负责"把字节安全地写到你给的路径"。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import aiofiles
import httpx

from src.config.settings import (
    OFFLINE_CONNECT_TIMEOUT,
    OFFLINE_MAX_REDIRECTS,
    OFFLINE_READ_TIMEOUT,
    OFFLINE_USER_AGENT,
)
from src.utils.logger import get_logger
from src.utils.ssrf_guard import SsrfBlocked, join_redirect, validate_url

logger = get_logger(__name__)

#: httpx 在 INFO 级别会打印**完整 URL（含 query 签名）**，直接违反设计 §9.2。
#: 这里把它压到 WARNING；本模块自己的日志只记 `safe_url_label()`。
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

#: 需要手动跟随的重定向状态（httpx 自身关闭了自动跟随，便于逐跳复检）
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: 出错响应最多保留多少字节用于分类（如识别"需要登录"页面），不整段读入
ERROR_BODY_LIMIT = 8 * 1024

#: `Content-Range: bytes 5-9/10` / `bytes */10`
_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(?:(\d+)-(\d+)|\*)/(\d+|\*)$", re.IGNORECASE)

#: 可注入的 DNS 解析器（单测用；生产走 ssrf_guard 默认实现）
Resolver = Callable[[str], Awaitable[list[str]]]

#: 进度回调签名：`(已下载字节, 总字节或 None)`
ProgressCallback = Callable[[int, int | None], None]


class OutboundError(Exception):
    """
    出站请求失败（策略 / 网络 / 上限 / 取消）。

    :param reason: 稳定错误码（见模块文档），供上层映射中文提示
    :param detail: 诊断信息（**不得**含完整 URL / query）
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


class OutboundStatusError(OutboundError):
    """
    源站返回 4xx / 5xx（`stream_to_file` 在写文件前抛出，避免把错误页写成产物）。

    :param status_code: HTTP 状态码
    :param headers: 响应头（键已小写）
    :param body: 诊断用响应体（至多 `ERROR_BODY_LIMIT` 字节）
    """

    def __init__(self, status_code: int, headers: Mapping[str, str], body: bytes = b"") -> None:
        self.status_code = status_code
        self.headers = dict(headers)
        self.body = body
        super().__init__("http_status", f"HTTP {status_code}")


@dataclass(frozen=True)
class OutboundResponse:
    """一次性读取完成的小响应（HTML / 播放列表 / JSON）。"""

    url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes


@dataclass(frozen=True)
class DownloadResult:
    """`stream_to_file` 的落盘结果。"""

    url: str
    status_code: int
    headers: Mapping[str, str]
    bytes_written: int
    total_bytes: int | None
    resumed_from: int
    already_complete: bool = False


@dataclass
class OutboundStream:
    """
    流式响应句柄（调用方必须经 `OutboundClient.open_stream()` 获取，退出时自动关闭）。

    :ivar max_bytes: 本次响应允许的最大字节数（超限抛 `too_large`）
    """

    url: str
    status_code: int
    headers: Mapping[str, str]
    response: httpx.Response = field(repr=False)
    max_bytes: int = 0

    async def aclose(self) -> None:
        """关闭底层响应（幂等）。"""
        await self.response.aclose()

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        """
        逐块产出响应体，并累计校验大小上限。

        :raises OutboundError: 超过 `max_bytes`（`too_large`）
        """
        total = 0
        async for chunk in self.response.aiter_bytes():
            if not chunk:
                continue
            total += len(chunk)
            if total > self.max_bytes:
                raise OutboundError("too_large", f"响应体超过上限 {self.max_bytes} 字节")
            yield chunk

    async def read_error_body(self, limit: int = ERROR_BODY_LIMIT) -> bytes:
        """
        为错误分类读取少量响应体（**忽略**大小上限，但最多 `limit` 字节）。
        """
        buffer = bytearray()
        try:
            async for chunk in self.response.aiter_bytes():
                if not chunk:
                    continue
                remaining = limit - len(buffer)
                if remaining <= 0:
                    break
                buffer.extend(chunk[:remaining])
                if len(buffer) >= limit:
                    break
        finally:
            await self.aclose()
        return bytes(buffer)


def _header_dict(headers: httpx.Headers) -> dict[str, str]:
    """把 httpx 响应头转成**键全小写**的普通字典（跨模块不泄漏 httpx 类型）。"""
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _int_header(headers: Mapping[str, str], name: str) -> int | None:
    """读取整数型响应头；缺失或非法返回 None。"""
    raw = headers.get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _parse_content_range(value: str | None) -> tuple[int | None, int | None]:
    """
    解析 `Content-Range`。

    :returns: `(起始字节, 总字节)`；`*` 或无法解析的字段为 None
    """
    if not value:
        return None, None
    match = _CONTENT_RANGE_RE.match(value.strip())
    if match is None:
        return None, None
    start = int(match.group(1)) if match.group(1) is not None else None
    total = int(match.group(3)) if match.group(3) != "*" else None
    return start, total


def safe_url_label(url: str) -> str:
    """
    生成可写日志的 URL 标签：**只保留域名 + 哈希前 8 位**。

    设计 §9.2 要求日志不得出现完整 URL / query（可能含签名）。
    其它模块（分析器 / 下载器）记日志时也应使用本函数。
    """
    try:
        host = (urlsplit(url).hostname or "?").lower()
    except ValueError:
        host = "?"
    digest = hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{host}#{digest}"


class OutboundClient:
    """
    统一出站客户端（异步上下文管理器）。

    用法::

        async with OutboundClient() as client:
            page = await client.get_bytes(url, max_bytes=OFFLINE_HTML_MAX_BYTES)
            await client.stream_to_file(seg_url, path, max_bytes=OFFLINE_SEGMENT_MAX_BYTES)

    :param transport: 仅单测注入（`httpx.MockTransport`）；生产为 None
    :param resolver: 仅单测注入 DNS 解析器；生产为 None（走 ssrf_guard 默认实现）
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        connect_timeout: float = OFFLINE_CONNECT_TIMEOUT,
        read_timeout: float = OFFLINE_READ_TIMEOUT,
    ) -> None:
        self._resolver = resolver
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            # 不读 HTTP_PROXY / NO_PROXY / netrc：出站路径必须由我们自己的策略决定
            trust_env=False,
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=read_timeout,
                pool=connect_timeout,
            ),
            transport=transport,
        )

    async def aclose(self) -> None:
        """关闭连接池。"""
        await self._client.aclose()

    async def __aenter__(self) -> OutboundClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def _build_headers(
        self,
        referer: str | None,
        extra: Mapping[str, str] | None,
    ) -> dict[str, str]:
        """
        合并请求头：默认 UA / Accept / `identity`（保证 Range 与 Content-Length 语义），
        再叠加 Referer 与调用方自定义头（自定义头优先）。
        """
        merged: dict[str, str] = {
            "user-agent": OFFLINE_USER_AGENT,
            "accept": "*/*",
            "accept-encoding": "identity",
        }
        if referer:
            merged["referer"] = referer
        if extra:
            for key, value in extra.items():
                merged[str(key).lower()] = str(value)
        return merged

    async def _request_stream(
        self,
        url: str,
        *,
        max_bytes: int,
        referer: str | None,
        headers: Mapping[str, str] | None,
    ) -> OutboundStream:
        """
        发送 GET 并手动跟随重定向，**每一跳**都重新做 SSRF 校验。

        :raises OutboundError: 策略拒绝 / 超时 / 网络错误 / 重定向过多 / 超限
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须为正数")
        merged = self._build_headers(referer, headers)
        current = url
        logger.info("出站请求 %s", safe_url_label(url))
        for _hop in range(OFFLINE_MAX_REDIRECTS + 1):
            try:
                target = await validate_url(current, resolver=self._resolver)
            except SsrfBlocked as exc:
                logger.warning("出站被策略拒绝 %s：%s", safe_url_label(current), exc.reason)
                raise OutboundError("ssrf_blocked", exc.reason) from exc
            try:
                request = self._client.build_request("GET", target.url, headers=merged)
                response = await self._client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise OutboundError("net_timeout", type(exc).__name__) from exc
            except httpx.InvalidURL as exc:
                raise OutboundError("invalid_url", "URL 无法构造请求") from exc
            except httpx.HTTPError as exc:
                raise OutboundError("network_error", type(exc).__name__) from exc
            location = response.headers.get("location")
            if response.status_code in REDIRECT_STATUSES and location:
                await response.aclose()
                current = join_redirect(target.url, location)
                continue
            headers_out = _header_dict(response.headers)
            content_length = _int_header(headers_out, "content-length")
            if content_length is not None and content_length > max_bytes:
                await response.aclose()
                raise OutboundError("too_large", f"Content-Length 超过上限 {max_bytes} 字节")
            return OutboundStream(
                url=target.url,
                status_code=response.status_code,
                headers=headers_out,
                response=response,
                max_bytes=max_bytes,
            )
        raise OutboundError("too_many_redirects", f"重定向超过 {OFFLINE_MAX_REDIRECTS} 跳")

    @asynccontextmanager
    async def open_stream(
        self,
        url: str,
        *,
        max_bytes: int,
        referer: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[OutboundStream]:
        """
        打开流式响应（退出上下文时自动关闭）。

        4xx / 5xx **不自动抛错**：调用方需要看状态码决定分类（防盗链 / 过期 / 缺 Referer）。
        """
        stream = await self._request_stream(
            url, max_bytes=max_bytes, referer=referer, headers=headers
        )
        try:
            yield stream
        finally:
            await stream.aclose()

    async def get_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        referer: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> OutboundResponse:
        """
        一次性读取小响应（HTML / 播放列表 / JSON）。

        :raises OutboundStatusError: 状态码 >= 400
        :raises OutboundError: 策略 / 网络 / 超限
        """
        async with self.open_stream(
            url, max_bytes=max_bytes, referer=referer, headers=headers
        ) as stream:
            if stream.status_code >= 400:
                body = await stream.read_error_body()
                raise OutboundStatusError(stream.status_code, stream.headers, body)
            chunks: list[bytes] = []
            async for chunk in stream.iter_bytes():
                chunks.append(chunk)
            return OutboundResponse(
                url=stream.url,
                status_code=stream.status_code,
                headers=dict(stream.headers),
                content=b"".join(chunks),
            )

    async def stream_to_file(
        self,
        url: str,
        dest: str,
        *,
        max_bytes: int,
        referer: str | None = None,
        headers: Mapping[str, str] | None = None,
        resume: bool = False,
        progress: ProgressCallback | None = None,
        cancel_event=None,
    ) -> DownloadResult:
        """
        流式下载到本地路径（失败/取消时**保留**已写字节，供下次断点续传）。

        断点续传口径（AC-216）：
          - `resume=True` 且目标已存在 ⇒ 发 `Range: bytes=N-`；
          - 源站返回 206 且 `Content-Range` 起点匹配 ⇒ 追加写；
          - 源站返回 200（不支持 Range）⇒ 截断重下；
          - 源站返回 416 且总长等于已下字节 ⇒ 视为已完整（`already_complete=True`）。

        :param max_bytes: 单文件总字节上限（含续传前已有字节）
        :param progress: 进度回调 `(已下载总字节, 总字节或 None)`
        :param cancel_event: `asyncio.Event`，置位后抛 `canceled`
        :raises OutboundStatusError: 状态码 >= 400（**不会**写错误页）
        :raises OutboundError: 策略 / 网络 / 超限 / 取消 / 续传不一致
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须为正数")
        if cancel_event is not None and cancel_event.is_set():
            raise OutboundError("canceled", "用户取消")
        existing = os.path.getsize(dest) if resume and os.path.isfile(dest) else 0
        if existing > max_bytes:
            raise OutboundError("too_large", "已下载字节数已超过单文件上限")
        request_headers = dict(headers or {})
        if existing:
            request_headers["range"] = f"bytes={existing}-"
        async with self.open_stream(
            url,
            max_bytes=max_bytes,
            referer=referer,
            headers=request_headers,
        ) as stream:
            if stream.status_code == 416 and existing:
                _, total = _parse_content_range(stream.headers.get("content-range"))
                if total is not None and total == existing:
                    return DownloadResult(
                        url=stream.url,
                        status_code=stream.status_code,
                        headers=dict(stream.headers),
                        bytes_written=existing,
                        total_bytes=total,
                        resumed_from=existing,
                        already_complete=True,
                    )
            if stream.status_code >= 400:
                body = await stream.read_error_body()
                raise OutboundStatusError(stream.status_code, stream.headers, body)

            resumed_from = 0
            write_mode = "wb"
            total_bytes = _int_header(stream.headers, "content-length")
            if existing and stream.status_code == 206:
                start, total = _parse_content_range(stream.headers.get("content-range"))
                if start != existing:
                    raise OutboundError(
                        "range_mismatch", f"续传起点不一致：期望 {existing}，响应 {start}"
                    )
                resumed_from = existing
                write_mode = "ab"
                if total is not None:
                    total_bytes = total
            elif existing and stream.status_code == 200:
                # 源站无视 Range：从头重下，避免把整文件追加成两倍
                total_bytes = _int_header(stream.headers, "content-length")
            done = resumed_from
            if progress is not None:
                progress(done, total_bytes)
            async with aiofiles.open(dest, write_mode) as handle:
                async for chunk in stream.iter_bytes():
                    if cancel_event is not None and cancel_event.is_set():
                        raise OutboundError("canceled", "用户取消")
                    await handle.write(chunk)
                    done += len(chunk)
                    if done > max_bytes:
                        raise OutboundError("too_large", "下载字节数超过单文件上限")
                    if progress is not None:
                        progress(done, total_bytes)
            return DownloadResult(
                url=stream.url,
                status_code=stream.status_code,
                headers=dict(stream.headers),
                bytes_written=done,
                total_bytes=total_bytes,
                resumed_from=resumed_from,
            )

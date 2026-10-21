"""
loopback 策略代理（v1.8.0 新增）

设计依据：`文档记录/设计文档/v1.8.0.md` §1.4 / §9.1 第 8 条。

为什么需要它：`yt-dlp` 与无头浏览器有自己的网络栈，如果直接出网，就绕过了
`ssrf_guard` + `outbound_http` 这一唯一出口。本模块在 `127.0.0.1` 上起一个
**临时 HTTP/CONNECT 代理**（随机端口 + 随机令牌），让第三方分析器的每个请求
都先经过同一套"解析后 IP 校验"策略，再转发到上游。

硬约束：
  - **只绑定 `127.0.0.1`**，不允许外部访问；
  - `Proxy-Authorization: Basic` 随机令牌，常量时间比较，失败 407；
  - 目标被策略拒绝 ⇒ 403 且**不建立任何上游连接**；
  - 上游连接使用**已校验的 IP**（而非再次解析域名），顺带收窄 DNS rebinding 面；
  - 日志只记 host:port，不记完整 URL / query。

单测通过注入 `resolver` / `connect` 避免真的出网；生产两者均为默认实现。
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from src.config.settings import OFFLINE_READ_TIMEOUT
from src.utils.logger import get_logger
from src.utils.ssrf_guard import SsrfBlocked, ValidatedTarget, validate_url

logger = get_logger(__name__)

#: 代理只监听环回地址（设计要求：外部不可达）
LISTEN_HOST = "127.0.0.1"

#: 代理认证用户名（密码是每次启动随机生成的令牌）
PROXY_USER = "jflove"

#: 请求头上限，防止慢速大包头打爆内存
_MAX_HEAD_BYTES = 16 * 1024

#: 读请求头/请求体的超时
_HEAD_TIMEOUT = 10.0

#: 允许转发的请求体上限（分析器基本只发 GET）
_MAX_BODY_BYTES = 8 * 1024 * 1024

#: 双向转发块大小
_RELAY_CHUNK = 64 * 1024

#: 可注入的 DNS 解析器（单测用；生产为 None）
Resolver = Callable[[str], Awaitable[list[str]]]

#: 可注入的上游连接工厂（单测用；生产为 asyncio.open_connection）
ConnectFactory = Callable[
    [str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
]


def _split_authority(value: str) -> tuple[str, int, str] | None:
    """
    解析 CONNECT 目标 `host:port` / `[v6]:port`。

    :returns: `(host, port, 用于拼 URL 的 host 形式)`；非法返回 None
    """
    text = value.strip()
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return None
        host = text[1:end]
        rest = text[end + 1:]
        if not rest.startswith(":"):
            return None
        port_text = rest[1:]
        host_for_url = f"[{host}]"
    else:
        host, separator, port_text = text.rpartition(":")
        if not separator or not host:
            return None
        host_for_url = host
    try:
        port = int(port_text)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return host, port, host_for_url


async def _read_head(reader: asyncio.StreamReader) -> bytes | None:
    """
    读取完整请求头（以 `\\r\\n\\r\\n` 结束）。

    :returns: 原始头字节；超时 / 超长 / 连接提前关闭返回 None
    """
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=_HEAD_TIMEOUT)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
        return None
    if len(head) > _MAX_HEAD_BYTES:
        return None
    return head


def _parse_head(head: bytes) -> tuple[str, str, str, dict[str, str]] | None:
    """
    解析请求行与请求头。

    :returns: `(method, target, version, headers)`；非法返回 None
    """
    try:
        text = head.decode("iso-8859-1")
    except UnicodeDecodeError:
        return None
    lines = text.split("\r\n")
    if not lines or not lines[0]:
        return None
    parts = lines[0].split(" ")
    if len(parts) != 3:
        return None
    method, target, version = parts
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            return None
        headers[name.strip().lower()] = value.strip()
    return method.upper(), target, version, headers


async def _send_status(
    writer: asyncio.StreamWriter,
    status: int,
    reason: str,
    extra_headers: str = "",
) -> None:
    """返回一个无响应体的状态（代理自身错误 / 拒绝）。"""
    payload = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Length: 0\r\n"
        f"{extra_headers}"
        "Connection: close\r\n\r\n"
    ).encode("iso-8859-1")
    writer.write(payload)
    await writer.drain()


class EgressProxy:
    """
    环回策略代理。

    :param resolver: 仅单测注入的 DNS 解析器
    :param connect: 仅单测注入的上游连接工厂
    """

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        connect: ConnectFactory | None = None,
    ) -> None:
        #: 每次启动随机令牌（16 字节十六进制 → ASCII，可直接放进代理 URL 的 userinfo）
        self._token = secrets.token_hex(16)
        self._expected_auth = "Basic " + base64.b64encode(
            f"{PROXY_USER}:{self._token}".encode("ascii")
        ).decode("ascii")
        self._resolver = resolver
        self._connect = connect or asyncio.open_connection
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def token(self) -> str:
        """代理令牌（测试断言用；生产不对外暴露）。"""
        return self._token

    @property
    def address(self) -> tuple[str, int]:
        """实际监听地址 `(host, port)`。"""
        if self._server is None or not self._server.sockets:
            raise RuntimeError("策略代理尚未启动")
        sockname = self._server.sockets[0].getsockname()
        return str(sockname[0]), int(sockname[1])

    @property
    def proxy_url(self) -> str:
        """
        可直接交给 yt-dlp / 浏览器的代理 URL（含随机令牌）。

        形如 `http://jflove:<token>@127.0.0.1:<port>`。
        """
        host, port = self.address
        return f"http://{PROXY_USER}:{self._token}@{host}:{port}"

    async def start(self) -> str:
        """启动代理并返回代理 URL（重复调用幂等）。"""
        if self._server is None:
            self._server = await asyncio.start_server(
                self._handle_client, host=LISTEN_HOST, port=0
            )
            logger.info("出站策略代理已启动 %s:%s", *self.address)
        return self.proxy_url

    async def close(self) -> None:
        """停止监听、取消进行中的隧道并关闭所有连接。"""
        server = self._server
        self._server = None
        if server is not None:
            server.close()
        # 先取消处理协程，再 wait_closed()：Python 3.12+ 的 wait_closed 会等待
        # 活跃连接结束，若先等它，长隧道会让 close() 永远返回不了。
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for writer in list(self._writers):
            try:
                writer.close()
            except OSError:
                pass
        if server is not None:
            await server.wait_closed()
        self._writers.clear()
        self._tasks.clear()

    async def __aenter__(self) -> EgressProxy:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """处理一条客户端连接（一个请求；CONNECT 则升级为隧道）。"""
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self._writers.add(writer)
        try:
            head = await _read_head(reader)
            if head is None:
                await _send_status(writer, 400, "Bad Request")
                return
            parsed = _parse_head(head)
            if parsed is None:
                await _send_status(writer, 400, "Bad Request")
                return
            method, target, version, headers = parsed
            if not self._authorized(headers):
                await _send_status(
                    writer,
                    407,
                    "Proxy Authentication Required",
                    'Proxy-Authenticate: Basic realm="jflove"\r\n',
                )
                return
            if method == "CONNECT":
                await self._handle_connect(target, reader, writer)
            elif target.lower().startswith(("http://", "https://")):
                await self._handle_forward(method, target, version, headers, reader, writer)
            else:
                await _send_status(writer, 400, "Bad Request")
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError):
            # 客户端中途断开是常态（yt-dlp 会主动取消请求），不记为错误
            pass
        except Exception:
            # 不打印异常原文（可能带地址/URL），只留通用错误
            logger.error("策略代理处理请求失败")
            try:
                await _send_status(writer, 500, "Internal Server Error")
            except (ConnectionError, OSError):
                pass
        finally:
            self._writers.discard(writer)
            try:
                writer.close()
            except OSError:
                pass
            if task is not None:
                self._tasks.discard(task)

    def _authorized(self, headers: dict[str, str]) -> bool:
        """校验 `Proxy-Authorization`（常量时间比较）。"""
        provided = headers.get("proxy-authorization", "")
        return hmac.compare_digest(provided, self._expected_auth)

    async def _validate(self, url: str) -> ValidatedTarget | None:
        """
        对目标做完整 SSRF 校验。

        :returns: 通过时返回 `ValidatedTarget`；被拒绝返回 None（调用方回 403）
        """
        try:
            return await validate_url(url, resolver=self._resolver)
        except SsrfBlocked as exc:
            logger.warning("策略代理拒绝出站目标：%s", exc.reason)
            return None

    async def _connect_upstream(
        self,
        target: ValidatedTarget,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        """
        连接上游（优先用**已校验的 IP**，避免二次 DNS 解析被 rebinding 利用）。
        """
        address = target.ips[0] if target.ips else target.host
        try:
            reader, writer = await self._connect(address, port)
        except OSError:
            return None
        self._writers.add(writer)
        return reader, writer

    async def _handle_connect(
        self,
        target: str,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """处理 HTTPS 隧道（CONNECT）。"""
        authority = _split_authority(target)
        if authority is None:
            await _send_status(client_writer, 400, "Bad Request")
            return
        _, port, host_for_url = authority
        validated = await self._validate(f"https://{host_for_url}:{port}/")
        if validated is None:
            await _send_status(client_writer, 403, "Forbidden")
            return
        upstream = await self._connect_upstream(validated, port)
        if upstream is None:
            await _send_status(client_writer, 502, "Bad Gateway")
            return
        upstream_reader, upstream_writer = upstream
        try:
            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()
            await self._relay(client_reader, client_writer, upstream_reader, upstream_writer)
        finally:
            try:
                upstream_writer.close()
            except OSError:
                pass

    async def _handle_forward(
        self,
        method: str,
        target: str,
        version: str,
        headers: dict[str, str],
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """处理普通 HTTP 代理请求（absolute-form → origin-form）。"""
        validated = await self._validate(target)
        if validated is None:
            await _send_status(client_writer, 403, "Forbidden")
            return
        transfer_encoding = headers.get("transfer-encoding", "").lower()
        if transfer_encoding and transfer_encoding != "identity":
            await _send_status(client_writer, 501, "Not Implemented")
            return
        body = b""
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                await _send_status(client_writer, 400, "Bad Request")
                return
            if length < 0 or length > _MAX_BODY_BYTES:
                await _send_status(client_writer, 413, "Payload Too Large")
                return
            if length:
                try:
                    body = await asyncio.wait_for(
                        client_reader.readexactly(length), timeout=_HEAD_TIMEOUT
                    )
                except (asyncio.IncompleteReadError, TimeoutError):
                    await _send_status(client_writer, 400, "Bad Request")
                    return
        upstream = await self._connect_upstream(validated, validated.port)
        if upstream is None:
            await _send_status(client_writer, 502, "Bad Gateway")
            return
        upstream_reader, upstream_writer = upstream
        try:
            origin = urlsplit(validated.url)
            origin_target = origin.path or "/"
            if origin.query:
                origin_target = f"{origin_target}?{origin.query}"
            lines = [f"{method} {origin_target} {version}"]
            for name, value in headers.items():
                if name in ("proxy-authorization", "proxy-connection", "connection"):
                    continue
                lines.append(f"{name.title()}: {value}")
            if "host" not in headers:
                lines.append(f"Host: {validated.host}")
            lines.append("Connection: close")
            request_head = ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")
            upstream_writer.write(request_head + body)
            await upstream_writer.drain()
            # 请求体已一次性转发完，这里只需要把上游响应原样回给客户端；
            # 不建立 client→upstream 的反向管道，避免把后续数据误当第二个请求。
            while True:
                try:
                    data = await asyncio.wait_for(
                        upstream_reader.read(_RELAY_CHUNK),
                        timeout=OFFLINE_READ_TIMEOUT,
                    )
                except TimeoutError:
                    break
                if not data:
                    break
                client_writer.write(data)
                await client_writer.drain()
        finally:
            try:
                upstream_writer.close()
            except OSError:
                pass

    async def _relay(
        self,
        downstream_reader: asyncio.StreamReader,
        downstream_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        """双向转发字节，任一方向结束即收尾（CONNECT 隧道 / HTTP 响应转发共用）。"""

        async def pipe(
            source: asyncio.StreamReader,
            destination: asyncio.StreamWriter,
        ) -> None:
            try:
                while True:
                    data = await source.read(_RELAY_CHUNK)
                    if not data:
                        break
                    destination.write(data)
                    await destination.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                try:
                    destination.write_eof()
                except (OSError, RuntimeError):
                    pass

        upward = asyncio.create_task(pipe(downstream_reader, upstream_writer))
        downward = asyncio.create_task(pipe(upstream_reader, downstream_writer))
        try:
            await asyncio.wait({upward, downward}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # 无论正常结束还是被 close() 取消，都必须收掉两个方向的协程，
            # 否则会在事件循环里留下 pending task（"Task was destroyed" 警告）。
            for task in (upward, downward):
                if not task.done():
                    task.cancel()
            await asyncio.gather(upward, downward, return_exceptions=True)
            for writer in (upstream_writer, downstream_writer):
                try:
                    writer.close()
                except OSError:
                    pass

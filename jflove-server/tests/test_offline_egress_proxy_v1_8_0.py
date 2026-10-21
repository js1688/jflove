"""
出站策略代理单测（v1.8.0 / S2）

覆盖 `src/utils/egress_proxy.py` 的硬约束（设计 §1.4 / §9.1 第 8 条）：
  - 只绑定 `127.0.0.1`，随机端口 + 随机令牌；
  - 无令牌 / 错令牌 ⇒ 407，且**零上游连接**；
  - CONNECT / HTTP 目标为内网 ⇒ 403，且**零上游连接**；
  - 合法 CONNECT 能双向转发；合法 HTTP 请求会改写成 origin-form；
  - 上游连接失败 ⇒ 502；畸形请求 ⇒ 400；关闭后端口不再监听。

单测通过注入 `resolver` / `connect` 避免真的出网：`connect` 把"上游"接到本地
测试服务器，但**策略判定仍走真实的 `ssrf_guard.validate_url`**。
"""

from __future__ import annotations

import asyncio
import base64

from src.utils.egress_proxy import EgressProxy


async def public_resolver(host: str) -> list[str]:
    """把测试域名解析到公网 IP（上游连接由注入的 connect 接管）。"""
    return ["93.184.216.34"]


def auth_header(token: str) -> bytes:
    """构造代理认证头。"""
    credentials = base64.b64encode(f"jflove:{token}".encode("ascii")).decode("ascii")
    return f"Proxy-Authorization: Basic {credentials}\r\n".encode("ascii")


async def read_head(reader: asyncio.StreamReader) -> bytes:
    """读取一个 HTTP 响应头（到空行为止）。"""
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)


async def open_client(proxy: EgressProxy) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """连接到代理（客户端连的是环回代理，不是被代理目标）。"""
    return await asyncio.open_connection("127.0.0.1", proxy.address[1])


def test_binds_loopback_only_and_builds_token_proxy_url() -> None:
    """代理只能监听 127.0.0.1；代理 URL 里带随机令牌。"""

    async def run() -> tuple[tuple[str, int], str, str]:
        proxy = EgressProxy()
        url = await proxy.start()
        try:
            return proxy.address, url, proxy.token
        finally:
            await proxy.close()

    (host, port), url, token = asyncio.run(run())
    assert host == "127.0.0.1"
    assert port > 0
    assert url == f"http://jflove:{token}@127.0.0.1:{port}"
    assert len(token) == 32


def test_missing_authorization_gets_407_without_connecting() -> None:
    """没有 `Proxy-Authorization` ⇒ 407，且不建立任何上游连接。"""
    calls: list[tuple[str, int]] = []

    async def fake_connect(host: str, port: int):
        calls.append((host, port))
        raise AssertionError("鉴权失败时不应连接上游")

    async def run() -> bytes:
        proxy = EgressProxy(connect=fake_connect)
        await proxy.start()
        try:
            reader, writer = await open_client(proxy)
            writer.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
            await writer.drain()
            head = await read_head(reader)
            writer.close()
            return head
        finally:
            await proxy.close()

    head = asyncio.run(run())
    assert b"407" in head
    assert b"Proxy-Authenticate" in head
    assert calls == []


def test_wrong_token_gets_407_without_connecting() -> None:
    """令牌错误同样 407（不能只校验"有没有"）。"""
    calls: list[tuple[str, int]] = []

    async def fake_connect(host: str, port: int):
        calls.append((host, port))
        raise AssertionError("鉴权失败时不应连接上游")

    async def run() -> bytes:
        proxy = EgressProxy(connect=fake_connect)
        await proxy.start()
        try:
            reader, writer = await open_client(proxy)
            writer.write(
                b"CONNECT example.com:443 HTTP/1.1\r\n"
                b"Host: example.com:443\r\n" + auth_header("wrong-token") + b"\r\n"
            )
            await writer.drain()
            head = await read_head(reader)
            writer.close()
            return head
        finally:
            await proxy.close()

    head = asyncio.run(run())
    assert b"407" in head
    assert calls == []


def test_connect_to_internal_ip_is_forbidden_without_connecting() -> None:
    """CONNECT 目标是内网字面量 ⇒ 403 且零连接（SSRF 主入口）。"""
    calls: list[tuple[str, int]] = []

    async def fake_connect(host: str, port: int):
        calls.append((host, port))
        raise AssertionError("内网目标不应被连接")

    async def run() -> bytes:
        proxy = EgressProxy(connect=fake_connect)
        await proxy.start()
        try:
            reader, writer = await open_client(proxy)
            writer.write(
                b"CONNECT 127.0.0.1:8989 HTTP/1.1\r\n"
                b"Host: 127.0.0.1:8989\r\n" + auth_header(proxy.token) + b"\r\n"
            )
            await writer.drain()
            head = await read_head(reader)
            writer.close()
            return head
        finally:
            await proxy.close()

    head = asyncio.run(run())
    assert b"403" in head
    assert calls == []


def test_http_forward_to_private_ip_is_forbidden_without_connecting() -> None:
    """HTTP absolute-form 目标是私网 ⇒ 403 且零连接。"""
    calls: list[tuple[str, int]] = []

    async def fake_connect(host: str, port: int):
        calls.append((host, port))
        raise AssertionError("私网目标不应被连接")

    async def run() -> bytes:
        proxy = EgressProxy(connect=fake_connect)
        await proxy.start()
        try:
            reader, writer = await open_client(proxy)
            writer.write(
                b"GET http://10.0.0.1/admin HTTP/1.1\r\n"
                b"Host: 10.0.0.1\r\n" + auth_header(proxy.token) + b"\r\n"
            )
            await writer.drain()
            head = await read_head(reader)
            writer.close()
            return head
        finally:
            await proxy.close()

    head = asyncio.run(run())
    assert b"403" in head
    assert calls == []


def test_connect_tunnel_relays_bidirectional_bytes() -> None:
    """合法 CONNECT：先回 200，随后双向转发（这里用本地 echo 当上游）。"""
    async def echo_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(1024)
        if data:
            writer.write(data)
            await writer.drain()
        writer.close()

    async def run() -> bytes:
        echo_server = await asyncio.start_server(echo_handler, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]

        async def fake_connect(host: str, port: int):
            return await asyncio.open_connection("127.0.0.1", echo_port)

        proxy = EgressProxy(resolver=public_resolver, connect=fake_connect)
        await proxy.start()
        try:
            reader, writer = await open_client(proxy)
            writer.write(
                b"CONNECT example.com:443 HTTP/1.1\r\n"
                b"Host: example.com:443\r\n" + auth_header(proxy.token) + b"\r\n"
            )
            await writer.drain()
            head = await read_head(reader)
            assert b"200" in head
            writer.write(b"ping")
            await writer.drain()
            return await asyncio.wait_for(reader.read(4), timeout=3)
        finally:
            writer.close()
            await proxy.close()
            echo_server.close()
            await echo_server.wait_closed()

    assert asyncio.run(run()) == b"ping"


def test_http_forward_rewrites_to_origin_form_and_strips_proxy_auth() -> None:
    """HTTP 转发：absolute-form 改 origin-form；上游看不到 Proxy-Authorization。"""
    records: list[str] = []

    async def upstream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        records.append(head.decode("iso-8859-1"))
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello"
        )
        await writer.drain()
        writer.close()

    async def run() -> bytes:
        upstream_server = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]

        async def fake_connect(host: str, port: int):
            return await asyncio.open_connection("127.0.0.1", upstream_port)

        proxy = EgressProxy(resolver=public_resolver, connect=fake_connect)
        await proxy.start()
        try:
            reader, writer = await open_client(proxy)
            writer.write(
                b"GET http://example.com/a?x=1 HTTP/1.1\r\n"
                b"Host: example.com\r\n" + auth_header(proxy.token) + b"\r\n"
            )
            await writer.drain()
            return await asyncio.wait_for(reader.read(), timeout=3)
        finally:
            writer.close()
            await proxy.close()
            upstream_server.close()
            await upstream_server.wait_closed()

    response = asyncio.run(run())
    assert b"200 OK" in response
    assert response.endswith(b"hello")
    assert len(records) == 1
    forwarded = records[0]
    assert forwarded.startswith("GET /a?x=1 HTTP/1.1")
    assert "Host: example.com" in forwarded
    assert "Proxy-Authorization" not in forwarded
    assert "proxy-authorization" not in forwarded
    assert "Connection: close" in forwarded


def test_upstream_connect_failure_gets_502() -> None:
    """上游连不上 ⇒ 502（不是 500，也不泄露内部地址）。"""
    async def fake_connect(host: str, port: int):
        raise ConnectionRefusedError("refused")

    async def run() -> bytes:
        proxy = EgressProxy(resolver=public_resolver, connect=fake_connect)
        await proxy.start()
        try:
            reader, writer = await open_client(proxy)
            writer.write(
                b"CONNECT example.com:443 HTTP/1.1\r\n"
                b"Host: example.com:443\r\n" + auth_header(proxy.token) + b"\r\n"
            )
            await writer.drain()
            head = await read_head(reader)
            writer.close()
            return head
        finally:
            await proxy.close()

    assert b"502" in asyncio.run(run())


def test_malformed_request_line_gets_400() -> None:
    """请求行不是三段式 ⇒ 400（不能让异常冒到事件循环）。"""
    async def run() -> bytes:
        proxy = EgressProxy()
        await proxy.start()
        try:
            reader, writer = await open_client(proxy)
            writer.write(b"GARBAGE\r\n\r\n")
            await writer.drain()
            head = await read_head(reader)
            writer.close()
            return head
        finally:
            await proxy.close()

    assert b"400" in asyncio.run(run())


def test_close_stops_listening() -> None:
    """`close()` 后端口不再接受连接（防"僵尸代理"继续出网）。"""

    async def run() -> bool:
        proxy = EgressProxy()
        await proxy.start()
        port = proxy.address[1]
        await proxy.close()
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            return True
        writer.close()
        return False

    assert asyncio.run(run()) is True


def test_close_during_active_tunnel_returns_quickly() -> None:
    """有活跃隧道时 `close()` 必须能立即收尾（否则服务端停机/热重载会卡死）。"""

    async def run() -> None:
        hold = asyncio.Event()

        async def silent_upstream(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await hold.wait()
            writer.close()

        upstream_server = await asyncio.start_server(silent_upstream, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]

        async def fake_connect(host: str, port: int):
            return await asyncio.open_connection("127.0.0.1", upstream_port)

        proxy = EgressProxy(resolver=public_resolver, connect=fake_connect)
        await proxy.start()
        reader, writer = await open_client(proxy)
        try:
            writer.write(
                b"CONNECT example.com:443 HTTP/1.1\r\n"
                b"Host: example.com:443\r\n" + auth_header(proxy.token) + b"\r\n"
            )
            await writer.drain()
            assert b"200" in await read_head(reader)
            await asyncio.wait_for(proxy.close(), timeout=3)
        finally:
            writer.close()
            hold.set()
            upstream_server.close()
            await upstream_server.wait_closed()

    asyncio.run(run())


def test_proxy_url_is_usable_as_basic_auth_credentials() -> None:
    """代理 URL 的 userinfo 必须与代理期望的 Basic 头一致（交给 yt-dlp 前先自检）。"""
    async def run() -> bytes:
        proxy = EgressProxy()
        await proxy.start()
        try:
            credentials = base64.b64encode(
                f"jflove:{proxy.token}".encode("ascii")
            ).decode("ascii")
            reader, writer = await open_client(proxy)
            writer.write(
                b"GET http://10.0.0.1/ HTTP/1.1\r\n"
                b"Host: 10.0.0.1\r\n"
                + f"Proxy-Authorization: Basic {credentials}\r\n".encode("ascii")
                + b"\r\n"
            )
            await writer.drain()
            head = await read_head(reader)
            writer.close()
            return head
        finally:
            await proxy.close()

    # 认证通过后才会进入策略判定；被 403（而不是 407）即证明令牌正确
    assert b"403" in asyncio.run(run())


def test_private_resolution_via_injected_resolver_is_forbidden() -> None:
    """域名解析到内网 ⇒ 403 且零连接（拦截 DNS 指向内网的写法）。"""
    calls: list[tuple[str, int]] = []

    async def private_resolver(host: str) -> list[str]:
        return ["192.168.1.20"]

    async def fake_connect(host: str, port: int):
        calls.append((host, port))
        raise AssertionError("内网目标不应被连接")

    async def run() -> bytes:
        proxy = EgressProxy(resolver=private_resolver, connect=fake_connect)
        await proxy.start()
        try:
            reader, writer = await open_client(proxy)
            writer.write(
                b"CONNECT evil.example.com:443 HTTP/1.1\r\n"
                b"Host: evil.example.com:443\r\n" + auth_header(proxy.token) + b"\r\n"
            )
            await writer.drain()
            head = await read_head(reader)
            writer.close()
            return head
        finally:
            await proxy.close()

    assert b"403" in asyncio.run(run())
    assert calls == []

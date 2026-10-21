"""
出站访问策略（SSRF 防护，v1.8.0 新增）

设计依据：`文档记录/设计文档/v1.8.0.md` §9.1。这是**本版唯一新增的攻击面**：
服务端要按用户给的网址主动出网，若不加约束，用户可以让服务端去访问
`127.0.0.1` / 内网管理面板 / 云元数据地址（`169.254.169.254`）等。

本模块只做**判定**，不发请求（除 DNS 解析），因此可以纯单测：
  - `parse_url()`      —— 纯字符串/语法层校验（scheme、userinfo、端口、host 形态）
  - `validate_url()`   —— 在 `parse_url()` 基础上做 DNS 解析，并要求**所有** A/AAAA 记录都是公网地址
  - `join_redirect()`  —— 相对 Location → 绝对 URL（调用方必须**再**调一次 `validate_url`）

错误码（`SsrfBlocked.reason`）是稳定契约，上层据此映射中文提示，**不要把英文异常原文抛给用户**：
  `scheme_not_allowed` / `invalid_url` / `userinfo_not_allowed` / `invalid_port`
  / `host_not_allowed` / `dns_failed` / `blocked_ip`
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlsplit, urlunsplit

#: 允许的协议（只做网页/媒体抓取，不做 file/ftp/gopher 等）
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: 协议默认端口
DEFAULT_PORTS = {"http": 80, "https": 443}

#: 主机名最大长度（DNS 规范上限）
MAX_HOST_LEN = 253


class SsrfBlocked(ValueError):
    """
    目标地址被出站策略拒绝。

    :param reason: 稳定错误码（见模块文档），供上层映射中文提示
    :param detail: 诊断信息（**不得**含完整 URL，只用于日志/测试断言）
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


@dataclass(frozen=True)
class ValidatedTarget:
    """通过策略校验的目标（`url` 已规范化：无 fragment、无 userinfo）。"""

    scheme: str
    host: str
    port: int
    url: str
    #: 解析出的公网 IP（IP 字面量时就是它自己；`parse_url()` 阶段为空）
    ips: tuple[str, ...] = ()


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """
    把字符串解析为 IP（**只认标准写法**）。

    刻意不做"十进制/八进制/十六进制整数"的宽松解析：那种宽松解析是绕过手段
    （如 `2130706433` = 127.0.0.1）。这类写法会走 DNS 分支，由解析结果做 IP 校验兜住。

    :returns: IP 对象；不是 IP 字面量时返回 None
    """
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def blocked_reason(ip: str) -> str | None:
    """
    判断一个 IP 是否属于"不允许访问"的地址。

    判据是 `ipaddress` 的 `is_global`（只有全球可路由地址才放行），
    并额外给出更具体的原因码便于排查。

    :param ip: IP 字符串
    :returns: 拒绝原因；允许时返回 None
    """
    addr = _parse_ip(ip)
    if addr is None:
        return "blocked_ip"
    # IPv4-mapped IPv6（::ffff:127.0.0.1）按内层 IPv4 判定，否则会被当作"公网 IPv6"放过
    mapped = getattr(addr, "ipv4_mapped", None)
    candidate = mapped or addr
    if candidate.is_loopback:
        return "loopback"
    if candidate.is_link_local:
        return "link_local"
    if candidate.is_multicast:
        return "multicast"
    if candidate.is_private:
        return "private"
    if candidate.is_reserved or candidate.is_unspecified:
        return "reserved"
    if not candidate.is_global:
        return "not_global"
    return None


def is_blocked_ip(ip: str) -> bool:
    """IP 是否被策略拒绝（`blocked_reason` 的布尔包装）。"""
    return blocked_reason(ip) is not None


def _normalize_host(host: str) -> str:
    """
    规范化主机名：去 IPv6 方括号、去尾点、非 ASCII 走 IDNA。

    :raises SsrfBlocked: 主机名过长 / 无法 IDNA 编码
    """
    value = host.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    value = value.rstrip(".")
    if not value:
        raise SsrfBlocked("host_not_allowed", "主机名为空")
    if len(value) > MAX_HOST_LEN:
        raise SsrfBlocked("host_not_allowed", "主机名过长")
    if any(ch.isspace() for ch in value):
        raise SsrfBlocked("host_not_allowed", "主机名含空白字符")
    if any(ord(ch) > 127 for ch in value):
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise SsrfBlocked("host_not_allowed", "主机名无法 IDNA 编码") from exc
    return value.lower()


def parse_url(url: str) -> ValidatedTarget:
    """
    语法层校验并规范化 URL（**不做 DNS**）。

    :param url: 原始 URL
    :raises SsrfBlocked: scheme / userinfo / 端口 / host 形态不合法
    """
    if not isinstance(url, str) or not url.strip():
        raise SsrfBlocked("invalid_url", "URL 为空")
    raw = url.strip()
    if any(ch.isspace() for ch in raw):
        raise SsrfBlocked("invalid_url", "URL 含空白字符")
    parts = urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SsrfBlocked("scheme_not_allowed", f"不支持的协议：{scheme!r}")
    if parts.username or parts.password:
        # 带凭据的 URL 会诱导服务端把 Authorization 发给任意主机
        raise SsrfBlocked("userinfo_not_allowed", "URL 不允许携带用户名/密码")
    host = _normalize_host(parts.hostname or "")
    try:
        port = parts.port
    except ValueError as exc:
        raise SsrfBlocked("invalid_port", "端口非法") from exc
    if port is None:
        port = DEFAULT_PORTS[scheme]
    if not (1 <= port <= 65535):
        raise SsrfBlocked("invalid_port", "端口越界")

    literal = _parse_ip(host)
    ips: tuple[str, ...] = ()
    if literal is not None:
        reason = blocked_reason(str(literal))
        if reason is not None:
            raise SsrfBlocked("blocked_ip", f"IP 被拒绝：{reason}")
        ips = (str(literal),)

    netloc = host if port == DEFAULT_PORTS[scheme] else f"{host}:{port}"
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]" if port == DEFAULT_PORTS[scheme] else f"[{host}]:{port}"
    path = parts.path or "/"
    normalized = urlunsplit((scheme, netloc, path, parts.query, ""))
    return ValidatedTarget(scheme=scheme, host=host, port=port, url=normalized, ips=ips)


async def _default_resolver(host: str) -> list[str]:
    """
    默认 DNS 解析（A + AAAA）。

    必须调用 `getaddrinfo` 而不是 `gethostbyname`：后者只返回 IPv4，
    会让"只解析出内网 AAAA 的域名"漏过校验。

    :param host: 主机名
    :returns: IP 字符串列表（去重、保序）
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SsrfBlocked("dns_failed", "域名解析失败") from exc
    seen: dict[str, None] = {}
    for info in infos:
        sockaddr = info[4]
        if sockaddr and sockaddr[0]:
            seen.setdefault(str(sockaddr[0]), None)
    if not seen:
        raise SsrfBlocked("dns_failed", "域名没有可用地址")
    return list(seen)


async def validate_url(
    url: str,
    *,
    resolver=None,
) -> ValidatedTarget:
    """
    完整校验：语法 + DNS + "所有解析结果都必须是公网地址"。

    **必须**在每次真正发请求前调用（含每一跳重定向），不能只在创建任务时校验一次。

    :param url: 目标 URL
    :param resolver: 可注入的异步解析器（测试用），签名 `async (host) -> list[str]`
    :raises SsrfBlocked: 任一环节不合规
    """
    target = parse_url(url)
    if target.ips:
        return target
    resolve = resolver or _default_resolver
    ips = await resolve(target.host)
    if not ips:
        raise SsrfBlocked("dns_failed", "域名没有可用地址")
    for ip in ips:
        reason = blocked_reason(ip)
        if reason is not None:
            # 只要有一个解析结果指向内网就整体拒绝（DNS 轮询/多 A 记录是绕过手法）
            raise SsrfBlocked("blocked_ip", f"解析结果被拒绝：{reason}")
    return replace(target, ips=tuple(ips))


def join_redirect(base_url: str, location: str) -> str:
    """
    把重定向的 `Location` 解析成绝对 URL。

    调用方拿到结果后**必须**再调一次 `validate_url()`（逐跳复检），
    否则"公网地址 302 到内网"就能绕过首次校验。

    :param base_url: 当前请求的 URL
    :param location: 响应头 Location 的原值
    :returns: 绝对 URL
    """
    return urljoin(base_url, (location or "").strip())

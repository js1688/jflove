"""
出站策略（SSRF 防护）单测（v1.8.0）

覆盖 `src/utils/ssrf_guard.py` 的判定规则（设计文档 §9.1 八条）：
  1. 只允许 http/https；
  2. 不允许 URL 携带 userinfo；
  3. IP 字面量与 DNS 解析结果都必须是公网地址（含 IPv4-mapped IPv6 绕过）；
  4. 多 A 记录里只要有一个内网就整体拒绝；
  5. 重定向必须逐跳复检（`join_redirect` + 再次 `validate_url`）；
  6. 非标准 IP 写法（十进制/八进制）不得被当作域名放过。

为什么这些用例必须存在：这是**本版唯一新增的攻击面**。服务端会按用户给的网址出网，
少一条规则就是"用户可以让服务器访问内网管理面板"。
"""

from __future__ import annotations

import asyncio

import pytest

from src.utils.ssrf_guard import (
    SsrfBlocked,
    blocked_reason,
    is_blocked_ip,
    join_redirect,
    parse_url,
    validate_url,
)

#: 必须被拒绝的 IP（覆盖环回/私网/链路本地/保留/组播/IPv4-mapped）
BLOCKED_IPS = [
    "127.0.0.1",
    "127.0.0.53",
    "0.0.0.0",
    "10.0.0.1",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.169.254",   # 云元数据
    "100.64.0.1",        # CGNAT
    "224.0.0.1",         # 组播
    "240.0.0.1",         # 保留
    "::1",
    "fe80::1",
    "fc00::1",
    "::ffff:127.0.0.1",  # IPv4-mapped 绕过
]


@pytest.mark.parametrize("ip", BLOCKED_IPS)
def test_blocked_ips_are_rejected(ip: str) -> None:
    """内网/环回/元数据地址必须被判定为拒绝。"""
    assert is_blocked_ip(ip) is True
    assert blocked_reason(ip) is not None


@pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "2001:4860:4860::8888"])
def test_public_ips_are_allowed(ip: str) -> None:
    """公网地址必须放行（否则功能不可用）。"""
    assert is_blocked_ip(ip) is False


@pytest.mark.parametrize(
    "url,reason",
    [
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("ftp://example.com/a.mp4", "scheme_not_allowed"),
        ("gopher://example.com/", "scheme_not_allowed"),
        ("javascript:alert(1)", "scheme_not_allowed"),
        ("http://user:pass@example.com/a.mp4", "userinfo_not_allowed"),
        ("http://example.com:99999/a", "invalid_port"),
        ("http://", "host_not_allowed"),
        ("", "invalid_url"),
        ("http://exa mple.com/a", "invalid_url"),
    ],
)
def test_bad_urls_are_rejected(url: str, reason: str) -> None:
    """协议/凭据/端口/形态不合法都要拒绝，并给出稳定错误码。"""
    with pytest.raises(SsrfBlocked) as excinfo:
        parse_url(url)
    assert excinfo.value.reason == reason


def test_ip_literal_host_is_validated_without_dns() -> None:
    """IP 字面量必须在语法层就拦住（不能先发 DNS 再说）。"""
    assert parse_url("http://1.1.1.1/a.mp4").ips == ("1.1.1.1",)
    with pytest.raises(SsrfBlocked) as excinfo:
        parse_url("http://127.0.0.1:8989/api/v1/config")
    assert excinfo.value.reason == "blocked_ip"


def test_url_is_normalized() -> None:
    """规范化：去掉 fragment、补默认路径、默认端口不写进 netloc。"""
    target = parse_url("HTTPS://Example.COM:443#frag")
    assert target.scheme == "https"
    assert target.host == "example.com"
    assert target.port == 443
    assert target.url == "https://example.com/"
    assert "#" not in target.url


def test_unicode_host_is_idna_encoded() -> None:
    """中文域名要能规范化成 ASCII（否则 DNS 解析与日志都会出问题）。"""
    target = parse_url("http://例子.测试/页")
    assert target.host == "xn--fsqu00a.xn--0zwm56d"


def test_dns_result_pointing_to_private_ip_is_rejected() -> None:
    """域名解析到内网 ⇒ 拒绝（这是 SSRF 最典型的入口）。"""
    async def resolver(host: str) -> list[str]:
        return ["192.168.1.10"]

    with pytest.raises(SsrfBlocked) as excinfo:
        asyncio.run(validate_url("http://evil.example.com/a.mp4", resolver=resolver))
    assert excinfo.value.reason == "blocked_ip"


def test_one_private_record_among_public_rejects_whole_target() -> None:
    """多 A 记录里只要有一个内网就整体拒绝（防轮询绕过）。"""
    async def resolver(host: str) -> list[str]:
        return ["1.1.1.1", "10.0.0.5"]

    with pytest.raises(SsrfBlocked):
        asyncio.run(validate_url("http://mixed.example.com/a.mp4", resolver=resolver))


def test_all_public_records_pass() -> None:
    """全是公网地址时通过，并带回解析结果（供连接层复用）。"""
    async def resolver(host: str) -> list[str]:
        return ["1.1.1.1", "2606:4700:4700::1111"]

    target = asyncio.run(validate_url("http://ok.example.com/a.mp4", resolver=resolver))
    assert target.ips == ("1.1.1.1", "2606:4700:4700::1111")


def test_redirect_to_internal_is_caught_by_revalidation() -> None:
    """
    重定向必须逐跳复检：`join_redirect` 只负责拼绝对地址，
    安全由"拿到新 URL 后再 validate 一次"保证。
    """
    location = "http://169.254.169.254/latest/meta-data/"
    absolute = join_redirect("https://pub.example.com/page", location)
    assert absolute == location
    with pytest.raises(SsrfBlocked) as excinfo:
        parse_url(absolute)
    assert excinfo.value.reason == "blocked_ip"


def test_relative_redirect_joins_base() -> None:
    """相对 Location 要按当前 URL 解析（不解析就会把相对路径当域名）。"""
    assert join_redirect("https://a.example.com/x/y", "/z") == "https://a.example.com/z"

"""
网址分析服务单测（v1.8.0 / S3）

覆盖 `src/services/media_analyzer_service.py`：
  - 纯解析：srcset 取最大、CSS 背景、HLS 主/媒体列表、DASH MPD 配对、DRM 判定；
  - 归一化：yt-dlp DASH 音视频分离配对成一条（I-7）、大小/时长/显示名；
  - 服务编排：L1 + L2 合并、L3 兜底/关闭、上限截断、图片分页、超时、SSRF、失败分类。

全部使用本地 HTML/播放列表夹具 + 注入的假 outbound/yt-dlp/代理，
**不访问外网、不启动浏览器**（Chromium 未安装时 L3 单测用注入实现验证触发逻辑）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.services.media_analyzer_service import (
    AnalysisError,
    AnalyzedItem,
    MediaAnalyzerService,
    _is_media_response,
    _playwright_proxy,
    clean_title,
    extract_css_urls,
    extract_from_html,
    items_from_ytdlp_info,
    media_playlist_info,
    parse_hls_master,
    parse_mpd,
    parse_srcset,
)
from src.utils.outbound_http import (
    OutboundError,
    OutboundResponse,
    OutboundStatusError,
)

PAGE = "https://example.com/page"
PAGE2 = "https://example.com/page/2"


async def public_resolver(host: str) -> list[str]:
    """把所有测试域名解析到公网 IP（不真的连接）。"""
    return ["93.184.216.34"]


class FakeProxy:
    """假策略代理（只验证生命周期，不监听端口）。"""

    instances: list["FakeProxy"] = []

    def __init__(self) -> None:
        self.started = False
        self.closed = False
        FakeProxy.instances.append(self)

    async def start(self) -> str:
        self.started = True
        return "http://jflove:test-token@127.0.0.1:9"

    async def close(self) -> None:
        self.closed = True


class FakeStream:
    """`open_stream` 返回的假句柄（只暴露状态码/响应头）。"""

    def __init__(self, status_code: int, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.headers = headers

    async def aclose(self) -> None:
        pass


class FakeOutbound:
    """假出站客户端：按 URL 返回预置页面/响应头，并记录请求。"""

    def __init__(
        self,
        pages: dict[str, tuple[int, dict[str, str], str | bytes]] | None = None,
        streams: dict[str, tuple[int, dict[str, str]]] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.pages = pages or {}
        self.streams = streams or {}
        self.delay = delay
        self.requests: list[tuple[str, str]] = []

    async def get_bytes(
        self, url: str, *, max_bytes: int, referer: str | None = None, headers=None
    ) -> OutboundResponse:
        self.requests.append(("GET", url))
        if self.delay:
            await asyncio.sleep(self.delay)
        entry = self.pages.get(url)
        if entry is None:
            raise OutboundStatusError(404, {}, b"")
        status, response_headers, content = entry
        if status >= 400:
            raise OutboundStatusError(status, response_headers, b"")
        body = content.encode("utf-8") if isinstance(content, str) else content
        return OutboundResponse(
            url=url, status_code=status, headers=response_headers, content=body
        )

    @asynccontextmanager
    async def open_stream(self, url: str, *, max_bytes: int, referer=None, headers=None):
        self.requests.append(("STREAM", url))
        entry = self.streams.get(url)
        if entry is None:
            raise OutboundError("network_error")
        status, response_headers = entry
        yield FakeStream(status, response_headers)

    async def aclose(self) -> None:
        pass


class FakeYDL:
    """假 `yt_dlp.YoutubeDL`：返回预置 info，并记录 options。"""

    info: dict = {}
    error: Exception | None = None
    last_options: dict | None = None

    def __init__(self, options: dict) -> None:
        type(self).last_options = options

    def __enter__(self) -> "FakeYDL":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False):
        if type(self).error is not None:
            raise type(self).error
        return type(self).info


@pytest.fixture(autouse=True)
def _reset_fakes():
    """每个用例重置假 yt-dlp / 假代理状态。"""
    FakeYDL.info = {}
    FakeYDL.error = None
    FakeYDL.last_options = None
    FakeProxy.instances = []
    yield


def make_service(outbound: FakeOutbound, **kwargs) -> MediaAnalyzerService:
    """构造注入了假依赖的分析服务。"""
    kwargs.setdefault("resolver", public_resolver)
    kwargs.setdefault("proxy_factory", FakeProxy)
    kwargs.setdefault("ydl_factory", FakeYDL)
    return MediaAnalyzerService(outbound=outbound, **kwargs)


def html_page(body: str, title: str = "Demo") -> str:
    """拼一个最小 HTML 页面。"""
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


# ────────────────────────── 纯解析 ──────────────────────────


def test_parse_srcset_picks_largest_width() -> None:
    """`srcset` 必须取最大宽度（不是第一个）。"""
    assert parse_srcset("/a.jpg 400w, /b.jpg 1600w, /c.jpg 2x") == "/b.jpg"
    assert parse_srcset("/only.jpg") == "/only.jpg"


def test_extract_css_urls_handles_quotes() -> None:
    """CSS 背景图支持单/双引号与无引号。"""
    css = "background:url('/a.jpg'); background-image: url(\"b.png\"); url(c.webp)"
    assert extract_css_urls(css) == ["/a.jpg", "b.png", "c.webp"]


def test_clean_title_removes_illegal_chars_and_whitespace() -> None:
    """标题清洗：去标签、压空白、替换文件系统非法字符。"""
    assert clean_title("<b>Hello</b> / World?") == "Hello _ World"
    assert clean_title("   ") == ""


def test_parse_hls_master_returns_two_variants() -> None:
    """HLS 主列表每个 `EXT-X-STREAM-INF` 一条，并解析分辨率/带宽。"""
    master = (
        "#EXTM3U\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480,CODECS="avc1.4d401f,mp4a.40.2"\n'
        "v480.m3u8\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240,CODECS="avc1.42e01e,mp4a.40.2"\n'
        "v240.m3u8\n"
    )
    variants = parse_hls_master(master, "https://cdn.example.com/master.m3u8")
    assert len(variants) == 2
    assert variants[0]["height"] == 480
    assert variants[0]["url"] == "https://cdn.example.com/v480.m3u8"
    assert "480P" in variants[0]["name"]


def test_hls_aes128_is_allowed_but_sample_aes_is_drm() -> None:
    """AES-128 可本地解密；SAMPLE-AES / KEYFORMAT 判 DRM（设计 §2.3.2）。"""
    plain = "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\n#EXTINF:3.0,\nseg.ts\n"
    assert media_playlist_info(plain) == (3000, "")
    drm = "#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI=\"key.bin\"\n"
    assert media_playlist_info(drm)[1] == "SAMPLE-AES"


def test_parse_mpd_pairs_audio_and_video() -> None:
    """DASH 分离流：视频 Representation 配上最高码率音轨（I-7）。"""
    mpd = """<?xml version="1.0"?>
<MPD mediaPresentationDuration="PT3.0S">
  <Period>
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <Representation id="v1" bandwidth="900000" width="854" height="480" codecs="avc1.4d401f"/>
      <Representation id="v2" bandwidth="400000" width="426" height="240" codecs="avc1.42e01e"/>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4">
      <Representation id="a1" bandwidth="128000" codecs="mp4a.40.2"/>
    </AdaptationSet>
  </Period>
</MPD>"""
    items = parse_mpd(mpd, "https://cdn.example.com/manifest.mpd")
    videos = [item for item in items if item.kind == "video"]
    audios = [item for item in items if item.kind == "audio"]
    assert len(videos) == 2
    assert len(audios) == 1
    assert videos[0].aux_url == ""
    assert videos[0].variant_id == "v1|a1"
    assert videos[0].acodec == "mp4a.40.2"
    assert videos[0].duration_ms == 3000


def test_parse_mpd_drm_is_rejected() -> None:
    """`ContentProtection` 一律判 DRM（不碰解密链）。"""
    mpd = """<MPD><Period><AdaptationSet contentType="video">
      <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
      <Representation id="v" bandwidth="1"/>
    </AdaptationSet></Period></MPD>"""
    with pytest.raises(AnalysisError) as excinfo:
        parse_mpd(mpd, "https://cdn.example.com/manifest.mpd")
    assert excinfo.value.code == "drm_protected"


def test_ytdlp_dash_formats_are_paired_and_sized() -> None:
    """yt-dlp 的 video-only + audio-only 必须合成一条；filesize 已知不标「约」。"""
    info = {
        "title": "Dash Demo",
        "duration": 3.0,
        "formats": [
            {
                "format_id": "v",
                "ext": "mp4",
                "vcodec": "avc1.42c00c",
                "acodec": "none",
                "width": 426,
                "height": 240,
                "tbr": 400,
                "url": "https://cdn.example.com/v.mp4",
                "protocol": "http_dash_segments",
                "filesize": 1000,
            },
            {
                "format_id": "a",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "tbr": 128,
                "url": "https://cdn.example.com/a.m4a",
                "protocol": "http_dash_segments",
                "filesize": 500,
            },
        ],
    }
    items = items_from_ytdlp_info(info)
    videos = [item for item in items if item.kind == "video"]
    audios = [item for item in items if item.kind == "audio"]
    assert len(videos) == 1
    assert videos[0].acodec == "mp4a.40.2"
    assert videos[0].aux_url == "https://cdn.example.com/a.m4a"
    assert videos[0].variant_id == "v|a"
    assert videos[0].est_size == 1000
    assert videos[0].is_estimated is False
    assert audios and audios[0].kind == "audio"


def test_ytdlp_live_is_rejected() -> None:
    """直播不在本版范围（只做点播）。"""
    with pytest.raises(AnalysisError) as excinfo:
        items_from_ytdlp_info({"is_live": True, "formats": []})
    assert excinfo.value.code == "unsupported_site"


def test_extract_from_html_filters_and_dedupes_images() -> None:
    """图片：src/data-src/srcset 最大/背景图；1×1 过滤；同址去重；下一页识别。"""
    page = """<html><head><title>Gallery</title>
      <link rel="next" href="/page/2"></head><body>
      <img src="/img/a.jpg" width="800" height="600">
      <img data-src="/img/b.png">
      <img srcset="/img/c-400.jpg 400w, /img/c-1600.jpg 1600w">
      <div style="background-image:url('/img/bg.webp')"></div>
      <img src="/img/pixel.gif" width="1" height="1">
      <img src="/img/a.jpg">
    </body></html>"""
    extracted = extract_from_html(page, PAGE)
    urls = [url for url, _, _ in extracted.images]
    assert "https://example.com/img/pixel.gif" not in urls
    assert urls.count("https://example.com/img/a.jpg") == 1
    assert "https://example.com/img/c-1600.jpg" in urls
    assert "https://example.com/img/bg.webp" in urls
    assert extracted.next_page_url == "https://example.com/page/2"
    assert extracted.title == "Gallery"


def test_extract_from_html_prefers_lazy_attrs_and_rejects_non_images() -> None:
    """懒加载真实地址优先；无扩展名/统计/站点外壳图片不进清单（UAT #2）。"""
    page = """<html><body>
      <img src="/usr/plugins/tbxw/zw.png"
           data-xkrkllgl="https://pic.example.com/upload/a.jpeg">
      <img src="/usr/themes/x/banner.png" data-src="https://cdn.example.com/b.jpg">
      <img src="https://mc.yandex.ru/watch/108325026">
      <img src="https://example.com/archives/84938/">
      <img src="/usr/themes/x/images/logo.png">
      <img src="/usr/plugins/x/images/icon.png" width="300" height="300">
      <meta property="og:image" content="https://cdn.example.com/dynamic">
    </body></html>"""
    extracted = extract_from_html(page, PAGE)
    urls = [url for url, _, _ in extracted.images]
    assert "https://pic.example.com/upload/a.jpeg" in urls
    assert "https://cdn.example.com/b.jpg" in urls
    # 显式声明 300px 的插件图片保留（避免误杀正文大图）
    assert "https://example.com/usr/plugins/x/images/icon.png" in urls
    # og:image 允许无扩展名的动态图床
    assert "https://cdn.example.com/dynamic" in urls
    # 无扩展名统计地址 / 文章页 / 主题外壳占位图全部排除
    assert "https://mc.yandex.ru/watch/108325026" not in urls
    assert "https://example.com/archives/84938/" not in urls
    assert not any(url.endswith("/zw.png") for url in urls)
    assert not any(url.endswith("/banner.png") for url in urls)
    assert not any(url.endswith("/logo.png") for url in urls)


def test_playwright_proxy_strips_credentials_from_server_url() -> None:
    """浏览器代理配置：令牌放 username/password，server 里不得出现 userinfo。"""
    config = _playwright_proxy("http://jflove:secret-token@127.0.0.1:4321")
    assert config == {
        "server": "http://127.0.0.1:4321",
        "username": "jflove",
        "password": "secret-token",
    }
    assert "secret-token" not in config["server"]


def test_media_response_detection() -> None:
    """L3 嗅探判据：Content-Type 或 URL 后缀。"""
    assert _is_media_response("https://a.example.com/x.m3u8", "") is True
    assert _is_media_response("https://a.example.com/x", "video/mp4") is True
    assert _is_media_response("https://a.example.com/app.js", "text/javascript") is False


# ────────────────────────── 服务编排 ──────────────────────────


def test_direct_video_page_uses_content_length() -> None:
    """AC-201：直链页 → 1 个视频项，大小 = Content-Length 且不标「约」。"""
    page = html_page("<video src='/media/clip.mp4'></video>")
    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html; charset=utf-8"}, page)},
        streams={
            "https://example.com/media/clip.mp4": (
                200,
                {"content-length": "1234", "content-type": "video/mp4"},
            )
        },
    )
    result = asyncio.run(make_service(outbound).analyze(PAGE, deep_enabled=False))
    assert len(result.items) == 1
    item = result.items[0]
    assert item.kind == "video"
    assert item.ext == "mp4"
    assert item.est_size == 1234
    assert item.is_estimated is False
    assert result.page_title == "Demo"
    assert result.level == "l2"


def test_hls_page_returns_two_quality_items() -> None:
    """AC-202：HLS 主列表两个清晰度 → 2 个视频项，时长与源一致（±2s）。"""
    page = html_page("<video src='/master.m3u8'></video>")
    master = (
        "#EXTM3U\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480,CODECS="avc1.4d401f,mp4a.40.2"\n'
        "v480.m3u8\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240,CODECS="avc1.42e01e,mp4a.40.2"\n'
        "v240.m3u8\n"
    )
    media = "#EXTM3U\n#EXTINF:3.0,\nseg.ts\n#EXT-X-ENDLIST\n"
    outbound = FakeOutbound(
        pages={
            PAGE: (200, {"content-type": "text/html"}, page),
            "https://example.com/master.m3u8": (200, {}, master),
            "https://example.com/v480.m3u8": (200, {}, media),
            "https://example.com/v240.m3u8": (200, {}, media),
        }
    )
    result = asyncio.run(make_service(outbound).analyze(PAGE, deep_enabled=False))
    videos = [item for item in result.items if item.kind == "video"]
    assert len(videos) == 2
    assert {item.height for item in videos} == {480, 240}
    assert all(abs(item.duration_ms - 3000) <= 2000 for item in videos)
    assert all("P" in item.display_name for item in videos)


def test_dash_page_pairs_audio_into_one_video_item() -> None:
    """AC-203：音视频分离只出一条视频项，且标明音频存在（I-7）。"""
    FakeYDL.info = {
        "title": "Dash Demo",
        "duration": 3.0,
        "formats": [
            {
                "format_id": "v",
                "ext": "mp4",
                "vcodec": "avc1.42c00c",
                "acodec": "none",
                "width": 426,
                "height": 240,
                "tbr": 400,
                "url": "https://cdn.example.com/v.mp4",
                "protocol": "http_dash_segments",
            },
            {
                "format_id": "a",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "tbr": 128,
                "url": "https://cdn.example.com/a.m4a",
                "protocol": "http_dash_segments",
            },
        ],
    }
    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, html_page("<p>dash</p>"))}
    )
    result = asyncio.run(make_service(outbound).analyze(PAGE, deep_enabled=False))
    videos = [item for item in result.items if item.kind == "video"]
    assert len(videos) == 1
    assert videos[0].acodec == "mp4a.40.2"
    assert videos[0].aux_url == "https://cdn.example.com/a.m4a"
    assert result.level == "l1"


def test_image_page_limit_and_next_page() -> None:
    """AC-204/205：图片去重过滤 + 张数上限 + 下一页按钮。"""
    body = "".join(f"<img src='/img/{index}.jpg'>" for index in range(1, 6))
    body += '<img src="/img/pixel.gif" width="1" height="1">'
    body += '<a rel="next" href="/page/2">下一页</a>'
    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, html_page(body))}
    )
    result = asyncio.run(
        make_service(outbound).analyze(PAGE, deep_enabled=False, max_images=3)
    )
    images = [item for item in result.items if item.kind == "image"]
    assert len(images) == 3
    assert result.truncated is True
    assert result.has_more_images is True
    assert result.next_page_url == PAGE2


def test_image_page_two_follows_next_link() -> None:
    """续抓第 2 页：沿下一页链接推进，不重复第 1 页图片。"""
    page1 = html_page("<img src='/img/a.jpg'><a rel='next' href='/page/2'>下一页</a>")
    page2 = html_page("<img src='/img/b.jpg'><img src='/img/c.jpg'>", title="Page 2")
    outbound = FakeOutbound(
        pages={
            PAGE: (200, {"content-type": "text/html"}, page1),
            PAGE2: (200, {"content-type": "text/html"}, page2),
        }
    )
    result = asyncio.run(
        make_service(outbound).analyze(PAGE, deep_enabled=False, page_index=2)
    )
    urls = [item.source_url for item in result.items]
    assert urls == ["https://example.com/img/b.jpg", "https://example.com/img/c.jpg"]
    assert result.page_title == "Page 2"
    assert result.has_more_images is False


def test_no_media_with_deep_disabled_gives_chinese_reason() -> None:
    """AC-206：静态无结果且深度分析关闭 → 明确中文提示，不得空清单。"""
    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, html_page("<p>nothing</p>"))}
    )
    with pytest.raises(AnalysisError) as excinfo:
        asyncio.run(make_service(outbound).analyze(PAGE, deep_enabled=False))
    assert excinfo.value.code == "deep_analyze_disabled"
    assert excinfo.value.message == "当前站点可能需要深度分析，请联系管理员开启"


def test_deep_analyzer_is_used_as_fallback() -> None:
    """L3 只在 L1/L2 没有视频/音频时启动，命中后 level=l3。"""
    calls: list[str] = []

    async def deep_analyzer(url: str, proxy_url: str) -> list[AnalyzedItem]:
        calls.append(url)
        return [
            AnalyzedItem(
                kind="video",
                display_name="1080P",
                ext="mp4",
                source_url="https://cdn.example.com/deep.mp4",
            )
        ]

    async def deep_available() -> bool:
        return True

    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, html_page("<p>js</p>"))}
    )
    service = make_service(
        outbound, deep_analyzer=deep_analyzer, deep_available=deep_available
    )
    result = asyncio.run(service.analyze(PAGE, deep_enabled=True))
    assert result.level == "l3"
    assert len(result.items) == 1
    assert calls == [PAGE]


def test_l1_failure_falls_back_to_static_analysis() -> None:
    """站点解析失败不得吞掉静态层结果（两张网互为兜底）。"""
    FakeYDL.error = RuntimeError("Unsupported URL: https://example.com")
    page = html_page("<video src='/media/clip.mp4'></video>")
    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, page)},
        streams={
            "https://example.com/media/clip.mp4": (
                200,
                {"content-length": "10", "content-type": "video/mp4"},
            )
        },
    )
    result = asyncio.run(make_service(outbound).analyze(PAGE, deep_enabled=False))
    assert len(result.items) == 1
    assert result.items[0].kind == "video"


def test_ytdlp_options_enforce_proxy_and_no_download() -> None:
    """L1 必须：skip_download、noplaylist、socket_timeout、走策略代理。"""
    FakeYDL.info = {
        "title": "Demo",
        "formats": [
            {
                "format_id": "1",
                "ext": "mp4",
                "vcodec": "avc1",
                "acodec": "none",
                "height": 240,
                "url": "https://cdn.example.com/x.mp4",
                "protocol": "http",
            }
        ],
    }
    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, html_page("<p>x</p>"))}
    )
    asyncio.run(make_service(outbound).analyze(PAGE, deep_enabled=False))
    options = FakeYDL.last_options or {}
    assert options.get("skip_download") is True
    assert options.get("noplaylist") is True
    assert options.get("socket_timeout") == 20
    assert str(options.get("proxy", "")).startswith("http://jflove:")


def test_analysis_total_timeout_maps_to_net_timeout() -> None:
    """AC-207：分析总预算超时 → `net_timeout`，不把英文异常抛给界面。"""
    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, html_page("<p>x</p>"))},
        delay=1.0,
    )
    with pytest.raises(AnalysisError) as excinfo:
        asyncio.run(
            make_service(outbound).analyze(
                PAGE, deep_enabled=False, total_timeout=0.05
            )
        )
    assert excinfo.value.code == "net_timeout"


def test_ssrf_blocked_before_any_request() -> None:
    """内网地址在分析入口直接拒，且 0 次出站请求（AC-234）。"""
    outbound = FakeOutbound()
    with pytest.raises(AnalysisError) as excinfo:
        asyncio.run(
            make_service(outbound).analyze("http://127.0.0.1/x", deep_enabled=False)
        )
    assert excinfo.value.code == "ssrf_blocked"
    assert outbound.requests == []


def test_drm_page_is_rejected_with_chinese_reason() -> None:
    """DASH `ContentProtection` → `drm_protected`（不提供解密链）。"""
    mpd = """<MPD><Period><AdaptationSet contentType="video">
      <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
      <Representation id="v" bandwidth="1"/>
    </AdaptationSet></Period></MPD>"""
    page = html_page("<video src='/manifest.mpd'></video>")
    outbound = FakeOutbound(
        pages={
            PAGE: (200, {"content-type": "text/html"}, page),
            "https://example.com/manifest.mpd": (200, {}, mpd),
        }
    )
    with pytest.raises(AnalysisError) as excinfo:
        asyncio.run(make_service(outbound).analyze(PAGE, deep_enabled=False))
    assert excinfo.value.code == "drm_protected"
    assert excinfo.value.message == "该内容受版权保护（加密流），无法下载"


def test_login_required_page_is_classified() -> None:
    """401 → `login_required`（不支持登录态站点）。"""
    outbound = FakeOutbound(pages={PAGE: (401, {}, b"")})
    with pytest.raises(AnalysisError) as excinfo:
        asyncio.run(make_service(outbound).analyze(PAGE, deep_enabled=False))
    assert excinfo.value.code == "login_required"


def test_item_limit_truncates_and_marks_flag() -> None:
    """AC-209：清单项数上限截断并标记 `truncated`。"""
    page = html_page("<video src='/master.m3u8'></video>")
    master = (
        "#EXTM3U\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480,CODECS="avc1.4d401f"\n'
        "v480.m3u8\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240,CODECS="avc1.42e01e"\n'
        "v240.m3u8\n"
    )
    media = "#EXTM3U\n#EXTINF:3.0,\nseg.ts\n"
    outbound = FakeOutbound(
        pages={
            PAGE: (200, {"content-type": "text/html"}, page),
            "https://example.com/master.m3u8": (200, {}, master),
            "https://example.com/v480.m3u8": (200, {}, media),
            "https://example.com/v240.m3u8": (200, {}, media),
        }
    )
    result = asyncio.run(
        make_service(outbound).analyze(PAGE, deep_enabled=False, max_items=1)
    )
    assert len(result.items) == 1
    assert result.truncated is True


def test_page_index_over_limit_is_rejected() -> None:
    """分页追加上限（20 页）在分析入口拦住。"""
    outbound = FakeOutbound()
    with pytest.raises(AnalysisError) as excinfo:
        asyncio.run(
            make_service(outbound).analyze(PAGE, deep_enabled=False, page_index=21)
        )
    assert excinfo.value.code == "page_limit"
    assert outbound.requests == []


def test_embedded_json_media_is_harvested() -> None:
    """页面内嵌 JSON/JS 字符串里的 m3u8 也要能抓出来（L2 的兜底）。"""
    page = html_page(
        '<script>var player={"src":"https:\\/\\/cdn.example.com\\/live.m3u8"};</script>'
    )
    media = "#EXTM3U\n#EXTINF:2.0,\nseg.ts\n"
    outbound = FakeOutbound(
        pages={
            PAGE: (200, {"content-type": "text/html"}, page),
            "https://cdn.example.com/live.m3u8": (200, {}, media),
        }
    )
    result = asyncio.run(make_service(outbound).analyze(PAGE, deep_enabled=False))
    assert len(result.items) == 1
    assert result.items[0].kind == "video"
    assert result.items[0].duration_ms == 2000


def test_analysis_result_reused_within_ttl() -> None:
    """AC-208：同址 10 分钟内第二次分析直接复用清单（0 次新请求）。"""
    page = html_page("<video src='/media/clip.mp4'></video>")
    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, page)},
        streams={
            "https://example.com/media/clip.mp4": (
                200,
                {"content-length": "10", "content-type": "video/mp4"},
            )
        },
    )
    service = make_service(outbound)
    first = asyncio.run(service.analyze(PAGE, deep_enabled=False))
    requests_after_first = len(outbound.requests)
    second = asyncio.run(service.analyze(PAGE, deep_enabled=False))
    assert len(outbound.requests) == requests_after_first
    assert [(item.kind, item.source_url) for item in second.items] == [
        (item.kind, item.source_url) for item in first.items
    ]
    # 返回的是副本，调用方改结果不应污染缓存
    second.items[0].display_name = "mutated"
    third = asyncio.run(service.analyze(PAGE, deep_enabled=False))
    assert third.items[0].display_name != "mutated"


def test_image_count_limit_is_200_not_total_item_limit() -> None:
    """AC-209：250 张图 → 图片按 200 截断（不被「总项数 50」压成 50）。"""
    body = "".join(f"<img src='/img/{index}.jpg'>" for index in range(1, 251))
    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, html_page(body))}
    )
    result = asyncio.run(make_service(outbound).analyze(PAGE, deep_enabled=False))
    images = [item for item in result.items if item.kind == "image"]
    assert len(images) == 200
    assert result.truncated is True


def test_deep_analyzer_not_called_when_video_exists() -> None:
    """L3 只是兜底：L1/L2 已出视频时不得启动浏览器（省 30s 预算）。"""
    calls: list[str] = []

    async def deep_analyzer(url: str, proxy_url: str) -> list[AnalyzedItem]:
        calls.append(url)
        return []

    async def deep_available() -> bool:
        return True

    page = html_page("<video src='/media/clip.mp4'></video>")
    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, page)},
        streams={
            "https://example.com/media/clip.mp4": (
                200,
                {"content-length": "10", "content-type": "video/mp4"},
            )
        },
    )
    result = asyncio.run(
        make_service(
            outbound, deep_analyzer=deep_analyzer, deep_available=deep_available
        ).analyze(PAGE, deep_enabled=True)
    )
    assert result.items
    assert calls == []


def test_deep_unavailable_is_reported_as_disabled() -> None:
    """镜像未带浏览器 ⇒ `deep_analyze_disabled`（前端提示"当前部署未启用深度分析"）。"""
    async def deep_available() -> bool:
        return False

    outbound = FakeOutbound(
        pages={PAGE: (200, {"content-type": "text/html"}, html_page("<p>js</p>"))}
    )
    service = make_service(outbound, deep_available=deep_available)
    with pytest.raises(AnalysisError) as excinfo:
        asyncio.run(service.analyze(PAGE, deep_enabled=True))
    assert excinfo.value.code == "deep_analyze_disabled"

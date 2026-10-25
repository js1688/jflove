"""
网址分析服务（v1.8.0 新增 / S3）

设计依据：`文档记录/设计文档/v1.8.0.md` §2.3。
把用户给的**无需登录**网页地址，分析成一张可勾选的媒体清单（视频 / 音频 / 图片），
不下载任何媒体内容（唯一例外：播放列表文本本身）。

三级分析（自动降级，用户无感）：
  - **L1**：`yt-dlp.extract_info(download=False)`，必须经 `egress_proxy` 出网；
  - **L2**：`outbound_http` 抓 HTML / 内嵌 JSON / m3u8 / mpd，自研静态解析（图片只在这里出）；
  - **L3**：Playwright + Chromium headless，仅当前两级**没有视频/音频**时兜底，
    浏览器经同一个策略代理出网。

归一化（`normalize`）：
  - DASH 音视频分离必须**配对成一条**视频项（I-7），`aux_url` 带音轨；
  - HLS 主列表每个清晰度一条；图片按 URL 去重、过滤统计像素、`srcset` 取最大；
  - 时长 / 编码 / 分辨率 / 预估大小补全，显示名统一为「1080P · H264 · 5.2 Mbps」；
  - 上限截断（项数 / 图片张数 / 分页页数）并给出 `truncated` 标记。

失败一律抛 `AnalysisError(code)`，`code` 对应设计 §2.2.9 的稳定错误码，
中文文案由 `ANALYSIS_ERROR_MESSAGES` 统一给出，**禁止**把英文异常原文抛给界面。
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

from src.config.settings import (
    OFFLINE_ANALYSIS_REUSE_TTL,
    OFFLINE_ANALYZE_TOTAL_TIMEOUT,
    OFFLINE_BROWSER_IDLE_SECONDS,
    OFFLINE_CONNECT_TIMEOUT,
    OFFLINE_READ_TIMEOUT,
    OFFLINE_DEFAULT_MAX_IMAGES,
    OFFLINE_DEFAULT_MAX_ITEMS,
    OFFLINE_HTML_MAX_BYTES,
    OFFLINE_MAX_IMAGE_PAGES,
    OFFLINE_PLAYLIST_MAX_BYTES,
    OFFLINE_SEGMENT_CONCURRENCY,
    OFFLINE_SEGMENT_MAX_BYTES,
    OFFLINE_USER_AGENT,
)
from src.utils.egress_proxy import EgressProxy
from src.utils.logger import get_logger
from src.utils.outbound_http import (
    OutboundClient,
    OutboundError,
    OutboundStatusError,
    safe_url_label,
)
from src.utils.ssrf_guard import SsrfBlocked, validate_url

logger = get_logger(__name__)

#: 业务错误码 → 中文文案（设计 §2.2.9 / 需求 F29）
ANALYSIS_ERROR_MESSAGES: dict[str, str] = {
    "unsupported_site": "暂不支持这个网站的解析",
    "no_media": "没有在这个页面里找到可下载的内容",
    "deep_analyze_disabled": "当前站点可能需要深度分析，请联系管理员开启",
    "login_required": "该内容需要登录才能访问，本功能不支持登录态站点",
    "drm_protected": "该内容受版权保护（加密流），无法下载",
    "url_expired": "媒体地址已过期，请重新分析",
    "referer_denied": "源站拒绝了请求（防盗链）",
    "net_timeout": "访问源站超时",
    "ssrf_blocked": "该地址不允许访问",
    "parser_outdated": "解析组件可能已过时，等待服务端升级",
    "page_limit": "已达抓取页数上限",
}

#: 视频容器扩展名（媒体清单里出现即为可下载项）
VIDEO_EXTS = frozenset({"mp4", "webm", "mkv", "mov", "m4v", "ts", "flv"})
#: 音频容器扩展名（保留原格式，不转码）
AUDIO_EXTS = frozenset({"mp3", "m4a", "aac", "flac", "wav", "ogg", "opus"})
#: 图片扩展名（image_probe 支持的范围）
IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp", "avif"})
#: 播放列表扩展名
PLAYLIST_EXTS = frozenset({"m3u8", "mpd"})

_ATTR_RE = re.compile(r'([A-Za-z0-9-]+)=("([^"]*)"|[^,]*)')
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_ISO_DURATION_RE = re.compile(
    r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$", re.IGNORECASE
)
#: 明显的统计像素 / 占位图（按 URL 词元匹配，避免误杀 "static" 这类路径）
_TRACKING_IMAGE_RE = re.compile(
    r"(?:^|[/_.-])(1x1|pixel|spacer|transparent|beacon|tracking|analytics)(?:[/_.-]|$)",
    re.IGNORECASE,
)
#: 站点外壳图片（logo / 横幅 / 图标 / 占位图 / 主题与插件静态资源）。
#: 这些通常不是正文图片；只有显式声明了 >=200px 尺寸时才保留。
_CHROME_IMAGE_RE = re.compile(
    r"(?:^|[/_.-])(logo|banner|icon|favicon|avatar|sprite|placeholder|zw)(?:[/_.-]|$)",
    re.IGNORECASE,
)
#: HTML/内嵌 JSON 中直接出现的媒体地址（含转义 `\/`）
_EMBEDDED_MEDIA_RE = re.compile(
    r"https?:\\?/\\?/[^\s\"'<>\\]+?\.(?:m3u8|mpd|mp4|m4v|webm|mkv|m4a|mp3|aac|flac|wav"
    r"|jpe?g|png|gif|webp|bmp|avif)(?:\?[^\s\"'<>\\]*)?",
    re.IGNORECASE,
)
#: 下一页按钮文案（只认明确语义，避免误点普通链接）
_NEXT_PAGE_TEXT_RE = re.compile(r"^(下一页|下页|next|older|»|>)$", re.IGNORECASE)


class AnalysisError(Exception):
    """
    分析失败（稳定错误码）。

    :param code: 设计 §2.2.9 的错误码
    :param detail: 诊断信息（不直接展示给用户，不含完整 URL）
    """

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        self.message = ANALYSIS_ERROR_MESSAGES.get(code, "分析失败")
        super().__init__(self.message)


@dataclass
class AnalyzedItem:
    """一条可下载项（字段与 `offline_download_items` 表一一对应）。"""

    kind: str
    display_name: str
    ext: str
    source_url: str
    aux_url: str = ""
    protocol: str = "http"
    variant_id: str = ""
    vcodec: str = ""
    acodec: str = ""
    width: int = 0
    height: int = 0
    duration_ms: int = 0
    est_size: int = 0
    is_estimated: bool = True
    sort_order: int = 0
    note: str = ""
    #: 内部排序用码率（kbps），不入库
    tbr_kbps: int = 0


@dataclass
class AnalysisResult:
    """一次分析的结果。"""

    page_title: str
    items: list[AnalyzedItem]
    level: str
    truncated: bool = False
    has_more_images: bool = False
    next_page_url: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class _L1Result:
    items: list[AnalyzedItem]
    title: str = ""
    error: AnalysisError | None = None


@dataclass
class _L2Result:
    items: list[AnalyzedItem]
    title: str = ""
    next_page_url: str = ""
    error: AnalysisError | None = None


# ────────────────────────── 通用小工具 ──────────────────────────


def clean_title(value: str, limit: int = 80) -> str:
    """清洗页面标题：去标签、压空白、去文件系统非法字符、限制长度。"""
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", text)
    return text[:limit].strip(" ._") or ""


def absolute_url(base_url: str, value: str) -> str:
    """把相对/转义 URL 解析成绝对地址；`data:` / `blob:` / 锚点返回空串。"""
    text = html.unescape((value or "").strip())
    if not text or text.startswith(("data:", "blob:", "javascript:", "#")):
        return ""
    return urljoin(base_url, text)


def url_ext(url: str) -> str:
    """取 URL 路径扩展名（小写、jpeg→jpg；无扩展名返回空串）。"""
    path = urlsplit(url).path.lower()
    if "." not in path.rsplit("/", 1)[-1]:
        return ""
    suffix = path.rsplit(".", 1)[-1]
    return {"jpeg": "jpg", "m4v": "mp4", "mov": "mp4"}.get(suffix, suffix)


def parse_srcset(value: str) -> str:
    """
    从 `srcset` 里取**最大尺寸**的 URL。

    宽度描述符（`1600w`）与倍率（`2x`）不可直接比较，口径是：
    **只要存在宽度描述符，就只在宽度里取最大**；全无宽度时才按倍率取最大。
    """
    width_best = -1.0
    width_url = ""
    density_best = -1.0
    density_url = ""
    first_url = ""
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        candidate = bits[0]
        if not first_url:
            first_url = candidate
        if len(bits) < 2:
            continue
        descriptor = bits[1].lower()
        try:
            if descriptor.endswith("w"):
                width = float(descriptor[:-1])
                if width > width_best:
                    width_best = width
                    width_url = candidate
            elif descriptor.endswith("x"):
                density = float(descriptor[:-1])
                if density > density_best:
                    density_best = density
                    density_url = candidate
        except ValueError:
            continue
    return width_url or density_url or first_url


def extract_css_urls(text: str) -> list[str]:
    """提取 CSS `background-image: url(...)` 里的地址。"""
    return [match.group(2).strip() for match in _CSS_URL_RE.finditer(text or "")]


def _int_value(value) -> int:
    """把 yt-dlp / HTML 里的数字（可能带单位）安全转成 int。"""
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _attr_list(value: str) -> dict[str, str]:
    """解析 HLS 属性列表（`RESOLUTION=1920x1080,BANDWIDTH=5000000`）。"""
    result: dict[str, str] = {}
    for match in _ATTR_RE.finditer(value or ""):
        key = match.group(1).upper()
        raw = match.group(3) if match.group(3) is not None else match.group(2).strip()
        result[key] = raw
    return result


def _duration_ms(seconds) -> int:
    """秒 → 毫秒（非法值归零）。"""
    try:
        return max(0, int(float(seconds) * 1000))
    except (TypeError, ValueError):
        return 0


def _iso_duration_ms(value: str | None) -> int:
    """ISO8601 时长（`PT3M34.5S`）→ 毫秒。"""
    if not value:
        return 0
    match = _ISO_DURATION_RE.match(value.strip())
    if match is None:
        return 0
    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = float(match.group(4) or 0)
    return int(((days * 86400) + (hours * 3600) + (minutes * 60) + seconds) * 1000)


def _estimate_bytes(tbr_kbps: int, duration_ms: int) -> int:
    """按码率 × 时长估算字节数（结果必须标注「约」，对应 `is_estimated=1`）。"""
    if tbr_kbps <= 0 or duration_ms <= 0:
        return 0
    return int(tbr_kbps * 1000 / 8 * (duration_ms / 1000))


def _codec_short(codec: str) -> str:
    """编码名简化：`avc1.640028` → `avc1`，`mp4a.40.2` → `mp4a`。"""
    return (codec or "").split(".")[0].split(",")[0].strip()


def _display_video(height: int, width: int, vcodec: str, tbr_kbps: int) -> str:
    """视频显示名：`1080P · avc1 · 5.2 Mbps`。"""
    parts: list[str] = []
    if height:
        parts.append(f"{height}P")
    elif width:
        parts.append(f"{width}px")
    codec = _codec_short(vcodec)
    if codec:
        parts.append(codec)
    if tbr_kbps >= 1000:
        parts.append(f"{tbr_kbps / 1000:.1f} Mbps")
    elif tbr_kbps > 0:
        parts.append(f"{tbr_kbps} kbps")
    return " · ".join(parts) or "视频"


def _display_audio(acodec: str, abr_kbps: int) -> str:
    """音频显示名：`音频 · mp4a · 128 kbps`。"""
    parts = ["音频"]
    codec = _codec_short(acodec)
    if codec:
        parts.append(codec)
    if abr_kbps > 0:
        parts.append(f"{abr_kbps} kbps")
    return " · ".join(parts)


def _display_image(width: int, height: int) -> str:
    """图片显示名：`1920×1080`；尺寸未知时用「图片」。"""
    if width and height:
        return f"{width}×{height}"
    return "图片"


def _protocol(value: str) -> str:
    """yt-dlp 协议 → 落库协议口径。"""
    if value in ("m3u8", "m3u8_native"):
        return "m3u8"
    if value in ("http_dash_segments", "dash"):
        return "mpd_segments"
    return "http"


# ────────────────────────── HLS / DASH 解析 ──────────────────────────


def parse_hls_master(text: str, base_url: str) -> list[dict]:
    """
    解析 HLS 主列表里的变体。

    :returns: `[{url, bandwidth, width, height, codecs, name}]`；非主列表返回空列表
    """
    lines = [line.strip() for line in (text or "").splitlines()]
    variants: list[dict] = []
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = _attr_list(line.split(":", 1)[1])
        uri = ""
        for follow in lines[index + 1:]:
            if follow and not follow.startswith("#"):
                uri = follow
                break
        if not uri:
            continue
        width = height = 0
        resolution = attrs.get("RESOLUTION", "")
        if "x" in resolution.lower():
            width_text, _, height_text = resolution.lower().partition("x")
            width = _int_value(width_text)
            height = _int_value(height_text)
        bandwidth = _int_value(attrs.get("BANDWIDTH"))
        codecs = attrs.get("CODECS", "")
        variants.append(
            {
                "url": absolute_url(base_url, uri),
                "bandwidth": bandwidth,
                "width": width,
                "height": height,
                "codecs": codecs,
                "name": _display_video(height, width, codecs, int(bandwidth / 1000)),
            }
        )
    return variants


def media_playlist_info(text: str) -> tuple[int, str]:
    """
    解析 HLS 媒体列表：总时长 + 加密判定。

    :returns: `(时长毫秒, 不支持的解密方式；空串表示可下载)`
    """
    duration = 0.0
    drm = ""
    for line in (text or "").splitlines():
        if line.startswith("#EXTINF:"):
            try:
                duration += float(line.split(":", 1)[1].split(",")[0])
            except (ValueError, IndexError):
                continue
        elif line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
            attrs = _attr_list(line.split(":", 1)[1])
            method = attrs.get("METHOD", "").upper()
            if method in ("", "NONE"):
                continue
            if method != "AES-128":
                drm = method or "UNKNOWN"
            elif attrs.get("KEYFORMAT"):
                # 非默认 KEYFORMAT 属于 DRM（FairPlay / Widevine 等）
                drm = attrs["KEYFORMAT"]
    return int(duration * 1000), drm


def parse_mpd(text: str, mpd_url: str) -> list[AnalyzedItem]:
    """
    解析 DASH MPD：视频/音频 Representation 配对成一条视频项（I-7）。

    :raises AnalysisError: `drm_protected` / `parser_outdated`
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise AnalysisError("parser_outdated", "MPD 解析失败") from exc

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    for element in root.iter():
        if local(element.tag) == "ContentProtection":
            raise AnalysisError("drm_protected", "MPD 含 ContentProtection")

    duration_ms = _iso_duration_ms(
        root.get("mediaPresentationDuration") or root.get("duration")
    )
    videos: list[dict] = []
    audios: list[dict] = []
    for element in root.iter():
        if local(element.tag) != "AdaptationSet":
            continue
        content_type = (element.get("contentType") or "").lower()
        mime_type = (element.get("mimeType") or "").lower()
        if content_type == "audio" or mime_type.startswith("audio"):
            bucket = audios
        elif content_type == "video" or mime_type.startswith("video"):
            bucket = videos
        else:
            continue
        for representation in element:
            if local(representation.tag) != "Representation":
                continue
            bucket.append(
                {
                    "id": representation.get("id", ""),
                    "bandwidth": _int_value(representation.get("bandwidth")),
                    "width": _int_value(representation.get("width")),
                    "height": _int_value(representation.get("height")),
                    "codecs": representation.get("codecs", ""),
                }
            )

    best_audio = max(audios, key=lambda item: item["bandwidth"], default=None)
    items: list[AnalyzedItem] = []
    for video in videos:
        acodec = ""
        aux_id = ""
        if best_audio is not None:
            acodec = best_audio["codecs"]
            aux_id = best_audio["id"]
        variant_id = video["id"] if not aux_id else f"{video['id']}|{aux_id}"
        tbr_kbps = int(video["bandwidth"] / 1000)
        items.append(
            AnalyzedItem(
                kind="video",
                display_name=_display_video(
                    video["height"], video["width"], video["codecs"], tbr_kbps
                ),
                ext="mp4",
                source_url=mpd_url,
                aux_url="",
                protocol="mpd_segments",
                variant_id=variant_id,
                vcodec=video["codecs"],
                acodec=acodec,
                width=video["width"],
                height=video["height"],
                duration_ms=duration_ms,
                est_size=_estimate_bytes(tbr_kbps, duration_ms),
                is_estimated=True,
                tbr_kbps=tbr_kbps,
                note="" if acodec else "无音轨",
            )
        )
    # 纯音频页（或用户只想听声音）：额外给一条音频项（F23.4）
    for audio in audios:
        tbr_kbps = int(audio["bandwidth"] / 1000)
        items.append(
            AnalyzedItem(
                kind="audio",
                display_name=_display_audio(audio["codecs"], tbr_kbps),
                ext="m4a",
                source_url=mpd_url,
                protocol="mpd_segments",
                variant_id=audio["id"],
                acodec=audio["codecs"],
                duration_ms=duration_ms,
                est_size=_estimate_bytes(tbr_kbps, duration_ms),
                is_estimated=True,
                tbr_kbps=tbr_kbps,
            )
        )
    return items


# ────────────────────────── yt-dlp 归一化 ──────────────────────────


def items_from_ytdlp_info(info: dict) -> list[AnalyzedItem]:
    """
    把 `yt-dlp.extract_info` 的 info 归一化成清单项。

    - 音视频分离（DASH/HLS）按「最高音轨」配到每条视频轨（`aux_url`）；
    - 视频按 `(height,width)` 去重，优先保留带音轨/高码率的那条；
    - 音频单独给「仅音频」项；纯音频页只出音频项。

    :raises AnalysisError: 直播（`unsupported_site`）/ 全部 DRM（`drm_protected`）
    """
    if not isinstance(info, dict):
        return []
    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        info = next((entry for entry in entries if isinstance(entry, dict)), {})
        if not info:
            return []
    if info.get("is_live"):
        raise AnalysisError("unsupported_site", "不录制直播")

    duration_ms = _duration_ms(info.get("duration"))
    formats = info.get("formats") or []
    if not formats and info.get("url"):
        formats = [info]

    videos: list[AnalyzedItem] = []
    audios: list[AnalyzedItem] = []
    for fmt in formats:
        if not isinstance(fmt, dict) or fmt.get("has_drm"):
            continue
        source_url = fmt.get("url") or fmt.get("manifest_url") or fmt.get("fragment_base_url")
        if not source_url:
            continue
        vcodec = "" if str(fmt.get("vcodec") or "none") == "none" else str(fmt["vcodec"])
        acodec = "" if str(fmt.get("acodec") or "none") == "none" else str(fmt["acodec"])
        if not vcodec and not acodec:
            continue
        ext = str(fmt.get("ext") or "") or ("mp4" if vcodec else "m4a")
        tbr = _int_value(fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr"))
        width = _int_value(fmt.get("width"))
        height = _int_value(fmt.get("height"))
        format_duration = _duration_ms(fmt.get("duration")) or duration_ms
        filesize = _int_value(fmt.get("filesize") or fmt.get("filesize_approx"))
        is_estimated = filesize <= 0
        size = filesize or _estimate_bytes(tbr, format_duration)
        protocol = _protocol(str(fmt.get("protocol") or "http"))
        variant_id = str(fmt.get("format_id") or "")
        if vcodec:
            item = AnalyzedItem(
                kind="video",
                display_name=_display_video(height, width, vcodec, tbr),
                ext=ext,
                source_url=str(source_url),
                protocol=protocol,
                variant_id=variant_id,
                vcodec=vcodec,
                acodec=acodec,
                width=width,
                height=height,
                duration_ms=format_duration,
                est_size=size,
                is_estimated=is_estimated,
                tbr_kbps=tbr,
            )
            if not acodec:
                item.note = "无音轨"
            videos.append(item)
        else:
            audios.append(
                AnalyzedItem(
                    kind="audio",
                    display_name=_display_audio(acodec, _int_value(fmt.get("abr") or tbr)),
                    ext=ext,
                    source_url=str(source_url),
                    protocol=protocol,
                    variant_id=variant_id,
                    acodec=acodec,
                    width=width,
                    height=height,
                    duration_ms=format_duration,
                    est_size=size,
                    is_estimated=is_estimated,
                    tbr_kbps=tbr,
                )
            )

    if not videos and not audios and info.get("_has_drm"):
        raise AnalysisError("drm_protected", "yt-dlp 标记 DRM")

    # 视频去重：同一分辨率优先保留"带音轨/高码率"的那条
    best_videos: dict[tuple[int, int], AnalyzedItem] = {}
    for item in videos:
        key = (item.height, item.width)
        current = best_videos.get(key)
        if current is None or _video_score(item) > _video_score(current):
            best_videos[key] = item
    merged_videos = sorted(
        best_videos.values(), key=lambda item: (item.height, item.width), reverse=True
    )

    # 音视频分离：给每条视频轨配最好的音频轨（I-7）
    if audios:
        best_audio = max(audios, key=lambda item: item.tbr_kbps)
        for item in merged_videos:
            if item.acodec:
                continue
            item.aux_url = best_audio.source_url
            item.acodec = best_audio.acodec
            item.note = ""
            item.variant_id = f"{item.variant_id}|{best_audio.variant_id}"

    result = list(merged_videos)
    if audios:
        # 额外提供「仅音频」（F23.4）：去重后只留码率最高的一条
        result.append(max(audios, key=lambda item: item.tbr_kbps))
    return result


def _video_score(item: AnalyzedItem) -> tuple[int, int]:
    """视频去重打分：带音轨优先，其次码率。"""
    return (1 if item.acodec or item.aux_url else 0, item.tbr_kbps)


# ────────────────────────── HTML 静态解析 ──────────────────────────


class _PageParser(HTMLParser):
    """收集页面里的标题 / 媒体标签 / 链接 / 内嵌 JSON / 样式。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self._in_title = False
        self.videos: list[dict] = []
        self.audios: list[dict] = []
        self.sources: list[dict] = []
        self.images: list[dict] = []
        self.metas: list[dict] = []
        self.links: list[dict] = []
        self.anchors: list[dict] = []
        self.styles: list[str] = []
        self.inline_styles: list[str] = []
        self.jsonld: list[str] = []
        self._style_buffer: list[str] | None = None
        self._jsonld_buffer: list[str] | None = None
        self._anchor: dict | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        data = {key.lower(): (value or "") for key, value in attrs}
        if data.get("style"):
            self.inline_styles.append(data["style"])
        if tag == "title":
            self._in_title = True
        elif tag == "video":
            self.videos.append(data)
        elif tag == "audio":
            self.audios.append(data)
        elif tag == "source":
            self.sources.append(data)
        elif tag == "img":
            self.images.append(data)
        elif tag == "meta":
            self.metas.append(data)
        elif tag == "link":
            self.links.append(data)
        elif tag == "a":
            self._anchor = {"attrs": data, "text": []}
        elif tag == "style":
            self._style_buffer = []
        elif tag == "script" and "ld+json" in data.get("type", "").lower():
            self._jsonld_buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._anchor is not None:
            self._anchor["text"].append(data)
        if self._style_buffer is not None:
            self._style_buffer.append(data)
        if self._jsonld_buffer is not None:
            self._jsonld_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._anchor is not None:
            self._anchor["text"] = "".join(self._anchor["text"]).strip()
            self.anchors.append(self._anchor)
            self._anchor = None
        elif tag == "style" and self._style_buffer is not None:
            self.styles.append("".join(self._style_buffer))
            self._style_buffer = None
        elif tag == "script" and self._jsonld_buffer is not None:
            self.jsonld.append("".join(self._jsonld_buffer))
            self._jsonld_buffer = None


def _walk_json_urls(node, output: list[str]) -> None:
    """递归收集 JSON-LD 里的 `contentUrl` / `url` / `image`。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("contentUrl", "url", "image", "thumbnailUrl") and isinstance(
                value, str
            ):
                output.append(value)
            elif isinstance(value, (dict, list)):
                _walk_json_urls(value, output)
    elif isinstance(node, list):
        for entry in node:
            _walk_json_urls(entry, output)


def _classify_url(url: str) -> str:
    """按扩展名判断 URL 类型：video / audio / image / playlist / 空。"""
    ext = url_ext(url)
    if ext in PLAYLIST_EXTS:
        return "playlist"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    return ""


def _image_is_tracking(url: str, attrs: dict) -> bool:
    """过滤统计像素 / 占位图（尺寸 <50 或 URL 命中已知词元）。"""
    width = _int_value(attrs.get("width"))
    height = _int_value(attrs.get("height"))
    if width and height and (width < 50 or height < 50):
        return True
    return bool(_TRACKING_IMAGE_RE.search(url))


def _image_is_chrome(url: str, attrs: dict) -> bool:
    """
    过滤站点外壳图片（logo / 横幅 / 主题与插件静态资源）。

    正文图片常常没有宽高属性；这里只在 URL 命中外壳词元或位于 `/themes/`、
    `/plugins/` 下时才判定，且**显式声明 >=200px** 的图片一律保留，
    避免误杀正文里的大图。
    """
    width = _int_value(attrs.get("width"))
    height = _int_value(attrs.get("height"))
    if (width and width >= 200) or (height and height >= 200):
        return False
    path = urlsplit(url).path.lower()
    if "/themes/" in path or "/plugins/" in path:
        return True
    return bool(_CHROME_IMAGE_RE.search(url))


@dataclass
class _HtmlExtract:
    title: str
    media: list[tuple[str, str]]  # (url, hint: video/audio/playlist/image)
    images: list[tuple[str, int, int]]
    next_page_url: str


def extract_from_html(text: str, page_url: str) -> _HtmlExtract:
    """从 HTML 中抽取标题 / 媒体候选 / 图片候选 / 下一页链接。"""
    parser = _PageParser()
    try:
        parser.feed(text or "")
    except Exception:  # HTMLParser 对畸形 HTML 可能抛异常，保留已解析部分
        pass

    title = clean_title("".join(parser.title_parts))
    media: list[tuple[str, str]] = []
    images: list[tuple[str, int, int]] = []

    def add_media(value: str, hint: str = "") -> None:
        url = absolute_url(page_url, value)
        if not url:
            return
        kind = _classify_url(url) or hint
        if kind in ("video", "audio", "playlist"):
            media.append((url, kind))

    def add_image(value: str, attrs: dict | None = None, *, allow_no_ext: bool = False) -> None:
        url = absolute_url(page_url, value)
        if not url:
            return
        kind = _classify_url(url)
        if kind and kind not in ("image",):
            # `srcset`/背景图里偶尔混入视频地址，按媒体候选处理
            if kind in ("video", "audio", "playlist"):
                media.append((url, kind))
            return
        if not kind:
            # 无扩展名（`/watch/123`、`/archives/84938/`、统计脚本等）不做图片猜测：
            # 之前会把 HTML 页面当图片下载，既超时又污染清单。只有显式可信来源
            # （如 og:image）才允许无扩展名。
            if not allow_no_ext:
                return
        if _image_is_tracking(url, attrs or {}):
            return
        # og:image 也要过外壳过滤（实测站点把主题默认图写在 og:image 里）
        if _image_is_chrome(url, attrs or {}):
            return
        if "svg" in url.lower().split("?")[0]:
            return
        images.append(
            (url, _int_value((attrs or {}).get("width")), _int_value((attrs or {}).get("height")))
        )

    for tag in parser.videos:
        add_media(tag.get("src", ""), "video")
    for tag in parser.audios:
        add_media(tag.get("src", ""), "audio")
    for tag in parser.sources:
        add_media(tag.get("src", ""), "")

    for meta in parser.metas:
        key = (meta.get("property") or meta.get("name") or "").lower()
        content = meta.get("content", "")
        if key in ("og:title", "twitter:title") and not title:
            title = clean_title(content)
        elif key in ("og:video", "og:video:url", "og:video:secure_url", "og:audio"):
            add_media(content, "")
        elif key in ("og:image", "og:image:url", "og:image:secure_url"):
            # og:image 是页面显式声明的主图，允许无扩展名的动态图床地址
            add_image(content, allow_no_ext=True)

    for tag in parser.images:
        # 懒加载站点普遍把占位图放在 `src`、真实地址放在 `data-*`：
        # 必须先取 data-*，否则会把占位图当正文图片（实测 `zw.png` 恒中招）。
        for attr in (
            "data-src",
            "data-original",
            "data-lazy-src",
            "data-xkrkllgl",
            "data-echo",
            "data-url",
            "src",
        ):
            value = tag.get(attr, "")
            if value:
                add_image(value, tag)
                break
        srcset = tag.get("srcset") or tag.get("data-srcset") or ""
        if srcset:
            add_image(parse_srcset(srcset), tag)
        style = tag.get("style", "")
        for value in extract_css_urls(style):
            add_image(value, tag)

    for source in parser.sources:
        srcset = source.get("srcset") or source.get("data-srcset") or ""
        if srcset:
            add_image(parse_srcset(srcset))

    for style_text in parser.inline_styles + parser.styles:
        for value in extract_css_urls(style_text):
            add_image(value)

    # JSON-LD（结构化数据里的 contentUrl / image）
    for block in parser.jsonld:
        try:
            data = json.loads(block)
        except (TypeError, ValueError):
            continue
        urls: list[str] = []
        _walk_json_urls(data, urls)
        for value in urls:
            add_media(value)
            add_image(value)

    # 内嵌 JSON / JS 字符串里直接出现的媒体地址
    normalized = html.unescape((text or "").replace("\\/", "/").replace("\\u0026", "&"))
    for match in _EMBEDDED_MEDIA_RE.finditer(normalized):
        add_media(match.group(0))

    next_page_url = ""
    for link in parser.links:
        rel = (link.get("rel") or "").lower().split()
        if "next" in rel and link.get("href"):
            next_page_url = absolute_url(page_url, link["href"])
            break
    if not next_page_url:
        for anchor in parser.anchors:
            attrs = anchor.get("attrs", {})
            rel = (attrs.get("rel") or "").lower().split()
            text = anchor.get("text", "").strip()
            href = attrs.get("href", "")
            if not href:
                continue
            if "next" in rel or _NEXT_PAGE_TEXT_RE.match(text):
                next_page_url = absolute_url(page_url, href)
                break

    return _HtmlExtract(
        title=title,
        media=_dedupe_media(media),
        images=_dedupe_images(images),
        next_page_url=next_page_url,
    )


def _dedupe_media(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """媒体候选按 URL 去重（保留第一次出现的提示类型）。"""
    seen: dict[str, None] = {}
    result: list[tuple[str, str]] = []
    for url, hint in items:
        key = url.split("#", 1)[0]
        if key in seen:
            continue
        seen[key] = None
        result.append((url, hint))
    return result


def _dedupe_images(items: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """图片候选按 URL 去重（保留第一次出现的宽高）。"""
    seen: dict[str, None] = {}
    result: list[tuple[str, int, int]] = []
    for url, width, height in items:
        key = url.split("#", 1)[0]
        if key in seen:
            continue
        seen[key] = None
        result.append((url, width, height))
    return result


# ────────────────────────── 浏览器管理 ──────────────────────────


class _YdlLogger:
    """yt-dlp 日志接收器：吞掉输出，避免把 URL / query 打到控制台。"""

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


def _playwright_proxy(proxy_url: str) -> dict:
    """把代理 URL 拆成 Playwright 需要的 `server/username/password`。"""
    parts = urlsplit(proxy_url)
    config: dict[str, str] = {"server": f"http://{parts.hostname}:{parts.port}"}
    if parts.username:
        config["username"] = unquote(parts.username)
    if parts.password:
        config["password"] = unquote(parts.password)
    return config


def _is_media_response(url: str, content_type: str) -> bool:
    """L3 嗅探判据：URL 后缀或 Content-Type 命中媒体类型。"""
    lower_type = (content_type or "").lower()
    if lower_type.startswith(("video/", "audio/", "image/")):
        return True
    if "mpegurl" in lower_type or "dash+xml" in lower_type:
        return True
    return _classify_url(url) != ""


def _system_chromium() -> str:
    """系统 Chromium 内核浏览器（Playwright 未下载浏览器时的兜底）。"""
    for name in (
        "microsoft-edge",
        "microsoft-edge-stable",
        "chromium",
        "google-chrome",
        "chrome",
    ):
        path = shutil.which(name)
        if path:
            return path
    return ""


class _BrowserManager:
    """Playwright 浏览器单实例（懒启动、60s 闲置回收、崩溃重启一次）。"""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._last_used = 0.0
        self._lock = asyncio.Lock()
        self._available: bool | None = None

    async def available(self) -> bool:
        """
        探测浏览器运行时是否可用（**不启动浏览器**，避免 `/meta` 每次拉起 Chromium）。

        判据：Playwright 自带 Chromium 二进制存在，或系统有 Chromium 内核可兜底。
        """
        if self._available is not None:
            return self._available
        try:
            from playwright.async_api import async_playwright

            if self._playwright is None:
                self._playwright = await async_playwright().start()
            bundled = self._playwright.chromium.executable_path
            self._available = bool(bundled and os.path.exists(bundled)) or bool(
                _system_chromium()
            )
        except Exception:
            self._available = False
        return self._available

    async def _ensure_browser(self):
        if self._browser is not None and not self._browser.is_connected():
            self._browser = None
        idle = time.monotonic() - self._last_used
        if self._browser is not None and idle > OFFLINE_BROWSER_IDLE_SECONDS:
            await self._close_browser()
        if self._browser is None:
            from playwright.async_api import async_playwright

            if self._playwright is None:
                self._playwright = await async_playwright().start()
            launch_args = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-dev-shm-usage"],
            }
            try:
                self._browser = await self._playwright.chromium.launch(**launch_args)
            except Exception:
                # 镜像内没有 Playwright 自带 Chromium 时，退回复用系统 Chromium 内核
                # （本机为 Microsoft Edge），避免深度分析被“没下载浏览器”一刀切关闭。
                system_browser = _system_chromium()
                if not system_browser:
                    raise
                self._browser = await self._playwright.chromium.launch(
                    **launch_args, executable_path=system_browser
                )
        self._last_used = time.monotonic()
        return self._browser

    async def new_context(self, proxy_url: str):
        """创建任务级 context（同一浏览器实例内串行创建）。"""
        async with self._lock:
            browser = await self._ensure_browser()
            context = await browser.new_context(
                proxy=_playwright_proxy(proxy_url) if proxy_url else None,
                user_agent=OFFLINE_USER_AGENT,
                accept_downloads=False,
            )
            self._last_used = time.monotonic()
            return context

    async def close_idle(self) -> None:
        """闲置回收（供 S5 后台巡检调用）。"""
        async with self._lock:
            if (
                self._browser is not None
                and time.monotonic() - self._last_used > OFFLINE_BROWSER_IDLE_SECONDS
            ):
                await self._close_browser()

    async def _close_browser(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

    async def shutdown(self) -> None:
        """进程退出时释放浏览器与 Playwright driver。"""
        async with self._lock:
            await self._close_browser()
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            self._available = None


#: 全局浏览器管理器（单实例；S5 的巡检可调用 `close_idle()`）
BROWSER_MANAGER = _BrowserManager()


class MediaAnalyzerService:
    """
    网址分析服务。

    :param outbound: 仅单测注入的出站客户端（生产为 None，内部创建/关闭）
    :param proxy_factory: 策略代理工厂（单测可注入假对象）
    :param ydl_factory: yt-dlp 的 `YoutubeDL` 工厂（单测可注入假实现）
    :param deep_analyzer: 仅单测注入的 L3 实现 `async (url, proxy_url) -> items`
    :param deep_available: 仅单测注入的 L3 可用性探测 `async () -> bool`
    """

    def __init__(
        self,
        *,
        outbound: OutboundClient | None = None,
        proxy_factory=EgressProxy,
        ydl_factory=None,
        deep_analyzer=None,
        deep_available=None,
        browser_manager: _BrowserManager | None = None,
        resolver=None,
        connect_timeout: float = OFFLINE_CONNECT_TIMEOUT,
        read_timeout: float = OFFLINE_READ_TIMEOUT,
    ) -> None:
        self._outbound = outbound
        #: v1.8.2：出站超时（管理员可配，由 S5 传入；单测默认常量）
        self._connect_timeout = float(connect_timeout)
        self._read_timeout = float(read_timeout)
        self._proxy_factory = proxy_factory
        self._ydl_factory = ydl_factory
        self._deep_analyzer = deep_analyzer
        self._deep_available = deep_available
        self._browser_manager = browser_manager or BROWSER_MANAGER
        #: 仅单测注入的 DNS 解析器（生产为 None，走 ssrf_guard 默认实现）
        self._resolver = resolver
        #: 同址分析结果缓存（AC-208）：`key -> (monotonic 时间, 结果)`
        self._cache: dict[tuple, tuple[float, AnalysisResult]] = {}

    async def analyze(
        self,
        url: str,
        *,
        deep_enabled: bool = True,
        max_items: int = OFFLINE_DEFAULT_MAX_ITEMS,
        max_images: int = OFFLINE_DEFAULT_MAX_IMAGES,
        page_index: int = 1,
        total_timeout: float = OFFLINE_ANALYZE_TOTAL_TIMEOUT,
    ) -> AnalysisResult:
        """
        分析一个无需登录的页面地址。

        :param deep_enabled: 管理员级「深度分析」开关
        :param max_items: **非图片**可下载项最大条数（图片由 `max_images` 单独约束）
        :param max_images: 单页图片最大张数（超出截断）
        :param page_index: 图片分页序号（1 起；>1 表示「继续抓下一页」）
        :param total_timeout: 单次分析总预算（秒）
        :raises AnalysisError: 稳定错误码（见模块级 `ANALYSIS_ERROR_MESSAGES`）
        """
        if page_index < 1:
            raise AnalysisError("no_media", "页码非法")
        if page_index > OFFLINE_MAX_IMAGE_PAGES:
            raise AnalysisError("page_limit")
        max_items = max(1, int(max_items))
        max_images = max(1, int(max_images))

        try:
            target = await validate_url(url, resolver=self._resolver)
        except SsrfBlocked as exc:
            raise AnalysisError("ssrf_blocked", exc.reason) from exc

        # AC-208：同址 10 分钟内复用（媒体地址带签名，过期必须重新分析）
        cache_key = (target.url, bool(deep_enabled), max_items, max_images, page_index)
        if page_index == 1:
            cached = self._cache.get(cache_key)
            if cached is not None and time.monotonic() - cached[0] <= OFFLINE_ANALYSIS_REUSE_TTL:
                return _copy_result(cached[1])

        outbound = self._outbound or OutboundClient(
            connect_timeout=self._connect_timeout,
            read_timeout=self._read_timeout,
        )
        created_outbound = self._outbound is None
        warnings: list[str] = []
        # v1.8.2（F37.2）：L3 的 goto 超时与总预算联动（默认预算下 ≈54s，替代固定 20s）
        goto_timeout_ms = 20000
        if total_timeout and total_timeout > 0:
            goto_timeout_ms = int(min(90000, max(20000, total_timeout * 1000 * 0.6)))
        # v1.8.2（F37.4）：超时保留已抓到的部分结果（设计 v1.8.0 §2.3.3 的未落地项）
        collected: dict = {"items": [], "title": ""}
        try:
            if total_timeout and total_timeout > 0:
                async with asyncio.timeout(total_timeout):
                    result = await self._analyze_inner(
                        target.url,
                        outbound=outbound,
                        deep_enabled=deep_enabled,
                        page_index=page_index,
                        max_items=max_items,
                        max_images=max_images,
                        warnings=warnings,
                        collected=collected,
                        goto_timeout_ms=goto_timeout_ms,
                    )
            else:
                result = await self._analyze_inner(
                    target.url,
                    outbound=outbound,
                    deep_enabled=deep_enabled,
                    page_index=page_index,
                    max_items=max_items,
                    max_images=max_images,
                    warnings=warnings,
                    collected=collected,
                    goto_timeout_ms=goto_timeout_ms,
                )
            if page_index == 1:
                self._remember(cache_key, result)
            return result
        except TimeoutError as exc:
            partial = list(collected.get("items") or [])
            if partial:
                # 已抓到部分结果：落 ready + warning，而不是整体失败
                finalized, truncated = _finalize(partial, max_items, max_images)
                if finalized:
                    for index, item in enumerate(finalized):
                        item.sort_order = index
                    warnings.append("解析超时，已返回部分结果")
                    return AnalysisResult(
                        page_title=clean_title(str(collected.get("title") or "")) or "离线下载",
                        items=finalized,
                        level="partial",
                        truncated=truncated,
                        has_more_images=False,
                        next_page_url="",
                        warnings=warnings,
                    )
            budget = int(total_timeout) if total_timeout else int(OFFLINE_ANALYZE_TOTAL_TIMEOUT)
            raise AnalysisError(
                "net_timeout", f"解析超时（{budget}s），可稍后重试或让管理员调大解析超时"
            ) from exc
        finally:
            if created_outbound:
                await outbound.aclose()

    def _remember(self, cache_key: tuple, result: AnalysisResult) -> None:
        """写入分析缓存，并做一次惰性清理（避免长时间运行无限增长）。"""
        now = time.monotonic()
        if len(self._cache) >= 64:
            self._cache = {
                key: value
                for key, value in self._cache.items()
                if now - value[0] <= OFFLINE_ANALYSIS_REUSE_TTL
            }
            if len(self._cache) >= 64:
                oldest = min(self._cache, key=lambda key: self._cache[key][0])
                self._cache.pop(oldest, None)
        self._cache[cache_key] = (now, _copy_result(result))

    async def _analyze_inner(
        self,
        url: str,
        *,
        outbound: OutboundClient,
        deep_enabled: bool,
        page_index: int,
        max_items: int,
        max_images: int,
        warnings: list[str],
        collected: dict | None = None,
        goto_timeout_ms: int = 20000,
    ) -> AnalysisResult:
        """真正的分析流程（代理的启动/关闭都在这里，保证超时也能收尾）。"""
        proxy = self._proxy_factory()
        proxy_url = ""
        try:
            try:
                proxy_url = await proxy.start()
            except OSError as exc:
                warnings.append("策略代理启动失败，已跳过站点解析")
                logger.error("策略代理启动失败：%s", type(exc).__name__)

            if page_index > 1:
                items, title, next_url = await self._analyze_image_page(
                    outbound, url, page_index
                )
                level = "l2"
            else:
                l1_result, l2_result = await asyncio.gather(
                    self._analyze_l1(url, proxy_url),
                    self._analyze_l2(outbound, url),
                    return_exceptions=True,
                )
                if isinstance(l1_result, BaseException):
                    l1_result = _L1Result([], "", _as_analysis_error(l1_result))
                if isinstance(l2_result, BaseException):
                    l2_result = _L2Result([], "", "", _as_analysis_error(l2_result))
                items = _merge_items(l1_result.items, l2_result.items)
                title = l2_result.title or l1_result.title
                next_url = l2_result.next_page_url
                level = "l1" if l1_result.items else "l2"
                if collected is not None:
                    collected["items"] = list(items)
                    collected["title"] = title

                has_av = any(item.kind in ("video", "audio") for item in items)
                l3_error: AnalysisError | None = None
                if not has_av:
                    if not deep_enabled:
                        l3_error = AnalysisError("deep_analyze_disabled")
                    elif not await self._deep_available_flag():
                        l3_error = AnalysisError("deep_analyze_disabled")
                    else:
                        try:
                            l3_items = await self._analyze_deep(
                                url, proxy_url, outbound, goto_timeout_ms=goto_timeout_ms
                            )
                        except AnalysisError as exc:
                            l3_error = exc
                        except Exception as exc:
                            l3_error = _as_analysis_error(exc)
                        else:
                            if l3_items:
                                items = _merge_items(items, l3_items)
                                level = "l3"
                                if collected is not None:
                                    collected["items"] = list(items)
                            else:
                                l3_error = AnalysisError("no_media")
                if not items:
                    raise _pick_failure(l1_result.error, l2_result.error, l3_error)

            finalized, truncated = _finalize(items, max_items, max_images)
            if not finalized:
                raise AnalysisError(
                    "deep_analyze_disabled" if not deep_enabled else "no_media"
                )
            for index, item in enumerate(finalized):
                item.sort_order = index
            has_more = bool(next_url) and page_index < OFFLINE_MAX_IMAGE_PAGES
            return AnalysisResult(
                page_title=clean_title(title) or "离线下载",
                items=finalized,
                level=level,
                truncated=truncated,
                has_more_images=has_more,
                next_page_url=next_url,
                warnings=warnings,
            )
        finally:
            await proxy.close()

    async def _deep_available_flag(self) -> bool:
        """深度分析组件是否可用（单测注入优先）。"""
        if self._deep_available is not None:
            return bool(await self._deep_available())
        return await self._browser_manager.available()

    # ── L1: yt-dlp ──────────────────────────────────────────────

    async def _analyze_l1(self, url: str, proxy_url: str) -> _L1Result:
        """在线程里跑 yt-dlp（阻塞库），失败只记 warning，不阻断 L2。"""
        if not proxy_url:
            return _L1Result([], "", AnalysisError("unsupported_site", "代理不可用"))
        try:
            info = await asyncio.to_thread(self._extract_l1_sync, url, proxy_url)
            items = items_from_ytdlp_info(info)
            return _L1Result(items, clean_title(str(info.get("title") or "")), None)
        except AnalysisError as exc:
            return _L1Result([], "", exc)
        except Exception as exc:
            code = _classify_ytdlp_error(str(exc))
            logger.info("站点解析失败 %s：%s", safe_url_label(url), code)
            return _L1Result([], "", AnalysisError(code))

    def _extract_l1_sync(self, url: str, proxy_url: str) -> dict:
        """同步调用 yt-dlp，只取清单（`skip_download=True`）。"""
        try:
            import yt_dlp
        except ImportError as exc:
            raise AnalysisError("parser_outdated", "yt-dlp 未安装") from exc
        factory = self._ydl_factory or yt_dlp.YoutubeDL
        options = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "socket_timeout": 20,
            "proxy": proxy_url,
            "cachedir": False,
            "writeinfojson": False,
            "writethumbnail": False,
            "writesubtitles": False,
            "logger": _YdlLogger(),
        }
        with factory(options) as ydl:
            info = ydl.extract_info(url, download=False)
        return info if isinstance(info, dict) else {}

    # ── L2: 静态抓取 ────────────────────────────────────────────

    async def _analyze_l2(self, outbound: OutboundClient, url: str) -> _L2Result:
        """抓 HTML / 播放列表 / 直链，产出视频、音频、图片清单。"""
        try:
            return await self._analyze_l2_inner(outbound, url)
        except AnalysisError as exc:
            return _L2Result([], "", "", exc)
        except OutboundStatusError as exc:
            return _L2Result([], "", "", _status_error(exc))
        except OutboundError as exc:
            return _L2Result([], "", "", AnalysisError(_outbound_error_code(exc)))
        except Exception:
            logger.error("静态分析失败 %s", safe_url_label(url))
            return _L2Result([], "", "", AnalysisError("parser_outdated"))

    async def _analyze_l2_inner(self, outbound: OutboundClient, url: str) -> _L2Result:
        ext = url_ext(url)
        if ext in PLAYLIST_EXTS:
            items = await self._expand_playlist(outbound, url, referer=url)
            return _L2Result(items, "", "")
        if ext in VIDEO_EXTS or ext in AUDIO_EXTS or ext in IMAGE_EXTS:
            item = await self._item_from_direct_url(outbound, url, url)
            return _L2Result([item] if item else [], "", "")

        text, final_url = await self._fetch_page(outbound, url)
        extracted = extract_from_html(text, final_url)
        items: list[AnalyzedItem] = []
        for media_url, hint in extracted.media[:20]:
            try:
                if _classify_url(media_url) == "playlist":
                    expanded = await self._expand_playlist(
                        outbound, media_url, referer=final_url
                    )
                    items.extend(expanded)
                else:
                    item = await self._item_from_direct_url(
                        outbound, media_url, final_url, hint=hint
                    )
                    if item is not None:
                        items.append(item)
            except AnalysisError as exc:
                if exc.code == "drm_protected":
                    raise
                continue
            except (OutboundError, OutboundStatusError):
                continue
        for image_url, width, height in extracted.images:
            items.append(
                AnalyzedItem(
                    kind="image",
                    display_name=_display_image(width, height),
                    ext=url_ext(image_url) or "jpg",
                    source_url=image_url,
                    protocol="http",
                    width=width,
                    height=height,
                )
            )
        return _L2Result(items, extracted.title, extracted.next_page_url)

    async def _fetch_page(self, outbound: OutboundClient, url: str) -> tuple[str, str]:
        """抓页面 HTML；401/403 映射为登录/防盗链失败。"""
        response = await outbound.get_bytes(url, max_bytes=OFFLINE_HTML_MAX_BYTES, referer=url)
        content_type = str(response.headers.get("content-type", "")).lower()
        if content_type and not any(
            marker in content_type for marker in ("html", "xml", "text/plain", "json")
        ):
            # 非 HTML 页面：交给调用方按扩展名处理（这里返回空文本）
            return "", response.url
        charset = "utf-8"
        match = re.search(r"charset=([\w-]+)", content_type)
        if match:
            charset = match.group(1)
        try:
            return response.content.decode(charset, "replace"), response.url
        except LookupError:
            return response.content.decode("utf-8", "replace"), response.url

    async def _item_from_direct_url(
        self,
        outbound: OutboundClient,
        media_url: str,
        referer: str,
        *,
        hint: str = "",
    ) -> AnalyzedItem | None:
        """直链媒体：只取响应头（不读正文），拿 Content-Length / Content-Type。"""
        try:
            async with outbound.open_stream(
                media_url, max_bytes=OFFLINE_SEGMENT_MAX_BYTES, referer=referer
            ) as stream:
                if stream.status_code >= 400:
                    return None
                headers = dict(stream.headers)
        except (OutboundError, OutboundStatusError):
            return None
        content_type = str(headers.get("content-type", "")).lower()
        length_text = headers.get("content-length")
        try:
            size = int(length_text) if length_text is not None else 0
        except ValueError:
            size = 0
        ext = url_ext(media_url)
        if not ext:
            if content_type.startswith("image/"):
                ext = content_type.split("/", 1)[1].split(";")[0] or "jpg"
            elif content_type.startswith("audio/"):
                ext = "m4a"
            else:
                ext = "mp4"
        if content_type.startswith("image/") or ext in IMAGE_EXTS:
            return AnalyzedItem(
                kind="image",
                display_name="图片",
                ext=ext,
                source_url=media_url,
                protocol="http",
                est_size=size,
                is_estimated=size <= 0,
            )
        kind = "audio" if (content_type.startswith("audio/") or ext in AUDIO_EXTS) else "video"
        if hint == "audio" and kind == "video":
            kind = "audio"
        return AnalyzedItem(
            kind=kind,
            display_name="音频" if kind == "audio" else "视频",
            ext=ext,
            source_url=media_url,
            protocol="http",
            est_size=size,
            is_estimated=size <= 0,
        )

    async def _expand_playlist(
        self, outbound: OutboundClient, url: str, *, referer: str
    ) -> list[AnalyzedItem]:
        """展开 HLS 主列表 / 媒体列表 / DASH MPD。"""
        response = await outbound.get_bytes(
            url, max_bytes=OFFLINE_PLAYLIST_MAX_BYTES, referer=referer
        )
        text = response.content.decode("utf-8", "replace")
        if url_ext(url) == "mpd" or text.lstrip().startswith("<"):
            return parse_mpd(text, response.url)

        variants = parse_hls_master(text, response.url)
        if not variants:
            duration_ms, drm = media_playlist_info(text)
            if drm:
                raise AnalysisError("drm_protected", f"HLS 加密方式 {drm}")
            return [
                AnalyzedItem(
                    kind="video",
                    display_name="视频 · HLS",
                    ext="mp4",
                    source_url=response.url,
                    protocol="m3u8",
                    duration_ms=duration_ms,
                )
            ]

        # 主列表：每个清晰度一条；并发取各变体媒体列表补时长
        semaphore = asyncio.Semaphore(OFFLINE_SEGMENT_CONCURRENCY)

        async def duration_for(variant: dict) -> tuple[int, str]:
            async with semaphore:
                try:
                    media = await outbound.get_bytes(
                        variant["url"],
                        max_bytes=OFFLINE_PLAYLIST_MAX_BYTES,
                        referer=referer,
                    )
                except (OutboundError, OutboundStatusError):
                    return 0, ""
                return media_playlist_info(media.content.decode("utf-8", "replace"))

        durations = await asyncio.gather(*(duration_for(variant) for variant in variants))
        items: list[AnalyzedItem] = []
        for variant, (duration_ms, drm) in zip(variants, durations):
            if drm:
                raise AnalysisError("drm_protected", f"HLS 加密方式 {drm}")
            tbr = int(variant["bandwidth"] / 1000)
            codecs = variant["codecs"]
            vcodec = codecs.split(",")[0].strip() if codecs else ""
            acodec = codecs.split(",")[1].strip() if "," in codecs else ""
            items.append(
                AnalyzedItem(
                    kind="video",
                    display_name=variant["name"],
                    ext="mp4",
                    source_url=variant["url"],
                    protocol="m3u8",
                    variant_id=str(variant["bandwidth"]),
                    vcodec=vcodec,
                    acodec=acodec,
                    width=variant["width"],
                    height=variant["height"],
                    duration_ms=duration_ms,
                    est_size=_estimate_bytes(tbr, duration_ms),
                    is_estimated=True,
                    tbr_kbps=tbr,
                )
            )
        return items

    # ── L3: 浏览器嗅探 ──────────────────────────────────────────

    async def _analyze_deep(
        self,
        url: str,
        proxy_url: str,
        outbound: OutboundClient,
        *,
        goto_timeout_ms: int = 20000,
    ) -> list[AnalyzedItem]:
        """L3：无头浏览器拦截媒体响应；只兜底，不替代前两级。"""
        if self._deep_analyzer is not None:
            return await self._deep_analyzer(url, proxy_url)
        return await self._playwright_analyze(
            url, proxy_url, outbound, goto_timeout_ms=goto_timeout_ms
        )

    async def _playwright_analyze(
        self,
        url: str,
        proxy_url: str,
        outbound: OutboundClient,
        *,
        goto_timeout_ms: int = 20000,
    ) -> list[AnalyzedItem]:
        """Playwright 嗅探实现（浏览器缺失时由调用方决定降级文案）。"""
        context = await self._browser_manager.new_context(proxy_url)
        found: dict[str, str] = {}
        try:
            page = await context.new_page()

            def on_response(response) -> None:
                response_url = response.url
                content_type = response.headers.get("content-type", "")
                if _is_media_response(response_url, content_type):
                    found.setdefault(response_url, content_type)

            page.on("response", on_response)
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=goto_timeout_ms
                )
            except Exception as exc:
                # playwright 的 TimeoutError 不是内置 TimeoutError，按类名识别
                if type(exc).__name__ == "TimeoutError":
                    raise AnalysisError("net_timeout", "浏览器打开页面超时") from exc
                raise
            await page.wait_for_timeout(3000)
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            await page.wait_for_timeout(2000)
        finally:
            try:
                await context.close()
            except Exception:
                pass

        items: list[AnalyzedItem] = []
        for media_url, content_type in found.items():
            try:
                if _classify_url(media_url) == "playlist":
                    items.extend(await self._expand_playlist(outbound, media_url, referer=url))
                elif content_type.startswith("image/") or _classify_url(media_url) == "image":
                    items.append(
                        AnalyzedItem(
                            kind="image",
                            display_name="图片",
                            ext=url_ext(media_url) or "jpg",
                            source_url=media_url,
                            protocol="http",
                        )
                    )
                else:
                    item = await self._item_from_direct_url(
                        outbound, media_url, url, hint="video"
                    )
                    if item is not None:
                        items.append(item)
            except (AnalysisError, OutboundError, OutboundStatusError):
                continue
        return items

    # ── 图片分页（续抓）─────────────────────────────────────────

    async def _analyze_image_page(
        self, outbound: OutboundClient, url: str, page_index: int
    ) -> tuple[list[AnalyzedItem], str, str]:
        """
        续抓第 N 页图片。

        任务表没有保存"下一页 URL"，因此从第 1 页开始沿 `rel=next` / 「下一页」逐页推进；
        每页只解析 HTML，不下载图片。页数上限由调用方保证。
        """
        current = url
        for _ in range(1, page_index):
            text, final_url = await self._fetch_page(outbound, current)
            extracted = extract_from_html(text, final_url)
            if not extracted.next_page_url:
                raise AnalysisError("no_media", "没有下一页")
            current = extracted.next_page_url
        text, final_url = await self._fetch_page(outbound, current)
        extracted = extract_from_html(text, final_url)
        items = [
            AnalyzedItem(
                kind="image",
                display_name=_display_image(width, height),
                ext=url_ext(image_url) or "jpg",
                source_url=image_url,
                protocol="http",
                width=width,
                height=height,
            )
            for image_url, width, height in extracted.images
        ]
        return items, extracted.title, extracted.next_page_url


# ────────────────────────── 内部收尾工具 ──────────────────────────


def _as_analysis_error(exc: BaseException) -> AnalysisError:
    """把未分类异常收敛成稳定错误码。"""
    if isinstance(exc, AnalysisError):
        return exc
    if isinstance(exc, TimeoutError):
        return AnalysisError("net_timeout")
    if isinstance(exc, OutboundStatusError):
        return _status_error(exc)
    if isinstance(exc, OutboundError):
        return AnalysisError(_outbound_error_code(exc))
    return AnalysisError("parser_outdated")


def _status_error(exc: OutboundStatusError) -> AnalysisError:
    """HTTP 状态码 → 业务错误码。"""
    if exc.status_code == 401:
        return AnalysisError("login_required")
    if exc.status_code == 403:
        return AnalysisError("referer_denied")
    if exc.status_code in (404, 410):
        return AnalysisError("no_media")
    return AnalysisError("unsupported_site")


def _outbound_error_code(exc: OutboundError) -> str:
    """出站错误码 → 分析错误码。"""
    if exc.reason == "ssrf_blocked":
        return "ssrf_blocked"
    if exc.reason in ("net_timeout", "network_error"):
        return "net_timeout"
    if exc.reason == "too_large":
        return "unsupported_site"
    return "unsupported_site"


def _classify_ytdlp_error(message: str) -> str:
    """把 yt-dlp 的英文错误粗略映射到稳定的业务错误码。"""
    lower = (message or "").lower()
    if "drm" in lower or "protected" in lower:
        return "drm_protected"
    if any(
        marker in lower
        for marker in ("login", "sign in", "private", "authentication", "cookie")
    ):
        return "login_required"
    if "timed out" in lower or "timeout" in lower:
        return "net_timeout"
    return "unsupported_site"


def _pick_failure(*errors: AnalysisError | None) -> AnalysisError:
    """在多个失败里挑一个最该展示给用户的（DRM / 登录优先）。"""
    ordered = [error for error in errors if error is not None]
    for code in ("drm_protected", "login_required", "referer_denied"):
        for error in ordered:
            if error.code == code:
                return error
    for code in ("deep_analyze_disabled", "net_timeout", "no_media"):
        for error in ordered:
            if error.code == code:
                return error
    return ordered[0] if ordered else AnalysisError("no_media")


def _merge_items(
    first: list[AnalyzedItem], second: list[AnalyzedItem]
) -> list[AnalyzedItem]:
    """合并 L1/L2/L3 结果并去重（L1 元信息优先）。"""
    seen: set[tuple] = set()
    result: list[AnalyzedItem] = []
    for item in list(first) + list(second):
        key = (item.kind, item.source_url.split("#", 1)[0], item.aux_url, item.height)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _finalize(
    items: list[AnalyzedItem], max_items: int, max_images: int
) -> tuple[list[AnalyzedItem], bool]:
    """
    截断 + 排序。

    口径（与需求 AC-209 对齐）：`max_items` 约束**非图片**可下载项（视频/音频/字幕），
    `max_images` 单独约束图片张数；两者相加才是一次清单的总条数。
    否则「图片最大 200 张」会被「总项数 50」永远压成 50，两个上限互相打架。
    """
    images = [item for item in items if item.kind == "image"]
    others = [item for item in items if item.kind != "image"]
    truncated = len(images) > max_images or len(others) > max_items
    ordered = sorted(
        others,
        key=lambda item: (
            0 if item.kind == "video" else 1,
            -(item.height or 0),
            -(item.tbr_kbps or 0),
        ),
    )[:max_items] + images[:max_images]
    return ordered, truncated


def _copy_result(result: AnalysisResult) -> AnalysisResult:
    """深拷贝分析结果（缓存复用/返回给调用方时避免共享可变对象）。"""
    return AnalysisResult(
        page_title=result.page_title,
        items=[replace(item) for item in result.items],
        level=result.level,
        truncated=result.truncated,
        has_more_images=result.has_more_images,
        next_page_url=result.next_page_url,
        warnings=list(result.warnings),
    )

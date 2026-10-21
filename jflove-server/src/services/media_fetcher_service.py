"""
媒体下载与落盘服务（v1.8.0 新增 / S4）

设计依据：`文档记录/设计文档/v1.8.0.md` §2.4。

职责：
  - 直链 HTTP（Range 续传）、HLS（播放列表本地化 + AES-128 key 落盘 + 分片下载）、
    DASH（init + 分片拼接）、图片（含子目录 / zip）；
  - 先用 `ffmpeg -c copy` 做**无损**合并/重封装，绝不重编码；
  - 所有产物先写 `<目标目录>/.jflove-download/<task_id>/`，成功后 `os.replace` 原子改名；
  - 同名冲突：内容相同则复用；不同则追加 8 位哈希后缀，不覆盖原文件。

出站收敛（设计 §1.4）：
  - 网络请求只能经 `outbound_http`；
  - `ffmpeg` / `ffprobe` 只喂**本地文件**，并强制 `-protocol_whitelist file,crypto`；
  - HLS 的 AES key 由我们下载到本地，再让 ffmpeg 用本地 key（`-allowed_extensions ALL`）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from src.config.settings import (
    OFFLINE_DEFAULT_MAX_FILE_SIZE_MB,
    OFFLINE_MIN_IMAGE_SIDE,
    OFFLINE_DIR_NAME,
    OFFLINE_SEGMENT_CONCURRENCY,
)
from src.utils import media_probe
from src.utils.image_probe import is_too_small, probe_image_file
from src.utils.logger import get_logger
from src.utils.outbound_http import (
    OutboundClient,
    OutboundError,
    OutboundStatusError,
    safe_url_label,
)

logger = get_logger(__name__)

#: 合并/重封装超时（秒）
_FFMPEG_TIMEOUT = 600.0
#: 分片/图片写盘块大小
_CHUNK = 256 * 1024
#: 单张图片上限（分析/下载统一口径）
_IMAGE_MAX_BYTES = 20 * 1024 * 1024
#: 可重试的瞬态错误（CDN 节点重置 / 读超时；4xx 不重试）
_RETRY_CODES = frozenset({"net_timeout", "network_error"})
#: 单个请求/分片最大尝试次数（含首试）
_MAX_ATTEMPTS = 3
#: 重试退避基数（秒）；按尝试次数线性递增
_RETRY_BASE_DELAY = 0.5
#: 能可靠识别文件头的图片格式（AVIF 等未覆盖格式不做内容校验）
_PROBEABLE_IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp"})


class FetchError(Exception):
    """
    下载失败（稳定错误码，供 S5 映射中文并落库）。

    错误码：`url_expired` / `referer_denied` / `net_timeout` / `too_large`
    / `disk_full` / `target_not_writable` / `drm_protected` / `merge_failed`
    / `canceled` / `http_error` / `network_error`
    """

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


@dataclass
class FetchOutcome:
    """单个产物的下载结果。"""

    output_name: str
    output_path: str
    bytes_written: int
    status: str = "success"
    error_code: str = ""
    error_message: str = ""
    item_id: int = 0


@dataclass
class _HlsPlaylist:
    variants: list[dict]
    segments: list[dict]
    keys: list[dict]
    init_uri: str
    text: str


def _safe_title(value: str, limit: int = 80) -> str:
    """清洗产物标题（去文件系统非法字符；空则用 offline）。"""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return text[:limit] or "offline"


def _sha256(path: str) -> str:
    """流式计算文件 SHA256（同名冲突判重用）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ext_from_url(url: str, default: str) -> str:
    """从 URL 路径取扩展名（保留默认值）。"""
    suffix = os.path.splitext(urlsplit(url).path)[1].lstrip(".").lower()
    return suffix or default


def _file_suffix(url: str, default: str) -> str:
    """取扩展名（jpeg→jpg；无则 default）。"""
    ext = _ext_from_url(url, default)
    return "jpg" if ext == "jpeg" else ext


def _ffprobe_exe() -> str | None:
    """系统 ffprobe（镜像内已装；开发机没有就跳过校验）。"""
    return shutil.which("ffprobe")


async def _run_ffmpeg(args: list[str]) -> None:
    """
    执行 ffmpeg（参数列表，无 shell）；失败只带错误码，不把路径/命令回显给界面。

    :raises FetchError: 可执行文件缺失 / 超时 / 退出码非 0
    """
    exe = media_probe.get_ffmpeg_exe()
    if not exe:
        raise FetchError("merge_failed", "ffmpeg 不可用")
    command = [exe, "-hide_banner", "-loglevel", "error", *args]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_FFMPEG_TIMEOUT
        )
    except (asyncio.TimeoutError, OSError) as exc:
        raise FetchError("merge_failed", type(exc).__name__) from exc
    if process.returncode != 0:
        logger.error("ffmpeg 合并失败，退出码 %s", process.returncode)
        raise FetchError("merge_failed", (stderr or b"").decode("utf-8", "replace")[-400:])


async def _probe_output(path: str) -> dict:
    """用 ffprobe 读本地产物（时长 / 流类型）；ffprobe 缺失时返回空结果。"""
    exe = _ffprobe_exe()
    if not exe:
        return {}
    command = [
        exe,
        "-v",
        "error",
        "-protocol_whitelist",
        "file,crypto",
        "-show_entries",
        "format=duration:stream=codec_type",
        "-of",
        "default=noprint_wrappers=1",
        path,
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
    except (asyncio.TimeoutError, OSError):
        return {}
    text = stdout.decode("utf-8", "replace")
    duration = 0.0
    streams: list[str] = []
    for line in text.splitlines():
        if line.startswith("codec_type="):
            streams.append(line.split("=", 1)[1].strip())
        elif line.startswith("duration="):
            try:
                duration = float(line.split("=", 1)[1])
            except ValueError:
                duration = 0.0
    return {"duration": duration, "streams": streams}


def parse_hls_playlist(text: str, base_url: str) -> _HlsPlaylist:
    """解析 HLS 主/媒体列表（段、KEY、MAP、变体）。"""
    lines = [line.strip() for line in (text or "").splitlines()]
    variants: list[dict] = []
    segments: list[dict] = []
    keys: list[dict] = []
    init_uri = ""
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = dict(re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line.split(":", 1)[1]))
            uri = ""
            for follow in lines[index + 1:]:
                if follow and not follow.startswith("#"):
                    uri = follow
                    break
            if uri:
                variants.append(
                    {
                        "url": urljoin(base_url, uri),
                        "bandwidth": int((attrs.get("BANDWIDTH", "0") or "0").strip('"') or 0),
                    }
                )
        elif line.startswith("#EXT-X-KEY:"):
            attrs = dict(re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line.split(":", 1)[1]))
            method = attrs.get("METHOD", "").strip('"').upper()
            uri = attrs.get("URI", "").strip('"')
            keyformat = attrs.get("KEYFORMAT", "").strip('"')
            if method not in ("", "NONE"):
                keys.append(
                    {
                        "method": method,
                        "uri": urljoin(base_url, uri) if uri else "",
                        "iv": attrs.get("IV", "").strip('"'),
                        "keyformat": keyformat,
                    }
                )
        elif line.startswith("#EXT-X-MAP:"):
            attrs = dict(re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line.split(":", 1)[1]))
            uri = attrs.get("URI", "").strip('"')
            if uri:
                init_uri = urljoin(base_url, uri)
        elif line and not line.startswith("#"):
            segments.append({"url": urljoin(base_url, line)})
    return _HlsPlaylist(
        variants=variants,
        segments=segments,
        keys=keys,
        init_uri=init_uri,
        text=text or "",
    )


def parse_mpd_reps(text: str, mpd_url: str) -> dict[str, dict]:
    """
    解析 DASH MPD 的 Representation 及其分片地址。

    支持：`SegmentTemplate`（`$Number` / `$Time` / `$RepresentationID$` / `$Bandwidth$`）、
    `SegmentList`、`SegmentBase`（单文件）。

    :returns: `{rep_id: {kind, init_url, segments, single_url, bandwidth, codecs}}`
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(text)

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def attr(element, name: str, default: str = "") -> str:
        return element.get(name, default) if element is not None else default

    def template_info(element, base: str) -> dict:
        info: dict = {"media": "", "init": "", "start": 1, "timescale": 1, "duration": 0}
        if element is None:
            return info
        info["media"] = attr(element, "media")
        info["init"] = attr(element, "initialization")
        info["start"] = int(attr(element, "startNumber", "1") or 1)
        info["timescale"] = int(attr(element, "timescale", "1") or 1)
        info["duration"] = int(attr(element, "duration", "0") or 0)
        base_url = attr(element, "BaseURL")
        if base_url:
            info["base"] = urljoin(base, base_url)
        return info

    period = next((el for el in root.iter() if local(el.tag) == "Period"), None)
    mpd_duration = attr(root, "mediaPresentationDuration")
    # 简化时长解析：PT#H#M#S
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(\d+(?:\.\d+)?)S$", mpd_duration or "")
    total_seconds = 0.0
    if match:
        total_seconds = (
            int(match.group(1) or 0) * 3600
            + int(match.group(2) or 0) * 60
            + float(match.group(3) or 0)
        )

    reps: dict[str, dict] = {}
    for adaptation in (period if period is not None else root).iter():
        if local(adaptation.tag) != "AdaptationSet":
            continue
        content_type = attr(adaptation, "contentType").lower()
        mime_type = attr(adaptation, "mimeType").lower()
        kind = (
            "audio"
            if content_type == "audio" or mime_type.startswith("audio")
            else "video"
        )
        aset_template = next(
            (el for el in adaptation if local(el.tag) == "SegmentTemplate"), None
        )
        aset_list = next((el for el in adaptation if local(el.tag) == "SegmentList"), None)
        for representation in adaptation:
            if local(representation.tag) != "Representation":
                continue
            rep_id = attr(representation, "id")
            if not rep_id:
                continue
            rep_base = attr(representation, "BaseURL")
            base_url = urljoin(mpd_url, rep_base) if rep_base else mpd_url
            rep_template = next(
                (el for el in representation if local(el.tag) == "SegmentTemplate"), None
            )
            template_el = rep_template if rep_template is not None else aset_template
            template = template_info(template_el, base_url)
            segments: list[str] = []
            init_url = ""
            single_url = ""
            rep_list = next(
                (el for el in representation if local(el.tag) == "SegmentList"), None
            )
            list_el = rep_list if rep_list is not None else aset_list
            if list_el is not None:
                init_el = next(
                    (el for el in list_el if local(el.tag) == "Initialization"), None
                )
                if init_el is not None and attr(init_el, "sourceURL"):
                    init_url = urljoin(base_url, attr(init_el, "sourceURL"))
                for segment in list_el:
                    if local(segment.tag) == "SegmentURL":
                        media = attr(segment, "media")
                        if media:
                            segments.append(urljoin(base_url, media))
            elif template["media"]:
                media_template = template["media"]
                init_template = template["init"]
                if init_template:
                    init_url = urljoin(
                        template.get("base", base_url),
                        init_template.replace("$RepresentationID$", rep_id).replace(
                            "$Bandwidth$", attr(representation, "bandwidth", "0")
                        ),
                    )
                timeline = next(
                    (el for el in template_el if local(el.tag) == "SegmentTimeline"),
                    None,
                ) if template_el is not None else None
                if timeline is not None:
                    times: list[int] = []
                    current = 0
                    for entry in timeline:
                        if local(entry.tag) != "S":
                            continue
                        current = int(attr(entry, "t", str(current)) or current)
                        repeat = int(attr(entry, "r", "0") or 0)
                        duration = int(attr(entry, "d", "0") or 0)
                        for _ in range(repeat + 1):
                            times.append(current)
                            current += duration
                    for time_value in times:
                        segments.append(
                            urljoin(
                                template.get("base", base_url),
                                media_template.replace("$RepresentationID$", rep_id)
                                .replace("$Bandwidth$", attr(representation, "bandwidth", "0"))
                                .replace("$Time$", str(time_value)),
                            )
                        )
                else:
                    duration = template["duration"]
                    count = 1
                    if duration > 0 and total_seconds > 0:
                        count = max(1, int(total_seconds * template["timescale"] / duration))
                    for number in range(template["start"], template["start"] + count):
                        media = re.sub(
                            r"\$Number(?:%0(\d+)d)?\$",
                            lambda m, n=number: str(n).zfill(int(m.group(1) or 0)),
                            media_template,
                        )
                        media = media.replace("$RepresentationID$", rep_id).replace(
                            "$Bandwidth$", attr(representation, "bandwidth", "0")
                        )
                        segments.append(urljoin(template.get("base", base_url), media))
            else:
                single_url = base_url
            reps[rep_id] = {
                "kind": kind,
                "init_url": init_url,
                "segments": segments,
                "single_url": single_url,
                "bandwidth": int(attr(representation, "bandwidth", "0") or 0),
                "codecs": attr(representation, "codecs"),
            }
    return reps


class MediaFetcherService:
    """
    媒体下载器。

    :param outbound: 出站客户端（生产为 None，由调用方传入共享实例）
    :param max_file_bytes: 单文件上限（S5 传入管理员配置）
    """

    def __init__(self, outbound: OutboundClient, *, max_file_bytes: int | None = None) -> None:
        self._outbound = outbound
        self._max_file_bytes = max_file_bytes or OFFLINE_DEFAULT_MAX_FILE_SIZE_MB * 1024 * 1024

    # ── 对外入口 ────────────────────────────────────────────────

    async def fetch_item(
        self,
        target_dir: str,
        task_id: int,
        item: dict,
        *,
        title: str = "",
        referer: str = "",
        progress=None,
        cancel_event=None,
    ) -> FetchOutcome:
        """下载一条可下载项并原子落盘。"""
        tmp_root = os.path.join(target_dir, OFFLINE_DIR_NAME, str(task_id))
        os.makedirs(tmp_root, exist_ok=True)
        kind = str(item.get("kind") or "video")
        if kind == "image":
            outcomes = await self.fetch_images(
                target_dir,
                task_id,
                [item],
                title=title,
                referer=referer,
                progress=progress,
                cancel_event=cancel_event,
            )
            if not outcomes:
                raise FetchError("no_media", "图片尺寸过小，已跳过")
            outcome = outcomes[0]
            if outcome.status == "failed":
                raise FetchError(outcome.error_code or "network_error", outcome.error_message)
            return outcome
        name = self._final_name(title, item)
        temp_path = os.path.join(tmp_root, f"item_{item.get('id', 0)}.part")
        try:
            if str(item.get("protocol")) == "m3u8":
                produced = await self._fetch_hls(
                    item, tmp_root, referer=referer, progress=progress, cancel_event=cancel_event
                )
            elif str(item.get("protocol")) == "mpd_segments":
                produced = await self._fetch_dash(
                    item, tmp_root, referer=referer, progress=progress, cancel_event=cancel_event
                )
            else:
                produced = await self._fetch_direct(
                    item, temp_path, referer=referer, progress=progress, cancel_event=cancel_event
                )
            return self._finalize(target_dir, produced, name)
        except FetchError:
            raise
        except OutboundStatusError as exc:
            raise FetchError(_status_code(exc.status_code), f"HTTP {exc.status_code}") from exc
        except OutboundError as exc:
            raise FetchError(_outbound_code(exc), exc.reason) from exc

    async def fetch_images(
        self,
        target_dir: str,
        task_id: int,
        images: list[dict],
        *,
        title: str = "",
        referer: str = "",
        pack_zip: bool = False,
        progress=None,
        cancel_event=None,
    ) -> list[FetchOutcome]:
        """下载一批图片：单张直接落盘；多张落同名子目录；可选 zip。"""
        safe_title = _safe_title(title)
        subdir = safe_title if len(images) > 1 else ""
        final_dir = os.path.join(target_dir, subdir) if subdir else target_dir
        tmp_root = os.path.join(target_dir, OFFLINE_DIR_NAME, str(task_id))
        os.makedirs(final_dir, exist_ok=True)
        os.makedirs(tmp_root, exist_ok=True)
        outcomes: list[FetchOutcome] = []
        for index, image in enumerate(images, start=1):
            if cancel_event is not None and cancel_event.is_set():
                raise FetchError("canceled")
            item_id = int(image.get("id", 0) or 0)
            ext = str(image.get("ext") or "jpg").lstrip(".") or "jpg"
            temp_path = os.path.join(tmp_root, f"image_{index:03d}.part")
            outcome = FetchOutcome(
                output_name="",
                output_path="",
                bytes_written=0,
                item_id=item_id,
            )
            try:
                await self._fetch_direct(
                    image,
                    temp_path,
                    referer=referer,
                    max_bytes=_IMAGE_MAX_BYTES,
                    progress=progress,
                    cancel_event=cancel_event,
                )
                meta = probe_image_file(temp_path)
                if meta is None and ext.lower() in _PROBEABLE_IMAGE_EXTS:
                    # 站点把图片加密后交给前端 JS 解密时，服务端拿到的是密文。
                    # 直接落盘会产出打不开的“图片”，这里明确失败而不是静默保存。
                    raise FetchError("content_invalid", "图片内容无法识别（可能经过站点加密）")
                if is_too_small(meta, OFFLINE_MIN_IMAGE_SIDE):
                    os.remove(temp_path)
                    outcome.status = "skipped"
                    outcomes.append(outcome)
                    continue
                final_name = (
                    f"{index:03d}.{ext}" if subdir else f"{safe_title}_img_{index:03d}.{ext}"
                )
                outcome = self._finalize(final_dir, temp_path, final_name)
                outcome.item_id = item_id
            except FetchError as exc:
                # 单张失败**不中断整批**：已成功的图片保留，失败项逐条落库，
                # 由调用方决定任务终态（避免一张坏图导致其余图片全部丢失）。
                try:
                    if os.path.isfile(temp_path):
                        os.remove(temp_path)
                except OSError:
                    pass
                outcome.status = "failed"
                outcome.error_code = exc.code
                outcome.error_message = exc.detail
            outcomes.append(outcome)
        successful = [outcome for outcome in outcomes if outcome.status == "success"]
        if pack_zip and len(successful) > 1:
            zip_path = os.path.join(tmp_root, f"{safe_title}.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for outcome in successful:
                    archive.write(
                        outcome.output_path,
                        arcname=os.path.basename(outcome.output_path),
                    )
            outcomes.append(self._finalize(target_dir, zip_path, f"{safe_title}.zip"))
        return outcomes

    # ── 各协议实现 ──────────────────────────────────────────────

    async def _fetch_direct(
        self,
        item: dict,
        temp_path: str,
        *,
        referer: str = "",
        max_bytes: int | None = None,
        progress=None,
        cancel_event=None,
    ) -> str:
        """直链下载（Range 续传写 `.part`）。"""
        limit = max_bytes or self._max_file_bytes
        last_error: FetchError | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                await self._outbound.stream_to_file(
                    str(item["source_url"]),
                    temp_path,
                    max_bytes=limit,
                    referer=referer,
                    resume=True,
                    progress=progress,
                    cancel_event=cancel_event,
                )
                return temp_path
            except OutboundStatusError as exc:
                raise FetchError(_status_code(exc.status_code), f"HTTP {exc.status_code}") from exc
            except OutboundError as exc:
                error = FetchError(_outbound_code(exc), exc.reason)
                last_error = error
                if error.code not in _RETRY_CODES or attempt >= _MAX_ATTEMPTS:
                    raise error from exc
                await asyncio.sleep(_RETRY_BASE_DELAY * attempt)
        assert last_error is not None
        raise last_error

    async def _fetch_hls(
        self,
        item: dict,
        tmp_root: str,
        *,
        referer: str = "",
        progress=None,
        cancel_event=None,
    ) -> str:
        """
        HLS：播放列表本地化 + key 落盘 + 分片下载 + ffmpeg 无损合并。

        :returns: 合并后的本地 mp4 路径
        """
        url = str(item["source_url"])
        playlist_text = await self._get_text(url, referer)
        playlist = parse_hls_playlist(playlist_text, url)
        if playlist.variants:
            # 选中清单里已带清晰度；若拿到的仍是主列表，取最高码率变体
            best = max(playlist.variants, key=lambda variant: variant["bandwidth"])
            return await self._fetch_hls(
                {**item, "source_url": best["url"]},
                tmp_root,
                referer=referer,
                progress=progress,
                cancel_event=cancel_event,
            )
        if not playlist.segments:
            raise FetchError("merge_failed", "播放列表没有分片")
        drm = _hls_drm(playlist)
        if drm:
            raise FetchError("drm_protected", drm)

        for key_index, key in enumerate(playlist.keys):
            if key["method"] != "AES-128" or key.get("keyformat"):
                raise FetchError("drm_protected", key["method"])
            if key["uri"]:
                key_bytes = await self._get_bytes(key["uri"], referer, 64 * 1024)
                key_name = f"key_{key_index}.bin"
                with open(os.path.join(tmp_root, key_name), "wb") as handle:
                    handle.write(key_bytes)
                key["local"] = key_name

        init_local = ""
        if playlist.init_uri:
            init_local = "init.mp4"
            await self._download_to(playlist.init_uri, os.path.join(tmp_root, init_local), referer)
            if progress:
                progress(os.path.getsize(os.path.join(tmp_root, init_local)), None)

        local_names: list[str] = []
        semaphore = asyncio.Semaphore(OFFLINE_SEGMENT_CONCURRENCY)
        downloaded = 0

        async def download_segment(index: int, segment: dict) -> str:
            nonlocal downloaded
            async with semaphore:
                if cancel_event is not None and cancel_event.is_set():
                    raise FetchError("canceled")
                name = f"seg_{index:05d}.ts"
                path = os.path.join(tmp_root, name)
                if os.path.isfile(path) and os.path.getsize(path) > 0:
                    local_names.append(name)
                    return name
                await self._download_to(segment["url"], path, referer, cancel_event=cancel_event)
                downloaded += os.path.getsize(path)
                if progress:
                    progress(downloaded, None)
                local_names.append(name)
                return name

        segment_tasks = [
            download_segment(index, segment)
            for index, segment in enumerate(playlist.segments)
        ]
        if segment_tasks:
            await asyncio.gather(*segment_tasks)

        local_playlist = os.path.join(tmp_root, "local.m3u8")
        with open(local_playlist, "w", encoding="utf-8") as handle:
            handle.write(_rewrite_hls(playlist, init_local))
        output = os.path.join(tmp_root, "merged.mp4")
        await _run_ffmpeg(
            [
                "-allowed_extensions",
                "ALL",
                "-protocol_whitelist",
                "file,crypto",
                "-i",
                local_playlist,
                "-c",
                "copy",
                "-y",
                output,
            ]
        )
        return output

    async def _fetch_dash(
        self,
        item: dict,
        tmp_root: str,
        *,
        referer: str = "",
        progress=None,
        cancel_event=None,
    ) -> str:
        """DASH：按 variant_id 取视频/音频 Representation，拼 init+分片后 mux。"""
        url = str(item["source_url"])
        text = await self._get_text(url, referer)
        reps = parse_mpd_reps(text, url)
        if not reps:
            raise FetchError("merge_failed", "MPD 没有可用 Representation")
        variant = str(item.get("variant_id") or "")
        video_id, _, audio_id = variant.partition("|")
        if not video_id:
            video_id = max(
                (rep_id for rep_id, rep in reps.items() if rep["kind"] == "video"),
                default="",
            )
        if not audio_id:
            audio_id = max(
                (rep_id for rep_id, rep in reps.items() if rep["kind"] == "audio"),
                default="",
            )
        if video_id not in reps:
            raise FetchError("url_expired", "MPD 中找不到对应视频轨")
        if reps[video_id]["kind"] == "audio":
            # 「仅音频」项：variant_id 直接指向音频 Representation
            return await self._download_rep(
                reps[video_id], tmp_root, "audio", referer, progress, cancel_event
            )

        video_path = await self._download_rep(
            reps[video_id], tmp_root, "video", referer, progress, cancel_event
        )
        audio_path = ""
        if audio_id and audio_id in reps:
            audio_path = await self._download_rep(
                reps[audio_id], tmp_root, "audio", referer, progress, cancel_event
            )
        output = os.path.join(tmp_root, "merged.mp4")
        args = ["-protocol_whitelist", "file,crypto", "-i", video_path]
        if audio_path:
            args += ["-i", audio_path, "-map", "0:v:0", "-map", "1:a:0"]
        else:
            args += ["-map", "0:v:0"]
        args += ["-c", "copy", "-y", output]
        await _run_ffmpeg(args)
        probe = await _probe_output(output)
        if audio_path and "audio" not in probe.get("streams", []):
            raise FetchError("merge_failed", "合并产物缺少音轨")
        return output

    async def _download_rep(
        self,
        rep: dict,
        tmp_root: str,
        label: str,
        referer: str,
        progress,
        cancel_event,
    ) -> str:
        """下载一个 Representation（init + 分片拼接 / 单文件）。"""
        if rep["single_url"]:
            path = os.path.join(tmp_root, f"{label}.mp4")
            await self._download_to(rep["single_url"], path, referer, cancel_event=cancel_event)
            return path
        parts: list[str] = []
        if rep["init_url"]:
            init_path = os.path.join(tmp_root, f"{label}_init.mp4")
            await self._download_to(rep["init_url"], init_path, referer, cancel_event=cancel_event)
            parts.append(init_path)
        semaphore = asyncio.Semaphore(OFFLINE_SEGMENT_CONCURRENCY)

        async def download(index: int, segment_url: str) -> str:
            async with semaphore:
                if cancel_event is not None and cancel_event.is_set():
                    raise FetchError("canceled")
                path = os.path.join(tmp_root, f"{label}_{index:05d}.m4s")
                if not (os.path.isfile(path) and os.path.getsize(path) > 0):
                    await self._download_to(
                        segment_url, path, referer, cancel_event=cancel_event
                    )
                if progress:
                    progress(os.path.getsize(path), None)
                return path

        segment_tasks = [
            download(index, segment_url)
            for index, segment_url in enumerate(rep["segments"])
        ]
        if segment_tasks:
            parts.extend(await asyncio.gather(*segment_tasks))
        output = os.path.join(tmp_root, f"{label}.mp4")
        with open(output, "wb") as target:
            for part in parts:
                with open(part, "rb") as source:
                    shutil.copyfileobj(source, target, _CHUNK)
        return output

    # ── 通用下载/落盘工具 ──────────────────────────────────────

    async def _get_bytes(self, url: str, referer: str, limit: int) -> bytes:
        """下载小文件（播放列表 / key）；瞬态网络错误按次数重试。"""
        last_error: FetchError | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await self._outbound.get_bytes(
                    url, max_bytes=limit, referer=referer
                )
                return response.content
            except OutboundStatusError as exc:
                # 4xx/5xx 是确定结果，重试无意义
                raise FetchError(_status_code(exc.status_code), f"HTTP {exc.status_code}") from exc
            except OutboundError as exc:
                error = FetchError(_outbound_code(exc), exc.reason)
                last_error = error
                if error.code not in _RETRY_CODES or attempt >= _MAX_ATTEMPTS:
                    raise error from exc
                await asyncio.sleep(_RETRY_BASE_DELAY * attempt)
        assert last_error is not None
        raise last_error

    async def _get_text(self, url: str, referer: str) -> str:
        """下载播放列表文本。"""
        data = await self._get_bytes(url, referer, 2 * 1024 * 1024)
        return data.decode("utf-8", "replace")

    async def _download_to(
        self,
        url: str,
        path: str,
        referer: str,
        *,
        cancel_event=None,
    ) -> None:
        """流式下载到指定路径（已存在同尺寸则跳过；瞬态网络错误重试）。

        CDN 分片节点偶发连接重置很常见；HLS 一个分片失败会导致整个任务失败，
        因此这里对 `net_timeout` / `network_error` 做有限次退避重试。
        """
        last_error: FetchError | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                await self._outbound.stream_to_file(
                    url,
                    path,
                    max_bytes=self._max_file_bytes,
                    referer=referer,
                    resume=True,
                    cancel_event=cancel_event,
                )
                return
            except OutboundStatusError as exc:
                raise FetchError(_status_code(exc.status_code), f"HTTP {exc.status_code}") from exc
            except OutboundError as exc:
                error = FetchError(_outbound_code(exc), exc.reason)
                last_error = error
                if error.code not in _RETRY_CODES or attempt >= _MAX_ATTEMPTS:
                    raise error from exc
                await asyncio.sleep(_RETRY_BASE_DELAY * attempt)
        assert last_error is not None
        raise last_error

    def _final_name(self, title: str, item: dict) -> str:
        """视频/音频产物的最终文件名：`{标题}_{清晰度或音频}.{ext}`。"""
        ext = str(item.get("ext") or "mp4").lstrip(".") or "mp4"
        if item.get("kind") == "audio":
            label = "audio"
        elif item.get("height"):
            label = f"{int(item['height'])}P"
        elif item.get("display_name"):
            label = _safe_title(str(item["display_name"]))
        else:
            label = "video"
        return f"{_safe_title(title)}_{label}.{ext}"

    def _finalize(self, target_dir: str, temp_path: str, name: str) -> FetchOutcome:
        """
        原子落盘 + 同名冲突处理。

        - 目标同名且内容相同 ⇒ 复用已有文件（`status=skipped`）；
        - 同名不同内容 ⇒ `{stem}_{sha1[:8]}.{ext}`，原文件不动。
        """
        os.makedirs(target_dir, exist_ok=True)
        final_path = os.path.join(target_dir, name)
        reused = False
        if os.path.exists(final_path):
            same = False
            try:
                same = os.path.getsize(final_path) == os.path.getsize(temp_path) and (
                    _sha256(final_path) == _sha256(temp_path)
                )
            except OSError:
                same = False
            if same:
                os.remove(temp_path)
                reused = True
            else:
                stem, suffix = os.path.splitext(name)
                digest = _sha256(temp_path)[:8]
                final_path = os.path.join(target_dir, f"{stem}_{digest}{suffix}")
        if not reused:
            os.replace(temp_path, final_path)
        return FetchOutcome(
            output_name=os.path.basename(final_path),
            output_path=final_path,
            bytes_written=os.path.getsize(final_path),
            status="skipped" if reused else "success",
        )


def _rewrite_hls(playlist: _HlsPlaylist, init_local: str) -> str:
    """把远端播放列表改写成本地路径（KEY/MAP 本地化，段名本地化）。"""
    output: list[str] = []
    key_names = [key.get("local", "") for key in playlist.keys]
    key_index = 0
    segment_index = 0
    for line in playlist.text.splitlines():
        if line.startswith("#EXT-X-KEY:"):
            local = key_names[key_index] if key_index < len(key_names) else ""
            key_index += 1
            if local:
                line = re.sub(r'URI="[^"]*"', f'URI="{local}"', line)
            output.append(line)
        elif line.startswith("#EXT-X-MAP:"):
            if init_local:
                line = re.sub(r'URI="[^"]*"', f'URI="{init_local}"', line)
            output.append(line)
        elif line.startswith("#EXT-X-BYTERANGE:"):
            # 已按 Range 下载到独立文件，继续保留会让 ffmpeg 二次偏移
            continue
        elif line and not line.startswith("#"):
            output.append(f"seg_{segment_index:05d}.ts")
            segment_index += 1
        else:
            output.append(line)
    return "\n".join(output) + "\n"


def _hls_drm(playlist: _HlsPlaylist) -> str:
    """判断 HLS 是否含不支持的 DRM（SAMPLE-AES / KEYFORMAT）。"""
    for key in playlist.keys:
        if key["method"] not in ("AES-128", "NONE"):
            return key["method"]
        if key.get("keyformat"):
            return key["keyformat"]
    return ""


def _status_code(status: int) -> str:
    """HTTP 状态码 → 下载错误码。"""
    if status in (401, 403, 404, 410):
        return "url_expired"
    if status == 429:
        return "referer_denied"
    return "http_error"


def _outbound_code(exc: OutboundError) -> str:
    """出站错误码 → 下载错误码。"""
    if exc.reason == "net_timeout":
        return "net_timeout"
    if exc.reason == "too_large":
        return "too_large"
    if exc.reason == "canceled":
        return "canceled"
    if exc.reason == "ssrf_blocked":
        return "url_expired"
    return "network_error"


def cleanup_task_temp(target_dir: str, task_id: int) -> None:
    """清理某任务在目标目录下的隐藏临时目录（成功/取消/失败收尾用）。"""
    path = os.path.join(target_dir, OFFLINE_DIR_NAME, str(task_id))
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def sanitize_log_label(url: str) -> str:
    """日志标签（复用 outbound_http 的口径，避免出现完整 URL）。"""
    return safe_url_label(url)

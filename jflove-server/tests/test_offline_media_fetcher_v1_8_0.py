"""
媒体下载器单测（v1.8.0 / S4）

覆盖 `src/services/media_fetcher_service.py`：
  - HLS 播放列表解析 / 本地化、DASH MPD 分片解析；
  - 直链下载、同名冲突（同内容复用 / 不同内容哈希后缀）、图片子目录 + zip；
  - HLS / DASH 用**真实 ffmpeg 夹具**合并，ffprobe 断言产物含音轨（AC-215）；
  - 403 映射、取消。

夹具用 ffmpeg 本地生成（无外网）；生成器缺失时 `pytest.skip` 并打印原因。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import struct
import subprocess

import pytest

from src.services.media_fetcher_service import (
    FetchError,
    MediaFetcherService,
    parse_hls_playlist,
    parse_mpd_reps,
)
from src.utils.outbound_http import (
    DownloadResult,
    OutboundError,
    OutboundResponse,
    OutboundStatusError,
)


class FakeOutbound:
    """假出站客户端：按 URL 返回预置字节，`stream_to_file` 直接写盘。"""

    def __init__(self, files: dict[str, bytes], status: int = 200) -> None:
        self.files = files
        self.status = status
        self.requests: list[str] = []

    async def get_bytes(self, url: str, *, max_bytes: int, referer=None, headers=None):
        self.requests.append(url)
        if self.status >= 400:
            raise OutboundStatusError(self.status, {}, b"")
        if url not in self.files:
            raise OutboundStatusError(404, {}, b"")
        return OutboundResponse(
            url=url, status_code=200, headers={}, content=self.files[url]
        )

    async def stream_to_file(
        self,
        url: str,
        dest: str,
        *,
        max_bytes: int,
        referer=None,
        headers=None,
        resume: bool = False,
        progress=None,
        cancel_event=None,
    ) -> DownloadResult:
        self.requests.append(url)
        if cancel_event is not None and cancel_event.is_set():
            raise OutboundError("canceled")
        if self.status >= 400:
            raise OutboundStatusError(self.status, {}, b"")
        if url not in self.files:
            raise OutboundStatusError(404, {}, b"")
        data = self.files[url]
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as handle:
            handle.write(data)
        if progress:
            progress(len(data), len(data))
        return DownloadResult(
            url=url,
            status_code=200,
            headers={},
            bytes_written=len(data),
            total_bytes=len(data),
            resumed_from=0,
        )


def png_bytes(width: int, height: int) -> bytes:
    """构造只含合法 PNG 头的字节（image_probe 只读头，不解码）。"""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">I", 13) + b"IHDR" + struct.pack(
        ">IIBBBBB", width, height, 8, 2, 0, 0, 0
    )
    return signature + ihdr + b"\x00\x00\x00\x00"


def _ffmpeg_available() -> bool:
    """ffmpeg + libopenh264 是否可用（夹具编码器见设计 S-14）。"""
    if not shutil.which("ffmpeg"):
        return False
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "libopenh264" in (result.stdout or "")


pytestmark = pytest.mark.skipif(
    not _ffmpeg_available(), reason="需要 ffmpeg + libopenh264 生成测试夹具"
)


def make_hls_fixture(directory) -> tuple[str, dict[str, bytes]]:
    """用 ffmpeg 生成 3 秒 HLS（音视频齐全），返回播放列表名 + 文件字典。"""
    playlist = directory / "out.m3u8"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440",
        "-t",
        "3",
        "-c:v",
        "libopenh264",
        "-b:v",
        "300k",
        "-c:a",
        "aac",
        "-f",
        "hls",
        "-hls_time",
        "1",
        "-hls_list_size",
        "0",
        "-hls_segment_filename",
        str(directory / "seg%03d.ts"),
        str(playlist),
    ]
    subprocess.run(command, check=True, capture_output=True)
    files = {"https://cdn.example.com/out.m3u8": playlist.read_bytes()}
    for path in directory.iterdir():
        if path.name != "out.m3u8":
            files[f"https://cdn.example.com/{path.name}"] = path.read_bytes()
    return playlist.name, files


def make_dash_fixture(directory) -> tuple[str, dict[str, bytes]]:
    """用 ffmpeg 生成 3 秒 DASH（音视频分离），返回 MPD 名 + 文件字典。"""
    manifest = directory / "manifest.mpd"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440",
        "-t",
        "3",
        "-c:v",
        "libopenh264",
        "-b:v",
        "300k",
        "-g",
        "10",
        "-keyint_min",
        "10",
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-f",
        "dash",
        "-seg_duration",
        "1",
        "-use_template",
        "1",
        "-use_timeline",
        "0",
        str(manifest),
    ]
    subprocess.run(command, check=True, capture_output=True)
    files = {"https://cdn.example.com/manifest.mpd": manifest.read_bytes()}
    for path in directory.iterdir():
        if path.name != "manifest.mpd":
            files[f"https://cdn.example.com/{path.name}"] = path.read_bytes()
    return manifest.name, files


# ────────────────────────── 解析 ──────────────────────────


def test_parse_hls_playlist_extracts_segments_key_and_variants() -> None:
    """HLS 解析：变体、分片、AES key、EXT-X-MAP。"""
    text = (
        "#EXTM3U\n"
        "#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\",IV=0x1\n"
        "#EXT-X-MAP:URI=\"init.mp4\"\n"
        "#EXTINF:1.0,\nseg0.ts\n"
        "#EXTINF:2.0,\nseg1.ts\n"
    )
    playlist = parse_hls_playlist(text, "https://cdn.example.com/a/out.m3u8")
    assert len(playlist.segments) == 2
    assert playlist.segments[0]["url"] == "https://cdn.example.com/a/seg0.ts"
    assert playlist.keys[0]["method"] == "AES-128"
    assert playlist.keys[0]["uri"] == "https://cdn.example.com/a/key.bin"
    assert playlist.init_uri == "https://cdn.example.com/a/init.mp4"


def test_parse_mpd_reps_number_template() -> None:
    """DASH SegmentTemplate：`$Number%05d$` / `$RepresentationID$` 展开。"""
    mpd = """<?xml version="1.0"?>
<MPD mediaPresentationDuration="PT3.00S">
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <SegmentTemplate initialization="init-$RepresentationID$.m4s"
        media="chunk-$RepresentationID$-$Number%05d$.m4s"
        startNumber="1" timescale="1000" duration="1000"/>
      <Representation id="0" bandwidth="800000" codecs="avc1.42c00c"/>
    </AdaptationSet>
    <AdaptationSet mimeType="audio/mp4">
      <SegmentTemplate initialization="init-$RepresentationID$.m4s"
        media="chunk-$RepresentationID$-$Number%05d$.m4s"
        startNumber="1" timescale="1000" duration="1000"/>
      <Representation id="1" bandwidth="128000" codecs="mp4a.40.2"/>
    </AdaptationSet>
  </Period>
</MPD>"""
    reps = parse_mpd_reps(mpd, "https://cdn.example.com/manifest.mpd")
    assert reps["0"]["kind"] == "video"
    assert reps["1"]["kind"] == "audio"
    assert reps["0"]["init_url"] == "https://cdn.example.com/init-0.m4s"
    assert reps["0"]["segments"] == [
        "https://cdn.example.com/chunk-0-00001.m4s",
        "https://cdn.example.com/chunk-0-00002.m4s",
        "https://cdn.example.com/chunk-0-00003.m4s",
    ]


# ────────────────────────── 直链 / 冲突 ──────────────────────────


def test_direct_download_and_same_name_reuse(tmp_path) -> None:
    """直链落盘；同名同内容复用（skipped），不重写。"""
    outbound = FakeOutbound({"https://cdn.example.com/a.mp4": b"hello"})
    service = MediaFetcherService(outbound)
    outcome = asyncio.run(
        service.fetch_item(
            str(tmp_path),
            1,
            {
                "id": 1,
                "kind": "video",
                "ext": "mp4",
                "height": 240,
                "source_url": "https://cdn.example.com/a.mp4",
                "protocol": "http",
            },
            title="Demo",
        )
    )
    assert outcome.output_name == "Demo_240P.mp4"
    assert (tmp_path / "Demo_240P.mp4").read_bytes() == b"hello"
    again = asyncio.run(
        service.fetch_item(
            str(tmp_path),
            1,
            {
                "id": 1,
                "kind": "video",
                "ext": "mp4",
                "height": 240,
                "source_url": "https://cdn.example.com/a.mp4",
                "protocol": "http",
            },
            title="Demo",
        )
    )
    assert again.status == "skipped"
    assert again.output_name == "Demo_240P.mp4"


def test_same_name_different_content_gets_hash_suffix(tmp_path) -> None:
    """同名不同内容：追加 8 位哈希，原文件不被修改（AC-218）。"""
    original = tmp_path / "Demo_240P.mp4"
    original.write_bytes(b"old")
    digest = hashlib.sha256(b"new").hexdigest()[:8]
    outbound = FakeOutbound({"https://cdn.example.com/a.mp4": b"new"})
    outcome = asyncio.run(
        MediaFetcherService(outbound).fetch_item(
            str(tmp_path),
            1,
            {
                "id": 1,
                "kind": "video",
                "ext": "mp4",
                "height": 240,
                "source_url": "https://cdn.example.com/a.mp4",
                "protocol": "http",
            },
            title="Demo",
        )
    )
    assert outcome.output_name == f"Demo_240P_{digest}.mp4"
    assert original.read_bytes() == b"old"


def test_http_403_maps_to_url_expired(tmp_path) -> None:
    """签名过期 / 防盗链 403 → `url_expired`（S5 据此决定是否自动重分析）。"""
    outbound = FakeOutbound({}, status=403)
    with pytest.raises(FetchError) as excinfo:
        asyncio.run(
            MediaFetcherService(outbound).fetch_item(
                str(tmp_path),
                1,
                {
                    "id": 1,
                    "kind": "video",
                    "ext": "mp4",
                    "source_url": "https://cdn.example.com/a.mp4",
                    "protocol": "http",
                },
                title="Demo",
            )
        )
    assert excinfo.value.code == "url_expired"


def test_cancel_before_download(tmp_path) -> None:
    """取消事件已置位 ⇒ 抛 `canceled`，不落正式文件。"""
    cancel = asyncio.Event()
    cancel.set()
    outbound = FakeOutbound({"https://cdn.example.com/a.mp4": b"hello"})
    with pytest.raises(FetchError) as excinfo:
        asyncio.run(
            MediaFetcherService(outbound).fetch_item(
                str(tmp_path),
                1,
                {
                    "id": 1,
                    "kind": "video",
                    "ext": "mp4",
                    "height": 240,
                    "source_url": "https://cdn.example.com/a.mp4",
                    "protocol": "http",
                },
                title="Demo",
                cancel_event=cancel,
            )
        )
    assert excinfo.value.code == "canceled"
    assert not (tmp_path / "Demo_240P.mp4").exists()


# ────────────────────────── 图片 ──────────────────────────


def test_image_batch_subdir_zip_and_pixel_filter(tmp_path) -> None:
    """图片一批：落同名子目录、过滤 1×1、可选 zip（AC-227）。"""
    files = {
        "https://cdn.example.com/a.png": png_bytes(100, 100),
        "https://cdn.example.com/b.png": png_bytes(100, 100),
        "https://cdn.example.com/pixel.png": png_bytes(1, 1),
    }
    images = [
        {"id": 1, "kind": "image", "ext": "png", "source_url": "https://cdn.example.com/a.png"},
        {"id": 2, "kind": "image", "ext": "png", "source_url": "https://cdn.example.com/b.png"},
        {
            "id": 3,
            "kind": "image",
            "ext": "png",
            "source_url": "https://cdn.example.com/pixel.png",
        },
    ]
    outcomes = asyncio.run(
        MediaFetcherService(FakeOutbound(files)).fetch_images(
            str(tmp_path),
            1,
            images,
            title="Gallery",
            pack_zip=True,
        )
    )
    image_names = [
        outcome.output_name
        for outcome in outcomes
        if outcome.status == "success" and not outcome.output_name.endswith(".zip")
    ]
    assert image_names == ["001.png", "002.png"]
    assert [outcome.status for outcome in outcomes].count("skipped") == 1
    assert (tmp_path / "Gallery" / "001.png").exists()
    zip_file = next(outcome for outcome in outcomes if outcome.output_name.endswith(".zip"))
    assert zip_file.output_name == "Gallery.zip"
    assert (tmp_path / "Gallery.zip").exists()


# ────────────────────────── HLS / DASH 真实夹具 ──────────────────────────


def test_hls_download_and_merge_has_audio(tmp_path) -> None:
    """HLS：分片下载 + 本地化 + ffmpeg 合并；产物含音频（AC-215）。"""
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    _, files = make_hls_fixture(fixture_dir)
    outbound = FakeOutbound(files)
    outcome = asyncio.run(
        MediaFetcherService(outbound).fetch_item(
            str(tmp_path / "out"),
            1,
            {
                "id": 1,
                "kind": "video",
                "ext": "mp4",
                "height": 240,
                "display_name": "240P",
                "source_url": "https://cdn.example.com/out.m3u8",
                "protocol": "m3u8",
            },
            title="HLS Demo",
        )
    )
    output = tmp_path / "out" / outcome.output_name
    assert output.exists()
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,crypto",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=noprint_wrappers=1",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "codec_type=video" in probe.stdout
    assert "codec_type=audio" in probe.stdout


def test_dash_download_and_mux_has_audio(tmp_path) -> None:
    """DASH：init+分片拼接 + ffmpeg mux；产物含音频（I-7）。"""
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    _, files = make_dash_fixture(fixture_dir)
    outbound = FakeOutbound(files)
    outcome = asyncio.run(
        MediaFetcherService(outbound).fetch_item(
            str(tmp_path / "out"),
            1,
            {
                "id": 1,
                "kind": "video",
                "ext": "mp4",
                "height": 240,
                "display_name": "240P",
                "source_url": "https://cdn.example.com/manifest.mpd",
                "protocol": "mpd_segments",
                "variant_id": "0|1",
            },
            title="DASH Demo",
        )
    )
    output = tmp_path / "out" / outcome.output_name
    assert output.exists()
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,crypto",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=noprint_wrappers=1",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "codec_type=video" in probe.stdout
    assert "codec_type=audio" in probe.stdout


def test_audio_item_keeps_original_format(tmp_path) -> None:
    """AC-226：仅音频项保留源扩展名（m4a），不转码。"""
    outbound = FakeOutbound({"https://cdn.example.com/a.m4a": b"audio"})
    outcome = asyncio.run(
        MediaFetcherService(outbound).fetch_item(
            str(tmp_path),
            1,
            {
                "id": 1,
                "kind": "audio",
                "ext": "m4a",
                "source_url": "https://cdn.example.com/a.m4a",
                "protocol": "http",
            },
            title="Demo",
        )
    )
    assert outcome.output_name == "Demo_audio.m4a"
    assert (tmp_path / "Demo_audio.m4a").read_bytes() == b"audio"


def test_single_image_does_not_create_subdir(tmp_path) -> None:
    """AC-227：单张图片直接落目标目录，不套同名子目录。"""
    outbound = FakeOutbound({"https://cdn.example.com/a.png": png_bytes(100, 100)})
    outcomes = asyncio.run(
        MediaFetcherService(outbound).fetch_images(
            str(tmp_path),
            1,
            [
                {
                    "id": 1,
                    "kind": "image",
                    "ext": "png",
                    "source_url": "https://cdn.example.com/a.png",
                }
            ],
            title="Cover",
        )
    )
    assert outcomes[0].output_name == "Cover_img_001.png"
    assert (tmp_path / "Cover_img_001.png").exists()
    assert not (tmp_path / "Cover").exists()


class FlakyOutbound(FakeOutbound):
    """前 N 次 `stream_to_file` 抛瞬态网络错误，之后成功。"""

    def __init__(self, files: dict[str, bytes], failures: int) -> None:
        super().__init__(files)
        self.failures = failures
        self.attempts = 0

    async def stream_to_file(self, url, dest, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OutboundError("network_error", "模拟连接重置")
        return await super().stream_to_file(url, dest, **kwargs)


def test_transient_download_error_is_retried(tmp_path) -> None:
    """瞬态网络错误重试后成功（CDN 节点重置的真实主因）。"""
    outbound = FlakyOutbound(
        {"https://cdn.example.com/a.png": png_bytes(100, 100)}, failures=2
    )
    outcome = asyncio.run(
        MediaFetcherService(outbound).fetch_item(
            str(tmp_path),
            1,
            {
                "id": 1,
                "kind": "image",
                "ext": "png",
                "source_url": "https://cdn.example.com/a.png",
            },
            title="Retry",
        )
    )
    assert outbound.attempts == 3
    assert outcome.status == "success"
    assert (tmp_path / outcome.output_name).exists()


def test_image_batch_keeps_successes_when_one_fails(tmp_path) -> None:
    """一张失败不拖垮整批：成功项落盘并回填，失败项返回 failed。"""
    outbound = FakeOutbound(
        {
            "https://cdn.example.com/a.png": png_bytes(100, 100),
            "https://cdn.example.com/c.png": png_bytes(120, 120),
        }
    )
    outcomes = asyncio.run(
        MediaFetcherService(outbound).fetch_images(
            str(tmp_path),
            1,
            [
                {
                    "id": 1,
                    "kind": "image",
                    "ext": "png",
                    "source_url": "https://cdn.example.com/a.png",
                },
                {
                    "id": 2,
                    "kind": "image",
                    "ext": "png",
                    "source_url": "https://cdn.example.com/b.png",
                },
                {
                    "id": 3,
                    "kind": "image",
                    "ext": "png",
                    "source_url": "https://cdn.example.com/c.png",
                },
            ],
            title="Gallery",
        )
    )
    assert [outcome.status for outcome in outcomes] == ["success", "failed", "success"]
    assert outcomes[1].error_code == "url_expired"
    assert outcomes[1].item_id == 2
    assert (tmp_path / "Gallery" / "001.png").exists()
    assert (tmp_path / "Gallery" / "003.png").exists()


def test_image_batch_rejects_encrypted_or_non_image_bytes(tmp_path) -> None:
    """看起来是 .png 但内容不是图片 ⇒ content_invalid，不落盘垃圾文件。"""
    outbound = FakeOutbound(
        {"https://cdn.example.com/enc.png": b"\x09\x3d\xe3\xb1\xfc\x4a\xf4\x6f" * 8}
    )
    outcomes = asyncio.run(
        MediaFetcherService(outbound).fetch_images(
            str(tmp_path),
            1,
            [
                {
                    "id": 1,
                    "kind": "image",
                    "ext": "png",
                    "source_url": "https://cdn.example.com/enc.png",
                }
            ],
            title="Encrypted",
        )
    )
    assert outcomes[0].status == "failed"
    assert outcomes[0].error_code == "content_invalid"
    assert not list(tmp_path.rglob("*.png"))


def test_cleanup_task_temp_removes_hidden_dir(tmp_path) -> None:
    """AC-217：取消/失败后临时目录被清理，无残留。"""
    from src.services.media_fetcher_service import cleanup_task_temp

    hidden = tmp_path / ".jflove-download" / "7"
    hidden.mkdir(parents=True)
    (hidden / "seg.ts").write_bytes(b"x")
    cleanup_task_temp(str(tmp_path), 7)
    assert not hidden.exists()


def make_av_pair_fixture(
    directory, *, video_duration: int = 2, audio_duration: int = 2
) -> tuple[bytes, bytes]:
    """生成「纯视频 mp4 + 纯音频 m4a」一对夹具（v1.8.2 / F36）。"""
    video = directory / "v.mp4"
    audio = directory / "a.m4a"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
            "-t", str(video_duration), "-c:v", "libopenh264", "-b:v", "300k", "-an",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", str(audio_duration), "-c:a", "aac", "-vn",
            str(audio),
        ],
        check=True,
        capture_output=True,
    )
    return video.read_bytes(), audio.read_bytes()


def test_aux_url_is_downloaded_and_muxed(tmp_path) -> None:
    """v1.8.2 / F36：音视频分离项必须请求 aux_url 并合并出音轨（issue #9）。"""
    from src.services.media_fetcher_service import _probe_output

    video_bytes, audio_bytes = make_av_pair_fixture(tmp_path)
    outbound = FakeOutbound(
        {
            "https://cdn.example.com/v.mp4": video_bytes,
            "https://cdn.example.com/a.m4a": audio_bytes,
        }
    )
    outcome = asyncio.run(
        MediaFetcherService(outbound).fetch_item(
            str(tmp_path),
            1,
            {
                "id": 1,
                "kind": "video",
                "ext": "mp4",
                "height": 240,
                "source_url": "https://cdn.example.com/v.mp4",
                "aux_url": "https://cdn.example.com/a.m4a",
                "protocol": "http",
            },
            title="Demo",
        )
    )
    assert outcome.status == "success"
    assert "https://cdn.example.com/a.m4a" in outbound.requests
    probe = asyncio.run(_probe_output(outcome.output_path))
    assert "audio" in probe["streams"], probe
    assert "video" in probe["streams"], probe


def test_aux_url_failure_fails_item_without_silent_video(tmp_path) -> None:
    """v1.8.2 / F36.4：音轨 404 ⇒ 整项失败，不能落一个无声视频当成功。"""
    video_bytes, _ = make_av_pair_fixture(tmp_path)
    outbound = FakeOutbound({"https://cdn.example.com/v.mp4": video_bytes})
    with pytest.raises(FetchError) as excinfo:
        asyncio.run(
            MediaFetcherService(outbound).fetch_item(
                str(tmp_path),
                1,
                {
                    "id": 1,
                    "kind": "video",
                    "ext": "mp4",
                    "height": 240,
                    "source_url": "https://cdn.example.com/v.mp4",
                    "aux_url": "https://cdn.example.com/missing.m4a",
                    "protocol": "http",
                },
                title="Demo",
            )
        )
    assert excinfo.value.code in ("url_expired", "network_error", "merge_failed")
    assert not (tmp_path / "Demo_240P.mp4").exists()


def test_main_with_audio_skips_aux(tmp_path) -> None:
    """M-4：主轨自带音轨时不得再请求 aux（HLS/DASH 已合并的常见情况）。"""
    main = tmp_path / "av.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "2", "-c:v", "libopenh264", "-b:v", "300k", "-c:a", "aac",
            str(main),
        ],
        check=True,
        capture_output=True,
    )
    outbound = FakeOutbound({"https://cdn.example.com/av.mp4": main.read_bytes()})
    outcome = asyncio.run(
        MediaFetcherService(outbound).fetch_item(
            str(tmp_path),
            1,
            {
                "id": 1,
                "kind": "video",
                "ext": "mp4",
                "height": 240,
                "source_url": "https://cdn.example.com/av.mp4",
                "aux_url": "https://cdn.example.com/never.m4a",
                "protocol": "http",
            },
            title="Demo",
        )
    )
    assert outcome.status == "success"
    assert "https://cdn.example.com/never.m4a" not in outbound.requests


def test_merge_duration_mismatch_fails(tmp_path) -> None:
    """M-1（F36.3）：音轨比主轨长 >1s ⇒ merge_failed，不落成片。"""
    video_bytes, audio_bytes = make_av_pair_fixture(
        tmp_path, video_duration=1, audio_duration=6
    )
    outbound = FakeOutbound(
        {
            "https://cdn.example.com/v.mp4": video_bytes,
            "https://cdn.example.com/a.m4a": audio_bytes,
        }
    )
    with pytest.raises(FetchError) as excinfo:
        asyncio.run(
            MediaFetcherService(outbound).fetch_item(
                str(tmp_path),
                1,
                {
                    "id": 1,
                    "kind": "video",
                    "ext": "mp4",
                    "height": 240,
                    "source_url": "https://cdn.example.com/v.mp4",
                    "aux_url": "https://cdn.example.com/a.m4a",
                    "protocol": "http",
                },
                title="Demo",
            )
        )
    assert excinfo.value.code == "merge_failed"
    assert not (tmp_path / "Demo_240P.mp4").exists()


def test_ffmpeg_missing_fails_merge_without_output(tmp_path, monkeypatch) -> None:
    """AC-274：ffmpeg 不可用 ⇒ merge_failed，且不落任何成片（不得静默无声）。"""
    import src.services.media_fetcher_service as fetcher_module

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    target = tmp_path / "out"
    video_bytes, audio_bytes = make_av_pair_fixture(fixtures)
    outbound = FakeOutbound(
        {
            "https://cdn.example.com/v.mp4": video_bytes,
            "https://cdn.example.com/a.m4a": audio_bytes,
        }
    )
    monkeypatch.setattr(fetcher_module.media_probe, "get_ffmpeg_exe", lambda: None)
    with pytest.raises(FetchError) as excinfo:
        asyncio.run(
            MediaFetcherService(outbound).fetch_item(
                str(target),
                "1",
                {
                    "id": 1,
                    "kind": "video",
                    "ext": "mp4",
                    "height": 240,
                    "source_url": "https://cdn.example.com/v.mp4",
                    "aux_url": "https://cdn.example.com/a.m4a",
                    "protocol": "http",
                },
                title="Demo",
            )
        )
    assert excinfo.value.code == "merge_failed"
    produced = list(target.glob("*.mp4")) if target.exists() else []
    assert produced == []


def make_hls_track(directory, *, video: bool) -> dict[str, bytes]:
    """生成「纯视频 / 纯音频」HLS 轨道，键为带子目录前缀的 URL（R-1 回归）。"""
    directory.mkdir(parents=True, exist_ok=True)
    playlist = directory / "out.m3u8"
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if video:
        command += [
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
            "-t", "2", "-c:v", "libopenh264", "-b:v", "300k", "-an",
        ]
    else:
        command += [
            "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "2", "-c:a", "aac", "-vn",
        ]
    command += [
        "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
        "-hls_segment_filename", str(directory / "seg%03d.ts"),
        str(playlist),
    ]
    subprocess.run(command, check=True, capture_output=True)
    prefix = f"https://cdn.example.com/{directory.name}"
    files = {f"{prefix}/out.m3u8": playlist.read_bytes()}
    for path in directory.iterdir():
        if path.name != "out.m3u8":
            files[f"{prefix}/{path.name}"] = path.read_bytes()
    return files


def test_hls_main_plus_hls_aux_merges_without_overwrite(tmp_path) -> None:
    """R-1：HLS 主轨 + HLS 音轨必须合并成功（临时文件不得同名覆盖）。"""
    files = make_hls_track(tmp_path / "v", video=True)
    files.update(make_hls_track(tmp_path / "a", video=False))
    outbound = FakeOutbound(files)
    outcome = asyncio.run(
        MediaFetcherService(outbound).fetch_item(
            str(tmp_path / "out"),
            1,
            {
                "id": 1,
                "kind": "video",
                "ext": "mp4",
                "height": 240,
                "source_url": "https://cdn.example.com/v/out.m3u8",
                "aux_url": "https://cdn.example.com/a/out.m3u8",
                "protocol": "m3u8",
            },
            title="Demo",
        )
    )
    assert outcome.status == "success"
    assert "https://cdn.example.com/a/out.m3u8" in outbound.requests

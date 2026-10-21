"""
图片头探测单测（v1.8.0）

离线下载清单要展示图片分辨率，并过滤统计像素（需求 F23.5 / AC-204）。
这里用**手工构造的最小合法文件头**覆盖 5 种格式，避免依赖外部图片素材。

覆盖：
  1. PNG / JPEG / GIF / WebP(VP8/VP8L/VP8X) / BMP 的宽高解析；
  2. 非图片字节返回 None（不得抛异常）；
  3. 截断/畸形头不得抛异常；
  4. `is_too_small` 只在**两边都**小于阈值时为真（1×1 统计像素 vs 60×1 细条）。
"""

from __future__ import annotations

import struct

from src.utils.image_probe import (
    ImageMeta,
    is_too_small,
    probe_image_file,
    probe_image_header,
)


def _png(width: int, height: int) -> bytes:
    """构造 PNG 头（签名 + IHDR 前 16 字节足够解析宽高）。"""
    ihdr = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr


def _gif(width: int, height: int) -> bytes:
    """构造 GIF 头（Logical Screen Descriptor）。"""
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00"


def _bmp(width: int, height: int) -> bytes:
    """构造 BMP 头（BITMAPINFOHEADER 前 26 字节）。"""
    header = b"BM" + b"\x00" * 16
    return header + struct.pack("<ii", width, height)


def _jpeg(width: int, height: int) -> bytes:
    """构造 JPEG：SOI + APP0(最小) + SOF0(含宽高) + EOI。"""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">H", height)
        + struct.pack(">H", width)
        + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def _webp_vp8x(width: int, height: int) -> bytes:
    """构造 WebP（VP8X 扩展头）：RIFF + WEBP + VP8X + 画布尺寸-1。"""
    payload = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00"
    payload += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = payload
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def _webp_vp8l(width: int, height: int) -> bytes:
    """构造 WebP（VP8L 无损头）：14 位宽 + 14 位高（各减 1）。"""
    bits = ((width - 1) & 0x3FFF) | (((height - 1) & 0x3FFF) << 14)
    payload = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + struct.pack("<I", bits)
    return b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WEBP" + payload


def _webp_vp8(width: int, height: int) -> bytes:
    """构造 WebP（VP8 有损头）：起始码 + 宽高各 2 字节（低 14 位）。"""
    frame = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height)
    payload = b"VP8 " + struct.pack("<I", len(frame)) + frame
    return b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WEBP" + payload


def test_png_size() -> None:
    meta = probe_image_header(_png(1920, 1080))
    assert meta == ImageMeta(width=1920, height=1080, fmt="png")


def test_jpeg_size() -> None:
    meta = probe_image_header(_jpeg(800, 600))
    assert meta == ImageMeta(width=800, height=600, fmt="jpeg")


def test_gif_size() -> None:
    meta = probe_image_header(_gif(64, 32))
    assert meta == ImageMeta(width=64, height=32, fmt="gif")


def test_bmp_size() -> None:
    meta = probe_image_header(_bmp(120, 90))
    assert meta is not None
    assert (meta.width, meta.height, meta.fmt) == (120, 90, "bmp")


def test_webp_vp8x_size() -> None:
    meta = probe_image_header(_webp_vp8x(1024, 768))
    assert meta is not None
    assert (meta.width, meta.height, meta.fmt) == (1024, 768, "webp")


def test_webp_vp8l_size() -> None:
    meta = probe_image_header(_webp_vp8l(320, 240))
    assert meta is not None
    assert (meta.width, meta.height) == (320, 240)


def test_webp_vp8_size() -> None:
    meta = probe_image_header(_webp_vp8(480, 360))
    assert meta is not None
    assert (meta.width, meta.height) == (480, 360)


def test_non_image_returns_none() -> None:
    """HTML / 纯文本 / 空字节都不能当图片（否则清单会混进一堆垃圾项）。"""
    assert probe_image_header(b"<html><body>hi</body></html>") is None
    assert probe_image_header(b"") is None
    assert probe_image_header(b"\x00" * 64) is None


def test_truncated_headers_do_not_raise() -> None:
    """畸形/截断头只能返回 None，**不得抛异常**（攻击者可以直接喂垃圾字节）。"""
    for raw in (_png(10, 10)[:10], _jpeg(10, 10)[:6], _gif(10, 10)[:7], _webp_vp8x(9, 9)[:20]):
        assert probe_image_header(raw) is None


def test_small_icon_detected_but_thin_strip_is_not() -> None:
    """1×1 统计像素应被过滤；60×1 的细条不算（否则会误杀分隔线图片）。"""
    assert is_too_small(ImageMeta(1, 1, "gif"), 50) is True
    assert is_too_small(ImageMeta(48, 48, "png"), 50) is True
    assert is_too_small(ImageMeta(60, 1, "png"), 50) is False
    assert is_too_small(None, 50) is False


def test_probe_image_file_reads_head_only(tmp_path) -> None:
    """文件探测只读头部：写入真文件后能拿到宽高。"""
    path = tmp_path / "a.png"
    path.write_bytes(_png(300, 200) + b"\x00" * 4096)
    meta = probe_image_file(str(path))
    assert meta is not None
    assert (meta.width, meta.height) == (300, 200)


def test_probe_image_file_missing_returns_none(tmp_path) -> None:
    """文件不存在时返回 None（调用方不必 try/except）。"""
    assert probe_image_file(str(tmp_path / "nope.png")) is None

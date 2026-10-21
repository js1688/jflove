"""
图片头探测（v1.8.0 新增）

离线下载需要两个本模块才能回答的问题：
  1. 这张图**多大**？—— 清单里要给用户展示分辨率（需求 F23.2）
  2. 它是不是**统计像素/图标**？—— 宽高都小于阈值就不进清单（需求 F23.5）

为什么不用 Pillow：只需要宽高，引入一个带 C 扩展的图片库（约数十 MB，且要跟随
Python ABI 升级）不划算；四种常见格式的文件头解析约 100 行，且**不依赖解码**，
不会因为畸形图片触发解码器漏洞。

支持：PNG / JPEG / GIF / WebP（VP8 / VP8L / VP8X）/ BMP。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: 只读文件头（足够覆盖 JPEG 的 SOF 段；超过这个长度仍没找到就放弃）
HEAD_BYTES = 64 * 1024


@dataclass(frozen=True)
class ImageMeta:
    """图片头信息（只含宽高与格式名）。"""

    width: int
    height: int
    fmt: str

    @property
    def is_too_small(self) -> bool:
        """是否两边都小于阈值（调用方给阈值，见 `is_too_small()`）"""
        return self.width <= 0 or self.height <= 0


def _png_size(data: bytes) -> tuple[int, int] | None:
    """PNG：签名 8 字节后紧跟 IHDR（宽高各 4 字节大端）。"""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _gif_size(data: bytes) -> tuple[int, int] | None:
    """GIF：Logical Screen Descriptor 从偏移 6 开始（宽高各 2 字节小端）。"""
    if len(data) < 10 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    width, height = struct.unpack("<HH", data[6:10])
    return int(width), int(height)


def _bmp_size(data: bytes) -> tuple[int, int] | None:
    """BMP：BITMAPINFOHEADER 的宽高（有符号，高度为负表示自上而下）。"""
    if len(data) < 26 or data[:2] != b"BM":
        return None
    width, height = struct.unpack("<ii", data[18:26])
    return abs(int(width)), abs(int(height))


#: JPEG 里可能携带尺寸的 SOF 标记（含无损/算术编码的变体）
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """
    JPEG：从 SOI 开始逐段扫描，遇到 SOFn 段读出高宽。

    段结构：`0xFF <marker> <2字节长度> <载荷>`；`0xD8`(SOI)/`0xD9`(EOI) 无长度。
    """
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    pos = 2
    total = len(data)
    while pos + 3 < total:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xD9:
            return None
        if pos + 4 > total:
            return None
        seg_len = int.from_bytes(data[pos + 2:pos + 4], "big")
        if seg_len < 2 or pos + 2 + seg_len > total:
            return None
        if marker in _JPEG_SOF_MARKERS and seg_len >= 7:
            height = int.from_bytes(data[pos + 5:pos + 7], "big")
            width = int.from_bytes(data[pos + 7:pos + 9], "big")
            return width, height
        pos += 2 + seg_len
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    """WebP：RIFF 容器 + VP8（有损）/ VP8L（无损）/ VP8X（扩展）三种头。"""
    # 只要求容器头完整（16 字节）；各子格式再各自校验所需长度
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8 ":
        if len(data) < 30:
            return None
        # 帧头：3 字节固定 + 3 字节起始码(9d 01 2a) + 宽高（各 2 字节，低 14 位有效）
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return int(width), int(height)
    if chunk == b"VP8L":
        if len(data) < 25 or data[20] != 0x2F:
            return None
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return int(width), int(height)
    if chunk == b"VP8X":
        if len(data) < 30:
            return None
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return int(width), int(height)
    return None


def probe_image_header(data: bytes) -> ImageMeta | None:
    """
    从文件头字节解析图片宽高与格式。

    :param data: 文件头字节（建议至少 `HEAD_BYTES`，不足也能解析已到达的部分）
    :returns: `ImageMeta`；不是受支持的图片（或头不完整）时返回 None
    """
    if not data:
        return None
    probes = (
        ("png", _png_size),
        ("jpeg", _jpeg_size),
        ("gif", _gif_size),
        ("webp", _webp_size),
        ("bmp", _bmp_size),
    )
    for fmt, parser in probes:
        try:
            size = parser(data)
        except (struct.error, ValueError, IndexError):
            size = None
        if size is not None and size[0] > 0 and size[1] > 0:
            return ImageMeta(width=int(size[0]), height=int(size[1]), fmt=fmt)
    return None


def probe_image_file(path: str, *, head_bytes: int = HEAD_BYTES) -> ImageMeta | None:
    """
    读取本地文件头并解析宽高（只读前 `head_bytes` 字节）。

    :param path: 本地文件路径
    :param head_bytes: 读取上限
    :returns: `ImageMeta`；无法识别时返回 None
    """
    try:
        with open(path, "rb") as fp:
            head = fp.read(head_bytes)
    except OSError:
        return None
    return probe_image_header(head)


def is_too_small(meta: ImageMeta | None, min_side: int) -> bool:
    """
    是否应被判为统计像素/图标（**两边都**小于阈值才算）。

    :param meta: 图片头信息；None 视为无法判定（**不算** too small，交给调用方决定）
    :param min_side: 阈值像素
    """
    if meta is None:
        return False
    return meta.width < min_side and meta.height < min_side

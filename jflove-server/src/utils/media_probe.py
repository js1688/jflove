"""
媒体探测与健康判定工具（v1.4.0 新增）

通过 imageio-ffmpeg 内置的 FFmpeg 二进制（无需在系统安装 FFmpeg）探测媒体文件，
供媒体修复服务的健康判定使用：

  - 判断文件是否损坏 / 是否为浏览器 MSE 可直接播放的标准流式格式
  - 健康文件 → 原文件直接流式（零处理）；非健康文件 → 交由 media_repair_service 修复

说明：imageio-ffmpeg 仅内置 ffmpeg（无 ffprobe），故探测改用 `ffmpeg -i` 解析 stderr
文本。imageio-ffmpeg 未安装或二进制不可用时，本模块自动降级（不修复、走原文件）。

安全约束：
  - 探测命令使用参数列表（无 shell 拼接），文件路径已由调用方 _safe_join 校验
  - 探测不读取文件内容到响应，不产生日志明文路径
"""

import asyncio
import os
import re
import shutil
import struct
import time

from src.config.settings import MEDIA_REPAIR_NO_OUTPUT_TIMEOUT
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 探测结果短 TTL 缓存（S1 修复）：文件未变化且未过期则复用，避免每次 /stream 请求
# 重复执行 `ffmpeg -i` 探测（seek 多次 range 请求时收益明显）
_PROBE_TTL_SECONDS = 60.0
_probe_cache: dict[str, tuple[float, int, float, dict]] = {}

_UNSET = object()
_ffmpeg_exe: str | None | object = _UNSET

# 浏览器 MSE 原生支持、无需修复的简单格式（probe 正常即视为健康）
_DIRECT_EXTS = {"webm", "mp3", "wav", "flac", "ogg", "opus"}
# MP4 家族：需额外检查 moov 是否位于文件前部（可流式）
_MP4_LIKE_EXTS = {"mp4", "m4v", "m4a", "mov", "3gp"}
# 已知的浏览器/MSE 不支持的容器（需转封装为 fMP4）
_REPAIR_EXTS = {
    "mkv", "avi", "flv", "wmv", "mpg", "mpeg", "ts", "asf", "rm", "rmvb", "webm-hd",
}


def get_ffmpeg_exe() -> str | None:
    """
    获取 FFmpeg 可执行文件路径（懒加载并缓存）。

    imageio-ffmpeg 未安装 / 二进制缺失时返回 None，调用方应降级为"不修复"。

    :returns: FFmpeg 可执行文件绝对路径；不可用时返回 None
    """
    global _ffmpeg_exe
    if _ffmpeg_exe is _UNSET:
        # v1.4.1：优先使用系统 ffmpeg（Docker 镜像内 apt 安装，最可靠）；
        # 退回 imageio-ffmpeg 内置二进制（无系统 ffmpeg 的环境 / 本机开发）。
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            _ffmpeg_exe = system_ffmpeg
        else:
            try:
                import imageio_ffmpeg
                _ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception as e:  # pragma: no cover - 依赖缺失场景
                logger.warning("imageio-ffmpeg 不可用，媒体修复自动关闭：%s", e)
                _ffmpeg_exe = None
    return _ffmpeg_exe if isinstance(_ffmpeg_exe, str) and _ffmpeg_exe else None


def _box_contains(data: bytes, start: int, end: int, target: bytes) -> bool:
    """在 [start, end) 内扫描同层 box，判断是否包含 target 类型 box。"""
    pos = start
    while pos + 8 <= end and pos + 8 <= len(data):
        box_size = struct.unpack(">I", data[pos:pos + 4])[0]
        box_type = data[pos + 4:pos + 8]
        if box_type == target:
            return True
        if box_size < 8:
            break
        pos += box_size
    return False


def _moov_at_front(file_path: str, head_bytes: int = 4 * 1024 * 1024) -> bool:
    """
    检查 MP4 是否为「可流式分片 fMP4」：moov 位于文件前部 **且含 mvex**，
    且 moov 之后紧跟 moof（而非孤儿 mdat）。

    v1.4.2 hotfix 修正：Web MSE 边下边播只认分片 fMP4（moov 含 mvex +
    后续 moof）。faststart MP4（moov 前置但非分片）无法被 MSE 增量 append，
    必须视为需修复；moov 在尾部（mdat 在前）同样无法流式 → 需修复。
    桌面/移动端原生播放器虽能播 faststart/普通 MP4，但「修复必要性」与
    「Web 可流式」统一以分片 fMP4 为标准（用户确认：不能边下边播即需修复）。

    v1.4.2 二轮 hotfix：`+faststart` 与 `+frag_keyframe` 组合会产生
    「moov → mdat(孤儿) → moof」的坏结构（首个 moof 丢失），MSE 首段 append
    失败导致闪屏/只播后半段。此类文件 moov 虽含 mvex，但 moov 后紧跟 mdat
    （孤儿，无 moof 描述）→ 同样需修复。

    :param file_path: 文件绝对路径
    :param head_bytes: 读取的头部字节数（4MB，覆盖绝大多数 moov）
    :returns: moov 前置且含 mvex、且 moov 后紧跟 moof 返回 True；否则 False
    """
    try:
        with open(file_path, "rb") as f:
            head = f.read(head_bytes)
    except OSError:
        return False

    pos = 0
    size = len(head)
    # 跳过 ftyp box（若存在）
    if size >= 8 and head[4:8] == b"ftyp":
        ftyp_size = struct.unpack(">I", head[0:4])[0]
        pos = ftyp_size if ftyp_size >= 8 else 0

    # 扫描头部 box：首个业务 box 为 moov 且其内部含 mvex → 可流式
    while pos + 8 <= size:
        box_size = struct.unpack(">I", head[pos:pos + 4])[0]
        box_type = head[pos + 4:pos + 8]
        if box_type == b"moov":
            if pos + box_size > size:
                # moov 超出已读头部，无法确认 mvex → 保守视为需修复
                return False
            if not _box_contains(head, pos + 8, pos + box_size, b"mvex"):
                # moov 前置但无 mvex → 非分片（faststart/普通 MP4），需修复
                return False
            # moov 之后必须紧跟 moof：跳过 free/skip/wide 等无关 box 后，
            # 若首个媒体 box 是 mdat（孤儿，无 moof 描述）→ 坏 fMP4，需修复
            nxt = pos + box_size
            while nxt + 8 <= size:
                nxt_size = struct.unpack(">I", head[nxt:nxt + 4])[0]
                nxt_type = head[nxt + 4:nxt + 8]
                if nxt_size < 8:
                    return False
                if nxt_type in (b"free", b"skip", b"wide"):
                    nxt += nxt_size
                    continue
                # 首个媒体 box 必须是 moof；mdat 在前 = 孤儿 mdat → 坏结构
                return nxt_type == b"moof"
            return False
        if box_type == b"mdat":
            # mdat 在前 → moov 在文件尾部（非流式），需要修复
            return False
        if box_size < 8:
            break
        pos += box_size
    return False


async def probe_media(file_path: str) -> dict:
    """
    用 `ffmpeg -i` 探测媒体文件（只解析容器头，不完整解码）。

    探测结果按 (mtime, size) 短 TTL 缓存，文件未变化时复用，避免重复启动 ffmpeg。

    :param file_path: 文件绝对路径
    :returns: 探测结果字典：
        - available: imageio-ffmpeg 是否可用
        - has_stream: 是否解析到至少一个音/视频流
        - duration_ok: 是否能读取到有效 Duration（非 N/A）
        - fatal: 是否存在致命错误（Invalid data / moov atom not found 等）
        - duration: 媒体时长（秒 float，解析失败为 0.0）
    """
    # 短 TTL 缓存命中：文件未变化且未过期
    cached = _probe_cache.get(file_path)
    if cached is not None:
        mtime, size, ts, result = cached
        try:
            st = os.stat(file_path)
            if (
                mtime == st.st_mtime
                and size == st.st_size
                and time.time() - ts < _PROBE_TTL_SECONDS
            ):
                return result
        except OSError:
            pass

    exe = get_ffmpeg_exe()
    if not exe:
        return {
            "available": False,
            "has_stream": False,
            "duration_ok": False,
            "fatal": False,
            "duration": 0.0,
        }

    cmd = [exe, "-hide_banner", "-i", file_path]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(
            proc.communicate(), timeout=MEDIA_REPAIR_NO_OUTPUT_TIMEOUT
        )
    except (asyncio.TimeoutError, OSError) as e:
        # v1.4.1：探测「执行失败」（二进制不可执行/超时）≠ 文件无媒体流。
        # available=False 表示“探测未成功运行”，供 ensure_playable 回退 byte 模式。
        # 日志只记录异常类型，不记录完整 message（OSError 可能含文件路径，§9.4）。
        logger.warning("媒体探测执行失败：%s", type(e).__name__)
        return {
            "available": False,
            "has_stream": False,
            "duration_ok": False,
            "fatal": True,
            "duration": 0.0,
        }

    text = (err or b"").decode(errors="replace")
    has_stream = "Stream #" in text
    duration_ok = ("Duration:" in text) and ("Duration: N/A" not in text)
    fatal = any(
        marker in text
        for marker in (
            "Invalid data found",
            "moov atom not found",
            "Could not find",
            "not found",
            "Error",
            "error while",
        )
    )
    # 解析媒体时长（秒，如 "Duration: 00:00:03.13"）；供 time 修复流 meta 帧
    # 的 duration 字段使用（桌面/移动端线性 seek 映射需要）
    duration = 0.0
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        try:
            duration = (
                float(match.group(1)) * 3600
                + float(match.group(2)) * 60
                + float(match.group(3))
            )
        except ValueError:
            duration = 0.0
    result = {
        "available": bool(exe),
        "has_stream": has_stream,
        "duration_ok": duration_ok,
        "fatal": fatal,
        "duration": duration,
    }
    # 写入短 TTL 缓存（mtime+size 作为失效依据）；容量超限时整体清空防增长
    try:
        st = os.stat(file_path)
        _probe_cache[file_path] = (st.st_mtime, st.st_size, time.time(), result)
        if len(_probe_cache) > 512:
            _probe_cache.clear()
    except OSError:
        pass
    return result


def is_mse_friendly(filename: str, probe: dict, file_path: str) -> bool:
    """
    判断文件是否为"原生播放器可直接流式播放"的健康格式（v1.4.2 语义）。

    供「修复必要性判定」使用：返回 True = 健康（修复请求拒绝）；
    False = 需修复（MKV/AVI/FLV 等非流式容器、moov 尾部 MP4 等）。

    :param filename: 文件名（用于推断扩展名）
    :param probe: probe_media 的返回结果
    :param file_path: 文件绝对路径（用于 MP4 moov 位置检查）
    :returns: 健康返回 True（原文件直接流式，零处理）
    """
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    healthy_stream = bool(probe.get("has_stream")) and not bool(probe.get("fatal"))

    if ext in _DIRECT_EXTS:
        return healthy_stream
    if ext in _MP4_LIKE_EXTS:
        return healthy_stream and _moov_at_front(file_path)
    if ext in _REPAIR_EXTS:
        return False
    # 未知扩展名：若 probe 显示有流且无致命错误，视为健康（交给播放器尝试）
    return healthy_stream


def is_broken_media(filename: str, probe: dict, file_path: str) -> bool:
    """
    判断文件是否为"真损坏、无法在线播放"（v1.4.2 播放门禁专用）。

    与 is_mse_friendly（修复必要性）的区别：
      - 本函数只拦「探测出致命错误或无媒体流」的文件——任何端都播不了，
        必须拒绝播放并引导修复；
      - MKV/AVI/moov 尾部等"格式不理想但桌面/移动端可能原生可播"的文件
        **不拦**：桌面端 QMediaPlayer、移动端 ExoPlayer 原生支持这些容器，
        直接放行原文件字节流（v1.4.2 播放纯净化 = 零转码，格式兼容性
        交还给各端原生播放器）。

    :param filename: 文件名（用于扩展名判定，仅媒体扩展名参与门禁）
    :param probe: probe_media 的返回结果
    :param file_path: 文件绝对路径（未使用，签名与 is_mse_friendly 对齐）
    :returns: 真损坏返回 True（应 415 + MEDIA_NEEDS_REPAIR）；否则 False（放行）
    """
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext not in _DIRECT_EXTS and ext not in _MP4_LIKE_EXTS and ext not in _REPAIR_EXTS:
        return False  # 非媒体扩展名（文本/图片/压缩包等）不参与媒体门禁
    if not probe.get("available"):
        return False  # 探测本身失败（ffmpeg 缺失等）→ 放行，不误伤
    return bool(probe.get("fatal")) or not bool(probe.get("has_stream"))


def is_web_streamable(filename: str, probe: dict, file_path: str) -> bool:
    """
    判断文件能否被 Web 端 MSE 边下边播（v1.4.2 hotfix：以能否流式为标准）。

    供 /stream 的 mse_only 模式使用：Web 只接受「可流式」格式——
      - _DIRECT_EXTS（WebM / MP3 / WAV / FLAC / OGG / OPUS）：probe 正常即可流式
      - MP4 家族：必须是分片 fMP4（moov 前置且含 mvex）
      - 其余（TS/MKV/AVI/普通 MP4/faststart/损坏/未知）→ 不可流式，应引导修复

    :returns: Web MSE 可边下边播返回 True；否则 False
    """
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    healthy_stream = bool(probe.get("has_stream")) and not bool(probe.get("fatal"))
    if not probe.get("available"):
        # 探测失败（ffmpeg 缺失等）：仅直接可播格式放行，其余保守拒绝
        return ext in _DIRECT_EXTS
    if ext in _DIRECT_EXTS:
        return healthy_stream
    if ext in _MP4_LIKE_EXTS:
        return healthy_stream and _moov_at_front(file_path)
    return False


def needs_repair(filename: str, probe: dict, file_path: str) -> bool:
    """
    判断文件是否需要修复（供修复服务使用）。

    :param filename: 文件名
    :param probe: probe_media 的返回结果
    :param file_path: 文件绝对路径
    :returns: 需要修复返回 True
    """
    return not is_mse_friendly(filename, probe, file_path)


def patch_mp4_durations(file_path: str, duration_seconds: float) -> bool:
    """
    修正 fMP4 产物的所有 duration 字段（mvhd / tkhd / mdhd）（v1.4.2 hotfix）。

    ffmpeg `-movflags +empty_moov+frag_keyframe` 生成的 moov 是空壳，
    mvhd / tkhd / mdhd 的 duration 都写成 0（时长由 mvex + moof 决定），
    浏览器在边下边播时读不到总时长，video.duration 异常、进度条与 seek
    异常。本函数按真实总时长重写这些字段（tkhd 用 movie timescale，
    mvhd / mdhd 用各自 timescale）。

    :param file_path: 产物绝对路径
    :param duration_seconds: 真实总时长（秒，来自 probe_media 解析）
    :returns: 成功返回 True；无法定位 moov/mvhd 返回 False
    """
    if duration_seconds <= 0:
        return False
    try:
        with open(file_path, "r+b") as f:
            data = f.read(4 * 1024 * 1024)
            n = len(data)
            # 1) 定位顶层 moov
            pos = 0
            moov_pos = -1
            while pos + 8 <= n:
                size = struct.unpack(">I", data[pos:pos + 4])[0]
                typ = data[pos + 4:pos + 8]
                if typ == b"moov":
                    moov_pos = pos
                    break
                if size < 8:
                    break
                pos += size
            if moov_pos < 0:
                return False
            moov_size = struct.unpack(">I", data[moov_pos:moov_pos + 4])[0]
            moov_end = moov_pos + moov_size
            if moov_end > n:
                return False

            movie_timescale = 0
            # (offset, is_64bit, timescale) —— tkhd 的 timescale 先记 0，统一用 movie
            writes: list[tuple[int, bool, int]] = []

            def _scan(start: int, end: int) -> None:
                nonlocal movie_timescale
                p = start
                while p + 8 <= end:
                    size = struct.unpack(">I", data[p:p + 4])[0]
                    typ = data[p + 4:p + 8]
                    if size < 8:
                        break
                    if typ == b"mvhd":
                        v = data[p + 8]
                        if v == 1:
                            movie_timescale = struct.unpack(">I", data[p + 28:p + 32])[0]
                            writes.append((p + 32, True, movie_timescale))
                        else:
                            movie_timescale = struct.unpack(">I", data[p + 20:p + 24])[0]
                            writes.append((p + 24, False, movie_timescale))
                    elif typ == b"tkhd":
                        v = data[p + 8]
                        if v == 1:
                            writes.append((p + 32, True, 0))
                        else:
                            writes.append((p + 28, False, 0))
                    elif typ == b"mdhd":
                        v = data[p + 8]
                        if v == 1:
                            ts = struct.unpack(">I", data[p + 28:p + 32])[0]
                            writes.append((p + 32, True, ts))
                        else:
                            ts = struct.unpack(">I", data[p + 20:p + 24])[0]
                            writes.append((p + 24, False, ts))
                    if typ in (b"trak", b"mdia"):
                        _scan(p + 8, p + size)
                    p += size

            _scan(moov_pos + 8, moov_end)
            if movie_timescale <= 0:
                return False

            # 2) 按各自 timescale 重写 duration
            for off, is64, ts in writes:
                if ts <= 0:
                    ts = movie_timescale  # tkhd 用 movie timescale
                new_dur = int(round(duration_seconds * ts))
                if is64:
                    f.seek(off)
                    f.write(struct.pack(">Q", new_dur))
                else:
                    if new_dur > 0xFFFFFFFF:
                        return False
                    f.seek(off)
                    f.write(struct.pack(">I", new_dur))
            return True
    except OSError:
        return False


def is_repair_supported_extension(filename: str) -> bool:
    """
    判断扩展名是否属于可尝试修复的媒体类型。

    用于在修复开启但 ffmpeg 不可用时区分"可修但缺能力"与"无法解析"。

    :param filename: 文件名
    :returns: 属于已知媒体扩展名返回 True
    """
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in _DIRECT_EXTS or ext in _MP4_LIKE_EXTS or ext in _REPAIR_EXTS


def ffmpeg_version() -> str:
    """返回 FFmpeg 版本号（用于日志/排障）；不可用时返回 'N/A'。"""
    exe = get_ffmpeg_exe()
    if not exe:
        return "N/A"
    try:
        import subprocess
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, timeout=10
        ).stdout
        return out.splitlines()[0] if out else "N/A"
    except Exception:
        return "N/A"


# ── fMP4 codec 解析（v1.4.0 测试发现）──────────────────────────────
# 浏览器 MSE 的 SourceBuffer 需要精确的 codec 字符串（如 "avc1.64001f, mp4a.40.2"），
# 声明与实际流 track 不匹配会导致 append 报错。修复流由 ffmpeg 实时生成，Web 端
# 无法预知 codec，故由服务端解析 fMP4 init segment 的 stsd（avcC/esds）构造 codec，
# 放入 meta 帧供客户端创建 SourceBuffer。

def _desc_len(data: bytes, pos: int) -> tuple[int, int]:
    """解析 MPEG-4 descriptor 的变长 length 字段，返回 (length, 新 pos)。"""
    length = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        length = (length << 7) | (b & 0x7F)
        if not (b & 0x80):
            break
    return length, pos


def _box_ranges(data: bytes, start: int, end: int, target: bytes) -> list[tuple[int, int]]:
    """在 [start, end) 范围内遍历同层 box，返回匹配 target 类型的 (start, end) 列表。"""
    found = []
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        btype = data[pos + 4:pos + 8]
        if btype == target:
            found.append((pos, pos + size))
        if size < 8:
            break
        pos += size
    return found


def _avc1_codec(box: bytes) -> str | None:
    """从 avc1/avc3 box 提取 H.264 codec 字符串（avc1.<profile><compat><level>）。

    box 结构：size(4)+type(4)+...；avcC 的 'avcC' 在 box 内偏移 4，
    payload: version(1)+profile_idc(1)+compat(1)+level_idc(1)，故
    profile 在 'avcC' 后第 5 字节（idx+5）。
    """
    idx = box.find(b"avcC")
    if idx == -1 or idx + 9 > len(box):
        return None
    profile = box[idx + 5]
    compat = box[idx + 6]
    level = box[idx + 7]
    return f"avc1.{profile:02x}{compat:02x}{level:02x}"


def _mp4a_codec(box: bytes) -> str | None:
    """从 mp4a box 提取 AAC codec 字符串（mp4a.40.<AOT>）。

    esds box 内：'esds' 在 box 内偏移 4，version/flags(4) 后为
    ES_Descriptor(0x03) → DecoderConfigDescriptor(0x04) → DecoderSpecificInfo(0x05)
    → AudioSpecificConfig（首字节高 5 bit = audioObjectType）。

    v1.4.1：TS 等来源 -c copy 重封装出的 mp4a box 可能**缺失 DecoderSpecificInfo**，
    此时无法解析精确 AOT，回退 AAC-LC（mp4a.40.2）——否则 meta.codec 只含视频，
    浏览器 MSE 会因「实际含音频轨但 codecs 未声明」而拒绝 addSourceBuffer。
    """
    idx = box.find(b"esds")
    if idx == -1:
        return "mp4a.40.2"  # mp4a 但无 esds：按 AAC-LC 兜底
    p = idx + 8  # esds payload（version/flags 之后）起始
    if p < len(box) and box[p] == 0x03:
        _, p = _desc_len(box, p + 1)
        p += 3  # ES_ID(2) + streamPriority(1)
    if p < len(box) and box[p] == 0x04:
        _, p = _desc_len(box, p + 1)
        # objectType(1)+streamType(1)+bufferSizeDB(3)+maxBitrate(4)+avgBitrate(4)
        p += 13
    if p < len(box) and box[p] == 0x05:
        _, p = _desc_len(box, p + 1)
        if p < len(box):
            aot = (box[p] >> 3) & 0x1F  # AudioSpecificConfig 高 5 bit = audioObjectType
            return f"mp4a.40.{aot}"
    return "mp4a.40.2"  # 缺 DecoderSpecificInfo：按 AAC-LC 兜底


def parse_fmp4_codec(init: bytes) -> str:
    """
    从 fMP4 init segment（ftyp + moov）解析 codec 字符串。

    例：仅视频 → "avc1.64001f"；视频+音频 → "avc1.64001f, mp4a.40.2"。

    :param init: fMP4 开头字节（需含完整 moov）
    :returns: codec 字符串；无法解析时返回空串
    """
    video = None
    audio = None
    pos = 0
    n = len(init)
    while pos + 8 <= n:
        size = struct.unpack(">I", init[pos:pos + 4])[0]
        btype = init[pos + 4:pos + 8]
        if btype == b"moov":
            for trak_s, trak_e in _box_ranges(init, pos + 8, pos + size, b"trak"):
                for mdia_s, mdia_e in _box_ranges(init, trak_s + 8, trak_e, b"mdia"):
                    for minf_s, minf_e in _box_ranges(init, mdia_s + 8, mdia_e, b"minf"):
                        for stbl_s, stbl_e in _box_ranges(init, minf_s + 8, minf_e, b"stbl"):
                            for stsd_s, stsd_e in _box_ranges(init, stbl_s + 8, stbl_e, b"stsd"):
                                ep = stsd_s + 8 + 8  # stsd: version/flags(4)+entry_count(4)
                                while ep + 8 <= stsd_e:
                                    esz = struct.unpack(">I", init[ep:ep + 4])[0]
                                    etype = init[ep + 4:ep + 8]
                                    entry = init[ep:min(ep + esz, n)]
                                    if etype in (b"avc1", b"avc3"):
                                        video = _avc1_codec(entry)
                                    elif etype == b"mp4a":
                                        audio = _mp4a_codec(entry)
                                    if esz < 8:
                                        break
                                    ep += esz
            # 所有 trak 遍历完成后汇总 codec（视频, 音频）
            return _join_codec(video, audio)
        if size < 8:
            break
        pos += size
    return _join_codec(video, audio)


def _join_codec(video: str | None, audio: str | None) -> str:
    """拼接 codec 字符串（按 视频, 音频 顺序）。"""
    return ", ".join(c for c in (video, audio) if c)

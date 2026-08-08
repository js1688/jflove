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
        try:
            import imageio_ffmpeg
            _ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as e:  # pragma: no cover - 依赖缺失场景
            logger.warning("imageio-ffmpeg 不可用，媒体修复自动关闭：%s", e)
            _ffmpeg_exe = None
    return _ffmpeg_exe if isinstance(_ffmpeg_exe, str) and _ffmpeg_exe else None


def _moov_at_front(file_path: str, head_bytes: int = 64 * 1024) -> bool:
    """
    检查 MP4 文件 moov box 是否位于文件前部（可流式播放）。

    通过解析头部 box 结构判断：首个非 ftyp box 为 moov（fMP4 / faststart）
    → 可流式；首个非 ftyp box 为 mdat → moov 在文件尾部，MSE 无法边下边播，
    需要修复。避免"文件小于读取窗口时 b'moov' 必然命中"的误判。

    :param file_path: 文件绝对路径
    :param head_bytes: 读取的头部字节数
    :returns: moov 位于前部返回 True；无法解析/读取出错返回 False
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

    # 扫描头部 box，判断首个业务 box 是否为 moov
    while pos + 8 <= size:
        box_size = struct.unpack(">I", head[pos:pos + 4])[0]
        box_type = head[pos + 4:pos + 8]
        if box_type == b"moov":
            return True
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
        logger.warning("媒体探测超时/失败：%s", e)
        return {
            "available": bool(exe),
            "has_stream": False,
            "duration_ok": False,
            "fatal": True,
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
    result = {
        "available": bool(exe),
        "has_stream": has_stream,
        "duration_ok": duration_ok,
        "fatal": fatal,
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
    判断文件是否为"浏览器 MSE 可直接播放的标准流式格式"（健康）。

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


def needs_repair(filename: str, probe: dict, file_path: str) -> bool:
    """
    判断文件是否需要修复（供修复服务使用）。

    :param filename: 文件名
    :param probe: probe_media 的返回结果
    :param file_path: 文件绝对路径
    :returns: 需要修复返回 True
    """
    return not is_mse_friendly(filename, probe, file_path)


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

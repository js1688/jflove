"""
媒体修复服务（v1.4.0 新增）

在服务端对"损坏 / 非流式"媒体文件做无损修复，使浏览器 MSE 可正常边下边播：

  - 健康文件：零处理，直接原文件字节 range 流式（stream_mode="byte"）
  - 需修复文件：ffmpeg `-c copy` 重封装为 fMP4，**stdout 管道直出（不落盘）**，
    stream_mode="time"，按时间 range（range_start_seconds）输出
  - 重编码降级：`-c copy` 失败且 `media_repair_allow_transcode="1"` 时，
    降级重编码（libx264 veryfast，不限制分辨率）

安全约束：
  - 修复只读源文件，**永不写回原始文件**
  - ffmpeg 走 asyncio 子进程；客户端中断时 kill + wait 回收，避免僵尸/孤儿进程
  - 动态并发控制（并发计数 + asyncio.Condition），配置可调、写后立即生效
  - ffmpeg 命令使用参数列表（无 shell 拼接），路径已由调用方 _safe_join 校验
"""

import asyncio
import json

import aiosqlite

from src.config.settings import (
    MEDIA_REPAIR_CONCURRENT_HARD_MAX,
    MEDIA_REPAIR_CONCURRENT_KEY,
    MEDIA_REPAIR_ENABLED_KEY,
    MEDIA_REPAIR_NO_OUTPUT_TIMEOUT,
    MEDIA_REPAIR_PIPE_CHUNK_SIZE,
    MEDIA_REPAIR_AUTO_CONCURRENT_BASE,
    MEDIA_REPAIR_TRANSCODE_KEY,
    MEDIA_REPAIR_WAIT_TIMEOUT,
)
from src.services import config_service
from src.utils import media_probe
from src.utils.crypto import encrypt_stream_chunk
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 动态并发控制状态（模块级）──────────────────────────────
# asyncio.Semaphore 创建后 value 不可改，无法“配置变更立即生效”；
# 且释放路径可能在生成器 finally（GeneratorExit）中同步执行，不能使用
# 需要 await 的 Condition。故采用「整数计数 + 轮询等待」：
#   - 计数增减为同步 int 操作（GIL 原子），可在 finally/中断中安全执行
#   - 每次获取槽位前读取最新并发上限 → 配置变更立即生效
_running_ffmpeg = 0


# ── 配置读取（config 表 + 内存缓存，写后立即生效）────────────

async def is_repair_enabled(db: aiosqlite.Connection) -> bool:
    """读取媒体修复总开关（config 表，默认关闭）。"""
    value = await config_service.get(db, MEDIA_REPAIR_ENABLED_KEY, "0")
    return value == "1"


async def is_transcode_enabled(db: aiosqlite.Connection) -> bool:
    """读取重编码子开关（config 表，默认关闭）。"""
    value = await config_service.get(db, MEDIA_REPAIR_TRANSCODE_KEY, "0")
    return value == "1"


async def get_concurrent_limit(db: aiosqlite.Connection) -> int:
    """
    读取修复并发数上限。

    管理员配置（media_repair_max_concurrent，1~硬上限）优先；未配置时使用自动基线
    （按 CPU 核数推导，见 settings.MEDIA_REPAIR_AUTO_CONCURRENT_BASE）。

    :param db: 数据库连接
    :returns: 并发数上限（≥1，≤ MEDIA_REPAIR_CONCURRENT_HARD_MAX）
    """
    raw = await config_service.get(db, MEDIA_REPAIR_CONCURRENT_KEY, default=None)
    if raw and raw.strip().isdigit():
        return max(1, min(int(raw.strip()), MEDIA_REPAIR_CONCURRENT_HARD_MAX))
    return MEDIA_REPAIR_AUTO_CONCURRENT_BASE


async def _acquire_slot(db: aiosqlite.Connection) -> None:
    """
    获取一个修复并发名额（超过上限则轮询等待，带超时）。

    每次读取最新并发上限，配置变更后下一次获取立即生效。

    :param db: 数据库连接
    :raises TimeoutError: 等待超过 MEDIA_REPAIR_WAIT_TIMEOUT 仍无空位（M3 修复：防挂起）
    """
    global _running_ffmpeg
    waited = 0.0
    while True:
        limit = await get_concurrent_limit(db)
        if _running_ffmpeg < limit:
            _running_ffmpeg += 1
            return
        await asyncio.sleep(0.05)
        waited += 0.05
        if waited >= MEDIA_REPAIR_WAIT_TIMEOUT:
            raise TimeoutError("媒体修复队列繁忙，请稍后重试")


def _release_slot() -> None:
    """释放一个修复并发名额（同步操作，可在生成器 finally/中断中安全调用）。"""
    global _running_ffmpeg
    _running_ffmpeg = max(0, _running_ffmpeg - 1)


# ── 健康判定与播放模式决策 ─────────────────────────────────

async def ensure_playable(
    db: aiosqlite.Connection,
    file_path: str,
    filename: str,
) -> dict:
    """
    判定文件的播放模式（修复开关开启时执行）。

    :param db: 数据库连接
    :param file_path: 已由 file_service 权限/路径校验的文件绝对路径
    :param filename: 文件名（用于扩展名推断）
    :returns: 决策字典：
        - {"mode": "byte"}：健康文件，原文件字节 range 直接流式（零处理）
        - {"mode": "time"}：需修复文件，走 ffmpeg 管道时间 range 流
        - {"mode": "error", "message": str}：无法修复/无法解析，提示下载查看
    """
    if not await is_repair_enabled(db):
        return {"mode": "byte"}

    if not media_probe.get_ffmpeg_exe():
        return {"mode": "byte"}

    probe = await media_probe.probe_media(file_path)
    if media_probe.is_mse_friendly(filename, probe, file_path):
        return {"mode": "byte"}

    # 非 MSE 友好：
    #  - 是媒体文件（解析出音/视频流）→ 修复（time）
    #  - 非媒体扩展名（.bin/.txt/.zip 等，修复不适用）→ 回退原文件流（byte），
    #    避免修复开关开启时影响普通文件的下载/预览
    if not probe.get("has_stream"):
        if media_probe.is_repair_supported_extension(filename):
            return {"mode": "error", "message": "无法解析该媒体文件，请下载后查看"}
        return {"mode": "byte"}

    return {"mode": "time"}


# ── ffmpeg 修复管道流（不落盘）──────────────────────────────

def _build_ffmpeg_cmd(
    file_path: str,
    range_start_seconds: float,
    copy: bool,
) -> list[str]:
    """
    构造 ffmpeg 修复命令（参数列表，禁止 shell 拼接，防注入）。

    :param file_path: 源文件绝对路径（只读）
    :param range_start_seconds: 时间 range 起点（秒，0 表示从头）
    :param copy: True 用 `-c copy`（无损重封装）；False 用重编码降级
    :returns: ffmpeg 命令参数列表
    """
    cmd = [
        media_probe.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel", "error",
        "-err_detect", "ignore_err",
        "-fflags", "+genpts",
    ]
    if range_start_seconds > 0:
        # -ss 置于 -i 前：输入 seek（快进，不解码），配合 -c copy 高效
        cmd += ["-ss", f"{range_start_seconds:.3f}"]
    cmd += ["-i", file_path]
    # 可选流映射：仅取首路视频 + 首路音频（若无则不报错），容错损坏文件
    cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]
    if copy:
        cmd += ["-c", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy"]
    # fMP4（fragmented MP4）：MSE 兼容性最佳，且支持 stdout 管道输出（不落盘）
    cmd += [
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1",
    ]
    return cmd


async def _read_pipe_chunk(
    proc: asyncio.subprocess.Process, chunk_size: int
) -> bytes | None:
    """
    读取 ffmpeg stdout 一块数据（带无输出超时保护）。

    :param proc: ffmpeg 子进程
    :param chunk_size: 读取字节数
    :returns: 读取到的字节；流结束返回 None
    :raises TimeoutError: 超时未读到数据（视为 ffmpeg 卡死）
    """
    try:
        chunk = await asyncio.wait_for(
            proc.stdout.read(chunk_size), timeout=MEDIA_REPAIR_NO_OUTPUT_TIMEOUT
        )
    except asyncio.TimeoutError:
        raise TimeoutError("ffmpeg 长时间无输出，已终止")
    return chunk or None


async def _run_ffmpeg_pipe(
    file_path: str,
    range_start_seconds: float,
    copy: bool,
) -> asyncio.subprocess.Process:
    """启动一个 ffmpeg 修复子进程（stdout=PIPE，stderr 丢弃）。"""
    cmd = _build_ffmpeg_cmd(file_path, range_start_seconds, copy)
    return await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


def _kill_proc_sync(proc: asyncio.subprocess.Process | None) -> None:
    """
    同步终止 ffmpeg 子进程（不 await），防止孤儿进程。

    可在生成器 finally / GeneratorExit（客户端中断）中安全调用。
    进程退出后由 asyncio child watcher 自动 waitpid 回收，不产生僵尸。
    """
    if proc is None:
        return
    if proc.returncode is None:
        try:
            proc.kill()
        except (ProcessLookupError, RuntimeError):
            pass


async def _reap_proc(proc: asyncio.subprocess.Process | None) -> None:
    """显式回收已终止的 ffmpeg 子进程（正常路径调用，保证退出码可用）。"""
    if proc is None or proc.returncode is not None:
        return
    try:
        await proc.wait()
    except (ProcessLookupError, RuntimeError):
        pass


async def stream_repaired_frames(
    db: aiosqlite.Connection,
    file_path: str,
    session_key: bytes,
    range_start_seconds: float,
    allow_transcode: bool,
) -> None:
    """
    修复模式流式生成器：ffmpeg stdout 管道 → 逐块 ChaCha20 加密帧。

    供 StreamingResponse 使用；本函数为 async generator，被取消（客户端中断）
    时在 finally 中终止 ffmpeg 子进程并回收。

    :param db: 数据库连接（读取并发上限）
    :param file_path: 源文件绝对路径（只读）
    :param session_key: 会话密钥
    :param range_start_seconds: 时间 range 起点（秒）
    :param allow_transcode: 是否允许 `-c copy` 失败时降级重编码
    """
    # 帧 0：元数据（stream_mode="time"，客户端据此走时间 range）
    meta = {
        "type": "meta",
        "stream_mode": "time",
        "content_type": "video/mp4",
        "range_start_seconds": range_start_seconds,
    }
    yield encrypt_stream_chunk(
        session_key, json.dumps(meta, ensure_ascii=False).encode()
    )

    proc: asyncio.subprocess.Process | None = None
    try:
        await _acquire_slot(db)
        try:
            # 首选 -c copy（无损）
            proc = await _run_ffmpeg_pipe(file_path, range_start_seconds, copy=True)
            first = await _read_pipe_chunk(proc, MEDIA_REPAIR_PIPE_CHUNK_SIZE)

            # -c copy 启动即失败（无输出且进程已退出）→ 允许时降级重编码
            if first is None and proc.returncode is not None and proc.returncode != 0:
                _kill_proc_sync(proc)
                await _reap_proc(proc)
                proc = None
                if allow_transcode:
                    logger.info("媒体修复 -c copy 失败，降级重编码（不限制分辨率）")
                    proc = await _run_ffmpeg_pipe(
                        file_path, range_start_seconds, copy=False
                    )
                    first = await _read_pipe_chunk(
                        proc, MEDIA_REPAIR_PIPE_CHUNK_SIZE
                    )

            if first is None:
                # 彻底失败（无输出）→ 记录并结束（客户端收到流结束，触发下载提示）
                logger.warning("媒体修复失败：无法生成可播放流")
                return

            yield encrypt_stream_chunk(session_key, first)

            # 后续数据块
            while True:
                chunk = await _read_pipe_chunk(proc, MEDIA_REPAIR_PIPE_CHUNK_SIZE)
                if chunk is None:
                    break
                yield encrypt_stream_chunk(session_key, chunk)

            await _reap_proc(proc)
            if proc and proc.returncode != 0:
                logger.warning("媒体修复中途退出，returncode=%s", proc.returncode)
        finally:
            _release_slot()
            _kill_proc_sync(proc)
    except asyncio.CancelledError:
        # 客户端中断：保持取消语义（finally 会 kill 子进程）
        raise
    except TimeoutError as e:
        # 超时（ffmpeg 卡死 / 并发队列繁忙，M3）：终止子进程并结束流，
        # 客户端收到流提前结束（比无限挂起友好）
        logger.warning("媒体修复终止（超时/繁忙）：%s", e)
    finally:
        # 兜底：客户端中断（GeneratorExit）时同步杀进程，防止孤儿 ffmpeg
        _kill_proc_sync(proc)

"""
手动离线媒体修复任务服务（v1.4.2 新增）

架构变更说明（相对 v1.4.0/v1.4.1 的实时修复）：
  - 播放路径零 ffmpeg：/stream 只输出健康文件；损坏文件 415 + MEDIA_NEEDS_REPAIR
  - 修复改为手动触发的异步任务：用户在文件管理右键「修复损坏媒体」创建任务，
    服务端队列 worker 执行 ffmpeg 无损重封装，产物落盘于原文件同目录
    .jflove-repair/ 下
  - 产物可验证播放、可在用户二次确认后覆盖原文件（os.replace 原子替换，
    直接覆盖不留备份）

任务全平台共享（不做账户归属隔离）：避免多账号重复修复同一文件；
操作权限统一为磁盘 write+delete（与创建者无关）。

生命周期：pending → running → verifying → success/failed/canceled；
success --覆盖--> overridden。服务重启时未终态任务标记 failed 并清理半成品。

安全约束：
  - 产物路径由 _safe_join 派生，不接受客户端传路径；隐藏目录路径段访问拒绝
  - 日志不记录路径/文件名明文（只记 task_id / disk_id）
  - ffmpeg 参数列表形式（无 shell 拼接），stderr 丢弃
"""

from __future__ import annotations

import asyncio
import glob
import os
from datetime import datetime, timezone

import aiosqlite

from src.config.settings import (
    MEDIA_REPAIR_NO_OUTPUT_TIMEOUT,
    REPAIR_DIR_NAME,
    REPAIR_OUTPUT_EXT,
    REPAIR_PROGRESS_THROTTLE,
    REPAIR_TEMP_SUFFIX,
)
from src.repositories import repair_task_repository, virtual_disk_repository
from src.services.permission_service import check_disk_permission
from src.services.file_service import _safe_join
from src.utils import media_probe
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 任务队列与 worker 协程（start_workers 启动 / stop_workers 停止）
_queue: asyncio.Queue[int] | None = None
_workers: list[asyncio.Task] = []
_worker_stop = False
# 取消信号：task_id -> asyncio.Event（worker 执行循环中检测，触发后终止 ffmpeg）
_cancel_events: dict[int, asyncio.Event] = {}


# ── 路径与权限辅助 ─────────────────────────────────────────

def _repair_dir_for(base: str, rel_path: str) -> str:
    """
    计算原文件所在目录的修复隐藏目录绝对路径（不创建目录）。

    :param base: 虚拟磁盘根目录绝对路径
    :param rel_path: 原文件所在目录（磁盘内相对路径）
    """
    return os.path.join(_safe_join(base, rel_path), REPAIR_DIR_NAME)


def _artifact_stem(filename: str) -> str:
    """原文件名去扩展名（产物 stem 基础）。"""
    return os.path.splitext(filename)[0] or filename


def _next_output_name(repair_dir: str, filename: str) -> str:
    """
    计算产物文件名：默认 {stem}.mp4；同 stem 已存在（如 a.ts 与 a.avi 同目录）
    则追加序号 a.1.mp4、a.2.mp4，保证能分清文件归属（2026-08-28 用户确认）。
    """
    stem = _artifact_stem(filename)
    candidate = f"{stem}{REPAIR_OUTPUT_EXT}"
    existing = set(os.listdir(repair_dir)) if os.path.isdir(repair_dir) else set()
    seq = 1
    while candidate in existing:
        candidate = f"{stem}.{seq}{REPAIR_OUTPUT_EXT}"
        seq += 1
    return candidate


async def has_repair_permission(
    db: aiosqlite.Connection, user_id: int, role: str, disk_id: int
) -> bool:
    """
    修复操作权限门槛：写权限 + 删除权限并存（admin 天然满足）。

    写用于产物写入隐藏目录；删除用于覆盖时替换原文件（2026-08-28 用户确认）。
    """
    if role == "admin":
        return True
    ok_write = await check_disk_permission(db, user_id, disk_id, "write")
    ok_delete = await check_disk_permission(db, user_id, disk_id, "delete")
    return bool(ok_write and ok_delete)


async def _disk_base(db: aiosqlite.Connection, disk_id: int) -> str:
    """取虚拟磁盘根路径，不存在抛 ValueError。"""
    disk = await virtual_disk_repository.find_by_id(db, disk_id)
    if not disk:
        raise ValueError("虚拟磁盘不存在")
    return disk["real_path"]


# ── 任务生命周期操作（controller 调用）────────────────────

async def create_task(
    db: aiosqlite.Connection,
    user_id: int,
    username: str,
    role: str,
    disk_id: int,
    rel_path: str,
    filename: str,
) -> dict:
    """
    创建修复任务（入口校验 + 入队）。

    :returns: {"task_id": int, "message": str}
    :raises PermissionError: 无写+删权限 / 路径越界
    :raises ValueError: 文件不存在 / 健康无需修复 / 无法修复 / 已有任务进行中
    """
    if not filename:
        raise ValueError("filename 不能为空")
    if not await has_repair_permission(db, user_id, role, disk_id):
        raise PermissionError("修复需要磁盘写权限与删除权限")

    base = await _disk_base(db, disk_id)
    file_path = _safe_join(base, os.path.join(rel_path.lstrip("/"), filename))
    if not os.path.isfile(file_path):
        raise ValueError("文件不存在")

    # 健康判定（probe 结果短 TTL 缓存复用，60s 内同文件零重复探测）
    probe = await media_probe.probe_media(file_path)
    if not probe.get("available"):
        raise ValueError("无法解析该媒体文件（探测失败）")
    if media_probe.is_mse_friendly(filename, probe, file_path):
        raise ValueError("该文件无需修复")
    if not probe.get("has_stream"):
        raise ValueError("该文件无法修复，请下载后查看")

    # 同文件互斥：存在任意未删除任务记录则拒绝（重复修复拦截，
    # 必须先删除记录后才能重新发起修复）。
    existing = await repair_task_repository.list_by_file(
        db, disk_id, rel_path, filename
    )
    if existing:
        raise ValueError("该文件已有修复任务，请先删除任务记录后再修复")

    # 失败重试不留垃圾：清理该文件历史任务产物与半成品（按任务记录精确清理）
    await _cleanup_file_artifacts(db, disk_id, rel_path, filename)

    task_id = await repair_task_repository.insert(
        db, user_id, username, disk_id, rel_path, filename,
        os.path.getsize(file_path),
    )
    if _queue is not None:
        _queue.put_nowait(task_id)
    logger.info("媒体修复任务已创建: task_id=%s disk_id=%s", task_id, disk_id)
    return {"task_id": task_id, "message": "已加入修复队列"}


async def cancel_task(
    db: aiosqlite.Connection, user_id: int, role: str, task_id: int
) -> None:
    """
    取消任务（pending 直接置 canceled；running/verifying 置信号由 worker 清理）。

    :raises PermissionError: 无写+删权限
    :raises ValueError: 任务不存在 / 状态不可取消
    """
    task = await repair_task_repository.find_by_id(db, task_id)
    if not task:
        raise ValueError("任务不存在")
    if not await has_repair_permission(db, user_id, role, task["disk_id"]):
        raise PermissionError("修复需要磁盘写权限与删除权限")
    if task["status"] == "pending":
        await repair_task_repository.update_status(db, task_id, "canceled")
        return
    if task["status"] in ("running", "verifying"):
        ev = _cancel_events.get(task_id)
        if ev is None:
            ev = asyncio.Event()
            _cancel_events[task_id] = ev
        ev.set()
        return
    raise ValueError("任务已结束，无法取消")


async def override_origin(
    db: aiosqlite.Connection, user_id: int, role: str, task_id: int
) -> None:
    """
    覆盖原文件：产物 os.replace 到原文件位置（原子替换=删除原文件+顶替，不留备份）。

    :raises PermissionError: 无写+删权限
    :raises ValueError: 任务非 success / 产物缺失 / 原文件不存在
    :raises OSError: 替换失败（产物保留、原文件不动，任务状态不变）
    """
    task = await repair_task_repository.find_by_id(db, task_id)
    if not task:
        raise ValueError("任务不存在")
    if task["status"] != "success":
        raise ValueError("仅修复成功的任务可覆盖")
    if not await has_repair_permission(db, user_id, role, task["disk_id"]):
        raise PermissionError("修复需要磁盘写权限与删除权限")

    base = await _disk_base(db, task["disk_id"])
    repair_dir = _repair_dir_for(base, task["rel_path"])
    artifact = os.path.join(repair_dir, task["output_name"])
    origin = _safe_join(
        base, os.path.join(task["rel_path"].lstrip("/"), task["filename"])
    )
    if not os.path.isfile(artifact):
        raise ValueError("修复产物不存在（可能已被删除）")
    if not os.path.isfile(origin):
        raise ValueError("原文件不存在（可能已被移动或删除）")

    os.replace(artifact, origin)
    await repair_task_repository.update_status(db, task_id, "overridden")
    _remove_dir_if_empty(repair_dir)
    logger.info("修复产物已覆盖原文件: task_id=%s disk_id=%s", task_id, task["disk_id"])


async def delete_artifact(
    db: aiosqlite.Connection, user_id: int, role: str, task_id: int
) -> None:
    """
    删除产物（success 未覆盖时可删）；隐藏目录空则清理目录。

    :raises PermissionError: 无写+删权限
    :raises ValueError: 任务不存在 / 状态不允许 / 产物不存在
    """
    task = await repair_task_repository.find_by_id(db, task_id)
    if not task:
        raise ValueError("任务不存在")
    if task["status"] != "success":
        raise ValueError("仅修复成功的任务存在可删除产物")
    if not await has_repair_permission(db, user_id, role, task["disk_id"]):
        raise PermissionError("修复需要磁盘写权限与删除权限")

    base = await _disk_base(db, task["disk_id"])
    repair_dir = _repair_dir_for(base, task["rel_path"])
    artifact = os.path.join(repair_dir, task["output_name"])
    # 产物存在才删；不存在（可能被人工挪走）也视为删除成功，不报错
    if os.path.isfile(artifact):
        os.remove(artifact)
    _remove_dir_if_empty(repair_dir)
    logger.info("修复产物已删除: task_id=%s", task_id)


def task_row_to_dict(row: aiosqlite.Row) -> dict:
    """任务行 → API 响应字典（不暴露服务端路径，只回磁盘 ID 与文件名）。"""
    return {
        "id": row["id"],
        "username": row["username"],
        "disk_id": row["disk_id"],
        "filename": row["filename"],
        "status": row["status"],
        "progress": row["progress"],
        "error_message": row["error_message"],
        "source_size": row["source_size"],
        "output_name": row["output_name"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


# ── worker 队列（应用 lifespan 调用）───────────────────────

async def start_workers(db_factory) -> None:
    """
    启动修复队列与 worker 协程池（应用 lifespan 调用）。

    :param db_factory: 异步上下文管理器工厂，产出独立 aiosqlite 连接
        （worker 与请求级连接生命周期解耦）
    """
    global _queue, _worker_stop
    _worker_stop = False
    _queue = asyncio.Queue()

    # 服务重启恢复：未终态任务标记 failed + 清理 *.repairing.mp4 半成品
    async with db_factory() as db:
        interrupted = await repair_task_repository.mark_interrupted_as_failed(db)
        if interrupted:
            logger.info("服务重启：%s 个中断修复任务已标记失败", interrupted)
        await _cleanup_temp_artifacts(db)

    async with db_factory() as db:
        limit = await _current_limit(db)

    async def _worker() -> None:
        while not _worker_stop:
            task_id = await _queue.get()
            try:
                async with db_factory() as db:
                    await _run_task(db, task_id)
            except Exception as e:  # noqa: BLE001 - worker 兜底，防队列整体崩溃
                logger.error(
                    "修复任务执行异常: task_id=%s %s", task_id, type(e).__name__
                )
            finally:
                _queue.task_done()

    for _ in range(max(1, limit)):
        _workers.append(asyncio.create_task(_worker()))
    logger.info("媒体修复队列已启动: workers=%s", max(1, limit))


async def stop_workers() -> None:
    """停止全部 worker（应用 shutdown 调用）；在途 ffmpeg 由系统随进程退出。"""
    global _worker_stop
    _worker_stop = True
    for w in _workers:
        w.cancel()
    _workers.clear()


async def _current_limit(db: aiosqlite.Connection) -> int:
    """读取修复并发上限（复用 v1.4.0 配置键 media_repair_max_concurrent）。"""
    # 延迟导入避免与 media_repair_service 的模块级初始化相互依赖
    from src.services.media_repair_service import get_concurrent_limit

    return await get_concurrent_limit(db)


async def _run_task(db: aiosqlite.Connection, task_id: int) -> None:
    """执行单个修复任务：running → ffmpeg 写临时产物 → verifying → success/failed。"""
    task = await repair_task_repository.find_by_id(db, task_id)
    if not task or task["status"] != "pending":
        return  # 已被取消或删除
    cancel_ev = asyncio.Event()
    _cancel_events[task_id] = cancel_ev
    repair_dir = ""
    try:
        await repair_task_repository.update_status(db, task_id, "running")
        base = await _disk_base(db, task["disk_id"])
        origin = _safe_join(
            base, os.path.join(task["rel_path"].lstrip("/"), task["filename"])
        )
        if not os.path.isfile(origin):
            await repair_task_repository.update_status(
                db, task_id, "failed", "原文件不存在"
            )
            return
        repair_dir = _repair_dir_for(base, task["rel_path"])
        os.makedirs(repair_dir, exist_ok=True)
        output_name = _next_output_name(repair_dir, task["filename"])
        temp_path = os.path.join(
            repair_dir, f"{_artifact_stem(task['filename'])}{REPAIR_TEMP_SUFFIX}"
        )
        _remove_file(temp_path)  # 防御：清掉同名残留半成品

        proc = await asyncio.create_subprocess_exec(
            *_build_repair_cmd(origin, temp_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        outcome = await _wait_ffmpeg(db, task_id, task, proc, temp_path, cancel_ev)
        if outcome != "ok":
            # canceled / failed / stalled：状态已写库，半成品由 _wait_ffmpeg 清理
            return

        # rename 为正式产物 → 自动验证（能解析出流、无致命错、有时长）
        artifact = os.path.join(repair_dir, output_name)
        os.replace(temp_path, artifact)
        await repair_task_repository.update_status(
            db, task_id, "verifying", output_name=output_name
        )
        probe = await media_probe.probe_media(artifact)
        # v1.4.2 hotfix：ffmpeg frag_keyframe+faststart 回写的 mvhd/tkhd/mdhd
        # duration 是首个分片时长而非总时长，需按真实总时长修正，否则浏览器
        # 视频总时长、进度条拖拽与 seek 全部异常。
        if probe.get("duration", 0) > 0:
            media_probe.patch_mp4_durations(artifact, probe["duration"])
        verified = (
            probe.get("has_stream")
            and not probe.get("fatal")
            and probe.get("duration", 0) > 0
        )
        if verified:
            await repair_task_repository.update_progress(db, task_id, 100)
            await repair_task_repository.update_status(db, task_id, "success")
            logger.info("媒体修复完成: task_id=%s", task_id)
        else:
            os.remove(artifact)
            _remove_dir_if_empty(repair_dir)
            await repair_task_repository.update_status(
                db, task_id, "failed", "修复产物验证未通过", output_name=""
            )
            logger.warning("媒体修复验证未通过: task_id=%s", task_id)
    except (PermissionError, ValueError) as e:
        await repair_task_repository.update_status(db, task_id, "failed", str(e))
    except OSError:
        logger.error("修复任务 IO 异常: task_id=%s", task_id)
        await repair_task_repository.update_status(
            db, task_id, "failed", "修复过程发生 IO 错误"
        )
    finally:
        _cancel_events.pop(task_id, None)
        if repair_dir:
            _remove_dir_if_empty(repair_dir)


async def _wait_ffmpeg(
    db: aiosqlite.Connection,
    task_id: int,
    task: aiosqlite.Row,
    proc: asyncio.subprocess.Process,
    temp_path: str,
    cancel_ev: asyncio.Event,
) -> str:
    """
    等待 ffmpeg 完成，期间处理取消检测与进度上报。

    :returns: "ok"（正常退出且产物非空）/ "canceled" / "failed"（均已完成状态写库与清理）
    """
    source_size = max(1, task["source_size"])
    last_progress_write = 0.0
    last_growth = datetime.now(timezone.utc).timestamp()
    last_size = 0
    while proc.returncode is None:
        if cancel_ev.is_set():
            _kill(proc)
            _remove_file(temp_path)
            await repair_task_repository.update_status(db, task_id, "canceled")
            return "canceled"
        await asyncio.sleep(0.5)
        now = datetime.now(timezone.utc).timestamp()
        try:
            size = os.path.getsize(temp_path)
        except OSError:
            size = last_size
        # 进度上报（节流 ≥ REPAIR_PROGRESS_THROTTLE 秒，上限 99 留给验证完成）
        if now - last_progress_write >= REPAIR_PROGRESS_THROTTLE:
            last_progress_write = now
            await repair_task_repository.update_progress(
                db, task_id, min(99, int(size / source_size * 100))
            )
        # 停滞检测：超过 MEDIA_REPAIR_NO_OUTPUT_TIMEOUT 秒产物无增长视为卡死
        if size > last_size:
            last_size = size
            last_growth = now
        elif now - last_growth > MEDIA_REPAIR_NO_OUTPUT_TIMEOUT:
            _kill(proc)
            _remove_file(temp_path)
            await repair_task_repository.update_status(
                db, task_id, "failed", "修复执行超时（长时间无输出）"
            )
            return "failed"
    await proc.wait()
    if cancel_ev.is_set():
        _remove_file(temp_path)
        await repair_task_repository.update_status(db, task_id, "canceled")
        return "canceled"
    if (
        proc.returncode != 0
        or not os.path.isfile(temp_path)
        or os.path.getsize(temp_path) == 0
    ):
        _remove_file(temp_path)
        await repair_task_repository.update_status(
            db, task_id, "failed", "修复执行失败（ffmpeg 退出异常）"
        )
        return "failed"
    return "ok"


def _build_repair_cmd(src: str, dst: str) -> list[str]:
    """
    构造离线修复 ffmpeg 命令（输出到文件）。

    参数语义沿用 v1.4.0 实时修复：视频无损 copy、音频统一转 AAC
    （TS/MKV 来源的 mp4a esds 常缺 DecoderSpecificInfo，转 AAC 后完整可解）。
    离线产物用 **分片 fMP4**（moov 只含 mvex + moof/mdat 分片）——
      - `+empty_moov`：moov 为空壳（仅 ftyp/mvex），分片 fMP4 的标准结构；
        **不能用 `+faststart`**（与 `frag_keyframe` 组合会丢失首个 moof，
        产生孤儿 mdat 且时间戳不从 0 开始，导致 MSE 闪屏/只播后半段）
      - `+frag_keyframe`：按关键帧分片
      - `+default_base_moof`：moof 相对寻址，tfhd 不带 base-data-offset
        （否则 Chrome MSE 报「TFHD base-data-offset not allowed by MSE」）
    视频 `-c:v copy` 无损不丢清晰度；mvhd/tkhd/mdhd duration 由
    patch_mp4_durations 在产物落盘后按真实总时长修正。
    """
    return [
        media_probe.get_ffmpeg_exe() or "ffmpeg",
        "-hide_banner", "-loglevel", "error", "-y",
        "-err_detect", "ignore_err", "-fflags", "+genpts",
        "-i", src,
        "-map", "0:v:0?", "-map", "0:a:0?",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+empty_moov+frag_keyframe+default_base_moof",
        "-f", "mp4", dst,
    ]


# ── 文件系统清理辅助 ───────────────────────────────────────

def _kill(proc: asyncio.subprocess.Process | None) -> None:
    """同步终止子进程（不存在/已退出时静默）。"""
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.kill()
    except (ProcessLookupError, RuntimeError):
        pass


def _remove_file(path: str) -> None:
    """删除文件（不存在时静默）。"""
    try:
        os.remove(path)
    except OSError:
        pass


def _remove_dir_if_empty(path: str) -> None:
    """目录存在且为空则删除（隐藏目录不留垃圾）。"""
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except OSError:
        pass


async def _cleanup_file_artifacts(
    db: aiosqlite.Connection, disk_id: int, rel_path: str, filename: str
) -> None:
    """
    按任务记录精确清理该文件的全部历史产物与半成品（失败重试不留垃圾）。

    只删该 (disk_id, rel_path, filename) 历史任务登记过的 output_name 与
    同 stem 的 *.repairing.mp4，不影响同 stem 其他文件（如 a.ts 与 a.backup.ts）
    的产物。
    """
    rows = await repair_task_repository.list_by_file(db, disk_id, rel_path, filename)
    if not rows:
        return
    try:
        base = await _disk_base(db, disk_id)
        repair_dir = _repair_dir_for(base, rel_path)
    except (ValueError, PermissionError):
        return
    for row in rows:
        if row["output_name"]:
            _remove_file(os.path.join(repair_dir, row["output_name"]))
    for temp in glob.glob(
        os.path.join(repair_dir, f"{glob.escape(_artifact_stem(filename))}*{REPAIR_TEMP_SUFFIX}")
    ):
        _remove_file(temp)
    _remove_dir_if_empty(repair_dir)


async def _cleanup_temp_artifacts(db: aiosqlite.Connection) -> None:
    """
    服务启动时清理所有历史任务目录下的 *.repairing.mp4 半成品。

    按任务表登记的 (disk_id, rel_path) 去重后逐目录清理，不做全盘扫描。
    """
    dirs = await repair_task_repository.list_distinct_dirs(db)
    if not dirs:
        return
    roots = {
        d["id"]: d["real_path"] for d in await virtual_disk_repository.list_all(db)
    }
    for disk_id, rel_path in dirs:
        base = roots.get(disk_id)
        if not base:
            continue
        try:
            repair_dir = _repair_dir_for(base, rel_path)
        except PermissionError:
            continue
        for temp in glob.glob(os.path.join(glob.escape(repair_dir), f"*{REPAIR_TEMP_SUFFIX}")):
            _remove_file(temp)
        _remove_dir_if_empty(repair_dir)

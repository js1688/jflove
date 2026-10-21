"""
离线下载任务编排（v1.8.0 新增 / S5）

设计依据：`文档记录/设计文档/v1.8.0.md` §2.2 / §2.5 / §2.6。

职责：
  - 账号级默认目录（D9）与全局策略（config）的读取/校验；
  - 分析队列 + 下载队列（**与媒体修复完全独立**）；
  - 任务状态机、取消、重试、图片分页、重启兜底、惰性过期、临时目录清理；
  - 调用 S3 分析器与 S4 下载器，把结果落库。

安全口径：
  - 所有任务查询走 `offline_download_repository.find_task(task_id, user_id)`，他人任务一律 404；
  - 落盘只接受「虚拟磁盘 + 相对路径」，每次 select/下载开始前都复检磁盘写权限（防 TOCTOU）；
  - 日志只记 task_id / 错误码，不记完整 URL / 文件路径。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import aiosqlite

from src.config.settings import (
    OFFLINE_ANALYSIS_TTL,
    OFFLINE_DEFAULT_MAX_CONCURRENT,
    OFFLINE_DEFAULT_MAX_FILE_SIZE_MB,
    OFFLINE_DEFAULT_MAX_IMAGES,
    OFFLINE_DEFAULT_MAX_ITEMS,
    OFFLINE_DEEP_ANALYZE_KEY,
    OFFLINE_DIR_NAME,
    OFFLINE_DISK_SAFETY_FACTOR,
    OFFLINE_ENABLED_KEY,
    OFFLINE_HARD_MAX_CONCURRENT,
    OFFLINE_HARD_MAX_FILE_SIZE_MB,
    OFFLINE_HARD_MAX_IMAGES,
    OFFLINE_HARD_MAX_ITEMS,
    OFFLINE_MAX_CONCURRENT_KEY,
    OFFLINE_MAX_FILE_SIZE_KEY,
    OFFLINE_MAX_IMAGES_KEY,
    OFFLINE_MAX_ITEMS_KEY,
    OFFLINE_MAX_IMAGE_PAGES,
    OFFLINE_PROGRESS_THROTTLE,
    OFFLINE_QUEUE_MAX,
)
from src.repositories import (
    offline_download_repository as repo,
    permission_repository,
    virtual_disk_repository,
)
from src.services import config_service, permission_service
from src.services.media_analyzer_service import (
    BROWSER_MANAGER,
    AnalysisError,
    AnalysisResult,
    MediaAnalyzerService,
)
from src.services.media_fetcher_service import (
    FetchError,
    MediaFetcherService,
    cleanup_task_temp,
)
from src.utils.logger import get_logger
from src.utils.outbound_http import OutboundClient
from src.utils.path_safety import safe_join
from src.utils.ssrf_guard import SsrfBlocked, validate_url

logger = get_logger(__name__)

ANALYSIS_WORKERS = 2
DOWNLOAD_WORKERS = 4

_STATUS_TEXT = {
    "queued": "排队中",
    "analyzing": "分析中",
    "ready": "待选择",
    "downloading": "下载中",
    "merging": "合并中",
    "success": "已完成",
    "failed": "失败",
    "canceled": "已取消",
}

_ERROR_MESSAGES = {
    "unsupported_site": "暂不支持这个网站的解析",
    "no_media": "没有在这个页面里找到可下载的内容",
    "deep_analyze_disabled": "当前站点可能需要深度分析，请联系管理员开启",
    "login_required": "该内容需要登录才能访问，本功能不支持登录态站点",
    "drm_protected": "该内容受版权保护（加密流），无法下载",
    "url_expired": "媒体地址已过期，请重新分析",
    "referer_denied": "源站拒绝了请求（防盗链）",
    "net_timeout": "访问源站超时",
    "ssrf_blocked": "该地址不允许访问",
    "disk_full": "磁盘剩余空间不足",
    "too_large": "超出单文件体积上限",
    "target_not_writable": "目标目录不可写",
    "target_not_configured": "请先设置离线下载目录",
    "config_invalid": "离线下载目录已失效，请重新配置",
    "no_writable_disk": "你没有可写入的磁盘，无法使用离线下载",
    "parser_outdated": "解析组件可能已过时，等待服务端升级",
    "content_invalid": "图片内容无法识别（可能经过站点加密）",
    "restart_interrupted": "服务重启导致任务中断",
    "canceled": "已取消",
    "merge_failed": "合并产物失败",
    "http_error": "源站返回异常状态",
    "network_error": "网络连接失败",
    "not_found": "任务不存在",
    "invalid_path": "目录不合法",
    "invalid_state": "任务状态不允许该操作",
    "no_items": "未选择任何项目",
    "queue_full": "分析队列已满",
    "page_limit": "已达抓取页数上限",
    "disabled": "离线下载功能已关闭",
}

_analysis_queue: asyncio.Queue[int] | None = None
_download_queue: asyncio.Queue[int] | None = None
_workers: list[asyncio.Task] = []
_worker_stop = False
_cancel_events: dict[int, asyncio.Event] = {}
_download_semaphore: asyncio.Semaphore | None = None
_download_semaphore_value = 0
#: 自动重分析的并发闸门（与 ANALYSIS_WORKERS 同额度；按事件循环懒加载，测试/多循环安全）
_reanalyze_semaphore: asyncio.Semaphore | None = None
_reanalyze_loop: asyncio.AbstractEventLoop | None = None


class _TaskError(Exception):
    """服务内部业务异常（带稳定错误码）。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or _ERROR_MESSAGES.get(code, "操作失败")
        super().__init__(self.message)


# ── 全局策略 / 账号目录 ─────────────────────────────────────


async def get_policy(db: aiosqlite.Connection) -> dict:
    """读取全局策略（管理员可配，默认开；非法值回落默认并夹到硬边界）。"""

    async def _int(key: str, default: int, low: int, high: int) -> int:
        raw = await config_service.get(db, key, str(default))
        try:
            value = int(str(raw))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    enabled = (await config_service.get(db, OFFLINE_ENABLED_KEY, "1")) == "1"
    deep = (await config_service.get(db, OFFLINE_DEEP_ANALYZE_KEY, "1")) == "1"
    return {
        "enabled": enabled,
        "deep_analyze": deep,
        "max_concurrent": await _int(
            OFFLINE_MAX_CONCURRENT_KEY,
            OFFLINE_DEFAULT_MAX_CONCURRENT,
            1,
            OFFLINE_HARD_MAX_CONCURRENT,
        ),
        "max_file_bytes": await _int(
            OFFLINE_MAX_FILE_SIZE_KEY,
            OFFLINE_DEFAULT_MAX_FILE_SIZE_MB,
            1,
            OFFLINE_HARD_MAX_FILE_SIZE_MB,
        )
        * 1024
        * 1024,
        "max_items": await _int(
            OFFLINE_MAX_ITEMS_KEY,
            OFFLINE_DEFAULT_MAX_ITEMS,
            1,
            OFFLINE_HARD_MAX_ITEMS,
        ),
        "max_images": await _int(
            OFFLINE_MAX_IMAGES_KEY,
            OFFLINE_DEFAULT_MAX_IMAGES,
            1,
            OFFLINE_HARD_MAX_IMAGES,
        ),
    }


async def list_writable_disks(db: aiosqlite.Connection, user: dict) -> list[dict]:
    """返回该账号**有 write 权限**的虚拟磁盘（管理员视为全部）。"""
    disks = await virtual_disk_repository.list_all(db)
    if user.get("role") == "admin":
        return [{"disk_id": int(disk["id"]), "name": disk["name"]} for disk in disks]
    permissions = await permission_repository.get_disk_permissions(db, user["id"])
    writable = {
        int(perm["virtual_disk_id"])
        for perm in permissions
        if bool(perm["can_write"])
    }
    return [
        {"disk_id": int(disk["id"]), "name": disk["name"]}
        for disk in disks
        if int(disk["id"]) in writable
    ]


def _normalize_rel_path(rel_path: str) -> str:
    """校验相对路径：拒绝绝对路径、`..`、隐藏 `.jflove-*` 段。"""
    text = (rel_path or "").strip().replace("\\", "/")
    if text.startswith("/") or os.path.isabs(text):
        raise _TaskError("invalid_path", "目录不合法")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise _TaskError("invalid_path", "目录不合法")
    if any(part.startswith(".jflove-") for part in parts):
        raise _TaskError("invalid_path", "目录不合法")
    return "/".join(parts)


async def _disk_or_raise(db: aiosqlite.Connection, disk_id: int):
    """取磁盘；不存在抛业务异常。"""
    disk = await virtual_disk_repository.find_by_id(db, disk_id)
    if disk is None:
        raise _TaskError("not_found", "虚拟磁盘不存在")
    return disk


async def validate_target(
    db: aiosqlite.Connection,
    user: dict,
    disk_id: int,
    rel_path: str,
    *,
    create: bool = False,
) -> tuple[str, str]:
    """
    校验目标目录并返回 `(磁盘根绝对路径, 目标绝对路径)`。

    :raises _TaskError: 无写权限 / 路径非法 / 目录不存在
    """
    if disk_id <= 0:
        raise _TaskError("target_not_configured")
    disk = await _disk_or_raise(db, disk_id)
    if user.get("role") != "admin" and not await permission_service.check_disk_permission(
        db, user["id"], disk_id, "write"
    ):
        raise _TaskError("target_not_writable")
    rel = _normalize_rel_path(rel_path)
    try:
        target = safe_join(str(disk["real_path"]), rel, forbidden_parts=(OFFLINE_DIR_NAME,))
    except PermissionError as exc:
        raise _TaskError("invalid_path", "目录不合法") from exc
    if create:
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            raise _TaskError("target_not_writable", "目录不可写") from exc
    elif not os.path.isdir(target):
        raise _TaskError("invalid_path", "目录不存在")
    return str(disk["real_path"]), target


async def get_meta(db: aiosqlite.Connection, user: dict) -> dict:
    """客户端初始化元信息（只含**自己**的配置与可写磁盘）。"""
    policy = await get_policy(db)
    disks = await list_writable_disks(db, user)
    setting = await repo.get_setting(db, user["id"])
    config = {"disk_id": 0, "rel_path": "", "valid": False}
    if setting is not None:
        disk_id = int(setting["default_disk_id"])
        rel_path = str(setting["default_rel_path"])
        config = {"disk_id": disk_id, "rel_path": rel_path, "valid": False}
        disk = await virtual_disk_repository.find_by_id(db, disk_id)
        if disk is not None:
            writable = await permission_service.check_disk_permission(
                db, user["id"], disk_id, "write"
            ) if user.get("role") != "admin" else True
            try:
                target = safe_join(
                    str(disk["real_path"]), rel_path, forbidden_parts=(OFFLINE_DIR_NAME,)
                )
            except PermissionError:
                target = ""
            config["valid"] = bool(writable and target and os.path.isdir(target))
    configured = setting is not None and config["valid"]
    if not policy["enabled"]:
        reason = "disabled"
    elif not disks:
        reason = "no_writable_disk"
    elif setting is None:
        reason = "not_configured"
    elif not config["valid"]:
        reason = "config_invalid"
    else:
        reason = ""
    return {
        "enabled": policy["enabled"],
        "deep_analyze_enabled": policy["deep_analyze"],
        "deep_analyze_available": await BROWSER_MANAGER.available(),
        "can_use": bool(policy["enabled"] and disks),
        "configured": configured,
        "unusable_reason": reason,
        "config": config,
        "writable_disks": disks,
        "max_items": policy["max_items"],
        "max_file_size": policy["max_file_bytes"],
        "max_images": policy["max_images"],
        "poll_interval_ms": 2500,
    }


async def save_setting(
    db: aiosqlite.Connection,
    user: dict,
    disk_id: int,
    rel_path: str,
    *,
    create: bool = False,
) -> dict:
    """保存/清除**自己的**默认目录（`disk_id=0` 表示清除）。"""
    if disk_id <= 0:
        await repo.clear_setting(db, user["id"])
        return {"configured": False, "disk_id": 0, "rel_path": ""}
    if not await list_writable_disks(db, user):
        raise _TaskError("no_writable_disk")
    _, target = await validate_target(db, user, disk_id, rel_path, create=create)
    rel = _normalize_rel_path(rel_path)
    await repo.upsert_setting(db, user["id"], disk_id, rel)
    return {"configured": True, "disk_id": disk_id, "rel_path": rel, "path": target}


# ── 任务读写 ───────────────────────────────────────────────


def task_to_dict(
    row: aiosqlite.Row, *, can_operate: bool, disk_name: str = ""
) -> dict:
    """任务行 → API 字典（不暴露服务端绝对路径）。"""
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "source_domain": row["source_domain"],
        "page_title": row["page_title"],
        "status": row["status"],
        "status_text": _STATUS_TEXT.get(row["status"], row["status"]),
        "item_total": int(row["item_total"]),
        "item_done": int(row["item_done"]),
        "bytes_total": int(row["bytes_total"]),
        "bytes_done": int(row["bytes_done"]),
        "speed_bps": int(row["speed_bps"]),
        "target_disk_name": disk_name,
        "target_rel_path": row["target_rel_path"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "can_operate": can_operate,
    }


def item_to_dict(row: aiosqlite.Row) -> dict:
    """清单项行 → API 字典。"""
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "display_name": row["display_name"],
        "ext": row["ext"],
        "width": int(row["width"]),
        "height": int(row["height"]),
        "duration_ms": int(row["duration_ms"]),
        "est_size": int(row["est_size"]),
        "is_estimated": bool(row["is_estimated"]),
        "is_selected": bool(row["is_selected"]),
        "status": row["status"],
        "error_message": row["error_message"],
    }


async def _can_operate(db: aiosqlite.Connection, user: dict, row: aiosqlite.Row) -> bool:
    """对自己的任务 + 目标磁盘仍有写权限才允许操作。"""
    disk_id = int(row["target_disk_id"])
    if disk_id <= 0:
        return False
    if user.get("role") == "admin":
        return True
    return await permission_service.check_disk_permission(db, user["id"], disk_id, "write")


async def list_tasks(
    db: aiosqlite.Connection, user: dict, page: int = 1, page_size: int = 20
) -> dict:
    """任务列表（仅自己的；惰性过期）。"""
    await repo.expire_ready_tasks(db, user["id"], _now_iso())
    rows, total = await repo.list_tasks(db, user["id"], page, page_size)
    tasks = []
    for row in rows:
        disk = (
            await virtual_disk_repository.find_by_id(db, int(row["target_disk_id"]))
            if int(row["target_disk_id"]) > 0
            else None
        )
        tasks.append(
            task_to_dict(
                row,
                can_operate=await _can_operate(db, user, row),
                disk_name=str(disk["name"]) if disk is not None else "",
            )
        )
    return {"total": total, "tasks": tasks}


async def get_detail(db: aiosqlite.Connection, user: dict, task_id: int) -> dict:
    """任务详情 + 清单（他人任务按不存在处理）。"""
    await repo.expire_ready_tasks(db, user["id"], _now_iso())
    row = await repo.find_task(db, task_id, user["id"])
    if row is None:
        raise _TaskError("not_found")
    items = await repo.list_items(db, task_id)
    disk = (
        await virtual_disk_repository.find_by_id(db, int(row["target_disk_id"]))
        if int(row["target_disk_id"]) > 0
        else None
    )
    return {
        "task": task_to_dict(
            row,
            can_operate=await _can_operate(db, user, row),
            disk_name=str(disk["name"]) if disk is not None else "",
        ),
        "items": [item_to_dict(item) for item in items],
        "has_more_images": bool(
            row["status"] == "ready"
            and int(row["image_page_index"]) < OFFLINE_MAX_IMAGE_PAGES
        ),
    }


async def create_task(
    db: aiosqlite.Connection, user: dict, url: str, *, analyzer: MediaAnalyzerService | None = None
) -> dict:
    """
    创建分析任务（D9 前置校验：无可写磁盘 / 未配置目录时**不产生任何出站请求**）。
    """
    policy = await get_policy(db)
    if not policy["enabled"]:
        raise _TaskError("disabled", "离线下载功能已关闭")
    if not await list_writable_disks(db, user):
        raise _TaskError("no_writable_disk")
    setting = await repo.get_setting(db, user["id"])
    if setting is None:
        raise _TaskError("target_not_configured")
    try:
        await validate_target(
            db, user, int(setting["default_disk_id"]), str(setting["default_rel_path"])
        )
    except _TaskError as exc:
        raise _TaskError("config_invalid", exc.message) from exc
    try:
        target = await validate_url(url)
    except SsrfBlocked as exc:
        raise _TaskError("ssrf_blocked") from exc
    if await repo.count_pending(db) >= OFFLINE_QUEUE_MAX:
        raise _TaskError("queue_full", "分析队列已满")
    domain = (urlsplit(target.url).hostname or "").lower()
    task_id = await repo.insert_task(
        db,
        user_id=user["id"],
        username=user.get("username", ""),
        source_url=target.url,
        source_domain=domain,
        status="queued",
    )
    _enqueue_analysis(task_id)
    return {
        "task_id": task_id,
        "status": "analyzing",
        "source_domain": domain,
        "page_title": "",
    }


async def select_items(
    db: aiosqlite.Connection,
    user: dict,
    task_id: int,
    item_ids: list[int],
    disk_id: int,
    target_path: str,
    pack_zip: bool = False,
) -> dict:
    """勾选并开始下载（逐次校验磁盘写权限 / 路径 / 余量 / 体积上限）。"""
    await repo.expire_ready_tasks(db, user["id"], _now_iso())
    row = await repo.find_task(db, task_id, user["id"])
    if row is None:
        raise _TaskError("not_found")
    if row["status"] != "ready":
        raise _TaskError("invalid_state", "任务状态不允许下载")
    unique_ids = sorted({int(value) for value in item_ids})
    if not unique_ids:
        raise _TaskError("no_items", "未选择任何项目")
    policy = await get_policy(db)
    if len(unique_ids) > policy["max_items"] + policy["max_images"]:
        raise _TaskError("too_large", "选择项数超过上限")
    selected = await repo.select_items(db, task_id, unique_ids)
    if selected != len(unique_ids):
        await repo.select_items(db, task_id, [])
        raise _TaskError("no_items", "部分项目不属于该任务")
    items = await repo.list_selected_items(db, task_id)
    if not items:
        raise _TaskError("no_items", "未选择任何项目")

    if disk_id <= 0 or not target_path:
        setting = await repo.get_setting(db, user["id"])
        if setting is None:
            raise _TaskError("target_not_configured")
        disk_id = int(setting["default_disk_id"])
        target_path = str(setting["default_rel_path"])
    base, _target = await validate_target(db, user, disk_id, target_path)
    total_bytes = sum(int(item["est_size"]) for item in items)
    for item in items:
        if int(item["est_size"]) > policy["max_file_bytes"]:
            raise _TaskError("too_large")
    required = int(total_bytes * OFFLINE_DISK_SAFETY_FACTOR)
    if required > 0:
        free = shutil.disk_usage(base).free
        if free < required:
            raise _TaskError("disk_full", f"磁盘剩余空间不足（需约 {required}，剩 {free}）")
    await repo.set_target(
        db,
        task_id,
        disk_id=disk_id,
        rel_path=_normalize_rel_path(target_path),
        pack_zip=pack_zip,
        bytes_total=total_bytes,
    )
    await repo.set_status(db, task_id, "downloading")
    _enqueue_download(task_id)
    return {
        "task_id": task_id,
        "status": "downloading",
        "selected": len(unique_ids),
        "bytes_total": total_bytes,
    }


async def cancel_task(db: aiosqlite.Connection, user: dict, task_id: int) -> dict:
    """取消任务（排队/分析/下载/合并）；终态拒绝。"""
    row = await repo.find_task(db, task_id, user["id"])
    if row is None:
        raise _TaskError("not_found")
    if row["status"] in ("success", "failed", "canceled"):
        raise _TaskError("invalid_state", "任务已结束")
    event = _cancel_events.get(task_id)
    if event is not None:
        event.set()
    await repo.set_status(db, task_id, "canceled")
    base = await _task_base(db, row)
    if base:
        cleanup_task_temp(os.path.join(base, row["target_rel_path"]), task_id)
    return {"task_id": task_id, "status": "canceled"}


async def retry_task(db: aiosqlite.Connection, user: dict, task_id: int) -> dict:
    """重试失败/取消任务；不可重试类直接拒绝。"""
    row = await repo.find_task(db, task_id, user["id"])
    if row is None:
        raise _TaskError("not_found")
    if row["status"] not in ("failed", "canceled"):
        raise _TaskError("invalid_state", "任务状态不可重试")
    if row["error_code"] in (
        "drm_protected",
        "login_required",
        "too_large",
        "disk_full",
        "no_writable_disk",
        "target_not_configured",
        "config_invalid",
    ):
        raise _TaskError("invalid_state", "该失败类型不可重试")
    items = await repo.list_items(db, task_id)
    if not items or row["error_code"] in ("url_expired", "restart_interrupted"):
        await repo.set_status(db, task_id, "queued")
        _enqueue_analysis(task_id)
        return {"task_id": task_id, "status": "analyzing"}
    selected = [item for item in items if bool(item["is_selected"])]
    if not selected:
        await repo.set_status(db, task_id, "queued")
        _enqueue_analysis(task_id)
        return {"task_id": task_id, "status": "analyzing"}
    await repo.set_status(db, task_id, "downloading")
    _enqueue_download(task_id)
    return {"task_id": task_id, "status": "downloading"}


async def delete_record(db: aiosqlite.Connection, user: dict, task_id: int) -> dict:
    """删除任务记录（不删磁盘文件）。"""
    row = await repo.find_task(db, task_id, user["id"])
    if row is None:
        raise _TaskError("not_found")
    await repo.soft_delete(db, task_id, user["id"])
    base = await _task_base(db, row)
    if base:
        cleanup_task_temp(os.path.join(base, row["target_rel_path"]), task_id)
    return {"task_id": task_id, "deleted": True}


async def next_page(
    db: aiosqlite.Connection,
    user: dict,
    task_id: int,
    *,
    analyzer: MediaAnalyzerService | None = None,
) -> dict:
    """图片追加下一页（仅 ready；按 page_index 自增）。"""
    await repo.expire_ready_tasks(db, user["id"], _now_iso())
    row = await repo.find_task(db, task_id, user["id"])
    if row is None:
        raise _TaskError("not_found")
    if row["status"] != "ready":
        raise _TaskError("invalid_state", "任务状态不允许追加")
    page_index = int(row["image_page_index"])
    if page_index >= OFFLINE_MAX_IMAGE_PAGES:
        raise _TaskError("page_limit")
    policy = await get_policy(db)
    service = analyzer or MediaAnalyzerService()
    try:
        result = await service.analyze(
            row["source_url"],
            deep_enabled=False,
            max_items=policy["max_items"],
            max_images=policy["max_images"],
            page_index=page_index + 1,
        )
    except AnalysisError as exc:
        raise _TaskError(exc.code, exc.message) from exc
    existing = await repo.list_items(db, task_id)
    new_rows = [item for item in result.items if item.kind == "image"]
    if not new_rows:
        raise _TaskError("no_media", "下一页没有图片")
    merged = [_item_row_to_dict(item) for item in existing] + [
        _analyzed_to_dict(item) for item in new_rows
    ]
    await repo.replace_items(db, task_id, merged)
    await repo.set_image_page_index(db, task_id, page_index + 1)
    await repo.set_analysis_result(
        db,
        task_id,
        level=row["analysis_level"],
        expire_at=row["analysis_expire_at"] or _iso_in(OFFLINE_ANALYSIS_TTL),
        item_total=len(merged),
    )
    return {
        "added": len(new_rows),
        "item_total": len(merged),
        "has_more": bool(result.next_page_url) and page_index + 1 < OFFLINE_MAX_IMAGE_PAGES,
        "page_index": page_index + 1,
    }


# ── worker 队列 ────────────────────────────────────────────


async def start_workers(db_factory) -> None:
    """启动分析与下载 worker（应用 lifespan 调用；重启兜底在这里做）。"""
    global _analysis_queue, _download_queue, _worker_stop
    _worker_stop = False
    _analysis_queue = asyncio.Queue()
    _download_queue = asyncio.Queue()
    async with db_factory() as db:
        interrupted = await repo.mark_interrupted(db)
        if interrupted:
            logger.info("服务重启：%s 个离线下载任务已标记失败", interrupted)
        await _cleanup_orphan_temps(db)

    async def _mark_crashed(task_id: int, code: str) -> None:
        """worker 未捕获异常兜底：把非终态任务落为 failed，避免永久卡在「进行中」。"""
        try:
            async with db_factory() as db:
                row = await repo.find_task_any(db, task_id)
                if row is not None and row["status"] not in repo.TERMINAL_STATUSES:
                    await repo.set_status(
                        db,
                        task_id,
                        "failed",
                        error_code=code,
                        error_message=_ERROR_MESSAGES.get(code, "下载失败"),
                    )
        except Exception:  # noqa: BLE001 - 兜底里再失败只能记日志
            logger.error("worker 兜底落库失败: task_id=%s", task_id)

    async def analysis_worker() -> None:
        while not _worker_stop:
            task_id = await _analysis_queue.get()
            try:
                async with db_factory() as db:
                    await _run_analysis(db, task_id)
            except Exception as exc:  # noqa: BLE001 - worker 兜底
                logger.error("分析 worker 异常: task_id=%s %s", task_id, type(exc).__name__)
                await _mark_crashed(task_id, "parser_outdated")
            finally:
                _analysis_queue.task_done()

    async def download_worker() -> None:
        while not _worker_stop:
            task_id = await _download_queue.get()
            try:
                async with db_factory() as db:
                    await _run_download(db, task_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("下载 worker 异常: task_id=%s %s", task_id, type(exc).__name__)
                await _mark_crashed(task_id, "network_error")
            finally:
                _download_queue.task_done()

    _workers.extend(asyncio.create_task(analysis_worker()) for _ in range(ANALYSIS_WORKERS))
    _workers.extend(asyncio.create_task(download_worker()) for _ in range(DOWNLOAD_WORKERS))
    logger.info("离线下载队列已启动: analysis=%s download=%s", ANALYSIS_WORKERS, DOWNLOAD_WORKERS)


async def stop_workers() -> None:
    """停止 worker 并唤醒取消信号。"""
    global _worker_stop
    _worker_stop = True
    for event in _cancel_events.values():
        event.set()
    _cancel_events.clear()
    for worker in _workers:
        worker.cancel()
    _workers.clear()
    await BROWSER_MANAGER.shutdown()


def _enqueue_analysis(task_id: int) -> None:
    """入分析队列（服务未启动时忽略，测试可直接调 worker）。"""
    if _analysis_queue is not None:
        _analysis_queue.put_nowait(task_id)


def _enqueue_download(task_id: int) -> None:
    """入下载队列。"""
    if _download_queue is not None:
        _download_queue.put_nowait(task_id)


async def _run_analysis(db: aiosqlite.Connection, task_id: int) -> None:
    """执行分析任务：queued → analyzing → ready/failed。"""
    row = await repo.find_task_any(db, task_id)
    if row is None or row["status"] != "queued":
        return
    await repo.set_status(db, task_id, "analyzing")
    policy = await get_policy(db)
    service = MediaAnalyzerService()
    try:
        result = await service.analyze(
            row["source_url"],
            deep_enabled=policy["deep_analyze"],
            max_items=policy["max_items"],
            max_images=policy["max_images"],
        )
    except AnalysisError as exc:
        await repo.set_status(
            db, task_id, "failed", error_code=exc.code, error_message=exc.message
        )
        return
    except Exception:
        await repo.set_status(
            db,
            task_id,
            "failed",
            error_code="parser_outdated",
            error_message=_ERROR_MESSAGES["parser_outdated"],
        )
        return
    current = await repo.find_task_any(db, task_id)
    if current is None or current["status"] == "canceled":
        return
    await _store_analysis(db, task_id, result)


async def _store_analysis(
    db: aiosqlite.Connection, task_id: int, result: AnalysisResult
) -> None:
    """把分析结果落 items 表并置 ready。"""
    items = [_analyzed_to_dict(item) for item in result.items]
    await repo.replace_items(db, task_id, items)
    await repo.set_analysis_result(
        db,
        task_id,
        level=result.level,
        expire_at=_iso_in(OFFLINE_ANALYSIS_TTL),
        item_total=len(items),
        page_title=result.page_title,
    )
    await repo.set_status(db, task_id, "ready")


#: 自动重分析时允许刷新的 item 字段（与仓库白名单保持一致）
_REANALYZE_SOURCE_FIELDS = (
    "source_url",
    "aux_url",
    "protocol",
    "variant_id",
    "vcodec",
    "acodec",
    "width",
    "height",
    "duration_ms",
    "est_size",
    "is_estimated",
    "display_name",
    "ext",
)


def _match_reanalysis_item(old: aiosqlite.Row, news: list, used: set[int]) -> int | None:
    """
    在重新分析结果里找与旧项对应的项（用于刷新过期地址）。

    匹配优先级：`kind + variant_id` → `kind + display_name` → `kind + ext/宽高`。
    刻意**不做**“按顺序硬配”：站点改版时错配会把 A 的地址写到 B 上，
    宁可不匹配（最终仍按 `url_expired` 失败），也不下载错内容。
    """
    kind = old["kind"]

    def candidate(index: int, item) -> bool:
        return index not in used and item.kind == kind

    if old["variant_id"]:
        for index, item in enumerate(news):
            if candidate(index, item) and item.variant_id == old["variant_id"]:
                return index
    name = old["display_name"]
    for index, item in enumerate(news):
        if candidate(index, item) and item.display_name == name:
            return index
    ext = old["ext"] or ""
    width, height = int(old["width"] or 0), int(old["height"] or 0)
    for index, item in enumerate(news):
        if (
            candidate(index, item)
            and item.ext == ext
            and int(item.width or 0) == width
            and int(item.height or 0) == height
        ):
            return index
    return None


async def _auto_reanalyze(
    db: aiosqlite.Connection,
    task_id: int,
    row: aiosqlite.Row,
    policy: dict,
) -> bool:
    """
    媒体地址过期后的**一次**自动重分析（AC-225 / 设计 §2.5.5）。

    行为：
      1. 分析队列已满时**不**重分析（避免雪崩），返回 False；
      2. 复用既有分析器（SSRF / 超时 / 三级降级规则不变）；
      3. 按 `variant_id / display_name / 宽高` 把新地址刷到旧 item 上，
         **保留**已成功项、用户勾选、产物名与断点进度；
      4. 只重置“确实被刷新过”的 `url_expired` 失败项；否则返回 False。

    :returns: 是否完成了一次有效重分析（调用方据此决定是否重试一轮）
    """
    if await repo.count_pending(db) >= OFFLINE_QUEUE_MAX:
        logger.info("自动重分析跳过（分析队列已满）: task_id=%s", task_id)
        return False
    analyzer = MediaAnalyzerService()
    try:
        async with _get_reanalyze_semaphore():
            result = await analyzer.analyze(
                row["source_url"],
                deep_enabled=policy["deep_analyze"],
                max_items=policy["max_items"],
                max_images=policy["max_images"],
            )
    except AnalysisError as exc:
        logger.info("自动重分析失败: task_id=%s code=%s", task_id, exc.code)
        return False
    except Exception:  # noqa: BLE001 - 重分析失败不改变最终错误码（仍是 url_expired）
        logger.error("自动重分析异常: task_id=%s", task_id)
        return False

    old_items = await repo.list_items(db, task_id)
    news = list(result.items)
    used: set[int] = set()
    refreshed_ids: list[int] = []
    for old in old_items:
        index = _match_reanalysis_item(old, news, used)
        if index is None:
            continue
        used.add(index)
        new = news[index]
        await repo.refresh_item_source(
            db,
            int(old["id"]),
            **{name: getattr(new, name) for name in _REANALYZE_SOURCE_FIELDS},
        )
        refreshed_ids.append(int(old["id"]))

    failed_ids = [
        int(item["id"])
        for item in old_items
        if item["status"] == "failed" and item["error_code"] == "url_expired"
    ]
    targets = [item_id for item_id in failed_ids if item_id in refreshed_ids]
    if not targets:
        logger.info("自动重分析未覆盖失败项: task_id=%s", task_id)
        return False
    await repo.reset_failed_items(
        db, task_id, error_code="url_expired", item_ids=targets
    )
    await repo.set_analysis_result(
        db,
        task_id,
        level=result.level,
        expire_at=_iso_in(OFFLINE_ANALYSIS_TTL),
        item_total=len(old_items),
        page_title=result.page_title or row["page_title"],
    )
    logger.info(
        "自动重分析完成: task_id=%s refreshed=%s expired_items=%s",
        task_id,
        len(refreshed_ids),
        len(targets),
    )
    return True


async def _sync_state_from_items(
    db: aiosqlite.Connection, task_id: int, state: dict
) -> None:
    """重试前按已成功项重算进度（避免重复计入已下载字节）。"""
    items = await repo.list_items(db, task_id)
    state["done"] = sum(
        int(item["bytes_done"] or 0) for item in items if item["status"] == "success"
    )
    state["item_done"] = sum(1 for item in items if item["status"] == "success")


async def _fetch_selected_items(
    db: aiosqlite.Connection,
    fetcher: MediaFetcherService,
    task_id: int,
    row: aiosqlite.Row,
    target_dir: str,
    state: dict,
    event: asyncio.Event,
) -> None:
    """下载本轮被勾选项；已成功项跳过（断点续下载）。"""
    items = await repo.list_selected_items(db, task_id)
    if not items:
        raise FetchError("no_media", "没有可下载项")
    images = [item for item in items if item["kind"] == "image"]
    others = [item for item in items if item["kind"] != "image"]
    for item in others:
        if item["status"] == "success":
            continue
        base_done = int(state["done"])
        await self_fetch_item(
            db,
            fetcher,
            task_id,
            item,
            target_dir,
            row,
            state,
            event,
            base_done=base_done,
        )
    if images:
        image_base = int(state["done"])
        outcomes = await fetcher.fetch_images(
            target_dir,
            task_id,
            [dict(item) for item in images],
            title=row["page_title"] or row["source_domain"],
            referer=row["source_url"],
            pack_zip=bool(row["pack_zip"]),
            progress=lambda done, total: _merge_progress(state, image_base + done, total),
            cancel_event=event,
        )
        image_bytes = 0
        failures = []
        for outcome in outcomes:
            if outcome.status == "failed":
                # 单张失败也要落库（error_message 用中文），其余图片继续保留
                await repo.update_item(
                    db,
                    outcome.item_id,
                    status="failed",
                    error_code=outcome.error_code,
                    error_message=_ERROR_MESSAGES.get(outcome.error_code, "下载失败"),
                )
                failures.append(outcome)
                continue
            if outcome.output_name.endswith(".zip") or outcome.item_id <= 0:
                continue
            await repo.update_item(
                db,
                outcome.item_id,
                status=outcome.status,
                output_name=outcome.output_name,
                bytes_done=outcome.bytes_written,
            )
            if outcome.status == "success":
                state["item_done"] += 1
                image_bytes += outcome.bytes_written
        state["done"] = image_base + image_bytes
        if failures:
            # 已成功的图片已落盘并回填；任务整体按第一个失败原因置 failed，
            # 用户重试时只重下失败项（已成功项跳过）。
            first = failures[0]
            raise FetchError(first.error_code or "network_error", first.error_message)


async def _run_download(db: aiosqlite.Connection, task_id: int) -> None:
    """执行下载任务：downloading → success/failed/canceled。"""
    row = await repo.find_task_any(db, task_id)
    if row is None or row["status"] != "downloading":
        return
    event = asyncio.Event()
    _cancel_events[task_id] = event
    base = await _task_base(db, row)
    if not base:
        await repo.set_status(
            db, task_id, "failed", error_code="target_not_writable", error_message="目标目录不可写"
        )
        return
    from src.repositories import user_repository

    owner_row = await user_repository.find_by_id(db, int(row["user_id"]))
    owner = (
        dict(owner_row)
        if owner_row is not None
        else {"id": int(row["user_id"]), "role": "user"}
    )
    try:
        # 用**校验后**的绝对路径，不要拿 DB 里的相对路径自己 join（防 DB 被篡改）
        _base, target_dir = await validate_target(
            db,
            owner,
            int(row["target_disk_id"]),
            str(row["target_rel_path"]),
        )
    except _TaskError as exc:
        await repo.set_status(
            db, task_id, "failed", error_code=exc.code, error_message=exc.message
        )
        return
    policy = await get_policy(db)
    semaphore = await _get_download_semaphore(db, policy["max_concurrent"])
    state = {"done": 0, "total": int(row["bytes_total"]), "item_done": 0}
    reporter = asyncio.create_task(_report_progress(db, task_id, state))
    try:
        await semaphore.acquire()
        outbound = OutboundClient()
        try:
            fetcher = MediaFetcherService(outbound, max_file_bytes=policy["max_file_bytes"])
            reanalyzed = False
            while True:
                try:
                    await _fetch_selected_items(
                        db, fetcher, task_id, row, target_dir, state, event
                    )
                    break
                except FetchError as exc:
                    # AC-225：媒体地址过期（401/403）→ 自动重分析一次后从断点继续。
                    # 只允许一次；队列满 / 重分析失败 / 未覆盖失败项 → 按原错误落库。
                    if exc.code != "url_expired" or reanalyzed or event.is_set():
                        raise
                    if not await _auto_reanalyze(db, task_id, row, policy):
                        raise
                    reanalyzed = True
                    fresh = await repo.find_task_any(db, task_id)
                    if fresh is not None:
                        row = fresh
                    await _sync_state_from_items(db, task_id, state)
                    logger.info("离线下载自动重分析后继续: task_id=%s", task_id)
        finally:
            await outbound.aclose()
        if event.is_set():
            raise FetchError("canceled")
        await repo.set_progress(
            db,
            task_id,
            bytes_done=int(state["done"]),
            bytes_total=int(row["bytes_total"]),
            speed_bps=0,
            item_done=state["item_done"],
        )
        await repo.set_status(db, task_id, "success")
        cleanup_task_temp(target_dir, task_id)
        logger.info("离线下载完成: task_id=%s", task_id)
    except FetchError as exc:
        if event.is_set() and exc.code == "canceled":
            await repo.set_status(db, task_id, "canceled")
        else:
            await repo.set_status(
                db,
                task_id,
                "failed",
                error_code=exc.code,
                error_message=_ERROR_MESSAGES.get(exc.code, "下载失败"),
            )
        cleanup_task_temp(target_dir, task_id)
    except _TaskError as exc:
        await repo.set_status(
            db, task_id, "failed", error_code=exc.code, error_message=exc.message
        )
        cleanup_task_temp(target_dir, task_id)
    finally:
        reporter.cancel()
        try:
            await reporter
        except asyncio.CancelledError:
            pass
        semaphore.release()
        _cancel_events.pop(task_id, None)


async def self_fetch_item(
    db: aiosqlite.Connection,
    fetcher: MediaFetcherService,
    task_id: int,
    item: aiosqlite.Row,
    target_dir: str,
    task_row: aiosqlite.Row,
    state: dict,
    event: asyncio.Event,
    *,
    base_done: int = 0,
) -> None:
    """
    下载单条非图片项并回填结果（含一次网络重试）。

    :param base_done: 本项开始前已完成的字节数（多任务项累计用；进度回调是**单项内**
        的相对字节，必须加上它才是总进度，否则会重复计数/超过 100%）
    """
    await repo.update_item(db, int(item["id"]), status="downloading")
    try:
        outcome = await fetcher.fetch_item(
            target_dir,
            task_id,
            dict(item),
            title=task_row["page_title"] or task_row["source_domain"],
            referer=task_row["source_url"],
            progress=lambda done, total: _merge_progress(state, base_done + done, total),
            cancel_event=event,
        )
    except FetchError as exc:
        if exc.code in ("net_timeout", "network_error"):
            try:
                outcome = await fetcher.fetch_item(
                    target_dir,
                    task_id,
                    dict(item),
                    title=task_row["page_title"] or task_row["source_domain"],
                    referer=task_row["source_url"],
                    progress=lambda done, total: _merge_progress(state, base_done + done, total),
                    cancel_event=event,
                )
            except FetchError as retry_exc:
                await repo.update_item(
                    db,
                    int(item["id"]),
                    status="failed",
                    error_code=retry_exc.code,
                    error_message=_ERROR_MESSAGES.get(retry_exc.code, "下载失败"),
                )
                raise retry_exc
        else:
            await repo.update_item(
                db,
                int(item["id"]),
                status="failed",
                error_code=exc.code,
                error_message=_ERROR_MESSAGES.get(exc.code, "下载失败"),
            )
            raise
    await repo.update_item(
        db,
        int(item["id"]),
        status=outcome.status,
        output_name=outcome.output_name,
        bytes_done=outcome.bytes_written,
    )
    state["item_done"] += 1
    # 以“基准 + 本项产物字节”为准，不累加：进度回调已把 base_done+done 写进来，
    # 再 += 会把同一次下载计两遍（实测曾经出现 bytes_done = 2 × 文件大小）。
    state["done"] = base_done + outcome.bytes_written


async def _report_progress(db: aiosqlite.Connection, task_id: int, state: dict) -> None:
    """每秒落库一次进度（EMA 速度），避免高频写库。"""
    last_done = 0
    last_time = time.monotonic()
    speed = 0.0
    while True:
        await asyncio.sleep(OFFLINE_PROGRESS_THROTTLE)
        now = time.monotonic()
        delta = max(0, int(state["done"]) - last_done)
        elapsed = max(0.001, now - last_time)
        instant = delta / elapsed
        speed = instant if speed == 0 else speed * 0.7 + instant * 0.3
        last_done = int(state["done"])
        last_time = now
        try:
            await repo.set_progress(
                db,
                task_id,
                bytes_done=int(state["done"]),
                bytes_total=int(state["total"]),
                speed_bps=int(speed),
                item_done=int(state["item_done"]),
            )
        except Exception:  # noqa: BLE001 - 进度写失败不终止下载
            return


def _merge_progress(state: dict, done: int, total: int | None) -> None:
    """合并进度（总数已知时更新总长）。"""
    state["done"] = max(int(state["done"]), int(done))
    if total and int(total) > 0:
        state["total"] = max(int(state["total"]), int(total))


def _get_reanalyze_semaphore() -> asyncio.Semaphore:
    """取自动重分析闸门（按事件循环懒加载，避免跨 asyncio.run 复用导致 RuntimeError）。"""
    global _reanalyze_semaphore, _reanalyze_loop
    loop = asyncio.get_running_loop()
    if _reanalyze_semaphore is None or _reanalyze_loop is not loop:
        _reanalyze_semaphore = asyncio.Semaphore(ANALYSIS_WORKERS)
        _reanalyze_loop = loop
    return _reanalyze_semaphore


async def _get_download_semaphore(
    db: aiosqlite.Connection, value: int
) -> asyncio.Semaphore:
    """按当前配置生成下载并发信号量（配置变化时重建）。"""
    global _download_semaphore, _download_semaphore_value
    if _download_semaphore is None or _download_semaphore_value != value:
        _download_semaphore = asyncio.Semaphore(value)
        _download_semaphore_value = value
    return _download_semaphore


async def _task_base(db: aiosqlite.Connection, row: aiosqlite.Row) -> str:
    """取任务目标磁盘根路径（磁盘被删则空串）。"""
    disk_id = int(row["target_disk_id"])
    if disk_id <= 0:
        return ""
    disk = await virtual_disk_repository.find_by_id(db, disk_id)
    return str(disk["real_path"]) if disk is not None else ""


async def _cleanup_orphan_temps(db: aiosqlite.Connection) -> None:
    """启动时清理不属于活跃任务的临时目录（按 24h 判定）。"""
    disks = await virtual_disk_repository.list_all(db)
    now = time.time()
    for disk in disks:
        root = os.path.join(str(disk["real_path"]), OFFLINE_DIR_NAME)
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            path = os.path.join(root, entry)
            try:
                if os.path.isdir(path) and now - os.path.getmtime(path) > 24 * 3600:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue


def _analyzed_to_dict(item) -> dict:
    """AnalyzedItem → 仓库 items dict。"""
    return {
        "kind": item.kind,
        "display_name": item.display_name,
        "ext": item.ext,
        "source_url": item.source_url,
        "aux_url": item.aux_url,
        "protocol": item.protocol,
        "variant_id": item.variant_id,
        "vcodec": item.vcodec,
        "acodec": item.acodec,
        "width": item.width,
        "height": item.height,
        "duration_ms": item.duration_ms,
        "est_size": item.est_size,
        "is_estimated": item.is_estimated,
        "note": item.note,
    }


def _item_row_to_dict(item: aiosqlite.Row) -> dict:
    """items 行 → 仓库可重建的 dict（分页追加时保留已有项）。"""
    return {
        "kind": item["kind"],
        "display_name": item["display_name"],
        "ext": item["ext"],
        "source_url": item["source_url"],
        "aux_url": item["aux_url"],
        "protocol": item["protocol"],
        "variant_id": item["variant_id"],
        "vcodec": item["vcodec"],
        "acodec": item["acodec"],
        "width": item["width"],
        "height": item["height"],
        "duration_ms": item["duration_ms"],
        "est_size": item["est_size"],
        "is_estimated": bool(item["is_estimated"]),
        "note": item["error_message"],
    }


def _now_iso() -> str:
    """当前 UTC ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _iso_in(seconds: float) -> str:
    """当前时间 + seconds 的 ISO 字符串。"""
    from datetime import timedelta

    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


# 供测试/接口层使用的错误文案
ERROR_MESSAGES = _ERROR_MESSAGES

#: 全局策略默认值（GET /config 未落库时展示默认，管理员改后立即生效）
OFFLINE_CONFIG_DEFAULTS = {
    OFFLINE_ENABLED_KEY: "1",
    OFFLINE_DEEP_ANALYZE_KEY: "1",
    OFFLINE_MAX_CONCURRENT_KEY: str(OFFLINE_DEFAULT_MAX_CONCURRENT),
    OFFLINE_MAX_FILE_SIZE_KEY: str(OFFLINE_DEFAULT_MAX_FILE_SIZE_MB),
    OFFLINE_MAX_ITEMS_KEY: str(OFFLINE_DEFAULT_MAX_ITEMS),
    OFFLINE_MAX_IMAGES_KEY: str(OFFLINE_DEFAULT_MAX_IMAGES),
}


def validate_config_value(key: str, value: str) -> str:
    """
    校验管理员写入的离线下载配置值（服务端白名单 + 范围，不信任客户端）。

    :returns: 规范化后的字符串值
    :raises ValueError: 非法值（中文原因）
    """
    text = str(value).strip()
    if key in (OFFLINE_ENABLED_KEY, OFFLINE_DEEP_ANALYZE_KEY):
        if text not in ("0", "1"):
            raise ValueError("该开关只能为 0 或 1")
        return text
    ranges = {
        OFFLINE_MAX_CONCURRENT_KEY: (1, OFFLINE_HARD_MAX_CONCURRENT),
        OFFLINE_MAX_FILE_SIZE_KEY: (1, OFFLINE_HARD_MAX_FILE_SIZE_MB),
        OFFLINE_MAX_ITEMS_KEY: (1, OFFLINE_HARD_MAX_ITEMS),
        OFFLINE_MAX_IMAGES_KEY: (1, OFFLINE_HARD_MAX_IMAGES),
    }
    if key in ranges:
        try:
            number = int(text)
        except (TypeError, ValueError) as exc:
            raise ValueError("该配置必须为整数") from exc
        low, high = ranges[key]
        if not low <= number <= high:
            raise ValueError(f"该配置必须在 {low}~{high} 之间")
        return str(number)
    return text

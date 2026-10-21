"""
离线下载控制器（v1.8.0 新增 / S6）

10 个接口（设计 §2.2 / §7），全部：
  - 不在明文白名单；请求/响应走加密信封（POST body / GET query 信封）；
  - **无路径参数**，`task_id` 一律在加密 body 内；
  - JWT 只从加密 body 的 `token` 字段读取（§9.3）；
  - 记录归属一律由 `token` 解析出的 `user_id` 决定，**不接受**客户端传入 `user_id`。
"""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from src.models.database import get_db
from src.services import auth_service, offline_download_service as service
from src.utils.logger import get_logger
from src.utils.middleware import decrypt_request_body, encrypt_response

router = APIRouter(prefix="/api/v1/offline", tags=["离线下载"])
logger = get_logger(__name__)

#: 业务错误码 → HTTP 状态码（404 一律用于"不存在或不是你的"）
_STATUS_MAP = {
    "not_found": 404,
    "no_writable_disk": 403,
    "target_not_writable": 403,
    "invalid_state": 409,
}
_PAGE_LIMIT = 100


async def _get_user(request: Request, db: aiosqlite.Connection, body: dict) -> dict:
    """从加密请求体取 JWT 并解析当前用户（401 语义与既有 controller 一致）。"""
    token = body.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="缺少认证令牌")
    try:
        return await auth_service.get_current_user(db, token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _raise_task_error(exc: service._TaskError, *, no_writable_status: int = 400) -> None:
    """
    把服务层业务异常映射成 HTTPException（中文 detail 由全局 handler 加密）。

    `no_writable_disk` 在 analyze 是 400、在 settings 是 403（设计 §2.2.1 / §2.2.8）。
    """
    if exc.code == "no_writable_disk":
        raise HTTPException(status_code=no_writable_status, detail=exc.message)
    status = _STATUS_MAP.get(exc.code, 400)
    raise HTTPException(status_code=status, detail=exc.message)


def _session(request: Request) -> str:
    """取会话 ID（加密响应需要）。"""
    return request.headers.get("X-Session-ID", "")


@router.post(
    "/analyze",
    summary="创建离线下载分析任务",
    description=(
        "粘贴一个无需登录的网页地址，服务端异步分析出视频/音频/图片清单。\n\n"
        "请求体（加密后）：`token`、`url`。\n"
        "响应：`task_id`、`status`、`source_domain`、`page_title`。"
    ),
)
async def analyze(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """创建分析任务。"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=400, detail="网址不能为空")
    try:
        result = await service.create_task(db, user, url)
    except service._TaskError as exc:
        _raise_task_error(exc)
    return encrypt_response(_session(request), result)


@router.get(
    "/tasks",
    summary="离线下载任务列表（按账号隔离）",
    description="只返回当前账号自己的任务；GET 请求使用 query 加密信封。",
)
async def list_tasks(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """任务列表（D10）。"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    try:
        page = max(1, int(body.get("page", 1)))
        page_size = max(1, min(_PAGE_LIMIT, int(body.get("page_size", 20))))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="分页参数非法") from exc
    result = await service.list_tasks(db, user, page, page_size)
    return encrypt_response(_session(request), result)


@router.get(
    "/detail",
    summary="离线下载任务详情（含清单）",
    description="他人任务一律 404（不泄露存在性）。GET 请求使用 query 加密信封。",
)
async def detail(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """任务详情 + 清单。"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    task_id = _int(body.get("task_id"))
    if task_id <= 0:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    try:
        result = await service.get_detail(db, user, task_id)
    except service._TaskError as exc:
        _raise_task_error(exc)
    return encrypt_response(_session(request), result)


@router.post(
    "/select",
    summary="勾选并开始下载",
    description="校验清单/目标目录/磁盘写权限/余量/体积上限后进入下载队列。",
)
async def select(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """提交勾选。"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    task_id = _int(body.get("task_id"))
    item_ids = body.get("item_ids") or []
    if task_id <= 0 or not isinstance(item_ids, list):
        raise HTTPException(status_code=400, detail="参数非法")
    try:
        ids = [int(value) for value in item_ids]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="item_ids 非法") from exc
    disk_id = _int(body.get("disk_id"))
    target_path = str(body.get("target_path", "") or "")
    pack_zip = bool(body.get("pack_zip", False))
    try:
        result = await service.select_items(
            db, user, task_id, ids, disk_id, target_path, pack_zip
        )
    except service._TaskError as exc:
        _raise_task_error(exc)
    return encrypt_response(_session(request), result)


@router.post("/next-page", summary="图片追加下一页")
async def next_page(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """继续抓下一页图片。"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    task_id = _int(body.get("task_id"))
    if task_id <= 0:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    try:
        result = await service.next_page(db, user, task_id)
    except service._TaskError as exc:
        _raise_task_error(exc)
    return encrypt_response(_session(request), result)


@router.post("/cancel", summary="取消任务")
async def cancel(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """取消排队/分析/下载中的任务。"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    task_id = _int(body.get("task_id"))
    if task_id <= 0:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    try:
        result = await service.cancel_task(db, user, task_id)
    except service._TaskError as exc:
        _raise_task_error(exc)
    return encrypt_response(_session(request), result)


@router.post("/retry", summary="重试任务")
async def retry(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """重试失败/取消任务（DRM / 需登录等不可重试）。"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    task_id = _int(body.get("task_id"))
    if task_id <= 0:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    try:
        result = await service.retry_task(db, user, task_id)
    except service._TaskError as exc:
        _raise_task_error(exc)
    return encrypt_response(_session(request), result)


@router.post("/delete-record", summary="删除任务记录（不删文件）")
async def delete_record(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """软删除任务记录。"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    task_id = _int(body.get("task_id"))
    if task_id <= 0:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    try:
        result = await service.delete_record(db, user, task_id)
    except service._TaskError as exc:
        _raise_task_error(exc)
    return encrypt_response(_session(request), result)


@router.get(
    "/meta",
    summary="离线下载初始化元信息",
    description="返回**自己的**默认目录、可写磁盘、全局策略；GET 请求使用 query 加密信封。",
)
async def meta(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """初始化元信息（不含服务端绝对路径/其他账号信息）。"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    result = await service.get_meta(db, user)
    return encrypt_response(_session(request), result)


@router.post(
    "/settings",
    summary="保存/清除我的默认下载目录",
    description=(
        "`disk_id=0` 表示清除。只操作**自己**那一行；`user_id` 只来自 token。"
        "目标磁盘必须当前账号有 write 权限，路径必须是磁盘内相对路径。"
    ),
)
async def settings(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """保存/清除账号级默认目录（D9）。"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = _int(body.get("disk_id"))
    rel_path = str(body.get("rel_path", "") or "")
    create = bool(body.get("create", False))
    try:
        result = await service.save_setting(db, user, disk_id, rel_path, create=create)
    except service._TaskError as exc:
        _raise_task_error(exc, no_writable_status=403)
    return encrypt_response(_session(request), result)


def _int(value) -> int:
    """安全转 int（非法值归 0，由调用方给 400）。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

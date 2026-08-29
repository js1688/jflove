"""
媒体修复任务控制器（v1.4.2 新增）

手动离线修复的任务管理接口：
  - 创建任务（文件管理右键「修复损坏媒体」/ 播放失败弹窗「立即修复」）
  - 任务列表（全平台共享，所有登录用户可见；只读账号可看不可操作）
  - 取消 / 覆盖原文件 / 删除产物 / 删除记录

安全约定（对照 §9 安全宪法）：
  - 全部接口为加密 body JSON（decrypt_request_body + encrypt_response），
    不在明文白名单；task_id 等参数在加密 body 内，无路径参数路由
  - 操作类接口统一校验磁盘写+删权限（与创建者无关，任务全平台共享）
  - 错误统一经 HTTPException 抛出，由全局 handler 加密
"""

from fastapi import APIRouter, Depends, HTTPException, Request

import aiosqlite

from src.models.database import get_db
from src.repositories import repair_task_repository
from src.services import repair_task_service
from src.utils.middleware import decrypt_request_body, encrypt_response
from src.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/files/repair", tags=["媒体修复"])
logger = get_logger(__name__)


async def _get_user(request: Request, db: aiosqlite.Connection, body: dict) -> dict:
    """
    从加密请求体提取 JWT 并解析当前用户（与 file_controller 一致的鉴权语义）。

    :raises HTTPException: 令牌缺失/无效返回 401
    """
    token = body.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="缺少认证令牌")
    from src.services import auth_service

    try:
        return await auth_service.get_current_user(db, token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.post(
    "/create",
    summary="创建媒体修复任务（v1.4.2）",
    description=(
        "对损坏/非流式媒体文件发起异步离线修复。健康文件拒绝（无需修复）；\n"
        "同文件存在未完成任务拒绝（重复修复拦截）。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：虚拟磁盘 ID\n"
        "- `path`：文件所在目录（磁盘内相对路径）\n"
        "- `filename`：文件名（含扩展名）\n\n"
        "响应体（加密后）字段：\n"
        "- `task_id`：新任务 ID\n"
        "- `message`：结果描述（已加入修复队列）\n\n"
        "错误：403 无写+删权限；400 健康无需修复 / 无法修复 / 已有任务 / 文件不存在"
    ),
)
async def create_task(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """创建修复任务（校验权限与健康状态后入队）"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    disk_id = int(body.get("disk_id", 0))
    rel_path = body.get("path", "")
    filename = body.get("filename", "").strip()
    try:
        result = await repair_task_service.create_task(
            db, user["id"], user.get("username", ""), user["role"],
            disk_id, rel_path, filename,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, result)


@router.get(
    "/tasks",
    summary="修复任务列表（v1.4.2）",
    description=(
        "分页查询修复任务（**全平台共享**：所有登录用户可见同一列表，"
        "避免多账号重复修复；username 字段仅供展示）。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `page`：页码（默认 1）\n"
        "- `page_size`：每页条数（默认 50，上限 100）\n\n"
        "响应体（加密后）字段：\n"
        "- `total`：总条数\n"
        "- `tasks`：任务列表，每项含 `id`、`username`、`disk_id`、`filename`、"
        "`status`（pending/running/verifying/success/failed/canceled/overridden）、"
        "`progress`（0~100）、`error_message`、`source_size`、`output_name`、"
        "`created_at`、`started_at`、`finished_at`"
    ),
)
async def list_tasks(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """任务列表（全平台共享，轮询刷新）"""
    body = await decrypt_request_body(request)
    await _get_user(request, db, body)  # 仅鉴权（列表全平台共享，不区分用户）
    try:
        page = max(1, int(body.get("page", 1)))
        page_size = max(1, min(100, int(body.get("page_size", 50))))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="分页参数非法")
    rows, total = await repair_task_repository.list_tasks(db, page, page_size)
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {
        "total": total,
        "tasks": [repair_task_service.task_row_to_dict(r) for r in rows],
    })


@router.post(
    "/cancel",
    summary="取消修复任务（v1.4.2）",
    description=(
        "取消排队中/执行中的任务；执行中取消会终止 ffmpeg 并清理半成品。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `task_id`：任务 ID\n\n"
        "错误：403 无写+删权限；400 任务不存在/状态不可取消"
    ),
)
async def cancel_task(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """取消任务"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    task_id = int(body.get("task_id", 0))
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    try:
        await repair_task_service.cancel_task(db, user["id"], user["role"], task_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "任务已取消"})


@router.post(
    "/override",
    summary="覆盖原文件（v1.4.2）",
    description=(
        "把修复成功的产物原子替换到原文件位置：**原损坏文件被直接删除，"
        "不留备份、不可恢复**。客户端必须先经用户重点二次确认。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `task_id`：任务 ID\n\n"
        "错误：403 无写+删权限；400 任务非成功状态/产物缺失/原文件不存在"
    ),
)
async def override_origin(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """覆盖原文件（os.replace 原子替换，不留备份）"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    task_id = int(body.get("task_id", 0))
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    try:
        await repair_task_service.override_origin(db, user["id"], user["role"], task_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError:
        raise HTTPException(status_code=500, detail="覆盖失败，产物已保留")
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "已覆盖原文件"})


@router.post(
    "/delete-artifact",
    summary="删除修复产物（v1.4.2）",
    description=(
        "删除修复成功但尚未覆盖的产物；隐藏目录为空时顺带清理目录。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `task_id`：任务 ID"
    ),
)
async def delete_artifact(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """删除产物"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    task_id = int(body.get("task_id", 0))
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    try:
        await repair_task_service.delete_artifact(db, user["id"], user["role"], task_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "产物已删除"})


@router.post(
    "/delete-record",
    summary="删除任务记录（v1.4.2）",
    description=(
        "软删除终态任务记录（列表不再展示；不影响磁盘上产物，产物经"
        " delete-artifact 单独删除）。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `task_id`：任务 ID\n\n"
        "错误：400 任务不存在/未结束不可删"
    ),
)
async def delete_record(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """删除任务记录（仅终态；M-1 修复：要求磁盘写+删权限，只读账号不可删）"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    task_id = int(body.get("task_id", 0))
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    task = await repair_task_repository.find_by_id(db, task_id)
    if not task or task["status"] not in (
        "success", "failed", "canceled", "overridden",
    ):
        raise HTTPException(status_code=400, detail="任务不存在或未结束")
    if not await repair_task_service.has_repair_permission(
        db, user["id"], user["role"], task["disk_id"]
    ):
        raise HTTPException(status_code=403, detail="修复需要磁盘写权限与删除权限")
    await repair_task_repository.soft_delete(db, task_id)
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"message": "记录已删除"})


async def _require_terminal(db: aiosqlite.Connection, task_id: int) -> bool:
    """校验任务存在且处于终态（success/failed/canceled/overridden）。"""
    task = await repair_task_repository.find_by_id(db, task_id)
    return bool(task) and task["status"] in (
        "success", "failed", "canceled", "overridden",
    )

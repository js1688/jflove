"""
同步配置控制器（v1.1.6 精简版）

v1.1.6 变更：
  - 移除全部 sync_configs CRUD 接口（配置改为客户端本地存储）
  - 新增 `POST /api/v1/sync/snapshot`：直接传 disk_id + remote_path 扫描远端目录
  - 移除 touch 接口（last_synced_at 改为客户端本地记录）

所有接口均使用 ChaCha20-Poly1305 加密传输，须携带 X-Session-ID 请求头。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
import aiosqlite

from src.models.database import get_db
from src.services import sync_service, auth_service
from src.utils.middleware import decrypt_request_body, encrypt_response

router = APIRouter(prefix="/api/v1/sync", tags=["同步"])


async def _get_user(request: Request, db: aiosqlite.Connection, body: dict) -> dict:
    """仅从加密 body 中解析当前用户（不再支持 Authorization 头兜底）"""
    token = body.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="缺少认证令牌")
    try:
        return await auth_service.get_current_user(db, token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.post(
    "/snapshot",
    summary="获取远端目录快照",
    description=(
        "递归扫描指定虚拟磁盘下的子目录，返回文件清单。\n"
        "客户端使用此快照与本地目录扫描结果做 diff，决定上传/下载哪些文件。\n\n"
        "请求体（加密后）字段：\n"
        "- `token`：JWT 令牌\n"
        "- `disk_id`：虚拟磁盘 ID\n"
        "- `remote_path`：磁盘内子目录相对路径（留空表示根目录）\n\n"
        "响应体（加密后）字段：\n"
        "- `files`：文件清单，每项含 `path`（相对路径）、`size`（字节）、"
        "`modified_at`（Unix 时间戳浮点秒）"
    ),
)
async def get_snapshot(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """获取远端目录快照（直接传 disk_id + remote_path）"""
    body = await decrypt_request_body(request)
    user = await _get_user(request, db, body)
    try:
        files = await sync_service.list_remote_snapshot(
            db,
            user["id"],
            user["role"],
            int(body.get("disk_id") or 0),
            body.get("remote_path", ""),
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session_id = request.headers.get("X-Session-ID", "")
    return encrypt_response(session_id, {"files": files})

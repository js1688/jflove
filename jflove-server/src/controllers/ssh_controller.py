"""
远程 SSH/SFTP 连接配置控制器（v1.10.0 新增）

服务端**只存档配置，不建立任何 SSH 连接、不做 TCP 转发**。
所有接口使用加密信封，`token` 放在加密 body 里；`user_id` 一律取自 token，
禁止从请求体读取（`文档记录/设计文档/v1.10.0.md` §2.4 / §5）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
import aiosqlite

from src.models.database import get_db
from src.repositories import ssh_repository as ssh_repo
from src.services import auth_service
from src.services import ssh_service
from src.utils.middleware import decrypt_request_body, encrypt_response

router = APIRouter(prefix="/api/v1/ssh", tags=["远程连接"])


async def _ctx(
    request: Request, db: aiosqlite.Connection
) -> tuple[dict, dict, str]:
    """
    解密请求体并解析当前用户。

    :returns: (用户, 请求体, session_id)
    :raises HTTPException 401: 缺少令牌或令牌无效
    """
    body = await decrypt_request_body(request)
    token = body.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="缺少认证令牌")
    try:
        user = await auth_service.get_current_user(db, token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return user, body, request.headers.get("X-Session-ID", "")


def _raise(exc: ssh_service.SshError) -> None:
    """把业务错误转成 HTTPException（由全局处理器加密）"""
    raise HTTPException(status_code=exc.status, detail=str(exc))


# ── 分组 ─────────────────────────────────────────────


@router.post("/groups/list", summary="列出连接分组")
async def groups_list(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """列出当前账号的连接分组（含连接数）"""
    user, _, sid = await _ctx(request, db)
    groups = await ssh_service.list_groups(db, user["id"])
    return encrypt_response(sid, {"groups": groups})


@router.post("/groups/create", summary="新建分组")
async def groups_create(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """新建分组；同账号活跃分组不可重名"""
    user, body, sid = await _ctx(request, db)
    try:
        group_id = await ssh_service.create_group(db, user["id"], body)
    except ssh_service.SshError as exc:
        _raise(exc)
    return encrypt_response(sid, {"id": group_id})


@router.post("/groups/update", summary="更新分组")
async def groups_update(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """重命名或调整分组排序"""
    user, body, sid = await _ctx(request, db)
    try:
        await ssh_service.update_group(db, user["id"], int(body.get("id") or 0), body)
    except ssh_service.SshError as exc:
        _raise(exc)
    return encrypt_response(sid, {"ok": True})


@router.post("/groups/delete", summary="删除分组")
async def groups_delete(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """删除分组，组内连接落回未分组（不删连接）"""
    user, body, sid = await _ctx(request, db)
    try:
        await ssh_service.delete_group(db, user["id"], int(body.get("id") or 0))
    except ssh_service.SshError as exc:
        _raise(exc)
    return encrypt_response(sid, {"ok": True})


# ── 连接 ─────────────────────────────────────────────


@router.post("/connections/list", summary="列出连接（不含凭据）")
async def connections_list(
    request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """列出当前账号的连接摘要，支持名称/主机/用户名模糊搜索"""
    user, body, sid = await _ctx(request, db)
    connections = await ssh_repo.list_connections(
        db, user["id"], str(body.get("keyword", "") or "")
    )
    return encrypt_response(sid, {"connections": connections})


@router.post("/connections/get", summary="读取连接详情（含凭据）")
async def connections_get(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """读取单条连接全量字段，供客户端直连使用"""
    user, body, sid = await _ctx(request, db)
    try:
        connection = await ssh_service.get_connection(
            db, user["id"], int(body.get("id") or 0)
        )
    except ssh_service.SshError as exc:
        _raise(exc)
    return encrypt_response(sid, {"connection": connection})


@router.post("/connections/create", summary="新建连接")
async def connections_create(
    request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """新建连接；保存时校验跳板链路无环"""
    user, body, sid = await _ctx(request, db)
    try:
        connection_id = await ssh_service.create_connection(db, user["id"], body)
    except ssh_service.SshError as exc:
        _raise(exc)
    return encrypt_response(sid, {"id": connection_id})


@router.post("/connections/update", summary="更新连接")
async def connections_update(
    request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """更新连接；凭据留空表示不修改"""
    user, body, sid = await _ctx(request, db)
    try:
        await ssh_service.update_connection(
            db, user["id"], int(body.get("id") or 0), body
        )
    except ssh_service.SshError as exc:
        _raise(exc)
    return encrypt_response(sid, {"ok": True})


@router.post("/connections/delete", summary="删除连接")
async def connections_delete(
    request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """删除连接；被跳板引用时返回 409"""
    user, body, sid = await _ctx(request, db)
    try:
        await ssh_service.delete_connection(db, user["id"], int(body.get("id") or 0))
    except ssh_service.SshError as exc:
        _raise(exc)
    return encrypt_response(sid, {"ok": True})


@router.post("/connections/duplicate", summary="复制连接")
async def connections_duplicate(
    request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """复制一条连接（名称追加「副本」）"""
    user, body, sid = await _ctx(request, db)
    try:
        new_id = await ssh_service.duplicate_connection(
            db, user["id"], int(body.get("id") or 0)
        )
    except ssh_service.SshError as exc:
        _raise(exc)
    return encrypt_response(sid, {"id": new_id})


@router.post("/connections/touch", summary="记录最近连接时间")
async def connections_touch(
    request: Request, db: aiosqlite.Connection = Depends(get_db)
):
    """客户端连接成功后回写最近连接时间"""
    user, body, sid = await _ctx(request, db)
    connection_id = int(body.get("id") or 0)
    if not connection_id:
        raise HTTPException(status_code=400, detail="缺少连接 ID")
    await ssh_repo.touch_connected(db, user["id"], connection_id)
    return encrypt_response(sid, {"ok": True})

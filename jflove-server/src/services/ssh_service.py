"""
SSH 连接配置服务层（v1.10.0 新增）

职责：参数校验、跳板环检测、凭据指纹派生、删除引用保护。
服务端**不建立任何 SSH 连接**——配置只被存档，由客户端拉取后自行直连
（`文档记录/设计文档/v1.10.0.md` §2.3）。
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import aiosqlite

from src.repositories import ssh_repository as repo

#: 连接后执行命令的最大长度（单行）
MAX_INIT_COMMAND = 1024

#: 跳板链路最大级数（防病态配置把客户端拖死）
MAX_JUMP_DEPTH = 16

#: 允许的认证方式
AUTH_TYPES = ("password", "publickey", "password_keyboard")

#: 私钥内容最大长度（PEM 通常几 KB，64KB 足够）
MAX_PRIVATE_KEY = 64 * 1024


class SshError(Exception):
    """业务错误：携带 HTTP 状态码，由控制器统一转换"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def private_key_fingerprint(private_key: str) -> str:
    """
    计算私钥内容的 SHA256 指纹（用于 UI 展示，替代回传原文）。

    :param private_key: PEM 文本
    :returns: `SHA256:<base64>`；空私钥返回空串
    """
    if not private_key:
        return ""
    digest = hashlib.sha256(private_key.encode("utf-8")).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def _as_int(value: Any, field: str) -> int:
    """把可选整数字段转成 int，非法值抛业务错误"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        raise SshError(f"{field} 必须是数字")


def normalize(body: dict[str, Any], is_create: bool) -> dict[str, Any]:
    """
    校验并规范化连接字段。

    :param body: 解密后的请求体
    :param is_create: 新建时必填校验更严
    :raises SshError: 任一字段非法
    """
    name = str(body.get("name", "")).strip()
    host = str(body.get("host", "")).strip()
    if not name:
        raise SshError("名称不能为空")
    if not host:
        raise SshError("主机不能为空")

    port = _as_int(body.get("port", 22), "端口")
    if not 1 <= port <= 65535:
        raise SshError("端口范围必须是 1-65535")

    auth_type = str(body.get("auth_type", "password"))
    if auth_type not in AUTH_TYPES:
        raise SshError("认证方式不合法")

    username = str(body.get("username", "") or "")
    init_command = str(body.get("init_command", "") or "")
    if "\n" in init_command or "\r" in init_command:
        raise SshError("连接后执行命令只能是单行")
    if len(init_command) > MAX_INIT_COMMAND:
        raise SshError(f"连接后执行命令不能超过 {MAX_INIT_COMMAND} 字符")

    password = str(body.get("password", "") or "")
    private_key = str(body.get("private_key", "") or "")
    passphrase = str(body.get("passphrase", "") or "")
    if len(private_key) > MAX_PRIVATE_KEY:
        raise SshError("私钥内容过大（上限 64KB）")

    if is_create:
        if auth_type in ("password", "password_keyboard") and not password:
            raise SshError("该认证方式需要密码")
        if auth_type == "publickey" and not private_key:
            raise SshError("该认证方式需要私钥")

    return {
        "ssh_group_id": _as_int(body.get("ssh_group_id", 0), "分组"),
        "name": name,
        "host": host,
        "port": port,
        "username": username,
        "auth_type": auth_type,
        "password": password,
        "private_key": private_key,
        "passphrase": passphrase,
        "jump_connection_id": _as_int(body.get("jump_connection_id", 0), "跳板连接"),
        "terminal_type": str(body.get("terminal_type") or "xterm-256color"),
        "charset": str(body.get("charset") or "UTF-8"),
        "init_command": init_command,
    }


async def assert_no_cycle(
    db: aiosqlite.Connection, user_id: int, connection_id: int, jump_id: int
) -> None:
    """
    校验跳板链路不存在环、且每一跳都存在。

    :param connection_id: 当前连接 ID（新建时传 0）
    :param jump_id: 目标跳板 ID（0 表示直连）
    :raises SshError: 出现环、跳板不存在或链路超深
    """
    if not jump_id:
        return
    if jump_id == connection_id:
        raise SshError("跳板链路存在循环")
    seen = {connection_id} if connection_id else {0}
    current = jump_id
    depth = 0
    while current:
        if current in seen:
            raise SshError("跳板链路存在循环")
        seen.add(current)
        row = await repo.find_connection(db, user_id, current)
        if row is None:
            raise SshError("跳板连接不存在")
        current = int(row.get("jump_connection_id") or 0)
        depth += 1
        if depth >= MAX_JUMP_DEPTH:
            raise SshError(f"跳板链路超过 {MAX_JUMP_DEPTH} 级")


async def create_connection(db: aiosqlite.Connection, user_id: int, body: dict) -> int:
    """新建连接（校验 + 跳板环检测 + 落库）"""
    data = normalize(body, is_create=True)
    if data["ssh_group_id"]:
        if await repo.find_group(db, user_id, data["ssh_group_id"]) is None:
            raise SshError("分组不存在", status=404)
    await assert_no_cycle(db, user_id, 0, data["jump_connection_id"])
    return await repo.insert_connection(db, user_id, data)


async def update_connection(
    db: aiosqlite.Connection, user_id: int, connection_id: int, body: dict
) -> None:
    """
    更新连接。

    凭据语义：`password`/`private_key`/`passphrase` 缺省或空串表示**不修改**；
    显式传 `clear_password` / `clear_private_key` 才会清空。
    """
    current = await repo.find_connection(db, user_id, connection_id)
    if current is None:
        raise SshError("连接不存在", status=404)

    # 部分更新：缺省字段沿用当前值（主机指纹回写、单独改分组都依赖这一点）
    merged: dict[str, Any] = {
        "name": body.get("name", current["name"]),
        "host": body.get("host", current["host"]),
        "port": body.get("port", current["port"]),
        "username": body.get("username", current["username"]),
        "auth_type": body.get("auth_type", current["auth_type"]),
        "password": body.get("password", ""),
        "private_key": body.get("private_key", ""),
        "passphrase": body.get("passphrase", ""),
        "ssh_group_id": body.get("ssh_group_id", current["ssh_group_id"]),
        "jump_connection_id": body.get(
            "jump_connection_id", current["jump_connection_id"]
        ),
        "terminal_type": body.get("terminal_type", current["terminal_type"]),
        "charset": body.get("charset", current["charset"]),
        "init_command": body.get("init_command", current["init_command"]),
    }
    data = normalize(merged, is_create=False)
    if data["ssh_group_id"]:
        if await repo.find_group(db, user_id, data["ssh_group_id"]) is None:
            raise SshError("分组不存在", status=404)
    await assert_no_cycle(db, user_id, connection_id, data["jump_connection_id"])

    fields: dict[str, Any] = {
        "ssh_group_id": data["ssh_group_id"],
        "name": data["name"],
        "host": data["host"],
        "port": data["port"],
        "username": data["username"],
        "auth_type": data["auth_type"],
        "jump_connection_id": data["jump_connection_id"],
        "terminal_type": data["terminal_type"],
        "charset": data["charset"],
        "init_command": data["init_command"],
    }
    if body.get("clear_password"):
        fields["password"] = ""
    elif data["password"]:
        fields["password"] = data["password"]
    if body.get("clear_private_key"):
        fields["private_key"] = ""
        fields["passphrase"] = ""
    elif data["private_key"]:
        fields["private_key"] = data["private_key"]
        fields["passphrase"] = data["passphrase"]
    if await repo.update_connection(db, user_id, connection_id, fields) is False:
        raise SshError("没有可更新的字段")


async def get_connection(
    db: aiosqlite.Connection, user_id: int, connection_id: int
) -> dict[str, Any]:
    """读取连接全量（含凭据），附带私钥指纹供 UI 展示"""
    row = await repo.find_connection(db, user_id, connection_id)
    if row is None:
        raise SshError("连接不存在", status=404)
    row["private_key_fingerprint"] = private_key_fingerprint(row.get("private_key", ""))
    return row


async def delete_connection(
    db: aiosqlite.Connection, user_id: int, connection_id: int
) -> None:
    """删除连接；被其他连接当作跳板时拒绝（先断开引用）"""
    row = await repo.find_connection(db, user_id, connection_id)
    if row is None:
        raise SshError("连接不存在", status=404)
    refs = await repo.count_jump_references(db, user_id, connection_id)
    if refs:
        raise SshError(f"该连接被 {refs} 条连接引用为跳板，请先解除引用", status=409)
    await repo.soft_delete_connection(db, user_id, connection_id)


async def duplicate_connection(
    db: aiosqlite.Connection, user_id: int, connection_id: int
) -> int:
    """复制一条连接（不复制最近连接时间，名称追加「副本」）"""
    row = await repo.find_connection(db, user_id, connection_id)
    if row is None:
        raise SshError("连接不存在", status=404)
    data = {k: row[k] for k in (
        "ssh_group_id", "host", "port", "username", "auth_type", "password",
        "private_key", "passphrase", "jump_connection_id", "terminal_type",
        "charset", "init_command", "host_key_fingerprint",
    )}
    data["name"] = f"{row['name']} 副本"
    return await repo.insert_connection(db, user_id, data)


# ── 分组 ─────────────────────────────────────────────


async def list_groups(db: aiosqlite.Connection, user_id: int) -> list[dict]:
    """列出分组（含连接数）"""
    return await repo.list_groups(db, user_id)


async def create_group(db: aiosqlite.Connection, user_id: int, body: dict) -> int:
    """新建分组（同账号活跃分组不重名）"""
    name = str(body.get("name", "")).strip()
    if not name:
        raise SshError("分组名称不能为空")
    if await repo.find_group_by_name(db, user_id, name):
        raise SshError("分组名称已存在", status=409)
    sort_order = _as_int(body.get("sort_order", 0), "排序")
    return await repo.insert_group(db, user_id, name, sort_order)


async def update_group(
    db: aiosqlite.Connection, user_id: int, group_id: int, body: dict
) -> None:
    """重命名/排序分组"""
    if await repo.find_group(db, user_id, group_id) is None:
        raise SshError("分组不存在", status=404)
    fields: dict[str, Any] = {}
    if "name" in body:
        name = str(body.get("name", "")).strip()
        if not name:
            raise SshError("分组名称不能为空")
        if await repo.find_group_by_name(db, user_id, name, exclude_id=group_id):
            raise SshError("分组名称已存在", status=409)
        fields["name"] = name
    if "sort_order" in body:
        fields["sort_order"] = _as_int(body.get("sort_order", 0), "排序")
    if not fields:
        raise SshError("没有可更新的字段")
    await repo.update_group(db, user_id, group_id, fields)


async def delete_group(db: aiosqlite.Connection, user_id: int, group_id: int) -> None:
    """删除分组，组内连接落回未分组（不删连接）"""
    if await repo.find_group(db, user_id, group_id) is None:
        raise SshError("分组不存在", status=404)
    await repo.soft_delete_group(db, user_id, group_id)

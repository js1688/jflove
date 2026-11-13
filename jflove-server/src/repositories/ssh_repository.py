"""
SSH 连接配置数据访问层（v1.10.0 新增）

两张表：`ssh_groups` / `ssh_connections`。

硬约束（`文档记录/设计文档/v1.10.0.md` §2.2）：

1. **按账号隔离**：所有面向用户的读取/写入都带 `user_id` 条件。
   把条件放进 SQL，而不是"查出来再判断归属"——后者只要漏一个调用点就是越权。
   他人记录一律按**不存在**处理（上层抛 404）。
2. **凭据明文列**：`password` / `private_key` / `passphrase` 按用户裁决明文存储（D37），
   列表只返回 `has_*` 标记，不返回原文。
3. **软删除**：不做列级 UNIQUE（软删除会让已删名字永久占用，依据 `users` 的教训）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

#: 列表摘要字段（**不含凭据原文**）
_SUMMARY_FIELDS = (
    "id, ssh_group_id, name, host, port, username, auth_type, jump_connection_id, "
    "last_connected_at, updated_at, "
    "CASE WHEN password <> '' THEN 1 ELSE 0 END AS has_password, "
    "CASE WHEN private_key <> '' THEN 1 ELSE 0 END AS has_private_key"
)

#: 全量字段（含凭据，仅 `find_connection` 使用）
_FULL_FIELDS = (
    "id, user_id, ssh_group_id, name, host, port, username, auth_type, password, "
    "private_key, passphrase, jump_connection_id, terminal_type, charset, init_command, "
    "host_key_fingerprint, last_connected_at, created_at, updated_at"
)

#: 允许被 update 修改的字段白名单（**不接受**客户端任意字段）
UPDATABLE_FIELDS = frozenset(
    {
        "ssh_group_id",
        "name",
        "host",
        "port",
        "username",
        "auth_type",
        "password",
        "private_key",
        "passphrase",
        "jump_connection_id",
        "terminal_type",
        "charset",
        "init_command",
        "host_key_fingerprint",
    }
)


def _now() -> str:
    """返回当前 UTC 时间的 ISO 字符串（与既有表口径一致）"""
    return datetime.now(timezone.utc).isoformat()


def _summary(row: aiosqlite.Row) -> dict[str, Any]:
    """把摘要行转成普通字典，并把 `has_*` 转成 bool"""
    data = dict(row)
    data["has_password"] = bool(data.get("has_password"))
    data["has_private_key"] = bool(data.get("has_private_key"))
    return data


def _full(row: aiosqlite.Row) -> dict[str, Any]:
    """把全量行转成普通字典（含凭据原文 + 派生标记）"""
    data = dict(row)
    data["has_password"] = bool(data.get("password"))
    data["has_private_key"] = bool(data.get("private_key"))
    return data


# ── 分组 ─────────────────────────────────────────────


async def list_groups(db: aiosqlite.Connection, user_id: int) -> list[dict[str, Any]]:
    """
    列出某账号的全部活跃分组（含连接数）。

    :param db: 数据库连接
    :param user_id: 当前账号 ID
    :returns: `[{id, name, sort_order, connection_count}]`
    """
    sql = (
        "SELECT g.id, g.name, g.sort_order, "
        "(SELECT COUNT(*) FROM ssh_connections c "
        " WHERE c.ssh_group_id = g.id AND c.user_id = g.user_id "
        " AND c.deleted_at IS NULL) AS connection_count "
        "FROM ssh_groups g "
        "WHERE g.user_id = ? AND g.deleted_at IS NULL "
        "ORDER BY g.sort_order ASC, g.id ASC"
    )
    async with db.execute(sql, (user_id,)) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def find_group(
    db: aiosqlite.Connection, user_id: int, group_id: int
) -> dict[str, Any] | None:
    """按 ID 查某账号的活跃分组；不存在返回 None"""
    async with db.execute(
        "SELECT id, name, sort_order FROM ssh_groups "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (group_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def find_group_by_name(
    db: aiosqlite.Connection, user_id: int, name: str, exclude_id: int = 0
) -> dict[str, Any] | None:
    """查同账号下同名的活跃分组（用于重名校验）"""
    async with db.execute(
        "SELECT id, name FROM ssh_groups "
        "WHERE user_id = ? AND name = ? AND id <> ? AND deleted_at IS NULL",
        (user_id, name, exclude_id),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def insert_group(
    db: aiosqlite.Connection, user_id: int, name: str, sort_order: int = 0
) -> int:
    """新建分组，返回新 ID"""
    now = _now()
    async with db.execute(
        "INSERT INTO ssh_groups (user_id, name, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, name, sort_order, now, now),
    ) as cur:
        group_id = cur.lastrowid
    await db.commit()
    return int(group_id or 0)


async def update_group(
    db: aiosqlite.Connection, user_id: int, group_id: int, fields: dict[str, Any]
) -> bool:
    """更新分组名/排序；没有可更新字段返回 False"""
    allowed = {k: v for k, v in fields.items() if k in {"name", "sort_order"}}
    if not allowed:
        return False
    sets = ", ".join(f"{k} = ?" for k in allowed)
    params = list(allowed.values()) + [_now(), group_id, user_id]
    await db.execute(
        f"UPDATE ssh_groups SET {sets}, updated_at = ? "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        params,
    )
    await db.commit()
    return True


async def soft_delete_group(
    db: aiosqlite.Connection, user_id: int, group_id: int
) -> None:
    """
    软删除分组，并把组内连接落回「未分组」（`ssh_group_id = 0`）。

    **不删连接**——用户删组只是整理分类，连接配置必须保留。
    """
    now = _now()
    await db.execute(
        "UPDATE ssh_connections SET ssh_group_id = 0, updated_at = ? "
        "WHERE ssh_group_id = ? AND user_id = ? AND deleted_at IS NULL",
        (now, group_id, user_id),
    )
    await db.execute(
        "UPDATE ssh_groups SET deleted_at = ?, updated_at = ? "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (now, now, group_id, user_id),
    )
    await db.commit()


# ── 连接 ─────────────────────────────────────────────


async def list_connections(
    db: aiosqlite.Connection, user_id: int, keyword: str = ""
) -> list[dict[str, Any]]:
    """
    列出某账号的活跃连接（摘要，不含凭据）。

    :param keyword: 名称/主机/用户名的模糊匹配；空串表示不过滤
    """
    # 转义 LIKE 通配符：用户输入 `%` / `_` 应当按字面量匹配
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{escaped}%"
    sql = (
        f"SELECT {_SUMMARY_FIELDS} FROM ssh_connections "
        "WHERE user_id = ? AND deleted_at IS NULL "
        "AND (? = '' OR name LIKE ? ESCAPE '\\' OR host LIKE ? ESCAPE '\\' "
        "OR username LIKE ? ESCAPE '\\') "
        "ORDER BY updated_at DESC, id DESC"
    )
    async with db.execute(sql, (user_id, keyword, like, like, like)) as cur:
        rows = await cur.fetchall()
    return [_summary(r) for r in rows]


async def find_connection(
    db: aiosqlite.Connection, user_id: int, connection_id: int
) -> dict[str, Any] | None:
    """按 ID 查某账号的活跃连接（全量，含凭据）；不存在返回 None"""
    async with db.execute(
        f"SELECT {_FULL_FIELDS} FROM ssh_connections "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (connection_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    return _full(row) if row else None


async def insert_connection(
    db: aiosqlite.Connection, user_id: int, data: dict[str, Any]
) -> int:
    """新建连接，返回新 ID（字段由服务层校验/补默认值）"""
    now = _now()
    #: 建表默认值（服务层已校验必填项，这里只兜底可选字段）
    defaults: dict[str, Any] = {
        "ssh_group_id": 0,
        "name": "",
        "host": "",
        "port": 22,
        "username": "",
        "auth_type": "password",
        "password": "",
        "private_key": "",
        "passphrase": "",
        "jump_connection_id": 0,
        "terminal_type": "xterm-256color",
        "charset": "UTF-8",
        "init_command": "",
        "host_key_fingerprint": "",
    }
    columns = ["user_id"] + list(defaults.keys()) + ["created_at", "updated_at"]
    values = [user_id] + [data.get(k, v) for k, v in defaults.items()] + [now, now]
    placeholders = ", ".join("?" for _ in columns)
    async with db.execute(
        f"INSERT INTO ssh_connections ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    ) as cur:
        conn_id = cur.lastrowid
    await db.commit()
    return int(conn_id or 0)


async def update_connection(
    db: aiosqlite.Connection,
    user_id: int,
    connection_id: int,
    fields: dict[str, Any],
) -> bool:
    """
    更新连接（只接受白名单字段）。

    凭据字段的「空串 = 不修改」语义由服务层处理：本函数只写传进来的字段。
    """
    allowed = {k: v for k, v in fields.items() if k in UPDATABLE_FIELDS}
    if not allowed:
        return False
    sets = ", ".join(f"{k} = ?" for k in allowed)
    params = list(allowed.values()) + [_now(), connection_id, user_id]
    await db.execute(
        f"UPDATE ssh_connections SET {sets}, updated_at = ? "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        params,
    )
    await db.commit()
    return True


async def soft_delete_connection(
    db: aiosqlite.Connection, user_id: int, connection_id: int
) -> None:
    """软删除连接"""
    now = _now()
    await db.execute(
        "UPDATE ssh_connections SET deleted_at = ?, updated_at = ? "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (now, now, connection_id, user_id),
    )
    await db.commit()


async def count_jump_references(
    db: aiosqlite.Connection, user_id: int, connection_id: int
) -> int:
    """统计有多少条活跃连接把该连接当作跳板"""
    async with db.execute(
        "SELECT COUNT(*) AS n FROM ssh_connections "
        "WHERE jump_connection_id = ? AND user_id = ? AND deleted_at IS NULL",
        (connection_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def touch_connected(
    db: aiosqlite.Connection, user_id: int, connection_id: int
) -> None:
    """记录最近一次成功连接时间（客户端连接成功后回写）"""
    now = _now()
    await db.execute(
        "UPDATE ssh_connections SET last_connected_at = ?, updated_at = ? "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (now, now, connection_id, user_id),
    )
    await db.commit()

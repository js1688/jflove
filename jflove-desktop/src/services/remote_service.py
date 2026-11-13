"""
远程连接配置服务（v1.10.0）

只做「与服务端交换配置」，**不建立 SSH 连接**。
连接配置由服务端存档，客户端每次使用前拉取（不落本地缓存）。
"""

from __future__ import annotations

from typing import Any

from src.utils.http_client import http_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 分组 ─────────────────────────────────────────────


def list_groups() -> list[dict[str, Any]]:
    """列出当前账号的连接分组（含连接数）"""
    return http_client.post("/api/v1/ssh/groups/list", {}).get("groups", [])


def create_group(name: str, sort_order: int = 0) -> int:
    """新建分组，返回 ID"""
    resp = http_client.post(
        "/api/v1/ssh/groups/create", {"name": name, "sort_order": sort_order}
    )
    return int(resp.get("id") or 0)


def update_group(
    group_id: int, name: str | None = None, sort_order: int | None = None
) -> None:
    """重命名/排序分组"""
    payload: dict[str, Any] = {"id": group_id}
    if name is not None:
        payload["name"] = name
    if sort_order is not None:
        payload["sort_order"] = sort_order
    http_client.post("/api/v1/ssh/groups/update", payload)


def delete_group(group_id: int) -> None:
    """删除分组（组内连接落回未分组）"""
    http_client.post("/api/v1/ssh/groups/delete", {"id": group_id})


# ── 连接 ─────────────────────────────────────────────


def list_connections(keyword: str = "") -> list[dict[str, Any]]:
    """列出连接摘要（不含凭据原文）"""
    return http_client.post(
        "/api/v1/ssh/connections/list", {"keyword": keyword}
    ).get("connections", [])


def get_connection(connection_id: int) -> dict[str, Any]:
    """
    读取连接全量（含凭据）。

    ⚠ 该结果**不得**回传给渲染层——私钥/密码原文只在 Python 侧使用。
    UI 需要展示时用 `list_connections` 的 `has_*` 字段。
    """
    return http_client.post(
        "/api/v1/ssh/connections/get", {"id": connection_id}
    ).get("connection", {})


def create_connection(payload: dict[str, Any]) -> int:
    """新建连接，返回 ID"""
    resp = http_client.post("/api/v1/ssh/connections/create", payload)
    return int(resp.get("id") or 0)


def update_connection(connection_id: int, payload: dict[str, Any]) -> None:
    """更新连接（凭据字段留空表示不修改）"""
    body = dict(payload)
    body["id"] = connection_id
    http_client.post("/api/v1/ssh/connections/update", body)


def delete_connection(connection_id: int) -> None:
    """删除连接"""
    http_client.post("/api/v1/ssh/connections/delete", {"id": connection_id})


def duplicate_connection(connection_id: int) -> int:
    """复制连接，返回新 ID"""
    resp = http_client.post(
        "/api/v1/ssh/connections/duplicate", {"id": connection_id}
    )
    return int(resp.get("id") or 0)


def touch_connection(connection_id: int) -> None:
    """记录最近一次成功连接时间（失败忽略，不影响主流程）"""
    try:
        http_client.post("/api/v1/ssh/connections/touch", {"id": connection_id})
    except Exception as exc:  # noqa: BLE001 - 仅统计用途，失败不阻断
        logger.debug("回写最近连接时间失败：%s", exc)


def save_host_key_fingerprint(connection_id: int, fingerprint: str) -> None:
    """把用户确认过的主机指纹写回服务端（不经渲染层）"""
    http_client.post(
        "/api/v1/ssh/connections/update",
        {"id": connection_id, "host_key_fingerprint": fingerprint},
    )

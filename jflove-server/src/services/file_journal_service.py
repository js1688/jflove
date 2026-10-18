"""
文件变更日志业务层（v1.7.0）

职责：
  - 把"文件系统操作"翻译成 `file_changes` 记录（upsert / delete）
  - 提供增量查询的组装逻辑（`changes` + `latest_rev` + `has_more` + `reset_required`）

关键约束：
  - 本模块**不碰文件系统**（stat / walk 由 `file_service` 完成），只做 DB 与语义组装，
    避免与 `file_service` 形成循环导入。
  - 变更日志是 append-only 的：同一路径允许多条记录，客户端按 `rev` 顺序回放。
"""

import aiosqlite

from src.repositories import file_change_repository
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 单次查询的默认 / 最大条数
DEFAULT_LIMIT = 500
MAX_LIMIT = 2000


async def record_upsert(
    db: aiosqlite.Connection,
    disk_id: int,
    rel_path: str,
    *,
    is_dir: bool = False,
    size: int = 0,
    mtime: float = 0.0,
    content_hash: str = "",
) -> None:
    """
    记录一次"新增或修改"变更。

    :param db: 数据库连接
    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 相对磁盘根的路径（`/` 分隔）
    :param is_dir: 是否目录
    :param size: 字节数（目录传 0）
    :param mtime: Unix 时间戳（秒）
    :param content_hash: 内容哈希（可空；上传完成时传入）
    """
    await file_change_repository.insert_many(db, disk_id, [{
        "rel_path": rel_path,
        "op": "upsert",
        "is_dir": is_dir,
        "size": size,
        "mtime": mtime,
        "content_hash": content_hash,
    }])


async def record_delete(
    db: aiosqlite.Connection,
    disk_id: int,
    rel_path: str,
    *,
    is_dir: bool = False,
) -> None:
    """
    记录一次"删除"变更。

    :param db: 数据库连接
    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 被删除项相对磁盘根的路径
    :param is_dir: 是否目录
    """
    await file_change_repository.insert_many(db, disk_id, [{
        "rel_path": rel_path,
        "op": "delete",
        "is_dir": is_dir,
    }])


async def record_many(
    db: aiosqlite.Connection,
    disk_id: int,
    rows: list[dict],
) -> None:
    """批量记录变更（rename / move / 目录删除等会产生多条）"""
    await file_change_repository.insert_many(db, disk_id, rows)


def _to_change(row: dict) -> dict:
    """把仓储行转换成对外字段名（`id` → `rev`，`rel_path` → `path`）"""
    return {
        "rev": int(row["id"]),
        "path": row["rel_path"],
        "op": row["op"],
        "is_dir": bool(row["is_dir"]),
        "size": int(row["size"] or 0),
        "mtime": float(row["mtime"] or 0),
        "hash": row.get("content_hash", "") or "",
    }


async def list_changes(
    db: aiosqlite.Connection,
    disk_id: int,
    prefix: str,
    since_rev: int,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """
    查询增量变更。

    :param db: 数据库连接
    :param disk_id: 虚拟磁盘 ID
    :param prefix: 子目录前缀（空串 = 磁盘根）
    :param since_rev: 上次拿到的最大 rev
    :param limit: 单次最多返回条数（内部收敛到 [1, MAX_LIMIT]）
    :returns: `{changes, latest_rev, has_more, reset_required}`
    """
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    since_rev = max(0, int(since_rev or 0))

    rows = await file_change_repository.list_since(
        db, disk_id, prefix, since_rev, limit,
    )
    changes = [_to_change(r) for r in rows]

    # 双重防御：仓储已按边界过滤，这里再断言一次，确保不会把兄弟目录的变更回传
    clean_prefix = (prefix or "").strip().strip("/")
    if clean_prefix:
        boundary = clean_prefix + "/"
        changes = [
            c for c in changes
            if c["path"] == clean_prefix or c["path"].startswith(boundary)
        ]

    latest = await file_change_repository.latest_rev(db)
    min_rev = await file_change_repository.min_rev(db)
    if changes:
        latest = max(latest, changes[-1]["rev"])

    # since_rev 太旧（中间记录已被清理）⇒ 客户端必须改走全量对账
    reset_required = bool(since_rev > 0 and min_rev > 0 and since_rev + 1 < min_rev)

    return {
        "changes": changes,
        "latest_rev": latest,
        "has_more": len(changes) >= limit,
        "reset_required": reset_required,
    }


async def cleanup(db: aiosqlite.Connection, keep_rows: int) -> int:
    """
    清理过老的变更记录（保留最近 keep_rows 条）。

    :returns: 删除条数
    """
    return await file_change_repository.cleanup_old(db, keep_rows)


async def get_latest_rev(db: aiosqlite.Connection) -> int:
    """返回全局最大 rev（供对账接口让客户端对齐游标）"""
    return await file_change_repository.latest_rev(db)

"""
挂载点本地索引（v1.7.0）

用 Python 标准库 `sqlite3` 持久化挂载点的远端文件清单与增量游标，
不引入新依赖。所有函数都是同步阻塞的，调用方应放在工作线程执行。

表结构：
  - `mount_files`：远端文件元数据（`rel_path` 相对挂载点根）
  - `mount_state`：每个挂载点已同步到的 `rev`
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from src.config.settings import LOCAL_STORAGE_DIR

#: 索引数据库路径（与其它本地数据同目录）
_DB_PATH = os.path.join(LOCAL_STORAGE_DIR, "mount_index.db")

_CREATE_FILES = """
CREATE TABLE IF NOT EXISTS mount_files (
    mount_id  TEXT    NOT NULL,
    rel_path  TEXT    NOT NULL,
    size      INTEGER NOT NULL DEFAULT 0,
    mtime     REAL    NOT NULL DEFAULT 0,
    rev       INTEGER NOT NULL DEFAULT 0,
    is_dir    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (mount_id, rel_path)
)
"""

_CREATE_STATE = """
CREATE TABLE IF NOT EXISTS mount_state (
    mount_id TEXT PRIMARY KEY,
    last_rev INTEGER NOT NULL DEFAULT 0
)
"""


def _connect() -> sqlite3.Connection:
    """打开索引库并确保表结构存在"""
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.execute(_CREATE_FILES)
    conn.execute(_CREATE_STATE)
    conn.commit()
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """
    数据库上下文：成功提交、异常回滚、**退出时关闭连接**。

    直接用 `with sqlite3.connect(...)` 只会提交/回滚，**不会关闭连接**，
    频繁调用会泄漏文件句柄（Windows 上还会阻止删除索引文件）。
    """
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def upsert_files(mount_id: str, rows: list[dict]) -> int:
    """
    批量写入/更新远端文件元数据。

    :param mount_id: 挂载点 ID
    :param rows: 每项含 rel_path / size / mtime / rev / is_dir
    :returns: 写入条数
    """
    if not rows:
        return 0
    payload = [
        (
            mount_id,
            r["rel_path"],
            int(r.get("size") or 0),
            float(r.get("mtime") or 0),
            int(r.get("rev") or 0),
            1 if r.get("is_dir") else 0,
        )
        for r in rows
    ]
    with _db() as conn:
        conn.executemany(
            "INSERT INTO mount_files (mount_id, rel_path, size, mtime, rev, is_dir)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(mount_id, rel_path) DO UPDATE SET"
            " size = excluded.size, mtime = excluded.mtime,"
            " rev = excluded.rev, is_dir = excluded.is_dir",
            payload,
        )
    return len(payload)


def delete_files(mount_id: str, rel_paths: list[str]) -> int:
    """
    批量删除远端文件元数据（远端已删除的条目）。

    :returns: 删除条数
    """
    if not rel_paths:
        return 0
    with _db() as conn:
        conn.executemany(
            "DELETE FROM mount_files WHERE mount_id = ? AND rel_path = ?",
            [(mount_id, p) for p in rel_paths],
        )
    return len(rel_paths)


def list_files(mount_id: str) -> list[dict]:
    """
    读出某挂载点的全部远端文件（按相对路径升序）。

    :returns: 每项含 rel_path / size / mtime / rev / is_dir
    """
    with _db() as conn:
        cur = conn.execute(
            "SELECT rel_path, size, mtime, rev, is_dir FROM mount_files"
            " WHERE mount_id = ? ORDER BY rel_path",
            (mount_id,),
        )
        return [
            {
                "rel_path": row[0],
                "size": row[1],
                "mtime": row[2],
                "rev": row[3],
                "is_dir": bool(row[4]),
            }
            for row in cur.fetchall()
        ]


def get_last_rev(mount_id: str) -> int:
    """取某挂载点已同步到的 rev（无记录返回 0）"""
    with _db() as conn:
        cur = conn.execute(
            "SELECT last_rev FROM mount_state WHERE mount_id = ?", (mount_id,)
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def set_last_rev(mount_id: str, rev: int) -> None:
    """记录某挂载点已同步到的 rev"""
    with _db() as conn:
        conn.execute(
            "INSERT INTO mount_state (mount_id, last_rev) VALUES (?, ?)"
            " ON CONFLICT(mount_id) DO UPDATE SET last_rev = excluded.last_rev",
            (mount_id, int(rev)),
        )


def reset_mount(mount_id: str) -> None:
    """清空某挂载点的索引与游标（删除挂载点 / 强制全量重建时使用）"""
    with _db() as conn:
        conn.execute("DELETE FROM mount_files WHERE mount_id = ?", (mount_id,))
        conn.execute("DELETE FROM mount_state WHERE mount_id = ?", (mount_id,))

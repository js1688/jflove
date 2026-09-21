"""
v1.5.0 用户表 schema 迁移回归测试

背景：`users.username` 原是**列级 UNIQUE**（不区分是否软删除）。软删除
（AGENTS.md §5.2 要求 `deleted_at`）会保留历史行 ⇒ 被删账号的用户名被永久占用，
管理员删了账号却无法用同名重建 —— 用户反馈的「账号删除后添加相同账号会报错」。

修复：去掉列级 UNIQUE，改为
  - 普通索引 `users_username_idx`；
  - **部分唯一索引** `users_username_active_uidx ON users(username)
    WHERE deleted_at IS NULL`（活跃账号仍不重名，登录/查重才能无歧义）。

SQLite 不支持 `ALTER TABLE ... DROP CONSTRAINT`，只能重建表。本文件用一个
**旧 schema 的库**验证运行时迁移：

  1. 迁移后 username 列不再带 UNIQUE，且两个索引都建好；
  2. 数据零丢失（行数 / id / 密码哈希 / 软删除标记都保留）；
  3. 迁移后"被软删除行占用的用户名"可以重建；
  4. 活跃账号重名仍被数据库拒绝（唯一性没有丢）。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

# 复用 tests/conftest.py 已经做好的 DB 重定向（它把 DB_PATH 指向临时库）
from tests.conftest import (  # noqa: F401  (client/env 供其它文件使用)
    client,
    env,
)

_OLD_SCHEMA = """
CREATE TABLE users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    password_hash TEXT  NOT NULL,
    role        TEXT    NOT NULL DEFAULT 'user',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    deleted_at  TEXT
)
"""

_NOW = "2026-01-01T00:00:00+00:00"

_ROWS = [
    (1, "mig_admin", "hash_admin", "admin", 1, None),
    (2, "mig_alice", "hash_alice", "user", 1, None),
    (3, "mig_gone", "hash_gone", "user", 1, _NOW),  # 已软删除（历史行）
]


@pytest.fixture()
def legacy_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    造一个**旧 schema** 的库并重定向 DB_PATH，然后跑 init_db() 触发迁移。

    注意：`src.models.database` 在 import 时就绑定了 `DB_PATH` 常量，
    因此必须同时 patch 模块内的名字才能生效。
    """
    import src.config.settings as settings
    import src.models.database as database

    db_path = tmp_path / "legacy_users.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_OLD_SCHEMA)
    conn.execute("CREATE INDEX users_username_idx ON users(username)")
    conn.executemany(
        "INSERT INTO users (id, username, password_hash, role, enabled,"
        " created_at, updated_at, deleted_at) VALUES (?,?,?,?,?,?,?,?)",
        [(i, u, h, r, e, _NOW, _NOW, d) for (i, u, h, r, e, d) in _ROWS],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(settings, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(database, "DB_PATH", str(db_path), raising=False)

    asyncio.run(database.init_db())
    return db_path


def _create_sql(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def _index_sql(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index'"
            " AND tbl_name='users' AND sql IS NOT NULL"
        ).fetchall()
        return {name: sql for name, sql in rows}
    finally:
        conn.close()


class Test用户表迁移:
    def test_迁移后username不再有列级UNIQUE(self, legacy_db: Path):
        create_sql = _create_sql(legacy_db)
        assert "UNIQUE" not in create_sql.upper(), (
            f"username 的列级 UNIQUE 应已移除，实际建表语句：{create_sql}"
        )
        assert "username" in create_sql

    def test_两个索引都已建立(self, legacy_db: Path):
        idx = _index_sql(legacy_db)
        assert "users_username_idx" in idx, "缺少普通索引 users_username_idx"
        assert "users_username_active_uidx" in idx, "缺少活跃行部分唯一索引"

        active = idx["users_username_active_uidx"]
        assert "UNIQUE" in active.upper(), "活跃行索引必须是 UNIQUE"
        assert "deleted_at IS NULL" in active, "部分索引条件必须是 deleted_at IS NULL"

    def test_迁移不丢数据(self, legacy_db: Path):
        conn = sqlite3.connect(str(legacy_db))
        try:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            deleted = conn.execute(
                "SELECT COUNT(*) FROM users WHERE deleted_at IS NOT NULL"
            ).fetchone()[0]
            ids = [r[0] for r in conn.execute("SELECT id FROM users ORDER BY id")]
            hashes = dict(
                conn.execute("SELECT username, password_hash FROM users")
            )
        finally:
            conn.close()

        assert total == len(_ROWS), f"行数应保持 {len(_ROWS)}，实际 {total}"
        assert deleted == 1, "软删除标记必须保留（AGENTS.md §5.2）"
        assert ids == [1, 2, 3], f"主键不能变（外层表按 id 引用），实际 {ids}"
        assert hashes["mig_alice"] == "hash_alice", "密码哈希必须原样保留"
        assert hashes["mig_gone"] == "hash_gone", "历史行内容也要保留"

    def test_迁移后历史行占用的用户名可以重建(self, legacy_db: Path):
        """核心：这才是用户要的"彻底解决" —— 删掉的名字能再用"""
        conn = sqlite3.connect(str(legacy_db))
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, enabled,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("mig_gone", "hash_new", "user", 1, _NOW, _NOW),
            )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE username='mig_gone'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 2, "历史行 + 新建的活跃行应共存（旧行保留、名字可复用）"

    def test_迁移后活跃账号仍不允许重名(self, legacy_db: Path):
        """唯一性没有丢：活跃账号重名必须被数据库拒绝（登录/查重不能有歧义）"""
        conn = sqlite3.connect(str(legacy_db))
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, enabled,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    ("mig_alice", "hash_dup", "user", 1, _NOW, _NOW),
                )
                conn.commit()
        finally:
            conn.close()

    def test_迁移是幂等的(self, legacy_db: Path):
        """重复跑 init_db 不应报错、也不应改变结果"""
        import src.models.database as database

        asyncio.run(database.init_db())
        asyncio.run(database.init_db())

        assert "UNIQUE" not in _create_sql(legacy_db).upper()
        conn = sqlite3.connect(str(legacy_db))
        try:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            conn.close()
        assert total == len(_ROWS), "幂等重跑不应增删数据"

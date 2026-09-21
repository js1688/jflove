"""
`expected_schema()` / `expected_indexes()` / `LEGACY_TABLES` 一致性回归测试（v1.5.0）

背景：`scripts/check_db_schema.py` 是**发布阻塞项**的门禁，它靠
`src/models/database.py` 导出的这三个名字判断"代码期望的表结构是什么"：

  - `expected_schema()`   —— 表名 → 建表 SQL（**必须含 init_db() 用 ALTER 追加的列**）
  - `expected_indexes()`  —— 索引 DDL 清单
  - `LEGACY_TABLES`       —— 代码已不再创建的历史遗留表（不参与比对、不复制到 prod）

一旦这三者与 `init_db()` 的实际行为脱节，门禁就会**拿错基准**：
要么把正常库误报成陈旧，要么把死表当成必需对象复制进生产库。
本文件把三者与 `init_db()` 的真实产物钉死在一起。

覆盖：
  1. `init_db()` 建出的表集合 == `expected_schema()` 的键集合；
  2. 每张表的列清单与建表语句**结构化等价**（含 ALTER 追加列）；
  3. `init_db()` 建出的索引 == `expected_indexes()`；
  4. `LEGACY_TABLES` 与"代码不再创建的表"一致（不能把活表列成死表）；
  5. `users` 的期望结构里**没有**列级 UNIQUE，且有活跃行部分唯一索引。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from src.models.database import (
    LEGACY_TABLES,
    expected_indexes,
    expected_schema,
    init_db,
)

# 复用 tests/conftest.py 的 DB 重定向（import 即生效）
from tests.conftest import (  # noqa: F401  (client/env 供其它文件使用)
    client,
    env,
)

# ── 这里自己实现一份轻量结构解析，**故意不 import 门禁脚本** ──
# 理由：测试要用独立的第二实现来核对，直接复用被测代码的解析函数会"同错同对"。
# （门禁脚本本身有 `设计预览/verify_schema_guard.py` 做反向验证。）


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                if i + 1 < len(body) and body[i + 1] == quote:
                    buf.append(body[i + 1])
                    i += 2
                    continue
                quote = ""
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


_CONSTRAINT_PREFIXES = ("primary key", "unique", "check", "foreign key", "constraint")


def _columns(ddl: str) -> dict[str, str]:
    """把建表语句解析成 列名 → 归一化列定义（去掉列名本身）"""
    import re

    text = re.sub(r"--[^\n]*", " ", ddl or "")
    m = re.search(r"\((.*)\)", text, re.DOTALL)
    if not m:
        return {}
    out: dict[str, str] = {}
    for part in _split_top_level(m.group(1)):
        if part.lower().startswith(_CONSTRAINT_PREFIXES):
            continue
        head = re.match(r'^["\'`]?(\w+)["\'`]?\s+(.*)$', part, re.DOTALL)
        if not head:
            continue
        rest = re.sub(r"\s+", " ", head.group(2)).strip().rstrip(",").lower()
        out[head.group(1).lower()] = rest
    return out


def _table_ddl(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return (row[0] if row else "") or ""


def _explicit_indexes(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
    }


@pytest.fixture()
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """用当前代码的 init_db() 造一个全新库（重定向 DB_PATH，不碰真实 dev 库）"""
    import src.config.settings as settings
    import src.models.database as database

    db_path = tmp_path / "fresh_expected_schema.db"
    monkeypatch.setattr(settings, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(database, "DB_PATH", str(db_path), raising=False)
    asyncio.run(init_db())
    return db_path


def test_表集合与expected_schema一致(fresh_db: Path) -> None:
    """init_db() 建出的表必须与 expected_schema() 的键**完全一致**

    多一张 = 代码建了表但没登记（门禁会漏检）；少一张 = 登记了不存在的表
    （门禁会要求 prod 建一张永远建不出来的表）。
    """
    conn = sqlite3.connect(str(fresh_db))
    try:
        actual = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not r[0].startswith("sqlite_")
        }
    finally:
        conn.close()

    expected = set(expected_schema())
    assert actual == expected, (
        f"init_db() 建的表 {sorted(actual)} 与 expected_schema() {sorted(expected)} 不一致；"
        "改了 init_db() 的建表语句就必须同步 expected_schema()"
    )


def test_每张表的列与expected_schema一致(fresh_db: Path) -> None:
    """
    列定义必须逐列等价 —— 尤其要覆盖 `ALTER TABLE ADD COLUMN` 追加的列。

    ⚠ 对比的是**库内建表语句解析出的列定义**，不是 `PRAGMA table_info`：
    后者只给类型名，拿不到 `NOT NULL` / `DEFAULT` / `UNIQUE`，
    用它对比会让"约束丢了"这类问题漏检（门禁脚本同样按 DDL 解析比较）。
    """
    conn = sqlite3.connect(str(fresh_db))
    try:
        for table, ddl in expected_schema().items():
            actual_ddl = _table_ddl(conn, table)
            assert actual_ddl, f"{table} 在库里不存在"
            actual_cols = _columns(actual_ddl)
            expected_cols = _columns(ddl)
            assert expected_cols, f"{table}: expected_schema() 的 DDL 解析不出列定义"
            assert actual_cols == expected_cols, (
                f"{table} 列定义不一致：\n"
                f"  库里={actual_cols}\n  期望={expected_cols}"
            )
            # 列名与顺序再与 PRAGMA 对一遍（防止解析器漏掉整列）
            pragma_cols = [r[1].lower() for r in conn.execute(f'PRAGMA table_info("{table}")')]
            assert pragma_cols == list(actual_cols), (
                f"{table} 解析出的列与 PRAGMA 不一致：{pragma_cols} vs {list(actual_cols)}"
            )
    finally:
        conn.close()


def test_用户表含ALTER追加的列(fresh_db: Path) -> None:
    """`users` 的期望结构必须包含 init_db() 用 ALTER 追加的两列

    这是 v1.5.0 实际踩过的坑：`_CREATE_USERS` 里没有这两列，
    若 `expected_schema()` 只返回原始建表语句，门禁会把**正常库误报成陈旧**。
    """
    conn = sqlite3.connect(str(fresh_db))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    finally:
        conn.close()
    assert "notes_disk_id" in cols
    assert "notes_path" in cols
    expected_users_cols = _columns(expected_schema()["users"])
    assert set(expected_users_cols) == set(cols)
    assert "not null default" in expected_users_cols["notes_path"]


def test_索引与expected_indexes一致(fresh_db: Path) -> None:
    conn = sqlite3.connect(str(fresh_db))
    try:
        actual = _explicit_indexes(conn)
    finally:
        conn.close()

    from src.models.database import _INDEXES  # noqa: PLC0415

    names = set()
    import re

    for ddl in expected_indexes():
        m = re.search(r"INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\"'`]+)", ddl, re.I)
        assert m, f"索引 DDL 解析不出名字：{ddl}"
        names.add(m.group(1).strip("\"'`"))
    assert names == actual, f"索引清单不一致：期望 {sorted(names)}，库里 {sorted(actual)}"
    assert len(expected_indexes()) == len(_INDEXES)


def test_遗留表清单只包含代码不建的表(fresh_db: Path) -> None:
    """`LEGACY_TABLES` 里不能混进"当前代码还在建"的活表

    混进去的后果：门禁会把这张表**排除在比对之外** ⇒ prod 缺了它也不报，
    发布出去的库结构就是错的。
    """
    assert LEGACY_TABLES, "LEGACY_TABLES 不应为空（notes_permissions 至少在里面）"
    conflict = set(LEGACY_TABLES) & set(expected_schema())
    assert not conflict, f"这些表既在 LEGACY_TABLES 又在 expected_schema() 里：{conflict}"


def test_活跃用户名唯一约束存在且无列级UNIQUE(fresh_db: Path) -> None:
    """`users` 的期望结构：无列级 UNIQUE + 有活跃行部分唯一索引"""
    ddl = expected_schema()["users"].lower()
    assert "username text not null unique" not in ddl
    assert "notes_path" in ddl

    conn = sqlite3.connect(str(fresh_db))
    try:
        idx = dict(
            conn.execute(
                "SELECT name, COALESCE(sql,'') FROM sqlite_master"
                " WHERE type='index' AND tbl_name='users' AND sql IS NOT NULL"
            ).fetchall()
        )
    finally:
        conn.close()

    assert "users_username_idx" in idx
    assert "users_username_active_uidx" in idx
    active = idx["users_username_active_uidx"].lower()
    assert active.startswith("create unique index")
    assert "where deleted_at is null" in active


def test_期望结构能在空库上直接建出来() -> None:
    """`expected_schema()` + `expected_indexes()` 必须是可执行的合法 DDL

    门禁脚本正是把它们在一个内存库里跑一遍来得到"代码期望的结构"；
    只要有一句写法不合法，前置检查就会静默降级为"跳过比对"（假通过）。
    """
    conn = sqlite3.connect(":memory:")
    try:
        for name, ddl in expected_schema().items():
            conn.execute(ddl)
            assert _table_ddl(conn, name), f"{name} 建表后查不到 DDL"
        for ddl in expected_indexes():
            conn.execute(ddl)
    finally:
        conn.close()

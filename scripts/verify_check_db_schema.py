#!/usr/bin/env python
"""
`check_db_schema.py` 的反向验证（LESSONS.md L17「guard 的 guard」）

为什么必须有这个脚本：
  `check_db_schema.py` 是发布前的**门禁**（AGENTS.md §5.1.1）。门禁本身出错是
  最危险的：它可能"只会放行"（装饰品）或"只会翻脸"（误报）。所以每次改动它
  （或改动 `expected_schema()` / 迁移脚本）之后，都要用**坏数据**证明它会拦住、
  用**纯格式差异**证明它不误报，两类用例一起跑。

跑法（本脚本只用标准库，用哪个解释器启动都行）：
    python scripts/verify_check_db_schema.py
    jflove-server/venv-linux/bin/python scripts/verify_check_db_schema.py

  它会自己去找一个「没装 aiosqlite」的解释器来验证「关卡①跑不了必须失败」
  这条断言；找不到时该组会 SKIP（可用环境变量 JFLOVE_BARE_PYTHON 指定）。

覆盖的判据（全部按 LESSONS.md L17 的清单）：
  少一列 / 列定义变化 / 缺表 / 缺索引           → 必须失败
  纯格式差异（引号、IF NOT EXISTS、空白、注释） → 必须通过
  环境缺失（关卡① 跑不了）                      → 必须失败（不得静默跳过）
  目标方（prod）多出的对象                      → 只报告、不删、仍计为差异

设计约束：**全过程不碰真实的 dev / prod 库** —— 所有变体都是副本，写在临时目录里。

退出码：0 = 工具可信（全部断言通过）；1 = 有断言失败（工具不可信，先别发布）。
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV_DB = ROOT / "jflove-db" / "jflove-dev.db"
PROD_DB = ROOT / "jflove-db" / "jflove-prod.db"
TOOL = ROOT / "scripts" / "check_db_schema.py"

#: 关卡①（代码 ↔ dev）需要 aiosqlite 等依赖 ⇒ 必须用模块 venv 的解释器
VENV_PY = (
    ROOT / "jflove-server" / "venv-win" / "Scripts" / "python.exe"
    if sys.platform == "win32"
    else ROOT / "jflove-server" / "venv-linux" / "bin" / "python"
)

#: 旧结构（v1.5.0 之前）的 users 建表语句：username 带**列级 UNIQUE**
_USERS_OLD_DDL = """
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at    TEXT,
    notes_disk_id INTEGER,
    notes_path    TEXT
)
"""

_ok = 0
_fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}  {extra}")


def run_tool(dev: Path, prod: Path, python: Path | None = None) -> tuple[int, str]:
    p = subprocess.run(
        [str(python or VENV_PY), str(TOOL), "--dev", str(dev), "--prod", str(prod)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def sql(path: Path, *statements: str) -> None:
    conn = sqlite3.connect(path)
    try:
        for stmt in statements:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def copy_dev(tmp: Path, name: str) -> Path:
    dst = tmp / name
    shutil.copy(DEV_DB, dst)
    return dst


def make_stale_dev(tmp: Path) -> Path:
    """造一个「没跑过 v1.5.0 迁移」的 dev：username 回到列级 UNIQUE + 去掉部分唯一索引"""
    dst = copy_dev(tmp, "stale.db")
    sql(
        dst,
        "DROP INDEX IF EXISTS users_username_active_uidx",
        "DROP TABLE users",
        _USERS_OLD_DDL,
        "CREATE INDEX IF NOT EXISTS users_username_idx ON users(username)",
    )
    return dst


def _reformat(sql_text: str) -> str:
    """把合法 DDL 改写成**结构等价、文本形态明显不同**的 DDL（纯格式差异）"""
    text = sql_text.strip()
    m = re.match(r"(?is)^CREATE\s+(UNIQUE\s+)?(TABLE|INDEX)\s+(\w+)(.*)$", text)
    if not m:
        return text
    unique, kind, name, rest = m.group(1) or "", m.group(2).upper(), m.group(3), m.group(4)
    head = f'CREATE {"UNIQUE " if unique else ""}{kind} IF NOT EXISTS "{name}"'
    body = rest.replace(",", " ,")          # 逗号前空白（normalize 必须吃掉）
    body = re.sub(r"\s+", "\n    ", body)    # 换行缩进与原文完全不同
    # 注释里故意写 UNIQUE / NOT NULL 字样：验证 strip_sql_comments 生效
    return f"{head}{body}\n-- 纯格式差异：注释里写 UNIQUE NOT NULL 也不能被当成定义"


def make_format_variant(tmp: Path) -> Path:
    """结构等价、书写格式完全不同的库（用于验证工具不误报）"""
    dst = tmp / "fmt.db"
    if dst.exists():
        dst.unlink()
    src = sqlite3.connect(DEV_DB)
    try:
        objects = src.execute(
            "SELECT name, sql FROM sqlite_master"
            " WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
            " AND tbl_name NOT IN ('notes_permissions') ORDER BY type DESC"
        ).fetchall()
    finally:
        src.close()

    dst_conn = sqlite3.connect(dst)
    try:
        for name, text in objects:
            if name not in ("notes_permissions",) and not name.startswith(
                "notes_permissions_"
            ):
                dst_conn.execute(_reformat(text))
        dst_conn.commit()
    finally:
        dst_conn.close()
    return dst


def find_bare_python() -> Path | None:
    """
    找一个**没装 aiosqlite** 的解释器（用于验证"关卡①跑不了 ⇒ 必须失败"）。

    为什么需要：这条断言最容易退化 —— 一旦工具又改回"缺依赖就跳过并返回 0"，
    测试必须在**任何**启动方式下都能发现。所以这里主动去找一个裸解释器
    （系统 python3 通常就是），而不是"碰巧用错解释器才测到"。
    """
    candidates: list[Path] = []
    env = os.environ.get("JFLOVE_BARE_PYTHON")
    if env:
        candidates.append(Path(env))
    if Path(sys.executable) != VENV_PY:
        candidates.append(Path(sys.executable))
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    for cand in candidates:
        if cand == VENV_PY or not cand.exists():
            continue
        probe = subprocess.run(
            [str(cand), "-c", "import aiosqlite"], capture_output=True
        )
        if probe.returncode != 0:
            return cand
    return None


def main() -> int:
    for p in (DEV_DB, PROD_DB, TOOL, VENV_PY):
        if not p.exists():
            print(f"[FATAL] 缺少必要文件：{p}")
            return 2

    tmp = Path(tempfile.mkdtemp(prefix="jflove-schema-verify-"))
    print(f"临时目录：{tmp}（全程不碰真实 dev / prod）\n")

    print("=== 1. 基线：真 dev + 真 prod 必须放行 ===")
    rc, _ = run_tool(DEV_DB, PROD_DB)
    check("退出码 0", rc == 0, f"实际 {rc}")

    print("=== 2. 环境缺失：没装 aiosqlite 的解释器必须硬失败、不得静默跳过 ===")
    bare = find_bare_python()
    if bare is None:
        print("  [SKIP] 找不到「缺 aiosqlite 的解释器」（可设 JFLOVE_BARE_PYTHON 指定）")
    else:
        print(f"  （使用：{bare}）")
        rc, out = run_tool(DEV_DB, PROD_DB, python=bare)
        check("退出码 2", rc == 2, f"实际 {rc}")
        check("输出含 [FATAL] 关卡①", "[FATAL] 关卡①" in out)
        check("指引到模块 venv 解释器", "venv-linux" in out or "venv-win" in out)
        check("给出 --skip-code-check 出口", "--skip-code-check" in out)

    print("=== 3. 坏数据：旧结构 dev（列级 UNIQUE）必须拦住 ===")
    stale = make_stale_dev(tmp)
    rc, out = run_tool(stale, PROD_DB)
    check("退出码 1", rc == 1, f"实际 {rc}")
    check("提示方向正确（代码期望[…] / dev 库[…]）",
          "代码期望[" in out and "dev 库[" in out)
    tip = out.split("dev 库与当前代码")[-1]
    check("不再出现方向写反的 prod[…] 提示", "prod[" not in tip)

    print("=== 4. 纯格式差异必须放行 ===")
    fmt = make_format_variant(tmp)
    rc, out = run_tool(fmt, PROD_DB)
    check("退出码 0", rc == 0, f"实际 {rc}\n{out[-500:]}")

    print("=== 5. 少一列必须拦住 ===")
    less = copy_dev(tmp, "lesscol.db")
    sql(less, "ALTER TABLE users DROP COLUMN notes_path")
    rc, out = run_tool(less, PROD_DB)
    check("退出码 1", rc == 1, f"实际 {rc}")
    check("报 missing_column", "missing_column" in out)

    print("=== 6. 列定义变化（role 去掉 DEFAULT）必须拦住 ===")
    coldef = copy_dev(tmp, "coldef.db")
    sql(
        coldef,
        "DROP TABLE users",
        """
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL,
            password_hash TEXT    NOT NULL,
            role          TEXT    NOT NULL,
            enabled       INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            deleted_at    TEXT,
            notes_disk_id INTEGER,
            notes_path    TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS users_username_idx ON users(username)",
        "CREATE UNIQUE INDEX IF NOT EXISTS users_username_active_uidx"
        " ON users(username) WHERE deleted_at IS NULL",
    )
    rc, out = run_tool(coldef, PROD_DB)
    check("退出码 1", rc == 1, f"实际 {rc}")
    check("报 table_ddl_mismatch", "table_ddl_mismatch" in out)

    print("=== 7. 缺索引必须拦住 ===")
    noidx = copy_dev(tmp, "noindex.db")
    sql(noidx, "DROP INDEX users_username_active_uidx")
    rc, out = run_tool(noidx, PROD_DB)
    check("退出码 1", rc == 1, f"实际 {rc}")
    check("报 missing_index", "missing_index" in out)

    print("=== 8. 缺表必须拦住 ===")
    notable = copy_dev(tmp, "notable.db")
    sql(notable, "DROP TABLE media_repair_tasks")
    rc, out = run_tool(notable, PROD_DB)
    check("退出码 1", rc == 1, f"实际 {rc}")
    check("报 missing_table", "missing_table" in out)

    print("=== 9. 目标方多出的表：只报告、不删、仍计为差异 ===")
    extra = tmp / "prod_extra.db"
    shutil.copy(PROD_DB, extra)
    sql(extra, "CREATE TABLE tmp_junk (id INTEGER PRIMARY KEY, note TEXT)")
    rc, out = run_tool(DEV_DB, extra)
    check("退出码 1（有差异需人工确认）", rc == 1, f"实际 {rc}")
    check("报 extra_table 且写明不自动删",
          "extra_table" in out and "不自动删" in out)
    conn = sqlite3.connect(extra)
    try:
        survived = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='tmp_junk'"
        ).fetchone()[0]
    finally:
        conn.close()
    check("该表仍在（没被顺手删掉）", survived == 1)

    print(f"\n=== 结果：{_ok} passed / {_fail} failed ===")
    if _fail:
        print("工具**不可信**：先修工具，别用它做发布门禁。")
    else:
        print("工具可信：坏数据被拦住、格式差异被放行。")
    print(f"（变体库保留在 {tmp}，可删除）")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())

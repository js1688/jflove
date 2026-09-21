#!/usr/bin/env python
"""
`check_db_schema.py` 的反向验证（LESSONS.md L17「guard 的 guard」）

为什么必须有这个脚本：
  `check_db_schema.py` 是发布前的**门禁**（AGENTS.md §5.1.1）。门禁本身出错是
  最危险的：它可能"只会放行"（装饰品）或"只会翻脸"（误报）。所以每次改动它
  （或改动 `expected_schema()` / 迁移脚本）之后，都要用**坏数据**证明它会拦住、
  用**纯格式差异**证明它不误报，两类用例一起跑。

跑法（本脚本只用标准库，用哪个解释器启动都行）：
    # ① 开发机（默认前提：仓库里的真 dev 库 + 模块 venv 解释器，AGENTS.md §3.1）
    python scripts/verify_check_db_schema.py

    # ② CI / 任意环境：把三个前提**显式传参**（dev 库与模块 venv 都不入库）
    python scripts/verify_check_db_schema.py \
        --dev "$RUNNER_TEMP/ci-dev.db" \
        --prod jflove-db/jflove-prod.db \
        --tool-python "$(python -c 'import sys; print(sys.executable)')"

  它会自己去找一个「没装 aiosqlite」的解释器来验证「关卡①跑不了必须失败」
  这条断言；找不到时该组会 SKIP（可用环境变量 JFLOVE_BARE_PYTHON 指定）。

为什么要能传参（v1.5.0 CI 复盘，实测踩坑）：
  本脚本原先只认开发机约定 —— `DEV_DB` / `PROD_DB` / `VENV_PY` 全写死，且不接受
  任何参数。`ci-guards.yml` 直接裸调用它，而 CI 干净检出里：dev 库被 .gitignore
  排除（`.gitignore:37`）、模块 venv 也不入库 ⇒ `main()` 的存在性检查立刻
  `[FATAL] 缺少必要文件：<repo>/jflove-db/jflove-dev.db` 退 2，整条 workflow 红。
  **门禁脚本的运行前提必须可声明、可注入**，不能只有"开发机默认值"这一种形态；
  报错时也要把"缺什么、怎么补"打出来，而不是只丢一个路径。

覆盖的判据（全部按 LESSONS.md L17 的清单）：
  少一列 / 列定义变化 / 缺表 / 缺索引           → 必须失败
  纯格式差异（引号、IF NOT EXISTS、空白、注释） → 必须通过
  环境缺失（关卡① 跑不了）                      → 必须失败（不得静默跳过）
  目标方（prod）多出的对象                      → 只报告、不删、仍计为差异

设计约束：**全过程不碰真实的 dev / prod 库** —— 所有变体都是副本，写在临时目录里。

退出码：0 = 工具可信（全部断言通过）；1 = 有断言失败（工具不可信，先别发布）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "check_db_schema.py"

# Windows 控制台默认 GBK：本脚本会回显**被测工具的输出**（含中文与 ✓/→），
# 不重配编码时那一行会抛 UnicodeEncodeError 把断言结果盖掉
# （与 check_db_schema.py:84-88、sync_version.py:34-39 同一个坑，同一套处理）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

#: 关卡①（代码 ↔ dev）需要 aiosqlite 等依赖 ⇒ 必须用模块 venv 的解释器
VENV_PY = (
    ROOT / "jflove-server" / "venv-win" / "Scripts" / "python.exe"
    if sys.platform == "win32"
    else ROOT / "jflove-server" / "venv-linux" / "bin" / "python"
)

# ── 以下三项是**默认值**（开发机约定），由 `configure()` 依 CLI 参数覆盖 ──
#: 基准开发库（默认：仓库里的真 dev；CI 里用 `init_db()` 现场生成的等价库）
DEV_DB = ROOT / "jflove-db" / "jflove-dev.db"
#: 生产库（入库，CI 里直接用仓库里那份）
PROD_DB = ROOT / "jflove-db" / "jflove-prod.db"
#: 用来跑 `check_db_schema.py` 的解释器（**必须装了 aiosqlite**）；
#: 默认模块 venv，venv 不存在（如 CI）时退化为当前解释器
TOOL_PY = VENV_PY

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


def parse_args(argv: list[str]) -> argparse.Namespace:
    """CLI 参数（默认值＝开发机约定，见文件头「为什么要能传参」）"""
    parser = argparse.ArgumentParser(
        description="check_db_schema.py 的反向验证（guard 的 guard）",
    )
    parser.add_argument(
        "--dev", default=str(DEV_DB),
        help="基准开发库路径（默认：仓库 jflove-db/jflove-dev.db；CI 里传现场生成的等价库）",
    )
    parser.add_argument(
        "--prod", default=str(PROD_DB),
        help="生产库路径（默认：仓库 jflove-db/jflove-prod.db）",
    )
    parser.add_argument(
        "--tool-python", default=None,
        help="用来跑 check_db_schema.py 的解释器（**必须装了 aiosqlite**）；"
             "默认模块 venv，venv 不存在时用当前解释器",
    )
    return parser.parse_args(argv)


def configure(dev: Path, prod: Path, tool_python: Path | None) -> None:
    """
    把「实际生效的路径」落到模块级配置上（各 helper 都读这些名字）。

    为什么要集中到一处：这些路径全是**运行前提**，散落在各函数里就没法注入，
    也就没法在 CI（无 dev 库、无模块 venv）里跑（LESSONS.md L20：改路径不要靠猜，
    要按真实取值方式显式注入）。
    """
    global DEV_DB, PROD_DB, TOOL_PY
    DEV_DB = dev
    PROD_DB = prod
    if tool_python is not None:
        TOOL_PY = tool_python
    elif not VENV_PY.exists():
        # CI 等环境没有模块 venv；用当前解释器（调用方需保证它有 aiosqlite）
        TOOL_PY = Path(sys.executable)


def check(name: str, cond: bool, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}  {extra}")


def run_tool(dev: Path, prod: Path, python: Path | None = None) -> tuple[int, str]:
    """
    跑一次 `check_db_schema.py`，返回 (退出码, 合并后的输出)。

    ⚠ `encoding="utf-8"` 不能省（v1.5.0 CI 复盘实测）：
    被测工具在 `check_db_schema.py:84-88` 显式把 stdout 重配成 **UTF-8**，
    而 `subprocess.run(text=True)` 默认按**父进程 locale** 解码 —— Windows 上是
    cp936 ⇒ reader 线程抛 `UnicodeDecodeError`、子进程输出**整个丢失** ⇒
    「输出里必须含 xxx」这类断言全部误报（实测本地 11 passed / 9 failed，
    还会被误判成"工具不可信"）。**凡是自己指定了输出编码的子进程，读取端必须跟着指定**。
    `errors="replace"` 兜住任何编码异常，保证"读不到"永远是断言失败而不是脚本崩溃。
    """
    p = subprocess.run(
        [str(python or TOOL_PY), str(TOOL), "--dev", str(dev), "--prod", str(prod)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT),
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
    if Path(sys.executable) != TOOL_PY:
        candidates.append(Path(sys.executable))
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    for cand in candidates:
        if cand == TOOL_PY or not cand.exists():
            continue
        probe = subprocess.run(
            [str(cand), "-c", "import aiosqlite"], capture_output=True
        )
        if probe.returncode != 0:
            return cand
    return None


def main() -> int:
    args = parse_args(sys.argv[1:])
    configure(
        Path(args.dev).resolve(),
        Path(args.prod).resolve(),
        Path(args.tool_python).resolve() if args.tool_python else None,
    )

    # 存在性检查针对**实际生效的路径**（不是开发机默认值）：
    # 这样 CI 里传了 --dev/--tool-python 就不会再被"仓库里没有 dev 库"卡住。
    for p, label in (
        (DEV_DB, "基准 dev 库"),
        (PROD_DB, "prod 库"),
        (TOOL, "被验证的工具 check_db_schema.py"),
        (TOOL_PY, "跑工具用的解释器"),
    ):
        if not p.exists():
            print(f"[FATAL] 缺少必要文件：{p}")
            print(f"         （{label}）")
            print("         可按需注入：--dev <路径> --prod <路径> --tool-python <解释器>")
            print("         CI 里 dev 库与模块 venv 都不入库，须先用当前代码的 init_db()")
            print("         现场生成一份等价 dev，再把它与装了 aiosqlite 的解释器传进来")
            print("         （见 .github/workflows/ci-guards.yml）。")
            return 2

    print(f"基准 dev 库：{DEV_DB}")
    print(f"prod 库    ：{PROD_DB}")
    print(f"工具解释器 ：{TOOL_PY}")

    tmp = Path(tempfile.mkdtemp(prefix="jflove-schema-verify-"))
    print(f"临时目录：{tmp}（变体库都是副本，不碰传入的 dev / prod 本身）\n")

    print("=== 1. 基线：基准 dev + 真 prod 必须放行 ===")
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

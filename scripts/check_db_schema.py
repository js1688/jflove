#!/usr/bin/env python
"""
dev / prod 表结构核对与对齐工具

背景（v1.5.0 复盘）：
  `AGENTS.md §5.1` 要求「生产库仅发布时同步表结构，不同步业务数据」；devops 技能
  发布步骤里也有「生产库表结构同步」一项。但**此前没有任何工具或强制校验** ——
  全靠人工记忆。v1.5.0 就差点漏掉：`users.username` 的列级 UNIQUE 需要在 prod
  上去掉，而"去掉约束"这种变更 `init_db()` 的运行时迁移**管不到已存在的老库**
  （它只会加列/建表），一旦漏做，prod 会带着旧约束上线。

本脚本把那条约束变成可执行检查：

    python scripts/check_db_schema.py            # 只核对差异（发布前必须通过）
    python scripts/check_db_schema.py --align    # 把 prod 结构对齐到 dev（不动数据）

判定口径：
  - **以 dev 为准**（dev 是开发期唯一被操作的结构真相），但 dev 本身必须先与
    **当前程序代码的 DDL** 一致（前置检查，见 `--skip-code-check`）；
  - **关卡①无法完成 ⇒ 直接失败（退出码 2），不再静默跳过**：跳过它时
    「dev == prod」只证明两个库彼此一样，而两边可能都是旧结构，且工具给出的
    差异方向是反的（v1.5.0 复查实测：用系统 python 跑会走到这条路）；
  - 只比对**结构**（表、列、索引、建表 SQL），不比数据；
  - prod 允许保留 `init_db()` 写入的默认 config 行，但本脚本不看数据；
  - **历史遗留表不参与比对**（见 `_LEGACY_TABLES`）：代码已不再创建的死表
    既不会被搬进 prod，也不会被报成差异。

--align 的安全保证：
  1. 先备份 prod 到 `<prod>.bak-align-<时间戳>`；
  2. 只做结构操作：建缺失表/索引、加缺失列、重建结构不一致的表（**保留全部行与 id**）；
  3. 不删除 prod 上多出来的表/列，只报告（避免误删真实数据）；
  4. 任何异常整体回滚。

退出码：0 = 结构一致；1 = 存在差异（--align 后仍不一致）；2 = 环境/参数错误
（含「关卡①无法完成」与库文件缺失）。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "jflove-db"
DEV_DB = DB_DIR / "jflove-dev.db"
PROD_DB = DB_DIR / "jflove-prod.db"

#: 不参与比对的对象（SQLite 内部表与隐式索引）
_IGNORED_TABLES = {"sqlite_sequence", "sqlite_stat1", "sqlite_stat4"}

#: **代码已明确不再创建**的历史遗留表 —— 存在也不比对、不对齐、不复制到 prod。
#:
#: 为什么必须单列一档（v1.5.0 复盘补的）：dev 库是长期滚出来的，会留下早期版本
#: 建过、现在代码已经不建的表。如果无脑"以 dev 为准"，`--align` 会把这些**死表
#: 复制进 prod**，而全新部署的 prod 本不该有它们 —— 等于用工具把垃圾搬进生产库，
#: 还报"结构一致 ✓"。
#:
#: **单一真相在代码侧**：优先从 `src/models/database.py::LEGACY_TABLES` 读
#: （那里的注释写明了每张表的来历）；导入失败时用下面的兜底集合，
#: 保证工具在没有 jflove-server 依赖的环境里也能跑。
_LEGACY_TABLES_FALLBACK = {"notes_permissions", "sync_configs"}


def _load_legacy_tables() -> frozenset:
    """从代码侧读取历史遗留表清单（失败则用兜底集合）"""
    try:
        sys.path.insert(0, str(ROOT / "jflove-server"))
        from src.models.database import LEGACY_TABLES  # noqa: PLC0415
        return frozenset(LEGACY_TABLES)
    except Exception:  # noqa: BLE001 - 兜底，保证工具可独立运行
        return frozenset(_LEGACY_TABLES_FALLBACK)


_LEGACY_TABLES = _load_legacy_tables()

# Windows 控制台默认是 GBK，输出中文与 ✓/→ 会直接抛 UnicodeEncodeError
# （v1.5.0 排查时踩过：桌面端子进程 stdio 也是同一个坑）。这里显式改成 UTF-8，
# 无法重配时退化为"不可编码字符替换输出"，保证脚本永远能跑完。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def log(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # 极端环境（stdout 已被重定向且不可重配）下仍要输出内容
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def strip_sql_comments(sql: str) -> str:
    """
    去掉 SQL 注释后再比较结构。

    ⚠ 必需：建表语句里可能带中文说明（如 `username TEXT NOT NULL, -- 去掉了原来的
    UNIQUE`），直接做子串/文本比较会把这些字样当成真实定义，导致误报
    （v1.5.0 排查时就被这个坑过一次）。
    """
    without_line = re.sub(r"--[^\n]*", " ", sql or "")
    return re.sub(r"/\*.*?\*/", " ", without_line, flags=re.DOTALL)


def normalize_sql(sql: str) -> str:
    """
    归一化建表/索引 SQL：去注释、去表头差异、压缩空白、去引号差异，便于比较。

    ⚠ 必须去掉 **表头差异**，否则会持续误报 `table_ddl_mismatch`（v1.5.0 踩过）：
    - 代码侧写的是 `CREATE TABLE IF NOT EXISTS users (...)`；
    - SQLite 存进 `sqlite_master` 的是**实际执行过的原文**，而重建表后的库
      存的是 `CREATE TABLE "users" (...)`（带引号、没有 IF NOT EXISTS）。
    两者结构完全相同，但字符串不同。这里统一剥掉 `IF NOT EXISTS`
    与对象名周围的引号后比较，只比"列定义与约束"。

    另一个必须吃掉的是**逗号前的空白**：`init_db()` 的后补列走
    `ALTER TABLE ADD COLUMN`，SQLite 会把 `, notes_disk_id INTEGER` 直接拼在
    最后一个列定义之后 ⇒ 库里存成 `deleted_at TEXT\n, notes_disk_id INTEGER`，
    而新建库是 `deleted_at TEXT, notes_disk_id INTEGER`。归一化后只差
    "text ," 与 "text,"，纯格式差异，必须消除。
    """
    text = strip_sql_comments(sql)
    text = re.sub(r"[\"'`]", "", text)
    # 去掉 `IF NOT EXISTS`（建表/建索引都可能带），本身不改变结构语义
    text = re.sub(r"\bIF\s+NOT\s+EXISTS\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    text = text.replace(" ,", ",")
    text = text.replace("( ", "(").replace(" )", ")").strip().rstrip(";")
    return text.lower()


def table_ddl(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return (row[0] if row else "") or ""


#: 建表语句主体里属于"表级约束"的子句前缀（不是列定义）
_CONSTRAINT_PREFIXES = (
    "primary key", "unique", "check", "foreign key", "constraint",
)


def _split_top_level(body: str) -> list[str]:
    """
    按**顶层**逗号切分建表语句主体。

    必须自己写而不是 `body.split(",")`：默认值或 CHECK 里可能带逗号
    （如 `DEFAULT 'a,b'`、`CHECK (x IN (1,2))`），直接 split 会把一个列定义
    劈成两半，从而产生假差异。
    """
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
                # SQL 里 '' 表示转义的单引号
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


def table_structure(ddl: str) -> tuple[dict[str, str], frozenset[str]]:
    """
    把建表语句解析为 `(列名 → 归一化列定义, 表级约束集合)`。

    与"整串文本比较"的关键区别：**按列名对齐**，所以列顺序不同不会被判成差异。
    """
    text = strip_sql_comments(ddl).strip()
    body = re.search(r"\((.*)\)", text, re.DOTALL)
    if not body:
        return {}, frozenset()
    columns: dict[str, str] = {}
    constraints: set[str] = set()
    for part in _split_top_level(body.group(1)):
        if part.lower().startswith(_CONSTRAINT_PREFIXES):
            constraints.add(normalize_sql(part))
            continue
        head = re.match(r'^["\'`]?(\w+)["\'`]?\s+(.*)$', part, re.DOTALL)
        if not head:
            # 形态不认识 → 当表级约束处理（宁可多比，不可漏比）
            constraints.add(normalize_sql(part))
            continue
        name = head.group(1).lower()
        columns[name] = f"{name} {normalize_sql(head.group(2))}"
    return columns, frozenset(constraints)


def ddl_structure_mismatch(
    dev_ddl: str,
    prod_ddl: str,
    base_label: str = "dev",
    target_label: str = "prod",
) -> list[str]:
    """
    返回 **基准要求、而目标不满足** 的结构项；目标方多出来的东西不算（另有
    `extra_column` 单独报告）。

    ⚠ 标签必须可传（v1.5.0 复查修复）：本函数同时服务于两道关 ——
    「dev ↔ prod」与「代码期望 ↔ dev」。若把 dev/prod 写死，关卡① 的提示会
    显示成「dev[X] prod[Y]」，而实际比的是 **代码期望[X] dev 库[Y]**，
    方向正好读反（照提示操作会把 prod 带偏）。

    ⚠ 为什么不能用整串比较（v1.5.0 实测踩坑）：整串比较会把
    「prod 多了一列」也判成 `table_ddl_mismatch`，而对齐动作是**重建表** ——
    重建时只拷贝交集列，于是那多出来的一列**连同数据被静默删掉**，
    与"不删除 prod 多出的列"的承诺自相矛盾。
    改成结构化比较后：只有 **基准的列定义对不上** 或 **基准的表级约束缺失**
    才要求重建，目标方自己的额外列不再引发破坏性重建。

    另一个好处：列顺序差异不再误报（SQLite 里列顺序不影响语义）。
    """
    dev_cols, dev_cons = table_structure(dev_ddl)
    if not dev_cols:
        # 解析不出来 → 退回整串比较：宁可误报（重建一次结构等价），也不要漏报
        if normalize_sql(dev_ddl) == normalize_sql(prod_ddl):
            return []
        return ["建表语句无法解析，按整串比较判定不一致"]
    prod_cols, prod_cons = table_structure(prod_ddl)

    reasons: list[str] = []
    for name, definition in dev_cols.items():
        if name not in prod_cols:
            # 缺列由 missing_column 分支走 `ALTER TABLE ADD COLUMN`（更轻、不动数据），
            # 不在这里重复要求重建表
            continue
        if prod_cols[name] != definition:
            reasons.append(
                f"列 {name} 定义不同：{base_label}[{definition}] "
                f"{target_label}[{prod_cols[name]}]"
            )
    for c in sorted(dev_cons - prod_cons):
        reasons.append(f"缺表级约束：{c}")
    return reasons


def index_ddl(conn: sqlite3.Connection) -> dict[str, str]:
    """
    显式索引（排除 SQLite 自动创建的隐式索引，以及死表上的索引）。

    ⚠ 必须排掉死表上的索引：dev 库里的 `notes_permissions_user_id_idx`
    挂在历史遗留表 `notes_permissions` 上，若不排除，`--align` 会把这个
    **死索引也建进 prod**（v1.5.0 实测发生过），让 prod 多出一份垃圾。
    """
    rows = conn.execute(
        "SELECT name, COALESCE(sql,''), tbl_name FROM sqlite_master"
        " WHERE type='index' AND sql IS NOT NULL"
    ).fetchall()
    return {
        name: sql
        for name, sql, tbl in rows
        if tbl not in _LEGACY_TABLES and tbl not in _IGNORED_TABLES
    }


def schema_snapshot(path: Path) -> dict:
    """取一个库的结构快照（跳过 SQLite 内部表与已知死表）"""
    conn = sqlite3.connect(str(path))
    try:
        raw_tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        legacy = sorted(t for t in raw_tables if t in _LEGACY_TABLES)
        tables = [
            t for t in raw_tables
            if t not in _IGNORED_TABLES and t not in _LEGACY_TABLES
        ]
        columns: dict[str, list[str]] = {}
        for t in tables:
            columns[t] = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        return {
            "tables": tables,
            "columns": columns,
            "ddl": {t: table_ddl(conn, t) for t in tables},
            "indexes": index_ddl(conn),
            # 库里存在但被判定为历史遗留、不参与比对的对象（报告里会提示）
            "legacy": legacy,
        }
    finally:
        conn.close()


def diff_schemas(
    dev: dict,
    prod: dict,
    base_label: str = "dev",
    target_label: str = "prod",
) -> list[dict]:
    """
    比对结构差异，返回差异清单。

    `dev` 恒为**基准**、`prod` 恒为**目标**；标签只影响提示文案，便于同一函数
    同时服务于「dev ↔ prod」（默认标签）与「代码期望 ↔ dev」（传自定义标签）。

    每项：{"kind", "target", "detail", "action"}
      kind: missing_table | missing_index | missing_column | table_ddl_mismatch |
            index_ddl_mismatch | extra_table | extra_index | extra_column
    """
    diffs: list[dict] = []

    dev_tables = set(dev["tables"])
    prod_tables = set(prod["tables"])

    for t in sorted(dev_tables - prod_tables):
        diffs.append({
            "kind": "missing_table",
            "target": t,
            "detail": f"{base_label} 有、{target_label} 无",
            "action": f"按 {base_label} 建表 + 建索引（不写数据）",
        })

    for t in sorted(prod_tables - dev_tables):
        # 目标方多出来的表通常是历史遗留（如已废弃表）—— 只报告，不自动删
        diffs.append({
            "kind": "extra_table",
            "target": t,
            "detail": f"{target_label} 有、{base_label} 无（历史遗留？）",
            "action": "人工确认后处理；本工具不自动删除",
        })

    for t in sorted(dev_tables & prod_tables):
        dev_cols = dev["columns"].get(t, [])
        prod_cols = prod["columns"].get(t, [])
        for col in dev_cols:
            if col not in prod_cols:
                diffs.append({
                    "kind": "missing_column",
                    "target": f"{t}.{col}",
                    "detail": f"{base_label} 有、{target_label} 无",
                    "action": f"ALTER TABLE ADD COLUMN（取 {base_label} 的列定义）",
                })
        for col in prod_cols:
            if col not in dev_cols:
                diffs.append({
                    "kind": "extra_column",
                    "target": f"{t}.{col}",
                    "detail": f"{target_label} 有、{base_label} 无",
                    "action": "人工确认；本工具不自动删列（可能承载数据）",
                })

        # 建表 SQL 的**结构化**比较（列定义 + 表级约束），
        # 只看"基准要求而目标不满足"的部分；目标方多出的列不在此判定，
        # 否则会触发破坏性的整表重建（见 ddl_structure_mismatch 的说明）。
        reasons = ddl_structure_mismatch(
            dev["ddl"].get(t, ""), prod["ddl"].get(t, ""), base_label, target_label
        )
        if reasons:
            diffs.append({
                "kind": "table_ddl_mismatch",
                "target": t,
                "detail": "建表语句不一致：" + "；".join(reasons),
                "action": f"重建表：按 {base_label} 建新表 → 拷贝交集列（含 id）→ 替换",
            })

    dev_idx = dev["indexes"]
    prod_idx = prod["indexes"]
    for name in sorted(set(dev_idx) - set(prod_idx)):
        diffs.append({
            "kind": "missing_index",
            "target": name,
            "detail": f"{base_label} 有、{target_label} 无",
            "action": f"按 {base_label} 的索引定义创建",
        })
    for name in sorted(set(prod_idx) - set(dev_idx)):
        diffs.append({
            "kind": "extra_index",
            "target": name,
            "detail": f"{target_label} 有、{base_label} 无（历史遗留？）",
            "action": "人工确认后处理；本工具不自动删除",
        })
    for name in sorted(set(dev_idx) & set(prod_idx)):
        if normalize_sql(dev_idx[name]) != normalize_sql(prod_idx[name]):
            diffs.append({
                "kind": "index_ddl_mismatch",
                "target": name,
                "detail": "索引定义不一致",
                "action": f"DROP + 按 {base_label} 重建该索引",
            })

    return diffs


def _legacy_row_counts(path: Path, tables: list[str]) -> dict[str, int]:
    if not tables:
        return {}
    conn = sqlite3.connect(str(path))
    try:
        return {
            t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            for t in tables
        }
    finally:
        conn.close()


def report_legacy(dev: dict, prod: dict, prod_path: Path) -> None:
    """
    提示被跳过的历史遗留表。

    ⚠ 为什么要单独打印：这些表**不参与比对**，如果一声不响地跳过，
    使用者会以为"结构一致"覆盖了全部对象。必须显式说明"什么被忽略了、为什么"。
    （v1.5.0 的教训：静默跳过 = 假通过。）
    """
    if not dev["legacy"] and not prod["legacy"]:
        return
    log("")
    log("历史遗留表（代码已不再创建，不参与比对、不会复制到 prod）：")
    for label, snap, path in (("dev", dev, None), ("prod", prod, prod_path)):
        if not snap["legacy"]:
            continue
        counts = _legacy_row_counts(path, snap["legacy"]) if path else {}
        for t in snap["legacy"]:
            rows = f"{counts[t]} 行" if path else "—"
            log(f"  [{label}] {t}（{rows}）")
    log("  说明：这些是早期版本建过的表，`init_db()` 不会创建也不会删。")
    log("  prod 上若残留，可用 `--prune-legacy` 清掉（仅当表为空时才会删）。")


def prune_legacy(prod_path: Path) -> int:
    """
    删除 prod 上**已知的、空的历史遗留表**（含其索引）。

    安全护栏：**只要表里有数据就拒绝删除**，只报告行数并让人工处理 ——
    死表也可能在某个老部署里真的存着数据，绝不能替用户决定丢弃。
    """
    snap = schema_snapshot(prod_path)
    if not snap["legacy"]:
        log("prod 上没有历史遗留表，无需清理。")
        return 0

    counts = _legacy_row_counts(prod_path, snap["legacy"])
    nonempty = {t: n for t, n in counts.items() if n}
    if nonempty:
        log("[中止] 以下历史遗留表**非空**，拒绝自动删除：")
        for t, n in sorted(nonempty.items()):
            log(f"  {t}: {n} 行")
        log("  请人工确认这些数据确实无用后再手动 DROP。")
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = prod_path.with_suffix(f".db.bak-prune-{stamp}")
    shutil.copyfile(prod_path, backup)
    log(f"已备份 prod → {backup.name}")

    conn = sqlite3.connect(str(prod_path))
    try:
        for t in snap["legacy"]:
            conn.execute(f'DROP TABLE IF EXISTS "{t}"')
            log(f"  [-] 删除遗留表 {t}（0 行）")
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        log(f"清理失败并已回滚：{e}")
        return 2
    finally:
        conn.close()
    return 0


def report(diffs: list[dict], dev_path: Path, prod_path: Path) -> None:
    log("=" * 72)
    log("dev / prod 表结构核对")
    log(f"  dev  = {dev_path}")
    log(f"  prod = {prod_path}")
    log("=" * 72)
    if not diffs:
        log("结构一致 ✓（发布前检查通过）")
        return
    log(f"发现 {len(diffs)} 处差异：")
    for d in diffs:
        log(f"  [{d['kind']}] {d['target']}")
        log(f"      {d['detail']}")
        log(f"      → {d['action']}")


def align(prod_path: Path, dev_path: Path, diffs: list[dict]) -> int:
    """把 prod 结构对齐到 dev（只改结构，保留数据）"""
    if not diffs:
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = prod_path.with_suffix(f".db.bak-align-{stamp}")
    shutil.copyfile(prod_path, backup)
    log(f"\n已备份 prod → {backup.name}")

    dev = schema_snapshot(dev_path)
    prod = schema_snapshot(prod_path)
    conn = sqlite3.connect(str(prod_path))
    applied = 0
    try:
        conn.execute("BEGIN")
        for d in diffs:
            kind = d["kind"]
            target = d["target"]

            if kind == "missing_table":
                conn.execute(dev["ddl"][target])
                log(f"  [+] 建表 {target}")

            elif kind == "missing_index":
                conn.execute(dev["indexes"][target])
                log(f"  [+] 建索引 {target}")

            elif kind == "index_ddl_mismatch":
                conn.execute(f"DROP INDEX IF EXISTS {target}")
                conn.execute(dev["indexes"][target])
                log(f"  [~] 重建索引 {target}")

            elif kind == "missing_column":
                table, col = target.split(".", 1)
                # 从 dev 的建表语句里摘出该列定义
                definition = _column_definition(dev["ddl"][table], col)
                if not definition:
                    log(f"  [!] 跳过 {target}（未能从 dev 解析列定义）")
                    continue
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
                log(f"  [+] 加列 {target}")

            elif kind == "table_ddl_mismatch":
                _rebuild_table(conn, target, dev, prod)
                log(f"  [~] 重建表 {target}（按 dev 结构，保留数据与 id）")

            else:
                # extra_* 一律不动（可能承载数据）
                log(f"  [=] 保留 {kind} {target}（需人工确认）")

            applied += 1
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        log(f"\n对齐失败并已回滚：{e}")
        conn.close()
        return 2

    # 索引可能因为重建表而丢失，补一遍 dev 的全部显式索引
    try:
        for sql in dev["indexes"].values():
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # 已存在
        conn.commit()
    finally:
        conn.close()

    log(f"\n已应用 {applied} 项结构变更")
    return 0


def _column_definition(create_sql: str, column: str) -> str:
    """从 `CREATE TABLE` 语句里摘出某一列的完整定义（含类型/默认值/约束）"""
    body = re.search(r"\((.*)\)", strip_sql_comments(create_sql), re.DOTALL)
    if not body:
        return ""
    for part in body.group(1).split(","):
        text = part.strip()
        # 列定义以列名开头；跳过 PRIMARY KEY / UNIQUE / FOREIGN KEY 等表级约束
        if re.match(rf"^{re.escape(column)}\s", text, re.IGNORECASE):
            return text
    return ""


def _rebuild_table(conn: sqlite3.Connection, table: str, dev: dict, prod: dict) -> None:
    """
    按 dev 的结构重建 prod 的表，**保留数据与 prod 自有列**。

    关键修正（v1.5.0 实测踩坑）：早期实现只拷贝"两边都有的列"，于是
    prod 上多出来的列**连同数据被静默删掉** —— 而同一份报告里却写着
    "不自动删列，需人工确认"，自相矛盾。现在把 prod 自有的列定义
    一并拼进新表并复制其数据，重建后 prod 不会丢任何一列。

    主键 id 原样保留：其它表按 id 整数引用。
    """
    tmp = f"{table}__align_tmp"
    create_sql = dev["ddl"][table]

    dev_cols = list(dev["columns"][table])
    prod_cols = prod["columns"].get(table, [])
    extra_cols = [c for c in prod_cols if c not in dev_cols]

    # 把 prod 自有列的定义原样拼回新表（放在最后一个 ")" 之前）
    extra_defs: list[str] = []
    for c in extra_cols:
        definition = _column_definition(prod["ddl"].get(table, ""), c)
        if definition:
            extra_defs.append(definition)
    if extra_defs:
        head, _, tail = create_sql.rpartition(")")
        create_sql = head.rstrip().rstrip(",") + ", " + ", ".join(extra_defs) + ")" + tail

    new_create = re.sub(
        rf"CREATE\s+TABLE\s+[\"'`]?{re.escape(table)}[\"'`]?",
        f'CREATE TABLE "{tmp}"',
        create_sql,
        count=1,
        flags=re.IGNORECASE,
    )

    # 先记下原来的 AUTOINCREMENT 序列值，重建完再恢复（见下方注释）
    old_seq = _autoinc_seq(conn, table)

    conn.execute(f'DROP TABLE IF EXISTS "{tmp}"')
    conn.execute(new_create)

    copy_cols = [c for c in dev_cols if c in prod_cols] + [
        c for c in extra_cols if c in prod_cols
    ]
    if copy_cols:
        cols = ", ".join(f'"{c}"' for c in copy_cols)
        conn.execute(f'INSERT INTO "{tmp}" ({cols}) SELECT {cols} FROM "{table}"')

    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{tmp}" RENAME TO "{table}"')
    _restore_autoinc_seq(conn, table, old_seq)
    if extra_cols:
        log(f"      （保留 prod 自有列：{', '.join(extra_cols)}）")


def _autoinc_seq(conn: sqlite3.Connection, table: str) -> int | None:
    """读一张表当前的 AUTOINCREMENT 序列值（没有则返回 None）"""
    try:
        row = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None  # 库里还没有 sqlite_sequence（从未用过 AUTOINCREMENT）
    return row[0] if row else None


def _restore_autoinc_seq(
    conn: sqlite3.Connection, table: str, old_seq: int | None
) -> None:
    """
    重建表后把 AUTOINCREMENT 序列恢复成**原来的值**（只许变大，不许变小）。

    为什么必须做（实测）：`_rebuild_table` 把行**连同显式 id** 拷进新表，
    SQLite 会据此把 `sqlite_sequence` **重置为 max(id)**。
    实测例子：原表 id 曾是 1,2,3、3 被物理删除后 seq=3；重建后 seq 退回 2，
    下一行新插入拿到 **id=3 —— 复用了已删除行的 id**。
    本项目全表软删除（`AGENTS.md §5.2`）时 seq 恰好等于 max(id)，看不出问题；
    但**只要历史上物理删除过行，重建就会让 id 复用**，而 `user_permissions.user_id`
    这类字段是按整数 id 引用的 —— 复用 id 会让旧引用指到新对象上。

    因此这里显式把 seq 恢复到不小于原值，把这个隐患彻底关掉。
    """
    if old_seq is None:
        return
    try:
        current = _autoinc_seq(conn, table)
        if current is None:
            conn.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)",
                (table, old_seq),
            )
        elif current < old_seq:
            conn.execute(
                "UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (old_seq, table)
            )
    except sqlite3.OperationalError:
        # 该表不是 AUTOINCREMENT（没有 sqlite_sequence 记录）→ 无需恢复
        pass


def _venv_python_rel() -> str:
    """模块 venv 的解释器，**相对 jflove-server 目录**的路径"""
    if sys.platform == "win32":
        return r"venv-win\Scripts\python.exe"
    return "venv-linux/bin/python"


def _venv_python_hint() -> str:
    """
    按平台给出模块 venv 的解释器路径（相对仓库根）。

    关卡①要 import `jflove-server/src/models/database.py`，它依赖 `aiosqlite` 等
    第三方包 —— 用系统 python 跑通常会失败，必须提示到**装了依赖的那个解释器**上
    （AGENTS.md §3.1 的 venv 路径约定）。
    """
    if sys.platform == "win32":
        return "jflove-server\\" + _venv_python_rel()
    return "jflove-server/" + _venv_python_rel()


def _migrate_command() -> str:
    """在 dev 库上跑当前代码迁移的命令（按平台拼路径）"""
    py = _venv_python_rel()
    runner = py if sys.platform == "win32" else "./" + py
    return (
        "cd jflove-server && " + runner
        + " -c \"import asyncio; from src.models.database import init_db;"
          " asyncio.run(init_db())\""
    )


def _code_check_failed(reason: str, exc: Exception | None = None) -> int:
    """
    关卡①无法完成时的统一收尾：**响亮失败**，绝不静默放行。

    ⚠ 为什么不能"跳过并返回 0"（L18「静默跳过 = 假通过」+ v1.5.0 实测踩坑）：
    关卡①是唯一能证明"dev 库 == 当前代码结构"的一步。它一旦被跳过，后面的
    「dev ↔ prod 一致」就**只证明两个库彼此一样**，而两边可能都是旧结构；
    更糟的是此时工具会照 dev 的数据给出**方向相反**的提示（把"代码期望"
    显示成 dev、把 dev 显示成 prod），照着 `--align` 做会把 prod 退回旧结构。
    所以这里必须非 0 退出，把"没检查"这件事变成阻塞项。
    """
    log("")
    log("=" * 72)
    log("[FATAL] 关卡①（代码 → dev）无法完成，本次核对**未通过**")
    log("=" * 72)
    log(f"原因：{reason}")
    if exc is not None:
        log(f"异常：{type(exc).__name__}: {exc}")
    log("")
    log("为什么这算失败：跳过这一步后，「dev == prod」只说明两个库彼此一样，")
    log("而两边可能都不是当前代码的结构 —— 此时工具给出的差异方向也是错的，")
    log("照它对齐会把 prod 带偏（AGENTS.md §5.1.1 关卡① 存在的原因）。")
    log("")
    hint = _venv_python_hint()
    log("请用**装了依赖的模块 venv 解释器**重跑（依赖含 aiosqlite）：")
    log(f"  {hint} scripts/check_db_schema.py")
    log("（Windows 用反斜杠路径，Linux/macOS 用上面的正斜杠路径）")
    log("")
    log("确实要跳过这道关（**不推荐**，仅在已知 dev 与代码一致时）：")
    log("  python scripts/check_db_schema.py --skip-code-check")
    return 2


def check_dev_matches_code(dev_path: Path) -> int:
    """
    确认 **dev 库本身就是"当前代码期望的结构"**（发布前的前置检查）。

    为什么必须有这一步：本工具以 dev 为基准去对齐 prod。如果 **dev 库是旧文件**
    （从没被当前代码的 `init_db()` 打开过，或开发时忘了跑迁移），
    那么"dev == prod"成立、工具报"结构一致"，但**两边都不是当前代码的结构** ——
    prod 会被一起带偏，而且**看不出来**。

    做法：让当前代码的建表/索引 DDL 在一个**内存临时库**里执行一遍，
    得到"代码期望的结构"，再与 dev 比对。全程不碰 dev 库本身。

    返回 0 = dev 与代码一致；1 = 不一致（需先在 dev 上跑 `init_db()`）；
    2 = 本关卡无法完成（缺依赖 / 代码 DDL 取不到）——**不是**放行。
    """
    try:
        sys.path.insert(0, str(ROOT / "jflove-server"))
        from src.models.database import expected_indexes, expected_schema  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001 - 取不到代码 DDL ⇒ 无法证明基准正确
        return _code_check_failed("无法加载程序代码的表结构定义", e)

    reference = sqlite3.connect(":memory:")
    try:
        for ddl in expected_schema().values():
            reference.execute(ddl)
        for idx in expected_indexes():
            reference.execute(idx)

        expected = {
            "tables": sorted(expected_schema().keys()),
            "columns": {},
            "ddl": {},
            "indexes": index_ddl(reference),
        }
        for t in expected["tables"]:
            expected["columns"][t] = [
                r[1] for r in reference.execute(f"PRAGMA table_info({t})")
            ]
            expected["ddl"][t] = table_ddl(reference, t)
    except Exception as e:  # noqa: BLE001
        return _code_check_failed("构建代码期望结构失败", e)
    finally:
        reference.close()

    actual = schema_snapshot(dev_path)
    # 标签必须显式传：这里的"基准"是**代码期望**、目标是 **dev 库**，
    # 用默认的 dev/prod 写法会把方向读反（见 ddl_structure_mismatch 的说明）。
    diffs = diff_schemas(expected, actual, base_label="代码期望", target_label="dev 库")
    # 目标方（dev）多出的对象在这层不关心（这里只关心 dev 是否缺当前代码要求的东西）
    blocking = [
        d for d in diffs
        if d["kind"] in {
            "missing_table", "missing_index", "missing_column",
            "table_ddl_mismatch", "index_ddl_mismatch",
        }
    ]

    log("-" * 72)
    log("前置检查：dev 库 vs 当前程序代码的表结构")
    if not blocking:
        log("dev 库与当前代码一致 ✓")
        return 0
    log(f"dev 库与当前代码有 {len(blocking)} 处差异：")
    for d in blocking:
        log(f"  [{d['kind']}] {d['target']}")
        log(f"      {d['detail']}")
        log(f"      → {d['action']}")
    log("")
    log("这意味着 **dev 库本身是陈旧的** —— 直接对齐 prod 会把两边一起带偏。")
    log("请先让 dev 库跑一次当前代码的迁移：")
    log("  " + _migrate_command())
    log("说明：运行时迁移会加表 / 加列 / 建索引；遇到「必须去掉列约束」的变更"
        "（如 v1.5.0 的 users.username）会**重建该表**，保留 id 与全部数据行，"
        "不会删除业务数据。")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="dev / prod 表结构核对与对齐")
    parser.add_argument(
        "--align", action="store_true",
        help="把 prod 结构对齐到 dev（先自动备份，只改结构不动数据）",
    )
    parser.add_argument(
        "--skip-code-check", action="store_true",
        help="跳过程序代码 DDL 与 dev 库的比对（不推荐：dev 陈旧时会一起带偏 prod）",
    )
    parser.add_argument(
        "--prune-legacy", action="store_true",
        help="删除 prod 上已知的历史遗留表（仅当表为空；会先自动备份）",
    )
    parser.add_argument("--dev", default=str(DEV_DB), help="开发库路径")
    parser.add_argument("--prod", default=str(PROD_DB), help="生产库路径")
    args = parser.parse_args()

    dev_path = Path(args.dev)
    prod_path = Path(args.prod)
    for p, label in ((dev_path, "dev"), (prod_path, "prod")):
        if not p.exists():
            log(f"[FATAL] {label} 库不存在：{p}")
            return 2

    # ── 前置：确认 dev 库本身就是"当前代码"的结构 ──
    # 否则 prod 会被对齐到一个陈旧基准上（两边一致但都不是当前代码的结构）。
    if not args.skip_code_check:
        rc = check_dev_matches_code(dev_path)
        if rc != 0:
            return rc

    if args.prune_legacy:
        rc = prune_legacy(prod_path)
        if rc != 0:
            return rc
        log("")

    dev = schema_snapshot(dev_path)
    prod = schema_snapshot(prod_path)
    diffs = diff_schemas(dev, prod)
    report(diffs, dev_path, prod_path)
    report_legacy(dev, prod, prod_path)

    if not args.align:
        if diffs:
            log("\n提示：执行 `python scripts/check_db_schema.py --align` 可自动对齐。")
            return 1
        return 0

    log("\n开始对齐 prod 结构到 dev …")
    rc = align(prod_path, dev_path, diffs)
    if rc != 0:
        return rc

    # 复验
    dev2 = schema_snapshot(dev_path)
    prod2 = schema_snapshot(prod_path)
    left = diff_schemas(dev2, prod2)
    log("")
    if left:
        log(f"对齐后仍有 {len(left)} 处差异（通常是 prod 多出的对象，需人工确认）：")
        for d in left:
            log(f"  [{d['kind']}] {d['target']}")
        # 只把"dev 有 prod 无"视为失败；prod 多出来的历史对象不算失败
        blocking = [
            d for d in left
            if d["kind"] in {
                "missing_table", "missing_index", "missing_column",
                "table_ddl_mismatch", "index_ddl_mismatch",
            }
        ]
        return 1 if blocking else 0
    log("对齐完成，结构一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

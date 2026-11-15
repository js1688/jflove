#!/usr/bin/env python3
"""复盘门禁：收件箱结构 + 报告模板 + 敏感内容扫描。

管的是两件事，退出码即判据（可直接当 CI / 提交前门禁）：

1. **收件箱卫生**（`.agents/skills/lessons/INBOX.md`）
   - 表头必须是固定的 8 列（列头被改 ⇒ 下游 triage 无法解析）；
   - 每行 8 格、日期 `YYYY-MM-DD`、复发为数字、状态 ∈ {open, triaged, rejected}；
   - `open` 条目 ≤ 40、文件 ≤ 200 行（超了先 triage，别继续堆）。

2. **脱敏**（收件箱 + `文档记录/技能复盘/**/*.md`）
   - 私钥块、已知 token 前缀（`sk-` / `ghp_` / `AKIA` / `AIza` / `xox?-`）、JWT、`Bearer <长串>`、
     `password=` / `token=` 这类赋值（值 ≥ 12 字符）、含真实用户名的绝对家目录路径；
   - 需要保留原文时写 `<redacted>`；确需放行的行尾加 `retro:allow` 注释。

3. **报告模板**（`文档记录/技能复盘/<YYYY-MM>.md`）
   - 必须含「## 本次运行判据」小节（否则无法证明这次运行是被验证过的）；
   - ≤ 180 行（报告是给人决策的，不是原始日志转储）。

用法：
    python scripts/check_retro.py

退出码：0 = 通过；1 = 有问题；2 = 环境问题（路径不存在等）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 控制台编码：Windows 默认 GBK，中文输出会乱码 / 报错
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[1]
INBOX = REPO_ROOT / ".agents" / "skills" / "lessons" / "INBOX.md"
REPORT_DIR = REPO_ROOT / "文档记录" / "技能复盘"

# 收件箱表头（顺序不可变；脚本按它判定列数）
REQUIRED_COLUMNS = ("日期", "角色/任务", "现象", "证据", "归因", "复发", "建议动作", "状态")
STATUSES = {"open", "triaged", "rejected"}
MAX_OPEN = 40
MAX_INBOX_LINES = 200
MAX_REPORT_LINES = 180
REPORT_SECTION = "## 本次运行判据"
REPORT_NAME_RE = re.compile(r"^\d{4}-\d{2}\.md$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 行尾放行标记：复盘里确实需要贴原文时用（如日志片段），但要留下痕迹
ALLOW_MARKER = "retro:allow"

# 高置信度敏感模式：(名称, 正则)
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("私钥块", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("OpenAI 风格 key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("AWS AK", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.")),
    ("Bearer 长串", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    (
        "凭据赋值",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\b\s*[:=]\s*"
            r"[\"']?[A-Za-z0-9._+/=\-]{12,}"
        ),
    ),
    ("CLI 密码参数", re.compile(r"(?i)--(password|passwd|token|secret)[= ]\S{6,}")),
    (
        "含用户名的绝对家目录",
        re.compile(
            r"/home/[A-Za-z0-9_][A-Za-z0-9_.\-]{1,}/"
            r"|[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9_][A-Za-z0-9_.\-]{1,}"
        ),
    ),
)


def _split_row(line: str) -> list[str]:
    """把 markdown 表格行拆成单元格（去掉首尾竖线与两侧空白）。"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def check_inbox() -> tuple[list[str], list[str]]:
    """校验收件箱结构与卫生，返回 (错误, 警告)。"""
    errors: list[str] = []
    warnings: list[str] = []
    if not INBOX.is_file():
        errors.append(f"{_rel(INBOX)}: 收件箱不存在（AGENTS.md §6.5 A 要求它作为唯一入口）")
        return errors, warnings

    lines = INBOX.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_INBOX_LINES:
        errors.append(
            f"{_rel(INBOX)}: {len(lines)} 行，超过上限 {MAX_INBOX_LINES} —— 先 triage 再写新的"
        )

    header_index = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("|") and "日期" in line),
        None,
    )
    if header_index is None:
        errors.append(f"{_rel(INBOX)}: 找不到表头（应含 {' / '.join(REQUIRED_COLUMNS)}）")
        return errors, warnings

    header = _split_row(lines[header_index])
    if tuple(header) != REQUIRED_COLUMNS:
        errors.append(
            f"{_rel(INBOX)}:{header_index + 1}: 表头必须恰好是 {len(REQUIRED_COLUMNS)} 列 "
            f"{' | '.join(REQUIRED_COLUMNS)}；实际 {' | '.join(header)}"
        )
        return errors, warnings

    open_count = 0
    triaged = 0
    for index in range(header_index + 2, len(lines)):  # +2 跳过分隔行
        raw = lines[index]
        if not raw.lstrip().startswith("|"):
            break
        cells = _split_row(raw)
        where = f"{_rel(INBOX)}:{index + 1}"
        if len(cells) != len(REQUIRED_COLUMNS):
            errors.append(f"{where}: {len(cells)} 列，应为 {len(REQUIRED_COLUMNS)} 列（多了/少了竖线会错列）")
            continue
        date, _who, phenomenon, evidence, _cause, recurrence, _action, status = cells
        if not DATE_RE.match(date):
            errors.append(f"{where}: 日期 `{date}` 不是 YYYY-MM-DD")
        if not phenomenon or not evidence:
            errors.append(f"{where}: 「现象」「证据」不得为空（无证据的复盘在 triage 时会被驳回）")
        if not recurrence.isdigit():
            errors.append(f"{where}: 「复发」必须是数字，实际 `{recurrence}`")
        if status not in STATUSES:
            errors.append(f"{where}: 状态 `{status}` 不在 {sorted(STATUSES)} 内")
        elif status == "open":
            open_count += 1
        else:
            triaged += 1

    if open_count > MAX_OPEN:
        errors.append(f"{_rel(INBOX)}: open 条目 {open_count} 条，超过上限 {MAX_OPEN} —— 先 triage")
    if open_count == 0 and triaged == 0:
        warnings.append(f"{_rel(INBOX)}: 表里还没有任何条目（示例行记得删）")

    return errors, warnings


def check_reports() -> tuple[list[str], list[str]]:
    """校验月度报告模板与长度。"""
    errors: list[str] = []
    warnings: list[str] = []
    if not REPORT_DIR.is_dir():
        errors.append(f"{_rel(REPORT_DIR)}/: 目录不存在（skills-self-growth 的产出位置）")
        return errors, warnings

    reports = sorted(path for path in REPORT_DIR.glob("*.md") if REPORT_NAME_RE.match(path.name))
    if not reports:
        warnings.append(f"{_rel(REPORT_DIR)}/: 还没有月度报告（首次运行后应出现 <YYYY-MM>.md）")
    for path in reports:
        text = path.read_text(encoding="utf-8")
        line_count = len(text.splitlines())
        if line_count > MAX_REPORT_LINES:
            errors.append(
                f"{_rel(path)}: {line_count} 行，超过上限 {MAX_REPORT_LINES} —— 报告是给人决策的，不是日志转储"
            )
        if REPORT_SECTION not in text:
            errors.append(f"{_rel(path)}: 缺少「{REPORT_SECTION}」小节（无法证明本次运行被验证过）")
    return errors, warnings


def scan_secrets() -> list[str]:
    """扫描收件箱与全部复盘报告，返回错误列表。"""
    errors: list[str] = []
    targets = [INBOX] if INBOX.is_file() else []
    if REPORT_DIR.is_dir():
        targets.extend(sorted(REPORT_DIR.rglob("*.md")))
    for path in targets:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if ALLOW_MARKER in line:
                continue
            for label, pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if match is None:
                    continue
                errors.append(
                    f"{_rel(path)}:{line_no}: 疑似{label} `{match.group(0)[:40]}` —— "
                    f"改写为 <redacted>；确需保留时行尾加 `{ALLOW_MARKER}`"
                )
    return errors


def _rel(path: Path) -> str:
    """优先显示仓库内相对路径（报告里不泄漏绝对家目录）。"""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def main() -> int:
    if not REPO_ROOT.is_dir():
        print(f"[ERROR] 仓库根不存在：{_rel(REPO_ROOT)}")
        return 2

    print(f"仓库根：{REPO_ROOT.name}/")
    print(f"收件箱：{_rel(INBOX)}")
    print(f"报告目录：{_rel(REPORT_DIR)}/")

    errors: list[str] = []
    warnings: list[str] = []
    for collector in (check_inbox, check_reports):
        found_errors, found_warnings = collector()
        errors.extend(found_errors)
        warnings.extend(found_warnings)
    errors.extend(scan_secrets())

    for warning in warnings:
        print(f"\n[WARN] {warning}")
    for error in errors:
        print(f"\n[ERROR] {error}")

    print(f"\n错误 {len(errors)} 项，警告 {len(warnings)} 项。")
    if errors:
        print("结论：不通过（退出码 1）—— 先修结构或脱敏，再提交。")
        return 1
    print("结论：通过（退出码 0）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

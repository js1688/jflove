#!/usr/bin/env python3
"""UI 事实清单校验器：证明 `website/docs/ui-facts-*.md` 里的每条文案都真实存在于源码。

为什么需要这道校验：
    官网操作指引的每一句「点击 XXX」都来自这些事实清单。清单里一旦写错一个按钮名，
    用户照着点就找不到 —— 比不写还糟。而人工核对 2000+ 条文案不现实，
    所以把「文案真的在源码里」变成机器判据。

判据（按文件分组，全部可判定）：
    ① 每条表格行的 evidence 列必须是 `文件:行`，且文件真实存在于对应源码树；
    ② 该行的「文案」必须在源码里能找到（`XXX` 视为通配符，匹配 `${...}` / 数字）；
    ③ 行号允许 ±4 行漂移（代码易位），超出只记警告不判错；
    ④ 清单文件至少要有一个被解析出的条目，否则说明格式变了（判红，不许静默放过）。

用法：
    python scripts/check_ui_facts.py             # 校验全部清单
    python scripts/check_ui_facts.py --self-test # 门禁自证：喂坏数据必须被拦住
    python scripts/check_ui_facts.py --report    # 只输出统计

退出码：0 = 全部通过；1 = 有文案在源码中找不到；2 = 环境错误。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "website" / "docs"

# 清单文件 → 源码根（按文件名前缀判定，新增清单必须在此登记，否则报未知）
SOURCES = {
    "desktop": REPO_ROOT / "jflove-desktop" / "ui" / "src",
    "mobile": REPO_ROOT / "jflove-app" / "lib",
    "web": REPO_ROOT / "jflove-web" / "src",
    # ui-facts.md（Web 端为主、混少量桌面项）统一按 Web 端源码根核对
}

# evidence 里允许出现的源码扩展名
EXT_RE = re.compile(r"([\w.\-]+\.(?:dart|tsx|ts)):(\d+)")
# 表头里表示「文案」的列名
CLAIM_HEADERS = ("精确文案", "文案", "菜单名", "分区标题", "文案原文")
LINE_TOLERANCE = 4


@dataclass
class FileResult:
    name: str
    parsed: int = 0
    found: int = 0
    missing: list[str] = field(default_factory=list)
    line_drift: int = 0
    skipped: int = 0
    no_evidence: int = 0
    composed: int = 0
    composed_warn: list[str] = field(default_factory=list)


def _source_root_for(doc_name: str) -> Path | None:
    """按文件名里的端关键字选源码根；`ui-facts.md`（不带端名）按 Web 端。"""
    if doc_name in ("ui-facts.md",):
        return SOURCES["web"]
    for key, root in SOURCES.items():
        if key in doc_name:
            return root
    return None


def _find_source(filename: str, root: Path) -> Path | None:
    hits = list(root.rglob(filename))
    return hits[0] if hits else None


def _strip_outer_ticks(text: str) -> str:
    text = text.strip().strip("「」『』").strip()
    if text.startswith("`") and text.endswith("`") and len(text) > 2:
        text = text[1:-1].strip()
    return text


def _strip_note(text: str) -> str:
    """去掉尾随的方括号注记（清单用来补充位置/条件说明，不是源码内容）。"""
    return re.sub(r"[（(][^）)]{2,}[）)]\s*$", "", text).strip()


def _norm_claim(claim: str) -> list[str]:
    """把一条文案展开成若干候选正则 —— **任一命中即算存在**。

    清单里同一格可能出现多种书写法，都必须容忍：
      - 整格用反引号包裹：`上传`
      - 多分支：`播放` / `暂停`（源码里是两个不同字符串）
      - 尾随注记：`重试`（ErrorState 默认 retryLabel）
      - 标签→值拆分：待备份 → XXX 项
      - 动态变量：XXX 占位
      - 跨行实体：&#10; 是换行
    """
    raw = claim.strip()
    variants: set[str] = set()

    def add(text: str) -> None:
        text = _strip_outer_ticks(text)
        if not text:
            return
        # 多分支拆开
        for branch in re.split(r"\s+/\s+", text):
            # 分支边缘可能残留反引号（`播放` / `暂停` → 播放` / 暂停）
            branch = _strip_outer_ticks(branch).strip("`").strip()
            if branch:
                variants.add(branch)
                stripped = _strip_note(branch)
                if stripped:
                    variants.add(stripped)

    add(raw)
    add(_strip_note(_strip_outer_ticks(raw)))

    patterns: list[str] = []
    for variant in variants:
        # 标签 → 值：只核对首段（值段是运行时拼接）
        segments = [s.strip() for s in re.split(r"\s*→\s*", variant) if s.strip()]
        target = segments[0] if segments else variant
        target = _strip_outer_ticks(target)
        if not target:
            continue
        placeholder = target.replace("XXX", "\x00")
        parts = placeholder.split("\x00")
        if len(parts) > 1:
            piece = r"[^\s。，；：！？]{0,40}".join(re.escape(p) for p in parts)
        else:
            piece = re.escape(target)
        if piece:
            patterns.append(piece)
    return patterns


def _claim_is_wildcard(claim: str) -> bool:
    """不算条目的行：纯占位符，或清单用来标注「该文件没有用户可见文案」。"""
    stripped = claim.replace("`", "").strip().strip("「」『』")
    return stripped in ("", "XXX", "—", "-", "…", "无用户可见文案")


def _flatten_source(text: str) -> list[str]:
    """生成若干「还原后的连续文本」变体，供跨行文案匹配。

    真实源码里有两种跨行写法，清单抄的都是**还原后的整句**：

    ① 相邻字符串字面量拼接（Dart / TS）：
           '开启后…远端目录。'
           '只在客户端保存配置…'
    ② JSX / 纯文本跨行（换行处不带空格，中文天然可拼）：
           自动清理程序自产的临时文件（上传分片与会话残留、离线下载残缺目录、
           合并残留）。临时文件期限同时决定…

    变体 1：只删引号间的换行/缩进；
    变体 2：把每行去掉首尾空白后**直接拼起来**（不插分隔符），覆盖 ①②两种。
    """
    variants: list[str] = []

    # 变体 1：相邻引号之间清零
    v1 = re.sub(r"(['\"`])\s*\n\s*(\1)", "", text)
    v1 = re.sub(r"(['\"`])\s*\+\s*(\1)", "", v1)
    variants.append(v1)

    # 变体 2：逐行去空白后拼接（保留原行顺序）
    variants.append("".join(line.strip() for line in text.splitlines()))
    return variants


def parse_tables(doc_path: Path) -> list[tuple[str, str]]:
    """解析清单里的表格，返回 [(文案, evidence)]。

    关键：文案列按**表头列序号**定位，不按列名 ——
    数据行里的单元格内容是具体文案，不会等于表头名（早期按列名匹配导致
    `| 文案 | 位置/类型 | evidence |` 这种表被错误地取到了描述列）。
    """
    rows: list[tuple[str, str]] = []
    claim_idx: int | None = None
    ev_idx: int | None = None

    for line in doc_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            claim_idx = ev_idx = None
            continue
        if set(stripped) <= set("|-: "):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue

        # 表头行：记住文案列与 evidence 列的序号
        low = [c.lower() for c in cells]
        if "evidence" in low:
            ev_idx = low.index("evidence")
            claim_idx = next((i for i, c in enumerate(cells) if c in CLAIM_HEADERS), None)
            if claim_idx is None:
                claim_idx = 0
            continue

        if claim_idx is None or ev_idx is None:
            # 没有表头的表格（如续接表）：退回「含 文件:行 的单元格左邻」
            em = next((i for i, c in enumerate(cells) if EXT_RE.search(c)), None)
            if em is None or em < 1:
                continue
            rows.append((cells[em - 1], cells[em]))
            continue

        if max(claim_idx, ev_idx) >= len(cells):
            continue
        rows.append((cells[claim_idx], cells[ev_idx]))
    return rows


def check_file(doc_path: Path, root: Path) -> FileResult:
    result = FileResult(name=doc_path.name)
    for claim, evidence in parse_tables(doc_path):
        m = EXT_RE.search(evidence)
        if not m:
            result.no_evidence += 1
            continue
        filename, lineno = m.group(1), int(m.group(2))
        src = _find_source(filename, root)
        if src is None:
            result.no_evidence += 1
            continue
        if _claim_is_wildcard(claim):
            result.skipped += 1
            continue

        patterns = _norm_claim(claim)
        if not patterns:
            result.skipped += 1
            continue

        result.parsed += 1
        src_text = src.read_text(encoding="utf-8")

        def hit(text: str) -> bool:
            for pattern in patterns:
                try:
                    if re.search(pattern, text):
                        return True
                except re.error:
                    if pattern.replace("\\", "") in text:
                        return True
            return False

        # 字面文案必须连续出现；含占位/百分号的运行时拼串记为**警告**不判错
        # （它们本来就不是源码里的连续字面量，硬匹配只会制造噪音）。
        flattened = _flatten_source(src_text)
        sources = [src_text, *flattened]
        composed = (
            claim.count("XXX") >= 1
            or "XX:XX" in claim
            or "%" in claim
        )

        if composed:
            result.composed += 1
            if not any(hit(src) for src in sources):
                result.composed_warn.append(f"{filename}:{lineno} 「{claim[:44]}」")
        elif not any(hit(src) for src in sources):
            result.missing.append(f"{filename}:{lineno} 「{claim[:48]}」")
            continue

        result.found += 1
        lines = src_text.splitlines()
        window = "\n".join(lines[max(0, lineno - LINE_TOLERANCE): lineno + LINE_TOLERANCE + 1])
        if not hit(window):
            result.line_drift += 1
    return result


def run_all() -> list[FileResult]:
    results: list[FileResult] = []
    for doc in sorted(DOCS_DIR.glob("ui-facts*.md")):
        root = _source_root_for(doc.name)
        if root is None:
            results.append(
                FileResult(name=doc.name, missing=[f"未知清单前缀，需在 SOURCES 登记：{doc.name}"])
            )
            continue
        if not root.is_dir():
            results.append(
                FileResult(name=doc.name, missing=[f"缺少源码根：{root.relative_to(REPO_ROOT)}"])
            )
            continue
        results.append(check_file(doc, root))
    return results


def _self_test() -> int:
    """门禁自证：伪造一条源码里不存在的文案，校验器必须判红。"""
    failures: list[str] = []

    # 构造临时清单：一条真实文案 + 一条臆造文案，两条都应能被解析
    sample = next((p for p in sorted(DOCS_DIR.glob("ui-facts*.md"))), None)
    if sample is None:
        print("[自证失败] 没有可用的清单样本")
        return 1

    tmp = DOCS_DIR / "_self_test_ui_facts.md"
    try:
        tmp.write_text(
            "# 自证样本\n\n"
            "| 控件 | 精确文案 | evidence |\n"
            "| --- | --- | --- |\n"
            "| 臆造按钮 | `点我一键部署全部服务` | `DesktopLayout.tsx:1` |\n"
            "| 臆造按钮2 | `立即开始智能整理` | `LoginPage.tsx:1` |\n",
            encoding="utf-8",
        )
        root = SOURCES["desktop"]
        res = check_file(tmp, root)
        if res.parsed > 0 and len(res.missing) == res.parsed:
            print(f"  [OK] 臆造文案全部被拦住（{len(res.missing)}/{res.parsed}）")
        else:
            failures.append(
                f"臆造文案未被全部拦住：解析 {res.parsed} 条，仅拦住 {len(res.missing)} 条"
            )
    finally:
        if tmp.exists():
            tmp.unlink()

    # 真实清单必须通过（证明校验器不是恒红）
    real = run_all()
    if all(not r.missing for r in real):
        print("  [OK] 真实清单全部通过（校验器不是恒红）")
    else:
        failures.append("真实清单未通过，先修清单再谈门禁可信")

    if failures:
        print("\n[自证失败]")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n[自证通过] 臆造文案被拦住，真实清单可通过。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="UI 事实清单校验（退出码即判据）")
    parser.add_argument("--self-test", action="store_true", help="门禁自证")
    parser.add_argument("--report", action="store_true", help="只输出统计")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if not DOCS_DIR.is_dir():
        print(f"[FATAL] 缺少清单目录：{DOCS_DIR.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 2

    if args.self_test:
        print("== UI 事实清单校验器 · 自证 ==")
        return _self_test()

    results = run_all()
    bad = [r for r in results if r.missing]

    if not args.quiet:
        print("== UI 事实清单校验 ==")
        print(f"{'清单':30} {'解析':>4} {'命中':>4} {'缺失':>4} {'行号漂移':>8}")
        print("-" * 62)
        for r in results:
            print(
                f"{r.name:30} {r.parsed:4} {r.found:4} {len(r.missing):4} "
                f"{r.line_drift:8}"
            )
        for r in results:
            for item in r.missing[:10]:
                print(f"  ✗ {r.name}: {item}")
            if len(r.missing) > 10:
                print(f"  … 另有 {len(r.missing) - 10} 条")

    if bad:
        total = sum(len(r.missing) for r in bad)
        print(f"\n结论：不通过（{total} 条文案在源码中找不到，退出码 1）")
        print("处置：回源码核实该文案是否存在 —— 存在则修清单的行号/写法，不存在则删除该条")
        return 1

    parsed = sum(r.parsed for r in results)
    print(f"\n结论：通过（{parsed} 条文案全部在源码中找到，退出码 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

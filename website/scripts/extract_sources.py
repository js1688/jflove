#!/usr/bin/env python3
"""官网内容抽取器：把项目真身解析成 `website/content/*.json`。

设计原则（对应 AGENTS.md §1「模块边界」与 §8.3「文档职责边界」）：
    **只读项目、只写 website/content/** —— 不复制粘贴项目文档，而是每次现场重新解析，
    让「官网漏更新」变成一次可判定的失败，而不是靠人工记得去改。

抽取来源（全部是唯一真相，官网不另存副本）：
    - 功能特性：根 `README.md` 的「功能特性」区段（速览表 + 小节标题）
    - API 路由：`jflove-server/src/controllers/*.py` 的 `APIRouter(prefix=...)` + `@router.<method>(...)`
    - 明文白名单：`jflove-server/src/main.py` 的 `_PLAIN_PATHS`
    - 版本号：仓库根 `version.json`

用法：
    python website/scripts/extract_sources.py            # 解析并写 content/*.json
    python website/scripts/extract_sources.py --check    # 只比对，不写盘；不一致退出 1
    python website/scripts/extract_sources.py --print    # 解析结果打到 stdout（调试用）

退出码：
    0 = 解析成功（--check 时表示与磁盘上已有一致）
    1 = 解析失败或 --check 发现不一致（结构漂移，必须人工处理，不得静默跳过）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")

WEBSITE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = WEBSITE_DIR.parent

CONTENT_DIR = WEBSITE_DIR / "content"
README_PATH = REPO_ROOT / "README.md"
VERSION_PATH = REPO_ROOT / "version.json"
CONTROLLERS_DIR = REPO_ROOT / "jflove-server" / "src" / "controllers"
MAIN_PY_PATH = REPO_ROOT / "jflove-server" / "src" / "main.py"

# 输出文件名（门禁按这三个名字读，改名必须同步 scripts/check_website_sync.py）
FEATURES_FILE = "features.json"
ROUTES_FILE = "api-routes.json"
META_FILE = "meta.json"


class ExtractError(RuntimeError):
    """解析失败：宁可红，不得静默跳过。"""


# ── 通用工具 ────────────────────────────────────────────────────────
def _read(path: Path) -> str:
    if not path.is_file():
        raise ExtractError(f"缺少必要文件：{path}")
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """取 `## <heading>` 到下一个二级标题之间的正文（不含标题行本身）。"""
    pattern = rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise ExtractError(f"README.md 找不到「## {heading}」区段（结构漂移，需人工确认）")
    return match.group(1)


def _split_row(line: str) -> list[str]:
    """按 `|` 切表格行；本项目表格内容里没有转义管道，故直接切即可。"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(cells: list[str]) -> bool:
    """表头后的分隔行：全部由 - / : 组成。"""
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells)


# ── 抽取 1：功能特性（速览表 + 小节） ──────────────────────────────
def extract_features() -> dict:
    """解析 README「功能特性」：速览表行 + `###` 小节标题。

    表与小节必须**一一对应**（AGENTS.md §8.3 要求「两处同一次改」）；
    数量或顺序不一致即视为遗漏，直接报错，不做任何容错猜测。
    """
    section = _section(_read(README_PATH), "功能特性")
    lines = section.splitlines()

    rows: list[dict] = []
    header: list[str] | None = None
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break  # 表格结束
            continue
        cells = _split_row(stripped)
        if header is None:
            header = cells
            in_table = True
            continue
        if _is_separator(cells):
            continue
        if len(cells) != len(header):
            raise ExtractError(
                f"功能特性速览表列数不一致：期望 {len(header)} 列，实际 {len(cells)} 列 → {stripped[:60]}"
            )
        rows.append(dict(zip(header, cells)))

    if header is None:
        raise ExtractError("README「功能特性」区段里找不到速览表")
    if not rows:
        raise ExtractError("README「功能特性」速览表没有任何数据行")

    sections = re.findall(r"^###\s+(.+?)\s*$", section, re.MULTILINE)
    table_names = [row[header[0]] for row in rows]

    if len(sections) != len(table_names):
        missing = [name for name in table_names if name not in sections]
        extra = [name for name in sections if name not in table_names]
        raise ExtractError(
            "功能特性速览表与小节数量不一致（AGENTS.md §8.3 要求同一次改完）：\n"
            f"  表 {len(table_names)} 行 / 小节 {len(sections)} 个\n"
            f"  表有而小节缺：{missing or '无'}\n"
            f"  小节有而表缺：{extra or '无'}"
        )
    if table_names != sections:
        raise ExtractError(
            "功能特性速览表顺序与小节顺序不一致（速览表行序 = 小节序）：\n"
            f"  表：{table_names}\n  节：{sections}"
        )

    platform_keys = [key for key in header if key not in (header[0], header[1])]
    features = []
    for row in rows:
        name = row[header[0]]
        features.append(
            {
                "name": name,
                "summary": row[header[1]],
                "supports": {key: row[key] for key in platform_keys},
            }
        )

    return {
        "source": "README.md#功能特性",
        "platforms": platform_keys,
        "count": len(features),
        "features": features,
    }


# ── 抽取 2：API 路由 ────────────────────────────────────────────────
_PREFIX_RE = re.compile(r'APIRouter\(\s*prefix\s*=\s*["\']([^"\']+)["\']')
# 一次匹配 method + 路径：`\s*` 同时覆盖单行 `@router.post("/x"` 与多行
# `@router.get(\n    "/x",\n`，此前拆成两次匹配时锚点落在 `(` 上，多行/单行都会错，勿再拆。
# ⚠ 必须允许**空路径** `""`（prefix 自身即完整路径，如 `GET /api/v1/users`）——
#   v1.10.21 实测有 8 条这种写法（config 2 / file 1 / note 1 / user 2 / virtual-disk 2），
#   早期版本用 `+` 写字符类会静默漏掉它们（66/74），已由 DECORATOR_COUNT_RE 兜底。
_ROUTE_RE = re.compile(
    r'@router\.(get|post|put|delete|patch)\(\s*["\']([^"\']*)["\']'
)
# 兜底计数：文件里出现的装饰器总数，必须等于解析出的路由数（防正则再漏）
_DECORATOR_COUNT_RE = re.compile(r"@router\.(?:get|post|put|delete|patch)\(")
_SUMMARY_RE = re.compile(r'summary\s*=\s*["\']([^"\']+)["\']')
_TAGS_RE = re.compile(r'tags\s*=\s*\[([^\]]*)\]')
# 装饰器结束：位于行首的 `)`（参数区内部描述文本都带缩进，不会落在行首）
_DECORATOR_END_RE = re.compile(r"\n\)")


def extract_routes() -> dict:
    """解析全部 controller 的 router 声明，产出路由清单。

    两种写法都要覆盖（项目里都存在）：
      ① 单行：`@router.post("/cancel", summary="取消任务")`
      ② 多行：`@router.get(\n    "/disks",\n    summary="...",\n)`
    """
    if not CONTROLLERS_DIR.is_dir():
        raise ExtractError(f"缺少 controller 目录：{CONTROLLERS_DIR}")

    controllers = []
    total = 0
    for path in sorted(CONTROLLERS_DIR.glob("*_controller.py")):
        text = _read(path)
        prefix_match = _PREFIX_RE.search(text)
        if prefix_match is None:
            raise ExtractError(
                f"{path.name}: 找不到 `APIRouter(prefix=...)` —— 路由无法归属，禁止静默跳过"
            )
        prefix = prefix_match.group(1)

        tags_match = _TAGS_RE.search(text)
        tags = []
        if tags_match is not None:
            tags = re.findall(r'["\']([^"\']+)["\']', tags_match.group(1))

        routes = []
        for match in _ROUTE_RE.finditer(text):
            # summary 只在本装饰器的参数区里找：从路由声明处截到行首 `)` 为止。
            # 不截断的话会把下一个路由的 summary 误挂上来（静默错配）。
            end_match = _DECORATOR_END_RE.search(text, match.end())
            limit = end_match.start() if end_match else len(text)
            header = text[match.start():limit]
            summary_match = _SUMMARY_RE.search(header)
            routes.append(
                {
                    "method": match.group(1).upper(),
                    "path": f"{prefix}{match.group(2)}",
                    "summary": summary_match.group(1) if summary_match else "",
                }
            )

        if not routes:
            raise ExtractError(f"{path.name}: 有 prefix 但解析出 0 条路由（解析口径已失效）")

        # 兜底：装饰器出现次数必须等于解析出的路由数，否则说明正则漏了某种写法。
        # 这道断言就是 v1.10.21 发现「66 vs 74」漏 8 条空路径路由的那道闸，不要移除。
        declared = len(_DECORATOR_COUNT_RE.findall(text))
        if declared != len(routes):
            raise ExtractError(
                f"{path.name}: 装饰器声明 {declared} 条，但只解析出 {len(routes)} 条 —— "
                "存在无法识别的路由写法，抽取器需更新（禁止静默漏条）"
            )

        total += len(routes)
        controllers.append(
            {
                "file": f"jflove-server/src/controllers/{path.name}",
                "prefix": prefix,
                "tags": tags,
                "count": len(routes),
                "routes": routes,
            }
        )

    if not controllers:
        raise ExtractError(f"{CONTROLLERS_DIR} 下没有任何 `*_controller.py`")

    return {
        "source": "jflove-server/src/controllers/*.py",
        "count": total,
        "controllers": controllers,
    }


# ── 抽取 3：明文白名单 + 版本元信息 ────────────────────────────────
_PLAIN_PATHS_RE = re.compile(r"_PLAIN_PATHS\s*=\s*\((.*?)\)", re.DOTALL)


def extract_meta() -> dict:
    main_text = _read(MAIN_PY_PATH)
    plain_match = _PLAIN_PATHS_RE.search(main_text)
    if plain_match is None:
        raise ExtractError("jflove-server/src/main.py 找不到 `_PLAIN_PATHS` 常量（安全宪法 §9.1）")
    plain_paths = re.findall(r'["\']([^"\']+)["\']', plain_match.group(1))
    if not plain_paths:
        raise ExtractError("`_PLAIN_PATHS` 解析为空 —— 明文白名单不允许为空（安全宪法 §9.1）")

    version_data = json.loads(_read(VERSION_PATH))
    version = version_data.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version or ""):
        raise ExtractError(f"version.json 的 version 非法：{version!r}")

    return {
        "product": "JFLove",
        "version": version,
        "plain_paths": plain_paths,
        "sources": [
            "README.md#功能特性",
            "jflove-server/src/controllers/*.py",
            "jflove-server/src/main.py#_PLAIN_PATHS",
            "version.json",
        ],
    }


# ── 组装 / 写盘 / 校验 ─────────────────────────────────────────────
def build() -> dict[str, dict]:
    """返回 {文件名: 内容}。时间戳单独处理（--check 时不参与比较）。"""
    return {
        FEATURES_FILE: extract_features(),
        ROUTES_FILE: extract_routes(),
        META_FILE: extract_meta(),
    }


def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _strip_timestamp(data: dict) -> dict:
    """去掉易变字段，供 --check 比较。"""
    return {k: v for k, v in data.items() if k != "generated_at"}


def main() -> int:
    parser = argparse.ArgumentParser(description="从项目真身抽取官网内容数据")
    parser.add_argument("--check", action="store_true", help="只比对不写盘，不一致退出 1")
    parser.add_argument("--print", dest="show", action="store_true", help="打印解析结果")
    args = parser.parse_args()

    try:
        payload = build()
    except ExtractError as exc:
        print(f"[extract][FATAL] {exc}", file=sys.stderr)
        print("结论：解析失败（退出码 1）—— 项目结构已漂移，需人工确认抽取口径。", file=sys.stderr)
        return 1

    if args.show:
        print(_dump(payload[FEATURES_FILE]))
        print(_dump(payload[ROUTES_FILE]))
        print(_dump(payload[META_FILE]))

    status = 0
    for filename, data in payload.items():
        target = CONTENT_DIR / filename
        if filename == META_FILE:
            data = {**data, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

        if args.check:
            if not target.is_file():
                print(f"[extract][FAIL] 缺少 {target.relative_to(REPO_ROOT)}（先跑一次同步）", file=sys.stderr)
                status = 1
                continue
            existing = json.loads(target.read_text(encoding="utf-8"))
            if _strip_timestamp(existing) != _strip_timestamp(data):
                print(
                    f"[extract][FAIL] {target.relative_to(REPO_ROOT)} 与项目真身不一致"
                    "（项目已变更，官网未跟上）",
                    file=sys.stderr,
                )
                status = 1
            continue

        CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(_dump(data), encoding="utf-8")
        print(f"[extract] 已写入 {target.relative_to(REPO_ROOT)}")

    if args.check and status == 0:
        print("[extract] 内容数据与项目真身一致")
    return status


if __name__ == "__main__":
    sys.exit(main())

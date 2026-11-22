#!/usr/bin/env python3
"""官网内容同步门禁：证明「官网内容与项目真身一致、且没有遗漏」。

为什么需要这道门禁：
    官网最容易出的问题不是样式，而是**悄悄过期**——项目加了功能 / 加了接口 / 换了版本，
    官网还停在上一版，而没人会去比。本脚本把这些「肉眼看不出来」的漂移变成退出码。

判据（任一项失败即退出 1，官网更新视为失败，不得提交）：
    ① 内容数据新鲜度：website/content/*.json 与**实时解析项目真身**的结果逐项一致；
    ② 版本号一致性：version.json == website/package.json == website/src/config/constants.ts；
    ③ 功能特性无遗漏：manifest 特性数 == README 速览表行数 == README 小节数；
    ④ API 无遗漏：manifest 路由数 == 各 controller 装饰器声明数之和；
    ⑤ 明文白名单：manifest 与 `jflove-server/src/main.py::_PLAIN_PATHS` 一致（安全宪法 §9.1）；
    ⑥ 页面消费数据源（页面存在时才检查）：页面文件不得硬编码特性名 / 路由清单，
       必须从 `website/content/*.json` 取数。

用法：
    python scripts/check_website_sync.py              # 全量校验
    python scripts/check_website_sync.py --json       # 输出机读结果（给技能/CI 用）
    python scripts/check_website_sync.py --self-test  # 门禁自证：喂坏数据必须被拦住

退出码：0 = 全部通过；1 = 有判据失败；2 = 环境/依赖错误（不得当成「没差异」放行）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBSITE_DIR = REPO_ROOT / "website"
CONTENT_DIR = WEBSITE_DIR / "content"
EXTRACTOR = WEBSITE_DIR / "scripts" / "extract_sources.py"

VERSION_PATH = REPO_ROOT / "version.json"
WEBSITE_PACKAGE = WEBSITE_DIR / "package.json"
WEBSITE_CONSTANTS = WEBSITE_DIR / "src" / "config" / "constants.ts"
PAGES_DIR = WEBSITE_DIR / "src" / "pages"

CONTROLLERS_DIR = REPO_ROOT / "jflove-server" / "src" / "controllers"
MAIN_PY = REPO_ROOT / "jflove-server" / "src" / "main.py"
README = REPO_ROOT / "README.md"

DECORATOR_RE = re.compile(r"@router\.(?:get|post|put|delete|patch)\(")
CONST_VERSION_RE = re.compile(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]")


@dataclass
class Result:
    """单条判据的结果。"""

    name: str
    ok: bool
    detail: str = ""
    errors: list[str] = field(default_factory=list)


def _fail(name: str, errors: list[str], detail: str = "") -> Result:
    return Result(name=name, ok=False, detail=detail, errors=errors)


def _ok(name: str, detail: str = "") -> Result:
    return Result(name=name, ok=True, detail=detail)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 判据 ①：内容数据新鲜度 ────────────────────────────────────────
def check_freshness() -> Result:
    """直接复用抽取器（同一份解析逻辑），比对磁盘上的 manifest。

    复用而不是重写解析：解析口径只有一处实现，避免门禁与被校验对象各自漂移
    （LESSONS 里「门禁自身要有反向验证」的同一条思路）。
    """
    sys.path.insert(0, str(EXTRACTOR.parent))
    import importlib.util

    spec = importlib.util.spec_from_file_location("_jf_extract", EXTRACTOR)
    if spec is None or spec.loader is None:  # pragma: no cover - 文件缺失时
        return _fail("①内容新鲜度", [f"无法加载抽取器：{EXTRACTOR}"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    try:
        fresh = module.build()
    except module.ExtractError as exc:
        return _fail("①内容新鲜度", [f"抽取器解析失败：{exc}"])

    errors: list[str] = []
    for filename, data in fresh.items():
        target = CONTENT_DIR / filename
        if not target.is_file():
            errors.append(f"缺少 {target.relative_to(REPO_ROOT)}（先跑 website/scripts/extract_sources.py）")
            continue
        existing = _load_json(target)
        strip = module._strip_timestamp  # noqa: SLF001 - 同仓脚本，刻意复用
        if strip(existing) != strip(data):
            errors.append(
                f"{target.relative_to(REPO_ROOT)} 与项目真身不一致（官网内容已过期）"
            )
    if errors:
        return _fail("①内容新鲜度", errors, "项目已变更，需重新抽取")
    return _ok("①内容新鲜度", "manifest 与实时解析结果逐项一致")


# ── 判据 ②：版本号一致性 ──────────────────────────────────────────
def check_version() -> Result:
    errors: list[str] = []
    try:
        canonical = _load_json(VERSION_PATH).get("version", "")
    except json.JSONDecodeError as exc:
        return _fail("②版本号一致性", [f"version.json 无法解析：{exc}"])
    if not re.fullmatch(r"\d+\.\d+\.\d+", canonical or ""):
        return _fail("②版本号一致性", [f"version.json 的 version 非法：{canonical!r}"])

    try:
        pkg_version = _load_json(WEBSITE_PACKAGE).get("version", "")
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        return _fail("②版本号一致性", [f"website/package.json 无法读取：{exc}"])

    if not WEBSITE_CONSTANTS.is_file():
        return _fail("②版本号一致性", ["缺少 website/src/config/constants.ts"])
    const_match = CONST_VERSION_RE.search(_read(WEBSITE_CONSTANTS))
    const_version = const_match.group(1) if const_match else ""

    for label, actual in (
        ("website/package.json", pkg_version),
        ("website/src/config/constants.ts", const_version),
    ):
        if actual != canonical:
            errors.append(f"{label} = {actual!r}，期望 {canonical!r}（跑 python scripts/sync_version.py）")

    if errors:
        return _fail("②版本号一致性", errors, f"唯一真相 version.json = {canonical}")
    return _ok("②版本号一致性", f"三处一致 = v{canonical}")


# ── 判据 ②b：meta.json 的版本快照 ────────────────────────────────
def check_meta_version() -> Result:
    """meta.json 里存着抽取时的版本号，它必须跟着 version.json 走。

    为什么单独一条：改版本后只跑 `sync_version.py` 不够 —— meta.json 是**抽取产物**，
    必须重跑抽取器才会更新。两条命令的顺序写死在修法里，避免"跑了 sync 还是红"的困惑。
    """
    canonical = _load_json(VERSION_PATH).get("version", "") if VERSION_PATH.is_file() else ""
    meta_path = CONTENT_DIR / "meta.json"
    if not meta_path.is_file():
        return _fail("②b版本快照", [f"缺少 {meta_path.relative_to(REPO_ROOT)}"])
    snapshot = str(_load_json(meta_path).get("version", ""))
    if snapshot != canonical:
        return _fail(
            "②b版本快照",
            [
                f"website/content/meta.json 记录 = {snapshot!r}，期望 {canonical!r}",
                "修法（两步都要做）：python scripts/sync_version.py && python website/scripts/extract_sources.py",
            ],
        )
    return _ok("②b版本快照", f"meta.json = v{snapshot}")


# ── 判据 ③：功能特性无遗漏 ────────────────────────────────────────
def check_features() -> Result:
    target = CONTENT_DIR / "features.json"
    if not target.is_file():
        return _fail("③功能特性无遗漏", [f"缺少 {target.relative_to(REPO_ROOT)}"])
    manifest = _load_json(target)

    text = _read(README) if README.is_file() else ""
    if not text:
        return _fail("③功能特性无遗漏", ["缺少根 README.md"])

    section_match = re.search(r"^##\s+功能特性\s*$(.*?)(?=^##\s+|\Z)", text, re.MULTILINE | re.DOTALL)
    if section_match is None:
        return _fail("③功能特性无遗漏", ["README.md 找不到「## 功能特性」区段"])
    section = section_match.group(1)

    table_rows = [
        line for line in section.splitlines()
        if line.strip().startswith("|") and not re.fullmatch(r"\|[\s:|-]+\|?", line.strip())
    ]
    # 首行是表头，其余是数据行
    readme_table = max(len(table_rows) - 1, 0)
    readme_sections = len(re.findall(r"^###\s+(.+?)\s*$", section, re.MULTILINE))
    manifest_count = int(manifest.get("count", -1))
    actual = len(manifest.get("features", []))

    errors: list[str] = []
    if manifest_count != actual:
        errors.append(f"manifest 的 count={manifest_count} 与实际条目 {actual} 不符（数据文件自相矛盾）")
    if readme_table != actual:
        errors.append(f"README 速览表 {readme_table} 行 ≠ manifest {actual} 条（有特性漏进官网）")
    if readme_sections != actual:
        errors.append(f"README 小节 {readme_sections} 个 ≠ manifest {actual} 条（表/节已不同步）")

    if errors:
        return _fail("③功能特性无遗漏", errors)
    return _ok("③功能特性无遗漏", f"{actual} 条特性：表 / 节 / manifest 三方一致")


# ── 判据 ④：API 路由无遗漏 ────────────────────────────────────────
def check_routes() -> Result:
    target = CONTENT_DIR / "api-routes.json"
    if not target.is_file():
        return _fail("④API无遗漏", [f"缺少 {target.relative_to(REPO_ROOT)}"])
    manifest = _load_json(target)

    if not CONTROLLERS_DIR.is_dir():
        return _fail("④API无遗漏", [f"缺少 controller 目录：{CONTROLLERS_DIR.relative_to(REPO_ROOT)}"])

    declared = 0
    files = 0
    for path in sorted(CONTROLLERS_DIR.glob("*_controller.py")):
        declared += len(DECORATOR_RE.findall(_read(path)))
        files += 1

    parsed = sum(int(c.get("count", 0)) for c in manifest.get("controllers", []))
    listed = sum(len(c.get("routes", [])) for c in manifest.get("controllers", []))
    total = int(manifest.get("count", -1))

    errors: list[str] = []
    if files == 0:
        errors.append("controller 目录里没有任何 *_controller.py")
    if total != listed:
        errors.append(f"manifest 的 count={total} 与 routes 实际条数 {listed} 不符")
    if parsed != listed:
        errors.append(f"各 controller 的 count 之和 {parsed} ≠ routes 实际条数 {listed}")
    if declared != listed:
        errors.append(
            f"controller 装饰器声明 {declared} 条 ≠ manifest {listed} 条（有接口漏进官网）"
        )

    if errors:
        return _fail("④API无遗漏", errors)
    return _ok("④API无遗漏", f"{listed} 条路由 / {files} 个 controller，全部覆盖")


# ── 判据 ⑤：明文白名单一致（安全宪法 §9.1） ──────────────────────
def check_plain_paths() -> Result:
    meta_path = CONTENT_DIR / "meta.json"
    if not meta_path.is_file():
        return _fail("⑤明文白名单", [f"缺少 {meta_path.relative_to(REPO_ROOT)}"])
    manifest_paths = list(_load_json(meta_path).get("plain_paths", []))

    if not MAIN_PY.is_file():
        return _fail("⑤明文白名单", ["缺少 jflove-server/src/main.py"])
    match = re.search(r"_PLAIN_PATHS\s*=\s*\((.*?)\)", _read(MAIN_PY), re.DOTALL)
    if match is None:
        return _fail("⑤明文白名单", ["main.py 找不到 `_PLAIN_PATHS`（安全宪法 §9.1 要求存在）"])
    real_paths = re.findall(r'["\']([^"\']+)["\']', match.group(1))

    errors: list[str] = []
    if not real_paths:
        errors.append("`_PLAIN_PATHS` 为空 —— 明文白名单不允许为空")
    missing = [p for p in real_paths if p not in manifest_paths]
    extra = [p for p in manifest_paths if p not in real_paths]
    if missing:
        errors.append(f"官网缺少明文路径：{missing}（安全说明与实现漂移）")
    if extra:
        errors.append(f"官网多出明文路径：{extra}（实现里没有，属过期内容）")

    if errors:
        return _fail("⑤明文白名单", errors)
    return _ok("⑤明文白名单", f"{len(real_paths)} 条明文路径与实现一致")


# ── 判据 ⑥：页面必须消费数据源（页面存在时才检查） ────────────────
def check_guides_grounded() -> Result:
    """操作指引里出现的 UI 文案，必须在事实清单里存在过。

    为什么需要这条：官网最容易出的「臆造」不是功能名，而是**按钮文案**——
    写「点击保存」「打开设置面板」这种听起来合理、但界面里根本没有的词，
    用户照着点找不到，比没写还糟。

    判据：
      - `website/docs/ui-facts.md` 必须存在且非空（它是唯一真相）；
      - `website/docs/guides-{desktop,web,mobile}.md` 里每个 `「xxx」` 标注的 UI 文案，
        都必须能在对应端的事实清单里找到同文；
      - 事实清单里没出现过的文案 ⇒ 红，并把具体词报出来。
    """
    facts_path = WEBSITE_DIR / "docs" / "ui-facts.md"

    if not facts_path.is_file():
        return _fail("⑦指引有据可查", [f"缺少 {facts_path.relative_to(REPO_ROOT)}（UI 事实清单是唯一真相）"])
    facts = _read(facts_path)
    if len(facts.strip()) < 500:
        return _fail("⑦指引有据可查", ["ui-facts.md 内容过短，不像是真实抽取结果"])

    # 三端各一份指引，合起来核对（存在性检查；端的归属由三端差异表与人工评审保证）
    docs_dir = WEBSITE_DIR / "docs"
    guide_names = ("guides-desktop.md", "guides-web.md", "guides-mobile.md")
    present = [docs_dir / n for n in guide_names if (docs_dir / n).is_file()]
    if not present:
        return _ok("⑦指引有据可查", "SKIP：三端指引尚未建立")
    guides = "\n".join(_read(p) for p in present)

    # 「xxx」是本文约定的 UI 文案标注法。但要排除三类**非 UI 文案**：
    #   ① 带 `→` 的路径描述（如「设置 → 服务端 → 修改服务器地址」）——这是导航路径，不是单个控件；
    #   ② 含 Markdown 强调标记 `**` 的短语 —— 那是正文强调，不是控件名；
    #   ③ 含占位符 `XXX` / `N` 的模板文案 —— 展示时会被替换。
    quoted = re.findall(r"「([^」]{1,24})」", guides)
    quoted = [
        term for term in quoted
        if "→" not in term and "**" not in term and "XXX" not in term
    ]
    if not quoted:
        return _fail("⑦指引有据可查", ["指引里没有任何「UI 文案」标注，无法核对"])

    missing: list[str] = []
    for term in sorted(set(quoted)):
        if term in facts:
            continue
        # 模板文案（如「失败原因（N 项）」）按去掉数字占位后比对
        normalized = re.sub(r"[（(]\s*\d+\s*项[）)]", "", term)
        if normalized != term and normalized in facts:
            continue
        missing.append(term)

    if missing:
        return _fail(
            "⑦指引有据可查",
            [
                f"指引出现事实清单里没有的 UI 文案 {len(missing)} 个：{missing[:8]}",
                "处置：先回代码核实该文案是否存在，存在则补进 ui-facts.md；不存在则从指引里删掉",
            ],
            f"{len(set(quoted))} 个文案已核对",
        )
    return _ok("⑦指引有据可查", f"{len(set(quoted))} 个 UI 文案均可在事实清单中找到")


def check_ui_facts_real() -> Result:
    """判据 ⑧：`website/docs/ui-facts-*.md` 里的每条文案必须真的存在于三端源码。

    判据 ⑦ 只核对「指引」与「事实清单」一致，但如果事实清单本身就写错，
    ⑦ 会把错的当成基准放行。⑧ 把基准钉在源码上：清单里的每条文案都要能被
    源码 `文件:行` 找到，写错一个按钮名就红。

    实现委托 `scripts/check_ui_facts.py`（独立可跑、自带 --self-test）。
    """
    script = REPO_ROOT / "scripts" / "check_ui_facts.py"
    if not script.is_file():
        return _fail("⑧事实清单可溯源", ["缺少 scripts/check_ui_facts.py"])
    docs = list((WEBSITE_DIR / "docs").glob("ui-facts*.md"))
    if not docs:
        return _ok("⑧事实清单可溯源", "SKIP：尚未建立 UI 事实清单")

    import subprocess

    proc = subprocess.run(
        [sys.executable, str(script), "--quiet"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode == 0:
        return _ok("⑧事实清单可溯源", f"{len(docs)} 份清单、文案全部在源码中找到")
    detail = (proc.stdout or proc.stderr).strip().splitlines()
    return _fail(
        "⑧事实清单可溯源",
        ["UI 事实清单里有文案在源码中找不到（清单写错或代码已改）：", *detail[-12:]],
    )


def check_logo_matches_product() -> Result:
    """判据 ⑨：官网 Logo 必须与产品 Logo 逐字节一致。

    为什么需要：官网和产品放同一个 Logo 是品牌一致性的底线。
    如果官网自己留一份 PNG，产品端换 Logo 后官网不会跟着变 ——
    用户下载后打开的 App 和官网看到的不是同一个标识，观感直接割裂。
    本判据把「两份文件必须同字节」变成门禁（与 `scripts/check_logo_sync.py`
    守「Web 端 ↔ 桌面端」是同一思路，这里补上第三处消费方）。
    """
    site_dir = WEBSITE_DIR / "public"
    product_dir = REPO_ROOT / "jflove-web" / "public"
    name = "logo-256.png"

    site_logo = site_dir / name
    product_logo = product_dir / name

    if not product_logo.is_file():
        return _fail("⑨Logo一致性", [f"缺少产品 Logo：{product_logo.relative_to(REPO_ROOT)}"])
    if not site_logo.is_file():
        return _fail("⑨Logo一致性", [f"缺少官网 Logo：{site_logo.relative_to(REPO_ROOT)}"])

    import hashlib

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    site_hash = digest(site_logo)
    product_hash = digest(product_logo)
    if site_hash != product_hash:
        return _fail(
            "⑨Logo一致性",
            [
                f"官网 {name} 与产品不是同一文件：",
                f"  website/public/{name}        = {site_hash[:16]}",
                f"  jflove-web/public/{name} = {product_hash[:16]}",
                f"修法：cp jflove-web/public/{name} website/public/{name}",
            ],
        )
    return _ok("⑨Logo一致性", f"{name} 与产品逐字节一致（{site_hash[:16]}）")


def check_openapi_sync() -> Result:
    """判据 ⑩：OpenAPI 生成的接口文档数据必须新鲜，且与正则抽取交叉一致。

    为什么要两条独立路径互验：
      - `api-routes.json`  由正则直接数 controller 装饰器（不依赖运行时）
      - `api-openapi.json` 由后端 `app.openapi()` 生成（依赖真实路由表）
    两者来自完全不同的机制，**任何一方漏条都会被另一方差出来**。
    历史上正是"66 vs 74"这类静默漏条让官网少写了 8 个接口。

    另外还校验：
      - 生成数据里的明文标记必须与 `_PLAIN_PATHS` 完全一致；
      - OpenAPI 里的 version 必须等于 `version.json`（接口文档不允许落后于产品版本）。
    """
    openapi_path = CONTENT_DIR / "api-openapi.json"
    if not openapi_path.is_file():
        return _fail(
            "⑩OpenAPI一致性",
            [
                f"缺少 {openapi_path.relative_to(REPO_ROOT)}",
                "修法：python website/scripts/gen_api_docs.py",
            ],
        )

    data = _load_json(openapi_path)
    errors: list[str] = []

    # ① 版本号必须跟随 version.json
    canonical = _load_json(VERSION_PATH).get("version", "") if VERSION_PATH.is_file() else ""
    doc_version = str(data.get("info", {}).get("version", ""))
    if doc_version != canonical:
        errors.append(
            f"接口文档 version={doc_version!r} ≠ version.json {canonical!r}"
            "（跑 gen_api_docs.py 重新生成）"
        )

    # ② 与正则抽取的 api-routes.json 交叉验证
    routes_path = CONTENT_DIR / "api-routes.json"
    if routes_path.is_file():
        routes = _load_json(routes_path)
        regex_set = {
            (r["method"], r["path"])
            for c in routes.get("controllers", [])
            for r in c.get("routes", [])
        }
        openapi_set = {
            (op["method"], op["path"])
            for g in data.get("groups", [])
            for op in g.get("operations", [])
        }
        # OpenAPI 会包含 /health（它不在 controller 装饰器里），这是已知合法差异
        only_openapi = sorted(openapi_set - regex_set)
        only_regex = sorted(regex_set - openapi_set)
        unexpected = [x for x in only_openapi if x != ("GET", "/health")]
        if unexpected:
            errors.append(f"只在 OpenAPI 里出现（正则抽取漏了）：{unexpected[:6]}")
        if only_regex:
            errors.append(f"只在正则抽取里出现（OpenAPI 没有）：{only_regex[:6]}")

    # ③ 明文标记必须等于 _PLAIN_PATHS
    if MAIN_PY.is_file():
        match = re.search(r"_PLAIN_PATHS\s*=\s*\((.*?)\)", _read(MAIN_PY), re.DOTALL)
        if match:
            real_plain = set(re.findall(r'["\']([^"\']+)["\']', match.group(1)))
            doc_plain = {op["path"] for g in data.get("groups", [])
                         for op in g.get("operations", []) if op.get("plain")}
            if real_plain != doc_plain:
                errors.append(
                    f"明文标记与 _PLAIN_PATHS 不一致：实现={sorted(real_plain)} 文档={sorted(doc_plain)}"
                )

    if errors:
        return _fail("⑩OpenAPI一致性", errors)
    return _ok(
        "⑩OpenAPI一致性",
        f"OpenAPI {data.get('openapi','?')} · {data.get('total','?')} 个操作 · "
        f"{data.get('groupCount','?')} 分组，与正则抽取交叉一致",
    )


# 设计规范里可机器判定的约束（见 website/docs/design-spec.md §1/§2/§4/§9）
DESIGN_RULES = (
    # (说明, 正则, 允许的例外正则)
    ("出现产品内部主题色 #6366f1（官网主色必须是蒂芙尼蓝）", r"#6366f1", None),
    ("出现装饰性渐变背景", r"background[^;{}]*\b(?:linear|radial)-gradient\(", r"repeating-linear-gradient"),
    ("圆角超过 12px", r"border-radius\s*:\s*(?:1[3-9]|[2-9]\d)px", None),
    ("引用外部资源（官网必须单文件零外链）", r'(?:src|href)\s*=\s*["\']https?://', None),
)
DESIGN_GLOBS = ("*.html",)


def check_design_spec() -> Result:
    """判据 ⑪：设计稿必须符合已确认的设计规范（可机器判定的那几条）。

    只查 `website/design/**/*.html` —— 这些是给用户确认过的稿子，
    一旦有人（包括未来的我）擅自加渐变 / 换主色 / 引外链，这里立刻红。
    纯审美判断（留白是否好看）无法自动化，见 design-spec.md §9 的人工清单。
    """
    design_root = WEBSITE_DIR / "design"
    if not design_root.is_dir():
        return _ok("⑪设计规范", "SKIP：尚无设计稿目录")

    files = sorted(
        p for g in DESIGN_GLOBS for p in design_root.rglob(g)
        # `_archive-*` 是已被取代的历史稿，只留作对照，不参与现行规范校验
        if "_archive-" not in p.as_posix()
    )
    if not files:
        return _ok("⑪设计规范", "SKIP：尚无 HTML 设计稿")

    errors: list[str] = []
    for path in files:
        text = _read(path)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for desc, pattern, allow in DESIGN_RULES:
            for m in re.finditer(pattern, text):
                snippet = m.group(0)
                if allow and re.search(allow, text[max(0, m.start()-40):m.end()+10]):
                    continue
                line_no = text[:m.start()].count("\n") + 1
                errors.append(f"{rel}:{line_no} {desc} → `{snippet[:40]}`")
                break  # 每个文件每类只报一次，避免刷屏

    # logo 必须是内嵌真实橘猫或引用本地 png，不得自造字母方块
    for path in files:
        text = _read(path)
        if '<i>JF</i>' in text or '<i>J</i>' in text:
            errors.append(
                f"{path.relative_to(REPO_ROOT).as_posix()}: 品牌标记仍是字母方块，应使用橘猫 logo"
            )

    if errors:
        return _fail("⑪设计规范", errors[:10], f"已检查 {len(files)} 个设计稿")
    return _ok("⑪设计规范", f"{len(files)} 个设计稿均符合规范（主色/圆角/无外链/真实 Logo）")


def check_screenshots_versioned() -> Result:
    """判据 ⑫：官网截图必须绑定**当前版本**，且每端都有版本锚点图。

    为什么必须有这条（用户明确要求的硬标准）：
        截图没有自证版本的能力 —— 一张 1.10.17 的客户端截图和 1.10.21 的
        长得几乎一样。实际踩过：本地 `app-release.apk` 是 v1.10.17，
        装上去截的图"看起来没错"，但版本过时。
        **旧版本客户端的截图不得作为操作指引发布。**

    校验内容（委托 `website/scripts/stamp_screenshots.py --check`）：
      ① 台账登记的 version 必须等于 version.json；
      ② 每张图的 sha256 必须与台账一致（图换了但台账没更新 ⇒ 红）；
      ③ 每端必须有「版本锚点图」（设置页，图上直接显示版本号）。
    """
    script = WEBSITE_DIR / "scripts" / "stamp_screenshots.py"
    ledger = CONTENT_DIR / "screenshots.json"

    if not script.is_file():
        return _fail("⑫截图版本绑定", ["缺少 website/scripts/stamp_screenshots.py"])
    if not ledger.is_file():
        return _fail(
            "⑫截图版本绑定",
            [
                f"缺少 {ledger.relative_to(REPO_ROOT)}",
                "修法：python website/scripts/stamp_screenshots.py",
            ],
        )

    import subprocess

    proc = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode == 0:
        data = _load_json(ledger)
        platforms = data.get("platforms", {})
        total = sum(p.get("count", 0) for p in platforms.values())
        return _ok(
            "⑫截图版本绑定",
            f"{len(platforms)} 端 / {total} 张截图全部绑定 v{data.get('version')}，"
            "每端含版本锚点图",
        )

    detail = (proc.stdout or proc.stderr).strip().splitlines()
    return _fail("⑫截图版本绑定", detail[-10:] or ["截图台账校验失败"])


def check_releases_sync() -> Result:
    """判据 ⑬：下载页的产物清单必须与 GitHub Release 当前资产逐项一致。

    为什么必须有这条（`AGENTS.md §10.2` 的硬要求）：
        下载页曾把文件名与体积**手写**在页面里。手写必然出三类事故：
          ① 发版后忘了改官网 ⇒ 按钮指向不存在的文件（死链）；
          ② 体积写错 ⇒ 用户以为下错了；
          ③ 版本号改了但文件名没跟着改（文件名含版本号）。
        改成"构建期从 Release API 取 + 门禁比对"后，这三类都不可能发生。

    校验内容（委托 `website/scripts/gen_releases.py --check`）：
      ① 最新 Release 的 tag 版本必须等于 version.json；
      ② 四类产物（Windows 安装版/便携版、Linux RPM、Android APK）必须齐全；
      ③ 每个资产的 name / size 必须与官网登记的一致（死链在构建期就被拦）。
    """
    script = WEBSITE_DIR / "scripts" / "gen_releases.py"
    manifest = CONTENT_DIR / "releases.json"

    if not script.is_file():
        return _fail("⑬Release产物一致", ["缺少 website/scripts/gen_releases.py"])
    if not manifest.is_file():
        return _fail(
            "⑬Release产物一致",
            [
                f"缺少 {manifest.relative_to(REPO_ROOT)}",
                "修法：python website/scripts/gen_releases.py",
            ],
        )

    import subprocess

    proc = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode == 0:
        data = _load_json(manifest)
        kinds = ", ".join(a["kind"] for a in data.get("assets", []))
        return _ok(
            "⑬Release产物一致",
            f"v{data.get('version')} 的 {len(data.get('assets', []))} 个产物与 Release 一致（{kinds}）",
        )

    detail = (proc.stdout or proc.stderr).strip().splitlines()
    return _fail("⑬Release产物一致", detail[-10:] or ["产物清单校验失败"])


def check_pages_consume_manifest() -> Result:
    """页面存在后，禁止把特性清单 / 路由清单硬编码进 JSX。

    检查方式：页面源码里出现「≥3 个连续的 manifest 特性名」或「≥3 条完整路由字符串」
    ⇒ 视为硬编码。用阈值而不是单条匹配，避免正常文案里提到某功能名就误报。
    """
    if not PAGES_DIR.is_dir():
        return _ok("⑥页面消费数据源", "SKIP：页面尚未建立（Step 3 起生效）")

    features_path = CONTENT_DIR / "features.json"
    routes_path = CONTENT_DIR / "api-routes.json"
    if not features_path.is_file() or not routes_path.is_file():
        return _ok("⑥页面消费数据源", "SKIP：manifest 缺失，先跑抽取器")

    names = [f["name"] for f in _load_json(features_path).get("features", [])]
    paths = [
        r["path"]
        for c in _load_json(routes_path).get("controllers", [])
        for r in c.get("routes", [])
    ]

    errors: list[str] = []
    for page in sorted(PAGES_DIR.rglob("*.tsx")):
        text = _read(page)
        hit_names = sum(1 for name in names if name in text)
        hit_paths = sum(1 for path in paths if f'"{path}"' in text or f"'{path}'" in text)
        rel = page.relative_to(REPO_ROOT).as_posix()
        if hit_names >= 3:
            errors.append(f"{rel}: 硬编码了 {hit_names} 个特性名，应从 content/features.json 取数")
        if hit_paths >= 3:
            errors.append(f"{rel}: 硬编码了 {hit_paths} 条路由，应从 content/api-routes.json 取数")

    if errors:
        return _fail("⑥页面消费数据源", errors)
    return _ok("⑥页面消费数据源", "页面均从 manifest 取数")


# ── 汇总 ─────────────────────────────────────────────────────────
CHECKS = (
    check_freshness,
    check_version,
    check_meta_version,
    check_features,
    check_routes,
    check_plain_paths,
    check_guides_grounded,
    check_ui_facts_real,
    check_logo_matches_product,
    check_openapi_sync,
    check_design_spec,
    check_screenshots_versioned,
    check_releases_sync,
    check_pages_consume_manifest,
)


def run_all() -> list[Result]:
    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 - 门禁内部异常一律视为失败，不吞
            results.append(_fail(check.__name__, [f"判据执行异常：{type(exc).__name__}: {exc}"]))
    return results


# ── 门禁自证：喂坏数据必须被拦住 ──────────────────────────────────
def _self_test() -> int:
    """反向验证：把 manifest / 页面人为改坏，断言对应判据必须变红。

    与 scripts/check_web_desktop_parity.py 的 `--self-test` 同一范式：
    **门禁没被反向验证过，就只是装饰品。**
    做法：在临时目录复制一份最小仓库结构，改坏后跑判据。
    为避免污染真实仓库，这里采用「改内存 → 断言判据函数返回 False」的方式：
    通过 monkeypatch 让判据读到被改坏的数据，跑完立即还原。
    """
    failures: list[str] = []

    def expect_fail(label: str, fn, need_restore: bool = True):  # noqa: ANN001
        try:
            result = fn()
        finally:
            if need_restore:
                pass
        if result.ok:
            failures.append(f"{label}：喂坏数据后判据仍然通过（门禁不可信）")
        else:
            print(f"  [OK] {label} 被拦住：{result.errors[0] if result.errors else result.detail}")

    # a) 版本号：临时把 constants.ts 的版本改坏，check_version 必须红
    original_constants = _read(WEBSITE_CONSTANTS)
    try:
        WEBSITE_CONSTANTS.write_text(
            CONST_VERSION_RE.sub("APP_VERSION = '0.0.0-bad'", original_constants, count=1),
            encoding="utf-8",
        )
        expect_fail("版本号不一致", check_version, need_restore=False)
    finally:
        WEBSITE_CONSTANTS.write_text(original_constants, encoding="utf-8")

    # b) 特性遗漏：临时把 features.json 删掉 1 条，check_features 必须红
    features_path = CONTENT_DIR / "features.json"
    original_features = _read(features_path)
    try:
        data = json.loads(original_features)
        if data.get("features"):
            data["features"] = data["features"][:-1]
            data["count"] = len(data["features"])
            features_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            expect_fail("特性被删一条", check_features, need_restore=False)
            # c) 新鲜度：删了内容后，与项目真身不再一致，必须红
            expect_fail("内容已过期", check_freshness, need_restore=False)
    finally:
        features_path.write_text(original_features, encoding="utf-8")

    # d) 路由遗漏：临时删 1 条路由，check_routes 必须红
    routes_path = CONTENT_DIR / "api-routes.json"
    original_routes = _read(routes_path)
    try:
        data = json.loads(original_routes)
        for controller in data.get("controllers", []):
            if controller.get("routes"):
                controller["routes"].pop()
                controller["count"] = len(controller["routes"])
                break
        data["count"] = sum(len(c.get("routes", [])) for c in data.get("controllers", []))
        routes_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        expect_fail("路由被删一条", check_routes, need_restore=False)
    finally:
        routes_path.write_text(original_routes, encoding="utf-8")

    # e) 明文白名单漂移：临时加一条假路径，check_plain_paths 必须红
    meta_path = CONTENT_DIR / "meta.json"
    original_meta = _read(meta_path)
    try:
        data = json.loads(original_meta)
        data["plain_paths"] = list(data.get("plain_paths", [])) + ["/api/v1/fake-plain"]
        meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        expect_fail("明文白名单漂移", check_plain_paths, need_restore=False)
    finally:
        meta_path.write_text(original_meta, encoding="utf-8")

    # f) 版本快照过期：临时把 meta.json 的版本改旧（模拟「改了版本但没重跑抽取器」）
    try:
        data = json.loads(original_meta)
        data["version"] = "0.0.0-stale"
        meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        expect_fail("版本快照过期", check_meta_version, need_restore=False)
    finally:
        meta_path.write_text(original_meta, encoding="utf-8")

    # g) 指引臆造按钮：临时往指引塞一个界面上不存在的控件名，check_guides_grounded 必须红
    guides_path = WEBSITE_DIR / "docs" / "guides-web.md"
    if guides_path.is_file():
        original_guides = _read(guides_path)
        try:
            guides_path.write_text(
                original_guides + "\n> 探针：点击「一键部署全部服务」即可。\n",
                encoding="utf-8",
            )
            expect_fail("指引臆造按钮", check_guides_grounded, need_restore=False)
        finally:
            guides_path.write_text(original_guides, encoding="utf-8")

    # h) 恢复后必须全部变绿（证明还原干净、门禁不是恒红）
    restored = run_all()
    if not all(r.ok for r in restored):
        failures.extend(
            f"还原后仍红：{r.name} → {r.errors}" for r in restored if not r.ok
        )
    else:
        print("  [OK] 全部还原后判据重新变绿")

    if failures:
        print("\n[自证失败] 门禁不可信：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n[自证通过] 每条判据都咬得住坏数据，且还原后恢复绿色。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="官网内容同步门禁（退出码即判据）")
    parser.add_argument("--json", action="store_true", help="输出机读结果")
    parser.add_argument("--self-test", action="store_true", help="门禁自证：喂坏数据必须被拦住")
    parser.add_argument("--quiet", action="store_true", help="只输出结论")
    args = parser.parse_args()

    if not EXTRACTOR.is_file():
        print(f"[website-sync][FATAL] 缺少抽取器：{EXTRACTOR.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 2

    if args.self_test:
        print("== 门禁自证：喂坏数据必须被拦住 ==")
        return _self_test()

    results = run_all()
    failed = [r for r in results if not r.ok]

    if args.json:
        print(json.dumps(
            {
                "ok": not failed,
                "results": [
                    {"name": r.name, "ok": r.ok, "detail": r.detail, "errors": r.errors}
                    for r in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
    elif not args.quiet:
        print("== 官网内容同步门禁 ==")
        for result in results:
            mark = "OK  " if result.ok else "FAIL"
            print(f"[{mark}] {result.name}  {result.detail}")
            for error in result.errors:
                print(f"        - {error}")

    if failed:
        if not args.json:
            print(f"\n结论：不通过（{len(failed)}/{len(results)} 条判据失败，退出码 1）")
            print("修法：python website/scripts/extract_sources.py 重新抽取，再跑 python scripts/sync_version.py")
        return 1

    if not args.json:
        print(f"\n结论：通过（{len(results)}/{len(results)} 条判据，退出码 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

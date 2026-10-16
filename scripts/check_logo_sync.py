"""
JFLove 品牌 logo 跨端一致性核对（v1.5.1，AC-3）

背景：桌面端（`jflove-desktop/ui/public/`）与 Web 端（`jflove-web/public/`）是
**两套独立前端工程**，各自持有品牌资产副本（不共享源码、不跨工程引用）。
本脚本负责证明"两份副本是同一份美术资产"，把这条约束从"靠记忆"变成
**可执行判据**（依据：`.agents/skills/lessons/LESSONS.md` L12）。

用法：
    python scripts/check_logo_sync.py              # 核对，退出码 0 = 一致；1 = 有差异
    python scripts/check_logo_sync.py --self-test  # 反向验证（guard 的 guard）：
                                                   # 用**真实资产**构造 5 类坏数据，
                                                   # 断言脚本全部拦住；退出码 0 = 工具可信

## 判据（v1.5.1 Phase 5 审查后重写，修掉两个中等问题的盲区）

1. **资产清单动态发现**，不硬编码文件名 —— 任一侧 `public/` 下 `logo-*` 全部参与比对。
   （审查 M-1：原实现只比对写死的三档名单，`logo-512.png` 这类清单外资产会被**静默跳过**，
   两端内容完全不同也报"逐字节一致"+EXIT=0。）
2. **代码引用反查**：扫描两端前端源码与 `index.html` 里出现的 `logo-*.{png,svg,…}`，
   每个被引用的资产都必须在**两端**都存在且逐字节相同。
   （这条让"新增一档尺寸只改了一端"无法蒙混过关。）
3. **不静默跳过**：输出里显式列出扫描过的目录、参与比对的文件、以及被反查到的引用点
   （依据 `LESSONS.md` L18：静默跳过 = 假通过）。
4. **反向验证用真实输入**：`--self-test` 要求真实资产存在，缺失即失败；
   不允许退化成占位内容后还报"工具可信"。
   （审查 M-2：原实现在真实资产缺失时用 placeholder 继续跑，清单名写错也照样输出"工具可信"。）
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

# Windows 默认 stdout/stderr 编码为 cp1252，print 中文会 UnicodeEncodeError
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

#: 两端资产目录（唯一真相 = 猫猫美术资产，两端各持一份同字节副本）
WEB_ASSET_DIR = ROOT / "jflove-web" / "public"
DESKTOP_ASSET_DIR = ROOT / "jflove-desktop" / "ui" / "public"

#: 资产发现口径（两端 public/ 下所有 logo-* 文件都参与比对）
ASSET_GLOB = "logo-*"

#: 源码引用反查口径：两端前端源码 + index.html
SRC_SCAN_TARGETS = (
    ROOT / "jflove-web" / "src",
    ROOT / "jflove-web" / "index.html",
    ROOT / "jflove-desktop" / "ui" / "src",
    ROOT / "jflove-desktop" / "ui" / "index.html",
)
SKIP_DIR_NAMES = frozenset({"node_modules", "dist", ".vite", "__pycache__"})
#: 参与引用反查的文本后缀（R5-T5：把打包脚本/CI/安装脚本也纳入，避免"清单外引用"漏检）
TEXT_SUFFIXES = frozenset(
    {
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".html", ".json",
        ".py", ".iss", ".sh", ".yml", ".yaml", ".md",
    }
)

#: 引用形态：logo-xxx.(png|svg|…)，两端都用相对路径引用（./logo-128.png）
REFERENCE_RE = re.compile(
    r"logo-[A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpg|jpeg|webp|svg|gif|ico)", re.IGNORECASE
)


def sha256_of(path: Path) -> str:
    """计算文件 sha256（十六进制小写）；文件缺失返回空串"""
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_assets(asset_dir: Path) -> dict[str, Path]:
    """
    动态发现某端 `public/` 下的品牌资产（**不硬编码文件名**，修 M-1 的覆盖面盲区）。

    :param asset_dir: 该端的 public 目录
    :returns: {文件名: 路径}
    """
    if not asset_dir.is_dir():
        return {}
    return {p.name: p for p in sorted(asset_dir.glob(ASSET_GLOB)) if p.is_file()}


def scan_references(targets=SRC_SCAN_TARGETS) -> dict[str, list[str]]:
    """
    反查两端源码/HTML 里引用了哪些 logo 资产。

    :param targets: 要扫描的目录或文件
    :returns: {资产名: ["相对路径:行号", …]}
    """
    found: dict[str, list[str]] = {}
    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files.extend(
                p
                for p in sorted(target.rglob("*"))
                if p.is_file()
                and p.suffix.lower() in TEXT_SUFFIXES
                and not (SKIP_DIR_NAMES & set(p.relative_to(ROOT).parts))
            )
        elif target.is_file():
            files.append(target)

    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name in REFERENCE_RE.findall(line):
                key = name.lower()
                found.setdefault(key, []).append(f"{path.relative_to(ROOT)}:{lineno}")
    return found


def check(
    web_dir: Path,
    desktop_dir: Path,
    references: dict[str, list[str]],
) -> tuple[list[str], list[str]]:
    """
    执行核对。

    :param web_dir: Web 端资产目录
    :param desktop_dir: 桌面端资产目录
    :param references: 源码引用反查结果（{资产名: [引用点, …]}）
    :returns: (问题列表, 说明列表)；问题为空表示通过
    """
    problems: list[str] = []
    notes: list[str] = []

    if not web_dir.is_dir():
        return [f"目录不存在：{web_dir}"], notes
    if not desktop_dir.is_dir():
        return [f"目录不存在：{desktop_dir}"], notes

    web_assets = discover_assets(web_dir)
    desktop_assets = discover_assets(desktop_dir)
    notes.append(
        f"资产发现口径：两侧 `{ASSET_GLOB}` 全部参与比对 —— "
        f"web {len(web_assets)} 个 / desktop {len(desktop_assets)} 个"
    )

    only_web = sorted(set(web_assets) - set(desktop_assets))
    only_desktop = sorted(set(desktop_assets) - set(web_assets))
    for name in only_web:
        problems.append(f"仅 Web 端存在（桌面端缺失）：{name}")
    for name in only_desktop:
        problems.append(f"仅桌面端存在（Web 端缺失）：{name}")

    common = sorted(set(web_assets) & set(desktop_assets))
    for name in common:
        web_hash = sha256_of(web_assets[name])
        desktop_hash = sha256_of(desktop_assets[name])
        if web_hash != desktop_hash:
            problems.append(
                f"内容不一致：{name}\n    web      {web_hash}\n    desktop  {desktop_hash}"
            )

    # 代码引用反查：被引用的资产必须在两端都存在（否则运行时 404 / 品牌位空掉）
    referenced = sorted(references)
    notes.append(f"源码引用反查：共发现 {len(referenced)} 个被引用的 logo 资产 → {referenced or '（无）'}")
    for name in referenced:
        for label, assets in (("Web 端", web_assets), ("桌面端", desktop_assets)):
            if name not in assets:
                where = "、".join(references[name][:3])
                problems.append(f"{label} 引用了不存在的资产：{name}（引用点 {where}）")
    for name in common:
        if name not in references:
            notes.append(f"提示：{name} 在两端源码中均未被引用（保留但已是死资产）")

    return problems, notes


def exit_code_for(problems: list[str]) -> int:
    """
    判据的唯一落点：有问题 → 1，无问题 → 0。

    抽成函数是为了让 `--self-test` 能**直接覆盖退出码装配**（R5-T6：原自测只驱动
    `check()`，不覆盖 `main()` 的退出码分支）。

    :param problems: 问题列表
    :returns: 进程退出码
    """
    return 1 if problems else 0


def print_report(web_dir: Path, desktop_dir: Path, references: dict[str, list[str]]) -> list[str]:
    """打印核对明细并返回问题列表"""
    print("[logo-sync] 品牌资产跨端核对")
    print(f"  web     目录：{web_dir}")
    print(f"  desktop 目录：{desktop_dir}")
    problems, notes = check(web_dir, desktop_dir, references)
    for note in notes:
        print(f"  · {note}")
    web_assets = discover_assets(web_dir)
    desktop_assets = discover_assets(desktop_dir)
    for name in sorted(set(web_assets) | set(desktop_assets)):
        web_hash = sha256_of(web_assets[name]) if name in web_assets else ""
        desktop_hash = sha256_of(desktop_assets[name]) if name in desktop_assets else ""
        ok = bool(web_hash) and web_hash == desktop_hash
        print(
            f"  [{'OK ' if ok else 'BAD'}] {name:<16}"
            f" {web_hash[:16] or '(缺失)':<18} {desktop_hash[:16] or '(缺失)'}"
        )
    return problems


def _expect(label: str, problems: list[str], should_fail: bool) -> bool:
    """断言一次核对的结果符合预期（should_fail=True 表示"必须被拦住"）"""
    caught = bool(problems)
    good = caught == should_fail
    verdict = "已拦住（预期）" if caught else "通过（预期）"
    print(f"[self-test] {label}：{verdict if good else '不符合预期！'}")
    if not good:
        print(f"            实际 problems={problems}")
    return good


def self_test() -> int:
    """
    反向验证：用**真实资产**构造 5 类坏数据，要求脚本全部拦住、好数据放行。

    真实资产缺失时**直接失败**（不允许退化成占位内容后仍报"工具可信" —— 审查 M-2）。

    :returns: 退出码（0 = 工具可信）
    """
    web_real = discover_assets(WEB_ASSET_DIR)
    desktop_real = discover_assets(DESKTOP_ASSET_DIR)
    # 两侧资产名集合必须相同（任一侧为空也算缺失）——没有真实输入就不做自测
    missing = sorted(set(web_real) ^ set(desktop_real))
    if not web_real or missing:
        print("[self-test][FAIL] 无法用真实资产做反向验证：")
        print(f"  两侧资产 = web {sorted(web_real)} / desktop {sorted(desktop_real)}")
        print("  本自测拒绝用占位内容替代真实输入（否则等于给一个不存在的资产背书）。")
        return 1

    ok = True
    with tempfile.TemporaryDirectory(prefix="jflove-logo-selftest-") as tmp:
        base = Path(tmp)
        for case in ("identical", "dirty", "extra", "absent"):
            web_dir = base / case / "web"
            desktop_dir = base / case / "desktop"
            web_dir.mkdir(parents=True)
            desktop_dir.mkdir(parents=True)
            for name in sorted(web_real):
                shutil.copy2(web_real[name], web_dir / name)
                shutil.copy2(web_real[name], desktop_dir / name)
        # 反查用引用：指向一个**两端都不存在**的资产（模拟"只改了一端引用的新尺寸"）
        fake_reference = {"logo-512.png": ["jflove-web/src/fake.tsx:1"]}

        identical_web, identical_desktop = base / "identical" / "web", base / "identical" / "desktop"
        dirty_web, dirty_desktop = base / "dirty" / "web", base / "dirty" / "desktop"
        extra_web, extra_desktop = base / "extra" / "web", base / "extra" / "desktop"
        absent_web, absent_desktop = base / "absent" / "web", base / "absent" / "desktop"

        # ① 未改动副本 → 必须通过
        ok &= _expect("① 未改动副本", check(identical_web, identical_desktop, {})[0], should_fail=False)

        # ② 翻转 1 个字节 → 必须拦住
        target = dirty_web / sorted(web_real)[0]
        data = bytearray(target.read_bytes())
        data[0] ^= 0x01
        target.write_bytes(bytes(data))
        ok &= _expect("② 改坏 1 字节", check(dirty_web, dirty_desktop, {})[0], should_fail=True)

        # ③ 清单外的额外资产只加在一端（原实现的盲区）→ 必须拦住
        extra_name = "logo-512.png"
        shutil.copy2(sorted(web_real.values())[0], extra_web / extra_name)
        ok &= _expect(
            f"③ 仅一端新增 {extra_name}", check(extra_web, extra_desktop, {})[0], should_fail=True
        )

        # ④ 一端缺文件 → 必须拦住
        (absent_desktop / sorted(web_real)[0]).unlink()
        ok &= _expect("④ 一端缺文件", check(absent_web, absent_desktop, {})[0], should_fail=True)

        # ⑤ 源码引用了两端都不存在的资产 → 必须拦住
        problems_ref, _ = check(identical_web, identical_desktop, fake_reference)
        ok &= _expect("⑤ 引用不存在的资产", problems_ref, should_fail=True)

        # ⑥ 退出码装配（R5-T6）：有问题必须 1、无问题必须 0
        code_cases = (
            ("⑥a 有问题 → 退出码 1", exit_code_for(["x"]), 1),
            ("⑥b 无问题 → 退出码 0", exit_code_for([]), 0),
        )
        for label, actual, expected in code_cases:
            good = actual == expected
            print(f"[self-test] {label}：{'符合预期' if good else f'不符合预期（实际 {actual}）'}")
            ok &= good

    print("[self-test] 结论：", "工具可信（反向验证全部符合预期）" if ok else "工具不可信！")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="JFLove 品牌 logo 跨端一致性核对（v1.5.1）")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="反向验证：用真实资产构造坏数据，断言本脚本能拦住（L17；真实资产缺失即失败）",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    references = scan_references()
    problems = print_report(WEB_ASSET_DIR, DESKTOP_ASSET_DIR, references)
    if problems:
        print("\n[logo-sync][FAIL] 两端品牌资产不一致：")
        for item in problems:
            print(f"  - {item}")
        print("修复方式：以桌面端 `ui/public/logo-*.png` 为准，复制到 `jflove-web/public/`，")
        print("          并确保新增尺寸在**两端**都存在（本脚本会反查源码引用）。")
        return exit_code_for(problems)
    print("\n[logo-sync][OK] 两侧资产逐字节一致，且源码引用的资产两端均存在。")
    return exit_code_for(problems)


if __name__ == "__main__":
    sys.exit(main())

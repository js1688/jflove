"""
JFLove Web 端 ↔ 桌面端 UI 跨端一致性核对（v1.5.1，AC-15）

背景：`jflove-web/src` 与 `jflove-desktop/ui/src` 是**同源快照、各自独立演进**的两份前端工程
（`AGENTS.md §1` 模块边界：两套工程各持源码，不得互相引用）。
v1.5.1 要求其中至少 `src/pages/NoteEditPage.tsx` 与 `src/styles/components.css` **逐字节一致**
（需求 AC-15；审查报告 §十七第 4 条建议把这条约束也纳入门禁）。

在此之前，这条约束只靠"人工比一次 sha256"维持 —— 没有可执行判据。
本脚本把它变成 **命令 + 退出码**（依据：`.claude/skills/LESSONS.md` L12：
"约束要绑定到可执行的检查，否则等于没写"）。

用法：
    python scripts/check_web_desktop_parity.py              # 核对，退出码 0 = 一致；1 = 有问题
    python scripts/check_web_desktop_parity.py --self-test  # 反向验证（guard 的 guard，L17）：
                                                            # 用**真实文件**构造 4 类坏数据，
                                                            # 断言本脚本全部拦住；退出码 0 = 工具可信

## 判据（显式清单 + 逐文件 sha256）

1. **显式清单**（`PARITY_MANIFEST`）：清单内的相对路径必须在**两端都存在**且 sha256 相同。
   - **两侧都缺** → 判为"清单腐烂"（问题）：清单里写了一个仓库里根本不存在的文件，
     说明这份判据已经名存实亡 —— 必须报错，**不能静默当成通过**（`LESSONS.md` L18）。
   - **仅一侧存在** → 问题（另一端运行时必然缺文件 / 行为分叉）。
   - **内容不同** → 问题。
   - 全部一致 → 退出码 0。
2. **不静默跳过**：输出里显式列出「清单」「每个文件的比对结果」「哪些文件**未参与比对**及原因」。
   本脚本**只**对清单内文件下判据；两端 82 个同名文件里剩下的那些属各自独立演进范围，
   脚本只作**信息展示**（`[INFO]`），其状态**不参与退出码**——这一点必须在输出里写明，
   否则使用者会误以为"退出码 0 = 两端完全同源"。
3. **反向验证用真实文件**：`--self-test` 要求真实清单文件存在，**任一缺失即直接 FAIL**，
   不允许退化成占位内容后仍报"工具可信"（`LESSONS.md` L17 / 审查报告 M-2 的同类教训）。

## 判据的边界（本脚本**不**检查什么）

- 不检查两端**依赖链**（`package.json` / `tsconfig.json` / `vite.config.ts`）是否一致；
- 不检查清单外同名文件的差异（它们正是"各自独立演进"的合法产物）；
- 不检查**运行时产物**（`dist/`、桌面端 Qt 外壳的 Python 层）；
- 不检查两端渲染结果（像素/布局），那属真机人工验收（`LESSONS.md` L27/L40）。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from pathlib import Path

# Windows 默认 stdout/stderr 编码非 UTF-8，print 中文会 UnicodeEncodeError（LESSONS.md L7）
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

#: 两端前端工程根（各自独立，不互相引用）
WEB_ROOT = ROOT / "jflove-web"
DESKTOP_ROOT = ROOT / "jflove-desktop" / "ui"

#: 源码子目录名（两端同名；用于统计"未参与比对"的同名文件）
SRC_DIRNAME = "src"

#: **显式清单**：这些相对路径必须在两端逐字节一致（AC-15）。
#: 新增条目时必须同时确认两端文件真实存在，否则本脚本会报"清单腐烂"。
PARITY_MANIFEST: tuple[str, ...] = (
    "src/pages/NoteEditPage.tsx",
    "src/styles/components.css",
)

#: 统计"未参与比对"时跳过的目录（构建产物 / 依赖，不属源码树）
SKIP_DIR_NAMES = frozenset({"node_modules", "dist", ".vite", "__pycache__"})


def sha256_of(path: Path) -> str:
    """计算文件 sha256（十六进制小写）；文件缺失返回空串"""
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def list_src_files(project_root: Path) -> set[str]:
    """
    列出某端 `src/` 下的全部相对路径（posix 风格，便于两端直接比对）。

    :param project_root: 该端前端工程根
    :returns: 相对 `src/` 的路径集合
    """
    src = project_root / SRC_DIRNAME
    if not src.is_dir():
        return set()
    found: set[str] = set()
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        if SKIP_DIR_NAMES & set(path.parts):
            continue
        found.add(path.relative_to(src).as_posix())
    return found


def src_relative(rel: str) -> str:
    """
    把清单条目（相对**工程根**，如 `src/pages/NoteEditPage.tsx`）换算成相对 `src/` 的路径。

    清单按工程根书写（可读性更好、与需求 AC-15 的写法一致），而 `list_src_files()`
    返回的是相对 `src/` 的路径，两者必须换算后才能做集合运算 —— 否则清单里的文件
    会被误算进"未参与比对"列表（本脚本第一版就踩了这个坑，靠实跑输出发现）。

    :param rel: 清单条目
    :returns: 相对 `src/` 的 posix 路径
    """
    rel_posix = rel.replace("\\", "/").lstrip("./")
    prefix = f"{SRC_DIRNAME}/"
    if rel_posix.startswith(prefix):
        return rel_posix[len(prefix):]
    return rel_posix


def uncompared_same_name(manifest: tuple[str, ...], web_root: Path, desktop_root: Path) -> list[str]:
    """
    找出"两端同名、但不在清单里"的文件 —— 这些是**未参与判据**的部分，必须在输出里列出来。

    :param manifest: 显式清单
    :param web_root: Web 端工程根
    :param desktop_root: 桌面端工程根
    :returns: 排序后的相对路径列表（相对 `src/`）
    """
    web_files = list_src_files(web_root)
    desktop_files = list_src_files(desktop_root)
    same_name = web_files & desktop_files
    manifest_names = {src_relative(item) for item in manifest}
    return sorted(same_name - manifest_names)


def check(
    manifest: tuple[str, ...],
    web_root: Path = WEB_ROOT,
    desktop_root: Path = DESKTOP_ROOT,
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """
    执行跨端一致性核对（纯函数，便于 `--self-test` 直接驱动）。

    :param manifest: 显式清单（相对路径，posix 风格）
    :param web_root: Web 端工程根
    :param desktop_root: 桌面端工程根
    :returns: (问题列表, 明细行列表)；明细行 = (相对路径, web 哈希, desktop 哈希)
    """
    problems: list[str] = []
    rows: list[tuple[str, str, str]] = []

    if not web_root.is_dir():
        return [f"Web 端工程根不存在：{web_root}"], rows
    if not desktop_root.is_dir():
        return [f"桌面端工程根不存在：{desktop_root}"], rows
    if not manifest:
        return ["显式清单为空 —— 没有任何文件参与比对，本门禁形同虚设"], rows

    for rel in manifest:
        rel_posix = rel.replace("\\", "/")
        web_path = web_root / rel_posix
        desktop_path = desktop_root / rel_posix
        web_hash = sha256_of(web_path)
        desktop_hash = sha256_of(desktop_path)
        rows.append((rel_posix, web_hash, desktop_hash))

        web_exists = bool(web_hash)
        desktop_exists = bool(desktop_hash)

        if not web_exists and not desktop_exists:
            # 清单腐烂：清单里的文件在仓库里两边都找不到 ⇒ 判据已失效，必须报错（L18）
            problems.append(
                f"清单里的文件两端都不存在（清单腐烂）：{rel_posix}\n"
                f"    web      {web_path}\n"
                f"    desktop  {desktop_path}\n"
                f"    ⇒ 请修正 PARITY_MANIFEST 或补回文件；本条判据当前是空的。"
            )
        elif not web_exists:
            problems.append(
                f"仅桌面端存在（Web 端缺失）：{rel_posix}\n    web      {web_path}"
            )
        elif not desktop_exists:
            problems.append(
                f"仅 Web 端存在（桌面端缺失）：{rel_posix}\n    desktop  {desktop_path}"
            )
        elif web_hash != desktop_hash:
            problems.append(
                f"内容不一致：{rel_posix}\n    web      {web_hash}\n    desktop  {desktop_hash}"
            )

    return problems, rows


def exit_code_for(problems: list[str]) -> int:
    """
    判据的唯一落点：有问题 → 1，无问题 → 0。

    抽成函数是为了让 `--self-test` 能**直接覆盖退出码装配**（而不是只驱动 `check()`）。

    :param problems: 问题列表
    :returns: 进程退出码
    """
    return 1 if problems else 0


def print_report(
    manifest: tuple[str, ...],
    web_root: Path,
    desktop_root: Path,
) -> list[str]:
    """打印核对明细并返回问题列表"""
    print("[parity] Web 端 ↔ 桌面端 UI 跨端一致性核对（AC-15）")
    print(f"  web     工程根：{web_root}")
    print(f"  desktop 工程根：{desktop_root}")
    print(f"  清单（显式维护，共 {len(manifest)} 项）：")
    for rel in manifest:
        print(f"    - {rel}")

    problems, rows = check(manifest, web_root, desktop_root)

    print("  逐文件比对结果：")
    for rel, web_hash, desktop_hash in rows:
        same = bool(web_hash) and web_hash == desktop_hash
        print(
            f"    [{'OK ' if same else 'BAD'}] {rel:<32}"
            f" web={web_hash[:16] or '(缺失)':<18} desktop={desktop_hash[:16] or '(缺失)'}"
        )

    # 未参与比对的部分必须显式说明（L18：静默跳过 = 假通过）
    print("  未参与比对的部分（显式说明，避免误读为「两端完全同源」）：")
    skipped = uncompared_same_name(manifest, web_root, desktop_root)
    print(
        f"    · 两端同名但不在清单内的源码文件：{len(skipped)} 个 → "
        f"本脚本不对它们下判据（属各自独立演进的合法范围），仅信息展示："
    )
    for rel in skipped:
        web_hash = sha256_of(web_root / SRC_DIRNAME / rel)
        desktop_hash = sha256_of(desktop_root / SRC_DIRNAME / rel)
        mark = "同" if web_hash == desktop_hash else "异"
        print(f"        [INFO][{mark}] {rel}")
    web_only = sorted(list_src_files(web_root) - list_src_files(desktop_root))
    desktop_only = sorted(list_src_files(desktop_root) - list_src_files(web_root))
    print(f"    · 仅 Web 端存在的源码文件：{len(web_only)} 个（不参与比对，各自工程自有）")
    for rel in web_only:
        print(f"        [INFO] web-only      {rel}")
    print(f"    · 仅桌面端存在的源码文件：{len(desktop_only)} 个（不参与比对，各自工程自有）")
    for rel in desktop_only:
        print(f"        [INFO] desktop-only  {rel}")
    print("    · 另外**不检查**：两端构建配置（package.json/tsconfig/vite.config）、")
    print("      构建产物（dist/）、桌面端 Qt 外壳 Python 层、渲染像素（属真机人工验收）。")

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


def self_test(
    manifest: tuple[str, ...] = PARITY_MANIFEST,
    web_root: Path = WEB_ROOT,
    desktop_root: Path = DESKTOP_ROOT,
) -> int:
    """
    反向验证：用**真实文件**在临时目录构造 4 类坏数据，要求本脚本全部拦住、好数据放行。

    真实清单文件缺失时**直接失败** —— 不允许退化成占位内容后仍报"工具可信"（L17）。

    :returns: 退出码（0 = 工具可信）
    """
    real_sources: dict[str, Path] = {}
    for rel in manifest:
        rel_posix = rel.replace("\\", "/")
        path = web_root / rel_posix
        if not path.is_file():
            real_sources = {}
            missing_rel = rel_posix
            break
        real_sources[rel_posix] = path
    else:
        missing_rel = ""

    if not real_sources:
        print("[self-test][FAIL] 无法用真实文件做反向验证：")
        print(f"  Web 端缺失清单文件：{missing_rel}（工程根 {web_root}）")
        print("  本自测拒绝用占位内容替代真实输入（否则等于给一份不存在的清单背书）。")
        return 1

    ok = True
    with tempfile.TemporaryDirectory(prefix="jflove-parity-selftest-") as tmp:
        base = Path(tmp)

        def _build(case: str) -> tuple[Path, Path]:
            """在临时目录里造一对工程根，并复制真实清单文件到两端"""
            web_case = base / case / "web"
            desktop_case = base / case / "desktop"
            web_case.mkdir(parents=True)
            desktop_case.mkdir(parents=True)
            for rel_posix, src in sorted(real_sources.items()):
                for root in (web_case, desktop_case):
                    dst = root / rel_posix
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(src.read_bytes())
            return web_case, desktop_case

        identical_web, identical_desktop = _build("identical")
        dirty_web, dirty_desktop = _build("dirty")
        one_side_web, one_side_desktop = _build("one-side")
        rotten_web, rotten_desktop = _build("rotten")

        # ① 未改动副本 → 必须放行
        ok &= _expect(
            "① 未改动副本（逐字节一致）",
            check(manifest, identical_web, identical_desktop)[0],
            should_fail=False,
        )

        # ② 改 1 字节 → 必须拦住
        victim_rel = sorted(real_sources)[0]
        victim = dirty_web / victim_rel
        data = bytearray(victim.read_bytes())
        data[-1] ^= 0x01
        victim.write_bytes(bytes(data))
        ok &= _expect(
            f"② 改 1 字节（{victim_rel}）",
            check(manifest, dirty_web, dirty_desktop)[0],
            should_fail=True,
        )

        # ③ 仅一侧存在（另一端删除该文件）→ 必须拦住
        (one_side_desktop / victim_rel).unlink()
        ok &= _expect(
            f"③ 仅一侧存在（桌面端删除 {victim_rel}）",
            check(manifest, one_side_web, one_side_desktop)[0],
            should_fail=True,
        )

        # ④ 清单里写了不存在的文件（清单腐烂）→ 必须拦住
        rotten_manifest = tuple(manifest) + ("src/styles/does-not-exist-v151.css",)
        ok &= _expect(
            "④ 清单含两端都不存在的文件（清单腐烂）",
            check(rotten_manifest, rotten_web, rotten_desktop)[0],
            should_fail=True,
        )

        # ⑤ 退出码装配：有问题必须 1、无问题必须 0（判据的唯一落点）
        for label, actual, expected in (
            ("⑤a 有问题 → 退出码 1", exit_code_for(["x"]), 1),
            ("⑤b 无问题 → 退出码 0", exit_code_for([]), 0),
        ):
            good = actual == expected
            print(f"[self-test] {label}：{'符合预期' if good else f'不符合预期（实际 {actual}）'}")
            ok &= good

    print("[self-test] 结论：", "工具可信（反向验证全部符合预期）" if ok else "工具不可信！")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="JFLove Web 端 ↔ 桌面端 UI 跨端一致性核对（v1.5.1，AC-15）"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="反向验证：用真实文件构造坏数据，断言本脚本能拦住（L17；真实文件缺失即失败）",
    )
    # 运行前提可注入（LESSONS.md L41）：默认值是开发机仓库约定，但必须能被覆盖
    parser.add_argument("--web-root", type=Path, default=WEB_ROOT, help="Web 端工程根")
    parser.add_argument("--desktop-root", type=Path, default=DESKTOP_ROOT, help="桌面端 ui 工程根")
    args = parser.parse_args()

    if args.self_test:
        return self_test(PARITY_MANIFEST, args.web_root, args.desktop_root)

    problems = print_report(PARITY_MANIFEST, args.web_root, args.desktop_root)
    if problems:
        print("\n[parity][FAIL] 两端不一致（退出码 1）：")
        for item in problems:
            print(f"  - {item}")
        print("修复方式：以 Web 端 `jflove-web/` 为源，把同一份文件按字节复制到")
        print("          `jflove-desktop/ui/`（或反之），不要手写第二遍；")
        print("          若确需两端分叉，请从 PARITY_MANIFEST 中移除该文件并说明原因。")
        return exit_code_for(problems)
    print("\n[parity][OK] 清单内文件两端逐字节一致。")
    print("           注意：退出码 0 只覆盖上面的清单，**不**代表两端整体同源。")
    return exit_code_for(problems)


if __name__ == "__main__":
    sys.exit(main())

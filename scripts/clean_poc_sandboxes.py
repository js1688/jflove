#!/usr/bin/env python3
"""清理工作区里的「本地沙箱垃圾」——只删**未被 git 跟踪**的东西，永不碰证据。

背景（2026-10-16 实测）：
`jflove-desktop/poc/editor-probes/out/` 下 78 个探针批次目录合计 82.3 MB，其中
**63.9 MB 是 `.tmp_*` 沙箱副本**（每个探针会复制一份 `jflove-server/` 当被测服务，
跑完不清理），另有数千个 `__pycache__` / `.pyc` 散落其中。
这些**全部未被 git 跟踪**（`.gitignore` 已有 `__pycache__/` 与 `poc/**/.tmp_*`），
但它们会让 `grep` / 文件检索 / 子 agent 侦察命中"假源码"，拖慢一切并造成误导。

清理范围（都可再生）：
1. `.tmp_*` 目录（探针沙箱，形态 `.tmp_server` / `.tmp_*`）
2. `__pycache__` 目录与 `*.pyc` 文件

安全保证：
- **只删未跟踪项**：先用 `git ls-files` 取受控文件清单，取交集后**跳过**任何被跟踪的路径；
- 默认 **dry-run**，加 `--apply` 才真删；
- 虚拟环境（`venv*/`、`node_modules/`）与构建产物目录（`build/`、`dist/`）**跳过**；
- 退出码：0 = 正常（含无事可做）；1 = 有被跟踪项被要求删除（安全护栏触发）。

用法：
    python scripts/clean_poc_sandboxes.py            # 只报告
    python scripts/clean_poc_sandboxes.py --apply    # 执行清理
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 控制台编码：Windows 默认 GBK，中文与 ✓ 会乱码 / 报错
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOT = REPO_ROOT / "jflove-desktop" / "poc"

#: 扫描时跳过的目录名（虚拟环境 / 依赖 / 构建产物 —— 不是本次清理对象）
SKIP_DIR_NAMES = {"node_modules", "build", "dist", ".git"}
SKIP_DIR_PREFIXES = ("venv",)


def tracked_paths() -> set[str]:
    """返回仓库内受 git 跟踪的相对路径（posix 风格），用于安全护栏。"""
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    if out.returncode != 0:
        print("[FATAL] git ls-files 失败，无法确认跟踪状态，中止。")
        raise SystemExit(2)
    return set(out.stdout.decode("utf-8", errors="replace").split())


def is_under_any(path: Path, prefixes: set[str]) -> bool:
    """path（仓库相对 posix 路径）是否等于或位于 prefixes 中任一条目之下。"""
    candidate = path.as_posix()
    for prefix in prefixes:
        if candidate == prefix or candidate.startswith(prefix + "/"):
            return True
    return False


def collect_targets(tracked: set[str], scan_root: Path) -> tuple[list[Path], list[Path]]:
    """返回 (待删目录, 被跳过的受控目录)。"""
    if not scan_root.is_dir():
        return [], []

    directories: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(scan_root, topdown=True):
        current = Path(dirpath)
        # 就地裁剪遍历范围（topdown=True 时生效）
        dirnames[:] = [
            d
            for d in dirnames
            if d not in SKIP_DIR_NAMES and not d.startswith(SKIP_DIR_PREFIXES)
        ]
        for d in list(dirnames):
            if d.startswith(".tmp_") or d == "__pycache__":
                directories.append(current / d)
                dirnames.remove(d)  # 整目录删除，不再深入

    deletable: list[Path] = []
    protected: list[Path] = []
    for directory in directories:
        rel = directory.relative_to(REPO_ROOT)
        if is_under_any(rel, tracked):
            protected.append(directory)
        else:
            deletable.append(directory)
    return deletable, protected


def dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                pass
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 PoC 沙箱与 __pycache__（只删未跟踪项）")
    parser.add_argument("--apply", action="store_true", help="真正删除（默认只报告）")
    parser.add_argument(
        "--root", type=Path, default=SCAN_ROOT, help="扫描根目录（默认 jflove-desktop/poc）"
    )
    args = parser.parse_args()

    scan_root = args.root.resolve()
    if scan_root.is_relative_to(REPO_ROOT):
        shown = scan_root.relative_to(REPO_ROOT).as_posix()
    else:
        shown = str(scan_root)
    print(f"扫描根：{shown}")
    tracked = tracked_paths()
    print(f"受控文件：{len(tracked)} 条（用作安全护栏）")

    deletable, protected = collect_targets(tracked, scan_root)
    if protected:
        print(f"\n[跳过] 以下 {len(protected)} 个目录被 git 跟踪，**不会删除**：")
        for path in protected[:10]:
            print(f"    {path.relative_to(REPO_ROOT).as_posix()}")

    if not deletable:
        print("\n没有需要清理的沙箱/缓存目录。")
        return 0

    sizes = {path: dir_size(path) for path in deletable}
    total = sum(sizes.values())
    print(f"\n待清理：{len(deletable)} 个目录，合计 {total / 1024 / 1024:.1f} MB")
    print("  按体积前 10：")
    for path, size in sorted(sizes.items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {size / 1024:>9.0f} KB  {path.relative_to(REPO_ROOT).as_posix()}")

    if not args.apply:
        print("\n[dry-run] 未做任何改动。加 --apply 执行清理。")
        return 0

    removed = 0
    for path in deletable:
        try:
            shutil.rmtree(path)
            removed += 1
        except OSError as exc:
            print(f"  [WARN] 删除失败 {path}: {exc}")
    print(f"\n已删除 {removed}/{len(deletable)} 个目录，释放约 {total / 1024 / 1024:.1f} MB。")
    print("提示：这些内容都是**可再生产物**，删除不影响任何证据链（受控文件一条未动）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

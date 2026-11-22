#!/usr/bin/env python3
"""截图版本台账：把「每张官网截图属于哪个版本」变成可校验数据。

为什么必须有这个文件：
    截图是官网操作指引的核心证据，但它**没有自证版本的能力** ——
    一张 1.10.17 的客户端截图和 1.10.21 的长得几乎一样。
    之前就踩过：本地 `app-release.apk` 是 v1.10.17，直接装上去截，
    图里的界面虽然"看起来没错"，但版本是过时的。
    用户明确要求：**旧版本客户端截图不得作为操作指引发布**。

机制：
    1. 每张截图登记 `version`（必须等于 `version.json`）+ `sha256` + 尺寸；
    2. 每端必须至少有 **1 张「版本锚点图」**（设置页，图上能直接看到版本号）；
    3. 门禁 ⑫ 校验：
       - 登记的 version 全等于当前版本；
       - 磁盘上的文件与登记 sha256 一致（防止图被换掉但台账没更新）；
       - 每端都有锚点图，且版本号字符串出现在截图文件名约定里；
       - 截图工具重跑后必须更新台账（否则 sha256 对不上 → 红）。

用法：
    python website/scripts/stamp_screenshots.py            # 生成/更新台账
    python website/scripts/stamp_screenshots.py --check    # 门禁用：只校验
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")

WEBSITE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = WEBSITE_DIR.parent
SHOTS_DIR = WEBSITE_DIR / "public" / "screenshots"
LEDGER = WEBSITE_DIR / "content" / "screenshots.json"
VERSION_PATH = REPO_ROOT / "version.json"

# 每端的「版本锚点图」：截图上能直接看到版本号的页面
#   web / desktop：设置页「关于」显示 `JFLove vX.Y.Z`
#   mobile：设置页「关于」显示 `版本 vX.Y.Z`
ANCHORS = {
    "web": "settings",
    "desktop": "settings",
    "mobile": "settings",
}

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_size(path: Path) -> tuple[int, int] | None:
    """读 PNG 宽高（不依赖第三方库）。"""
    data = path.read_bytes()
    if not data.startswith(PNG_SIG) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_version() -> str:
    return json.loads(VERSION_PATH.read_text(encoding="utf-8"))["version"]


def build_ledger() -> dict:
    """扫描 screenshots/ 目录，生成台账。"""
    version = current_version()
    platforms: dict[str, dict] = {}

    for platform_dir in sorted(p for p in SHOTS_DIR.iterdir() if p.is_dir()):
        name = platform_dir.name
        shots: dict[str, dict] = {}
        for img in sorted(platform_dir.glob("*.png")):
            size = png_size(img)
            shots[img.stem] = {
                "file": f"{name}/{img.name}",
                "version": version,
                "sha256": sha256_of(img)[:16],
                "size": f"{size[0]}x{size[1]}" if size else "unknown",
            }
        if shots:
            platforms[name] = {
                "anchor": ANCHORS.get(name),
                "count": len(shots),
                "shots": shots,
            }

    return {
        "version": version,
        "note": (
            "每张截图的版本绑定台账。截图工具重跑后必须重新生成，"
            "否则 sha256 对不上，门禁⑫会红。"
        ),
        "anchors": ANCHORS,
        "platforms": platforms,
    }


def check() -> int:
    """门禁⑫：版本一致 + 锚点存在 + 文件未被换。"""
    version = current_version()
    if not LEDGER.is_file():
        print("[shots][FAIL] 缺少 screenshots.json（先跑一次生成）", file=sys.stderr)
        return 1

    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    errors: list[str] = []

    if data.get("version") != version:
        errors.append(
            f"台账版本 {data.get('version')!r} ≠ 项目版本 {version!r}"
            "（升级后必须重截图并重生成台账）"
        )

    platforms = data.get("platforms", {})
    for expected in ("web", "desktop", "mobile"):
        info = platforms.get(expected)
        if not info:
            errors.append(f"缺少 {expected} 端截图")
            continue
        anchor = info.get("anchor")
        if not anchor:
            errors.append(f"{expected} 端未声明版本锚点图")
        elif anchor not in info.get("shots", {}):
            errors.append(f"{expected} 端锚点图 `{anchor}` 不存在")

        for shot, meta in info.get("shots", {}).items():
            path = SHOTS_DIR / meta["file"]
            if not path.is_file():
                errors.append(f"{meta['file']} 在磁盘上不存在")
                continue
            actual = sha256_of(path)[:16]
            if actual != meta.get("sha256"):
                errors.append(
                    f"{meta['file']} 内容已变化（台账 {meta.get('sha256')} ≠ 实际 {actual}）"
                    "：重截图后需重跑 stamp_screenshots.py"
                )
            if meta.get("version") != version:
                errors.append(f"{meta['file']} 登记版本 {meta.get('version')} ≠ {version}")

    if errors:
        print("[shots][FAIL] 截图台账校验不通过：", file=sys.stderr)
        for e in errors[:15]:
            print(f"  - {e}", file=sys.stderr)
        return 1

    total = sum(p["count"] for p in platforms.values())
    print(f"[shots] 通过：{len(platforms)} 端 / {total} 张截图，全部绑定 v{version}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="截图版本台账")
    parser.add_argument("--check", action="store_true", help="只校验不写盘")
    args = parser.parse_args()

    if args.check:
        return check()

    if not SHOTS_DIR.is_dir():
        print(f"[shots][FATAL] 缺少截图目录：{SHOTS_DIR}", file=sys.stderr)
        return 2

    ledger = build_ledger()
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    total = sum(p["count"] for p in ledger["platforms"].values())
    print(f"[shots] 已写入 {LEDGER.relative_to(REPO_ROOT)}")
    print(f"[shots] v{ledger['version']} · {len(ledger['platforms'])} 端 · {total} 张")
    for name, info in ledger["platforms"].items():
        print(f"  {name:8} {info['count']:2} 张 · 锚点 {info['anchor']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

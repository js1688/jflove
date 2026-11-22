#!/usr/bin/env python3
"""从 GitHub Releases 拉取各端产物清单 → `content/releases.json`。

为什么必须有这个脚本（而不是页面里手写文件名和体积）：
    `AGENTS.md §10.2` 明确要求"各端产物：页面运行时拉取，不写死体积 / 文件名"。
    手写会带来三类必然事故：
      ① 发版后忘了改官网 ⇒ 下载按钮指向不存在的文件（死链）；
      ② 体积写错 ⇒ 用户以为下错了；
      ③ 版本号改了但文件名没跟着改（文件名里含版本号）。
    把它变成"构建期从 API 取 + 门禁比对"，这三类都不可能再发生。

时序前提（用户既定流程：**先发产品，最后更新官网**）：
    本脚本在官网构建期运行，此时 Release 一定已发布。若资产缺失，
    脚本**直接失败**并提示"等产品发完再更新官网" —— 这正是我们要的拦截点。

用法：
    python website/scripts/gen_releases.py            # 生成/更新 releases.json
    python website/scripts/gen_releases.py --check    # 门禁⑬：只校验不写盘

环境变量：
    GITHUB_TOKEN  可选。CI 里带上可避免匿名限流（60 次/小时/IP）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")

WEBSITE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = WEBSITE_DIR.parent
CONTENT_DIR = WEBSITE_DIR / "content"
RELEASES_FILE = CONTENT_DIR / "releases.json"
VERSION_FILE = REPO_ROOT / "version.json"

OWNER_REPO = "js1688/jflove"
API_URL = f"https://api.github.com/repos/{OWNER_REPO}/releases/latest"

#: 文件名特征 → 官网用的产物类别。
#: 顺序重要：先匹配更具体的，避免 `win64-portable.zip` 被泛化规则吃掉。
KIND_RULES: list[tuple[str, str]] = [
    ("win64-setup.exe", "windows-setup"),
    ("win64-portable.zip", "windows-portable"),
    (".rpm", "linux-rpm"),
    (".apk", "android-apk"),
]


def human_size(num: int) -> str:
    """字节数转人类可读（与下载页原有文案口径一致：1 位小数 + MB/GB）。"""
    mb = num / 1024 / 1024
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.1f} MB"


def classify(name: str) -> str | None:
    for needle, kind in KIND_RULES:
        if needle in name:
            return kind
    return None


def fetch_release() -> dict:
    req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "jflove-website-gen-releases")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - 固定 https 域名
        return json.loads(resp.read().decode("utf-8"))


def build_manifest(release: dict) -> dict:
    tag = release.get("tag_name", "")
    version = tag.lstrip("vV")

    assets: list[dict] = []
    for a in release.get("assets", []):
        kind = classify(a.get("name", ""))
        if not kind:
            # 不认识的产物不静默丢弃，记进 ignored 供人核对
            continue
        assets.append(
            {
                "kind": kind,
                "name": a["name"],
                "size": a["size"],
                "size_human": human_size(a["size"]),
                "url": a["browser_download_url"],
                "state": a.get("state", "unknown"),
            }
        )

    assets.sort(key=lambda x: x["kind"])
    ignored = [
        a["name"]
        for a in release.get("assets", [])
        if not classify(a.get("name", ""))
    ]

    return {
        "version": version,
        "tag": tag,
        "published_at": release.get("published_at") or "",
        "release_url": release.get("html_url") or f"https://github.com/{OWNER_REPO}/releases",
        "assets": assets,
        "ignored_assets": ignored,
        # 不写 generated_at：时间戳每次运行都不同，会让 releases.json 产生
        # 无意义的 diff（与 extract_sources.py 同一教训）。
    }


def _strip_timestamp(data: dict) -> dict:
    """比对时忽略 generated_at。

    保留这个函数是为了兼容**旧版文件**（历史 releases.json 里带过该字段）；
    新生成的文件已不再写入时间戳。
    """
    return {k: v for k, v in data.items() if k != "generated_at"}


def current_version() -> str:
    return json.loads(VERSION_FILE.read_text(encoding="utf-8"))["version"]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成官网下载页的产物清单")
    parser.add_argument("--check", action="store_true", help="只校验不写盘（门禁⑬）")
    args = parser.parse_args()

    want = current_version()

    try:
        release = fetch_release()
    except urllib.error.HTTPError as exc:
        print(
            f"[releases][FATAL] 取 Release 失败：HTTP {exc.code}。\n"
            "  若为 404：说明 v{0} 还没发布 —— 按既定流程**先发产品，最后更新官网**。".format(want),
            file=sys.stderr,
        )
        return 2
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[releases][FATAL] 网络不可用：{exc}", file=sys.stderr)
        return 2

    manifest = build_manifest(release)

    # ── 一致性判据（两个方向都拦）──
    errors: list[str] = []

    if manifest["version"] != want:
        errors.append(
            f"Release 最新 tag 是 v{manifest['version']}，但 version.json 是 {want}"
            "（产品可能还没发这个版本，或发版后没更新 version.json）"
        )

    # 四类产物必须齐全：少一类就说明 CI 没跑完 / 资产没上传完
    required = {"windows-setup", "windows-portable", "linux-rpm", "android-apk"}
    got = {a["kind"] for a in manifest["assets"]}
    missing = sorted(required - got)
    if missing:
        errors.append(f"Release 缺少产物：{', '.join(missing)}（等产品 CI 传完再更新官网）")

    bad_state = [a["name"] for a in manifest["assets"] if a.get("state") != "uploaded"]
    if bad_state:
        errors.append(f"资产未上传完成（state != uploaded）：{', '.join(bad_state)}")

    if args.check:
        if not RELEASES_FILE.is_file():
            errors.append(f"缺少 {RELEASES_FILE.relative_to(REPO_ROOT)}（先跑一次生成）")
        else:
            existing = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
            if _strip_timestamp(existing) != _strip_timestamp(manifest):
                # 给出可读的差异点，不要只说"不一致"
                old_map = {a["kind"]: a for a in existing.get("assets", [])}
                new_map = {a["kind"]: a for a in manifest.get("assets", [])}
                for kind in sorted(set(old_map) | set(new_map)):
                    o, n = old_map.get(kind), new_map.get(kind)
                    if o is None:
                        errors.append(f"{kind}：官网已登记但 Release 里没有（死链）")
                    elif n is None:
                        errors.append(f"{kind}：Release 里有但官网没登记")
                    elif o["name"] != n["name"] or o["size"] != n["size"]:
                        errors.append(
                            f"{kind}：官网 {o['name']} ({o['size']}B) "
                            f"≠ Release {n['name']} ({n['size']}B)"
                        )
                if not errors:
                    errors.append("内容与 Release 不一致（可能是发布信息变化），需重新生成")

        if errors:
            print("[releases][FAIL]", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print(
            f"[releases] 一致：v{manifest['version']}，"
            f"{len(manifest['assets'])} 个产物全部对得上 Release"
        )
        return 0

    if errors:
        # 生成模式也拦：宁可构建失败，也不要产出死链页面
        print("[releases][FAIL] 产物不完整，拒绝写入", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    RELEASES_FILE.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[releases] 已写入 {RELEASES_FILE.relative_to(REPO_ROOT)}（v{manifest['version']}）")
    for a in manifest["assets"]:
        print(f"  {a['kind']:18} {a['size_human']:>9}  {a['name']}")
    if manifest["ignored_assets"]:
        print(f"  [提示] 未归类（未展示在下载页）：{', '.join(manifest['ignored_assets'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

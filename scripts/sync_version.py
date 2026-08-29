"""
JFLove 版本号分发器（单一来源 version.json）

读取仓库根 version.json 的 version 字段，同步到全部 7 处版本号位置：
  - server:   src/main.py(FastAPI version) + Dockerfile(LABEL version)
  - desktop:  src/config/settings.py(APP_VERSION)
  - web:      package.json(version) + src/config/constants.ts(APP_VERSION)
  - app:      pubspec.yaml(version: X.Y.Z+code，code 由版本号派生)
               + lib/pages/settings/settings_page.dart(「关于」页显示，带 v 前缀)

移动端 versionCode 派生规则：major*1000000 + minor*1000 + patch
  - 1.4.2  -> 1004002
  - 1.4.10 -> 1004010
  - 1.10.0 -> 1010000

用法：
    python scripts/sync_version.py                 # 同步全部 7 处
    python scripts/sync_version.py --scope server  # 只同步指定模块
    python scripts/sync_version.py --dry-run       # 只预览改动，不写盘
    python scripts/sync_version.py --check         # 校验一致性，不一致返回非 0（供 CI / build.py）

本脚本同时作为可 import 模块，供各模块 build.py 复用校验逻辑。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Windows 默认 stdout/stderr 编码为 cp1252，print 中文会 UnicodeEncodeError（GitHub Actions
# 的 windows-latest runner 上必现）。统一 reconfigure 到 UTF-8，保证本地 Windows 与 CI 行为一致。
# 本模块被各 build.py import，因此这里的 reconfigure 对进程全局生效。
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

VERSION_FILE = ROOT / "version.json"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

SCOPES = ("server", "desktop", "web", "app")


def load_version() -> str:
    """读 version.json 返回版本号（如 '1.4.2'）"""
    if not VERSION_FILE.exists():
        sys.exit(f"[sync][FATAL] 缺少版本源文件：{VERSION_FILE}")
    data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    version = data.get("version", "")
    if not VERSION_RE.match(version):
        sys.exit(f"[sync][FATAL] version.json 的 version 非法：{version!r}（要求形如 x.y.z）")
    return version


def version_code(version: str) -> int:
    """由语义版本号派生 Android versionCode（major*1e6 + minor*1e3 + patch）"""
    major, minor, patch = map(int, version.split("."))
    return major * 1_000_000 + minor * 1_000 + patch


# ── 提取器：读某位置当前「版本名」，找不到返回 "" ────────────────────
def _extract_re(path: Path, pattern: str) -> str:
    m = re.search(pattern, path.read_text(encoding="utf-8"))
    return m.group(1) if m else ""


def _extract_pkg(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8")).get("version", "")


# ── 替换器：返回写回的新文本 ────────────────────────────────────────
def _replace_re(path: Path, pattern: str, repl: str) -> str:
    return re.sub(pattern, repl, path.read_text(encoding="utf-8"), count=1)


def _replace_pkg(path: Path, version: str) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _replace_pubspec(path: Path, version: str) -> str:
    code = version_code(version)
    text = path.read_text(encoding="utf-8")
    return re.sub(
        r"(version:\s*)\d+\.\d+\.\d+\+\d+",
        rf"\g<1>{version}+{code}",
        text,
        count=1,
    )


# ── 位置表：(scope, 相对路径, 提取器, 替换器, 是否强制重写) ────────
# pubspec 强制重写：versionCode 由版本号派生，需始终重算
POSITIONS = [
    ("server", "jflove-server/src/main.py",
     lambda p: _extract_re(p, r'version="([^"]+)"'),
     lambda p, v: _replace_re(p, r'version="[^"]+"', f'version="{v}"'),
     False),
    ("server", "jflove-server/Dockerfile",
     lambda p: _extract_re(p, r'LABEL version="([^"]+)"'),
     lambda p, v: _replace_re(p, r'LABEL version="[^"]+"', f'LABEL version="{v}"'),
     False),
    ("desktop", "jflove-desktop/src/config/settings.py",
     lambda p: _extract_re(p, r'APP_VERSION = "([^"]+)"'),
     lambda p, v: _replace_re(p, r'APP_VERSION = "[^"]+"', f'APP_VERSION = "{v}"'),
     False),
    ("web", "jflove-web/package.json",
     _extract_pkg,
     _replace_pkg,
     False),
    ("web", "jflove-web/src/config/constants.ts",
     lambda p: _extract_re(p, r"APP_VERSION\s*=\s*'([^']+)'"),
     lambda p, v: _replace_re(p, r"APP_VERSION\s*=\s*'[^']+'", f"APP_VERSION = '{v}'"),
     False),
    ("app", "jflove-app/pubspec.yaml",
     lambda p: _extract_re(p, r"version:\s*(\d+\.\d+\.\d+)\+\d+"),
     _replace_pubspec,
     True),
    ("app", "jflove-app/lib/pages/settings/settings_page.dart",
     lambda p: _extract_re(p, r"_InfoRow\(label: '版本', value: 'v([0-9.]+)'"),
     lambda p, v: _replace_re(p, r"(value: 'v)[0-9.]+", rf"\g<1>{v}"),
     False),
]


def _positions(scope: str | None = None):
    for sc, rel, extractor, replacer, force in POSITIONS:
        if scope is None or sc == scope:
            yield sc, ROOT / rel, extractor, replacer, force


def check_consistency(scope: str | None = None) -> list[str]:
    """校验指定 scope（默认全部）版本名是否与 version.json 一致，返回不一致项描述列表"""
    version = load_version()
    issues = []
    for _sc, path, extractor, _replacer, _force in _positions(scope):
        current = extractor(path)
        if current != version:
            issues.append(f"{path.relative_to(ROOT)} = {current!r}，期望 {version!r}")
    return issues


def sync_all(scope: str | None = None, dry_run: bool = False) -> list[str]:
    """同步指定 scope（默认全部）到 version.json 版本，返回被改动的文件相对路径列表"""
    version = load_version()
    changed = []
    for _sc, path, extractor, replacer, force in _positions(scope):
        current = extractor(path)
        if not force and current == version:
            continue
        new_text = replacer(path, version)
        if not dry_run:
            path.write_text(new_text, encoding="utf-8")
        changed.append(str(path.relative_to(ROOT)))
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="同步版本号到各模块（单一来源 version.json）")
    parser.add_argument("--scope", choices=list(SCOPES), default=None,
                        help="只处理指定模块（默认全部）")
    parser.add_argument("--dry-run", action="store_true", help="只预览改动，不写盘")
    parser.add_argument("--check", action="store_true", help="只校验一致性，不一致返回非 0")
    args = parser.parse_args()

    version = load_version()
    if args.check:
        issues = check_consistency(args.scope)
        if issues:
            print(f"[sync] 版本不一致（期望 {version}）：", file=sys.stderr)
            for i in issues:
                print(f"  - {i}", file=sys.stderr)
            sys.exit(1)
        print(f"[sync] 版本一致性校验通过：全部 = v{version}")
        return

    changed = sync_all(args.scope, dry_run=args.dry_run)
    tag = "[dry-run] " if args.dry_run else ""
    if changed:
        print(f"[sync] {tag}已同步 v{version}，改动 {len(changed)} 处：")
        for rel in changed:
            print(f"  - {rel}")
        if args.scope in (None, "app"):
            print(f"[sync] 移动端 versionCode 派生为 {version_code(version)}")
    else:
        print(f"[sync] 全部位置已是 v{version}，无需改动")


if __name__ == "__main__":
    main()

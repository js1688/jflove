#!/usr/bin/env python3
"""验证证据报告门禁：测试/发布报告必须能证明「实机跑过」，不能只写 pytest 通过。

报告格式（写在 `文档记录/测试报告/<版本号>.md`）：

    ## 验证证据

    | 端 | 命令 | 结果 | 证据 |
    | --- | --- | --- | --- |
    | 服务端 | `server.sh smoke` | 退出码 0；/health 200 | /tmp/jf-server.log |
    | Web | `web_shot.mjs --click-text ...` | key-exchange 200 | /tmp/jf-web.png |
    | Linux 桌面 | `desktop_shot.py` | 1180x760 抓帧 | /tmp/jf-desktop.png |
    | Android | `android_smoke.sh --stop` | 安装 Success；登录 200 | /tmp/jf-android.png |
    | Windows | `scripts/verify/windows_wine.sh --skip-download` | 登录页渲染 | /tmp/jf-wine.png |

跳过某端时：结果写「跳过」，证据写「原因：<真实探测输出>」。

用法：
    python scripts/check_verification_report.py --version 1.7.0 \
        --require server,web,desktop,android,windows
    python scripts/check_verification_report.py --report 文档记录/测试报告/v1.7.0.md
    python scripts/check_verification_report.py --self-test

退出码：0 = 通过；1 = 证据缺失/格式错误；2 = 参数或文件错误。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")

ALIASES = {
    "server": ("服务端", "server", "后端"),
    "web": ("web", "web端", "浏览器"),
    "desktop": ("linux桌面", "linux 桌面", "desktop", "桌面端"),
    "android": ("android", "安卓", "移动端"),
    "windows": ("windows", "wine"),
}
HEADING = re.compile(r"^##\s+验证证据.*$")
NEXT_HEADING = re.compile(r"^##\s+")
SEPARATOR = re.compile(r"^:?-{3,}:?$")


def _section_lines(text: str) -> list[str] | None:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if HEADING.match(line.strip()):
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        if NEXT_HEADING.match(lines[j].strip()):
            end = j
            break
    return lines[start:end]


def _parse_rows(section: list[str]) -> tuple[list[list[str]], list[str]]:
    errors: list[str] = []
    rows: list[list[str]] = []
    saw_header = False
    for raw in section:
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 4:
            errors.append(f"表格列数不是 4：{line}")
            continue
        if all(SEPARATOR.match(c) for c in cells):
            continue
        if not saw_header:
            if "端" not in cells[0] or "命令" not in cells[1]:
                errors.append(f"表头应为「端 | 命令 | 结果 | 证据」，实际：{line}")
                continue
            saw_header = True
            continue
        rows.append(cells)
    if not saw_header:
        errors.append("缺少表格表头（端 | 命令 | 结果 | 证据）")
    if not rows:
        errors.append("验证证据表没有数据行")
    return rows, errors


def validate_text(text: str, required: list[str]) -> list[str]:
    section = _section_lines(text)
    if section is None:
        return ["缺少 `## 验证证据` 章节"]
    rows, errors = _parse_rows(section)

    executed = 0
    for cells in rows:
        end, cmd, result, evidence = cells
        if not cmd:
            errors.append(f"{end}：命令为空")
        if not result:
            errors.append(f"{end}：结果为空")
        if not evidence:
            errors.append(f"{end}：证据为空")
        if "跳过" in result:
            if "原因" not in evidence:
                errors.append(f"{end}：跳过必须写「原因：<真实探测输出>」")
            if "通过" in result or "✅" in result:
                errors.append(f"{end}：跳过行不得写成通过")
        else:
            executed += 1

    if executed == 0:
        errors.append("没有任何「实际执行」的端（全是跳过或空表）")

    for key in required:
        aliases = ALIASES.get(key)
        if aliases is None:
            errors.append(f"未知的端：{key}（可选：{', '.join(ALIASES)}）")
            continue
        matched = any(
            any(alias in cells[0].lower() for alias in aliases)
            for cells in rows
        )
        if not matched:
            errors.append(f"缺少端「{key}」的证据行")
    return errors


def _resolve_report(args) -> Path:
    base = Path("文档记录/测试报告")
    if args.report:
        return Path(args.report)
    if args.latest:
        candidates = sorted(base.glob("*.md"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise FileNotFoundError(f"{base} 下没有报告")
        return candidates[-1]
    if args.version:
        v = args.version[1:] if args.version.startswith("v") else args.version
        for name in (f"{v}.md", f"v{v}.md"):
            path = base / name
            if path.exists():
                return path
        raise FileNotFoundError(f"找不到 {base}/v{v}.md 或 {v}.md")
    raise ValueError("必须给 --report / --version / --latest 之一")


def _self_test() -> int:
    good = """## 验证证据

| 端 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| 服务端 | `scripts/verify/server.sh smoke` | 退出码 0；/health 200 | /tmp/jf-server.log |
| Web | `node scripts/verify/web_shot.mjs` | key-exchange 200 | /tmp/jf-web.png |
| Linux 桌面 | `desktop_shot.py` | 1180x760 抓帧 | /tmp/jf-desktop.png |
| Android | `scripts/verify/android_smoke.sh --stop` | 安装 Success；登录 200 | /tmp/jf-android.png |
| Windows | `scripts/verify/windows_wine.sh --skip-download` | 登录页渲染 | /tmp/jf-wine.png |
"""
    bad_missing = "## 测试范围\n\n只跑了 pytest。\n"
    bad_skip = """## 验证证据

| 端 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| Android | `adb devices` | 跳过 | 无 |
"""
    bad_no_end = good.replace("| Windows |", "| 其他 |")
    cases = [
        (good, list(ALIASES), 0, "完整五端"),
        (bad_missing, [], 1, "缺章节"),
        (bad_skip, ["android"], 1, "跳过无原因"),
        (bad_no_end, ["windows"], 1, "缺 Windows 行"),
    ]
    for text, required, expected_code, label in cases:
        errors = validate_text(text, required)
        code = 0 if not errors else 1
        ok = code == expected_code
        print(f"[self-test] {label}: {'OK' if ok else 'FAIL'}（{len(errors)} 个错误）")
        if not ok:
            print("\n".join(f"  - {e}" for e in errors))
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="验证证据报告门禁")
    parser.add_argument("--report", help="报告路径")
    parser.add_argument("--version", help="版本号（找 文档记录/测试报告/v<版本>.md）")
    parser.add_argument("--latest", action="store_true", help="取最新的测试报告")
    parser.add_argument(
        "--require",
        default="",
        help="必须出现的端，逗号分隔：server,web,desktop,android,windows",
    )
    parser.add_argument("--self-test", action="store_true", help="跑门禁自身的反向验证")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    required = [x.strip().lower() for x in args.require.split(",") if x.strip()]
    try:
        path = _resolve_report(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[verify-report] {exc}", file=sys.stderr)
        return 2
    if not path.exists():
        print(f"[verify-report] 报告不存在：{path}", file=sys.stderr)
        return 2

    errors = validate_text(path.read_text(encoding="utf-8"), required)
    if errors:
        print(f"[verify-report] FAIL: {path}")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(f"[verify-report] OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

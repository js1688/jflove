#!/usr/bin/env python3
"""技能校验器：检查 `.agents/skills/` 下的 DSH 技能是否会被技能目录正常收录。

背景（实测坑，见 AGENTS.md §6.2 第 2 条 / LESSONS.md L45）：
DSH 对 frontmatter 非法 YAML 的技能是**静默丢弃**——技能目录里查不到、
`skill("<name>")` 只回 `unknown or no longer available`，没有任何告警。
最常见的非法写法是 `description` 里含 `: `（冒号 + 空格，如 `Use when: …`）却没加引号。

校验项：
1. `.agents/skills/<名字>/SKILL.md` 存在，且目录名 = frontmatter 的 `name`（kebab-case）；
2. frontmatter 能被 YAML 解析且是映射，含非空 `description`；
3. 正文非空，且不含已删除的“指路代理”生成器残留（`sync_agent_skill_proxies`）；
4. AGENTS.md §6 要求的角色技能 / `lessons` / `subagent-delegation` 一个都不缺。

退出码：0 = 全部通过；1 = 有问题（可直接当 CI / 发布门禁）。

用法：
    jflove-server\\venv-win\\Scripts\\python.exe scripts\\check_skills.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 控制台编码：Windows 默认 GBK，中文描述会乱码 / 报错
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")

try:  # PyYAML 缺失时退化为正则形状检查（只警告，不误判为通过）
    import yaml
except ImportError:  # pragma: no cover - 取决于运行环境
    yaml = None

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"

# AGENTS.md §6 角色表 + 跨角色经验库 + 委派契约
REQUIRED_NAMES = (
    "product",
    "designer",
    "backend",
    "cross-platform-desktop",
    "web-frontend",
    "cross-platform-mobile",
    "code-review",
    "testing",
    "pmo",
    "devops",
    "lessons",
    "subagent-delegation",
)

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FORBIDDEN_BODY_MARKERS = ("sync_agent_skill_proxies",)


def _display_path(path: Path) -> str:
    """优先显示仓库内相对路径；仓库外的路径原样显示（校验器被单测/探针复用时用得上）。"""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def collect_skills() -> list[tuple[str, Path]]:
    """返回 [(技能名, SKILL.md 路径)]，只认顶层 `<name>/SKILL.md`（DSH 不递归）。"""
    found: list[tuple[str, Path]] = []
    if not SKILLS_ROOT.is_dir():
        return found
    for entry in sorted(SKILLS_ROOT.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            found.append((entry.name, entry / "SKILL.md"))
    return found


def check_one(skill_name: str, path: Path) -> tuple[list[str], list[str], str, int]:
    """校验单个技能，返回 (错误, 警告, 描述, 描述长度)。"""
    errors: list[str] = []
    warnings: list[str] = []
    text = path.read_text(encoding="utf-8")
    rel = _display_path(path)

    match = FRONTMATTER_RE.match(text)
    if match is None:
        errors.append(f"{rel}: 缺少 `---` frontmatter 块（DSH 会直接丢弃该技能）")
        return errors, warnings, "", 0

    raw_frontmatter = match.group(1)
    body = text[match.end():]
    name: object = None
    description: object = None

    if yaml is not None:
        try:
            data = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError as exc:
            first_line = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
            errors.append(
                f"{rel}: frontmatter 不是合法 YAML（{first_line}）——"
                "含 `: ` 的值必须加双引号，否则该技能被静默丢弃"
            )
            return errors, warnings, "", 0
        if not isinstance(data, dict):
            errors.append(f"{rel}: frontmatter 必须是映射（YAML mapping）")
            return errors, warnings, "", 0
        name = data.get("name")
        description = data.get("description")
    else:
        # PyYAML 缺失的降级提示由 main() 统一打印一次，这里不再逐技能重复
        name_match = re.search(r"^name:[ \t]*(.+?)[ \t]*$", raw_frontmatter, re.MULTILINE)
        desc_match = re.search(r"^description:[ \t]*(.+?)[ \t]*$", raw_frontmatter, re.MULTILINE)
        if name_match:
            name = name_match.group(1).strip("\"'")
        if desc_match:
            description = desc_match.group(1).strip("\"'")
        quoted = desc_match.group(1).lstrip().startswith(("\"", "'")) if desc_match else True
        if desc_match and ": " in description and not quoted:
            errors.append(
                f"{rel}: description 含 `: ` 但没加引号（合法 YAML 会失败 → 技能被静默丢弃）"
            )

    if not isinstance(name, str) or not name:
        errors.append(f"{rel}: 缺少非空 `name`")
    elif not KEBAB_RE.match(name):
        errors.append(f"{rel}: `name: {name}` 不是 kebab-case")
    elif name != skill_name:
        errors.append(f"{rel}: `name: {name}` 与目录名 `{skill_name}` 不一致（DSH 按目录名收录）")

    if not isinstance(description, str) or not description.strip():
        errors.append(f"{rel}: 缺少非空 `description`（技能目录里会显示为空）")
        description = ""

    if not body.strip():
        errors.append(f"{rel}: frontmatter 之后正文为空（加载后拿不到任何规则）")

    for marker in FORBIDDEN_BODY_MARKERS:
        if marker in body:
            errors.append(f"{rel}: 正文仍含已删除的生成器残留 `{marker}`（代理机制已废弃，需改为真身）")

    return errors, warnings, description.strip(), len(description.strip())


def main() -> int:
    if yaml is None:
        print("[WARN] 未检测到 PyYAML：只能做形状检查，无法证明 frontmatter 是合法 YAML。")
        print("[WARN] 建议用带 PyYAML 的解释器运行（jflove-server\\venv-win\\Scripts\\python.exe）。")
    print(f"技能扫描根：{SKILLS_ROOT.relative_to(REPO_ROOT).as_posix()}/")

    skills = collect_skills()
    all_errors: list[str] = []
    all_warnings: list[str] = []
    print(f"\n{'技能名':<26} {'描述字数':>6}  状态")
    print("-" * 52)
    for skill_name, path in skills:
        errors, warnings, _description, desc_len = check_one(skill_name, path)
        all_errors.extend(errors)
        all_warnings.extend(warnings)
        status = "OK" if not errors else f"错误 × {len(errors)}"
        print(f"{skill_name:<26} {desc_len:>6}  {status}")

    present = {name for name, _path in skills}
    missing = [name for name in REQUIRED_NAMES if name not in present]
    for name in missing:
        all_errors.append(f"缺少必需技能：.agents/skills/{name}/SKILL.md")

    for warning in all_warnings:
        print(f"\n{warning}")
    if missing:
        print(f"\n缺失必需技能（{len(missing)} 个）：{', '.join(missing)}")
    for error in all_errors:
        print(f"\n错误：{error}")

    print(
        f"\n共 {len(skills)} 个技能，必需 {len(REQUIRED_NAMES)} 个；"
        f"错误 {len(all_errors)} 项，缺失 {len(missing)} 项。"
    )
    if all_errors:
        print("结论：不通过（退出码 1）—— 上述技能在 DSH 技能目录里可能完全不可见。")
        return 1
    print("结论：通过（退出码 0）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

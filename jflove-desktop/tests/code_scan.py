"""
Phase 6 复用的**代码扫描**小工具（不是测试模块，不会被 pytest 收集）

用途：AC-8（全仓 0 命中全局热键注册 API）与 AC-42 / AC-47（编辑链路 0 命中网络 /
鉴权关键字）都要「只扫代码，不扫注释与文档」，规则集中在这里，避免两处各写一份。

三条设计取舍（**判据的前提，改动前先想清楚**）：
  1. 注释与三引号长串**必须**抹掉。本仓大量中文说明会原样引用关键字与文件名
     （`scratch_service.py` 顶部注释里的 `auth_service.py`、设计/审查文档里的
     `RegisterHotKey`），拿文档判代码只会造出假阳性。
  2. 普通**单行字符串字面量保留**。字符串里写死的 `http://` 远端地址是真命中，要能抓到。
  3. 抹除时**保留行数**（非换行字符替换成空格），行号才能拿来定位。
"""
from __future__ import annotations

import io
import os
import re
import tokenize
from pathlib import Path
from typing import Collection, Iterator, Sequence

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DESKTOP_ROOT.parent

#: 扫描范围内**只按这些后缀**取文件（文档类后缀一律不在内）
CODE_SUFFIXES: frozenset[str] = frozenset({
    ".py",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".dart",
    ".sh", ".bat", ".ps1",
    ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".iss", ".spec", ".sql", ".html", ".css",
})

#: 全仓扫描默认跳过的目录（依赖、虚拟环境、产物、**文档与计划**、第三方离线包）
DEFAULT_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".github_cache", ".idea", ".vscode",
    "node_modules", "venv-win", "venv-linux", "site-packages",
    "dist", "build", "__pycache__", ".pytest_cache", ".vite",
    "out", "vendor", "文档记录", "plans",
})

#: 读不下来的文件（编码异常等）会记在这里，供守卫用例断言「扫描面没有静默漏读」
READ_ERRORS: list[str] = []

_C_STYLE_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".dart", ".css"})
_HASH_STYLE_SUFFIXES = frozenset({
    ".sh", ".bat", ".ps1", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sql", ".html",
})
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_PY_PREFIXES = "rbuRBUfF"


# ── 注释剔除 ────────────────────────────────────────────────────────

def _blank(lines: list[str], start: tuple[int, int], end: tuple[int, int]) -> None:
    """把 `[start, end)` 这段源码原地抹成空格（保留行数，行号不错位）"""
    start_row, start_col = start
    end_row, end_col = end
    if start_row == end_row:
        line = lines[start_row - 1]
        lines[start_row - 1] = line[:start_col] + " " * (end_col - start_col) + line[end_col:]
        return
    lines[start_row - 1] = lines[start_row - 1][:start_col]
    for row in range(start_row + 1, end_row):
        lines[row - 1] = ""
    lines[end_row - 1] = " " * end_col + lines[end_row - 1][end_col:]


def _is_triple(text: str) -> bool:
    return text.lstrip(_PY_PREFIXES).startswith(('"""', "'''"))


def _python_code(text: str) -> str:
    """Python：抹掉注释与三引号长串（docstring / 多行说明都落在这里）"""
    lines = text.splitlines()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            _blank(lines, token.start, token.end)
        elif token.type in (tokenize.STRING, tokenize.FSTRING_MIDDLE):
            if _is_triple(token.string) or token.start[0] != token.end[0]:
                _blank(lines, token.start, token.end)
    return "\n".join(lines)


def _c_style_code(text: str) -> str:
    """C 系（TS/JS/Dart/CSS）：抹掉块注释（保行数）与整行 `//` 注释"""
    text = _BLOCK_COMMENT_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in text.split("\n")
    )


def _hash_style_code(text: str) -> str:
    """`#` / `;` 注释系（shell / PowerShell / YAML / Inno Setup）"""
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        out.append("" if stripped.startswith(("#", ";")) else line)
    return "\n".join(out)


def _strip(suffix: str, text: str) -> str:
    if suffix == ".py":
        return _python_code(text)
    if suffix in _C_STYLE_SUFFIXES:
        return _c_style_code(text)
    if suffix in _HASH_STYLE_SUFFIXES:
        return _hash_style_code(text)
    return text


# ── 遍历与扫描 ──────────────────────────────────────────────────────

def iter_repo_code_files(skip_dirs: Collection[str] = DEFAULT_SKIP_DIRS) -> Iterator[Path]:
    """全仓遍历代码文件（用 `os.walk` 剪枝，避免 `rglob` 先走完 `node_modules`）"""
    skip = set(skip_dirs)
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = sorted(name for name in dirs if name not in skip)
        for name in files:
            if Path(name).suffix.lower() in CODE_SUFFIXES:
                yield Path(root) / name


def code_lines(path: Path) -> Iterator[tuple[int, str, str]]:
    """产出 `(行号, 去注释后的行, 原始行)`；注释行与空行不产出"""
    try:
        raw = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as error:  # 记下来，别静默漏读
        READ_ERRORS.append(f"{path}: {error}")
        return
    code = _strip(path.suffix.lower(), raw)
    for number, (stripped, original) in enumerate(
        zip(code.splitlines(), raw.splitlines()), start=1
    ):
        if stripped.strip():
            yield number, stripped, original


def scan(paths: Sequence[Path], tokens: Sequence[str], base: Path = REPO_ROOT) -> list[str]:
    """扫一批文件，返回 `相对路径:行: 命中 ⇒ 原行` 形式的报告（空列表 = 无命中）"""
    hits: list[str] = []
    for path in paths:
        for number, stripped, original in code_lines(path):
            for token in tokens:
                if token in stripped:
                    try:
                        shown = path.relative_to(base)
                    except ValueError:
                        shown = path
                    hits.append(f"{shown}:{number}: {token} ⇒ {original.strip()}")
    return hits

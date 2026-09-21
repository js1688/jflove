"""
Windows 安装包（Inno Setup）语言文件探测 —— 回归测试。

背景（v1.5.0 CI 故障，本次修复）：
    旧实现用 `iscc.parent / "Languages" / "ChineseSimplified.isl"` 推断语言文件位置。
    CI 上 `shutil.which("iscc")` 命中的是 Chocolatey 的 **shim**
    （`C:\\ProgramData\\Chocolatey\\bin\\iscc.EXE`），于是探测结果指向了
    `C:\\ProgramData\\Chocolatey\\bin\\Languages\\...`；而 `jflove.iss` 当时写的是
    `MessagesFile: "compiler:Languages\\ChineseSimplified.isl"`，**`compiler:` 由 ISCC
    解析为它自身安装目录**（与 shim/PATH 无关）→ 真实编译器目录里没有该文件 →
    [Languages] 段直接报错、编译中止 → 安装包缺失 → Windows job 失败。

本测试固化三条不变式：
    1. 探测结果**必须真实存在**（不允许「探测说在、iscc 说不在」的第三种状态）；
    2. 仓库内置副本优先（CI / 离线 / 本机一致命中）；
    3. .iss 不再依赖 `compiler:` 猜目录，改为「传入绝对路径 + FileExists 兜底」。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

#: jflove-desktop 根目录（tests/ 的上一级）
_DESKTOP_ROOT = Path(__file__).resolve().parent.parent


def _load_build_module():
    """把仓库根的 build.py 作为模块加载（它不是包，无法直接 import）。"""
    spec = importlib.util.spec_from_file_location(
        "jflove_desktop_build", _DESKTOP_ROOT / "build.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _load_build_module()

_ISL_NAME = "ChineseSimplified.isl"


# ── 1. 仓库内置副本本身必须可用 ────────────────────────────────────

def test_bundled_isl_exists_in_repo():
    """内置语言文件必须在仓库里（否则 CI 上没有可靠来源）。"""
    assert build.BUNDLED_CHINESE_ISL.is_file(), (
        f"缺少内置简体中文语言文件：{build.BUNDLED_CHINESE_ISL}\n"
        "  它不应被 gitignore，也不应被构建脚本清理；详见同目录 README.md"
    )


def test_bundled_isl_is_wellformed():
    """内置文件必须是 Inno Setup 6 的 .isl：UTF-8、含 [LangOptions]、语言名正确。"""
    raw = build.BUNDLED_CHINESE_ISL.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "应为 UTF-8 无 BOM（Inno Setup 直接读取）"
    text = raw.decode("utf-8")  # 解码失败即视为文件损坏
    assert "[LangOptions]" in text
    assert "LanguageName=" in text
    assert "Inno Setup version 6." in text, "文件头应声明的目标 Inno Setup 版本"


# ── 2. 探测优先级 ────────────────────────────────────────────────

def test_prefers_bundled_copy_over_compiler_dir(tmp_path, monkeypatch):
    """既有内置副本、又有构建机副本时，必须选内置（可复现、可离线）。"""
    bundled = tmp_path / "bundled" / _ISL_NAME
    bundled.parent.mkdir(parents=True)
    bundled.write_text("[LangOptions]\nLanguageName=简体中文\n", encoding="utf-8")

    compiler_isl = tmp_path / "compiler" / "Languages" / _ISL_NAME
    compiler_isl.parent.mkdir(parents=True)
    compiler_isl.write_text("[LangOptions]\nLanguageName=compiler copy\n", encoding="utf-8")

    monkeypatch.delenv("JFLOVE_CHINESE_ISL", raising=False)
    monkeypatch.setattr(build, "BUNDLED_CHINESE_ISL", bundled)
    monkeypatch.setattr(build, "_compiler_dirs", lambda iscc: [compiler_isl.parent.parent])

    assert build.find_chinese_isl(tmp_path / "iscc.exe") == bundled


def test_env_override_wins(tmp_path, monkeypatch):
    """环境变量显式指定时优先于内置副本（特殊环境逃生口）。"""
    override = tmp_path / "custom.isl"
    override.write_text("[LangOptions]\n", encoding="utf-8")
    monkeypatch.setenv("JFLOVE_CHINESE_ISL", str(override))
    assert build.find_chinese_isl(tmp_path / "iscc.exe") == override


def test_invalid_env_override_is_ignored(tmp_path, monkeypatch, capsys):
    """环境变量指向不存在的文件 → 忽略并告警，继续走正常查找（不得让构建崩）。"""
    monkeypatch.setenv("JFLOVE_CHINESE_ISL", str(tmp_path / "nope.isl"))
    monkeypatch.setattr(build, "BUNDLED_CHINESE_ISL", tmp_path / "missing" / _ISL_NAME)
    monkeypatch.setattr(build, "_compiler_dirs", lambda iscc: [])

    assert build.find_chinese_isl(tmp_path / "iscc.exe") is None
    assert "JFLOVE_CHINESE_ISL" in capsys.readouterr().out


def test_found_isl_from_compiler_dir_when_no_bundled(tmp_path, monkeypatch):
    """没有内置副本时退回构建机真实编译器目录。"""
    compiler_isl = tmp_path / "Inno Setup 6" / "Languages" / _ISL_NAME
    compiler_isl.parent.mkdir(parents=True)
    compiler_isl.write_text("[LangOptions]\n", encoding="utf-8")

    monkeypatch.delenv("JFLOVE_CHINESE_ISL", raising=False)
    monkeypatch.setattr(build, "BUNDLED_CHINESE_ISL", tmp_path / "missing" / _ISL_NAME)
    monkeypatch.setattr(build, "_compiler_dirs", lambda iscc: [compiler_isl.parent.parent])

    assert build.find_chinese_isl(tmp_path / "iscc.exe") == compiler_isl


# ── 3. 本次 BUG 的核心反例：shim 目录绝不产生「假命中」 ─────────────

def test_shim_dir_without_file_returns_none(tmp_path, monkeypatch):
    """
    复现 v1.5.0 CI 场景：iscc 是包管理器 shim（其目录下有 Languages\\ 结构但**没有**
    该文件），真实编译器目录也不含该文件 ⇒ 必须返回 None（降级英文）。

    旧实现会返回 `shim/Languages/ChineseSimplified.isl` 这个**并不存在**的路径，
    进而传 HasChineseIsl=1 让 iscc 在 [Languages] 段中止 —— 本用例就是拦这个。
    """
    shim_dir = tmp_path / "Chocolatey" / "bin"
    (shim_dir / "Languages").mkdir(parents=True)  # 目录存在，文件不存在
    monkeypatch.delenv("JFLOVE_CHINESE_ISL", raising=False)
    monkeypatch.setattr(build, "BUNDLED_CHINESE_ISL", tmp_path / "missing" / _ISL_NAME)
    # 候选目录只给 shim 目录（真实编译器目录在本机可能存在，故屏蔽以保持确定性）
    monkeypatch.setattr(build, "_compiler_dirs", lambda iscc: [shim_dir])

    result = build.find_chinese_isl(shim_dir / "iscc.EXE")

    assert result is None, f"shim 目录下没有该文件时不得返回路径，实际返回：{result}"


def test_returned_path_always_exists(tmp_path, monkeypatch):
    """不变式：返回值要么是 None，要么是真实存在的文件（任何情况下都不许说谎）。"""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(parents=True)
    monkeypatch.delenv("JFLOVE_CHINESE_ISL", raising=False)
    monkeypatch.setattr(build, "BUNDLED_CHINESE_ISL", tmp_path / "missing" / _ISL_NAME)
    monkeypatch.setattr(build, "_compiler_dirs", lambda iscc: [shim_dir])

    result = build.find_chinese_isl(shim_dir / "iscc.EXE")
    assert result is None or result.is_file()


# ── 4. .iss 结构护栏：不许再回到 compiler: 猜目录 ────────────────────

def _active_language_lines(iss_text: str) -> list[str]:
    """
    取出 .iss 中 `[Languages]` 段的**有效指令行**（去注释、去空行）。

    为什么必须去注释：脚本里有意用注释记录了"不要再用 compiler:Languages\\..."这一教训，
    朴素的全文本 `in` 判断会被注释里的反例文本误伤（实测踩过）。
    """
    active: list[str] = []
    in_languages = False
    for raw in iss_text.splitlines():
        line = raw.strip()
        if line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_languages = line.lower() == "[languages]"
            continue
        if in_languages and line:
            active.append(line)
    return active


def test_iss_uses_passed_absolute_path_not_compiler_prefix():
    """`MessagesFile` 必须引用传入的 {#IslPath}，且不得用 compiler: 定位中文文件。"""
    text = build.INSTALLER_SCRIPT.read_text(encoding="utf-8")
    active = _active_language_lines(text)
    joined = "\n".join(active)

    assert 'MessagesFile: "{#IslPath}"' in joined
    assert "compiler:Languages" not in joined, (
        "不得再用 compiler:Languages\\ChineseSimplified.isl —— compiler: 由 ISCC 解析为"
        "自身安装目录，与探测目录不一致时会在 [Languages] 段编译中止"
    )
    # 英文兜底仍走编译器自带的 Default.isl（这个文件必然存在）
    assert 'MessagesFile: "compiler:Default.isl"' in joined


def test_iss_guards_with_fileexists_and_manual_default():
    """手工编译路径也要成立：默认指向仓库内置副本 + FileExists 兜底。"""
    text = build.INSTALLER_SCRIPT.read_text(encoding="utf-8")
    assert "#ifndef IslPath" in text
    assert 'AddBackslash(SourcePath) + "Languages\\ChineseSimplified.isl"' in text
    assert "#if FileExists(IslPath)" in text

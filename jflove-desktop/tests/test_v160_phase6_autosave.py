"""
v1.6.0 Phase 6 · 自动保存与草稿暂存（AC-40 / AC-54）

职责划分（设计 §6.2.2 / §6.3.2，判据必须按这个口径，否则会误判）：
  * **防抖与「只在脏时调用」在渲染层**（`ui/src/stores/scratch-store.ts`，唯一常量
    `AUTO_SAVE_DEBOUNCE_MS = 1500`）。AC-54 的「内容不变再等一个周期 ⇒ 草稿 mtime 不变」
    由 `ui/src/stores/__tests__/scratch-store-save.test.ts` 用假定时器断言 ——
    Python 侧**无法也不应**代管（`scratch_service.save_draft()` 每次都会重写内容与索引）。
  * **Python 侧只落盘、不重复防抖**。因此本模块只锁三件事：
    ① 唯一常量两侧同值（跨语言漂移会让 AC-40/AC-54 的计时判据失真）；
    ② `editor.draftSave` 的契约（无论窗格有没有路径，草稿只进草稿库）；
    ③ 草稿/保存路径里**没有** Python 侧定时器（防抖不得重复实现）。

安全口径：全程只碰 `tmp_path`，草稿用例把 `settings.SCRATCH_DIR` 指向 `tmp_path`。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from src.bridge import editor as editor_bridge
from src.config import settings

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
TS_SCRATCH_STORE = DESKTOP_ROOT / "ui" / "src" / "stores" / "scratch-store.ts"
NO_TIMER_FILES = (
    DESKTOP_ROOT / "src" / "services" / "scratch_service.py",
    DESKTOP_ROOT / "src" / "services" / "editor_service.py",
    DESKTOP_ROOT / "src" / "bridge" / "editor.py",
)
TIMER_TOKENS = ("QTimer", "startTimer", "singleShot")
FALLBACK_RE = re.compile(r"const FALLBACK_AUTO_SAVE_DEBOUNCE_MS = (\d+);")


# ── 工具 ────────────────────────────────────────────────────────────

def _entries(directory: Path) -> set[str]:
    return {item.name for item in directory.iterdir()}


def _pane(pane_id: str, path: str | None, dirty: bool = True) -> dict:
    """§7.1 #6 的 pane 结构（字段名逐字对齐设计表）"""
    return {
        "id": pane_id,
        "title": "sample.txt",
        "path": path,
        "language": "plain",
        "encoding": "utf-8",
        "lineEnd": "lf",
        "read_only": False,
        "dirty": dirty,
        "geometry": {"x": 10, "y": 10, "width": 400, "height": 300},
        "caret": 0,
        "scroll_top": 0,
        "font_size": 14,
        "show_line_numbers": True,
        "word_wrap": True,
    }


def _draft_handler(params: dict) -> dict:
    return editor_bridge.HANDLERS["editor"]["draftSave"](params)


# ── ① 唯一常量：两侧同值 ────────────────────────────────────────────

class TestDebounceConstant:
    """AC-40 / AC-54 的**取值前提**：1500 ms 是唯一常量，两侧不得漂移。"""

    def test_python_侧常量是_1500(self):
        assert settings.AUTO_SAVE_DEBOUNCE_MS == 1500

    def test_渲染层兜底值与_Python_侧常量同值(self):
        """`scratch-store.ts` 的兜底字面量必须与 `settings.py` 同值

        审查报告已核查过这是「有据重复」（TS 侧优先读 `VITE_AUTO_SAVE_DEBOUNCE_MS`，
        缺省用 1500 兜底）；本用例把这条"有据"钉成可执行判据 —— 任一侧改了数值、
        另一侧没跟 ⇒ 这里立刻失败，而不是等到 AC-40 的计时判据莫名其妙地失真。
        """
        assert TS_SCRATCH_STORE.exists(), f"渲染层 store 不存在：{TS_SCRATCH_STORE}"
        found = FALLBACK_RE.findall(TS_SCRATCH_STORE.read_text(encoding="utf-8"))
        assert len(found) == 1, f"应恰好一处兜底常量定义，实际 {len(found)} 处"
        assert int(found[0]) == settings.AUTO_SAVE_DEBOUNCE_MS


# ── ② draftSave 契约：草稿只进草稿库 ────────────────────────────────

class TestDraftSaveContract:
    """AC-40：草稿形态（`path == null`）只写草稿库；有路径的窗格暂存也不碰用户文件。"""

    def test_草稿形态暂存_用户文件系统零变化(self, tmp_path: Path, monkeypatch):
        scratch = tmp_path / "scratch"
        monkeypatch.setattr(settings, "SCRATCH_DIR", str(scratch))
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "keep.txt").write_text("keep\n", encoding="utf-8")
        before = _entries(user_dir)

        result = _draft_handler({"pane": _pane("d1", None), "content": "draft body\n"})

        assert result["ok"] is True
        assert isinstance(result["at"], float), "返回结构须含写入时间戳（渲染层据此判定）"
        assert _entries(user_dir) == before, "草稿形态不得在用户文件系统留下任何变化"
        assert (scratch / "pane-d1.txt").read_text(encoding="utf-8") == "draft body\n"

    def test_有路径窗格的暂存_仍只进草稿库(self, tmp_path: Path, monkeypatch):
        """有路径 ≠ 自动保存：`draftSave` 永远只写草稿库（写目标文件是 `writeText` 的职责）"""
        scratch = tmp_path / "scratch"
        monkeypatch.setattr(settings, "SCRATCH_DIR", str(scratch))
        target = tmp_path / "note.txt"
        target.write_text("on disk\n", encoding="utf-8")
        before_stat = os.stat(target)

        result = _draft_handler(
            {"pane": _pane("d2", str(target)), "content": "unsaved edit\n"}
        )

        assert result["ok"] is True
        assert target.read_text(encoding="utf-8") == "on disk\n", "暂存不得改动目标文件"
        assert os.stat(target).st_mtime == before_stat.st_mtime
        assert (scratch / "pane-d2.txt").read_text(encoding="utf-8") == "unsaved edit\n"


# ── ③ 自动保存必须有路径（服务层半边） ──────────────────────────────

class TestAutosaveNeedsPath:
    """AC-40：`自动保存只对已有路径生效` 的服务层半边 —— 没有路径就写不出去。"""

    def test_writeText_缺_path_报参数错误(self):
        with pytest.raises(ValueError):
            editor_bridge.HANDLERS["editor"]["writeText"](
                {"text": "body", "encoding": "utf-8", "lineEnding": "lf"}
            )

    def test_草稿与保存路径里没有_Python_侧定时器(self):
        """防抖只能在渲染层做一次：`scratch_service` / `editor_service` / `bridge/editor`
        不得出现任何 Qt 定时器（否则同一次编辑会被两侧各防抖一次，AC-40 的计时判据失真）。"""
        offenders: list[str] = []
        for path in NO_TIMER_FILES:
            text = path.read_text(encoding="utf-8")
            for token in TIMER_TOKENS:
                if token in text:
                    offenders.append(f"{path.name}: {token}")
        assert offenders == [], f"落盘路径出现 Python 侧定时器：{offenders}"

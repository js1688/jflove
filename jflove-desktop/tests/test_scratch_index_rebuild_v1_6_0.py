"""
v1.6.0 草稿库「索引损坏 ⇒ 按目录重建条目」回归测试（Phase 5 审查 S-7 修复）

背景（`文档记录/代码审查报告/v1.6.0.md` S-7，严重）：`src/services/scratch_service.py`
的 `_load_index()` 在「索引读不出来且 `.tmp` 副本也不可用」时直接返回**空索引**，
而写路径（`save_draft` / `discard_draft` / `mark_clean_exit`）会把读到的索引用
`_write_index()` **整体回写** ⇒ 索引损坏后的第一次保存，会把其它窗格的条目静默抹掉；
`pane-*.txt` 正文仍在磁盘上，但 `list_drafts()` 只认索引，条目再也无法被还原。

修复口径：索引不可用（半截 JSON / 副本也坏 / 结构里一条有效条目都没有）时，
**按草稿库目录扫描 `pane-<id>.txt` 重建条目**，只有目录里也没有内容文件才退回空索引。
重建只保证「内容还在」，标题 / 来源路径 / 几何 / 光标等元信息随索引丢失而回默认值。

覆盖点：
  1. 索引损坏 ⇒ 写路径保存新草稿后，**老条目仍在**（本修复的核心判据）
  2. 索引损坏 ⇒ `list_drafts()` 仍列出全部可还原条目，并带「已按目录重建」warning
  3. 索引结构完整但条目为空 + 内容文件仍在 ⇒ 同样重建
  4. 索引损坏且目录也没有内容文件 ⇒ 保持既有行为（空索引 + 原 warning 文案）
  5. 重建条目的元信息回默认值，但**内容文件逐字节不动**
  6. 重建**不复活**已丢弃 / 已清空的草稿
  7. 非法文件名的内容文件不参与重建
  8. 读路径（`draftList`）不擅自修复磁盘上的损坏索引
  9. 并发保存（多 paneId）在重建路径下也不丢条目

安全口径：全部用例把 `settings.SCRATCH_DIR` 指向 `tmp_path`，绝不触碰真实
`USER_DATA_DIR` 的草稿目录；断言只涉及条目数与文件存在性，不把正文写进日志。
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.config import settings
from src.services import scratch_service

LOGGER_NAME = "src.services.scratch_service"

BAD_INDEX_TEXT = '{"version": 1, "panes": ['
REBUILD_WARNING_PREFIX = "草稿索引不可用，已按草稿库目录重建"
NO_RECOVERY_WARNING = "草稿索引损坏且无可用临时副本，已按空索引处理（不影响已保存文件）"


# ── fixtures / 工具 ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def scratch_dir(tmp_path, monkeypatch):
    """把草稿库根目录指向临时目录（用例结束后自动还原 settings）。"""
    target = tmp_path / "scratch"
    monkeypatch.setattr(settings, "SCRATCH_DIR", str(target))
    return target


def _pane(pane_id: str = "a1b2c3", **overrides) -> dict:
    """构造 `editor.draftSave` 的 `pane` 参数（字段照 §7.1 #6 / §6.3.1）。"""
    pane = {
        "id": pane_id,
        "title": "a.log",
        "path": None,
        "language": "text",
        "encoding": "utf-8",
        "lineEnd": "lf",
        "read_only": False,
        "dirty": True,
        "geometry": {"x": 120, "y": 80, "w": 720, "h": 480, "z": 3,
                     "min": False, "maxed": False},
        "caret": {"line": 12, "col": 4},
        "scroll_top": 0,
        "font_size": 14,
        "show_line_numbers": True,
        "word_wrap": False,
    }
    pane.update(overrides)
    return pane


def _read_index(scratch) -> dict:
    return json.loads((scratch / "index.json").read_text(encoding="utf-8"))


def _index_ids(scratch) -> list[str]:
    return sorted(entry["id"] for entry in _read_index(scratch)["panes"])


def _corrupt_index(scratch) -> None:
    """让 `index.json` 半截，并清空 `.tmp/` 里所有候选副本（⇒ 走到"重建"分支）。"""
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "index.json").write_text(BAD_INDEX_TEXT, encoding="utf-8")
    tmp_dir = scratch / ".tmp"
    if tmp_dir.is_dir():
        for leftover in tmp_dir.glob("index.json.*.tmp"):
            leftover.unlink()


def _write_pane_file(scratch, pane_id: str, content: str) -> None:
    """直接落一个内容文件（模拟"索引丢了但正文还在"的孤儿）。"""
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / f"pane-{pane_id}.txt").write_text(content, encoding="utf-8")


# ── 1. 写路径：损坏后的保存不得吞掉其它条目（S-7 核心） ─────────────

class TestSaveAfterIndexLoss:
    """索引损坏 ⇒ 后续保存必须保住老条目（否则等于静默丢草稿）。"""

    def test_损坏后保存_老条目仍在(self, scratch_dir):
        """S-7 直接判据：先有 p1/p2，再损坏索引，保存 p3 ⇒ 三个条目都在。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.save_draft(_pane("p2"), "乙")
        assert _index_ids(scratch_dir) == ["p1", "p2"]

        _corrupt_index(scratch_dir)
        assert scratch_service.save_draft(_pane("p3"), "丙")["ok"] is True

        assert _index_ids(scratch_dir) == ["p1", "p2", "p3"]
        # 正文文件三份都还在，且内容未被重建过程改写
        for pane_id, text in (("p1", "甲"), ("p2", "乙"), ("p3", "丙")):
            path = scratch_dir / f"pane-{pane_id}.txt"
            assert path.read_text(encoding="utf-8") == text

    def test_损坏后保存_新草稿可被再次读出(self, scratch_dir):
        """重建出的索引是正常的 v1 索引：再走一次 list_drafts 能列出全部条目。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.save_draft(_pane("p2"), "乙")
        _corrupt_index(scratch_dir)
        scratch_service.save_draft(_pane("p3"), "丙")

        result = scratch_service.list_drafts()
        assert [entry["id"] for entry in result["entries"]] == ["p1", "p2", "p3"]
        assert result["cleanExit"] is False  # 写路径已置 false（有待还原内容）
        assert result["warnings"] == []

    def test_损坏后并发保存_不丢条目(self, scratch_dir):
        """重建 + 并发写（不同 paneId）不得互相覆盖：索引最终含全部条目。"""
        scratch_service.save_draft(_pane("p0"), "零")
        _corrupt_index(scratch_dir)
        ids = [f"c{i}" for i in range(4)]

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(
                lambda pane_id: scratch_service.save_draft(_pane(pane_id), pane_id),
                ids,
            ))
        assert all(item["ok"] is True for item in results)
        assert _index_ids(scratch_dir) == sorted(["p0", *ids])

    def test_损坏后丢弃_只减该条不重估其它(self, scratch_dir):
        """重建路径下丢弃单个草稿：只少一条，其余条目与正文都保留。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.save_draft(_pane("p2"), "乙")
        _corrupt_index(scratch_dir)

        assert scratch_service.discard_draft("p2") == {"ok": True}
        assert _index_ids(scratch_dir) == ["p1"]
        assert not (scratch_dir / "pane-p2.txt").exists()
        assert (scratch_dir / "pane-p1.txt").read_text(encoding="utf-8") == "甲"


# ── 2. 读路径：损坏后仍能列出可还原条目 ─────────────────────────────

class TestListAfterIndexLoss:
    """`draftList` 是启动还原的依据，索引损坏不得让它报"什么都没有"。"""

    def test_索引损坏_按目录重建并给出warning(self, scratch_dir, caplog):
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.save_draft(_pane("p2"), "乙")
        _corrupt_index(scratch_dir)

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = scratch_service.list_drafts()
        assert result["ok"] is True
        assert [entry["id"] for entry in result["entries"]] == ["p1", "p2"]
        assert result["warnings"] == [f"{REBUILD_WARNING_PREFIX} 2 个条目（窗格位置与光标等元信息无法恢复）"]  # noqa: E501
        assert any("按草稿库目录重建" in record.getMessage() for record in caplog.records)
        # 不可信数据不得反过来触发"异常退出 ⇒ 自动还原"分支
        assert result["cleanExit"] is True
        assert scratch_service.is_abnormal_exit() is False

    def test_索引结构为空但正文仍在_同样重建(self, scratch_dir):
        """结构完整（`panes: []`）但一条有效条目都没有 ⇒ 也以目录为准。"""
        (scratch_dir).mkdir(parents=True, exist_ok=True)
        (scratch_dir / "index.json").write_text(
            json.dumps({"version": 1, "clean_exit": False, "updated_at": 3.0, "panes": []}),
            encoding="utf-8",
        )
        _write_pane_file(scratch_dir, "p7", "七")
        _write_pane_file(scratch_dir, "p8", "八")

        result = scratch_service.list_drafts()
        assert [entry["id"] for entry in result["entries"]] == ["p7", "p8"]
        assert result["warnings"] == [f"{REBUILD_WARNING_PREFIX} 2 个条目（窗格位置与光标等元信息无法恢复）"]  # noqa: E501

    def test_索引结构里全是非法条目_同样重建(self, scratch_dir):
        """`panes` 里都是非法 id / 非字典 ⇒ 归一后为空 ⇒ 也按目录重建。"""
        scratch_dir.mkdir(parents=True, exist_ok=True)
        (scratch_dir / "index.json").write_text(
            json.dumps({"version": 1, "clean_exit": True,
                        "panes": [{"id": "../evil"}, "not-a-dict", 3]}),
            encoding="utf-8",
        )
        _write_pane_file(scratch_dir, "p1", "正文还在")

        result = scratch_service.list_drafts()
        assert [entry["id"] for entry in result["entries"]] == ["p1"]

    def test_重建条目元信息回默认值_内容不动(self, scratch_dir):
        """重建只能保住内容：标题 / 来源路径等回默认值，正文文件逐字节不动。"""
        scratch_service.save_draft(
            _pane("p1", title="笔记.md", path=r"D:\资料\笔记.md"), "原始正文\n",
        )
        before = (scratch_dir / "pane-p1.txt").read_bytes()
        _corrupt_index(scratch_dir)

        entry = scratch_service.list_drafts()["entries"][0]
        assert entry["id"] == "p1"
        assert entry["title"] == "" and entry["path"] is None
        assert entry["language"] == "text" and entry["encoding"] == "utf-8"
        assert entry["font_size"] == 14 and entry["word_wrap"] is False
        assert entry["updated_at"] > 0
        assert (scratch_dir / "pane-p1.txt").read_bytes() == before

    def test_读路径不修复磁盘索引(self, scratch_dir):
        """`draftList` 只读：重建结果留在内存，损坏的索引文件不被改动。"""
        _write_pane_file(scratch_dir, "p1", "甲")
        _corrupt_index(scratch_dir)
        before = (scratch_dir / "index.json").read_bytes()

        scratch_service.list_drafts()
        assert (scratch_dir / "index.json").read_bytes() == before


# ── 3. 不得过头：没有可重建内容 / 不得复活已清理的草稿 ───────────────

class TestRebuildStaysConservative:
    """重建只在"有内容文件"时发生，且绝不复活已被用户丢弃 / 清空的草稿。"""

    def test_损坏且无内容文件_保持既有空索引行为(self, scratch_dir, caplog):
        """目录里没有 `pane-*.txt` ⇒ 完全维持修复前的行为与文案。"""
        scratch_dir.mkdir(parents=True, exist_ok=True)
        (scratch_dir / "index.json").write_text(BAD_INDEX_TEXT, encoding="utf-8")
        tmp_dir = scratch_dir / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        (tmp_dir / "index.json.zzzz.tmp").write_text("这不是 JSON", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = scratch_service.list_drafts()
        assert result["entries"] == []
        assert result["cleanExit"] is True
        assert result["warnings"] == [NO_RECOVERY_WARNING]
        assert any("不可用" in record.getMessage() for record in caplog.records)

    def test_首次运行_无目录无索引_无warning(self, scratch_dir):
        """全新用户：既没有索引也没有内容文件 ⇒ 空清单且不打扰用户。"""
        result = scratch_service.list_drafts()
        assert result == {"ok": True, "cleanExit": True, "entries": [], "warnings": []}

    def test_不复活已丢弃的草稿(self, scratch_dir):
        """丢弃会删正文文件 ⇒ 索引损坏后重建也不该让它回来（AC-59）。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.save_draft(_pane("p2"), "乙")
        scratch_service.discard_draft("p2")
        _corrupt_index(scratch_dir)

        result = scratch_service.list_drafts()
        assert [entry["id"] for entry in result["entries"]] == ["p1"]

    def test_不复活已清空的草稿库(self, scratch_dir):
        """一键清空会删掉全部正文文件 ⇒ 之后索引再坏也是空清单（AC-61）。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.clear_all_drafts()
        _corrupt_index(scratch_dir)

        result = scratch_service.list_drafts()
        assert result["entries"] == []
        assert result["warnings"] == [NO_RECOVERY_WARNING]

    def test_非法文件名的内容文件不参与重建(self, scratch_dir):
        """`pane-bad!.txt` 不是本服务写出的内容文件 ⇒ 不进索引也不被删除。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        _corrupt_index(scratch_dir)
        stray = scratch_dir / "pane-bad!.txt"
        stray.write_text("外部文件", encoding="utf-8")

        result = scratch_service.list_drafts()
        assert [entry["id"] for entry in result["entries"]] == ["p1"]
        assert stray.read_text(encoding="utf-8") == "外部文件"

    def test_损坏后重建_干净退出标记仍可写入(self, scratch_dir):
        """重建出的索引仍是合法索引：`mark_clean_exit()` 能正常落盘且保留条目。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        _corrupt_index(scratch_dir)

        assert scratch_service.mark_clean_exit() == {"ok": True}
        index = _read_index(scratch_dir)
        assert index["clean_exit"] is True
        assert [entry["id"] for entry in index["panes"]] == ["p1"]

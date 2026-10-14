"""
v1.6.0 草稿库服务（`src/services/scratch_service.py`）单元测试

设计依据：`文档记录/设计文档/v1.6.0.md` §6.3（草稿库 / 原子写 / 崩溃检测，AC-S2）
与 §6.4（退出握手）、§7.1（桥方法 `editor.draft*` 返回形状）、§8.3（本地数据安全口径）。

覆盖点（与设计条款一一对应）：
  1. 目录结构：`index.json` + `pane-<paneId>.txt` + `.tmp/`，临时文件为 `mkstemp` 随机名
  2. 两个原子写：内容文件与索引都走「临时文件 + os.replace」，成功不留残余
  3. 写序：先内容后索引；索引写失败 ⇒ 内容完整 + 旧索引逐字节未变
  4. 加载：索引有条目但内容文件缺失 ⇒ 跳过并记 warning（不阻塞、不抛）
  5. 半截 JSON：退回 `.tmp/` 最新副本；`.tmp` 也坏 ⇒ 空索引不抛异常
  6. 崩溃检测：`clean_exit == false` ⇒ 异常退出判定；干净退出写 true
  7. 一键清空草稿（AC-61）不影响草稿库之外的文件
  8. 并发同 paneId 写入串行化（不交错），跨 paneId 不丢条目

安全口径：全部用例把 `settings.SCRATCH_DIR` 指向 `tmp_path`，
**绝不写真实 USER_DATA_DIR 的草稿目录**；另有用例断言日志不含文档正文（AGENTS §9.4.14）。
"""
from __future__ import annotations

import codecs
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.config import settings
from src.services import scratch_service

LOGGER_NAME = "src.services.scratch_service"


# ── fixtures / 工具 ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def scratch_dir(tmp_path, monkeypatch):
    """把草稿库根目录指向临时目录（用例结束后自动还原 settings）。"""
    target = tmp_path / "scratch"
    monkeypatch.setattr(settings, "SCRATCH_DIR", str(target))
    return target


def _pane(pane_id: str = "a1b2c3", **overrides) -> dict:
    """构造一个 `editor.draftSave` 的 `pane` 参数（字段照 §7.1 #6 / §6.3.1）。"""
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


def _stored_entry(pane_id: str, **overrides) -> dict:
    """构造磁盘态索引条目（存储名 `line_end`，照 §6.3.1）。"""
    entry = {
        "id": pane_id,
        "title": "t.log",
        "path": None,
        "language": "text",
        "encoding": "utf-8",
        "line_end": "lf",
        "read_only": False,
        "dirty": True,
        "geometry": {"x": 0, "y": 0, "w": 10, "h": 10, "z": 0,
                     "min": False, "maxed": False},
        "caret": {"line": 1, "col": 1},
        "scroll_top": 0,
        "font_size": 14,
        "show_line_numbers": True,
        "word_wrap": False,
        "updated_at": 1.0,
    }
    entry.update(overrides)
    return entry


def _read_index(scratch) -> dict:
    """读磁盘 `index.json`（测试断言用）。"""
    return json.loads((scratch / "index.json").read_text(encoding="utf-8"))


def _write_index(scratch, payload: dict) -> None:
    """直接写磁盘 `index.json`（模拟历史遗留 / 损坏场景）。"""
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "index.json").write_text(json.dumps(payload, ensure_ascii=False),
                                        encoding="utf-8")


def _tmp_files(scratch) -> list:
    """列出 `.tmp/` 下所有残余文件。"""
    tmp_dir = scratch / ".tmp"
    return sorted(tmp_dir.iterdir()) if tmp_dir.exists() else []


# ── 1. 目录结构与内容文件落盘 ────────────────────────────────────────

class TestScratchLayout:

    def test_保存草稿_生成索引与内容文件(self, scratch_dir):
        """§6.3.1：内容落 `pane-<id>.txt`、索引落 `index.json`、临时目录为 `.tmp/`。"""
        result = scratch_service.save_draft(_pane("p1"), "hello 草稿")
        assert result["ok"] is True and isinstance(result["at"], float)
        assert (scratch_dir / "pane-p1.txt").read_text(encoding="utf-8") == "hello 草稿"
        index = _read_index(scratch_dir)
        assert index["version"] == 1
        assert [e["id"] for e in index["panes"]] == ["p1"]
        assert (scratch_dir / ".tmp").is_dir()
        # 成功路径不留下任何临时文件
        assert _tmp_files(scratch_dir) == []

    def test_内容文件按UTF8原样写入(self, scratch_dir):
        """正文 UTF-8 明文写入，**不做换行转换**、不加 BOM（§6.3.1）。"""
        content = "第一行\r\nsecond\n\t中文 😀"
        scratch_service.save_draft(_pane("p1"), content)
        raw = (scratch_dir / "pane-p1.txt").read_bytes()
        assert raw == content.encode("utf-8")
        assert not raw.startswith(codecs.BOM_UTF8)

    def test_索引条目字段与公开PaneMeta形状(self, scratch_dir):
        """§6.3.1 存储名 `line_end` 与 §7.1 桥参数名 `lineEnd` 同时可用。"""
        scratch_service.save_draft(_pane("p1", lineEnd="crlf"), "x")
        stored = _read_index(scratch_dir)["panes"][0]
        assert stored["line_end"] == "crlf"
        assert stored["geometry"]["x"] == 120 and stored["caret"]["line"] == 12
        entry = scratch_service.list_drafts()["entries"][0]
        assert entry["lineEnd"] == "crlf" and entry["line_end"] == "crlf"
        assert entry["id"] == "p1" and entry["dirty"] is True

    def test_索引为整体替换_同窗格不重复条目(self, scratch_dir):
        """索引每次整体替换：同一 paneId 多次保存仍只有一条，updated_at 递增。"""
        first = scratch_service.save_draft(_pane("p1"), "一")
        second = scratch_service.save_draft(_pane("p1"), "二")
        index = _read_index(scratch_dir)
        assert len(index["panes"]) == 1
        assert second["at"] >= first["at"]
        assert index["panes"][0]["updated_at"] == second["at"]
        assert (scratch_dir / "pane-p1.txt").read_text(encoding="utf-8") == "二"

    def test_正文非字符串_拒绝写入(self, scratch_dir):
        """参数非法 ⇒ 抛错（桥转 `{ok:false}`），不落任何文件。"""
        with pytest.raises(ValueError):
            scratch_service.save_draft(_pane("p1"), b"bytes")  # type: ignore[arg-type]
        assert _tmp_files(scratch_dir) == []
        assert not (scratch_dir / "index.json").exists()

    def test_paneId非法_拒绝且不越界写文件(self, scratch_dir, tmp_path):
        """paneId 白名单校验挡住 `../` 路径穿越（草稿库外不得出现任何文件）。"""
        for bad in ["", "../evil", "a/b", "a\\b", "x" * 65, "id with space"]:
            with pytest.raises(ValueError):
                scratch_service.save_draft(_pane(bad), "x")
        assert list(tmp_path.rglob("*.txt")) == []

    def test_草稿目录动态解析settings(self, scratch_dir, tmp_path, monkeypatch):
        """目录每次调用都读 `settings.SCRATCH_DIR`（测试可注入，不做模块级缓存）。"""
        scratch_service.save_draft(_pane("p1"), "a")
        assert (scratch_dir / "pane-p1.txt").exists()
        other = tmp_path / "scratch2"
        monkeypatch.setattr(settings, "SCRATCH_DIR", str(other))
        scratch_service.save_draft(_pane("p2"), "b")
        assert (other / "pane-p2.txt").read_text(encoding="utf-8") == "b"
        assert not (scratch_dir / "pane-p2.txt").exists()


# ── 2. 原子写（AC-S2） ───────────────────────────────────────────────

class TestAtomicWrite:

    def test_临时文件为mkstemp随机名且落在tmp目录(self, scratch_dir, monkeypatch):
        """§6.3.1：`<目标名>.<rand>.tmp` 落在 `.tmp/`，两次写入名不同（非固定名）。"""
        seen: list[tuple[str, str]] = []
        real_replace = os.replace

        def spy(src, dst, *args, **kwargs):
            seen.append((src, dst))
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(scratch_service.os, "replace", spy)
        scratch_service.save_draft(_pane("p1"), "one")
        scratch_service.save_draft(_pane("p1"), "two")

        content_tmps = [s for s, d in seen if os.path.basename(d) == "pane-p1.txt"]
        index_tmps = [s for s, d in seen if os.path.basename(d) == "index.json"]
        assert len(content_tmps) == 2 and len(index_tmps) == 2
        for src, _dst in seen:
            assert os.path.dirname(src) == str(scratch_dir / ".tmp")
            assert src.endswith(".tmp")
        # 内容临时名 = pane-p1.txt.<rand>.tmp；索引临时名 = index.json.<rand>.tmp
        assert os.path.basename(content_tmps[0]).startswith("pane-p1.txt.")
        assert os.path.basename(index_tmps[0]).startswith("index.json.")
        assert content_tmps[0] != content_tmps[1]
        assert index_tmps[0] != index_tmps[1]

    def test_索引写失败_失败路径不留临时文件(self, scratch_dir, monkeypatch):
        """`os.replace` 抛错 ⇒ 清理临时文件、原索引逐字节未变、异常上抛（AC-41 ④）。"""
        scratch_service.save_draft(_pane("p1"), "旧")
        index_path = scratch_dir / "index.json"
        before = index_path.read_bytes()

        def boom(src, dst, *args, **kwargs):
            raise PermissionError("模拟目标被占用")

        monkeypatch.setattr(scratch_service.os, "replace", boom)
        monkeypatch.setattr(scratch_service, "_REPLACE_DELAY_SECONDS", 0.0)
        with pytest.raises(PermissionError):
            scratch_service.save_draft(_pane("p1"), "新")
        assert index_path.read_bytes() == before
        assert _tmp_files(scratch_dir) == []


# ── 3. 写序：先内容、再索引（§6.3.2） ─────────────────────────────────

class TestWriteOrder:

    def test_索引写失败_内容完整且旧索引未被破坏(self, scratch_dir, monkeypatch):
        """索引写失败时：新内容已完整落盘，旧索引原样保留（不存在第三种状态）。"""
        scratch_service.save_draft(_pane("p1"), "旧内容")
        index_path = scratch_dir / "index.json"
        before = index_path.read_bytes()
        real = scratch_service._atomic_write_bytes
        new_content = "新内容" * 500

        def flaky(path, data):
            if os.path.basename(path) == "index.json":
                raise OSError("模拟索引写失败")
            return real(path, data)

        monkeypatch.setattr(scratch_service, "_atomic_write_bytes", flaky)
        with pytest.raises(OSError):
            scratch_service.save_draft(_pane("p1"), new_content)
        # ① 内容完整（先写的那一步）
        assert (scratch_dir / "pane-p1.txt").read_text(encoding="utf-8") == new_content
        # ② 旧索引未被破坏，且不留临时文件
        assert index_path.read_bytes() == before
        assert _tmp_files(scratch_dir) == []

    def test_内容写失败_索引完全不写(self, scratch_dir, monkeypatch):
        """①失败 ⇒ 不进入②：索引既不新建也不改写。"""
        real = scratch_service._atomic_write_bytes

        def content_boom(path, data):
            if os.path.basename(path).startswith("pane-"):
                raise OSError("模拟内容写失败")
            return real(path, data)

        monkeypatch.setattr(scratch_service, "_atomic_write_bytes", content_boom)
        with pytest.raises(OSError):
            scratch_service.save_draft(_pane("p1"), "写不进去")
        assert not (scratch_dir / "index.json").exists()

        # 已有索引的场景：旧索引同样保持原样
        monkeypatch.setattr(scratch_service, "_atomic_write_bytes", real)
        scratch_service.save_draft(_pane("p1"), "基线")
        before = (scratch_dir / "index.json").read_bytes()
        monkeypatch.setattr(scratch_service, "_atomic_write_bytes", content_boom)
        with pytest.raises(OSError):
            scratch_service.save_draft(_pane("p1"), "再次失败")
        assert (scratch_dir / "index.json").read_bytes() == before
        assert (scratch_dir / "pane-p1.txt").read_text(encoding="utf-8") == "基线"


# ── 4. 加载容错：缺失内容文件 / 半截 JSON ────────────────────────────

class TestLoadTolerance:

    def test_索引不存在_首次运行返回空且不报错(self, scratch_dir, caplog):
        """首次运行：无索引 ⇒ 空条目、cleanExit True、无 warning、不抛异常。"""
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = scratch_service.list_drafts()
        assert result == {"ok": True, "cleanExit": True, "entries": [], "warnings": []}
        assert caplog.records == []

    def test_内容文件缺失_跳过并记warning(self, scratch_dir, caplog):
        """§6.3.2：索引有条目但内容文件缺失 ⇒ 跳过 + warning，**不阻塞、不抛**。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.save_draft(_pane("p2"), "乙")
        (scratch_dir / "pane-p1.txt").unlink()

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = scratch_service.list_drafts()
        assert [e["id"] for e in result["entries"]] == ["p2"]
        assert result["warnings"] == ["已跳过 1 个缺少内容文件的草稿条目"]
        skips = [r for r in caplog.records if "缺少内容文件" in r.getMessage()]
        assert skips and all(r.levelno == logging.WARNING for r in skips)

    def test_索引损坏_退回tmp最新副本(self, scratch_dir, caplog):
        """§6.3.2 ②：`index.json` 半截 ⇒ 用 `.tmp/` 下**最新**一份副本恢复。"""
        tmp_dir = scratch_dir / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        older = tmp_dir / "index.json.aaaa.tmp"
        newer = tmp_dir / "index.json.bbbb.tmp"
        older.write_text(json.dumps({"version": 1, "clean_exit": True,
                                     "updated_at": 1.0, "panes": []}),
                         encoding="utf-8")
        newer.write_text(json.dumps({"version": 1, "clean_exit": False, "updated_at": 2.0,
                                     "panes": [_stored_entry("p9")]}),
                         encoding="utf-8")
        os.utime(older, (1000, 1000))
        os.utime(newer, (2000, 2000))
        (scratch_dir / "pane-p9.txt").write_text("从副本恢复的内容", encoding="utf-8")
        (scratch_dir / "index.json").write_text('{"version": 1, "panes": [', encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = scratch_service.list_drafts()
        assert result["cleanExit"] is False
        assert [e["id"] for e in result["entries"]] == ["p9"]
        assert result["warnings"] == ["草稿索引损坏，已从临时副本恢复"]
        assert any("副本恢复" in r.getMessage() for r in caplog.records)
        # 恢复出的条目仍指向完整内容
        assert scratch_service.is_abnormal_exit() is True

    def test_索引与tmp都损坏_空索引不抛异常(self, scratch_dir, caplog):
        """§6.3.2 ③：两者都坏 ⇒ 空索引 + warning，不抛异常、不弹阻塞对话框。"""
        tmp_dir = scratch_dir / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        (tmp_dir / "index.json.zzzz.tmp").write_text("这不是 JSON", encoding="utf-8")
        (scratch_dir / "index.json").write_text("{{{ 半截", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = scratch_service.list_drafts()
        assert result["ok"] is True
        assert result["entries"] == []
        assert result["cleanExit"] is True
        assert result["warnings"] == [
            "草稿索引损坏且无可用临时副本，已按空索引处理（不影响已保存文件）",
        ]
        assert any("不可用" in r.getMessage() for r in caplog.records)

    def test_索引结构损坏字段_归一化不抛异常(self, scratch_dir):
        """结构怪（panes 非数组 / id 非法 / clean_exit 非布尔）⇒ 归一成安全空索引。"""
        _write_index(scratch_dir, {"version": "x", "clean_exit": "yes",
                                   "panes": [{"id": "../evil"}, "not-a-dict", 3]})
        result = scratch_service.list_drafts()
        assert result["entries"] == []
        assert result["cleanExit"] is True  # 不可信 clean_exit 不得误触发"异常退出"还原

    def test_list_drafts_不修改磁盘索引(self, scratch_dir):
        """`draftList` 是只读动作：过滤缺失条目只发生在内存，不落盘。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.save_draft(_pane("p2"), "乙")
        (scratch_dir / "pane-p1.txt").unlink()
        before = (scratch_dir / "index.json").read_bytes()
        scratch_service.list_drafts()
        assert (scratch_dir / "index.json").read_bytes() == before

    def test_崩在两步之间_索引偏旧但内容完整(self, scratch_dir):
        """§6.3.2 写序推论：崩在「内容已写、索引未写」之间 ⇒ 内容完整、索引 timestamp 偏旧。"""
        scratch_service.save_draft(_pane("p1"), "旧内容")
        # 模拟崩溃后的磁盘状态：索引停在旧时间戳，内容文件已是"刚写完的新内容"
        _write_index(scratch_dir, {"version": 1, "clean_exit": False, "updated_at": 1.0,
                                   "panes": [_stored_entry("p1", updated_at=1.0)]})
        (scratch_dir / "pane-p1.txt").write_text("崩溃前刚写完的新内容", encoding="utf-8")

        drafts = scratch_service.list_drafts()
        assert [e["id"] for e in drafts["entries"]] == ["p1"]
        assert drafts["entries"][0]["updated_at"] == 1.0  # 索引偏旧
        assert (scratch_dir / "pane-p1.txt").read_text(
            encoding="utf-8") == "崩溃前刚写完的新内容"  # 内容完整

    def test_索引损坏时保存仍成功(self, scratch_dir):
        """写路径同样经半截 JSON 容错：索引损坏不阻塞新草稿写入（不抛异常）。"""
        scratch_dir.mkdir(parents=True, exist_ok=True)
        (scratch_dir / "index.json").write_text("{半截", encoding="utf-8")
        assert scratch_service.save_draft(_pane("p1"), "恢复写入")["ok"] is True
        index = _read_index(scratch_dir)
        assert [e["id"] for e in index["panes"]] == ["p1"]
        assert index["clean_exit"] is False


# ── 5. 崩溃检测与退出握手（§6.3.2 / §6.4） ───────────────────────────

class TestCrashDetection:

    def test_clean_exit_false_判定异常退出(self, scratch_dir):
        """启动读到 `clean_exit:false` ⇒ 异常退出（供上层自动还原全部窗格，AC-60）。"""
        _write_index(scratch_dir, {"version": 1, "clean_exit": False, "updated_at": 5.0,
                                   "panes": [_stored_entry("p1")]})
        (scratch_dir / "pane-p1.txt").write_text("崩前内容", encoding="utf-8")
        assert scratch_service.is_abnormal_exit() is True
        drafts = scratch_service.list_drafts()
        assert drafts["cleanExit"] is False
        assert [e["id"] for e in drafts["entries"]] == ["p1"]

    def test_保存草稿置clean_exit为false(self, scratch_dir):
        """新草稿落盘 ⇒ 磁盘上已有待还原内容 ⇒ `clean_exit = false`（§6.3.1 示例）。"""
        scratch_service.save_draft(_pane("p1"), "x")
        assert _read_index(scratch_dir)["clean_exit"] is False
        assert scratch_service.is_abnormal_exit() is True

    def test_mark_clean_exit_写true且保留草稿(self, scratch_dir):
        """§6.4：干净退出写 `clean_exit=true`，**保留**草稿（是否清理由 retain 决定）。"""
        scratch_service.save_draft(_pane("p1"), "保留我")
        assert scratch_service.mark_clean_exit() == {"ok": True}
        index = _read_index(scratch_dir)
        assert index["clean_exit"] is True
        assert [e["id"] for e in index["panes"]] == ["p1"]
        assert (scratch_dir / "pane-p1.txt").read_text(encoding="utf-8") == "保留我"
        assert scratch_service.is_abnormal_exit() is False
        assert scratch_service.list_drafts()["cleanExit"] is True

    def test_mark_clean_exit_无索引时也可调用(self, scratch_dir):
        """首次运行就退出（无任何草稿）⇒ 写标记不抛异常。"""
        assert scratch_service.mark_clean_exit() == {"ok": True}
        assert _read_index(scratch_dir)["clean_exit"] is True


# ── 6. 丢弃单窗格 / 一键清空（AC-59 / AC-61） ────────────────────────

class TestDiscardAndClear:

    def test_discard_draft_删除内容与条目(self, scratch_dir):
        """§6.4 单窗格「放弃」：内容文件与索引条目一并清理，重启不还原。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.save_draft(_pane("p2"), "乙")
        assert scratch_service.discard_draft("p1") == {"ok": True}
        assert not (scratch_dir / "pane-p1.txt").exists()
        assert [e["id"] for e in _read_index(scratch_dir)["panes"]] == ["p2"]

    def test_discard_draft_幂等(self, scratch_dir):
        """AC-59：不存在也返回 ok（幂等），且不抛异常、不写坏索引。"""
        assert scratch_service.discard_draft("never-exist") == {"ok": True}
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.discard_draft("p1")
        before = (scratch_dir / "index.json").read_bytes()
        assert scratch_service.discard_draft("p1") == {"ok": True}
        assert (scratch_dir / "index.json").read_bytes() == before

    def test_discard_draft_非法id拒绝(self, scratch_dir):
        """非法 paneId 抛错（桥转 `{ok:false}`）。"""
        with pytest.raises(ValueError):
            scratch_service.discard_draft("../evil")

    def test_clear_all_drafts_清空草稿库(self, scratch_dir, tmp_path):
        """AC-61：清空全部草稿 + 遗留孤儿文件 + 临时文件；**不影响**草稿库之外的文件。"""
        scratch_service.save_draft(_pane("p1"), "甲")
        scratch_service.save_draft(_pane("p2"), "乙")
        # 孤儿内容文件（索引里没有条目）+ 崩溃遗留临时文件
        (scratch_dir / "pane-orphan.txt").write_text("孤儿", encoding="utf-8")
        (scratch_dir / ".tmp" / "index.json.dead.tmp").write_text("{}", encoding="utf-8")
        # 用户已保存的真实文件（草稿库之外）绝不能被碰
        user_file = tmp_path / "user-saved.txt"
        user_file.write_text("用户已保存的文件", encoding="utf-8")

        result = scratch_service.clear_all_drafts()
        assert result == {"ok": True, "removed": 3}
        assert list(scratch_dir.glob("pane-*.txt")) == []
        assert _tmp_files(scratch_dir) == []
        index = _read_index(scratch_dir)
        assert index["panes"] == [] and index["clean_exit"] is True
        assert scratch_service.list_drafts()["entries"] == []
        assert user_file.read_text(encoding="utf-8") == "用户已保存的文件"

    def test_clear_all_drafts_空库可用(self, scratch_dir):
        """无草稿时一键清空是安全幂等操作（removed=0）。"""
        assert scratch_service.clear_all_drafts() == {"ok": True, "removed": 0}
        assert scratch_service.list_drafts()["entries"] == []


# ── 7. 并发写入串行化（§6.3.2 末行） ─────────────────────────────────

class TestConcurrency:

    def test_并发同一pane写入_串行化不交错(self, scratch_dir, monkeypatch):
        """同一 paneId 的写入排队执行（队列 + 单飞）：原子写临界区不重叠、内容不交错。"""
        real = scratch_service._atomic_write_bytes
        state = {"active": 0, "overlapped": False}
        guard = threading.Lock()

        def spy(path, data):
            with guard:
                state["active"] += 1
                if state["active"] > 1:
                    state["overlapped"] = True
            time.sleep(0.02)
            try:
                return real(path, data)
            finally:
                with guard:
                    state["active"] -= 1

        monkeypatch.setattr(scratch_service, "_atomic_write_bytes", spy)
        payloads = ["甲" * 5000, "乙" * 5000]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(scratch_service.save_draft, _pane("p1"), text)
                       for text in payloads]
            assert all(f.result()["ok"] is True for f in futures)

        assert state["overlapped"] is False
        # 内容逐字节等于其中一次的完整写入（未被另一次交错的中间态污染）
        assert (scratch_dir / "pane-p1.txt").read_text(encoding="utf-8") in payloads
        index = _read_index(scratch_dir)
        assert [e["id"] for e in index["panes"]] == ["p1"]

    def test_并发不同pane写入_索引不丢条目(self, scratch_dir):
        """跨窗格并发：索引「读 → 改 → 写」单飞，两条目都在、内容各自正确。"""
        panes = [(_pane(f"p{i}"), f"内容-{i}") for i in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(scratch_service.save_draft, pane, text)
                       for pane, text in panes]
            assert all(f.result()["ok"] is True for f in futures)

        index = _read_index(scratch_dir)
        assert sorted(e["id"] for e in index["panes"]) == [f"p{i}" for i in range(6)]
        for i in range(6):
            assert (scratch_dir / f"pane-p{i}.txt").read_text(encoding="utf-8") == f"内容-{i}"
        assert _tmp_files(scratch_dir) == []


# ── 8. 安全口径：日志不含正文（AGENTS §9.4.14 / 设计 §7.4） ──────────

class TestLoggingSafety:

    def test_日志不记录文档正文与完整路径(self, scratch_dir, caplog):
        """日志只记文件名 / paneId：文档正文与目录全路径都不得出现。"""
        secret = "绝密正文-MARKER-9f3a"
        pane = _pane("p1", path="/home/someone/private/secret-plan.txt")
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            scratch_service.save_draft(pane, secret)
            scratch_service.list_drafts()
            scratch_service.mark_clean_exit()
            scratch_service.discard_draft("p1")
            scratch_service.clear_all_drafts()
        text = caplog.text
        assert secret not in text
        assert "secret-plan.txt" not in text
        assert str(scratch_dir) not in text
        assert "pane-p1.txt" in text  # 文件名是可以记的

"""
v1.6.1 草稿索引向后兼容扩展（DS-30）单元测试

设计依据：`文档记录/设计文档/v1.6.1.md` §5.3（草稿索引演进——本版唯一的存储格式改动：
新增 `view_mode` / `order` / `active` 三个**可选**字段，`geometry` 分支保留不动）与
§13.2 **U-6**（`_normalize_stored_meta` 是否也走白名单 ⇒ 读取路径要不要同步扩展）。

U-6 结论（本文件用用例固定）：

    **读取路径同样是白名单**。`scratch_service.py:404` 的 `_normalize_stored_meta`
    直接委托 `:254` 的 `_normalize_pane_meta`（与写入侧**同一函数**），`:409` 的
    `_normalize_index` 只做外层容器归一，`:288` 的 `_to_public_meta` 是 `dict(meta)`
    原样透传 ⇒ 白名单外的键在读取时同样被丢弃（见 `TestReadPathIsWhitelist`）。
    但正因为读写共用 `_normalize_pane_meta`，3 个字段加在**一处**即两侧同时生效，
    `_normalize_stored_meta` / `_normalize_index` / `_to_public_meta` 均无需改动。

覆盖点（与 §5.3「兼容判据」表逐行对应）：
  1. v1.6.1 新索引：三字段往返一致（`save_draft` → `list_drafts` 逐字段相等）
  2. 渲染层发送非法 `view_mode`（如 `"bogus"`）⇒ 存为 `"edit"`，不抛错
  3. v1.6.0 旧 `index.json`（**有** `geometry`、**无**三个新字段）⇒ 还原成功、
     `view_mode == "edit"`、`order` / `active` 取默认值、`geometry` 逐字段未变
  4. 旧草稿条目（**无** `geometry` 键）⇒ 不抛异常，几何照旧填中性默认值
  5. 读取路径（U-6）自证：手工造一份**带**三字段的 `index.json` 直接读 ⇒ 三字段带出

安全口径：全部用例把 `settings.SCRATCH_DIR` 指向 `tmp_path`，
**绝不写真实 USER_DATA_DIR 的草稿目录**（沿用 `test_scratch_service_v1_6_0.py` 的
monkeypatch 做法）；断言不涉及文档正文，也不把路径写进日志。
"""
from __future__ import annotations

import json

import pytest

from src.config import settings
from src.services import scratch_service

VIEW_MODES = ("edit", "preview", "split", "table", "tree")

# `_normalize_geometry` 在缺字段时填的中性默认值（`:251-262`，v1.6.1 不动该分支）
NEUTRAL_GEOMETRY = {"x": 0, "y": 0, "w": 0, "h": 0, "z": 0, "min": False, "maxed": False}


# ── fixtures / 工具 ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def scratch_dir(tmp_path, monkeypatch):
    """把草稿库根目录指向临时目录（用例结束后自动还原 settings）。"""
    target = tmp_path / "scratch"
    monkeypatch.setattr(settings, "SCRATCH_DIR", str(target))
    return target


def _pane(pane_id: str = "a1b2c3", **overrides) -> dict:
    """构造一个 `editor.draftSave` 的 `pane` 参数（字段照 v1.6.1 §5.3）。"""
    pane = {
        "id": pane_id,
        "title": "a.log",
        "path": None,
        "language": "text",
        "encoding": "utf-8",
        "lineEnd": "lf",
        "read_only": False,
        "dirty": True,
        # 渲染层 v1.6.1 起**不再发送** `geometry`（此处仍带一份，验证旧字段不被破坏）
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
    """构造磁盘态索引条目（存储名 `line_end`，照 §6.3.1 的 v1.6.0 结构）。"""
    entry = {
        "id": pane_id,
        "title": "t.log",
        "path": None,
        "language": "text",
        "encoding": "utf-8",
        "line_end": "lf",
        "read_only": False,
        "dirty": True,
        "geometry": {"x": 1, "y": 2, "w": 30, "h": 40, "z": 5,
                     "min": True, "maxed": False},
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
    """直接写磁盘 `index.json`（模拟 v1.6.0 旧索引 / 手工造数据）。"""
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "index.json").write_text(json.dumps(payload, ensure_ascii=False),
                                        encoding="utf-8")


def _write_pane_file(scratch, pane_id: str, content: str = "正文") -> None:
    """直接落一个内容文件（`list_drafts` 会跳过缺内容文件的条目）。"""
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / f"pane-{pane_id}.txt").write_text(content, encoding="utf-8")


# ── 1. 三字段往返（§5.3 兼容判据第 2 行） ────────────────────────────

class TestNewFieldsRoundTrip:
    """`draftSave` 写进去的三字段，必须原样从 `draftList` 出来。"""

    def test_三字段往返_逐字段相等(self, scratch_dir):
        """§5.3：`view_mode` / `order` / `active` 往返一致（逐字段相等）。"""
        scratch_service.save_draft(
            _pane("p1", view_mode="preview", order=3, active=True), "正文甲",
        )

        entry = scratch_service.list_drafts()["entries"][0]
        assert entry["id"] == "p1"
        assert entry["view_mode"] == "preview"
        assert entry["order"] == 3
        assert entry["active"] is True
        # 存储态与公开态三字段一致（`_to_public_meta` 原样透传）
        stored = _read_index(scratch_dir)["panes"][0]
        assert (stored["view_mode"], stored["order"], stored["active"]) == (
            "preview", 3, True,
        )
        # 旧字段不受影响
        assert entry["lineEnd"] == "lf" and entry["line_end"] == "lf"
        assert entry["geometry"]["x"] == 120 and entry["caret"] == {"line": 12, "col": 4}

    @pytest.mark.parametrize("mode", VIEW_MODES)
    def test_白名单五值均可存取(self, scratch_dir, mode):
        """§5.3：白名单 5 值 `edit` / `preview` / `split` / `table` / `tree` 全部可往返。"""
        scratch_service.save_draft(_pane("p1", view_mode=mode), "x")
        assert scratch_service.list_drafts()["entries"][0]["view_mode"] == mode
        assert _read_index(scratch_dir)["panes"][0]["view_mode"] == mode

    def test_多条目按order与active区分(self, scratch_dir):
        """标签条语义：一批条目里 `order` 各不相同、至多一个 `active=True`。"""
        scratch_service.save_draft(_pane("p1", view_mode="edit", order=0, active=False), "甲")
        scratch_service.save_draft(_pane("p2", view_mode="split", order=1, active=True), "乙")
        scratch_service.save_draft(_pane("p3", view_mode="tree", order=2, active=False), "丙")

        entries = {e["id"]: e for e in scratch_service.list_drafts()["entries"]}
        assert [(entries[i]["order"], entries[i]["view_mode"], entries[i]["active"])
                for i in ("p1", "p2", "p3")] == [
            (0, "edit", False), (1, "split", True), (2, "tree", False),
        ]
        assert sum(1 for e in entries.values() if e["active"] is True) == 1

    def test_三字段缺失取默认值(self, scratch_dir):
        """三个新字段都是**可选**：老渲染层不发时取默认值，不抛错。"""
        scratch_service.save_draft(_pane("p1"), "x")
        entry = scratch_service.list_drafts()["entries"][0]
        assert entry["view_mode"] == "edit"
        assert entry["order"] == 0
        assert entry["active"] is False


# ── 2. 非法 view_mode 的宽松口径（§5.3 兼容判据第 3 行） ──────────────

class TestIllegalViewMode:
    """口径与 `line_end` 一致：非法值静默取默认，**不抛错**。"""

    def test_非法view_mode存为edit且不抛错(self, scratch_dir):
        """`"bogus"` ⇒ 存为 `"edit"`，保存与读取都成功。"""
        assert scratch_service.save_draft(_pane("p1", view_mode="bogus"), "x")["ok"] is True
        assert _read_index(scratch_dir)["panes"][0]["view_mode"] == "edit"
        assert scratch_service.list_drafts()["entries"][0]["view_mode"] == "edit"

    @pytest.mark.parametrize("bad", ["BOGUS", "", "  ", "edit2", "PREVIEW_", None, 7,
                                     True, [], {"mode": "edit"}])
    def test_各类非法值与错误类型_一律取edit(self, scratch_dir, bad):
        """大小写不匹配 / 空串 / 非字符串都退回默认（宽松归一，不抛异常）。"""
        assert scratch_service.save_draft(_pane("p1", view_mode=bad), "x")["ok"] is True
        assert _read_index(scratch_dir)["panes"][0]["view_mode"] == "edit"

    def test_大写白名单值被归一为小写(self, scratch_dir):
        """`"PREVIEW"` 按 `line_end` 的既有口径 strip+lower 后命中白名单。"""
        scratch_service.save_draft(_pane("p1", view_mode="  PREVIEW "), "x")
        assert _read_index(scratch_dir)["panes"][0]["view_mode"] == "preview"

    @pytest.mark.parametrize("bad_order", [True, "3", None, [], 3.9])
    def test_order类型不可信取默认(self, scratch_dir, bad_order):
        """`_as_int` 口径：bool / 字符串 / 非数字退回 0；浮点截断取整。"""
        scratch_service.save_draft(_pane("p1", order=bad_order), "x")
        expected = 3 if bad_order == 3.9 else 0
        assert _read_index(scratch_dir)["panes"][0]["order"] == expected

    @pytest.mark.parametrize("bad_active", ["yes", 1, 0, None, []])
    def test_active非布尔取默认False(self, scratch_dir, bad_active):
        """`_as_bool` 口径：只有真布尔才算数，其余退回 False。"""
        scratch_service.save_draft(_pane("p1", active=bad_active), "x")
        assert _read_index(scratch_dir)["panes"][0]["active"] is False


# ── 3. v1.6.0 旧索引向后兼容（§5.3 兼容判据第 1 行） ─────────────────

class TestV160IndexBackwardCompat:
    """旧 `index.json` 一条都不能丢：有 `geometry`、无新字段。"""

    def test_旧索引有geometry无新字段_读取成功且取默认(self, scratch_dir):
        """v1.6.0 旧索引 ⇒ `list_drafts()` 成功，新字段取默认值，`geometry` 原样保留。"""
        _write_index(scratch_dir, {
            "version": 1,
            "clean_exit": False,
            "updated_at": 5.0,
            "panes": [_stored_entry("p1")],
        })
        _write_pane_file(scratch_dir, "p1", "旧版落盘的正文")

        result = scratch_service.list_drafts()
        assert result["ok"] is True
        assert result["cleanExit"] is False
        assert result["warnings"] == []
        assert [e["id"] for e in result["entries"]] == ["p1"]

        entry = result["entries"][0]
        assert entry["view_mode"] == "edit"
        assert entry["order"] == 0
        assert entry["active"] is False
        # geometry 分支保留不动：旧索引里的几何值逐个带出
        assert entry["geometry"] == _stored_entry("p1")["geometry"]
        # 旧字段也一字未改
        assert entry["line_end"] == "lf" and entry["lineEnd"] == "lf"
        assert entry["font_size"] == 14 and entry["scroll_top"] == 0
        assert entry["updated_at"] == 1.0
        # 读取是只读动作：磁盘索引不被就地改写
        assert _read_index(scratch_dir)["panes"][0]["geometry"] == \
            _stored_entry("p1")["geometry"]

    def test_旧索引在写路径上被保留并补齐新字段(self, scratch_dir):
        """旧索引 + 一次新保存 ⇒ 老条目仍在（新字段补齐为默认值），不丢草稿。"""
        _write_index(scratch_dir, {
            "version": 1, "clean_exit": False, "updated_at": 1.0,
            "panes": [_stored_entry("p1")],
        })
        _write_pane_file(scratch_dir, "p1", "老正文")

        scratch_service.save_draft(_pane("p2", view_mode="table", order=1, active=True), "新")

        index = _read_index(scratch_dir)
        assert sorted(e["id"] for e in index["panes"]) == ["p1", "p2"]
        old = next(e for e in index["panes"] if e["id"] == "p1")
        assert old["view_mode"] == "edit" and old["order"] == 0 and old["active"] is False
        assert old["geometry"] == _stored_entry("p1")["geometry"]
        new = next(e for e in index["panes"] if e["id"] == "p2")
        assert (new["view_mode"], new["order"], new["active"]) == ("table", 1, True)

    def test_旧条目无geometry键_不抛异常且填中性值(self, scratch_dir):
        """④ 旧草稿条目**没有** `geometry` 键 ⇒ 不抛异常，几何填中性默认值。"""
        legacy = _stored_entry("p1")
        del legacy["geometry"]
        _write_index(scratch_dir, {
            "version": 1, "clean_exit": True, "updated_at": 1.0, "panes": [legacy],
        })
        _write_pane_file(scratch_dir, "p1", "更老的正文")

        entry = scratch_service.list_drafts()["entries"][0]
        assert entry["geometry"] == NEUTRAL_GEOMETRY
        assert entry["view_mode"] == "edit"
        assert entry["order"] == 0 and entry["active"] is False

    def test_旧条目geometry非字典_不抛异常且填中性值(self, scratch_dir):
        """`geometry` 类型不可信（字符串 / 列表 / null）同样不抛异常。"""
        for bad in ("big", [], None, 7):
            _write_index(scratch_dir, {
                "version": 1, "clean_exit": True, "updated_at": 1.0,
                "panes": [_stored_entry("p1", geometry=bad)],
            })
            _write_pane_file(scratch_dir, "p1", "正文")
            assert scratch_service.list_drafts()["entries"][0]["geometry"] == \
                NEUTRAL_GEOMETRY

    def test_新字段类型不可信的旧索引_一并不抛异常(self, scratch_dir):
        """磁盘上已被写坏的 `view_mode` / `order` / `active` ⇒ 归一为默认值。"""
        _write_index(scratch_dir, {
            "version": 1, "clean_exit": True, "updated_at": 1.0,
            "panes": [_stored_entry("p1", view_mode="bogus", order="九", active="真")],
        })
        _write_pane_file(scratch_dir, "p1", "正文")

        entry = scratch_service.list_drafts()["entries"][0]
        assert entry["view_mode"] == "edit"
        assert entry["order"] == 0
        assert entry["active"] is False

    def test_索引损坏按目录重建_新字段回默认值(self, scratch_dir, caplog):
        """重建路径（S-7）也走同一归一：新字段取默认值，且不引入 `geometry` 异常。"""
        _write_pane_file(scratch_dir, "p1", "孤儿正文")
        scratch_dir.mkdir(parents=True, exist_ok=True)
        (scratch_dir / "index.json").write_text('{"version": 1, "panes": [',
                                                encoding="utf-8")

        result = scratch_service.list_drafts()
        assert [e["id"] for e in result["entries"]] == ["p1"]
        entry = result["entries"][0]
        assert entry["view_mode"] == "edit"
        assert entry["order"] == 0 and entry["active"] is False
        assert entry["geometry"] == NEUTRAL_GEOMETRY


# ── 4. U-6：读取路径也是白名单（结论即用例） ─────────────────────────

class TestReadPathIsWhitelist:
    """
    U-6 判据：`_normalize_stored_meta`（`:404`）→ `_normalize_pane_meta`（`:254`）。

    读取路径**是**白名单（同写入侧），但读写共用同一函数 ⇒ 新字段由
    `_normalize_pane_meta` 一处扩展即两侧生效（本类不经过 `save_draft`，
    直接读手工造的 `index.json`，以隔离"写入侧顺带写入"的干扰）。
    """

    def test_手工造新字段索引_读取路径带出三字段(self, scratch_dir):
        """不经 `save_draft`，手工造带三字段的索引 ⇒ `list_drafts` 三字段带出。"""
        _write_index(scratch_dir, {
            "version": 1, "clean_exit": False, "updated_at": 9.0,
            "panes": [
                _stored_entry("p1", view_mode="split", order=2, active=False),
                _stored_entry("p2", view_mode="tree", order=0, active=True),
            ],
        })
        _write_pane_file(scratch_dir, "p1", "甲")
        _write_pane_file(scratch_dir, "p2", "乙")

        entries = {e["id"]: e for e in scratch_service.list_drafts()["entries"]}
        assert (entries["p1"]["view_mode"], entries["p1"]["order"],
                entries["p1"]["active"]) == ("split", 2, False)
        assert (entries["p2"]["view_mode"], entries["p2"]["order"],
                entries["p2"]["active"]) == ("tree", 0, True)

    def test_白名单外字段_读取路径同样丢弃(self, scratch_dir):
        """读取路径是白名单的证据：未知键（含 `viewMode` 驼峰）不会出现在返回条目里。"""
        _write_index(scratch_dir, {
            "version": 1, "clean_exit": True, "updated_at": 1.0,
            "panes": [_stored_entry("p1", viewMode="tree", zCounter=9,
                                    secret_note="不该带出")],
        })
        _write_pane_file(scratch_dir, "p1", "甲")

        entry = scratch_service.list_drafts()["entries"][0]
        assert "viewMode" not in entry
        assert "zCounter" not in entry
        assert "secret_note" not in entry
        assert entry["view_mode"] == "edit"  # 驼峰键不被当作 snake_case 字段

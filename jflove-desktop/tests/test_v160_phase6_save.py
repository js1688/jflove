"""
v1.6.0 Phase 6 · 保存 / 另存 / 只读失败语义（AC-36 / AC-37 / AC-40 / AC-41）

依据：
  * 需求 `文档记录/需求文档/v1.6.0.md` §9 的 AC-36（草稿形态首存必须询问路径，取消 ⇒ 不产生
    文件且窗格仍脏）、AC-37（已有路径保存不询问；保存后目标目录**文件集合与名集不变**）、
    AC-40（自动保存只对已有路径生效）、AC-41（失败**非静默**、内容与脏标记不变、原文件
    sha256 不变、④ 无残留）。
  * 设计 `文档记录/设计文档/v1.6.0.md` §6.2.1（原子写）/ §6.2.2（只读兜底）/ §7.1 #1、#3、#6。
  * 审查报告 `文档记录/代码审查报告/v1.6.0.md` 的 M-4（只读目标残留临时文件；**登记未修**，
    BACKLOG BL-21）⇒ 对应用例用 ``xfail(strict=True)`` 留证，修好后会 XPASS 并提醒取消标记。

口径说明（为什么这样落地）：
  * "询问路径"是**渲染层的决策**（UI store 的用例面见 `ui/src/stores/__tests__/`），
    桥层的可判定部分是：`editor.openDialog(mode="save")` 只**返回路径**、自己**不落盘**；
    取消是正常返回 `{cancelled: true, path: null}`，不是错误。
  * 因此本模块用**真 handler + 打桩对话框接缝**（`editor._save_file_name` 是模块属性，
    不触碰 PySide6 类型，避免 BL-16/L-1 的析构期崩溃）来锁住 AC-36/AC-41 的可判定半边。

安全口径：全程只碰 ``tmp_path``；草稿用例把 ``settings.SCRATCH_DIR`` 指向 ``tmp_path``，
**绝不写真实 USER_DATA_DIR**（同 `tests/test_bridge_editor_v1_6_0.py` 的口径）。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from src.bridge import editor as editor_bridge
from src.config import settings
from src.services import editor_service


# ── 工具 ────────────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    """按块计算文件 sha256（大文件也能用）"""
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entries(directory: Path) -> set[str]:
    """目录条目名集合（AC-37 的"名集不变"判据）"""
    return {item.name for item in directory.iterdir()}


def _write_handler(params: dict) -> dict:
    """取 `editor.writeText` 的真 handler（避免测试里散落字符串）"""
    return editor_bridge.HANDLERS["editor"]["writeText"](params)


def _draft_handler(params: dict) -> dict:
    return editor_bridge.HANDLERS["editor"]["draftSave"](params)


def _pane(pane_id: str, path: str | None, dirty: bool = True) -> dict:
    """构造 §7.1 #6 要求的 pane 结构（字段名逐字对齐设计表）"""
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


# ── AC-37：已有路径保存（不询问、只写原路径、无副本） ────────────────

class TestSaveInPlace:
    """AC-37：已有路径保存不询问；目标目录文件集合与名集不变。"""

    def test_保存后只写原路径_目录条目集合不变(self, tmp_path: Path):
        """保存已有路径 ⇒ 目录里既不出现副本也不出现临时文件残留"""
        target = tmp_path / "a.txt"
        target.write_text("old\n", encoding="utf-8")
        before = _entries(tmp_path)

        result = _write_handler(
            {
                "path": str(target),
                "text": "new\n",
                "encoding": "utf-8",
                "lineEnding": "lf",
            }
        )

        assert result["ok"] is True
        assert target.read_text(encoding="utf-8") == "new\n"
        assert _entries(tmp_path) == before, "保存不得新增/删除目录条目（无副本、无残留）"
        # 返回的 sha256 必须与实际落盘字节一致（不是"声称成功"）
        assert result["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()

    def test_保存按原编码与原换行符写回_CRLF_与_BOM_保持(self, tmp_path: Path):
        """AC-20 的语义前提：写回后仍是「原编码 + 原换行符」（不悄悄改格式）

        口径：BOM 由**编码标识**承载（检测给出 `utf-8-bom`，`encode_text` 按
        `BOM_BY_ENCODING` 补回 `EF BB BF`），**不是**独立的 `bom` 入参 ——
        真桥 handler 的入参表只有 `{path, text, encoding, lineEnding, expectedSha256?}`
        （设计 §7.1 #3，`src/bridge/editor.py:315-328`）。
        """
        target = tmp_path / "b.txt"
        target.write_bytes("line1\r\nline2\r\n".encode("utf-8-sig"))

        result = _write_handler(
            {
                "path": str(target),
                "text": "line1\nline2\n",
                "encoding": "utf-8-bom",
                "lineEnding": "crlf",
            }
        )

        assert result["ok"] is True
        assert target.read_bytes() == "line1\r\nline2\r\n".encode("utf-8-sig")

    def test_检测到写回的往返_未编辑保存字节完全相同(self, tmp_path: Path):
        """AC-20 的最强判据：`detectAndRead` → 原样 `writeText` ⇒ 磁盘字节逐字节不变

        走的是**真桥 handler**（与渲染层同口径）：`encoding` 直接取检测结果回传。
        若哪天有人把 BOM 语义从编码标识挪去另加参数，本用例会在原地报警。
        """
        target = tmp_path / "roundtrip.txt"
        raw = "第一行\r\nsecond\r\n".encode("utf-8-sig")
        target.write_bytes(raw)

        read = editor_bridge.HANDLERS["editor"]["detectAndRead"](
            {"paths": [str(target)]}
        )["items"][0]
        assert read["ok"] is True
        assert read["encoding"] == "utf-8-bom", "BOM 必须体现为编码标识，否则写回会丢 BOM"

        result = _write_handler(
            {
                "path": str(target),
                "text": read["text"],
                "encoding": read["encoding"],
                "lineEnding": read["lineEnding"],
            }
        )

        assert result["ok"] is True
        assert target.read_bytes() == raw, "未编辑的保存不得改动任何字节（含 BOM 与 CRLF）"

    def test_保存只改内容_不改磁盘上的其它文件(self, tmp_path: Path):
        """同目录还有别的文件时必须一个字节都不动"""
        target = tmp_path / "a.txt"
        target.write_text("old\n", encoding="utf-8")
        neighbour = tmp_path / "b.bin"
        neighbour.write_bytes(b"\x00\x01\x02")
        neighbour_sha = _sha256_file(neighbour)

        _write_handler(
            {"path": str(target), "text": "new\n", "encoding": "utf-8", "lineEnding": "lf"}
        )

        assert _sha256_file(neighbour) == neighbour_sha


# ── AC-36 / AC-41：对话框决策与取消（桥层可判定半边） ────────────────

class TestSaveDialogDecision:
    """AC-36：草稿形态首存必须**询问路径**；取消 ⇒ 不产生文件。"""

    def test_保存对话框取消_不落盘且返回取消(self, tmp_path: Path, monkeypatch):
        """`editor.openDialog(mode="save")` 用户取消 ⇒ `{cancelled: true, path: null}` 且磁盘 0 变化"""
        monkeypatch.setattr(
            editor_bridge, "_save_file_name", lambda *a, **k: ("", "")
        )
        target = tmp_path / "draft-form.txt"
        before = _entries(tmp_path)

        result = editor_bridge.HANDLERS["editor"]["openDialog"](
            {"mode": "save", "defaultName": "untitled.txt"}
        )

        assert result == {"cancelled": True, "path": None}
        assert not target.exists()
        assert _entries(tmp_path) == before, "取消不得产生任何文件"

    def test_保存对话框只返回路径_自己绝不写文件(self, tmp_path: Path, monkeypatch):
        """拿到路径只是**返回值**：落盘由随后的 writeText 负责（职责分离）"""
        chosen = tmp_path / "chosen.txt"
        monkeypatch.setattr(
            editor_bridge, "_save_file_name", lambda *a, **k: (str(chosen), "文本")
        )

        result = editor_bridge.HANDLERS["editor"]["openDialog"](
            {"mode": "save", "defaultName": "untitled.txt"}
        )

        assert result == {"cancelled": False, "path": str(chosen)}
        assert not chosen.exists(), "对话框 handler 不得自己落盘"

    def test_打开对话框取消是正常返回_不是错误(self, monkeypatch):
        """同一支路的既有口径：取消 ⇒ `{cancelled: true, paths: []}`"""
        monkeypatch.setattr(editor_bridge, "_open_file_names", lambda *a, **k: ([], ""))
        result = editor_bridge.HANDLERS["editor"]["openDialog"]({"mode": "open"})
        assert result == {"cancelled": True, "paths": []}

    def test_对话框_mode_非法_抛参数错误(self):
        with pytest.raises(ValueError):
            editor_bridge.HANDLERS["editor"]["openDialog"]({"mode": "delete"})


# ── AC-41：失败非静默、原文件不变、无残留 ───────────────────────────

class TestFailureSemantics:
    """AC-41：只读 / 目录被删 ⇒ 明确失败（不静默），原文件内容与 sha256 不变。"""

    def test_桥层只读窗格保存_明确失败且原文件字节不变(self, tmp_path: Path):
        """窗格只读（`.diff` 扩展名走只读兜底）⇒ `{ok:false, reason:"read_only"}`"""
        target = tmp_path / "patch.diff"
        target.write_text("old\n", encoding="utf-8")
        before = _sha256_file(target)

        result = _write_handler(
            {"path": str(target), "text": "new\n", "encoding": "utf-8", "lineEnding": "lf"}
        )

        assert result["ok"] is False
        assert result["reason"] == "read_only"
        assert _sha256_file(target) == before
        assert _entries(tmp_path) == {"patch.diff"}

    def test_大文件经桥层保存_被服务层大小规则拒写(self, tmp_path: Path):
        """桥层**不传**只读标记时，第二层兜底仍成立（只读保护不依赖渲染层自觉）

        ⚠ 实测发现（Phase 6 观察项）：`editor.writeText` 的入参表（设计 §7.1 #3）里没有
        `readOnly`，因此 `editor_service.write_text(..., read_only=…)` 这个形参在**生产代码里
        没有任何调用方**（`editor_service.py:250` 只有定义与「桥层把 pane.readOnly 传进来」的
        docstring 自述）。只读窗格实际由服务层自带规则兜底：`.diff`/`.patch` 扩展名、
        以及 > `TEXT_READONLY_BYTES`。本用例用 5 MB 以上的 `.log` 验证「仅靠服务层也能挡住」，
        避免渲染层一旦漏拦就直落磁盘。**不单列缺陷**：两类只读来源都已被规则覆盖。
        """
        big = tmp_path / "big.log"
        big.write_bytes(b"a" * (settings.TEXT_READONLY_BYTES + 1))
        before = _sha256_file(big)

        result = _write_handler(
            {"path": str(big), "text": "new\n", "encoding": "utf-8", "lineEnding": "lf"}
        )

        assert result["ok"] is False
        assert result["reason"] == "read_only"
        assert _sha256_file(big) == before
        assert _entries(tmp_path) == {"big.log"}

    def test_目标目录被删_非静默失败且文案不含完整路径(self, tmp_path: Path):
        """AC-41 + 安全宪法 §9.13：失败要抛（不静默），但错误文案不含完整目录路径"""
        sub = tmp_path / "gone"
        sub.mkdir()
        target = sub / "a.txt"
        target.write_text("old\n", encoding="utf-8")
        os.remove(target)
        sub.rmdir()

        with pytest.raises(editor_service.EditorError) as excinfo:
            editor_service.write_text(str(target), "new\n", "utf-8", "lf")

        assert excinfo.value.reason == "io_error"
        message = str(excinfo.value)
        assert "a.txt" in message, "必要信息（文件名）应保留"
        assert str(sub) not in message, "不得回显完整路径"
        assert "new" not in message, "不得回显正文"

    @pytest.mark.skipif(os.name != "nt", reason="POSIX 上 os.replace 可覆盖只读文件")
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "M-4 未修（BACKLOG BL-21）：只读目标走 atomic_write 时 shutil.copymode 把只读位复制到"
            "临时文件，os.replace 失败后 _silent_unlink 删不掉只读 .tmp ⇒ 残留含新正文的临时文件"
        ),
    )
    def test_只读目标原子写失败后不留临时文件(self, tmp_path: Path):
        """AC-41 ④ 的负向对照：失败后目录里只应剩原文件（当前会残留 ⇒ 留证）"""
        target = tmp_path / "ro.txt"
        target.write_text("old", encoding="utf-8")
        os.chmod(target, 0o444)
        try:
            with pytest.raises(OSError):
                editor_service.atomic_write(str(target), b"NEW CONTENT")
            residue = sorted(name for name in _entries(tmp_path) if name != "ro.txt")
            assert residue == [], f"只读目标失败后残留临时文件：{residue}"
        finally:
            os.chmod(target, 0o666)


# ── AC-36 / AC-40：草稿形态只写草稿库，用户文件系统 0 变化 ───────────

class TestDraftFormWrites:
    """AC-36（草稿形态）+ AC-40（自动保存只对已有路径生效）的服务层半边。"""

    def test_草稿形态暂存_只写草稿目录且用户目录零变化(
        self, tmp_path: Path, monkeypatch
    ):
        """无路径窗格（草稿形态）的暂存只能落 `SCRATCH_DIR`，不许碰用户文件系统"""
        scratch = tmp_path / "scratch"
        monkeypatch.setattr(settings, "SCRATCH_DIR", str(scratch))
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        user_file = user_dir / "keep.txt"
        user_file.write_text("keep\n", encoding="utf-8")

        before_user = _entries(user_dir)
        before_sha = _sha256_file(user_file)

        result = _draft_handler({"pane": _pane("p-draft", None), "content": "hello\n"})

        assert result["ok"] is True
        # 用户文件系统一个条目都不许变
        assert _entries(user_dir) == before_user
        assert _sha256_file(user_file) == before_sha
        # 草稿库必须真的落了内容（不是"什么都没干"）
        assert scratch.exists()
        assert (scratch / "index.json").exists()
        assert (scratch / "pane-p-draft.txt").read_text(encoding="utf-8") == "hello\n"

    def test_内容不变重复暂存_内容文件字节不变(self, tmp_path: Path, monkeypatch):
        """AC-54 的服务层半边：同内容重复暂存后，正文文件字节逐字节不变

        （"内容不变就**不重发**"是渲染层的判定，见 `ui/src/stores/__tests__/` 的
        自动保存用例；此处锁的是"即使重发，落盘内容也必须一致"。）
        """
        scratch = tmp_path / "scratch"
        monkeypatch.setattr(settings, "SCRATCH_DIR", str(scratch))
        pane = _pane("p-1", None)

        _draft_handler({"pane": pane, "content": "same\n"})
        content_path = scratch / "pane-p-1.txt"
        first = content_path.read_bytes()
        _draft_handler({"pane": pane, "content": "same\n"})

        assert content_path.read_bytes() == first

    def test_草稿正文不是字符串_抛参数错误(self, tmp_path: Path, monkeypatch):
        """参数非法必须抛（不静默吞掉），且不产生草稿产物"""
        scratch = tmp_path / "scratch"
        monkeypatch.setattr(settings, "SCRATCH_DIR", str(scratch))

        with pytest.raises(ValueError):
            _draft_handler({"pane": _pane("p-2", None), "content": 123})

        assert not (scratch / "pane-p-2.txt").exists()

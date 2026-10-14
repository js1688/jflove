"""
editor_service 单元测试（v1.6.0 文本编辑器 · §6.2 / AC-20 / AC-38 ~ AC-41 / AC-S1）

覆盖点：
  - 编码 + 换行**原样回写**：UTF-8 BOM + CRLF 往返字节相同（AC-20 / AC-38）
  - GBK / UTF-16 BOM 往返；lf 文档写回不引入 \\r
  - ``atomic_write``：同目录随机临时名、成功后无残留；**中途失败不破坏原文件**
  - Windows ``os.replace`` 短重试（有界、耗尽后抛错）
  - 只读窗格**拒绝写入**（只读标记 / .diff / > 5 MB 既有文件，§6.2.2）
  - 多路径 ``detect_and_read`` 单条失败不影响其它路径，参数非法抛错（AC-48）
  - ``stat_path`` 不存在返回 exists=False（不抛）；``read_text_only`` 恒只读
  - ``expected_sha256`` 乐观并发前置校验
"""

from __future__ import annotations

import hashlib
import os
import stat as stat_mod

import pytest

from src.config.settings import TEXT_READONLY_BYTES
from src.services import editor_service
from src.services import text_detect_service as tds

_UTF8_BOM_CRLF = "\ufeff".encode("utf-8") + "第一行\r\n第二行\r\n".encode("utf-8")
_GBK_BODY = "GBK 正文：中文内容原样回写。\n第二行。\n"


def _bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _list_dir(path) -> list[str]:
    return sorted(os.listdir(path))


# ── 读取链路（§6.2.3） ──────────────────────────────────────────────

class TestReadLocalText:

    def test_read_utf8_bom_crlf(self, tmp_path):
        """读取 UTF-8 BOM + CRLF 文件：编码/换行判定正确，正文不含 BOM。"""
        path = tmp_path / "bom_crlf.txt"
        path.write_bytes(_UTF8_BOM_CRLF)
        result = editor_service.read_local_text(str(path))
        assert result.ok is True
        assert result.encoding == "utf-8-bom"
        assert result.line_end == "crlf"
        assert result.text == "第一行\r\n第二行\r\n"
        assert result.truncated is False

    def test_read_missing_file_returns_reason_without_raising(self, tmp_path):
        """不存在的路径 ⇒ ok=False + reason='not_found'（不抛，便于逐条汇总）。"""
        result = editor_service.read_local_text(str(tmp_path / "nope.txt"))
        assert result.ok is False
        assert result.reason == tds.REASON_NOT_FOUND
        assert result.text == ""

    def test_read_binary_returns_reason_without_raising(self, tmp_path):
        path = tmp_path / "blob.bin"
        path.write_bytes(b"\x00\x01\x02" * 64)
        result = editor_service.read_local_text(str(path))
        assert result.ok is False
        assert result.reason == tds.REASON_BINARY
        assert result.to_read_item()["ok"] is False

    def test_read_text_only_is_always_read_only(self, tmp_path):
        """diff 第二段（§7.1 #4）：读取结果恒为只读并带提示。"""
        path = tmp_path / "a.txt"
        path.write_bytes("hello\n".encode("utf-8"))
        result = editor_service.read_text_only(str(path))
        assert result.ok is True
        assert result.read_only is True
        assert result.warnings


class TestDetectAndRead:
    """多路径独立处理（§7.1 #2 / AC-48）。"""

    def test_single_failure_does_not_affect_others(self, tmp_path):
        good = tmp_path / "good.txt"
        good.write_bytes("ok\n".encode("utf-8"))
        binary = tmp_path / "bad.bin"
        # 刻意不用 b"\x00\x01" 重复序列：那会被 B3 判为"ASCII 为主的 UTF-16"（设计内行为），
        # 本用例要的是**确定**走 B4 的二进制样本
        binary.write_bytes(b"\x00\x01\x02" * 128)
        missing = tmp_path / "missing.txt"

        payload = editor_service.detect_and_read([str(good), str(binary), str(missing)])
        items = payload["items"]
        assert [item["path"] for item in items] == [str(good), str(binary), str(missing)]
        assert items[0]["ok"] is True and items[0]["text"] == "ok\n"
        assert items[0]["lineEnding"] == "lf" and items[0]["readOnly"] is False
        assert items[1]["ok"] is False and items[1]["reason"] == tds.REASON_BINARY
        assert items[2]["ok"] is False and items[2]["reason"] == tds.REASON_NOT_FOUND

    def test_invalid_arguments_raise(self, tmp_path):
        """参数非法（非数组 / 超长 / 元素非字符串）⇒ 抛错（§7.1 #2）。"""
        with pytest.raises(ValueError):
            editor_service.detect_and_read("not-a-list")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            editor_service.detect_and_read([str(tmp_path / f"{i}.txt") for i in range(65)])
        with pytest.raises(ValueError):
            editor_service.detect_and_read([123])  # type: ignore[list-item]
        with pytest.raises(ValueError):
            editor_service.detect_and_read([""])

    def test_stat_path(self, tmp_path):
        """§7.1 #5：元信息查询；不存在不抛。"""
        path = tmp_path / "f.txt"
        path.write_bytes(b"12345")
        info = editor_service.stat_path(str(path))
        assert info["exists"] is True
        assert info["size"] == 5
        assert info["isDir"] is False
        assert info["mtime"] > 0

        dir_info = editor_service.stat_path(str(tmp_path))
        assert dir_info["exists"] is True and dir_info["isDir"] is True

        missing = editor_service.stat_path(str(tmp_path / "nope"))
        assert missing == {"exists": False, "size": 0, "mtime": 0.0, "isDir": False}


# ── 原样回写（AC-20 / AC-38） ───────────────────────────────────────

class TestRoundTripPreserve:

    def test_utf8_bom_crlf_round_trip_is_byte_identical(self, tmp_path):
        """读取 → 原样保存 ⇒ 字节完全相同（AC-20 / AC-38 的核心判据）。"""
        path = tmp_path / "bom_crlf.txt"
        path.write_bytes(_UTF8_BOM_CRLF)
        result = editor_service.read_local_text(str(path))
        assert result.ok is True

        written = editor_service.write_text(
            str(path),
            result.text,
            result.encoding,
            result.line_end,
            expected_sha256=_bytes_sha256(_UTF8_BOM_CRLF),
        )
        assert written["ok"] is True
        assert written["sha256"] == _bytes_sha256(_UTF8_BOM_CRLF)
        assert path.read_bytes() == _UTF8_BOM_CRLF

    def test_gbk_round_trip_is_byte_identical(self, tmp_path):
        """GBK 文件按 gb18030 原编码回写。"""
        raw = _GBK_BODY.encode("gb18030")
        path = tmp_path / "gbk.txt"
        path.write_bytes(raw)
        result = editor_service.read_local_text(str(path))
        assert result.ok is True and result.encoding == "gbk"

        editor_service.write_text(str(path), result.text, result.encoding, result.line_end)
        assert path.read_bytes() == raw

    def test_utf16_bom_round_trip(self, tmp_path):
        """UTF-16LE（带 BOM）读取 → 回写补回 BOM。"""
        raw = b"\xff\xfe" + "中文 UTF-16 往返\n".encode("utf-16-le")
        path = tmp_path / "u16.txt"
        path.write_bytes(raw)
        result = editor_service.read_local_text(str(path))
        assert result.ok is True and result.encoding == "utf-16le"
        assert result.text == "中文 UTF-16 往返\n"

        editor_service.write_text(str(path), result.text, result.encoding, result.line_end)
        assert path.read_bytes() == raw

    def test_lf_write_does_not_introduce_cr(self, tmp_path):
        """lf 文档写回不引入 \\r；渲染层传来的 \\r\\n 也被归一。"""
        path = tmp_path / "lf.txt"
        editor_service.write_text(str(path), "a\r\nb\n", "utf-8", "lf")
        assert path.read_bytes() == b"a\nb\n"

    def test_crlf_write_is_idempotent_on_existing_crlf(self, tmp_path):
        """crlf 文档重复保存不产生 \\r\\r\\n（幂等归一）。"""
        path = tmp_path / "crlf.txt"
        editor_service.write_text(str(path), "a\r\nb\r\n", "utf-8", "crlf")
        assert path.read_bytes() == b"a\r\nb\r\n"

        result = editor_service.read_local_text(str(path))
        editor_service.write_text(str(path), result.text, "utf-8", "crlf")
        assert path.read_bytes() == b"a\r\nb\r\n"

    def test_invalid_encoding_or_line_ending_raises(self, tmp_path):
        path = tmp_path / "x.txt"
        with pytest.raises(ValueError):
            editor_service.write_text(str(path), "x", "cp1252", "lf")
        with pytest.raises(ValueError):
            editor_service.write_text(str(path), "x", "utf-8", "cr")
        assert not path.exists()

    def test_expected_sha256_conflict_raises_and_keeps_file(self, tmp_path):
        """乐观并发前置校验：磁盘内容与期望不符 ⇒ 抛 conflict 且不改文件。"""
        path = tmp_path / "conflict.txt"
        path.write_bytes(b"original\n")
        with pytest.raises(editor_service.EditorError) as exc:
            editor_service.write_text(
                str(path), "new\n", "utf-8", "lf", expected_sha256="0" * 64
            )
        assert exc.value.reason == "conflict"
        assert path.read_bytes() == b"original\n"


# ── 只读拒绝（§6.2.2 / AC-19 / AC-52） ──────────────────────────────

class TestReadOnlyRefusal:

    def test_read_only_flag_refused(self, tmp_path):
        path = tmp_path / "ro.txt"
        path.write_bytes(b"keep\n")
        result = editor_service.write_text(
            str(path), "changed\n", "utf-8", "lf", read_only=True
        )
        assert result == {"ok": False, "reason": "read_only", "error": "只读"}
        assert path.read_bytes() == b"keep\n"

    def test_diff_extension_refused(self, tmp_path):
        """'.diff' 只读预览窗格不得回写（§5.3 / §6.2.2）。"""
        path = tmp_path / "change.diff"
        path.write_bytes(b"-old\n+new\n")
        result = editor_service.write_text(str(path), "rewritten\n", "utf-8", "lf")
        assert result["ok"] is False and result["reason"] == "read_only"
        assert path.read_bytes() == b"-old\n+new\n"

    def test_large_existing_file_refused(self, tmp_path):
        """> 5 MB 降级只读的既有文件不得被回写（AC-52 兜底）。"""
        path = tmp_path / "big.txt"
        with open(path, "wb") as fp:
            fp.seek(TEXT_READONLY_BYTES)  # 稀疏写法：不真写 5 MB 数据
            fp.write(b"x")
        assert os.path.getsize(path) == TEXT_READONLY_BYTES + 1

        result = editor_service.write_text(str(path), "tiny\n", "utf-8", "lf")
        assert result["ok"] is False and result["reason"] == "read_only"
        assert os.path.getsize(path) == TEXT_READONLY_BYTES + 1

    def test_save_as_to_new_path_is_allowed(self, tmp_path):
        """另存为到新路径不是只读（目标不存在 ⇒ 可写）。"""
        target = tmp_path / "new.txt"
        written = editor_service.write_text(str(target), "brand new\n", "utf-8", "lf")
        assert written["ok"] is True
        assert target.read_bytes() == b"brand new\n"


# ── 原子写（§6.2.1 / AC-S1 / AC-41） ────────────────────────────────

class TestAtomicWrite:

    def test_write_replaces_content_without_temp_residue(self, tmp_path):
        """成功路径：内容替换、目录里不残留临时文件。"""
        path = tmp_path / "a.txt"
        editor_service.atomic_write(str(path), b"first")
        assert path.read_bytes() == b"first"
        editor_service.atomic_write(str(path), b"second")
        assert path.read_bytes() == b"second"
        assert _list_dir(tmp_path) == ["a.txt"]

    def test_temp_file_is_same_dir_random_name(self, tmp_path, monkeypatch):
        """临时文件与目标**同目录**且名字是 mkstemp 随机名（不是固定 .tmp）。"""
        seen: list[str] = []
        real_replace = editor_service._os_replace

        def _spy(src, dst):
            seen.append(os.path.basename(src))
            real_replace(src, dst)

        monkeypatch.setattr(editor_service, "_os_replace", _spy)
        path = tmp_path / "b.txt"
        editor_service.atomic_write(str(path), b"x")
        assert len(seen) == 1
        name = seen[0]
        assert name.startswith(".b.txt.jflove-") and name.endswith(".tmp")
        assert name != ".b.txt.tmp"

    def test_failure_during_write_keeps_original(self, tmp_path, monkeypatch):
        """写入中途失败（fsync 抛错）：原文件**一字不改**，临时文件被清理。"""
        path = tmp_path / "c.txt"
        path.write_bytes(b"ORIGINAL")

        def _boom(*args, **kwargs):
            raise OSError("模拟磁盘故障")

        monkeypatch.setattr(editor_service.os, "fsync", _boom)
        with pytest.raises(OSError):
            editor_service.atomic_write(str(path), b"NEW")
        assert path.read_bytes() == b"ORIGINAL"
        assert _list_dir(tmp_path) == ["c.txt"]

    def test_failure_during_replace_keeps_original(self, tmp_path, monkeypatch):
        """替换阶段失败：原文件不变、临时文件被清理（AC-41 ④）。"""
        path = tmp_path / "d.txt"
        path.write_bytes(b"ORIGINAL")

        def _boom(src, dst):
            raise PermissionError("模拟目标被占用")

        monkeypatch.setattr(editor_service, "_os_replace", _boom)
        with pytest.raises(PermissionError):
            editor_service.atomic_write(str(path), b"NEW")
        assert path.read_bytes() == b"ORIGINAL"
        assert _list_dir(tmp_path) == ["d.txt"]

    def test_replace_retries_then_succeeds(self, tmp_path, monkeypatch):
        """os.replace 短重试：前两次失败、第三次成功（重试间隔置 0 保持测试快）。"""
        monkeypatch.setattr(editor_service, "_REPLACE_RETRY_DELAY_SEC", 0)
        real_replace = os.replace
        calls: list[int] = []

        def _flaky(src, dst):
            calls.append(1)
            if len(calls) < editor_service._REPLACE_RETRIES:
                raise PermissionError("模拟 Windows 目标被短暂持有")
            real_replace(src, dst)

        monkeypatch.setattr(editor_service, "_os_replace", _flaky)
        path = tmp_path / "e.txt"
        editor_service.atomic_write(str(path), b"retried")
        assert len(calls) == editor_service._REPLACE_RETRIES
        assert path.read_bytes() == b"retried"

    def test_replace_retries_exhausted_raises(self, tmp_path, monkeypatch):
        """重试耗尽后抛错（不静默），且不触碰原文件。"""
        monkeypatch.setattr(editor_service, "_REPLACE_RETRY_DELAY_SEC", 0)
        path = tmp_path / "f.txt"
        path.write_bytes(b"ORIGINAL")
        calls: list[int] = []

        def _always_fail(src, dst):
            calls.append(1)
            raise PermissionError("一直失败")

        monkeypatch.setattr(editor_service, "_os_replace", _always_fail)
        with pytest.raises(PermissionError):
            editor_service.atomic_write(str(path), b"NEW")
        assert len(calls) == editor_service._REPLACE_RETRIES
        assert path.read_bytes() == b"ORIGINAL"
        assert _list_dir(tmp_path) == ["f.txt"]

    def test_preserve_mode_keeps_executable_bit(self, tmp_path):
        """POSIX 权限位保留：可执行位不被抹掉（Windows 上本用例只验证不报错）。"""
        path = tmp_path / "script.sh"
        path.write_bytes(b"#!/bin/sh\necho old\n")
        os.chmod(path, 0o755)
        editor_service.atomic_write(str(path), b"#!/bin/sh\necho new\n")
        assert path.read_bytes() == b"#!/bin/sh\necho new\n"
        if os.name == "posix":
            assert stat_mod.S_IMODE(os.stat(path).st_mode) == 0o755

    def test_write_text_returns_size_mtime_sha256(self, tmp_path):
        """§7.1 #3 返回结构：{ok, size, mtime, sha256}。"""
        path = tmp_path / "g.txt"
        result = editor_service.write_text(str(path), "abc\n", "utf-8", "lf")
        assert result["ok"] is True
        assert result["size"] == 4
        assert result["sha256"] == _bytes_sha256(b"abc\n")
        assert result["mtime"] == pytest.approx(os.stat(path).st_mtime)

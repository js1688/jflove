"""
text_detect_service 单元测试（v1.6.0 文本编辑器 · F8 / AC-49 ~ AC-53 / AC-S3 / AC-S4）

覆盖设计 §5.2 的强制判序与 R-9 实测修正后的嗅探口径：
  - 扩展名白名单命中 ⇒ 跳过嗅探（含"小型二进制改名 .log 以文本打开"的既定取舍）
  - 含 NUL 二进制拒绝；**无 NUL 随机二进制**也必须被拒（R-9：解码优先口径）
  - GBK 中文正文**不被误杀**（AC-50a 回归：已废除「非文本比例 > 0.30」）
  - UTF-16 无 BOM 中文 ⇒ 判二进制（设计已裁决接受 + 提示）
  - UTF-16 BOM / UTF-8 BOM / 换行符 / 空文件 / 不存在 / 目录
  - > 50 MB **在 stat 之后、读任何内容之前**返回 too_large
  - 5 ~ 50 MB 只读但内容**完整不截断**
  - read_text 与 probe 结构分离（probe 未通过 ⇒ ValueError，AC-S3）
  - 类型表清单（manifest）优先 / 缺失回退（§5.1 / DS-8）
"""

from __future__ import annotations

import json
import os
import random

import pytest

from src.config.settings import TEXT_MAX_BYTES, TEXT_READONLY_BYTES
from src.services import editor_service
from src.services import text_detect_service as tds

# ── 测试数据 ────────────────────────────────────────────────────────

_GBK_BODY = "中文正文测试：编码判定不得误杀 GBK 文件。\n第二行中文内容。\n"
_UTF8_BODY = "UTF-8 正文：混合中文与 ASCII 内容。\nsecond line\n"


def _no_nul_random_binary(size: int = 8192) -> bytes:
    """
    生成**不含 NUL** 的伪随机二进制（固定种子，可重复）。

    这是 R-9 的关键样本类别：它既没有 NUL（B4 不触发），又无法通过
    严格解码（utf-8 / gb18030 都失败）⇒ 必须由 ④ 编码判定拒绝。
    """
    rng = random.Random(20261012)
    data = bytearray()
    while len(data) < size:
        data.append(rng.randrange(1, 256))  # 1..255，天然避开 NUL
    return bytes(data)


@pytest.fixture()
def isolated_type_table():
    """测试结束后清空类型表缓存，避免注入的 manifest 泄漏到其它用例。"""
    yield
    tds.reset_type_table_cache()


# ── ① 扩展名白名单（AC-49 / §5.2.4） ────────────────────────────────

class TestExtensionWhitelist:
    """白名单命中即采用对应语言，并**跳过**二进制嗅探。"""

    def test_known_extension_gets_language_without_sniffing(self, tmp_path):
        """普通 .json 文件命中白名单，语言为 json。"""
        path = tmp_path / "conf.json"
        path.write_bytes(b'{"a": 1}\n')
        result = tds.probe(str(path))
        assert result.ok is True
        assert result.language == "json"
        assert result.binary is False
        assert result.encoding == "utf-8"

    def test_whitelist_hit_skips_sniff_on_binary_renamed_log(self, tmp_path):
        """
        §5.2.4 既定取舍（F8.5 残余风险）：白名单命中的小型二进制改名成 .log
        会以文本方式打开 —— 此处锁定该行为，避免被判为"回归"。
        """
        path = tmp_path / "fake.log"
        path.write_bytes(b"\x00\x01\x02\x03" * 64)
        result = tds.probe(str(path))
        assert result.ok is True
        assert result.binary is False
        assert result.language == "text"

    def test_unknown_extension_text_falls_back_to_text(self, tmp_path):
        """未知扩展名 + 文本内容 ⇒ 按 txt 处理（AC-22 / AC-51）。"""
        path = tmp_path / "note.zzz"
        path.write_bytes(_UTF8_BODY.encode("utf-8"))
        result = tds.probe(str(path))
        assert result.ok is True
        assert result.language == "text"
        assert result.encoding == "utf-8"

    def test_diff_and_patch_are_forced_read_only(self, tmp_path):
        """.diff / .patch ⇒ 强制只读预览（§5.3 / §6.2.2，AC-19）。"""
        for name in ("change.diff", "change.patch"):
            path = tmp_path / name
            path.write_bytes(b"--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n")
            result = tds.probe(str(path))
            assert result.ok is True
            assert result.read_only is True, name
            assert result.language == "diff"


# ── ③ 二进制嗅探（§5.2.2，R-9 修正口径） ────────────────────────────

class TestBinarySniff:
    """BOM 检查 + NUL 检查 + 严格解码优先；**不得**使用 0.30 比例阈值。"""

    def test_nul_binary_rejected(self, tmp_path):
        """含 NUL 的二进制被拒（B4），reason='binary'。"""
        path = tmp_path / "blob.bin"
        path.write_bytes(b"PK\x03\x04\x00\x00\x00\x00binary" + os.urandom(256))
        result = tds.probe(str(path))
        assert result.ok is False
        assert result.binary is True
        assert result.reason == tds.REASON_BINARY
        assert result.message != ""
        assert result.size == path.stat().st_size

    def test_no_nul_random_binary_rejected(self, tmp_path):
        """
        无 NUL 的随机二进制同样被拒（AC-50）。

        R-9 结论：原「非文本字节比例 > 0.30」与 GBK 中文区间重合，已废除；
        当前口径为严格解码优先 ⇒ 该样本由 ④ 编码判定以 unknown_encoding 拒绝，
        两条路径都不放行（`ok is False` 是本用例的核心断言）。
        """
        data = _no_nul_random_binary()
        assert b"\x00" not in data
        path = tmp_path / "random.bin"
        path.write_bytes(data)
        result = tds.probe(str(path))
        assert result.ok is False
        assert result.reason == tds.REASON_UNKNOWN_ENCODING
        assert result.binary is False  # 无 NUL ⇒ 不由嗅探直接判二进制

    def test_b3_utf16_pattern_is_never_silently_opened(self, tmp_path):
        """
        B3 的固有假阳性（设计内行为）：``\\x00\\x01`` 重复序列满足"一侧 NUL ≥ 90%"，
        会被判为无 BOM 的 UTF-16 文本 —— 但它**永远不会被静默解码**：
        结果带 ``encoding_uncertain`` 且未显式选编码时 read_text 直接抛错。
        """
        path = tmp_path / "looks_like_utf16.bin"
        path.write_bytes(b"\x00\x01" * 128)
        result = tds.probe(str(path))
        assert result.encoding_uncertain is True
        assert result.encoding == ""
        with pytest.raises(ValueError):
            tds.read_text(str(path), result)

    def test_gbk_chinese_body_not_killed(self, tmp_path):
        """
        AC-50a 回归：GBK 中文正文不得被误杀。

        两条路径都要过：① .txt（白名单命中）+ ② 无扩展名（走嗅探 B5 严格解码优先）。
        """
        data = _GBK_BODY.encode("gb18030")
        assert b"\x00" not in data

        whitelisted = tmp_path / "gbk.txt"
        whitelisted.write_bytes(data)
        result = tds.probe(str(whitelisted))
        assert result.ok is True
        assert result.encoding == "gbk"
        assert result.binary is False
        assert tds.read_text(str(whitelisted), result).text == _GBK_BODY

        sniffed = tmp_path / "gbk_no_ext"
        sniffed.write_bytes(data)
        result2 = tds.probe(str(sniffed))
        assert result2.ok is True, result2.message
        assert result2.binary is False
        assert result2.encoding == "gbk"
        assert tds.read_text(str(sniffed), result2).text == _GBK_BODY

    def test_sniff_bytes_utf8_and_gb18030_strict_are_text(self):
        """B5 单元口径：utf-8 strict / gb18030 strict 含多字节 ⇒ 文本。"""
        assert tds.sniff_bytes(_UTF8_BODY.encode("utf-8")).binary is False
        assert tds.sniff_bytes(_GBK_BODY.encode("gb18030")).binary is False
        assert tds.sniff_bytes(b"").empty is True

    def test_utf16_without_bom_chinese_is_binary_by_design(self, tmp_path):
        """
        既定行为（设计 §5.2.2 R-9 残留项，已裁决接受）：无 BOM 的 UTF-16 中文
        被判**二进制**（B3 的 0.90 门槛对中文从不触发），提示文案含 UTF-16 指引。
        """
        body = "这是一段无 BOM 的 UTF-16 中文内容，用于验证既定裁决行为。\n"
        # 刻意用**非白名单扩展名**：白名单命中会跳过嗅探（§5.2.4），
        # 只有走嗅探路径才能验证 B3/B4 的既定裁决
        path = tmp_path / "utf16le_cn.dat"
        path.write_bytes(body.encode("utf-16-le"))
        # 先确认样本足够长（≥16 字节）以便 B3 真正被评估，而不是直接落到 B4
        assert len(body.encode("utf-16-le")) >= 16
        result = tds.probe(str(path))
        assert result.ok is False
        assert result.binary is True
        assert result.reason == tds.REASON_BINARY
        assert "UTF-16" in result.message

    def test_utf16_ascii_without_bom_rescued_by_b3(self, tmp_path):
        """
        B3 救回分支：ASCII 为主的 UTF-16（一侧 NUL ≥ 90%）判为文本，但
        **不静默猜编码**（§5.2.3 硬约束 3）⇒ 需调用方显式指定编码后才能读。
        """
        body = "HelloWorld" * 6
        path = tmp_path / "utf16le_ascii"
        path.write_bytes(body.encode("utf-16-le"))
        result = tds.probe(str(path))
        assert result.ok is True
        assert result.encoding_uncertain is True
        assert result.encoding == ""
        assert any("UTF-16" in w for w in result.warnings)

        # 未指定编码 ⇒ 拒绝读取（不得静默按 utf-8 解码）
        with pytest.raises(ValueError):
            tds.read_text(str(path), result)
        # 用户显式选择后正常读取
        assert tds.read_text(str(path), result, encoding="utf-16le").text == body


# ── ④ 编码判定（§5.2.3） ────────────────────────────────────────────

class TestEncodingDetection:
    """BOM 优先 + 严格解码；不可判定必须拒绝（AC-S4 ④）。"""

    def test_utf8_bom_detected_and_stripped(self, tmp_path):
        """utf-8-bom ⇒ 编码 'utf-8-bom'，正文不含 BOM 字符（写回时再补）。"""
        path = tmp_path / "bom.txt"
        path.write_bytes(b"\xef\xbb\xbf" + _UTF8_BODY.encode("utf-8"))
        result = tds.probe(str(path))
        assert result.ok is True
        assert result.encoding == "utf-8-bom"
        assert result.bom is True
        read = tds.read_text(str(path), result)
        assert read.ok is True
        assert read.text == _UTF8_BODY
        assert not read.text.startswith("\ufeff")

    @pytest.mark.parametrize(
        "marker,encoding",
        [(b"\xff\xfe", "utf-16le"), (b"\xfe\xff", "utf-16be")],
    )
    def test_utf16_bom_detected(self, tmp_path, marker, encoding):
        """FF FE / FE FF ⇒ utf-16le / utf-16be，读取时剥离 BOM。"""
        codec = "utf-16-le" if encoding == "utf-16le" else "utf-16-be"
        path = tmp_path / f"{encoding}.txt"
        path.write_bytes(marker + "中文 BOM 测试\n".encode(codec))
        result = tds.probe(str(path))
        assert result.ok is True
        assert result.encoding == encoding
        assert tds.read_text(str(path), result).text == "中文 BOM 测试\n"

    def test_unrecognizable_bytes_rejected_no_silent_replace(self, tmp_path):
        """0x80 / 0xFF 既非合法 UTF-8 也非合法 GB18030 ⇒ unknown_encoding 拒绝。"""
        path = tmp_path / "weird.dat"
        path.write_bytes(b"header\x80\xff\x80\xff tail")
        result = tds.probe(str(path))
        assert result.ok is False
        assert result.reason == tds.REASON_UNKNOWN_ENCODING
        assert result.encoding == ""

    def test_line_end_detection(self, tmp_path):
        """换行符判定：\\r\\n 更多 ⇒ crlf，否则 lf（§5.2.3 硬约束 4）。"""
        crlf = tmp_path / "dos.txt"
        crlf.write_bytes(b"a\r\nb\r\nc\r\n")
        assert tds.probe(str(crlf)).line_end == "crlf"

        lf = tmp_path / "unix.txt"
        lf.write_bytes(b"a\nb\nc\n")
        assert tds.probe(str(lf)).line_end == "lf"

    def test_detect_encoding_unit(self):
        """编码判定单元口径（可直接调用的纯函数）。"""
        assert tds.detect_encoding(b"\xef\xbb\xbfabc") == "utf-8-bom"
        assert tds.detect_encoding(b"\xff\xfe" + "a".encode("utf-16-le")) == "utf-16le"
        assert tds.detect_encoding(b"\xfe\xff" + "a".encode("utf-16-be")) == "utf-16be"
        assert tds.detect_encoding(_UTF8_BODY.encode("utf-8")) == "utf-8"
        assert tds.detect_encoding(_GBK_BODY.encode("gb18030")) == "gbk"
        assert tds.detect_encoding(b"\x80\xff") is None


# ── ①' UTF-16 换行符判定（S-4 回归：判定不得按原始字节数换行） ───────

_UTF16_VARIANTS = [("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")]


class TestUtf16LineEnd:
    """UTF-16 家族的 CRLF 在字节流里是 ``0d 00 0a 00`` —— 必须先解码再数换行（S-4）。"""

    @pytest.mark.parametrize("encoding,prefix", _UTF16_VARIANTS)
    def test_utf16_crlf_detected_as_crlf(self, tmp_path, encoding, prefix):
        path = tmp_path / "dos16.txt"
        path.write_bytes(prefix + "第一行\r\n第二行\r\n第三行".encode(encoding))

        result = tds.probe(str(path))
        assert result.ok is True
        assert result.line_end == "crlf"

    @pytest.mark.parametrize("encoding,prefix", _UTF16_VARIANTS)
    def test_utf16_lf_stays_lf(self, tmp_path, encoding, prefix):
        path = tmp_path / "unix16.txt"
        path.write_bytes(prefix + "第一行\n第二行\n第三行".encode(encoding))

        result = tds.probe(str(path))
        assert result.ok is True
        assert result.line_end == "lf"

    def test_detect_line_end_unit(self):
        """纯函数口径：同一份字节，给定编码与否会得出不同结论（字节口径是历史行为）。"""
        raw = "a\r\nb\r\nc".encode("utf-16-le")
        assert tds._detect_line_end(raw) == "lf"
        assert tds._detect_line_end(raw, "utf-16le") == "crlf"
        assert tds._detect_line_end(raw, "le") == "crlf"
        assert tds._detect_line_end("a\r\nb".encode("utf-16-be"), "be") == "crlf"
        # 单字节编码继续走字节口径（utf-8 / gbk 的统计等价）
        assert tds._detect_line_end(b"a\r\nb\r\nc", "utf-8") == "crlf"
        assert tds._detect_line_end(b"a\nb\nc", "utf-8") == "lf"
        assert tds._detect_line_end(b"a\r\nb\nc\nd", "gbk") == "lf"

    @pytest.mark.parametrize("encoding,prefix", _UTF16_VARIANTS)
    def test_utf16_roundtrip_bytes_identical(self, tmp_path, encoding, prefix):
        """端到端判据（AC-20 / AC-S4③）：probe → read_text → encode_text 逐字节回到原文。"""
        path = tmp_path / "roundtrip16.txt"
        original = prefix + "Alpha\r\nBeta\r\nGamma\r\n".encode(encoding)
        path.write_bytes(original)

        probe = tds.probe(str(path))
        read = tds.read_text(str(path), probe)
        rebuilt = editor_service.encode_text(
            read.text, probe.encoding, probe.line_end, bom=probe.bom,
        )
        assert read.text == "Alpha\r\nBeta\r\nGamma\r\n"
        assert rebuilt == original

    def test_utf16_without_bom_line_end_uses_detected_byte_order(self, tmp_path):
        """B3 救回的「无 BOM UTF-16」：编码仍不猜，但换行符按嗅探出的字节序判定。

        注意扩展名要**不在白名单**内：白名单命中会按 AC-49 跳过嗅探 ⇒ 走不到 B3。
        """
        path = tmp_path / "nobom16.xyz"
        body = "alpha line\r\nbeta line\r\ngamma line\r\n"
        path.write_bytes(body.encode("utf-16-le"))  # 无 BOM，ASCII 为主 ⇒ B3 命中

        result = tds.probe(str(path))
        assert result.ok is True
        assert result.encoding_uncertain is True
        assert result.line_end == "crlf"


# ── ② 大小阈值（AC-52 / AC-53） ─────────────────────────────────────

class TestSizeThresholds:
    """> 50 MB 在读任何内容之前拒绝；5 ~ 50 MB 只读但完整。"""

    def test_too_large_rejected_after_stat_before_any_read(self, tmp_path, monkeypatch):
        """AC-53 / AC-S3：stat 之后立即返回，**不打开文件**（open 被替换成硬失败）。"""
        big = tmp_path / "huge.txt"
        with open(big, "wb") as fp:
            fp.seek(TEXT_MAX_BYTES)  # 稀疏写法：文件大小 = 50 MB + 1，不真写 50 MB 数据
            fp.write(b"x")
        assert os.path.getsize(big) == TEXT_MAX_BYTES + 1

        def _boom(*args, **kwargs):  # pragma: no cover - 被调用即测试失败
            raise AssertionError("probe 在读任何内容之前就应返回 too_large")

        monkeypatch.setattr("builtins.open", _boom)
        result = tds.probe(str(big))
        assert result.ok is False
        assert result.reason == tds.REASON_TOO_LARGE
        assert result.size == TEXT_MAX_BYTES + 1
        assert result.sample == b""  # 没有任何内容被读入

    def test_readonly_window_keeps_full_content(self, tmp_path):
        """AC-52：5 ~ 50 MB 降级只读，内容**完整不截断**。"""
        body = "abcdefgh\n"
        repeat = (TEXT_READONLY_BYTES // len(body)) + 2
        path = tmp_path / "big.txt"
        path.write_bytes(body.encode("utf-8") * repeat)
        result = tds.probe(str(path))
        assert result.ok is True
        assert result.read_only is True
        assert any("只读" in w for w in result.warnings)

        read = tds.read_text(str(path), result)
        assert read.ok is True
        assert read.read_only is True
        assert read.truncated is False
        assert len(read.text) == len(body) * repeat
        assert read.text.startswith(body) and read.text.endswith(body)


# ── 空文件 / 异常路径 / 结构隔离 ────────────────────────────────────

class TestEdgeCasesAndIsolation:

    def test_empty_file_opens_as_empty_text_pane(self, tmp_path):
        """B1：空文件不是二进制 ⇒ 按 txt 打开空窗格。"""
        path = tmp_path / "empty.txt"
        path.write_bytes(b"")
        result = tds.probe(str(path))
        assert result.ok is True
        assert result.empty is True
        assert result.binary is False
        assert result.language == "text"
        assert tds.read_text(str(path), result).text == ""

    def test_missing_file_and_directory(self, tmp_path):
        """不存在的路径 ⇒ not_found；目录 ⇒ io_error（均不抛）。"""
        missing = tds.probe(str(tmp_path / "nope.txt"))
        assert missing.ok is False
        assert missing.reason == tds.REASON_NOT_FOUND

        as_dir = tds.probe(str(tmp_path))
        assert as_dir.ok is False
        assert as_dir.reason == tds.REASON_IO_ERROR

    def test_public_dict_has_no_sample_payload(self, tmp_path):
        """probe 对外结构只含判定字段，不含正文（§5.2.1「只做判定」）。"""
        path = tmp_path / "a.txt"
        path.write_bytes(_UTF8_BODY.encode("utf-8"))
        public = tds.probe(str(path)).public_dict()
        assert "sample" not in public
        assert "text" not in public
        assert public["lineEnding"] == "lf"

    def test_read_text_requires_passed_probe(self, tmp_path):
        """
        AC-S3 结构隔离：probe 未通过时 read_text 直接抛 ValueError。

        这正是"不可能先读后判"的结构保证（桥只暴露 editor.detectAndRead，
        read_text 单独不可触达）。
        """
        path = tmp_path / "blob.bin"
        path.write_bytes(b"\x00\x01\x02" * 32)
        failed = tds.probe(str(path))
        assert failed.ok is False
        with pytest.raises(ValueError):
            tds.read_text(str(path), failed)
        with pytest.raises(TypeError):
            tds.read_text(str(path), "not-a-probe")  # type: ignore[arg-type]

    def test_strict_decode_failure_returns_error_without_replacement(self, tmp_path):
        """严格解码失败时返回 ok=False（禁止 errors='replace' 降级后仍允许保存）。"""
        path = tmp_path / "swap.txt"
        path.write_bytes(_UTF8_BODY.encode("utf-8"))
        result = tds.probe(str(path))
        assert result.ok is True
        # 模拟探测之后文件被外部替换成非法 UTF-8 字节（按 utf-8 严格解码必失败）
        path.write_bytes(b"\x80\xff" * 100)
        swapped = tds.read_text(str(path), result)
        assert swapped.ok is False
        assert swapped.reason == tds.REASON_UNKNOWN_ENCODING
        assert swapped.text == ""


# ── 类型表清单（§5.1 / DS-8） ───────────────────────────────────────

class TestTypeTableManifest:
    """Python 侧只读构建期派生的 manifest；缺失时回退内置表。"""

    def test_manifest_takes_precedence_over_fallback(self, tmp_path, monkeypatch,
                                                     isolated_type_table):
        manifest = tmp_path / "file-assoc.manifest.json"
        manifest.write_text(
            json.dumps({
                "version": 1,
                "generatedFrom": "config/scratch-types.ts",
                "types": [{"ext": "zzz", "label": "Z", "language": "json", "mime": []}],
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(tds, "MANIFEST_PATH", str(manifest))
        tds.reset_type_table_cache()
        assert tds.ext_language_map() == {"zzz": "json"}

        # 白名单确实来自 manifest：含 NUL 的 .zzz 也跳过嗅探（语言 json）
        binary_like = tmp_path / "x.zzz"
        binary_like.write_bytes(b"\x00\x01\x02" * 32)
        result = tds.probe(str(binary_like))
        assert result.ok is True
        assert result.language == "json"

    def test_missing_or_broken_manifest_falls_back(self, tmp_path, monkeypatch,
                                                   isolated_type_table):
        monkeypatch.setattr(tds, "MANIFEST_PATH", str(tmp_path / "nope.json"))
        tds.reset_type_table_cache()
        assert tds.ext_language_map()["txt"] == "text"

        broken = tmp_path / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(tds, "MANIFEST_PATH", str(broken))
        tds.reset_type_table_cache()
        assert tds.ext_language_map()["json"] == "json"

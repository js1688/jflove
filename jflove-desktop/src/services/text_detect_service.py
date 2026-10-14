"""
文本类型校验服务模块（v1.6.0 文本编辑器 Python 服务层 · 第 1 模块）

职责（设计 §5.2 / AC-49 ~ AC-53 / AC-S3 / AC-S4）：
  - ``probe(path)``：**只做判定，不加载内容**，返回 ProbeResult
  - ``read_text(path, probe)``：**只有 probe 通过才被调用**，按判定结果严格解码

强制判序（设计 §5.2.1，先于内容加载）：
  ① 扩展名白名单命中 → 采用对应 language，**跳过**二进制嗅探
  ② ``stat`` 取 size：> 50 MB **在读任何内容之前**直接拒绝（reason='too_large'）；
     > 5 MB 降级只读（read_only=True，内容仍**完整不截断**）
  ③ **一次有界读** TEXT_SAMPLE_BYTES(64 KB)：前 TEXT_SNIFF_BYTES(8 KB) 做二进制嗅探；
     判为二进制 ⇒ 拒绝（reason='binary'）
  ④ 编码判定用整个 64 KB 样本；不可判定 ⇒ 拒绝（reason='unknown_encoding'），
     **禁止静默替换**（AC-S4 ④）

结构隔离（AC-S3 / DS-2）：``probe()`` 与 ``read_text()`` 是**两个独立函数**，
``read_text`` 不读白名单、不做嗅探，且 probe 未通过时**直接抛错** ⇒
结构上不可能出现"先读后判"（桥只暴露 ``editor.detectAndRead``）。

二进制嗅探口径 = 设计 §5.2.2 **R-9 实测修正后**的版本：
  BOM 检查 + NUL 检查 + **严格解码优先**（utf-8 strict → gb18030 strict 且含多字节）。
  **严禁**使用已被推翻的「非文本字节比例 > 0.30」判据（GBK 中文实测 0.5443~0.5830
  与无 NUL 随机二进制 0.5438 区间重合，该阈值没有区分度）。

编码原样回写所需的编解码映射（§5.2.3 硬约束 5）在本模块定义为**唯一取值来源**，
``editor_service`` 直接复用，避免两处各写一份。
"""

import hashlib
import json
import os
import stat as stat_mod
from dataclasses import dataclass, field
from typing import Any

from src.config.settings import (
    BASE_DIR,
    TEXT_MAX_BYTES,
    TEXT_READONLY_BYTES,
    TEXT_SAMPLE_BYTES,
    TEXT_SNIFF_BYTES,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 分块读取粒度（§5.2.1 ⑤：≤ 4 MB/块） ─────────────────────────────
READ_CHUNK_BYTES = 4 * 1024 * 1024

# ── 拒绝原因（取值与设计 §7.1 #2 的 ReadItem.reason 逐条对应） ────────
REASON_TOO_LARGE = "too_large"
REASON_BINARY = "binary"
REASON_UNKNOWN_ENCODING = "unknown_encoding"
REASON_NOT_FOUND = "not_found"
REASON_IO_ERROR = "io_error"

# ── 编码标识（§5.2.3：utf-8 / utf-8-bom / utf-16le / utf-16be / gbk） ──
ENC_UTF8 = "utf-8"
ENC_UTF8_BOM = "utf-8-bom"
ENC_UTF16_LE = "utf-16le"
ENC_UTF16_BE = "utf-16be"
ENC_GBK = "gbk"

# 编码标识 → 标准库 codec（gbk 读写都用 gb18030：它是 GBK 超集，可解 GBK 全部字节）
CODEC_BY_ENCODING: dict[str, str] = {
    ENC_UTF8: "utf-8",
    ENC_UTF8_BOM: "utf-8",
    ENC_UTF16_LE: "utf-16-le",
    ENC_UTF16_BE: "utf-16-be",
    ENC_GBK: "gb18030",
}

# 编码标识 → 需补回的 BOM（§5.2.3 硬约束 5：utf-8-bom 写回必须补回 EF BB BF）
BOM_BY_ENCODING: dict[str, bytes] = {
    ENC_UTF8_BOM: b"\xef\xbb\xbf",
    ENC_UTF16_LE: b"\xff\xfe",
    ENC_UTF16_BE: b"\xfe\xff",
}

# ── 类型表（§5.3）：权威真相是前端 ``ui/src/config/scratch-types.ts``，
#    Python 侧**只读**构建期派生的 ``file-assoc.manifest.json``（§5.1 / DS-8）。
#    清单缺失（前端类型表尚未落地）时用下面的内置回退表兜底，扩展名清单
#    **严格照抄 §5.3**，不自行发明其它扩展名（避免形成第二份手写清单）。 ──

_FALLBACK_LANGUAGE_BY_EXT: dict[str, str] = {
    "json": "json",
    "jsonc": "json",
    "md": "markdown",
    "markdown": "markdown",
    "csv": "text",
    "tsv": "text",
    "sql": "sql",
    "xml": "xml",
    "html": "html",
    "svg": "xml",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "text",
    "ini": "text",
    "conf": "text",
    "properties": "text",
    "css": "css",
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "jsx": "javascript",
    "sh": "shell",
    "bat": "bat",
    "ps1": "powershell",
    "java": "java",
    "go": "go",
    "rs": "rust",
    "c": "c",
    "cpp": "cpp",
    "log": "text",
    "txt": "text",
    "diff": "diff",
    "patch": "diff",
}

# 强制只读扩展名（§5.3：diff / patch 只读预览；§6.2.2：只读窗格拒绝写入）
READ_ONLY_EXTS: frozenset[str] = frozenset({"diff", "patch"})

# 扩展名 → language 缓存（首次使用时装载 manifest 或回退表）
_EXT_LANGUAGE_CACHE: dict[str, str] | None = None


def _resolve_manifest_path() -> str:
    """
    定位类型表清单 ``file-assoc.manifest.json``。

    - 开发态：``jflove-desktop/ui/public/file-assoc.manifest.json``
    - 冻结态：``_MEIPASS/webui/file-assoc.manifest.json``（Vite dist 随包进 webui/）
    """
    if hasattr(__import__("sys"), "_MEIPASS"):
        return str(BASE_DIR / "webui" / "file-assoc.manifest.json")
    return str(BASE_DIR / "ui" / "public" / "file-assoc.manifest.json")


MANIFEST_PATH = _resolve_manifest_path()


@dataclass(frozen=True)
class SniffVerdict:
    """二进制嗅探结论（设计 §5.2.2 B1~B6）。"""

    binary: bool
    empty: bool = False
    bom: bool = False
    # B3 命中时的 UTF-16 字节分布推断（"le" / "be"）；空串表示未命中
    utf16_pattern: str = ""


@dataclass
class ProbeResult:
    """
    ``probe()`` 的结构化判定结果（**不含向渲染层暴露的正文**）。

    ``sample`` 是 ③ 步**已读到**的有界样本（≤ 64 KB），仅供 ``read_text`` 复用，
    避免第二次 I/O（§5.2.1 ⑤）；它不在 ``public_dict()`` 的公开字段里，
    因此"probe 只做判定、不返回内容"的对外语义不被破坏。
    """

    path: str
    name: str
    size: int = 0
    ok: bool = False
    language: str = "text"
    encoding: str = ""
    line_end: str = "lf"
    read_only: bool = False
    binary: bool = False
    empty: bool = False
    bom: bool = False
    encoding_uncertain: bool = False
    reason: str = ""
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    sample: bytes = field(default=b"", repr=False)

    def public_dict(self) -> dict[str, Any]:
        """转成可序列化的公开结构（不含 sample 正文）。"""
        return {
            "path": self.path,
            "name": self.name,
            "size": self.size,
            "ok": self.ok,
            "language": self.language,
            "encoding": self.encoding,
            "lineEnding": self.line_end,
            "readOnly": self.read_only,
            "binary": self.binary,
            "empty": self.empty,
            "encodingUncertain": self.encoding_uncertain,
            "reason": self.reason,
            "message": self.message,
            "warnings": list(self.warnings),
        }


@dataclass
class ReadResult:
    """``read_text()`` 的结果（成功含正文；失败含 reason / message）。"""

    path: str
    name: str
    ok: bool = True
    text: str = ""
    size: int = 0
    language: str = "text"
    encoding: str = ""
    line_end: str = "lf"
    read_only: bool = False
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    reason: str = ""
    message: str = ""

    @classmethod
    def from_probe_error(cls, probe_result: ProbeResult) -> "ReadResult":
        """由失败的 ProbeResult 构造失败结果（保留已知 size，便于提示文案）。"""
        return cls(
            path=probe_result.path,
            name=probe_result.name,
            ok=False,
            size=probe_result.size,
            language=probe_result.language,
            encoding=probe_result.encoding,
            line_end=probe_result.line_end,
            read_only=probe_result.read_only,
            reason=probe_result.reason,
            message=probe_result.message,
        )

    def to_read_item(self) -> dict[str, Any]:
        """
        转成设计 §7.1 #2 的 ``ReadItem``（渲染层字段名用 camelCase）。

        成功：``{path, ok:true, name, text, size, language, encoding, lineEnding,
        readOnly, warnings}``；失败：``{path, ok:false, reason, message, size?}``。
        """
        if not self.ok:
            item: dict[str, Any] = {
                "path": self.path,
                "ok": False,
                "reason": self.reason,
                "message": self.message,
            }
            if self.size > 0:
                item["size"] = self.size
            return item
        return {
            "path": self.path,
            "ok": True,
            "name": self.name,
            "text": self.text,
            "size": self.size,
            "language": self.language,
            "encoding": self.encoding,
            "lineEnding": self.line_end,
            "readOnly": self.read_only,
            "warnings": list(self.warnings),
        }


# ── 日志安全引用（§7.4：不打印完整路径，截断 + sha256） ───────────────

def safe_ref(path: str) -> str:
    """日志用的安全路径引用：``{basename[:12]}#{sha256(path)[:8]}``（§7.4）。"""
    name = (os.path.basename(path) or "")[:12]
    digest = hashlib.sha256(os.fsencode(path)).hexdigest()[:8]
    return f"{name}#{digest}"


# ── 类型表装载（§5.1 / §5.3 / DS-8） ────────────────────────────────

def _load_manifest_languages(path: str) -> dict[str, str]:
    """
    从 ``file-assoc.manifest.json`` 读取 ``扩展名 → language`` 映射。

    :param path: 清单文件路径
    :returns: 映射字典；文件缺失 / 损坏 / 结构不符时返回空字典（**不抛**）
    """
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return {}
    types = data.get("types") if isinstance(data, dict) else None
    if not isinstance(types, list):
        return {}
    mapping: dict[str, str] = {}
    for entry in types:
        if not isinstance(entry, dict):
            continue
        ext = entry.get("ext")
        language = entry.get("language")
        if isinstance(ext, str) and ext and isinstance(language, str) and language:
            mapping[ext.lower().lstrip(".")] = language
    return mapping


def ext_language_map() -> dict[str, str]:
    """返回 ``扩展名 → language`` 映射（优先 manifest，缺失时用内置回退表）。"""
    global _EXT_LANGUAGE_CACHE
    if _EXT_LANGUAGE_CACHE is None:
        loaded = _load_manifest_languages(MANIFEST_PATH)
        if loaded:
            _EXT_LANGUAGE_CACHE = loaded
        else:
            _EXT_LANGUAGE_CACHE = dict(_FALLBACK_LANGUAGE_BY_EXT)
    return _EXT_LANGUAGE_CACHE


def reset_type_table_cache() -> None:
    """清空类型表缓存（测试注入清单路径后调用）。"""
    global _EXT_LANGUAGE_CACHE
    _EXT_LANGUAGE_CACHE = None


def _extension(path: str) -> str:
    """取小写扩展名（不含点）；无扩展名 / 点文件返回空串。"""
    return os.path.splitext(path)[1].lower().lstrip(".")


# ── ③ 二进制嗅探（§5.2.2，R-9 修正口径） ───────────────────────────

def _decodes_strict(data: bytes, codec: str) -> bool:
    """严格解码（禁 errors="replace"）是否成功。"""
    try:
        data.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return False
    return True


def _has_multibyte(data: bytes) -> bool:
    """是否含 GBK 多字节序列（任一字节 ∈ 0x81..0xFE）。"""
    return any(0x81 <= byte <= 0xFE for byte in data)


def sniff_bytes(sniff: bytes) -> SniffVerdict:
    """
    二进制嗅探（**只看前 8 KB**，设计 §5.2.2 B1~B6，R-9 已修正）。

    :param sniff: ``sample[:TEXT_SNIFF_BYTES]``
    :returns: SniffVerdict —— 只有 B4 会给出 ``binary=True``；
        B5/B6 一律交给 ④ 编码判定裁决（不在此误杀 GBK 中文正文）
    """
    n = len(sniff)
    # B1 空文件不是二进制 ⇒ 按 txt 打开空窗格
    if n == 0:
        return SniffVerdict(binary=False, empty=True)
    # B2 BOM 明确声明是文本（UTF-16/32）⇒ 跳过 B3/B4
    if n >= 2 and sniff[0:2] in (b"\xff\xfe", b"\xfe\xff"):
        return SniffVerdict(binary=False, bom=True)
    if n >= 4 and sniff[0:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        return SniffVerdict(binary=False, bom=True)
    # B3 无 BOM 的 UTF-16：恰有一侧 NUL 占比 ≥ 90%
    #    ⚠ R-9 实测：中文 UTF-16 从不触发本行（0.1257 / 0.0420 ≪ 0.90），
    #    只有 ASCII 占 ≥90% 的 UTF-16 才可能满足；门槛不下调（未标定）
    if b"\x00" in sniff and n >= 16:
        even_nul = sniff[0::2].count(0) / len(sniff[0::2])
        odd_nul = sniff[1::2].count(0) / len(sniff[1::2])
        even_hit = even_nul >= 0.90
        odd_hit = odd_nul >= 0.90
        if even_hit != odd_hit:
            return SniffVerdict(binary=False, utf16_pattern="be" if even_hit else "le")
    # B4 含 NUL ⇒ 二进制（F8.1.2 给定判据；无 BOM 的 UTF-16 中文会落到本行，已裁决接受）
    if b"\x00" in sniff:
        return SniffVerdict(binary=True)
    # B5 严格解码优先：utf-8 strict 成功 ⇒ 文本；gb18030 strict 成功且含多字节 ⇒ 文本
    #    （**不得**使用被推翻的「非文本字节比例 > 0.30」）
    if _decodes_strict(sniff, "utf-8"):
        return SniffVerdict(binary=False)
    if _decodes_strict(sniff, "gb18030") and _has_multibyte(sniff):
        return SniffVerdict(binary=False)
    # B6 其它 ⇒ 文本，交 ④ 编码判定（不可判定时由 ④ 拒绝，不在此静默放过）
    return SniffVerdict(binary=False)


# ── ④ 编码判定（§5.2.3，纯标准库） ─────────────────────────────────

def detect_encoding(sample: bytes) -> str | None:
    """
    在 64 KB 采样窗口内判定编码（§5.2.3 优先级 1~6）。

    :param sample: ``probe`` 阶段一次有界读到的样本（≤ TEXT_SAMPLE_BYTES）
    :returns: 编码标识；**无法判定时返回 None**（调用方必须拒绝，不得静默替换）
    """
    # 1 utf-8 BOM
    if sample.startswith(b"\xef\xbb\xbf"):
        return ENC_UTF8_BOM
    # 2 utf-16le BOM（FF FE 且非 FF FE 00 00 —— 后者是 UTF-32LE）
    if sample.startswith(b"\xff\xfe") and not sample.startswith(b"\xff\xfe\x00\x00"):
        return ENC_UTF16_LE
    # 3 utf-16be BOM
    if sample.startswith(b"\xfe\xff"):
        return ENC_UTF16_BE
    # 4 utf-8 严格解码成功且不含 U+FFFD
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        if "\ufffd" not in text:
            return ENC_UTF8
    # 5 gb18030 严格解码成功且采样中含 ≥ 1 个多字节序列
    try:
        sample.decode("gb18030")
    except UnicodeDecodeError:
        return None
    if _has_multibyte(sample):
        return ENC_GBK
    # 6 其它 ⇒ 无法判定
    return None


def _detect_line_end(sample: bytes) -> str:
    """
    换行符判定（§5.2.3 硬约束 4）：统计 ``\\r\\n`` 与裸 ``\\n``，前者更多 ⇒ crlf。
    """
    crlf = sample.count(b"\r\n")
    lone_lf = sample.count(b"\n") - crlf
    return "crlf" if crlf > lone_lf else "lf"


def _bom_length(sample: bytes) -> int:
    """识别已知的 BOM 长度（utf-8 / utf-16 家族；UTF-32 BOM 归入 4 字节）。"""
    if sample.startswith(b"\xef\xbb\xbf"):
        return 3
    if sample.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return 4
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return 2
    return 0


# ── ① ② ③ ④ 主判定流程 ────────────────────────────────────────────

def probe(path: str) -> ProbeResult:
    """
    类型校验（**只做判定，不返回内容**；设计 §5.2.1 ①~④）。

    判序被硬性固定：白名单 → stat（过大在读任何内容之前拒绝）→ 一次有界读 →
    8 KB 嗅探 → 64 KB 编码判定。任一步失败立即返回，**不读取正文**。

    :param path: 本地文件绝对路径
    :returns: ProbeResult（``ok=False`` 时 ``reason`` 取 §7.1 #2 的取值集合）
    """
    name = os.path.basename(path) or path
    result = ProbeResult(path=path, name=name)

    # ① 扩展名白名单：命中即采用对应 language，并**跳过**二进制嗅探（AC-49 / F8.5 取舍）
    ext = _extension(path)
    whitelisted = ext_language_map().get(ext)
    if whitelisted:
        result.language = whitelisted
    # .diff / .patch 强制只读预览（§5.3 / §6.2.2）
    if ext in READ_ONLY_EXTS:
        result.read_only = True

    # ② stat 取大小 —— 必须在读任何内容之前
    try:
        st = os.stat(path)
    except FileNotFoundError:
        result.reason = REASON_NOT_FOUND
        result.message = f"{name} 不存在或已被移动"
        logger.info("probe 目标不存在: %s", safe_ref(path))
        return result
    except OSError as exc:
        result.reason = REASON_IO_ERROR
        result.message = f"{name} 无法访问：{exc.__class__.__name__}"
        logger.warning("probe 访问失败: %s (%s)", safe_ref(path), exc.__class__.__name__)
        return result
    if stat_mod.S_ISDIR(st.st_mode):
        result.reason = REASON_IO_ERROR
        result.message = f"{name} 是目录，不是文本文件"
        return result

    result.size = st.st_size
    # B1 语义的等价判定：size==0 ⇒ 空文件（白名单命中跳过嗅探时也要正确置位）
    result.empty = result.size == 0
    if result.size > TEXT_MAX_BYTES:
        # > 50 MB：直接拒绝（AC-53）—— 此处尚未打开文件，满足"在读任何内容之前"
        result.reason = REASON_TOO_LARGE
        result.message = (
            f"{name} 文件过大（> {TEXT_MAX_BYTES // (1024 * 1024)} MB），已拒绝打开"
        )
        logger.info("拒绝过大文件: %s size=%d", safe_ref(path), result.size)
        return result
    if result.size > TEXT_READONLY_BYTES:
        # > 5 MB 且 ≤ 50 MB：降级只读，内容仍完整不截断（AC-52）
        result.read_only = True
        result.warnings.append(
            f"{name} 已按只读打开（> {TEXT_READONLY_BYTES // (1024 * 1024)} MB）"
        )

    # ③ 单次有界读 64 KB（只读一次；前 8 KB 嗅探，整份样本用于编码判定）
    try:
        with open(path, "rb") as fp:
            sample = fp.read(TEXT_SAMPLE_BYTES)
    except OSError as exc:
        result.reason = REASON_IO_ERROR
        result.message = f"{name} 读取失败：{exc.__class__.__name__}"
        logger.warning("probe 读取失败: %s (%s)", safe_ref(path), exc.__class__.__name__)
        return result
    result.sample = sample

    if not whitelisted:
        verdict = sniff_bytes(sample[:TEXT_SNIFF_BYTES])
        result.empty = verdict.empty
        if verdict.binary:
            result.binary = True
            result.reason = REASON_BINARY
            # 文案含 R-9 裁决要求的 UTF-16 提示（§5.2.2 残留项）
            result.message = (
                f"{name} 不是可识别的文本文件（含二进制内容）；"
                "若内容是中文，可能是无 BOM 的 UTF-16 编码，可用支持该编码的编辑器打开"
            )
            logger.info("拒绝二进制文件: %s", safe_ref(path))
            return result
        if verdict.utf16_pattern:
            # B3 救回的"无 BOM UTF-16（ASCII 为主）"：**不静默猜编码**（§5.2.3 硬约束 3），
            # 交由调用方让用户显式选择 utf-16le / utf-16be 后再调 read_text(encoding=...)
            result.encoding_uncertain = True
            result.line_end = _detect_line_end(sample)
            result.ok = True
            result.warnings.append(
                "疑似无 BOM 的 UTF-16 文本，编码无法安全推断，请手动选择 utf-16le / utf-16be"
            )
            return result

    # ④ 编码判定（64 KB 样本）
    encoding = detect_encoding(sample)
    if encoding is None:
        result.reason = REASON_UNKNOWN_ENCODING
        result.message = (
            f"{name} 的文本编码无法判定（非 UTF-8 / UTF-8-BOM / GBK / UTF-16），已拒绝打开"
        )
        logger.info("编码不可判定: %s", safe_ref(path))
        return result
    result.encoding = encoding
    result.bom = _bom_length(sample) > 0
    result.line_end = _detect_line_end(sample)
    result.ok = True
    return result


# ── ⑤ 内容加载（只有 probe 通过才被调用） ───────────────────────────

def _strip_bom(data: bytes, encoding: str) -> bytes:
    """按编码剥离开头 BOM（正文里不应出现 BOM 字符；写回时再补）。"""
    prefix = BOM_BY_ENCODING.get(encoding)
    if prefix and data.startswith(prefix):
        return data[len(prefix):]
    return data


def read_text(
    path: str,
    probe_result: ProbeResult,
    *,
    encoding: str | None = None,
) -> ReadResult:
    """
    按 ``probe()`` 的判定结果严格解码读取正文（设计 §5.2.1 ⑤）。

    **结构隔离（AC-S3）**：本函数不参与白名单判定、不做嗅探；
    ``probe_result.ok`` 为 False 时直接抛 ``ValueError``。
    读取时复用 probe ③ 步**已读到**的样本前缀，再分块读取剩余部分（≤ 4 MB/块），
    因此不产生第二次 I/O；解码一律 ``errors="strict"``（§5.2.3 硬约束 2）。

    :param path: 本地文件绝对路径
    :param probe_result: ``probe(path)`` 的返回值
    :param encoding: 可选，覆盖 probe 判定结果（仅用于 §5.2.3 硬约束 3 的
        "无 BOM UTF-16 由用户显式选择编码"分支）
    :returns: ReadResult（严格解码失败时 ``ok=False``，不降级替换）
    :raises ValueError: probe 未通过、编码未经确认或编码标识不受支持
    """
    if not isinstance(probe_result, ProbeResult):
        raise TypeError("read_text 必须接收 probe() 返回的 ProbeResult")
    if not probe_result.ok:
        raise ValueError(
            f"probe() 未通过（reason={probe_result.reason or 'unknown'}），拒绝读取内容"
        )
    enc = encoding or probe_result.encoding
    if not enc:
        raise ValueError("编码未经确认，需先由用户选择编码（utf-16le / utf-16be）")
    if enc not in CODEC_BY_ENCODING:
        raise ValueError(f"不支持的编码标识：{enc}")

    result = ReadResult(
        path=path,
        name=probe_result.name,
        size=probe_result.size,
        language=probe_result.language,
        encoding=enc,
        line_end=probe_result.line_end,
        read_only=probe_result.read_only,
        warnings=list(probe_result.warnings),
    )

    # 复用 ③ 已读样本前缀，再按 4 MB 分块读剩余部分
    prefix = probe_result.sample
    chunks: list[bytes] = [prefix] if prefix else []
    try:
        with open(path, "rb") as fp:
            if prefix:
                fp.seek(len(prefix))
            while True:
                chunk = fp.read(READ_CHUNK_BYTES)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError as exc:
        result.ok = False
        result.reason = REASON_IO_ERROR
        result.message = f"{probe_result.name} 读取失败：{exc.__class__.__name__}"
        logger.warning("读取失败: %s (%s)", safe_ref(path), exc.__class__.__name__)
        return result

    data = b"".join(chunks)
    result.size = len(data)
    try:
        # 严格解码：一旦抛 UnicodeDecodeError ⇒ 返回错误，禁止替换后仍允许保存
        result.text = _strip_bom(data, enc).decode(CODEC_BY_ENCODING[enc])
    except UnicodeDecodeError:
        result.ok = False
        result.reason = REASON_UNKNOWN_ENCODING
        result.message = f"{probe_result.name} 内容无法按 {enc} 完整解码，已拒绝加载"
        logger.warning("严格解码失败: %s encoding=%s", safe_ref(path), enc)
        return result

    result.truncated = False  # > 5 MB 只读但**不截断**（AC-52）
    result.ok = True
    return result

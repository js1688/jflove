"""
编辑器本地文件读写服务模块（v1.6.0 文本编辑器 Python 服务层 · 第 2 模块）

职责（设计 §6.2 / AC-36 ~ AC-41 / AC-20 / AC-S1）：
  - ``read_local_text(path)``  打开链路：``probe()`` → ``read_text()``（顺序固定，不可能先读后判）
  - ``read_text_only(path)``   diff 第二段等只读场景（§7.1 #4）
  - ``detect_and_read(paths)`` 多路径批量打开（§7.1 #2），单条失败**不抛**、不影响其它路径
  - ``stat_path(path)``        文件元信息（§7.1 #5），不存在返回 ``exists=False``（不抛）
  - ``write_text(...)``        按探测到的原编码 + 原换行符回写（AC-20 / §5.2.3 硬约束 5）
  - ``atomic_write(...)``      同目录临时文件 + ``os.replace`` 的原子写（§6.2.1 / AC-S1）

关键口径：
  - **原子写**：临时文件必须与目标**同目录**（跨文件系统会让 ``os.replace`` 退化为
    非原子 copy+delete）；临时名用 ``tempfile.mkstemp`` 随机后缀（**不得**用固定
    ``.tmp`` 名——那正是本版本要修的既有缺陷）；失败只删临时文件，**绝不触碰原文件**。
  - **编码/换行原样保留（AC-20）**：``utf-8-bom`` 写回补 ``EF BB BF``；``gbk`` 用
    ``gb18030`` 编码；``crlf`` 文档写回时把换行统一为 ``\\r\\n``（对原本是 lf 的
    文档统一为 ``\\n``），往返后 sha256 不变。
  - **只读拒绝**（§6.2.2 / AC-19 / AC-52）：只读窗格（> 5 MB 降级 / ``.diff`` / ``.patch``）
    调 ``write_text`` **直接拒绝**，返回 ``{"ok": false, "error": "只读", "reason": "read_only"}``。

QFileDialog 口径（§6.2.2 / §7.1 #1）：``editor.openDialog`` **必须注册到 GUI 主线程**
（``editor_bridge.MAIN_THREAD_METHODS``）；本模块**不注册桥**、也不 import PySide6 ——
文件对话框由 ``editor_bridge`` 实现，此处只文档化该约束。
"""

import hashlib
import os
import shutil
import stat as stat_mod
import tempfile
import time
from typing import Any

from src.config.settings import TEXT_READONLY_BYTES
from src.services.text_detect_service import (
    BOM_BY_ENCODING,
    CODEC_BY_ENCODING,
    READ_ONLY_EXTS,
    ReadResult,
    probe,
    read_text,
    safe_ref,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 一次最多打开的文件数（§7.1 #2：paths ≤ 64 条，超长抛错）
MAX_OPEN_PATHS = 64

# 允许的换行符标识（§5.2.3 硬约束 4/5）
LINE_END_LF = "lf"
LINE_END_CRLF = "crlf"

# Windows 上 os.replace 可能因目标被其它进程短暂持有（杀毒 / 索引）而失败：
# 有界重试 3 次 × 50 ms，耗尽后抛错（不静默）—— §6.2.1
_REPLACE_RETRIES = 3
_REPLACE_RETRY_DELAY_SEC = 0.05

# 供测试注入的接缝：生产环境即 os.replace（替换模块属性不会影响全局 os 模块）
_os_replace = os.replace


class EditorError(Exception):
    """
    编辑器服务层错误。

    ``reason`` 供桥层转成 ``{ok:false, reason, message}`` 语义（§7.1 #2/#3）。
    ``message`` **不含完整路径与内容**（§9.13 / §7.4）。
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# ── 打开链路（§6.2.3 三条入口统一） ─────────────────────────────────

def read_local_text(path: str) -> ReadResult:
    """
    打开一个本地文件：``probe()`` 判定通过后才 ``read_text()`` 加载内容。

    :param path: 本地文件绝对路径
    :returns: ReadResult（文件级失败返回 ``ok=False`` 而**不抛**，便于逐条汇总）
    """
    probe_result = probe(path)
    if not probe_result.ok:
        return ReadResult.from_probe_error(probe_result)
    return read_text(path, probe_result)


def read_text_only(path: str) -> ReadResult:
    """
    只读场景的打开（§7.1 #4：工具箱 diff 的第二段本地文件）。

    :param path: 本地文件绝对路径
    :returns: ReadResult，成功时 ``read_only=True``（窗格不回写）
    """
    result = read_local_text(path)
    if result.ok:
        result.read_only = True
        result.warnings.append("只读预览：该窗格不会回写文件")
    return result


def detect_and_read(paths: list[str]) -> dict[str, list[dict[str, Any]]]:
    """
    批量打开（§7.1 #2 / AC-48）：逐条独立处理，单条失败进 ``items[].ok=false``。

    :param paths: 路径数组（≤ 64 条）
    :returns: ``{"items": ReadItem[]}``（字段名与设计 §7.1 #2 一致，桥可直接透传）
    :raises ValueError: 参数非法（非数组 / 超长 / 元素非字符串）
    """
    if not isinstance(paths, list):
        raise ValueError("paths 必须是数组")
    if len(paths) > MAX_OPEN_PATHS:
        raise ValueError(f"一次最多打开 {MAX_OPEN_PATHS} 个文件")
    items: list[dict[str, Any]] = []
    for raw_path in paths:
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("paths 的元素必须是非空字符串路径")
        # 逐条独立：任何单条失败都不影响其它路径（AC-48）
        items.append(read_local_text(raw_path).to_read_item())
    return {"items": items}


def stat_path(path: str) -> dict[str, Any]:
    """
    查询路径元信息（§7.1 #5）。

    :param path: 本地路径
    :returns: ``{exists, size, mtime, isDir}``；不存在 / 不可访问时 ``exists=False``（**不抛**）
    """
    try:
        st = os.stat(path)
    except OSError:
        return {"exists": False, "size": 0, "mtime": 0.0, "isDir": False}
    return {
        "exists": True,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "isDir": stat_mod.S_ISDIR(st.st_mode),
    }


# ── 编码 / 换行回写（AC-20 / §5.2.3 硬约束 5） ──────────────────────

def normalize_line_end(text: str, line_ending: str) -> str:
    """
    把正文换行统一为目标文档的换行符（**幂等**，先归一为 ``\\n`` 再展开）。

    先做 ``\\r\\n → \\n`` 归一，因此即便渲染层传来的正文仍保留 ``\\r\\n``，
    写回也不会出现 ``\\r\\r\\n``（保证 AC-38 的"读取 → 原样保存 sha256 相同"）。

    :param text: 正文
    :param line_ending: ``lf`` 或 ``crlf``
    :returns: 归一后的正文
    :raises ValueError: 换行符标识非法
    """
    if line_ending not in (LINE_END_LF, LINE_END_CRLF):
        raise ValueError(f"不支持的换行符标识：{line_ending}")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if line_ending == LINE_END_CRLF:
        return normalized.replace("\n", "\r\n")
    return normalized


def encode_text(
    text: str,
    encoding: str,
    line_ending: str,
    *,
    bom: bool | None = None,
) -> bytes:
    """
    把正文编码成落盘字节（含 BOM 补回）。

    :param text: 正文
    :param encoding: 编码标识（``utf-8`` / ``utf-8-bom`` / ``utf-16le`` / ``utf-16be`` / ``gbk``）
    :param line_ending: ``lf`` 或 ``crlf``
    :param bom: 是否补 BOM；``None`` 表示按编码标识默认（utf-8-bom / utf-16* 补）
    :returns: 待写入的字节
    :raises ValueError: 编码标识不受支持
    """
    if encoding not in CODEC_BY_ENCODING:
        raise ValueError(f"不支持的编码标识：{encoding}")
    payload = normalize_line_end(text, line_ending).encode(CODEC_BY_ENCODING[encoding])
    prefix = BOM_BY_ENCODING.get(encoding)
    if prefix and (bom is None or bom):
        payload = prefix + payload
    return payload


def _read_only_reason(path: str, read_only: bool) -> str:
    """
    只读兜底判定（§6.2.2）：窗格只读 / ``.diff``、``.patch`` / 目标文件 > 5 MB。

    :returns: 只读时返回 ``"read_only"``，否则空串
    """
    if read_only:
        return "read_only"
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in READ_ONLY_EXTS:
        return "read_only"
    try:
        if os.stat(path).st_size > TEXT_READONLY_BYTES:
            return "read_only"
    except OSError:
        # 目标不存在（另存为）⇒ 不是只读
        return ""
    return ""


def _sha256_file(path: str) -> str | None:
    """计算文件内容 sha256；文件不存在 / 不可读时返回 None。"""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fp:
            while True:
                chunk = fp.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def write_text(
    path: str,
    text: str,
    encoding: str,
    line_ending: str,
    *,
    expected_sha256: str | None = None,
    read_only: bool = False,
    bom: bool | None = None,
) -> dict[str, Any]:
    """
    按原编码 + 原换行符原子写回文件（§6.2.2 / §6.2.1）。

    :param path: 目标文件路径
    :param text: 正文
    :param encoding: 编码标识（必填，来自窗格状态；§5.2.3 硬约束 5）
    :param line_ending: ``lf`` / ``crlf``
    :param expected_sha256: 可选的乐观并发前置校验（设计未细化）：磁盘现状与该值
        不一致 ⇒ 抛 ``conflict``，避免覆盖外部修改
    :param read_only: 窗格只读标记（桥层把 pane.readOnly 传进来兜一层）
    :param bom: 是否补 BOM；``None`` 表示按编码标识默认
    :returns: ``{ok: true, size, mtime, sha256}``；只读时返回
        ``{ok: false, reason: "read_only", error: "只读"}``（**不抛**，§6.2.2）
    :raises EditorError: 磁盘写入失败 / 乐观并发校验冲突
    :raises ValueError: 编码或换行符标识非法
    """
    name = os.path.basename(path) or path
    if _read_only_reason(path, read_only):
        logger.info("拒绝写入只读目标: %s", safe_ref(path))
        return {"ok": False, "reason": "read_only", "error": "只读"}

    if expected_sha256:
        current = _sha256_file(path)
        if current is not None and current != expected_sha256:
            raise EditorError("conflict", f"{name} 已被外部修改，已取消保存")

    data = encode_text(text, encoding, line_ending, bom=bom)
    try:
        atomic_write(path, data)
        st = os.stat(path)
    except OSError as exc:
        raise EditorError("io_error", f"{name} 保存失败：{exc.__class__.__name__}") from exc
    logger.info("已保存文件: %s size=%d", safe_ref(path), len(data))
    return {
        "ok": True,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


# ── 原子写（§6.2.1 / AC-S1） ────────────────────────────────────────

def atomic_write(path: str, data: bytes, *, preserve_mode: bool = True) -> None:
    """
    原子写：同目录 ``mkstemp`` 临时文件 → ``fsync`` → ``os.replace``。

    失败路径只删除临时文件，**绝不触碰原文件**（AC-41 ④）；临时文件名形如
    ``.{name}.jflove-{随机}.tmp``，随机后缀天然支持多窗格并发保存（§6.2.1）。

    :param path: 目标文件路径
    :param data: 待写入字节
    :param preserve_mode: 是否用 ``shutil.copymode`` 保留 POSIX 权限位（Windows no-op）
    :raises OSError: 写入 / 替换失败（临时文件已清理）
    """
    directory = os.path.dirname(os.path.abspath(path))
    basename = os.path.basename(path)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{basename}.jflove-", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())  # 断电场景：数据先落盘再替换
        if preserve_mode and os.path.exists(path):
            try:
                shutil.copymode(path, tmp_path)  # 不抹掉可执行位（如 .sh）
            except OSError:
                pass  # Windows / 特殊文件系统上不支持权限位，不影响写入
        _replace_with_retry(tmp_path, path)
    except BaseException:
        _silent_unlink(tmp_path)
        raise


def _replace_with_retry(tmp_path: str, path: str) -> None:
    """
    ``os.replace`` + Windows 短重试（3 次 × 50 ms），耗尽后抛错（不静默）。

    :raises OSError: 重试耗尽仍失败（由调用方清理临时文件）
    """
    last_error: OSError | None = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            _os_replace(tmp_path, path)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < _REPLACE_RETRIES:
                time.sleep(_REPLACE_RETRY_DELAY_SEC)
    assert last_error is not None  # _REPLACE_RETRIES >= 1 时不可能走到
    raise last_error


def _silent_unlink(path: str) -> None:
    """静默删除临时文件（失败路径专用）。"""
    try:
        os.unlink(path)
    except OSError:
        pass

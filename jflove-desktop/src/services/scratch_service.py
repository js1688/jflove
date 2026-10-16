"""
草稿库服务模块（v1.6.0 文本编辑器 · 设计 §6.3 / AC-54 ~ AC-61 / AC-S2）

职责：把渲染层 `editor.draftSave` / `editor.draftDiscard` / `editor.draftList` /
`editor.clearAllDrafts` 落成 `SCRATCH_DIR` 下的**纯本地明文**草稿库（§7.3「零远端」）：

    SCRATCH_DIR/index.json          # 窗格元数据索引（整体替换 + 原子写）
    SCRATCH_DIR/pane-<paneId>.txt   # 单窗格正文（UTF-8 明文 + 原子写）
    SCRATCH_DIR/.tmp/               # 临时文件落点（与目标同卷；mkstemp 随机名）

关键契约（逐条照设计实现）

1. **两个原子写**（§6.3.2）：内容文件与 `index.json` 都走「同卷临时文件 + `os.replace`」；
   临时名一律 `tempfile.mkstemp` 随机名。§6.3.3 明确**本次不修**既有服务里固定 `.tmp`
   名的同型缺陷（`auth_service.py` / `server_history_service.py` / `sync_service.py`），
   故本模块**不得**照抄那种写法。
2. **写序**：先写内容文件，**再**写 `index.json`。崩在两步之间只可能出现「索引偏旧但内容完整」
   或「索引指向的内容文件已消失」；后者在加载时**跳过并记 warning**（不阻塞、不抛异常）。
3. **半截 JSON 容错**（AC-S2 ①）：`index.json` 解析失败 ⇒ 试 `.tmp/` 下**最新**一份
   `index.json.*.tmp`；再失败 ⇒ 视为空索引，**只记 warning、不抛异常、不弹阻塞对话框**。
4. **崩溃检测**：`clean_exit == false` ⇒ 异常退出分支，供上层自动还原全部窗格（AC-55/AC-60）；
   干净退出由退出握手（§6.4）调 :func:`mark_clean_exit` 写 true。
   边界说明：`index.json` **不存在**（首次运行）或索引不可恢复时按「无任何可还原内容」处理，
   `cleanExit` 返回 True —— 只有真正读到 `clean_exit:false` 才算异常退出。
   §6.3.2 规定的临时副本回退**只对「索引存在但解析失败」生效**（缺失 ≠ 解析失败），
   因此孤儿内容文件（无索引条目）不会被自动认领，只由 :func:`clear_all_drafts` 清理。
5. **同一 paneId 写入串行化**（队列 + 单飞，§6.3.2 末行）：内容写 + 索引读改写整体持
   per-pane 锁；`index.json` 的「读 → 改 → 写」另加索引级单飞锁，避免并发 `os.replace` 交错
   与跨窗格丢失条目。
6. **日志口径**（AGENTS §9.4.14 / 设计 §7.4）：只记 paneId / 文件名，**绝不记路径全量**，
   **绝不记文档正文**；草稿是明文存储（U-8 当前口径，§8.3），敏感内容靠「一键清空」兜底。
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import tempfile
import threading
import time
from typing import Any, Mapping

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 目录与文件名（设计 §6.3.1，逐字对齐） ─────────────────────────────
INDEX_NAME = "index.json"
PANE_FILE_PREFIX = "pane-"
PANE_FILE_SUFFIX = ".txt"
TMP_DIR_NAME = ".tmp"
INDEX_VERSION = 1

# ── Windows 上 `os.replace` 的短重试（照 §6.2.1：3 次 × 50 ms，耗尽后抛错） ──
_REPLACE_ATTEMPTS = 3
_REPLACE_DELAY_SECONDS = 0.05

# paneId 白名单：只允许 `[0-9A-Za-z_-]`（渲染层传 hex；同时挡住 `../` 路径穿越）
_PANE_ID_MAX_LEN = 64
_PANE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,%d}$" % _PANE_ID_MAX_LEN)

# 行尾取值（设计 §6.3.1 的 `line_end`）
_LINE_END_VALUES = ("lf", "crlf")

# 视图模式取值（设计 v1.6.1 §5.3 的 `view_mode`：白名单 5 值，非法 / 缺失 ⇒ `edit`）
_VIEW_MODE_VALUES = ("edit", "preview", "split", "table", "tree")
_VIEW_MODE_DEFAULT = "edit"

# ── 并发闸门（模块级，进程内共用） ────────────────────────────────────
# `_pane_locks`：同一 paneId 的「内容写 + 索引改」整体串行（队列 + 单飞，§6.3.2 末行）
_pane_locks: dict[str, threading.Lock] = {}
_pane_locks_guard = threading.Lock()
# `_index_lock`：index.json 的「读 → 改 → 写」单飞，避免跨窗格并发丢条目 / replace 交错
_index_lock = threading.RLock()


# ── 路径与目录 ───────────────────────────────────────────────────────

def _scratch_dir() -> str:
    """
    解析草稿库根目录（**每次调用都读 settings**，便于测试注入临时目录）。

    :returns: 草稿库根目录的绝对路径
    """
    return os.path.abspath(str(settings.SCRATCH_DIR))


def _ensure_dirs() -> tuple[str, str]:
    """
    确保草稿库目录与 `.tmp/` 落点存在。

    权限口径照 §8.3：随 `settings.py:93-94` 的既有 `mkdir(parents=True)` 模式，
    **不额外收紧权限**（与 `session.json` 同目录、同级别）。

    :returns: `(草稿库根目录, 临时目录)` 绝对路径
    """
    scratch = _scratch_dir()
    tmp_dir = os.path.join(scratch, TMP_DIR_NAME)
    os.makedirs(tmp_dir, exist_ok=True)
    return scratch, tmp_dir


def _pane_path(scratch: str, pane_id: str) -> str:
    """拼出某窗格内容文件的绝对路径（paneId 已校验，无路径穿越风险）。"""
    return os.path.join(scratch, f"{PANE_FILE_PREFIX}{pane_id}{PANE_FILE_SUFFIX}")


def _pane_file_name(pane_id: str) -> str:
    """日志用的内容文件名（**只记文件名，不记目录**，§9.4.14）。"""
    return f"{PANE_FILE_PREFIX}{pane_id}{PANE_FILE_SUFFIX}"


def _is_valid_pane_id(pane_id: str) -> bool:
    """paneId 是否合法（非空、长度受限、只含 `[0-9A-Za-z_-]`）。"""
    return bool(pane_id) and _PANE_ID_RE.match(pane_id) is not None


def _validate_pane_id(pane_id: str) -> str:
    """
    校验 paneId 并原样返回。

    :param pane_id: 渲染层传来的窗格 ID
    :raises ValueError: ID 为空或含非法字符（含路径分隔符 / `..`）
    """
    if not _is_valid_pane_id(pane_id):
        raise ValueError("非法的窗格 ID（只允许 1~64 位字母 / 数字 / 下划线 / 连字符）")
    return pane_id


def _extract_pane_id(pane: Mapping[str, Any]) -> str:
    """从 `pane` 参数里取出并校验 `id`。"""
    if not isinstance(pane, Mapping):
        raise ValueError("草稿元数据必须是对象")
    return _validate_pane_id(str(pane.get("id", "")).strip())


# ── 原子写（设计 §6.2.1 + §6.3.1 的 `.tmp/` 落点） ────────────────────

def _silent_unlink(path: str) -> None:
    """删除文件，失败静默（失败路径只删临时文件，**绝不触碰原文件**，AC-41 ④）。"""
    with contextlib.suppress(OSError):
        os.unlink(path)


def _replace_with_retry(src: str, dst: str) -> None:
    """
    执行 `os.replace`，Windows 下对短时占用做 3 次 × 50 ms 有界重试。

    重试耗尽后**抛错**（不静默），由调用方决定提示方式；临时文件由调用方清理。

    :param src: 临时文件路径（已 fsync）
    :param dst: 目标路径
    :raises OSError: 三次替换均失败
    """
    last_exc: OSError | None = None
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:  # PermissionError 是 OSError 子类（杀毒 / 索引短暂持有）
            last_exc = exc
            if attempt < _REPLACE_ATTEMPTS - 1:
                time.sleep(_REPLACE_DELAY_SECONDS)
    raise last_exc if last_exc is not None else OSError("原子替换失败")


def _atomic_write_bytes(path: str, data: bytes) -> None:
    """
    原子写：同卷临时文件（`mkstemp` 随机名）+ `fsync` + `os.replace`。

    临时文件落在目标所在目录的 `.tmp/` 子目录（设计 §6.3.1），与目标**同卷**，
    因此 `os.replace` 是真正的原子替换（跨卷会退化为 copy+delete）。

    :param path: 目标文件路径
    :param data: 待写入的字节
    :raises OSError: 写入或替换失败（此时只清理临时文件，原文件保持旧内容）
    """
    target = os.path.abspath(path)
    tmp_dir = os.path.join(os.path.dirname(target), TMP_DIR_NAME)
    os.makedirs(tmp_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f"{os.path.basename(target)}.", suffix=".tmp", dir=tmp_dir,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, target)
    except BaseException:
        _silent_unlink(tmp)
        raise


# ── 索引读写（v1 结构，§6.3.1） ──────────────────────────────────────

def _empty_index(*, clean_exit: bool) -> dict[str, Any]:
    """构造一个内存态空索引（不落盘）。"""
    return {
        "version": INDEX_VERSION,
        "clean_exit": clean_exit,
        "updated_at": 0.0,
        "panes": [],
    }


def _as_bool(value: Any, default: bool) -> bool:
    """宽松布尔归一：不可信类型退回默认值。"""
    return value if isinstance(value, bool) else default


def _as_int(value: Any, default: int) -> int:
    """宽松整数归一：bool / 非数字 / 不可转换退回默认值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def _as_number(value: Any, default: float) -> float:
    """宽松浮点归一：非数字退回默认值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _as_optional_str(value: Any) -> str | None:
    """可空字符串归一：空串 / 非字符串按 `None`（草稿形态）处理。"""
    if value is None:
        return None
    text = str(value)
    return text or None


def _as_view_mode(value: Any, default: str = _VIEW_MODE_DEFAULT) -> str:
    """
    视图模式归一（v1.6.1 §5.3）：只认白名单 5 值，非法 / 缺失一律退回 `default`。

    宽松口径与 `line_end` 一致：渲染层发来未知值时**静默取默认值**，不抛错
    （草稿元数据不是可信输入，不该因为一个模式串把整次保存打断）。
    """
    if not isinstance(value, str):
        return default
    text = value.strip().lower()
    return text if text in _VIEW_MODE_VALUES else default


def _normalize_geometry(raw: Any) -> dict[str, Any]:
    """窗格几何归一（v1 白名单字段；缺字段填中性默认值，由渲染层决定窗口策略）。"""
    source = raw if isinstance(raw, Mapping) else {}
    return {
        "x": _as_int(source.get("x"), 0),
        "y": _as_int(source.get("y"), 0),
        "w": _as_int(source.get("w"), 0),
        "h": _as_int(source.get("h"), 0),
        "z": _as_int(source.get("z"), 0),
        "min": _as_bool(source.get("min"), False),
        "maxed": _as_bool(source.get("maxed"), False),
    }


def _normalize_caret(raw: Any) -> dict[str, int]:
    """光标位置归一（行 / 列，越界值由渲染层修正）。"""
    source = raw if isinstance(raw, Mapping) else {}
    return {"line": _as_int(source.get("line"), 0), "col": _as_int(source.get("col"), 0)}


def _normalize_pane_meta(pane: Mapping[str, Any], pane_id: str, now: float) -> dict[str, Any]:
    """
    把渲染层传来的窗格元数据归一成 `index.json` v1 的条目结构（§6.3.1）。

    只保留 v1 白名单字段（未知字段丢弃，避免索引被渲染层任意键撑大）；
    桥参数名 `lineEnd` 与存储名 `line_end` 都接受，统一存 `line_end`。

    v1.6.1 §5.3（DS-30）向后兼容扩展 3 个**可选**字段：`view_mode`（白名单 5 值，
    非法 / 缺失 ⇒ `"edit"`）、`order`（标签条顺序，缺失 ⇒ 0）、`active`（是否激活项，
    缺失 ⇒ False）。`geometry` 分支**保留不动**（v1.6.0 旧索引里已有该字段，删掉会
    破坏"读取侧原样透传"的既有行为；渲染层不再发送时它照旧填中性默认值）。

    本函数**同时**是读取路径的归一入口（`_normalize_stored_meta` → 此处），
    因此新增字段在「写入」与「读取（含旧索引）」两侧自动一致。

    :param pane: `editor.draftSave` 的 `pane` 参数（或磁盘上的存储态条目）
    :param pane_id: 已校验的窗格 ID
    :param now: 本次写入时间戳（条目 `updated_at`）
    :returns: v1 条目字典
    """
    line_end = str(pane.get("lineEnd", pane.get("line_end", "lf")) or "lf").strip().lower()
    if line_end not in _LINE_END_VALUES:
        line_end = "lf"
    return {
        "id": pane_id,
        "title": str(pane.get("title") or ""),
        "path": _as_optional_str(pane.get("path")),
        "language": str(pane.get("language") or "text"),
        "encoding": str(pane.get("encoding") or "utf-8"),
        "line_end": line_end,
        "read_only": _as_bool(pane.get("read_only"), False),
        "dirty": _as_bool(pane.get("dirty"), True),
        "geometry": _normalize_geometry(pane.get("geometry")),
        "caret": _normalize_caret(pane.get("caret")),
        "scroll_top": _as_int(pane.get("scroll_top"), 0),
        "font_size": _as_int(pane.get("font_size"), 14),
        "show_line_numbers": _as_bool(pane.get("show_line_numbers"), True),
        "word_wrap": _as_bool(pane.get("word_wrap"), False),
        # v1.6.1 新增（§5.3，均可选；旧索引缺失时取默认值）
        "view_mode": _as_view_mode(pane.get("view_mode")),
        "order": _as_int(pane.get("order"), 0),
        "active": _as_bool(pane.get("active"), False),
        "updated_at": float(now),
    }


def _to_public_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    """
    把存储态条目转成桥返回的 `PaneMeta`（§7.1 #6/#8）。

    存储名 `line_end`（§6.3.1）与桥参数名 `lineEnd`（§7.1）**同时给出**，
    两类读取方都能直接取用；其余字段原样透传（含 v1.6.1 §5.3 的
    `view_mode` / `order` / `active`，它们已由 `_normalize_pane_meta` 归一进存储条目）。
    """
    public = dict(meta)
    public["lineEnd"] = meta.get("line_end", "lf")
    return public


def _read_json_file(path: str) -> Any | None:
    """
    读并解析 JSON 文件，**任何失败都返回 `None`**（不抛异常）。

    :param path: 文件路径（不记入日志）
    :returns: 解析结果；文件不存在 / 半截 JSON / 编码异常时为 None
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _read_latest_tmp_index(scratch: str) -> Any | None:
    """
    读取 `.tmp/` 下**最新**一份 `index.json.*.tmp`（设计只按 mtime 取最新，不逐份回退）。

    :param scratch: 草稿库根目录
    :returns: 解析结果；无候选 / 候选损坏时为 None
    """
    candidates = glob.glob(os.path.join(scratch, TMP_DIR_NAME, f"{INDEX_NAME}.*.tmp"))
    if not candidates:
        return None
    try:
        latest = max(candidates, key=os.path.getmtime)
    except OSError:
        return None
    return _read_json_file(latest)


def _list_pane_ids_from_dir(scratch: str) -> list[str]:
    """
    扫描草稿库目录，取出全部内容文件对应的 paneId（按文件名排序，结果稳定）。

    只认 `pane-<id>.txt` 且 `<id>` 通过 `_is_valid_pane_id`；用于索引不可用时
    **按目录重建条目**（内容文件与索引是两套独立文件，索引坏了不代表内容丢了）。

    :param scratch: 草稿库根目录
    :returns: paneId 列表
    """
    found: list[str] = []
    pattern = os.path.join(scratch, f"{PANE_FILE_PREFIX}*{PANE_FILE_SUFFIX}")
    for path in sorted(glob.glob(pattern)):
        if not os.path.isfile(path):
            continue
        name = os.path.basename(path)
        pane_id = name[len(PANE_FILE_PREFIX):len(name) - len(PANE_FILE_SUFFIX)]
        if _is_valid_pane_id(pane_id):
            found.append(pane_id)
    return found


def _rebuild_index_from_dir(scratch: str) -> dict[str, Any]:
    """
    按草稿库目录扫描**重建**索引条目（索引不可用时的兜底）。

    重建只拿得到「内容文件还在」这件事，因此除 `id` 与 `updated_at`（取文件 mtime）
    外的元信息（标题 / 来源路径 / 窗格几何 / 光标 / 编码 / 换行符）只能回默认值 ——
    这些信息随损坏的索引一起丢失，**宁可给默认值也不能把整条草稿丢掉**。

    `clean_exit` 仍按"不可信数据"处理为 `True`：只有布尔真值才说明上次是干净退出，
    重建出来的索引不得反过来触发"异常退出 ⇒ 自动还原"分支（条目的还原由上位逻辑
    按「索引里有条目」决定，与此标志无关）。

    :param scratch: 草稿库根目录
    :returns: v1 索引字典（目录里没有内容文件时 `panes` 为空）
    """
    panes: list[dict[str, Any]] = []
    for pane_id in _list_pane_ids_from_dir(scratch):
        try:
            mtime = os.path.getmtime(_pane_path(scratch, pane_id))
        except OSError:
            mtime = 0.0
        panes.append(_normalize_pane_meta({"id": pane_id}, pane_id, mtime))
    index = _empty_index(clean_exit=True)
    index["panes"] = panes
    if panes:
        index["updated_at"] = max(float(entry["updated_at"]) for entry in panes)
    return index


def _rebuild_index_entries(scratch: str, warnings: list[str]) -> dict[str, Any] | None:
    """
    索引不可用时尝试按目录重建；**目录里也没有内容文件**时返回 None（调用方走空索引）。

    成功时把面向渲染层的提示写进 `warnings`（只报条目数，不含任何路径）。

    :param scratch: 草稿库根目录
    :param warnings: 调用方收集的 warning 列表（就地追加）
    :returns: 重建出的索引；无可重建内容时为 None
    """
    rebuilt = _rebuild_index_from_dir(scratch)
    count = len(rebuilt["panes"])
    if not count:
        return None
    warnings.append(
        f"草稿索引不可用，已按草稿库目录重建 {count} 个条目"
        "（窗格位置与光标等元信息无法恢复）",
    )
    logger.warning("草稿索引不可用，已按草稿库目录重建 %d 个条目（内容仍在）", count)
    return rebuilt


def _normalize_stored_meta(raw: Mapping[str, Any], pane_id: str) -> dict[str, Any]:
    """
    把磁盘上的条目归一成 v1 结构（字段缺失 / 类型不可信时退回默认值）。

    **读取路径同样是白名单**（v1.6.1 §13.2 U-6 的结论）：本函数直接委托
    :func:`_normalize_pane_meta`，与写入侧共用同一份字段白名单 ⇒ 白名单外的键会被
    丢弃。因此 §5.3 的 3 个新字段只需加在 `_normalize_pane_meta` 一处，写入与读取
    （含 v1.6.0 旧索引）两侧即同时生效，无需另改本函数或 `_normalize_index`。
    """
    return _normalize_pane_meta(raw, pane_id, _as_number(raw.get("updated_at"), 0.0))


def _normalize_index(raw: Any) -> dict[str, Any]:
    """
    把磁盘内容归一成 v1 索引结构。

    `clean_exit` 非布尔（含缺失）时按 **True** 处理：不可信数据**不得**误触发
    「异常退出 ⇒ 自动还原全部窗格」分支。
    """
    if not isinstance(raw, Mapping):
        return _empty_index(clean_exit=True)
    panes: list[dict[str, Any]] = []
    raw_panes = raw.get("panes")
    if isinstance(raw_panes, list):
        for item in raw_panes:
            if not isinstance(item, Mapping):
                continue
            pane_id = str(item.get("id", "")).strip()
            if _is_valid_pane_id(pane_id):
                panes.append(_normalize_stored_meta(item, pane_id))
    version = raw.get("version")
    return {
        "version": version if isinstance(version, int) and version > 0 else INDEX_VERSION,
        "clean_exit": _as_bool(raw.get("clean_exit"), True),
        "updated_at": _as_number(raw.get("updated_at"), 0.0),
        "panes": panes,
    }


def _load_index(
    scratch: str, *, validate_content: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """
    加载索引（**永不抛异常**），并做半截 JSON 容错与缺失内容文件过滤。

    流程（设计 §6.3.2）：
      ① 读 `index.json` 成功 ⇒ 用它；
      ② 读失败（存在但解析不了）⇒ 试 `.tmp/` 下最新 `index.json.*.tmp`；
      ③ 仍失败 ⇒ **按草稿库目录重建条目**（`pane-*.txt` 仍在 ⇒ 草稿没丢）；目录里也
         没有内容文件才退回空索引 + warning（不阻塞、不弹窗）；
      ④ 读成功但一条有效条目都没有 ⇒ 同样按目录重建（见下）；
      ⑤ `validate_content=True` 时逐条校验内容文件是否在位，缺的**跳过并记 warning**。

    读路径（`draftList` / 崩溃检测）要过滤；写路径（保存 / 丢弃 / 置标记）**不过滤**：
    写路径读出来的索引会被**整体写回**，若在读取阶段就剔除条目，等于把磁盘上
    其它窗格的（可能只是暂时读不到的）条目静默删掉。
    同理 ③④「丢掉全部条目」也不许发生：整体回写一个空索引会把其它窗格的条目
    静默抹掉，而内容文件还在磁盘上成为无人认领的孤儿（S-7）。

    :param scratch: 草稿库根目录
    :param validate_content: 是否剔除「索引有条目但内容文件缺失」的条目
    :returns: `(v1 索引字典, 面向渲染层的 warning 列表)`
    """
    warnings: list[str] = []
    index_path = os.path.join(scratch, INDEX_NAME)
    raw = _read_json_file(index_path)
    damaged = raw is None and os.path.exists(index_path)

    if damaged:
        recovered = _read_latest_tmp_index(scratch)
        if recovered is not None:
            raw = recovered
            warnings.append("草稿索引损坏，已从临时副本恢复")
            logger.warning("草稿索引解析失败，已从 .tmp 最新副本恢复（文件名 %s）", INDEX_NAME)

    if raw is None:
        # 首次运行 / 索引不存在 / 无法恢复：先看草稿库目录里还有没有内容文件
        rebuilt = _rebuild_index_entries(scratch, warnings)
        if rebuilt is not None:
            return rebuilt, warnings
        if damaged:
            warnings.append("草稿索引损坏且无可用临时副本，已按空索引处理（不影响已保存文件）")
            logger.warning("草稿索引与临时副本均不可用，按空索引继续（不阻塞启动）")
        # 目录里也没有内容文件 ⇒ 无任何可还原内容 ⇒ 视为干净退出
        return _empty_index(clean_exit=True), warnings

    index = _normalize_index(raw)
    if not index["panes"]:
        # 索引结构完整却一条有效条目都没有：内容文件仍在 ⇒ 以目录为准重建
        rebuilt = _rebuild_index_entries(scratch, warnings)
        if rebuilt is not None:
            index = rebuilt
    if not validate_content:
        return index, warnings

    kept: list[dict[str, Any]] = []
    skipped = 0
    for entry in index["panes"]:
        pane_id = str(entry.get("id", ""))
        if os.path.exists(_pane_path(scratch, pane_id)):
            kept.append(entry)
        else:
            skipped += 1
            logger.warning(
                "草稿索引条目缺少内容文件，已跳过（不阻塞）: %s", _pane_file_name(pane_id),
            )
    if skipped:
        warnings.append(f"已跳过 {skipped} 个缺少内容文件的草稿条目")
        index["panes"] = kept
    return index, warnings


def _index_needs_heal(scratch: str) -> bool:
    """
    `index.json` **存在但解析不了** ⇒ 需要写回一份可用的索引（S-7 自愈）。

    文件不存在（首次运行）不算需要修复：不能让一个空操作把索引凭空建出来。

    :param scratch: 草稿库根目录
    :returns: True 表示磁盘上的索引文件损坏、值得覆盖修复
    """
    index_path = os.path.join(scratch, INDEX_NAME)
    return os.path.exists(index_path) and _read_json_file(index_path) is None


def _write_index(scratch: str, index: Mapping[str, Any]) -> None:
    """
    原子**整体替换** `index.json`（AC-S2：不追加、不增量改）。

    :param scratch: 草稿库根目录
    :param index: 完整 v1 索引字典
    :raises OSError: 写失败（原 index.json 保持旧内容，不留临时文件）
    """
    payload = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    _atomic_write_bytes(os.path.join(scratch, INDEX_NAME), payload.encode("utf-8"))


def _pane_lock(pane_id: str) -> threading.Lock:
    """
    取某 paneId 的写入锁（不存在则创建）。

    「队列 + 单飞」语义：同一 paneId 的「内容写 + 索引改」整体互斥，
    后到的调用排队等待，避免并发 `os.replace` 交错（§6.3.2 末行）。
    paneId 数量由窗格数决定（个位数），字典不做淘汰。
    """
    with _pane_locks_guard:
        lock = _pane_locks.get(pane_id)
        if lock is None:
            lock = threading.Lock()
            _pane_locks[pane_id] = lock
        return lock


# ── 对外 API（对应桥方法 §7.1 #6 ~ #9） ──────────────────────────────

def save_draft(pane: Mapping[str, Any], content: str) -> dict[str, Any]:
    """
    保存一个窗格的草稿（对应 `editor.draftSave`，§7.1 #6）。

    写序（§6.3.2，**不可调换**）：
      ① 原子写 `pane-<id>.txt`（失败 ⇒ 索引完全不动，旧草稿仍可用）；
      ② 原子**整体**替换 `index.json`（upsert 该条目）。
    新草稿落盘即置 `clean_exit = false` —— 磁盘上已有「崩溃后需还原」的内容（§6.3.1 示例）。

    :param pane: 窗格元数据（`id` 必填）
    :param content: 窗格正文（UTF-8 明文写入，不做换行转换）
    :returns: `{"ok": True, "at": <写入时间戳>}`
    :raises ValueError: paneId 非法 / 正文不是字符串
    :raises OSError: 内容或索引写失败（调用方须提示用户，**不得清空**内存内容）
    """
    if not isinstance(content, str):
        raise ValueError("草稿正文必须是字符串")
    pane_id = _extract_pane_id(pane)
    scratch, _tmp_dir = _ensure_dirs()
    now = time.time()
    meta = _normalize_pane_meta(pane, pane_id, now)
    data = content.encode("utf-8")

    with _pane_lock(pane_id):
        # ① 先内容：失败则索引保持旧状态，绝不会出现「索引指向半截文件」
        _atomic_write_bytes(_pane_path(scratch, pane_id), data)
        # ② 再索引：读 → 改 → 写 全程持索引单飞锁
        with _index_lock:
            index, _warnings = _load_index(scratch, validate_content=False)
            remaining = [p for p in index["panes"] if p.get("id") != pane_id]
            remaining.append(meta)
            index["version"] = INDEX_VERSION
            index["clean_exit"] = False
            index["updated_at"] = now
            index["panes"] = remaining
            _write_index(scratch, index)

    logger.info("草稿已写入: %s (%d 字节)", _pane_file_name(pane_id), len(data))
    return {"ok": True, "at": now}


def discard_draft(pane_id: str) -> dict[str, Any]:
    """
    丢弃单个窗格草稿（对应 `editor.draftDiscard`，§7.1 #7 / AC-59）。

    **幂等**：窗格不存在（无内容文件、无索引条目）也返回 `{"ok": True}`。
    单窗格「放弃」时调用，重启不再还原该窗格（§6.4）。

    :param pane_id: 窗格 ID
    :returns: `{"ok": True}`
    :raises ValueError: paneId 非法
    :raises OSError: 索引写失败
    """
    pane_id = _validate_pane_id(str(pane_id))
    scratch, _tmp_dir = _ensure_dirs()

    with _pane_lock(pane_id):
        _silent_unlink(_pane_path(scratch, pane_id))
        with _index_lock:
            # 不过滤：内容文件刚被删掉，过滤会把该条目当成"缺失"提前剔除而漏写索引
            index, _warnings = _load_index(scratch, validate_content=False)
            remaining = [p for p in index["panes"] if p.get("id") != pane_id]
            # 条目数没变时也要落盘：索引文件本身不可解析（S-7）时，随手把它修复成
            # 重建出的合法索引，免得损坏状态一直留到下一次写操作
            if len(remaining) != len(index["panes"]) or _index_needs_heal(scratch):
                index["panes"] = remaining
                index["updated_at"] = time.time()
                _write_index(scratch, index)

    logger.info("草稿已丢弃: %s", _pane_file_name(pane_id))
    return {"ok": True}


def list_drafts() -> dict[str, Any]:
    """
    列出全部可用草稿（对应 `editor.draftList`，§7.1 #8 / AC-55 / AC-60）。

    **永不抛异常**：索引损坏 ⇒ 退回 `.tmp` 最新副本；副本也不可用则**按草稿库目录
    重建条目**（`pane-*.txt` 还在就说明草稿没丢），问题经 `warnings` 上报
    （渲染层只提示，**不弹阻塞对话框**）；缺内容文件的条目已被跳过。

    :returns: `{"ok": True, "cleanExit": bool, "entries": [PaneMeta], "warnings": [str]}`
    """
    scratch, _tmp_dir = _ensure_dirs()
    with _index_lock:
        index, warnings = _load_index(scratch)
    entries = [_to_public_meta(entry) for entry in index["panes"]]
    return {
        "ok": True,
        "cleanExit": bool(index["clean_exit"]),
        "entries": entries,
        "warnings": warnings,
    }


def is_abnormal_exit() -> bool:
    """
    崩溃检测：启动时读索引，`clean_exit == false` ⇒ 上次异常退出（§6.3.2 / AC-60）。

    供上层决定是否**自动还原全部窗格**；索引缺失或不可恢复时返回 False
    （无任何可还原内容，不算异常退出）。

    :returns: True 表示上次为非干净退出
    """
    scratch, _tmp_dir = _ensure_dirs()
    with _index_lock:
        index, _warnings = _load_index(scratch, validate_content=False)
    return index["clean_exit"] is False


def mark_clean_exit() -> dict[str, Any]:
    """
    标记干净退出（退出握手 §6.4：`clean_exit = true` 落盘）。

    只置标记、**保留草稿**（是否清理由 `retain` 决定，走 :func:`clear_all_drafts`）。

    :returns: `{"ok": True}`
    :raises OSError: 索引写失败
    """
    scratch, _tmp_dir = _ensure_dirs()
    with _index_lock:
        index, _warnings = _load_index(scratch, validate_content=False)
        index["clean_exit"] = True
        index["updated_at"] = time.time()
        _write_index(scratch, index)
    logger.info("已写入干净退出标记（clean_exit=true）")
    return {"ok": True}


def clear_all_drafts() -> dict[str, Any]:
    """
    一键清空草稿库（对应 `editor.clearAllDrafts`，§7.1 #9 / AC-61 / §8.3 敏感内容兜底）。

    清理范围**仅限草稿库**：所有 `pane-*.txt`（含索引已丢失的遗留文件）、索引条目
    与 `.tmp/` 下遗留临时文件；**绝不影响**用户已保存的文件。清空后索引为
    `panes: []` 且 `clean_exit: true`（已无待还原内容）。

    :returns: `{"ok": True, "removed": <删除的草稿内容文件数>}`
    :raises OSError: 索引写失败
    """
    scratch, tmp_dir = _ensure_dirs()
    with _index_lock:
        removed = 0
        # 按索引条目 + 目录扫描双重覆盖：索引损坏时遗留的孤儿内容文件也要清掉
        for leftover in glob.glob(
            os.path.join(scratch, f"{PANE_FILE_PREFIX}*{PANE_FILE_SUFFIX}"),
        ):
            if os.path.isfile(leftover):
                _silent_unlink(leftover)
                removed += 1
        for stale in glob.glob(os.path.join(tmp_dir, "*.tmp")):
            _silent_unlink(stale)
        index = _empty_index(clean_exit=True)
        index["updated_at"] = time.time()
        _write_index(scratch, index)

    logger.info("已清空草稿库：删除 %d 个草稿文件（用户已保存文件不受影响）", removed)
    return {"ok": True, "removed": removed}

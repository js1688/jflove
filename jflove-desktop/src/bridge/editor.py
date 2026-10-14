"""
桥的「文本编辑器 / 退出握手 / 文件关联」处理函数（v1.6.0，设计 §7.1 #1 ~ #16）

## 命名空间与本模块的边界

本模块登记 **3 个命名空间**（`bridge.py` 的 `WHITELIST` 加一行 `**editor_bridge.HANDLERS`）：

- ``editor.*``（#1 ~ #12）：`QFileDialog` 选路径、本地文件读写、草稿库、退出回执
- ``quit.pending``（#13）：退出握手状态查询
- ``file_assoc.*``（#14 ~ #16）：文件关联清单读写 + 打开系统默认应用页

## 零远端（AC-42）

编辑器**不产生任何网络调用**：本文件不 import 网络栈、不引用任何远端接口路径，
全文（含注释与文档串）保持「零远端」口径（宪法 §9.5.16 / 设计 §7.3），
判据即 AC-42 的文本断言（见 `tests/test_bridge_editor_v1_6_0.py`）。
所有本机读写都在 ``src/services/text_detect_service.py`` / ``editor_service.py`` /
``scratch_service.py`` 完成（文件字节**不进渲染层**的例外口径见设计 §8.2）。

## 主线程口径（设计 §7.1 的 ✅ 标记，共 5 个）

``QFileDialog`` 与 ``QApplication.quit()`` 只能在 **GUI 线程**碰，因此
``editor.openDialog`` / ``editor.detectAndRead`` / ``editor.confirmQuit`` /
``editor.cancelQuit`` / ``file_assoc.openSystemDefaults`` 登记进本模块自带的
``MAIN_THREAD_METHODS``（照 `bridge/media.py:185` 的既有模式；`bridge.py:359-363`
由桥求并集后判定，**不要在 `bridge.py` 里逐个列出方法名**）。

## 日志与文案（§7.4 / 宪法 §9.4.13 / §9.4.14）

- 日志里的路径一律走 `text_detect_service.safe_ref()`（截断 + sha256），**文档内容永不入日志**；
- 参数只记键名（桥的既有约定，`bridge.py:354`）；
- 面向用户的错误文案 = 文件名 + 原因（**不含目录**），由服务层生成后原样上抛。

## 依赖注入

`set_main_window(_shell)`（在 `main.py` 的 `files_bridge.set_main_window(_shell)` 旁调用）：
- 供 `QFileDialog` 作 parent；
- `editor.confirmQuit` / `editor.cancelQuit` 转发到 ``_shell.confirm_quit(retain)`` /
  ``_shell.cancel_quit()``（由 `src/ui/web_shell.py` 实现退出握手，本模块只调用）。
"""

from __future__ import annotations

import inspect
import threading
from typing import Any, Callable

from PySide6.QtWidgets import QFileDialog

from src.services import editor_service, scratch_service
from src.services.text_detect_service import safe_ref
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 主窗口 / WebShell 引用（对话框 parent + 退出握手回执；由 `main.py` 注入）
_main_window: Any = None

#: 退出握手状态（设计 §6.4：dirtyCount 真相在 Python 侧）
_quit_lock = threading.Lock()
_dirty_count = 0
_quitting_pending = False

#: `editor.openDialog` 支持的两种模式（§7.1 #1）
_OPEN_DIALOG_MODES = ("open", "save")

#: 退出握手默认超时（§6.4 / 设计 DS-6：超时走原生 QMessageBox 兜底，默认「取消」）
DEFAULT_QUIT_TIMEOUT_MS = 5000


# ── 注入与内部工具 ────────────────────────────────


def set_main_window(window: Any) -> None:
    """注入主窗口（`QFileDialog` 的 parent + 退出握手回执对象）"""
    global _main_window
    _main_window = window


def _publish(topic: str, payload: Any) -> None:
    """向渲染层推事件（惰性导入避免与 `bridge` 形成循环依赖）"""
    from src.bridge import bridge as bridge_module

    bridge_module.publish_event(topic, payload)


def _notice(code: str, message: str, level: str = "warn") -> None:
    """
    推 `editor.notice`（§7.1 事件 #4：类型校验拒绝 / 编码不可判定 / 打开失败）。

    :param code: 机器可读的原因码（如 `binary` / `too_large`）
    :param message: 用户可见文案（仅文件名 + 原因，**不含目录与内容**）
    :param level: `info` / `warn` / `error`
    """
    _publish("editor.notice", {"level": level, "code": code, "message": message})


def _require_shell() -> Any:
    """取主窗口引用；未注入时抛明确错误（不静默失败）"""
    if _main_window is None:
        raise RuntimeError("主窗口未注入（请先调用 editor_bridge.set_main_window）")
    return _main_window


def _call_with_optional_arg(func: Callable[..., Any], arg: Any) -> Any:
    """
    兼容「带 `retain` 参数」与「无参」两种 `confirm_quit` 实现。

    设计 §6.4 要求回执携带 `retain`（是否保留下次恢复），故本模块**优先**按
    ``confirm_quit(retain)`` 调用；若对方签名不接受参数则退化为无参调用。
    用 `inspect.signature` 判定**而不是** `try/except TypeError`：后者会把
    `confirm_quit` 内部真实抛出的 TypeError 误判成签名不符，可能触发二次退出。
    """
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):  # 内建 / C 扩展可调用对象：无法取签名，按带参调用
        return func(arg)
    accepts_positional = any(
        param.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        )
        for param in signature.parameters.values()
    )
    return func(arg) if accepts_positional else func()


def _file_assoc_service() -> Any:
    """
    惰性导入文件关联服务（`src/services/file_assoc_service.py`，与桥**并行产出**）。

    放在函数内 import：该模块缺失时只让 `file_assoc.*` 三个方法报错，
    不影响 `editor.*` 命名空间与整个桥的加载。

    :raises RuntimeError: 服务模块不存在或导入失败
    """
    try:
        from src.services import file_assoc_service
    except ImportError as exc:  # 模块尚未落地 / 其自身依赖缺失
        raise RuntimeError(
            "文件关联服务不可用（src/services/file_assoc_service.py 尚未就绪）"
        ) from exc
    return file_assoc_service


def _call_file_assoc(name: str, *args: Any) -> Any:
    """
    调用文件关联服务的约定接口（`get()` / `set(selected)` / `open_system_defaults()`）。

    :raises RuntimeError: 模块缺失或未实现该接口
    """
    service = _file_assoc_service()
    func = getattr(service, name, None)
    if not callable(func):
        raise RuntimeError(f"文件关联服务缺少接口：{name}()")
    return func(*args)


def _qt_name_filters(raw: Any) -> str:
    """
    把 §7.1 #1 的 `filters`（`[{name, patterns[]}]`）转成 `QFileDialog` 的过滤串。

    非法结构（非数组 / 元素非对象 / patterns 非字符串数组）**静默跳过**：
    过滤器只是体验增强，不应让"选个文件"整体失败。
    """
    if not isinstance(raw, list):
        return ""
    parts: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        patterns = entry.get("patterns")
        if not isinstance(patterns, list):
            continue
        clean = [str(item) for item in patterns if isinstance(item, str) and item.strip()]
        if not clean:
            continue
        parts.append(f"{str(entry.get('name') or '文件')} ({' '.join(clean)})")
    return ";;".join(parts)


def _open_file_names(
    parent: Any, title: str, directory: str, name_filter: str
) -> tuple[list[str], str]:
    """
    `QFileDialog.getOpenFileNames` 的薄封装（**测试接缝**：替换本模块属性即可）。

    为什么不让测试直接 patch `QFileDialog`：那会往 PySide6（shiboken）类型上写 Python
    属性再回滚，实测会改变进程退出期的 Qt 析构时序（0xC0000409）。接缝放在本模块，
    测试改的是普通模块属性，不触碰 Qt 类型（同 `editor_service._os_replace` 的既有做法）。
    """
    return QFileDialog.getOpenFileNames(parent, title, directory, name_filter)


def _save_file_name(
    parent: Any, title: str, default_name: str, name_filter: str
) -> tuple[str, str]:
    """`QFileDialog.getSaveFileName` 的薄封装（测试接缝，理由同 `_open_file_names`）。"""
    return QFileDialog.getSaveFileName(parent, title, default_name, name_filter)


def _require_str(params: dict, key: str) -> str:
    """取必填的非空字符串参数（缺失 / 空串 ⇒ 抛参数错误）"""
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少参数 {key}")
    return value


# ── 退出握手状态（§7.1 #10 / #13，设计 §6.4） ─────────────────────


def set_quit_pending(pending: bool) -> None:
    """
    置 / 清「退出确认进行中」标记（供 `web_shell.request_quit()` 与超时兜底回调）。

    `quit.pending` 的 `pending` 字段即读该标记：True 表示已向渲染层发出
    `shell.quitRequested` 且**尚未收到回执**。
    """
    global _quitting_pending
    with _quit_lock:
        _quitting_pending = bool(pending)


def get_quit_state() -> dict[str, Any]:
    """读退出握手状态快照（`quit.pending` 的返回结构，§7.1 #13）"""
    with _quit_lock:
        return {"pending": _quitting_pending, "dirtyCount": _dirty_count}


def reset_quit_state() -> None:
    """复位退出握手状态（脏计数归零 + 清 pending；测试与登出场景可用）"""
    global _dirty_count, _quitting_pending
    with _quit_lock:
        _dirty_count = 0
        _quitting_pending = False


def notify_quit_requested(timeout_ms: int = DEFAULT_QUIT_TIMEOUT_MS) -> None:
    """
    退出路径（托盘「退出」等）调用：置 pending + 推 `shell.quitRequested`（§7.1 事件 #2）。

    渲染层弹确认框后回 `editor.confirmQuit` / `editor.cancelQuit`；`timeout_ms` 内无回执
    由调用方（`web_shell.py`）走原生 `QMessageBox` 兜底（默认「取消」）。
    `dirtyCount == 0` 时**不弹**确认框的分支由调用方判断（设计 §6.4）。
    """
    set_quit_pending(True)
    _publish("shell.quitRequested", {"timeoutMs": int(timeout_ms)})


# ── editor.*（#1 ~ #12） ──────────────────────────


def _h_editor_open_dialog(p: dict) -> dict:
    """
    `editor.openDialog`（#1，**GUI 线程**）—— 原生打开 / 保存对话框。

    :param p: `{mode: "open"|"save", purpose?: "pick", defaultName?: str,
        filters?: [{name, patterns[]}]}`
    :returns: open ⇒ `{cancelled, paths}`；save ⇒ `{cancelled, path|null}`
        （取消是正常返回 `cancelled: true`，**不是**错误）
    :raises ValueError: `mode` 取值非法
    """
    mode = str(p.get("mode") or "open").strip().lower()
    if mode not in _OPEN_DIALOG_MODES:
        raise ValueError("openDialog 的 mode 只支持 open / save")
    name_filter = _qt_name_filters(p.get("filters"))

    if mode == "save":
        default_name = str(p.get("defaultName") or "")
        path, _selected = _save_file_name(_main_window, "保存文件", default_name, name_filter)
        return {"cancelled": not path, "path": path or None}

    title = "选择文件" if str(p.get("purpose") or "") == "pick" else "打开文件"
    paths, _selected = _open_file_names(_main_window, title, "", name_filter)
    files = [str(item) for item in paths]
    return {"cancelled": not files, "paths": files}


def _h_editor_detect_and_read(p: dict) -> dict:
    """
    `editor.detectAndRead`（#2，**GUI 线程**）—— 批量做类型校验并加载文本。

    **整条留在主线程**（§7.1 #2 的 ✅ 标记）：入口含首次可能的「编码选择」交互，
    与对话框保持一致的线程语义；统计与 I/O 由服务层做**有界**处理。

    单条失败**不抛**（进 `items[].ok:false`）并顺带推一条 `editor.notice`；
    参数非法（非数组 / 超长 / 元素非字符串）由服务层抛错。

    :param p: `{paths: string[]}`（≤ 64 条）
    :returns: `{items: ReadItem[]}`
    """
    result = editor_service.detect_and_read(p.get("paths"))
    items = result.get("items") or []
    for item in items:
        if not item.get("ok"):
            _notice(str(item.get("reason") or "open_failed"), str(item.get("message") or ""))
    logger.info("批量打开 %d 条（成功 %d 条）", len(items), sum(1 for i in items if i.get("ok")))
    return result


def _h_editor_write_text(p: dict) -> dict:
    """
    `editor.writeText`（#3，**工作线程**）—— 按原编码 + 原换行符原子写回。

    :param p: `{path, text, encoding, lineEnding: "lf"|"crlf", expectedSha256?}`
    :returns: `{ok: true, size, mtime, sha256}`；只读目标返回
        `{ok: false, reason: "read_only", error: "只读"}`（服务层兜底，§6.2.2）
    :raises ValueError: 参数缺失 / 编码或换行符标识非法
    :raises editor_service.EditorError: 写入失败 / 乐观并发冲突（文案不含完整路径与内容）
    """
    path = _require_str(p, "path")
    text = p.get("text")
    if not isinstance(text, str):
        raise ValueError("writeText 的 text 必须是字符串")
    encoding = _require_str(p, "encoding")
    line_ending = str(p.get("lineEnding") or "lf").strip().lower()
    expected = p.get("expectedSha256")
    result = editor_service.write_text(
        path,
        text,
        encoding,
        line_ending,
        expected_sha256=str(expected) if expected else None,
    )
    logger.info("写回文件 %s ok=%s", safe_ref(path), bool(result.get("ok")))
    return result


def _h_editor_read_text_only(p: dict) -> dict:
    """
    `editor.readTextOnly`（#4，**工作线程**）—— 只读打开（工具箱 diff 的第二段）。

    :param p: `{path}`
    :returns: 单条 `ReadItem`（成功时 `readOnly: true`；失败 `{ok:false, reason, message}`）
    """
    path = _require_str(p, "path")
    item = editor_service.read_text_only(path).to_read_item()
    if not item.get("ok"):
        _notice(str(item.get("reason") or "open_failed"), str(item.get("message") or ""))
    return item


def _h_editor_stat_path(p: dict) -> dict:
    """
    `editor.statPath`（#5，**工作线程**）—— 路径元信息（外部变更检测）。

    :param p: `{path}`
    :returns: `{exists, size, mtime, isDir}`；不存在返回 `exists: false`（**不抛**）
    """
    return editor_service.stat_path(_require_str(p, "path"))


def _h_editor_draft_save(p: dict) -> dict:
    """
    `editor.draftSave`（#6，**工作线程**）—— 落一份窗格草稿 + 推 `editor.draftSaved`。

    :param p: `{pane: {id,title,path,language,encoding,lineEnd,read_only,dirty,
        geometry,caret,scroll_top,font_size,show_line_numbers,word_wrap}, content}`
    :returns: `{ok: true, at}`
    :raises ValueError: paneId 非法 / 正文不是字符串
    :raises OSError: 写失败（渲染层须保留内存内容并提示，**不得清空**）
    """
    pane = p.get("pane")
    if not isinstance(pane, dict):
        raise ValueError("draftSave 的 pane 必须是对象")
    content = p.get("content")
    if not isinstance(content, str):
        raise ValueError("draftSave 的 content 必须是字符串")
    result = scratch_service.save_draft(pane, content)
    _publish(
        "editor.draftSaved",
        {"paneId": str(pane.get("id") or ""), "at": result.get("at", 0)},
    )
    return result


def _h_editor_draft_discard(p: dict) -> dict:
    """
    `editor.draftDiscard`（#7，**工作线程**）—— 丢弃单个窗格草稿（**幂等**）。

    :param p: `{paneId}`
    :returns: `{ok: true}`（窗格不存在也返回 ok）
    :raises ValueError: paneId 缺失或含非法字符
    """
    return scratch_service.discard_draft(_require_str(p, "paneId"))


def _h_editor_draft_list(_p: dict) -> dict:
    """
    `editor.draftList`（#8，**工作线程**）—— 列出可还原草稿 + 崩溃检测结果。

    **永不抛**（服务层已做半截 JSON 容错）：索引损坏 ⇒ 返回可用条目 + `warnings`。

    :returns: `{cleanExit, entries: PaneMeta[], warnings: string[]}`
    """
    result = scratch_service.list_drafts()
    return {
        "cleanExit": bool(result.get("cleanExit")),
        "entries": list(result.get("entries") or []),
        "warnings": list(result.get("warnings") or []),
    }


def _h_editor_clear_all_drafts(_p: dict) -> dict:
    """
    `editor.clearAllDrafts`（#9，**工作线程**）—— 一键清空草稿库（AC-61 / §8.3）。

    :returns: `{ok: true, removed}`（**不影响**用户已保存的文件）
    :raises OSError: 索引写失败
    """
    return scratch_service.clear_all_drafts()


def _h_editor_report_dirty(p: dict) -> dict:
    """
    `editor.reportDirty`（#10，**工作线程**）—— 渲染层上报脏窗格数（轻量、无内容）。

    这是退出握手的**前置**：`dirtyCount == 0` 时退出路径不弹确认框（设计 §6.4）。

    :param p: `{count: number}`
    :returns: `{ok: true}`
    :raises ValueError: `count` 不是数字
    """
    global _dirty_count
    raw = p.get("count", 0)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("reportDirty 的 count 必须是数字")
    with _quit_lock:
        _dirty_count = max(0, int(raw))
    return {"ok": True}


def _h_editor_confirm_quit(p: dict) -> dict:
    """
    `editor.confirmQuit`（#11，**GUI 线程**）—— 渲染层确认退出。

    本方法只做**转发**：`clean_exit` 落盘 / 按 `retain` 清草稿 / `quit_application()`
    都在 `src/ui/web_shell.py::confirm_quit(retain)` 内完成（避免两处各写一份退出语义）。

    :param p: `{retain: bool}`（false ⇒ 不保留下次恢复）
    :returns: `{ok: true}`
    :raises RuntimeError: 主窗口未注入或未实现 `confirm_quit`
    """
    retain = bool(p.get("retain", False))
    shell = _require_shell()
    confirm = getattr(shell, "confirm_quit", None)
    if not callable(confirm):
        raise RuntimeError("主窗口未实现 confirm_quit（退出握手未接线）")
    _call_with_optional_arg(confirm, retain)
    set_quit_pending(False)
    logger.info("退出握手：已确认退出（retain=%s）", retain)
    return {"ok": True}


def _h_editor_cancel_quit(_p: dict) -> dict:
    """
    `editor.cancelQuit`（#12，**GUI 线程**）—— 渲染层取消退出（进程不退出）。

    :returns: `{ok: true}`
    """
    shell = _require_shell()
    cancel = getattr(shell, "cancel_quit", None)
    if callable(cancel):
        cancel()
    else:
        # 语义降级是安全的：取消退出的目标态就是"进程继续运行"，
        # 此处再清一次 pending 即已达成；只在日志里留痕提示接线缺失。
        logger.warning("主窗口未实现 cancel_quit，已仅清除 pending 标记")
    set_quit_pending(False)
    return {"ok": True}


# ── quit.*（#13） ─────────────────────────────────


def _h_quit_pending(_p: dict) -> dict:
    """
    `quit.pending`（#13，**工作线程**）—— 查询退出握手状态。

    :returns: `{pending: bool, dirtyCount: number}`
    """
    return get_quit_state()


# ── file_assoc.*（#14 ~ #16） ─────────────────────


def _h_file_assoc_get(_p: dict) -> dict:
    """
    `file_assoc.get`（#14，**工作线程**）—— 读当前文件关联清单。

    :returns: `{selected: string[], supported: string[], platform: "win32"|"linux"|"other",
        osRegistered: bool, note: string}`（manifest 缺失 ⇒ `supported: []` + note，**不抛**）
    :raises RuntimeError: 文件关联服务模块缺失或未实现 `get()`
    """
    return _call_file_assoc("get")


def _h_file_assoc_set(p: dict) -> dict:
    """
    `file_assoc.set`（#15，**工作线程**）—— 写入选中的文件关联扩展名。

    :param p: `{selected: string[]}`
    :returns: `{ok: true, selected: string[]}`
    :raises ValueError: 含不支持的扩展名（**不静默过滤**，由服务层判定）
    :raises RuntimeError: 文件关联服务模块缺失或未实现 `set()`
    """
    selected = p.get("selected")
    if not isinstance(selected, list):
        raise ValueError("file_assoc.set 的 selected 必须是数组")
    return _call_file_assoc("set", selected)


def _h_file_assoc_open_system_defaults(_p: dict) -> dict:
    """
    `file_assoc.openSystemDefaults`（#16，**GUI 线程**）—— 打开系统「默认应用」设置页。

    **GUI 线程**：`QDesktopServices.openUrl` / 系统链接需要 GUI 线程
    （平台不支持时返回 `{ok: false, hint}`，由服务层判定）。

    :returns: `{ok: bool, hint: string}`
    :raises RuntimeError: 文件关联服务模块缺失或未实现 `open_system_defaults()`
    """
    return _call_file_assoc("open_system_defaults")


# ── 登记表 ────────────────────────────────────────

#: 白名单登记表：{service: {method: 处理函数}}（照 `bridge/files.py:235` 的既有模式）
HANDLERS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "editor": {
        "openDialog": _h_editor_open_dialog,
        "detectAndRead": _h_editor_detect_and_read,
        "writeText": _h_editor_write_text,
        "readTextOnly": _h_editor_read_text_only,
        "statPath": _h_editor_stat_path,
        "draftSave": _h_editor_draft_save,
        "draftDiscard": _h_editor_draft_discard,
        "draftList": _h_editor_draft_list,
        "clearAllDrafts": _h_editor_clear_all_drafts,
        "reportDirty": _h_editor_report_dirty,
        "confirmQuit": _h_editor_confirm_quit,
        "cancelQuit": _h_editor_cancel_quit,
    },
    "quit": {
        "pending": _h_quit_pending,
    },
    "file_assoc": {
        "get": _h_file_assoc_get,
        "set": _h_file_assoc_set,
        "openSystemDefaults": _h_file_assoc_open_system_defaults,
    },
}

#: 必须在 **GUI 线程**执行的方法（设计 §7.1 的 ✅ 标记，恰好这 5 个）：
#: `QFileDialog` 只能在 GUI 线程创建；`detectAndRead` 与对话框同语义整条留在主线程；
#: `confirmQuit` / `cancelQuit` 要立刻作用于 `QApplication` / 主窗口状态；
#: `openSystemDefaults` 可能调 `QDesktopServices.openUrl`。
MAIN_THREAD_METHODS: set[tuple[str, str]] = {
    ("editor", "openDialog"),
    ("editor", "detectAndRead"),
    ("editor", "confirmQuit"),
    ("editor", "cancelQuit"),
    ("file_assoc", "openSystemDefaults"),
}

"""
v1.6.0 桥命名空间 `editor` / `quit` / `file_assoc` 单元测试

设计依据：`文档记录/设计文档/v1.6.0.md` §7.1（#1~#16 的精确方法名 / 字段名 / 主线程 ✅
标记 / 错误语义 + 4 个事件）、§7.4（日志与文案口径）、§7.5（启动期事件丢失）、
§8.1（安全对照表）与 AC-42（`editor.py` 零 `/api/v1`、零 `http_client`）。

覆盖点：
  1. 登记表：3 个命名空间 / 16 个方法名逐字对齐；`MAIN_THREAD_METHODS` **恰好** 5 个
  2. 桥注册：`bridge.WHITELIST` 含全部 16 个方法；主线程并集含那 5 个
  3. 未登记方法仍是既有文案 `未登记的方法：<service>.<method>`（桥的既有行为）
  4. 16 个 handler 逐一可调用，返回字段与 §7.1 的返回列一致
  5. 错误语义：单条失败不抛（进 `items[].ok:false`）/ 参数非法抛错 / 取消不是错误
  6. 事件：`editor.draftSaved`（草稿写成功）与 `editor.notice`（打开失败）经 `publish_event` 推出
  7. AC-42：`src/bridge/editor.py` 文本内**不出现** `/api/v1` / `http_client` / `requests`
  8. 退出握手：#11/#12 转发到 `_shell.confirm_quit(retain)` / `_shell.cancel_quit()`（假 shell）
  9. `file_assoc.*`：服务模块缺失 ⇒ 明确错误（不自己实现）

安全口径：草稿用例把 `settings.SCRATCH_DIR` 指向 `tmp_path`，**绝不写真实草稿目录**。
"""
from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

from src.bridge import bridge as bridge_module
from src.bridge import editor as editor_bridge
from src.config import settings

#: §7.1 #1~#16 的方法名（逐字对照设计表，含 `lineEnd` vs `lineEnding` 的**故意不统一**）
EXPECTED_HANDLERS: dict[str, set[str]] = {
    "editor": {
        "openDialog",
        "detectAndRead",
        "writeText",
        "readTextOnly",
        "statPath",
        "draftSave",
        "draftDiscard",
        "draftList",
        "clearAllDrafts",
        "reportDirty",
        "confirmQuit",
        "cancelQuit",
        # ⚠ **不在 §7.1 的表内**：快捷键偏好的读写属**设计缺口**（§4.2.4 只规定了存储位置
        # 与结构，方法表漏列）。按命名空间一致性补齐，见 `editor.py` 的缺口说明与
        # `tests/test_bridge_editor_shortcuts_v1_6_0.py`；已登记 BACKLOG BL-17。
        "shortcutsLoad",
        "shortcutsSave",
    },
    "quit": {"pending"},
    "file_assoc": {"get", "set", "openSystemDefaults"},
}

#: §7.1 主线程 ✅ 标记的方法（恰好这 5 个）
EXPECTED_MAIN_THREAD: set[tuple[str, str]] = {
    ("editor", "openDialog"),
    ("editor", "detectAndRead"),
    ("editor", "confirmQuit"),
    ("editor", "cancelQuit"),
    ("file_assoc", "openSystemDefaults"),
}

#: `editor.py` 源码路径（AC-42 用文本断言）
EDITOR_SOURCE_PATH = Path(editor_bridge.__file__)


# ── fixtures / 工具 ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    """隔离模块级状态：退出握手状态复位 + 主窗口不注入。"""
    editor_bridge.reset_quit_state()
    monkeypatch.setattr(editor_bridge, "_main_window", None)
    yield
    editor_bridge.reset_quit_state()


@pytest.fixture(autouse=True)
def scratch_dir(tmp_path, monkeypatch):
    """草稿库根目录指向临时目录（不触碰真实 USER_DATA_DIR）。"""
    target = tmp_path / "scratch"
    monkeypatch.setattr(settings, "SCRATCH_DIR", str(target))
    return target


@pytest.fixture
def events(monkeypatch):
    """捕获 `publish_event` 推出的事件（替换桥的 `_EventSink`）。"""
    captured: list[tuple[str, object]] = []

    class _Recorder:
        """`_EventSink` 的替身：记录事件，且**不**持有 Bridge 引用（避免 Qt 退出期崩溃）。"""

        def attach(self, bridge) -> None:  # noqa: ARG002 - 签名与 _EventSink 一致
            return None

        def publish(self, topic, payload):
            captured.append((topic, payload))

    monkeypatch.setattr(bridge_module, "_EVENTS", _Recorder())
    return captured


def _pane(pane_id: str = "pane01", **overrides) -> dict:
    """构造 §7.1 #6 的 `pane` 参数。"""
    pane = {
        "id": pane_id,
        "title": "a.log",
        "path": None,
        "language": "text",
        "encoding": "utf-8",
        "lineEnd": "lf",
        "read_only": False,
        "dirty": True,
        "geometry": {"x": 1, "y": 2, "w": 3, "h": 4, "z": 0, "min": False, "maxed": False},
        "caret": {"line": 0, "col": 0},
        "scroll_top": 0,
        "font_size": 14,
        "show_line_numbers": True,
        "word_wrap": False,
    }
    pane.update(overrides)
    return pane


def _install_fake_file_assoc(monkeypatch, **funcs):
    """把假 `src/services/file_assoc_service.py` 注入 `sys.modules`（并行产出的替身）。"""
    module = types.ModuleType("src.services.file_assoc_service")
    for name, func in funcs.items():
        setattr(module, name, func)
    monkeypatch.setitem(sys.modules, "src.services.file_assoc_service", module)
    import src.services as services_pkg

    monkeypatch.setattr(services_pkg, "file_assoc_service", module, raising=False)
    return module


def _call(service: str, method: str, params: dict | None = None):
    """直接调用白名单处理函数（等价于桥在工作线程 / GUI 线程里的调用）。"""
    return editor_bridge.HANDLERS[service][method](params or {})


# ── 1. 登记表与主线程集合 ────────────────────────────────────────────


def test_handlers_namespaces_and_method_names():
    """16 个方法名逐字对齐 §7.1；命名空间恰好 3 个。"""
    assert set(editor_bridge.HANDLERS) == set(EXPECTED_HANDLERS)
    for service, methods in EXPECTED_HANDLERS.items():
        assert set(editor_bridge.HANDLERS[service]) == methods
    total = sum(len(methods) for methods in editor_bridge.HANDLERS.values())
    # 16（§7.1 表内）+ 2（快捷键偏好：设计缺口补齐，见上方的 EXPECTED_HANDLERS 注释）
    assert total == 18
    assert all(callable(fn) for group in editor_bridge.HANDLERS.values() for fn in group.values())


def test_main_thread_methods_exactly_five():
    """✅ 标记的恰好是 #1 / #2 / #11 / #12 / #16 这 5 个。"""
    assert editor_bridge.MAIN_THREAD_METHODS == EXPECTED_MAIN_THREAD
    assert len(editor_bridge.MAIN_THREAD_METHODS) == 5


def test_main_thread_methods_all_registered():
    """主线程集合里的每一项都必须已登记（否则桥永远走不到内联分支）。"""
    for service, method in editor_bridge.MAIN_THREAD_METHODS:
        assert method in editor_bridge.HANDLERS.get(service, {})


def test_bridge_whitelist_registers_all_editor_methods():
    """`bridge.py` 的 `WHITELIST` 已并集 `editor_bridge.HANDLERS`。"""
    for service, methods in EXPECTED_HANDLERS.items():
        registered = bridge_module.WHITELIST.get(service, {})
        assert set(registered) == methods
        assert all(callable(registered[name]) for name in methods)


def test_bridge_main_thread_union_contains_editor():
    """`bridge.py` 的主线程并集包含 `editor_bridge.MAIN_THREAD_METHODS`。"""
    union = (
        __import__("src.bridge.media", fromlist=["x"]).MAIN_THREAD_METHODS
        | __import__("src.bridge.sync", fromlist=["x"]).MAIN_THREAD_METHODS
        | __import__("src.bridge.transfer", fromlist=["x"]).MAIN_THREAD_METHODS
        | editor_bridge.MAIN_THREAD_METHODS
    )
    assert EXPECTED_MAIN_THREAD <= union
    # 非主线程方法必须**不在**集合里（否则会跑到 GUI 线程做磁盘 I/O）
    assert ("editor", "writeText") not in union
    assert ("editor", "draftSave") not in union


# ── 2. 桥层：未登记方法文案（既有行为不得被本版本改掉） ─────────────


def test_unregistered_method_keeps_existing_message(qapp, events):
    """`editor.未登记方法` ⇒ `{ok:false, error:"未登记的方法：editor.xxx"}`。

    `events` fixture 是必需的：真实的 `_EventSink` 会持有 Bridge 引用直到解释器退出，
    使 QObject 在 QApplication 销毁之后才析构 ⇒ Qt 退出期崩溃（0xC0000409）。
    """
    bridge = bridge_module.Bridge()
    seen: list[tuple[str, str]] = []
    bridge.responseReady.connect(lambda rid, payload: seen.append((rid, payload)))
    try:
        bridge.request("r1", "editor", "__no_such_method__", "{}")
        assert len(seen) == 1
        rid, payload = seen[0]
        assert rid == "r1"
        assert json.loads(payload) == {
            "ok": False,
            "error": "未登记的方法：editor.__no_such_method__",
        }
    finally:
        del bridge  # 立即释放 QObject，不留到解释器退出阶段


def test_main_thread_method_runs_inline(qapp, monkeypatch, events):
    """主线程方法在 `request()` 槽内**同步**完成（不经过 Worker）。"""
    _install_fake_file_assoc(
        monkeypatch, open_system_defaults=lambda: {"ok": True, "hint": "已打开系统设置"}
    )
    bridge = bridge_module.Bridge()
    seen: list[str] = []
    bridge.responseReady.connect(lambda _rid, payload: seen.append(payload))
    try:
        bridge.request("r2", "file_assoc", "openSystemDefaults", "{}")
        assert len(seen) == 1
        assert json.loads(seen[0]) == {
            "ok": True,
            "result": {"ok": True, "hint": "已打开系统设置"},
        }
    finally:
        del bridge  # 立即释放 QObject（理由同上）


# ── 3. #1 editor.openDialog（主线程） ───────────────────────────────


def test_open_dialog_open(monkeypatch):
    """`mode="open"` ⇒ `{cancelled:false, paths:[...]}`。"""
    captured: dict = {}

    def _fake_open(parent, title, directory, name_filter):  # noqa: ARG001
        captured.update(title=title, directory=directory, name_filter=name_filter)
        return ["C:/tmp/a.txt", "C:/tmp/b.md"], ""

    # 走模块接缝：**不**改 PySide6 类型属性（那会扰动 Qt 退出期析构时序）
    monkeypatch.setattr(editor_bridge, "_open_file_names", _fake_open)
    result = _call(
        "editor",
        "openDialog",
        {"mode": "open", "filters": [{"name": "文本", "patterns": ["*.txt", "*.md"]}]},
    )
    assert result == {"cancelled": False, "paths": ["C:/tmp/a.txt", "C:/tmp/b.md"]}
    assert captured["name_filter"] == "文本 (*.txt *.md)"


def test_open_dialog_open_cancelled(monkeypatch):
    """取消**不是错误**：`cancelled:true` + 空数组。"""
    monkeypatch.setattr(editor_bridge, "_open_file_names", lambda *a, **k: ([], ""))
    assert _call("editor", "openDialog", {"mode": "open"}) == {"cancelled": True, "paths": []}


def test_open_dialog_save(monkeypatch):
    """`mode="save"` ⇒ `{cancelled, path}`（取消时 `path: null`）。"""
    monkeypatch.setattr(
        editor_bridge, "_save_file_name", lambda *a, **k: ("C:/tmp/out.txt", "")
    )
    assert _call("editor", "openDialog", {"mode": "save", "defaultName": "out.txt"}) == {
        "cancelled": False,
        "path": "C:/tmp/out.txt",
    }

    monkeypatch.setattr(editor_bridge, "_save_file_name", lambda *a, **k: ("", ""))
    assert _call("editor", "openDialog", {"mode": "save"}) == {
        "cancelled": True,
        "path": None,
    }


def test_open_dialog_default_mode_is_open(monkeypatch):
    """`mode` 缺省 = `open`（§7.1 #1）。"""
    monkeypatch.setattr(editor_bridge, "_open_file_names", lambda *a, **k: ([], ""))
    assert _call("editor", "openDialog", {}) == {"cancelled": True, "paths": []}


def test_open_dialog_rejects_bad_mode():
    """`mode` 非 open/save ⇒ 抛错（桥转 `{ok:false, error}`）。"""
    with pytest.raises(ValueError):
        _call("editor", "openDialog", {"mode": "pick"})


def test_open_dialog_ignores_malformed_filters(monkeypatch):
    """非法 `filters` 结构被忽略（过滤器只是体验增强，不让选文件整体失败）。"""
    captured: dict = {}

    def _fake_open(parent, title, directory, name_filter):  # noqa: ARG001
        captured["name_filter"] = name_filter
        return [], ""

    monkeypatch.setattr(editor_bridge, "_open_file_names", _fake_open)
    _call("editor", "openDialog", {"filters": ["not-a-dict", {"patterns": "x"}, {"name": "n"}]})
    assert captured["name_filter"] == ""


# ── 4. #2 editor.detectAndRead ─────────────────────────────────────


def test_detect_and_read_items(tmp_path, events):
    """成功条目字段 = §7.1 #2 的 `ReadItem`；失败条目进 `ok:false` 且不抛。"""
    good = tmp_path / "a.txt"
    # 用字节写入：Windows 的 text 模式会把 "\n" 翻译成 "\r\n"，会污染本用例的换行判定
    good.write_bytes(b"hello\n")
    binary = tmp_path / "b.bin"
    binary.write_bytes(b"\x00\x01\x02\x03" * 64)
    missing = tmp_path / "nope.txt"

    result = _call(
        "editor",
        "detectAndRead",
        {"paths": [str(good), str(binary), str(missing)]},
    )
    assert set(result) == {"items"}
    items = result["items"]
    assert len(items) == 3

    assert items[0] == {
        "path": str(good),
        "ok": True,
        "name": "a.txt",
        "text": "hello\n",
        "size": 6,
        "language": "text",
        "encoding": "utf-8",
        "lineEnding": "lf",
        "readOnly": False,
        "warnings": [],
    }
    assert items[1]["ok"] is False
    assert items[1]["reason"] == "binary"
    assert items[1]["path"] == str(binary)
    assert set(items[1]) <= {"path", "ok", "reason", "message", "size"}
    assert items[2]["ok"] is False
    assert items[2]["reason"] == "not_found"

    # 打开失败两条 ⇒ 两条 `editor.notice`（§7.1 事件 #4）
    notices = [payload for topic, payload in events if topic == "editor.notice"]
    assert len(notices) == 2
    assert {notice["code"] for notice in notices} == {"binary", "not_found"}
    assert all(notice["level"] == "warn" for notice in notices)


def test_detect_and_read_rejects_bad_params():
    """参数非法（非数组 / 超长）⇒ 抛错（§7.1 #2 错误语义）。"""
    with pytest.raises(ValueError):
        _call("editor", "detectAndRead", {"paths": "C:/tmp/a.txt"})
    with pytest.raises(ValueError):
        _call("editor", "detectAndRead", {"paths": [f"C:/tmp/{i}.txt" for i in range(65)]})


def test_detect_and_read_uses_line_ending_field(tmp_path):
    """成功条目用 `lineEnding`（**故意**与 `pane.lineEnd` 不统一，§7.1）。"""
    crlf = tmp_path / "c.txt"
    crlf.write_bytes(b"a\r\nb\r\n")
    item = _call("editor", "detectAndRead", {"paths": [str(crlf)]})["items"][0]
    assert item["lineEnding"] == "crlf"
    assert "lineEnd" not in item


# ── 5. #3 editor.writeText ─────────────────────────────────────────


def test_write_text_returns_size_mtime_sha256(tmp_path):
    """`{ok:true, size, mtime, sha256}`，且换行按 `lineEnding` 归一（AC-38）。"""
    target = tmp_path / "a.txt"
    target.write_bytes(b"hello\n")
    result = _call(
        "editor",
        "writeText",
        {
            "path": str(target),
            "text": "hello\n",
            "encoding": "utf-8",
            "lineEnding": "lf",
        },
    )
    assert set(result) == {"ok", "size", "mtime", "sha256"}
    assert result["ok"] is True
    assert result["size"] == 6
    assert result["sha256"] == hashlib.sha256(b"hello\n").hexdigest()
    assert target.read_bytes() == b"hello\n"


def test_write_text_read_only_falls_back(tmp_path):
    """只读兜底：`.diff` 目标返回 `{ok:false, reason:"read_only"}`（不抛，§6.2.2）。"""
    target = tmp_path / "a.diff"
    target.write_bytes(b"--- a\n+++ b\n")
    result = _call(
        "editor",
        "writeText",
        {
            "path": str(target),
            "text": "x\n",
            "encoding": "utf-8",
            "lineEnding": "lf",
        },
    )
    assert result["ok"] is False
    assert result["reason"] == "read_only"
    assert target.read_bytes() == b"--- a\n+++ b\n"  # 未被改写


def test_write_text_rejects_bad_params(tmp_path):
    """参数缺失 / 编码与换行符非法 ⇒ 抛错。"""
    target = tmp_path / "a.txt"
    target.write_bytes(b"x")
    with pytest.raises(ValueError):
        _call("editor", "writeText", {"text": "x", "encoding": "utf-8", "lineEnding": "lf"})
    with pytest.raises(ValueError):
        _call(
            "editor",
            "writeText",
            {"path": str(target), "text": 1, "encoding": "utf-8", "lineEnding": "lf"},
        )
    with pytest.raises(ValueError):
        _call(
            "editor",
            "writeText",
            {"path": str(target), "text": "x", "encoding": "utf-8", "lineEnding": "cr"},
        )


# ── 6. #4 / #5 只读打开与元信息 ────────────────────────────────────


def test_read_text_only(tmp_path):
    """成功 ⇒ 单条 `ReadItem` 且 `readOnly:true`。"""
    target = tmp_path / "a.txt"
    target.write_bytes(b"hi\n")  # 字节写入：避免 Windows text 模式的换行翻译
    item = _call("editor", "readTextOnly", {"path": str(target)})
    assert item["ok"] is True
    assert item["readOnly"] is True
    assert item["text"] == "hi\n"
    assert item["warnings"]


def test_read_text_only_missing_file(tmp_path, events):
    """失败 ⇒ `{ok:false, reason:"not_found"}`（不抛）+ 一条 notice。"""
    item = _call("editor", "readTextOnly", {"path": str(tmp_path / "nope.txt")})
    assert item["ok"] is False
    assert item["reason"] == "not_found"
    assert [topic for topic, _ in events] == ["editor.notice"]


def test_stat_path(tmp_path):
    """`{exists, size, mtime, isDir}`；不存在 ⇒ `exists:false`（不抛）。"""
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    result = _call("editor", "statPath", {"path": str(target)})
    assert set(result) == {"exists", "size", "mtime", "isDir"}
    assert result["exists"] is True
    assert result["size"] == 3
    assert result["isDir"] is False
    assert _call("editor", "statPath", {"path": str(tmp_path)})["isDir"] is True
    assert _call("editor", "statPath", {"path": str(tmp_path / "no")}) == {
        "exists": False,
        "size": 0,
        "mtime": 0.0,
        "isDir": False,
    }


# ── 7. #6 ~ #9 草稿库 ─────────────────────────────────────────────


def test_draft_save_and_list(events):
    """`draftSave` ⇒ `{ok:true, at}` + `editor.draftSaved`；`draftList` 见 `lineEnd`。"""
    saved = _call("editor", "draftSave", {"pane": _pane(), "content": "hello\n"})
    assert saved["ok"] is True
    assert isinstance(saved["at"], float)

    assert [topic for topic, _ in events] == ["editor.draftSaved"]
    assert events[0][1]["paneId"] == "pane01"
    assert events[0][1]["at"] == saved["at"]

    listed = _call("editor", "draftList")
    assert set(listed) == {"cleanExit", "entries", "warnings"}
    assert listed["cleanExit"] is False  # 有草稿 ⇒ 上次不是干净退出
    assert len(listed["entries"]) == 1
    entry = listed["entries"][0]
    assert entry["id"] == "pane01"
    assert entry["lineEnd"] == "lf"  # PaneMeta = draftSave 的 pane 字段


def test_draft_save_rejects_bad_params():
    """`pane` 非对象 / `content` 非字符串 / paneId 非法 ⇒ 抛错。"""
    with pytest.raises(ValueError):
        _call("editor", "draftSave", {"pane": "x", "content": "c"})
    with pytest.raises(ValueError):
        _call("editor", "draftSave", {"pane": _pane(), "content": 1})
    with pytest.raises(ValueError):
        _call("editor", "draftSave", {"pane": _pane("../evil"), "content": "c"})


def test_draft_discard_is_idempotent():
    """`draftDiscard` 幂等：不存在的 paneId 也返回 `{ok:true}`。"""
    assert _call("editor", "draftDiscard", {"paneId": "nope"}) == {"ok": True}
    _call("editor", "draftSave", {"pane": _pane("pane02"), "content": "x"})
    assert _call("editor", "draftDiscard", {"paneId": "pane02"}) == {"ok": True}
    assert _call("editor", "draftList")["entries"] == []
    with pytest.raises(ValueError):
        _call("editor", "draftDiscard", {})


def test_clear_all_drafts(tmp_path):
    """`clearAllDrafts` ⇒ `{ok:true, removed}`，且不影响草稿库外的文件。"""
    outside = tmp_path / "keep.txt"
    outside.write_text("keep", encoding="utf-8")
    _call("editor", "draftSave", {"pane": _pane("pane03"), "content": "x"})
    result = _call("editor", "clearAllDrafts")
    assert result["ok"] is True
    assert result["removed"] == 1
    assert outside.read_text(encoding="utf-8") == "keep"
    assert _call("editor", "draftList")["entries"] == []


# ── 8. #10 ~ #13 退出握手 ─────────────────────────────────────────


def test_report_dirty_and_quit_pending():
    """`reportDirty` 写脏计数；`quit.pending` 读 `{pending, dirtyCount}`。"""
    editor_bridge.set_quit_pending(True)
    assert _call("editor", "reportDirty", {"count": 3}) == {"ok": True}
    assert _call("quit", "pending") == {"pending": True, "dirtyCount": 3}
    assert _call("editor", "reportDirty", {"count": 0}) == {"ok": True}
    assert _call("quit", "pending") == {"pending": True, "dirtyCount": 0}
    with pytest.raises(ValueError):
        _call("editor", "reportDirty", {"count": "many"})


def test_confirm_quit_forwards_retain():
    """#11 转发到 `_shell.confirm_quit(retain)`（设计 §6.4）。"""
    calls: list[bool] = []

    class _Shell:
        def confirm_quit(self, retain: bool) -> None:
            calls.append(retain)

    editor_bridge.set_main_window(_Shell())
    editor_bridge.set_quit_pending(True)
    assert _call("editor", "confirmQuit", {"retain": False}) == {"ok": True}
    assert calls == [False]
    assert _call("quit", "pending")["pending"] is False


def test_confirm_quit_tolerates_no_arg_shell_method():
    """对方实现为无参 `confirm_quit()` 时也能调用（签名兼容，不二次调用）。"""
    calls: list[int] = []

    class _Shell:
        def confirm_quit(self) -> None:  # 无参实现
            calls.append(1)

    editor_bridge.set_main_window(_Shell())
    assert _call("editor", "confirmQuit", {"retain": True}) == {"ok": True}
    assert calls == [1]


def test_confirm_quit_without_shell_raises():
    """未注入主窗口 / 未实现 `confirm_quit` ⇒ 明确错误（不静默）。"""
    with pytest.raises(RuntimeError):
        _call("editor", "confirmQuit", {"retain": True})

    class _NoHandshake:
        pass

    editor_bridge.set_main_window(_NoHandshake())
    with pytest.raises(RuntimeError):
        _call("editor", "confirmQuit", {"retain": True})


def test_cancel_quit_clears_pending():
    """#12 转发到 `_shell.cancel_quit()` 并清 pending（AC-56：进程不退出）。"""
    calls: list[int] = []

    class _Shell:
        def cancel_quit(self) -> None:
            calls.append(1)

    editor_bridge.set_main_window(_Shell())
    editor_bridge.set_quit_pending(True)
    assert _call("editor", "cancelQuit") == {"ok": True}
    assert calls == [1]
    assert _call("quit", "pending")["pending"] is False


def test_cancel_quit_without_cancel_impl_still_ok():
    """主窗口未实现 `cancel_quit` 时降级为「仅清 pending」（目标态本就是继续运行）。"""
    editor_bridge.set_main_window(object())
    editor_bridge.set_quit_pending(True)
    assert _call("editor", "cancelQuit") == {"ok": True}
    assert _call("quit", "pending")["pending"] is False


def test_notify_quit_requested_publishes_event(events):
    """退出路径辅助：置 pending + 推 `shell.quitRequested {timeoutMs}`。"""
    editor_bridge.notify_quit_requested(5000)
    assert events == [("shell.quitRequested", {"timeoutMs": 5000})]
    assert _call("quit", "pending")["pending"] is True


# ── 9. #14 ~ #16 file_assoc.* ────────────────────────────────────


def test_file_assoc_get_passthrough(monkeypatch):
    """#14 返回字段逐字对齐 §7.1（服务层判定 manifest 缺失，不抛）。"""
    payload = {
        "selected": ["txt", "md"],
        "supported": ["txt", "md"],
        "platform": "win32",
        "osRegistered": False,
        "note": "尚未注册到系统默认应用",
    }
    _install_fake_file_assoc(monkeypatch, get=lambda: payload)
    result = _call("file_assoc", "get")
    assert result == payload
    assert set(result) == {"selected", "supported", "platform", "osRegistered", "note"}


def test_file_assoc_set_passthrough(monkeypatch):
    """#15 返回 `{ok:true, selected:[...]}`；含不支持扩展名由服务层抛错。"""
    seen: list[list[str]] = []

    def _set(selected):
        seen.append(selected)
        if "exe" in selected:
            raise ValueError("不支持的扩展名：exe")
        return {"ok": True, "selected": selected}

    _install_fake_file_assoc(monkeypatch, set=_set)
    assert _call("file_assoc", "set", {"selected": ["txt"]}) == {"ok": True, "selected": ["txt"]}
    assert seen == [["txt"]]
    with pytest.raises(ValueError):
        _call("file_assoc", "set", {"selected": ["exe"]})
    with pytest.raises(ValueError):
        _call("file_assoc", "set", {"selected": "txt"})


def test_file_assoc_open_system_defaults_passthrough(monkeypatch):
    """#16 返回 `{ok, hint}`（平台不支持 ⇒ `ok:false` + hint，不抛）。"""
    _install_fake_file_assoc(
        monkeypatch, open_system_defaults=lambda: {"ok": False, "hint": "当前平台不支持"}
    )
    assert _call("file_assoc", "openSystemDefaults") == {
        "ok": False,
        "hint": "当前平台不支持",
    }


def test_file_assoc_missing_service_raises_clear_error(monkeypatch):
    """服务模块尚未就绪 ⇒ 明确 RuntimeError（**不自行实现**该服务）。"""
    import src.services as services_pkg

    monkeypatch.delattr(services_pkg, "file_assoc_service", raising=False)
    monkeypatch.setitem(sys.modules, "src.services.file_assoc_service", None)
    cases = (("get", {}), ("set", {"selected": ["txt"]}), ("openSystemDefaults", {}))
    for method, params in cases:
        with pytest.raises(RuntimeError) as excinfo:
            _call("file_assoc", method, params)
        assert "file_assoc_service" in str(excinfo.value)


def test_file_assoc_missing_interface_raises(monkeypatch):
    """服务模块存在但缺接口 ⇒ 明确 RuntimeError。"""
    _install_fake_file_assoc(monkeypatch, get=lambda: {})
    with pytest.raises(RuntimeError) as excinfo:
        _call("file_assoc", "openSystemDefaults")
    assert "open_system_defaults" in str(excinfo.value)


def test_file_assoc_integration_with_real_service():
    """与**真实** `file_assoc_service` 的接口契约（惰性 import 的实际落点）。

    只做**读操作**与一次"必然被拒绝"的写：不触碰用户真实关联配置（禁令口径同草稿用例）。
    """
    pytest.importorskip("src.services.file_assoc_service")
    before = _call("file_assoc", "get")
    assert set(before) == {"selected", "supported", "platform", "osRegistered", "note"}
    assert before["platform"] in ("win32", "linux", "other")
    assert isinstance(before["supported"], list)
    assert isinstance(before["note"], str)

    # 含不支持的扩展名 ⇒ 抛错（**不静默过滤**），且不改变既有选择
    with pytest.raises(ValueError):
        _call("file_assoc", "set", {"selected": ["definitely-not-a-supported-ext"]})
    assert _call("file_assoc", "get")["selected"] == before["selected"]

    opened = _call("file_assoc", "openSystemDefaults")
    assert set(opened) == {"ok", "hint"}
    assert isinstance(opened["ok"], bool)
    assert isinstance(opened["hint"], str)


# ── 10. AC-42：zero remote ───────────────────────────────────────


def test_editor_bridge_source_has_zero_remote():
    """AC-42：`editor.py` 文本内零 `/api/v1`、零 `http_client`、零 `requests`。"""
    source = EDITOR_SOURCE_PATH.read_text(encoding="utf-8")
    for forbidden in ("/api/v1", "http_client", "requests", "session_key", "urllib"):
        assert forbidden not in source, f"editor.py 不得出现 {forbidden}"


def test_editor_bridge_module_imports_no_network_stack():
    """运行时口径：`editor` 桥模块与 `http_client` / `api_gateway` 无关。"""
    assert not hasattr(editor_bridge, "http_client")
    assert not hasattr(editor_bridge, "requests")
    assert not hasattr(editor_bridge, "api_gateway")


def test_editor_handlers_do_not_touch_api_gateway(monkeypatch):
    """所有 16 个 handler 调用期间 `api_gateway.handle_api_request` 不会被触达。"""
    calls: list[str] = []
    monkeypatch.setattr(
        bridge_module.api_gateway, "handle_api_request", lambda _p: calls.append("hit")
    )
    editor_bridge.set_main_window(object())
    _install_fake_file_assoc(
        monkeypatch,
        get=lambda: {
            "selected": [],
            "supported": [],
            "platform": "other",
            "osRegistered": False,
            "note": "",
        },
        set=lambda selected: {"ok": True, "selected": selected},
        open_system_defaults=lambda: {"ok": False, "hint": ""},
    )
    _call("editor", "draftList")
    _call("editor", "statPath", {"path": "C:/definitely/not/here"})
    _call("editor", "reportDirty", {"count": 0})
    _call("quit", "pending")
    _call("file_assoc", "get")
    _call("file_assoc", "set", {"selected": []})
    _call("file_assoc", "openSystemDefaults")
    assert calls == []

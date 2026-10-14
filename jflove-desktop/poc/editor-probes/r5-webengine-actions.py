# -*- coding: utf-8 -*-
"""R-5 实测：QWebEnginePage/View 的宿主动作清单 + setShortcuts([]) 的有效性

对应设计文档 `文档记录/设计文档/v1.6.0.md` §4.2.3 / §13.1 R-5 / AC-10。
风险：AC-10「配了但按了没反应」—— 要确认 WebEngine 宿主到底注册了哪些 action、
这些 action 是否抢占了编辑器要接管的组合键（Ctrl+S / Ctrl+Shift+S / Ctrl+P / Ctrl+O /
Ctrl+F / Ctrl+G / Ctrl+= / Ctrl+- / Ctrl+0 / F6 / F12 …），以及 setShortcuts([]) 是否有效。

不修改任何项目源码。原始输出写本目录 out/ 下：
    out/r5-<platform>.json   结构化证据（完整 action 清单 + 各阶段按键结果）
    out/r5-<platform>.log    完整原始 stdout/stderr
    out/r5-page.html         被测页面（file:// 加载，SavePage 才有意义）

运行：
    jflove-desktop/venv-win/Scripts/python.exe jflove-desktop/poc/editor-probes/r5-webengine-actions.py --platform=windows
    jflove-desktop/venv-win/Scripts/python.exe jflove-desktop/poc/editor-probes/r5-webengine-actions.py --platform=offscreen

平台说明：QAction 快捷键派发要求所在窗口处于激活状态，因此 windows（真实窗口）是权威平台；
offscreen 用于回答"无头环境是否仍成立"，结论中如实区分。

测量设计（为什么这样才可信）：
  1. Qt 6.11 的 `QWebEnginePage` 已经没有 `actions()`（只有 `action(WebAction)`），
     所以先断言 API 形态，再逐枚举取 action 对象。
  2. 读 `shortcuts()` 只能证明"注册了什么"；因此用两个注入通道真实按键：
     ① QTest.keyClick（Qt 合成事件）；② Win32 keybd_event（OS 级真实按键）。
     观察：48 个 action 的 triggered 计数、新顶层窗口（保存/打印对话框）、zoomFactor、
     loadStarted 次数、view Close 事件、渲染层 JS 的 keydown 记录。
  3. **敏感性对照是前提**：先把 Ctrl+S 手工绑到 SavePage 上再按，若 triggered 上升，
     说明"按键派发 + 观测链路"有效；对照失败 ⇒ 本轮所有"无反应"结论作废（如实标注）。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from pathlib import Path

# 原始输出统一 UTF-8（Windows 控制台默认 GBK，重定向到文件后会变成非 UTF-8）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:  # noqa: BLE001
        pass

# ── 平台与 Chromium 开关必须在 import QtWebEngineWidgets 之前设好 ──────────────
_parser = argparse.ArgumentParser(add_help=True)
_parser.add_argument("--platform", default=os.environ.get("R5_PLATFORM", "windows"))
_parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
_ARGS, _ = _parser.parse_known_args()

os.environ["QT_QPA_PLATFORM"] = _ARGS.platform
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

import PySide6  # noqa: E402
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

OUT_DIR = Path(_ARGS.out)
OUT_DIR.mkdir(parents=True, exist_ok=True)

PLATFORM = _ARGS.platform
#: 设计文档 §4.2.3 / §13.1 R-5 点名要看的组合键
CONFLICT_KEYS = [
    "Ctrl+S", "Ctrl+Shift+S", "Ctrl+P", "Ctrl+O", "Ctrl+F", "Ctrl+G",
    "Ctrl+=", "Ctrl+-", "Ctrl+0", "F6", "F12",
]
#: 扩展探测：宿主常见浏览器快捷键 + 本应用默认键位（反向验证宿主是否抢键）
EXTRA_KEYS = [
    "Ctrl+U", "Ctrl+R", "F5", "F11", "Ctrl+D", "Ctrl+L", "Ctrl+T", "Ctrl+Tab",
    "Ctrl+A", "Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+Z", "Ctrl+/", "Alt+Z",
    "Ctrl+Alt+N", "Ctrl+Alt+W", "Ctrl+Alt+T", "Ctrl+Alt+O", "Ctrl+Alt+K", "Ctrl+Shift+V",
]
#: 破坏性键（可能关闭视图）放最后单独跑
DESTRUCTIVE_KEYS = ["Ctrl+W", "Ctrl+N", "Ctrl+Alt+Left", "Ctrl+Alt+Right"]
#: 若真要"关闭宿主动作"，建议的最小集合（理由见 R5-结论.md）
DISABLE_SET = [
    "SavePage", "ViewSource", "RequestClose", "InspectElement", "ExitFullScreen",
    "Reload", "ReloadAndBypassCache", "Back", "Forward", "Stop",
]
#: OS 级注入只测这几个关键键（用于回答"渲染层到底收不收得到键"）
OS_KEYS = [("plain A", [], "a"), ("Ctrl+S", ["ctrl"], "s"), ("Ctrl+P", ["ctrl"], "p")]

JS_LOGGER = """
window.__probeLog = [];
window.__probeClicks = 0;
window.addEventListener('keydown', function (e) {
    window.__probeLog.push({
        phase: 'capture', key: e.key, code: e.code,
        ctrl: e.ctrlKey, shift: e.shiftKey, alt: e.altKey, meta: e.metaKey,
        target: (e.target && e.target.tagName) || '',
        defaultPrevented: e.defaultPrevented
    });
}, true);
window.addEventListener('keyup', function (e) {
    window.__probeLog.push({phase: 'keyup', key: e.key});
}, true);
window.addEventListener('click', function () { window.__probeClicks += 1; }, true);
'done';
"""

# ── Win32 按键注入（OS 级，真实按键；用于绕开 QTest 合成事件） ────────────────
_USER32 = ctypes.windll.user32 if sys.platform == "win32" else None
_KEYEVENTF_KEYUP = 0x0002
_VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "s": 0x53, "p": 0x50, "a": 0x41}


def os_press(mods: list[str], key: str) -> None:
    if _USER32 is None:
        return
    for m in mods:
        _USER32.keybd_event(_VK[m], _USER32.MapVirtualKeyW(_VK[m], 0), 0, 0)
    _USER32.keybd_event(_VK[key], _USER32.MapVirtualKeyW(_VK[key], 0), 0, 0)
    time.sleep(0.05)
    _USER32.keybd_event(_VK[key], _USER32.MapVirtualKeyW(_VK[key], 0), _KEYEVENTF_KEYUP, 0)
    for m in reversed(mods):
        _USER32.keybd_event(_VK[m], _USER32.MapVirtualKeyW(_VK[m], 0), _KEYEVENTF_KEYUP, 0)


class ViewWatcher(QObject):
    """观察视图 Close 事件（Ctrl+W 是否真的关掉 WebEngine 视图）"""

    def __init__(self) -> None:
        super().__init__()
        self.close_events = 0

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Close:
            self.close_events += 1
            print("      [watcher] view received Close event", flush=True)
        return False


class Probe:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.view = QWebEngineView()
        self.page = self.view.page()
        self.watcher = ViewWatcher()
        self.view.installEventFilter(self.watcher)
        self.known_tops = {id(w) for w in QApplication.topLevelWidgets()}
        self.trigger_counts: dict[str, int] = {}
        self.actions_by_name: dict[str, object] = {}
        self.load_started = 0
        #: SavePage 的执行路径在 QtWebEngine 里是"下载请求"（QWebEngineDownloadRequest），
        #: 不是 QFileDialog；这里挂上钩子并立刻 cancel，既能看到它有没有真的执行，又不落盘
        self.download_requests: list[dict] = []
        self.result: dict = {
            "platform": PLATFORM,
            "platform_name": app.platformName(),
            "python": sys.version,
            "pyside6": PySide6.__version__,
            "qt": __import__("PySide6.QtCore", fromlist=["qVersion"]).qVersion(),
            "api_shape": {},
            "profile": {},
            "actions": [],
            "conflict_matrix": {},
            "key_sweeps": {},
            "os_input": {},
            "control": {},
            "mitigation": {},
            "harness": {},
            "notes": [],
        }
        self.page.loadStarted.connect(self._on_load_started)
        QWebEngineProfile.defaultProfile().downloadRequested.connect(self._on_download)

    def _on_download(self, item: object) -> None:
        """记录（并取消）SavePage 产生的下载请求，避免写盘"""
        info = {
            "suggested_file_name": getattr(item, "suggestedFileName", lambda: "?")(),
            "url": getattr(item, "url", lambda: None)() and item.url().toString()[:80],
            "state": str(item.state()) if hasattr(item, "state") else "?",
        }
        self.download_requests.append(info)
        print(f"      [download] 收到下载请求：{info}（已 cancel，不落盘）", flush=True)
        try:
            item.cancel()
        except Exception as exc:  # noqa: BLE001
            print(f"      [download] cancel 失败：{exc}", flush=True)

    # ── 小工具 ────────────────────────────────────────────────────────────
    def _on_load_started(self) -> None:
        self.load_started += 1

    def new_tops(self) -> list[str]:
        return [type(w).__name__ for w in QApplication.topLevelWidgets() if id(w) not in self.known_tops]

    def sweep_dialogs(self) -> list[str]:
        closed = []
        for w in QApplication.topLevelWidgets():
            if id(w) not in self.known_tops and isinstance(w, QDialog):
                closed.append(type(w).__name__)
                w.reject()
                w.close()
        return closed

    def _js_eval(self, expr: str, timeout_ms: int = 3000):
        """同步取一次 JS 表达式结果（None = 回调超时/未定义）"""
        box = {"done": False, "value": None}

        def _cb(value: object) -> None:
            box["value"] = value
            box["done"] = True

        self.page.runJavaScript(expr, _cb)
        deadline = time.time() + timeout_ms / 1000
        while not box["done"] and time.time() < deadline:
            QTest.qWait(50)
        return box["value"] if box["done"] else None

    def take_js_keys(self) -> dict:
        """取走渲染层键盘/点击记录"""
        logs = self._js_eval("window.__probeLog")
        clicks = self._js_eval("window.__probeClicks")
        callback_ok = logs is not None
        if not isinstance(logs, list):
            logs = []
        self.page.runJavaScript("window.__probeLog = []")
        QTest.qWait(50)
        keys = sorted({
            f"{i.get('key')}|{i.get('phase')}|ctrl={i.get('ctrl')}|shift={i.get('shift')}"
            f"|alt={i.get('alt')}|target={i.get('target')}"
            for i in logs if isinstance(i, dict) and i.get("phase") in ("capture", "keyup")
        })
        return {
            "keys": keys,
            "clicks": clicks if isinstance(clicks, (int, float)) else None,
            "callback_ok": callback_ok,
        }

    def fire(self, seq: str, use_os: bool = False) -> dict:
        """按下组合键并采集所有可观测量"""
        seq_obj = QKeySequence(seq)
        key = seq_obj[0].key()
        mods = seq_obj[0].keyboardModifiers()
        target = self.view.focusProxy() or self.view
        before = {k: v for k, v in self.trigger_counts.items() if v}
        zoom_before = self.page.zoomFactor()
        load_before = self.load_started
        close_before = self.watcher.close_events
        self.page.runJavaScript("window.__probeLog = []")
        QTest.qWait(40)
        if use_os:
            _os_key = {"A": ([], "a"), "Ctrl+S": (["ctrl"], "s"), "Ctrl+P": (["ctrl"], "p")}.get(seq)
            if _os_key is None:
                return {"sequence": seq, "channel": "os_keybd_event", "skipped": "OS 通道未配置该键",
                        "action_triggered": {}, "renderer_keys": []}
            os_press(*_os_key)
        else:
            QTest.keyClick(target, key, mods)
        QTest.qWait(500)
        closed = self.sweep_dialogs()
        QTest.qWait(200)
        fired = {
            k: self.trigger_counts[k] - before.get(k, 0)
            for k in self.trigger_counts
            if self.trigger_counts[k] != before.get(k, 0)
        }
        js = self.take_js_keys()
        return {
            "sequence": seq,
            "channel": "os_keybd_event" if use_os else "qtest_keyclick",
            "window_active": self.view.isActiveWindow(),
            "view_has_focus": self.view.hasFocus(),
            "action_triggered": fired,
            "new_top_levels": self.new_tops(),
            "dialogs_closed": closed,
            "zoom_before": zoom_before,
            "zoom_after": self.page.zoomFactor(),
            "zoom_changed": zoom_before != self.page.zoomFactor(),
            "load_started_delta": self.load_started - load_before,
            "view_close_events_delta": self.watcher.close_events - close_before,
            "renderer_keys": js["keys"],
            "renderer_clicks": js["clicks"],
            "js_callback_ok": js["callback_ok"],
        }

    def run_sweep(self, keys: list[str], label: str, use_os: bool = False) -> None:
        print(f"--- 按键扫描：{label}（通道={'OS keybd_event' if use_os else 'QTest'}）---", flush=True)
        rows = []
        for seq in keys:
            row = self.fire(seq, use_os=use_os)
            rows.append(row)
            print(
                f"  {seq:16s} triggered={row.get('action_triggered')} tops={row.get('new_top_levels')} "
                f"zoom={row.get('zoom_before')}->{row.get('zoom_after')} "
                f"load_started={row.get('load_started_delta')} close={row.get('view_close_events_delta')} "
                f"renderer_keys={row.get('renderer_keys')}",
                flush=True,
            )
        self.result["key_sweeps"][label] = rows

    # ── 阶段：API 形态 + 动作清单 ──────────────────────────────────────────
    def collect_api_and_actions(self) -> None:
        shape = {
            "QWebEnginePage_has_actions": hasattr(QWebEnginePage, "actions"),
            "QWebEnginePage_action_attr": hasattr(QWebEnginePage, "action"),
            "QWebEngineView_has_actions": hasattr(QWebEngineView, "actions"),
            "QWebEngineView_pageAction": hasattr(self.view, "pageAction"),
            "view_actions_len": len(self.view.actions()),
            "QWebEnginePage_module": type(self.page).__module__,
            "QWebEnginePage_dir_action_names": [m for m in dir(QWebEnginePage) if "action" in m.lower()],
        }
        prof = QWebEngineProfile.defaultProfile()
        self.result["api_shape"] = shape
        self.result["profile"] = {
            "storage_name": prof.storageName(),
            "is_off_the_record": prof.isOffTheRecord(),
            "persistent_storage_path": prof.persistentStoragePath(),
            "cache_path": prof.cachePath(),
            "http_cache_type": str(prof.httpCacheType()),
        }
        print("API 形态：" + json.dumps(shape, ensure_ascii=False), flush=True)
        print("默认 profile：" + json.dumps(self.result["profile"], ensure_ascii=False), flush=True)
        print(f"== 动作清单（page.action(WebAction)，枚举 {len(list(QWebEnginePage.WebAction))} 项）==", flush=True)
        print(
            f"{'#':>3} {'枚举名':32s} {'present':7s} {'enabled':7s} {'visible':7s} "
            f"{'in_view':7s} {'shortcuts':14s} text",
            flush=True,
        )
        rows = []
        for idx, member in enumerate(QWebEnginePage.WebAction):
            name = member.name
            if name == "WebActionCount":
                # 计数哨兵值，不是真实动作；实测中对其调用 page.action() 会崩（见 R5-结论.md）
                print(f"{idx:>3} {name:32s} <哨兵值，跳过>", flush=True)
                rows.append({"index": idx, "enum": name, "value": int(member.value),
                             "present": False, "note": "计数哨兵，未调用 page.action()（会崩）"})
                continue
            action = self.page.action(member)
            if action is None:
                print(f"{idx:>3} {name:32s} None", flush=True)
                rows.append({"index": idx, "enum": name, "value": int(member.value), "present": False})
                continue
            self.actions_by_name[name] = action
            self.trigger_counts[name] = 0
            action.triggered.connect(
                lambda _c=False, n=name: self.trigger_counts.__setitem__(n, self.trigger_counts[n] + 1)
            )
            shortcuts = [s.toString() for s in action.shortcuts()]
            row = {
                "index": idx,
                "enum": name,
                "value": int(member.value),
                "present": True,
                "text": action.text(),
                "shortcuts": shortcuts,
                "enabled": action.isEnabled(),
                "visible": action.isVisible(),
                "checkable": action.isCheckable(),
                "auto_repeat": action.autoRepeat(),
                "shortcut_context": str(action.shortcutContext()),
                "in_view_actions": any(a is action for a in self.view.actions()),
                "object_name": action.objectName(),
            }
            rows.append(row)
            print(
                f"{idx:>3} {name:32s} {'yes':7s} {str(row['enabled']):7s} {str(row['visible']):7s} "
                f"{str(row['in_view_actions']):7s} {str(shortcuts):14s} {row['text']!r}",
                flush=True,
            )
        self.result["actions"] = rows
        print(f"有快捷键的 action 数：{sum(1 for r in rows if r.get('shortcuts'))}", flush=True)
        print(f"在 view.actions() 里的 action 数：{sum(1 for r in rows if r.get('in_view_actions'))}", flush=True)

        matrix = {}
        for key in CONFLICT_KEYS + EXTRA_KEYS + DESTRUCTIVE_KEYS:
            canonical = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)
            matrix[key] = [
                r["enum"] for r in rows
                if r.get("present") and canonical in [
                    QKeySequence(s).toString(QKeySequence.SequenceFormat.PortableText) for s in r["shortcuts"]
                ]
            ]
        self.result["conflict_matrix"] = matrix
        print("== 默认状态下被宿主动作占用的候选组合键 ==", flush=True)
        for k, v in matrix.items():
            print(f"  {k:16s} -> {v if v else '（无宿主动作占用）'}", flush=True)

    def install_js_logger(self) -> None:
        self.page.runJavaScript(JS_LOGGER)
        QTest.qWait(300)
        print("已注入渲染层 keydown/click 记录器", flush=True)

    # ── 激活 + 敏感性对照（决定本轮是否可测） ────────────────────────────
    def activate(self, timeout_ms: int = 6000) -> bool:
        view = self.view
        view.resize(900, 640)
        view.show()
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            view.raise_()
            view.activateWindow()
            wh = view.windowHandle()
            if wh is not None:
                wh.requestActivate()
            QTest.qWait(150)
            if view.isActiveWindow():
                break
        view.setFocus()
        QTest.qWait(300)
        print(
            f"激活结果：active={view.isActiveWindow()} focus={view.hasFocus()} "
            f"activeWindow={QApplication.activeWindow()}",
            flush=True,
        )
        return view.isActiveWindow()

    def run_control(self) -> bool:
        """敏感性对照（门禁）：手工给 SavePage 绑 Ctrl+S，按下去必须 triggered"""
        save = self.actions_by_name.get("SavePage")
        if save is None:
            self.result["control"] = {"error": "SavePage action 不存在"}
            return False
        if not any(a is save for a in self.view.actions()):
            self.view.addAction(save)
        save.setShortcut(QKeySequence("Ctrl+S"))
        print(f"敏感性对照：SavePage.shortcuts={[s.toString() for s in save.shortcuts()]}", flush=True)
        row = self.fire("Ctrl+S")
        row["download_requests"] = list(self.download_requests)
        self.result["control"]["sensitivity_with_shortcut"] = row
        print(
            f"  结果 triggered={row['action_triggered']} renderer_keys={row['renderer_keys']}",
            flush=True,
        )
        ok = bool(row["action_triggered"].get("SavePage"))
        self.result["harness"]["sensitivity_control_passed"] = ok
        if not ok:
            self.result["notes"].append(
                "敏感性对照失败：连手工绑定的 Ctrl+S 都没触发 ⇒ 本轮的'无反应'结论无效"
            )
        save.setShortcuts([])
        return ok

    # ── 三种关闭手段 + 直接触发 ──────────────────────────────────────────
    def run_mitigations(self) -> None:
        save = self.actions_by_name["SavePage"]
        print("--- 手段 A：setShortcuts([])（先确认能读到清空后的值）---", flush=True)
        save.setShortcut(QKeySequence("Ctrl+S"))
        save.setShortcuts([])
        after_clear = [s.toString() for s in save.shortcuts()]
        row_a = self.fire("Ctrl+S")
        self.result["mitigation"]["setShortcuts_empty"] = {"shortcuts_after": after_clear, "fire": row_a}
        print(f"  shortcuts_after={after_clear} triggered={row_a['action_triggered']}", flush=True)

        print("--- 手段 B：setEnabled(False)（快捷键恢复）---", flush=True)
        save.setShortcut(QKeySequence("Ctrl+S"))
        save.setEnabled(False)
        row_b = self.fire("Ctrl+S")
        self.result["mitigation"]["setEnabled_false"] = {
            "enabled_after": save.isEnabled(),
            "shortcut_restored": [s.toString() for s in save.shortcuts()],
            "fire": row_b,
        }
        print(f"  enabled={save.isEnabled()} triggered={row_b['action_triggered']}", flush=True)
        save.setEnabled(True)
        save.setShortcuts([])

        print("--- 手段 C：view.removeAction（快捷键恢复）---", flush=True)
        if not any(a is save for a in self.view.actions()):
            self.view.addAction(save)
        save.setShortcut(QKeySequence("Ctrl+S"))
        self.view.removeAction(save)
        row_c = self.fire("Ctrl+S")
        self.result["mitigation"]["view_removeAction"] = {
            "in_view_actions_after": any(a is save for a in self.view.actions()),
            "shortcut_after": [s.toString() for s in save.shortcuts()],
            "fire": row_c,
        }
        print(
            f"  in_view={any(a is save for a in self.view.actions())} "
            f"triggered={row_c['action_triggered']}",
            flush=True,
        )
        self.view.addAction(save)
        save.setShortcuts([])

        print("--- 手段 D：page.triggerAction(SavePage) 直接触发（不经键盘）---", flush=True)
        before = self.trigger_counts.get("SavePage", 0)
        downloads_before = len(self.download_requests)
        self.page.triggerAction(QWebEnginePage.WebAction.SavePage)
        QTest.qWait(800)
        closed = self.sweep_dialogs()
        QTest.qWait(200)
        self.result["mitigation"]["triggerAction_direct"] = {
            "triggered_delta": self.trigger_counts.get("SavePage", 0) - before,
            "new_top_levels": self.new_tops(),
            "dialogs_closed": closed,
            "download_requests_delta": len(self.download_requests) - downloads_before,
        }
        print(
            f"  triggered_delta={self.trigger_counts.get('SavePage', 0) - before} "
            f"tops={self.new_tops()} downloads={len(self.download_requests) - downloads_before}",
            flush=True,
        )

    def run_after_mitigation(self) -> None:
        applied = []
        for name in DISABLE_SET:
            action = self.actions_by_name.get(name)
            if action is None:
                continue
            action.setShortcuts([])
            action.setEnabled(False)
            applied.append(name)
        self.result["mitigation"]["applied_disable_set"] = applied
        print(f"--- 施加关闭集合（setShortcuts([])+setEnabled(False)）：{applied} ---", flush=True)
        self.run_sweep(CONFLICT_KEYS, "conflict_keys_after_mitigation")
        editing = {}
        for name in ["Copy", "Paste", "Undo", "Redo", "SelectAll", "Cut"]:
            action = self.actions_by_name.get(name)
            if action is not None:
                editing[name] = action.isEnabled()
        self.result["mitigation"]["editing_actions_after"] = editing
        print(f"  编辑类动作 enabled 状态：{editing}", flush=True)

    def run_os_channel(self) -> None:
        """OS 级注入通道：用于回答"渲染层能否收到键盘输入"（QTest 合成键的对照）"""
        print("--- OS 级按键通道（keybd_event）：渲染层键盘可达性 ---", flush=True)
        rows = []
        for label, _mods, _key in OS_KEYS:
            row = self.fire({"plain A": "A", "Ctrl+S": "Ctrl+S", "Ctrl+P": "Ctrl+P"}[label], use_os=True)
            rows.append(row)
            print(
                f"  {label:8s} channel={row['channel']} triggered={row['action_triggered']} "
                f"renderer_keys={row['renderer_keys']}",
                flush=True,
            )
        self.result["os_input"] = {"rows": rows}

    def check_renderer_mouse(self) -> None:
        """对照：鼠标点击能否到达渲染层（若能而键盘不能 ⇒ 是键盘通道问题）"""
        target = self.view.focusProxy() or self.view
        before = self.take_js_keys()["clicks"]
        QTest.mouseClick(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         target.rect().center())
        QTest.qWait(500)
        after = self.take_js_keys()["clicks"]
        self.result["harness"]["renderer_mouse_clicks"] = {"before": before, "after": after}
        print(f"渲染层鼠标对照：clicks {before} -> {after}", flush=True)

    def finalize(self) -> None:
        self.result["trigger_counts_total"] = {k: v for k, v in self.trigger_counts.items() if v}
        self.result["download_requests_total"] = list(self.download_requests)
        self.result["view_close_events_total"] = self.watcher.close_events
        self.result["final_new_top_levels"] = self.new_tops()


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 与 main.py:126 一致
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)  # 对话框可枚举/可关闭
    app.setApplicationName("JFLove")
    app.setOrganizationName("JFLove")

    probe = Probe(app)
    print(f"platform={app.platformName()}（请求 {PLATFORM}）", flush=True)

    def on_load(ok: bool) -> None:
        print(f"loadFinished ok={ok} focusProxy={type(probe.view.focusProxy()).__name__}", flush=True)
        probe.activate()
        probe.collect_api_and_actions()
        probe.install_js_logger()
        probe.check_renderer_mouse()
        valid = probe.run_control()
        probe.result["harness"]["harness_valid_for_dynamic_claims"] = valid
        probe.run_sweep(CONFLICT_KEYS, "conflict_keys_default")
        probe.run_sweep(EXTRA_KEYS, "extra_keys_default")
        probe.run_mitigations()
        probe.run_os_channel()
        probe.run_after_mitigation()
        if probe.watcher.close_events == 0:
            probe.run_sweep(DESTRUCTIVE_KEYS, "destructive_keys_default")
        else:
            probe.result["notes"].append("视图已被关闭，跳过破坏性键扫描")
        probe.finalize()
        path = OUT_DIR / f"r5-{PLATFORM}.json"
        path.write_text(json.dumps(probe.result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入 {path}", flush=True)
        app.quit()

    page_file = OUT_DIR / "r5-page.html"
    page_file.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>r5 probe</title></head>"
        "<body><p>r5 probe page</p><input id='x' value='abc'></body></html>",
        encoding="utf-8",
    )
    probe.view.loadFinished.connect(on_load)
    probe.view.setUrl(QUrl.fromLocalFile(str(page_file)))

    def watchdog() -> None:
        print("!!! 看门狗超时，强制退出（写出部分结果）", flush=True)
        try:
            probe.finalize()
            (OUT_DIR / f"r5-{PLATFORM}.json").write_text(
                json.dumps(probe.result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        finally:
            app.quit()

    QTimer.singleShot(300000, watchdog)
    rc = app.exec()
    print("exit", rc, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())

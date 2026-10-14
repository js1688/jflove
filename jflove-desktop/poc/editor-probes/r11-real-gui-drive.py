"""
r11 真机自动化复核（Phase 6 · P6-5）

## 这个探针解决什么

设计 §14.3 里有一批验收标准**在 jsdom 里做不出可信判据**：

- AC-13 列选择的**几何结果**：jsdom 没有 `Range.getClientRects`，CM6 的测量路径会
  抛 `TypeError: ...getClientRects is not a function`（见 V3 子代理报告），
  所以「矩形选择到底选中了哪几行哪几列」只能在真渲染层上验。
- AC-35 `Ctrl+滚轮` 调字号 / `Shift+滚轮` 横向滚动：需要真 `WheelEvent`
  走到宿主外壳上**非 passive** 的监听，并且要等 React 重渲染后才能读 `computedStyle`。
- R-1（AC-10 的真机半边）：OS 级 `Ctrl+S` 有没有真的到达渲染层、有没有被
  `preventDefault`、宿主有没有偷偷弹保存对话框 —— 必须是**真窗口 + OS 级按键**。
- AC-42 ②（打开文件不依赖登录、零远端）：真机运行态下**整轮无 http(s) 请求**。

与 r5 的关系：r5 已经证明 **Qt 合成事件（`QTest.keyClick`）到不了渲染层**，
而 **OS 级输入**（`keybd_event`）能到。本探针沿用 r5 的 OS 级按键手法，
但把它接到**真实外壳接线**（`Bridge` + `WebShell` + `load_ui()`）上，
并用 `runJavaScript` + 标题通道取渲染层真值、`grab()` 截图留证。

## 两条实测出来的探针经验（踩过坑，别再踩）

1. **`runJavaScript(script, callback)` 在本环境回调恒为空串**（外壳加载正常、桥调用
   正常、请求记录正常，只有回调值是 `""`）⇒ 改用**标题通道**：JS 侧
   `JSON.stringify` 结果写进 `document.title`，Python 侧轮询 `page().title()`。
   每次调用带自增标记，杜绝读到陈旧值。
2. **同一次 `runJavaScript` 里同步派发事件 + 同步读 `computedStyle` 必然读到旧值**：
   React 状态更新要等下一次渲染。凡是「派发 → 观察」的判据都必须**分两次调用**
   并在中间 `QTest.qWait`（H3/H4 就是这么写的）。
   同理，`paneRef.current` 在一次同步循环里不会更新 —— 连续 40 次滚轮只会按同一个
   基准算一次，所以步进/夹取判据要**逐次派发 + 逐次等待**。

## 破坏性说明

H3 会**真按键**（`Alt+Shift+方向键`），这会（在缺陷未修时）真的改文档内容，
随后被自动保存写回磁盘。本探针只在自己的 `out/<run>/fixtures/` 副本上做这些事，
**绝不触碰仓库里的其它文件**；每次运行都会重写 fixture（幂等）。

## 怎么跑

    jflove-desktop\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r11-real-gui-drive.py
    ... --offscreen      # 无显示环境：AC-13 / OS 级按键标 SKIP

## 退出码

- `0` 必须通过的判据全过（SKIP 不算失败；观察项不影响退出码）
- `1` 有必须通过的判据失败
- `2` 环境/脚本错误（UI 未加载、QtWebEngine 起不来）
- `3` 看门狗超时

## 边界（刻意不做）

- **不启动单实例守卫**：否则本机会把路径转交给用户正在运行的实例，探针自己拿不到窗口。
- **不建托盘、不建媒体浮层**：与编辑器链路无关；托盘/退出类判据归 r10。
- **不改用户真实用户目录**：`APPDATA` / `LOCALAPPDATA` 被指到本次 `out/` 沙箱
  （必须在导入 `src.config.settings` 之前设置），草稿/快捷键/日志全落在沙箱里。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
DESKTOP = SCRIPT.parents[2]
REPO = SCRIPT.parents[3]

# Windows 控制台是 GBK：日志里有 ⇒/· 等字符，不重配置会 UnicodeEncodeError 中断探针
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

sys.path.insert(0, str(DESKTOP))


# ─────────────────────────── 判定与留证 ───────────────────────────


class Report:
    """收集每条判据的结果，最后写 JSON + 决定退出码。"""

    def __init__(self, out_dir: Path) -> None:
        self.out = out_dir
        self.checks: list[dict] = []
        self.notes: list[str] = []

    def say(self, message: str) -> None:
        print(message, flush=True)

    def note(self, message: str) -> None:
        self.notes.append(message)
        self.say(f"  · {message}")

    def add(self, cid: str, desc: str, status: str, detail, gating: bool = True) -> None:
        """status: PASS / FAIL / SKIP（SKIP 同样不阻塞退出码）。"""
        record = {"id": cid, "desc": desc, "status": status, "gating": gating, "detail": detail}
        self.checks.append(record)
        mark = {"PASS": "[ OK ]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[status]
        suffix = "" if gating else " （观察项，不参与退出码）"
        self.say(f"{mark} {cid} {desc}{suffix}")
        if detail not in (None, {}, []):
            self.say(f"       {json.dumps(detail, ensure_ascii=False)[:600]}")

    def verdict(self) -> int:
        failed = [c for c in self.checks if c["gating"] and c["status"] == "FAIL"]
        return 1 if failed else 0

    def write(self, extra: dict) -> Path:
        payload = {
            "probe": "r11-real-gui-drive",
            "at": datetime.now().isoformat(timespec="seconds"),
            "checks": self.checks,
            "notes": self.notes,
            **extra,
        }
        path = self.out / "r11-result.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# ─────────────────────────── 渲染层脚本 ───────────────────────────

# 每个片段都自包含：runJavaScript 之间不共享作用域，故把取窗格的辅助函数内联进去。
_PANE_HELPER = """
  var r11pane = function (title) {
    // 标题栏与 `data-view-mode` 不在同一层：从标题元素向上找最近含 `.cm-editor` 的祖先
    var titles = document.querySelectorAll('.scratch-title-name');
    for (var i = 0; i < titles.length; i++) {
      if (titles[i].textContent !== title) continue;
      var node = titles[i];
      while (node && node !== document.body) {
        if (node.querySelector && node.querySelector('.cm-editor')) {
          return {root: node, content: node.querySelector('.cm-content'),
                  editor: node.querySelector('.cm-editor'),
                  scroller: node.querySelector('.cm-scroller')};
        }
        node = node.parentElement;
      }
    }
    return null;
  };
"""

JS_PANE_STATE = r"""
(function () {
  try {
    var panes = document.querySelectorAll('[data-view-mode]');
    var host = document.querySelector('.scratch-pane-host');
    var cm = document.querySelector('.cm-editor');
    return {
      paneCount: panes.length,
      modes: Array.prototype.map.call(panes, function (p) { return p.getAttribute('data-view-mode'); }),
      bodies: Array.prototype.map.call(document.querySelectorAll('[data-pane-body]'),
                                       function (p) { return p.getAttribute('data-pane-body'); }),
      titles: Array.prototype.map.call(document.querySelectorAll('.scratch-title-name'),
                                       function (t) { return t.textContent; }),
      hostHidden: host ? host.hasAttribute('hidden') : null,
      auxCount: document.querySelectorAll('.scratch-pane-aux').length,
      cmEditor: !!cm,
      cmContent: !!document.querySelector('.cm-content'),
      bundleLoaded: typeof window.__JFLOVE_CM__ !== 'undefined',
      focused: document.hasFocus(),
      hidden: document.hidden,
      bodyText: (document.body ? document.body.innerText.slice(0, 160) : '')
    };
  } catch (e) { return {r11Error: String(e)}; }
})()
"""

JS_INSTALL_KEY_LOG = r"""
(function () {
  try {
    window.__r11 = { keys: [] };
    window.addEventListener('keydown', function (e) {
      window.__r11.keys.push({key: e.key, ctrl: e.ctrlKey, alt: e.altKey, shift: e.shiftKey,
                              phase: 'capture', defaultPrevented: e.defaultPrevented});
    }, true);
    window.addEventListener('keydown', function (e) {
      window.__r11.keys.push({key: e.key, ctrl: e.ctrlKey, alt: e.altKey, shift: e.shiftKey,
                              phase: 'bubble', defaultPrevented: e.defaultPrevented});
    }, false);
    return {ok: true};
  } catch (e) { return {r11Error: String(e)}; }
})()
"""

JS_READ_KEY_LOG = r"""
(function () {
  try {
    var r = window.__r11 || {keys: []};
    return {keys: r.keys, focused: document.hasFocus(), hidden: document.hidden};
  } catch (e) { return {r11Error: String(e)}; }
})()
"""

JS_CLEAR_KEY_LOG = """
(function () { if (window.__r11) { window.__r11.keys = []; } return true; })()
"""


def js_rect_key(title: str, key: str, alt: bool, shift: bool) -> str:
    """在指定窗格派发**一个**合成方向键，并回传该次按键后的文档状态。

    逐次派发（而不是一次循环）是为了拿到「哪一个键改了文档」的精确证据。
    """
    return (
        "(function() {" + _PANE_HELPER + """
      try {
        var api = window.__JFLOVE_CM__;
        if (!api) return {r11Error: 'no-bundle'};
        var p = r11pane(%s);
        if (!p || !p.content) return {r11Error: 'no-pane'};
        var view = api.EditorView.findFromDOM(p.content);
        if (!view) return {r11Error: 'no-view'};
        var ev = new KeyboardEvent('keydown', {key: %s, code: %s, altKey: %s, shiftKey: %s,
                                              bubbles: true, cancelable: true});
        p.content.dispatchEvent(ev);
        return {prevented: ev.defaultPrevented, docLength: view.state.doc.length,
                lines: view.state.doc.lines,
                head: view.state.selection.main.head, hasFocus: view.hasFocus};
      } catch (e) { return {r11Error: String(e)}; }
    })()
    """ % (json.dumps(title), json.dumps(key), json.dumps(key),
           "true" if alt else "false", "true" if shift else "false")
    )


def js_rect_prepare(title: str) -> str:
    """把光标放到第 2 行第 4 列（0 基 [3]），并聚焦编辑器。"""
    return (
        "(function() {" + _PANE_HELPER + """
      try {
        var api = window.__JFLOVE_CM__;
        if (!api) return {r11Error: 'no-bundle'};
        var p = r11pane(%s);
        if (!p || !p.content) return {r11Error: 'no-pane'};
        var view = api.EditorView.findFromDOM(p.content);
        if (!view) return {r11Error: 'no-view'};
        view.focus();
        var line2 = view.state.doc.line(2);
        view.dispatch({selection: {anchor: line2.from + 3}});
        return {ok: true, docLength: view.state.doc.length, lines: view.state.doc.lines,
                anchor: view.state.selection.main.anchor, hasFocus: view.hasFocus};
      } catch (e) { return {r11Error: String(e)}; }
    })()
    """ % json.dumps(title)
    )


def js_rect_read(title: str) -> str:
    """读列选区的**真几何**：每个 range 的行号/列偏移/切片文本 + 文档各行真值。"""
    return (
        "(function() {" + _PANE_HELPER + """
      try {
        var api = window.__JFLOVE_CM__;
        var p = r11pane(%s);
        if (!p || !p.content) return {r11Error: 'no-pane'};
        var view = api.EditorView.findFromDOM(p.content);
        var doc = view.state.doc;
        var sel = view.state.selection;
        var ranges = [];
        for (var n = 0; n < sel.ranges.length; n++) {
          var r = sel.ranges[n];
          ranges.push({from: r.from, to: r.to, anchor: r.anchor, head: r.head,
                       fromLine: doc.lineAt(r.from).number, toLine: doc.lineAt(r.to).number,
                       fromCol: r.from - doc.lineAt(r.from).from,
                       toCol: r.to - doc.lineAt(r.to).from,
                       text: doc.sliceString(r.from, r.to)});
        }
        return {rangeCount: sel.ranges.length, ranges: ranges, docLength: doc.length,
                lines: doc.lines, lineTexts: doc.toString().split('\\n')};
      } catch (e) { return {r11Error: String(e)}; }
    })()
    """ % json.dumps(title)
    )


def js_keymap_truth() -> str:
    """离线包里到底有没有「列/矩形」命令与 copyLineDown（AC-13 缺口的根因取证）。"""
    return """
    (function() {
      var api = window.__JFLOVE_CM__;
      if (!api) return {error: 'no-bundle'};
      var cmds = (api.modules && api.modules.commands) || {};
      var names = Object.keys(cmds);
      var byName = function (re) {
        return names.filter(function (n) { return re.test(n); }).sort();
      };
      return {hasCopyLineDown: typeof cmds.copyLineDown === 'function',
              copyMoveNames: byName(/^(copy|move)Line/i),
              rectColumnNames: byName(/rect|column|block/i),
              hasRectSelectionExtension: !!(api.extensions && api.extensions.rectangularSelection)};
    })()
    """


def js_pos_css(title: str, line: int, col: int) -> str:
    """文档位置 (行, 列) 在页面 CSS 坐标下的矩形（供真鼠标拖拽定位）。"""
    return (
        "(function() {" + _PANE_HELPER + """
      try {
        var api = window.__JFLOVE_CM__;
        var p = r11pane(%s);
        if (!p || !p.content) return {r11Error: 'no-pane'};
        var view = api.EditorView.findFromDOM(p.content);
        var doc = view.state.doc;
        if (%d > doc.lines) return {r11Error: 'line-out-of-range', lines: doc.lines};
        var ln = doc.line(%d);
        var pos = Math.min(ln.from + %d, ln.to);
        var c = view.coordsAtPos(pos);
        var box = p.content.getBoundingClientRect();
        return {left: c.left, top: c.top, bottom: c.bottom,
                contentLeft: box.left, contentTop: box.top,
                dpr: window.devicePixelRatio,
                lines: doc.lines, docLength: doc.length,
                text: doc.lineAt(pos).text};
      } catch (e) { return {r11Error: String(e)}; }
    })()
    """ % (json.dumps(title), line, line, col)
    )


def js_font_px(title: str) -> str:
    """读指定窗格 `.cm-editor` 的计算字号（主题把 fontSize 挂在编辑器根上）。"""
    return (
        "(function() {" + _PANE_HELPER + """
      try {
        var p = r11pane(%s);
        if (!p || !p.editor) return {r11Error: 'no-pane'};
        return {px: getComputedStyle(p.editor).fontSize};
      } catch (e) { return {r11Error: String(e)}; }
    })()
    """ % json.dumps(title)
    )


def js_wheel_once(title: str, delta: int, ctrl: bool, shift: bool) -> str:
    """派发**一个**真 WheelEvent 到窗格外壳（宿主监听是 non-passive）。"""
    return (
        "(function() {" + _PANE_HELPER + """
      try {
        var p = r11pane(%s);
        if (!p || !p.editor) return {r11Error: 'no-pane'};
        var wrapper = p.editor.parentElement || p.editor;
        var ev = new WheelEvent('wheel', {deltaY: %d, ctrlKey: %s, shiftKey: %s,
                                          bubbles: true, cancelable: true});
        wrapper.dispatchEvent(ev);
        return {prevented: ev.defaultPrevented};
      } catch (e) { return {r11Error: String(e)}; }
    })()
    """ % (json.dumps(title), delta, "true" if ctrl else "false", "true" if shift else "false")
    )


def js_scroll_left(title: str) -> str:
    return (
        "(function() {" + _PANE_HELPER + """
      try {
        var p = r11pane(%s);
        if (!p || !p.scroller) return {r11Error: 'no-pane'};
        return {left: p.scroller.scrollLeft, width: p.scroller.scrollWidth,
                client: p.scroller.clientWidth};
      } catch (e) { return {r11Error: String(e)}; }
    })()
    """ % json.dumps(title)
    )


JS_SYNTHETIC_PANE_NEW = r"""
(function () {
  try {
    var ev = new KeyboardEvent('keydown', {key: 'n', code: 'KeyN', ctrlKey: true,
                                           altKey: true, bubbles: true, cancelable: true});
    window.dispatchEvent(ev);
    return {defaultPrevented: ev.defaultPrevented,
            paneCount: document.querySelectorAll('[data-view-mode]').length};
  } catch (e) { return {r11Error: String(e)}; }
})()
"""


# ─────────────────────────── OS 级按键 ───────────────────────────

_VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "s": 0x53, "n": 0x4E, "p": 0x50,
       "k": 0x4B, "c": 0x43}
_VK_MODS = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}
_KEYEVENTF_KEYUP = 0x0002


def os_release_modifiers() -> None:
    """先清掉可能残留的修饰键。

    上一轮 `keybd_event` 若丢了 keyup（例如进程被抢焦点打断），残留的修饰键会
    污染后续组合键 —— 实测曾出现 Ctrl+Alt+N 在渲染层被记成 `shift: true`。
    """
    if not sys.platform.startswith("win"):
        return
    try:
        user32 = ctypes.windll.user32
        for vk in _VK_MODS.values():
            user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
    except Exception:  # noqa: BLE001 - 仅诊断/清场，失败不致命
        return


def os_mod_state() -> dict:
    """当前 OS 层修饰键物理状态（诊断证据，进报告 JSON）。"""
    if not sys.platform.startswith("win"):
        return {}
    try:
        user32 = ctypes.windll.user32
        return {name: bool(user32.GetAsyncKeyState(vk) & 0x8000)
                for name, vk in _VK_MODS.items()}
    except Exception:  # noqa: BLE001
        return {}


def os_press(mods: list[str], key: str) -> bool:
    """真·OS 级按键（`keybd_event`）；非 Windows 返回 False（调用方标 SKIP）。

    按键之间留出小间隔：瞬时连发的组合键偶发丢键（Chromium 侧收不到 keydown）。
    """
    if not sys.platform.startswith("win"):
        return False
    user32 = ctypes.windll.user32
    for mod in mods:
        user32.keybd_event(_VK[mod], 0, 0, 0)
        time.sleep(0.03)
    user32.keybd_event(_VK[key], 0, 0, 0)
    time.sleep(0.06)
    user32.keybd_event(_VK[key], 0, _KEYEVENTF_KEYUP, 0)
    time.sleep(0.03)
    for mod in reversed(mods):
        user32.keybd_event(_VK[mod], 0, _KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)
    return True


def sha256_of(path: Path, retries: int = 8, delay: float = 0.25) -> dict:
    """读目标文件的摘要。

    Windows 上原子替换（`os.replace`）的瞬间，目标文件会短暂处于 delete-pending
    状态 ⇒ 读取抛 `PermissionError`（实测在「键位改文档 + 自动保存落盘」时命中）。
    这里容忍并重试；重试次数进返回体，作为「探针与写回并发」的证据。
    """
    if not path.exists():
        return {"exists": False}
    last = ""
    for attempt in range(retries + 1):
        try:
            data = path.read_bytes()
            out = {"exists": True, "size": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()[:16]}
            if attempt:
                out["retriedReads"] = attempt
            return out
        except PermissionError as exc:  # 原子替换瞬间的 delete-pending
            last = str(exc)
            time.sleep(delay)
    return {"exists": True, "unreadable": last}


# ─────────────────────────── 探针主体 ───────────────────────────


class R11:
    def __init__(self, report: Report, shell, out_dir: Path, real_window: bool) -> None:
        self.report = report
        self.shell = shell
        self.out = out_dir
        self.real_window = real_window
        self.downloads: list[str] = []
        self._seq = 0

    # ── 基础设施 ──
    def js(self, expr: str, timeout_ms: int = 4000):
        """在渲染层求值 `expr` 并经**标题通道**取回结果（见模块文档「经验 1」）。"""
        from PySide6.QtTest import QTest

        self._seq += 1
        marker = f"R11#{self._seq}"
        payload = (
            "(function(){var __v;try{__v=(" + expr + ");}catch(e){__v={r11Error:String(e)};}"
            "document.title='" + marker + "::'+JSON.stringify(__v===undefined?null:__v);"
            "return true;})()"
        )
        page = self.shell.view.page()
        page.runJavaScript(payload)
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            title = page.title()
            if title.startswith(marker + "::"):
                raw = title[len(marker) + 2:]
                try:
                    return json.loads(raw)
                except ValueError:
                    return {"r11Error": "bad-json", "raw": raw[:200]}
            QTest.qWait(40)
        return {"r11Error": "title-timeout", "title": page.title()[:120]}

    def wait_for(self, expr: str, predicate, timeout_ms: int = 25000, interval_ms: int = 250):
        from PySide6.QtTest import QTest

        deadline = time.time() + timeout_ms / 1000
        last = None
        while time.time() < deadline:
            last = self.js(expr, timeout_ms=3000)
            if isinstance(last, dict) and predicate(last):
                return last
            QTest.qWait(interval_ms)
        return last

    def shot(self, name: str) -> str:
        path = self.out / f"r11-{name}.png"
        self.shell.view.grab().save(str(path))
        return path.name

    def activate(self) -> None:
        """把探针窗口抢到前台（Windows 默认禁止后台进程抢焦点）。"""
        from PySide6.QtTest import QTest

        self.shell.show()
        self.shell.raise_()
        self.shell.activateWindow()
        if sys.platform.startswith("win"):
            try:
                user32 = ctypes.windll.user32
                user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
                hwnd = int(self.shell.winId())
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            except Exception as exc:  # noqa: BLE001 - 抢焦点失败只影响 H5
                self.report.note(f"抢前台失败：{exc}")
        QTest.qWait(600)

    def open_path(self, path: Path, title: str, expect_min: int = 1,
                  need_editor: bool = False) -> dict:
        """经生产通路推路径（`shell.notify_external_paths`）并等窗格出现。

        `need_editor=True` 时额外等真 CM6 挂载完成（离线包是**懒加载**的，
        窗格出现 ≠ 编辑器就绪 —— 直接断言 cmEditor 会抖）。
        """
        self.shell.notify_external_paths([str(path)])

        def ready(state: dict) -> bool:
            if state.get("paneCount", 0) < expect_min or title not in (state.get("titles") or []):
                return False
            return bool(state.get("cmEditor")) if need_editor else True

        return self.wait_for(JS_PANE_STATE, ready,
                             timeout_ms=20000 if need_editor else 12000) or {}

    # ── H1：外壳与真实 UI ──
    def h1_shell(self, load_ok: bool) -> dict:
        from PySide6.QtTest import QTest

        QTest.qWait(1200)
        ready = self.wait_for(
            JS_PANE_STATE,
            lambda v: bool(v.get("bodyText")) or v.get("paneCount", 0) > 0,
            timeout_ms=15000,
        )
        ok = bool(load_ok) and isinstance(ready, dict) and "r11Error" not in ready
        self.report.add(
            "H1", "真外壳加载前端产物并渲染出 DOM（预置：无窗格、未登录）",
            "PASS" if ok and ready.get("paneCount") == 0 else "FAIL",
            {"load_ui": load_ok, "state": ready, "renderer_ready": self.shell._is_renderer_ready()},
        )
        return ready or {}

    # ── H2：打开文件 → 默认编辑视图（M-13）+ 真 CM6 挂载 ──
    def h2_open(self, fixture: Path) -> bool:
        state = self.open_path(fixture, "wide.txt", need_editor=True)
        ok = (
            state.get("paneCount", 0) >= 1
            and state.get("cmEditor") is True
            and (state.get("modes") or [None])[0] == "edit"
            and (state.get("bodies") or [None])[0] == "edit"
            and state.get("hostHidden") is False
            and state.get("auxCount") == 0
        )
        self.report.add(
            "H2", "notify_external_paths → 窗格出现且 viewMode=edit（M-13）+ 真 CM6 挂载",
            "PASS" if ok else "FAIL", state,
        )
        self.report.say(f"       截图 {self.shot('h2-editor')}")
        return bool(ok)

    # ── H3：AC-13 真几何 + 「键位是否真的改了文档」的逐步证据 ──
    RECT_TITLE = "rect.txt"

    def h3_rectangle(self, fixture: Path, fixture_text: str) -> bool:
        if not self.real_window:
            self.report.add("H3", "AC-13 列选择真几何（矩形跨行同列）", "SKIP",
                            "offscreen：Chromium 取不到真实测量矩形，几何结论不可信")
            return False
        before = sha256_of(fixture)
        self.open_path(fixture, self.RECT_TITLE, expect_min=1)
        from PySide6.QtTest import QTest

        QTest.qWait(500)
        prep = self.js(js_rect_prepare(self.RECT_TITLE)) or {}
        steps = [{"step": "prepare", **prep}]
        for index in range(4):
            step = self.js(js_rect_key(self.RECT_TITLE, "ArrowRight", alt=True, shift=True)) or {}
            steps.append({"step": f"right{index + 1}", **step})
        for index in range(2):
            step = self.js(js_rect_key(self.RECT_TITLE, "ArrowDown", alt=True, shift=True)) or {}
            steps.append({"step": f"down{index + 1}", **step})
        geom = self.js(js_rect_read(self.RECT_TITLE)) or {}
        verdict, detail = self._judge_rectangle(geom, fixture_text, steps)
        detail["keymapTruth"] = self.js(js_keymap_truth())
        self.report.add("H3", "AC-13 列选择真几何（矩形跨行同列）",
                        "PASS" if verdict else "FAIL", detail)
        self.report.say(f"       截图 {self.shot('h3-rect')}")

        # 等自动保存落盘（防抖 1500 ms + 草稿写 + 写回，实测最慢约 5 s）
        after = sha256_of(fixture)
        waited = 0.0
        while waited < 9.0 and after.get("sha256") == before.get("sha256"):
            QTest.qWait(300)
            waited += 0.3
            after = sha256_of(fixture)
        mutated = [s for s in steps if isinstance(s.get("docLength"), int)
                   and s["docLength"] != fixture_text_len(fixture_text)]
        changed_on_disk = before.get("sha256") != after.get("sha256")
        self.report.add(
            "H3b",
            "观察：AC-13 键位是否改动了文档、并被自动保存写回磁盘",
            "FAIL" if (mutated and changed_on_disk) else "PASS",
            {"before": before, "after": after, "docMutatingSteps": mutated,
             "expectedDocLength": fixture_text_len(fixture_text),
             "note": "**本项只判「磁盘面」**（审查 L5）：FAIL 的条件是"
                     "「内存文档被改动 **且** 磁盘 sha256 变化」；只改内存不改磁盘仍会 PASS。"
                     "docMutatingSteps=按 Alt+Shift+方向键时被改动了文档的步骤（修复前该键位落到 "
                     "defaultKeymap 的 copyLineDown/Up，每次 +2 行）；"
                     "AC-13 是否真的按列扩展由 gating 的 H3（_judge_rectangle）判定；"
                     "before/after 的 sha256 相同 ⇒ 没被自动保存写回（修复后应有的状态）"},
            gating=False,
        )
        return bool(verdict)

    @staticmethod
    def _judge_rectangle(geom, fixture_text: str, steps: list[dict]) -> tuple[bool, dict]:
        """判据：选区是**跨行矩形** —— 每行取到等宽、同起始列的片段。"""
        lines = fixture_text.split("\n")
        if not isinstance(geom, dict) or geom.get("r11Error"):
            return False, {"error": (geom or {}).get("r11Error", "no-result"),
                           "docLengths": [s.get("docLength") for s in steps]}
        ranges = geom.get("ranges") or []
        info = {"rangeCount": geom.get("rangeCount"), "docLength": geom.get("docLength"),
                "ranges": ranges, "docLengths": [s.get("docLength") for s in steps],
                "prevented": [s.get("prevented") for s in steps]}
        if not ranges:
            return False, {**info, "why": "选区为空：Alt+Shift+方向键没有建立列选择"}
        if geom.get("rangeCount") == 1:
            parts = (ranges[0].get("text") or "").split("\n")
            if len(parts) < 2:
                return False, {**info, "why": "选区未跨行（退化成流式选择）",
                               "text": ranges[0].get("text")}
            widths = {len(p) for p in parts}
            if len(widths) != 1:
                return False, {**info, "why": f"各行宽度不一致：{sorted(widths)}"}
            first = ranges[0]["fromLine"]
            start_col = lines[first - 1].find(parts[0]) if first - 1 < len(lines) else -1
            aligned = start_col >= 0 and all(
                first - 1 + idx < len(lines)
                and lines[first - 1 + idx][start_col:start_col + len(parts[0])] == part
                for idx, part in enumerate(parts)
            )
            info.update({"shape": "single-range", "startCol": start_col, "aligned": aligned,
                         "text": ranges[0].get("text")})
            return bool(aligned), info
        cols = {(r["fromCol"], r["toCol"]) for r in ranges}
        widths = {r["toCol"] - r["fromCol"] for r in ranges}
        line_numbers = [r["fromLine"] for r in ranges]
        consecutive = line_numbers == list(range(line_numbers[0], line_numbers[0] + len(ranges)))
        aligned = all(
            r["fromLine"] - 1 < len(lines)
            and lines[r["fromLine"] - 1][r["fromCol"]:r["toCol"]] == r["text"]
            for r in ranges
        )
        info.update({"shape": "multi-range", "colOffsets": sorted(cols), "widths": sorted(widths),
                     "consecutive": consecutive, "aligned": aligned})
        return bool(len(cols) == 1 and len(widths) == 1 and consecutive and aligned), info

    # ── H4：AC-35 Ctrl+滚轮（逐次派发 + 逐次等待）──
    def h4_wheel(self, long_fixture: Path) -> bool:
        from PySide6.QtTest import QTest

        def font_px(title: str):
            raw = (self.js(js_font_px(title)) or {}).get("px")
            try:
                return int(str(raw).replace("px", "").strip())
            except (TypeError, ValueError):
                return None

        def wheel(title: str, delta: int, ctrl: bool = False, shift: bool = False) -> dict:
            result = self.js(js_wheel_once(title, delta, ctrl, shift)) or {}
            QTest.qWait(260)  # 等 React 重渲染（见模块文档「经验 2」）
            return result

        base = font_px("wide.txt")
        one_up = wheel("wide.txt", -120, ctrl=True)
        after_one = font_px("wide.txt")
        first_up = after_one  # 第一次滚轮后的字号（判据只看这一步；after_one 随后被循环改写）
        trace_up = [after_one]
        for _ in range(20):
            if after_one is not None and after_one >= 40:
                break
            wheel("wide.txt", -120, ctrl=True)
            after_one = font_px("wide.txt")
            trace_up.append(after_one)
        overshoot = wheel("wide.txt", -120, ctrl=True)
        after_over = font_px("wide.txt")

        trace_down = [after_over]
        current = after_over
        for _ in range(24):
            if current is not None and current <= 8:
                break
            wheel("wide.txt", 120, ctrl=True)
            current = font_px("wide.txt")
            trace_down.append(current)
        wheel("wide.txt", 120, ctrl=True)
        floor = font_px("wide.txt")

        self.open_path(long_fixture, "long.txt", expect_min=2)
        QTest.qWait(600)
        scroll_before = self.js(js_scroll_left("long.txt")) or {}
        shift_evt = wheel("long.txt", 160, shift=True)
        scroll_after = self.js(js_scroll_left("long.txt")) or {}
        delta_left = (scroll_after.get("left") or 0) - (scroll_before.get("left") or 0)

        step_ok = base is not None and first_up == base + 2
        max_ok = max([v for v in trace_up if v is not None] or [0]) == 40
        floor_ok = floor == 8
        ctrl_prevented = bool(one_up.get("prevented")) and bool(overshoot.get("prevented"))
        shift_ok = bool(shift_evt.get("prevented")) and delta_left > 0
        ok = step_ok and max_ok and floor_ok and ctrl_prevented and shift_ok
        self.report.add(
            "H4", "AC-35 Ctrl+滚轮步进 2 且夹取 [8,40]；Shift+滚轮横向滚动",
            "PASS" if ok else "FAIL",
            {"base": base, "afterOneUp": first_up, "traceUp": trace_up[:20],
             "afterOvershoot": after_over, "traceDown": trace_down[:26], "floor": floor,
             "ctrlPrevented": ctrl_prevented, "shiftPrevented": shift_evt.get("prevented"),
             "scrollBefore": scroll_before, "scrollAfter": scroll_after,
             "deltaLeft": delta_left, "stepOk": step_ok, "maxOk": max_ok, "floorOk": floor_ok},
        )
        self.report.say(f"       截图 {self.shot('h4-font')}")
        return bool(ok)

    # ── H5：OS 级按键（R-1 与 AC-10 阳性对照）──
    def h5_os_keys(self) -> bool:
        from PySide6.QtTest import QTest

        if not self.real_window or not sys.platform.startswith("win"):
            self.report.add("H5", "OS 级按键到达渲染层（R-1 / AC-10 阳性对照）", "SKIP",
                            "需要真窗口 + Windows：合成事件到不了渲染层（r5 已证）")
            return False
        self.activate()
        self.js(JS_INSTALL_KEY_LOG)
        state = self.js(JS_PANE_STATE) or {}
        if not state.get("focused"):
            self.report.add("H5", "OS 级按键到达渲染层（R-1 / AC-10 阳性对照）", "SKIP",
                            {"why": "窗口未取得前台焦点 ⇒ 应用级监听按 AC-8 自查让路",
                             "state": {k: state.get(k) for k in ("paneCount", "focused", "hidden")}})
            return False

        def press(mods: list[str], key: str, wanted: str) -> tuple[list[dict], int, dict]:
            """按一次 OS 级组合键；收不到就重新抢前台再按一次（前台焦点存在竞争）。"""
            diag: dict = {"attempts": []}
            for attempt in (1, 2):
                self.activate()
                self.shell.view.setFocus()
                QTest.qWait(150)
                os_release_modifiers()
                QTest.qWait(80)
                before_mods = os_mod_state()
                self.js(JS_CLEAR_KEY_LOG)
                os_press(mods, key)
                QTest.qWait(900)
                log = self.js(JS_READ_KEY_LOG) or {}
                raw = log.get("keys") or []
                diag["attempts"].append({"n": attempt, "beforeMods": before_mods,
                                         "afterMods": os_mod_state(), "all": raw})
                found = [k for k in raw if str(k.get("key", "")).lower() == wanted]
                if found:
                    diag["hit"] = attempt
                    return found, attempt, diag
            diag["hit"] = 0
            return [], 2, diag

        # 阳性对照：Ctrl+Alt+N = pane.new（本轮已实现），必须被应用级监听接管。
        # 注意：AC-10 要求 preventDefault **且 stopPropagation**，所以 bubble 阶段
        # 的记录可能根本收不到 —— 判据看「任一阶段 defaultPrevented」+「窗格数 +1」。
        pane_before = state.get("paneCount", 0)
        n_keys, n_attempts, n_diag = press(["ctrl", "alt"], "n", "n")
        panes_after = (self.js(JS_PANE_STATE) or {}).get("paneCount", 0)
        positive = bool(n_keys) and any(k.get("defaultPrevented") for k in n_keys) \
            and panes_after > pane_before
        self.report.add(
            "H5a", "OS 级 Ctrl+Alt+N → 渲染层收到并 preventDefault，且新建窗格（阳性对照）",
            "PASS" if positive else "FAIL",
            {"paneBefore": pane_before, "paneAfter": panes_after, "keys": n_keys,
             "attempts": n_attempts, "diag": n_diag},
        )

        # R-1 本体：Ctrl+S 必须到达渲染层；宿主不得自行弹保存对话框/发起下载
        s_keys, s_attempts, s_diag = press(["ctrl"], "s", "s")
        reached = bool(s_keys)
        prevented = any(k.get("defaultPrevented") for k in s_keys)
        host_quiet = not self.downloads
        self.report.add(
            "H5b", "R-1：OS 级 Ctrl+S 到达渲染层且宿主不自行处理（无保存对话框/下载）",
            "PASS" if (reached and host_quiet) else "FAIL",
            {"reached": reached, "defaultPrevented": prevented, "downloads": list(self.downloads),
             "keys": s_keys, "attempts": s_attempts, "diag": s_diag},
        )
        self.report.add(
            "H5c", "观察：Ctrl+S 是否被渲染层 preventDefault（AC-36/37 的接管信号）",
            "PASS" if prevented else "FAIL",
            {"prevented": prevented,
             "why": "true ⇒ `file.save` 已被渲染层接管（BL-33 快速修复，AC-36/AC-37）；"
                    "false ⇒ 没有任何接管方（修复前的状态）"},
            gating=False,
        )
        self.report.say(f"       截图 {self.shot('h5-oskeys')}")
        return bool(positive and reached and host_quiet)

    # ── H5x/H5y：纯渲染层合成按键（与 OS 焦点解耦的对照）──
    def h5_synthetic(self) -> None:
        state = self.js(JS_PANE_STATE) or {}
        raw = self.js(JS_SYNTHETIC_PANE_NEW) or {}
        self.report.add(
            "H5x", "记录（不判）：合成 keydown（window 派发）Ctrl+Alt+N 的接管情况（环境原样）",
            "PASS", {"documentHasFocus": state.get("focused"), "raw": raw}, gating=False,
        )
        # 控制变量：把 hasFocus 遮蔽为 true，分离「焦点环境」与「应用级接线」
        shadow = self.js(
            "(function(){try{Object.defineProperty(document,'hasFocus',"
            "{configurable:true,value:function(){return true;}});return {ok:true};}"
            "catch(e){return {r11Error:String(e)};}})()"
        )
        from PySide6.QtTest import QTest

        pane_before = (self.js(JS_PANE_STATE) or {}).get("paneCount", 0)
        result = self.js(JS_SYNTHETIC_PANE_NEW) or {}
        QTest.qWait(500)
        pane_after = (self.js(JS_PANE_STATE) or {}).get("paneCount", 0)
        wired = bool(result.get("defaultPrevented")) and pane_after > pane_before
        self.report.add(
            "H5y", "遮蔽 hasFocus 后合成 Ctrl+Alt+N 被应用级监听接管（AC-10 接线控制变量）",
            "PASS" if wired else "FAIL",
            {"shadow": shadow, "before": pane_before, "after": pane_after, "raw": result},
        )

    # ── H6：AC-11 真机端到端（真鼠标拖拽 + 真剪贴板）──
    CLIP_EXPECTED = "bcd\nh  \njkl"

    def _input_widget(self):
        """QtWebEngine 真正接收输入的内层 widget（`focusProxy`）。"""
        proxy = self.shell.view.focusProxy()
        return proxy if proxy is not None else self.shell.view

    def _drag(self, start: tuple[float, float], end: tuple[float, float],
              alt: bool = True, steps: int = 8) -> dict:
        """真鼠标拖拽（QMouseEvent 直接投给输入 widget，带修饰键与左键按住）。"""
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest  # 函数内导入：QTest 是逐函数局部名，不能跨函数借用
        from PySide6.QtWidgets import QApplication

        target = self._input_widget()
        mod = Qt.KeyboardModifier.AltModifier if alt else Qt.KeyboardModifier.NoModifier

        def _global(pt: QPointF) -> QPointF:
            """全局坐标：Chromium 的输入代理按 widget 局部坐标 + 全局坐标共同定位。"""
            return QPointF(target.mapToGlobal(pt.toPoint()))

        def _send(kind: QEvent.Type, pt: QPointF, button: Qt.MouseButton,
                  buttons: Qt.MouseButtons) -> None:
            QApplication.sendEvent(target, QMouseEvent(kind, pt, _global(pt), button, buttons, mod))

        p0, p1 = QPointF(*start), QPointF(*end)
        _send(QEvent.Type.MouseButtonPress, p0,
              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        for step in range(1, steps + 1):
            frac = step / steps
            pt = QPointF(p0.x() + (p1.x() - p0.x()) * frac, p0.y() + (p1.y() - p0.y()) * frac)
            _send(QEvent.Type.MouseMove, pt,
                  Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
            QTest.qWait(60)
        _send(QEvent.Type.MouseButtonRelease, p1,
              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        QTest.qWait(500)
        return {"target": type(target).__name__,
                "widgetSize": [target.width(), target.height()],
                "widgetOriginGlobal": [target.mapToGlobal(QPointF(0, 0).toPoint()).x(),
                                       target.mapToGlobal(QPointF(0, 0).toPoint()).y()],
                "start": [start[0], start[1]], "end": [end[0], end[1]], "steps": steps,
                "modifiers": "alt" if alt else "none"}

    def h6_mouse_rect(self, fixture: Path) -> bool:
        """AC-11：修饰键 + 真鼠标拖拽 → 矩形块选择 → 真 Ctrl+C → 真剪贴板。"""
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        if not self.real_window or not sys.platform.startswith("win"):
            self.report.add("H6", "AC-11 真机端到端（真拖拽 + 真剪贴板）", "SKIP",
                            "需要真窗口 + Windows（合成鼠标/按键到不了渲染层）")
            return False
        self.open_path(fixture, "ac11.txt", need_editor=True)
        QTest.qWait(500)
        QApplication.clipboard().clear()
        # 拖拽起止：AC-11 要求 0 基列 [1,4)（"abcdef" 的 bcd / "gh" 的 h＋两空格 / "ijklmn" 的 jkl）。
        # 端点口径：CM6 的矩形选择把端点当**开区间**，故起点取第 1 列左沿 (line 1, col 1)、
        # 终点取最后一列右沿 (line 3, col 4 ⇒ 'd' 之后)。取 col 3 会少选一列（实测 "bc/h /jk"）。
        start = self.js(js_pos_css("ac11.txt", 1, 1)) or {}
        end = self.js(js_pos_css("ac11.txt", 3, 4)) or {}
        if start.get("r11Error") or end.get("r11Error"):
            self.report.add("H6", "AC-11 真机端到端（真拖拽 + 真剪贴板）", "FAIL",
                            {"start": start, "end": end})
            return False
        # 坐标口径：`coordsAtPos` 给的是 CSS 像素，QtWebEngine 的 widget 也是 CSS 像素（zoom=1），
        # 因此**不得**再乘 devicePixelRatio（曾乘 dpr=1.25 ⇒ 事件落到第 3 行行尾，选择塌成光标）。
        drag_info = self._drag((start["left"], start["top"]),
                               (end["left"], end["bottom"]))
        drag_info = dict(drag_info, dpr=start.get("dpr"))
        geom = self.js(js_rect_read("ac11.txt")) or {}
        self.report.say(f"       截图 {self.shot('h6-rect')}")

        # 真 Ctrl+C（OS 级）→ 真剪贴板（首次可能没到，重试一次）
        clip = ""
        attempts = 0
        for attempts in (1, 2):
            self.activate()
            self.shell.view.setFocus()
            QTest.qWait(150)
            os_release_modifiers()
            os_press(["ctrl"], "c")
            QTest.qWait(700)
            clip = QApplication.clipboard().text() or ""
            if clip:
                break
        ranges = geom.get("ranges") or []
        shape = "single-range" if geom.get("rangeCount") == 1 else f"{geom.get('rangeCount')}ranges"
        # 矩形选择在 CM6 里是**每行一个 range**：判定要按「行数 = 3、起始列都是 1、首末行宽度 = 3」
        # 来看，而不是像普通选择那样要求 rangeCount == 1。**中间行会被行尾夹短**（"gh" 只有 2 字符
        # ⇒ 该行 range 到列 2 为止），补空格由渲染层的块选择复制负责 —— 所以逐字符判据落在剪贴板上。
        widths = [(r.get("toCol") or 0) - (r.get("fromCol") or 0) for r in ranges]
        lines = [r.get("fromLine") for r in ranges]
        rect_ok = (len(ranges) == 3 and lines == [1, 2, 3]
                   and all((r.get("fromCol") == 1) for r in ranges)
                   and widths[:1] == [3] and widths[-1:] == [3]
                   and all(w <= 3 for w in widths))
        row_span = None
        if ranges:
            row_span = (ranges[-1].get("fromLine") or 0) - (ranges[0].get("fromLine") or 0) + 1
        ok = rect_ok and clip == self.CLIP_EXPECTED
        self.report.add(
            "H6", "AC-11 真机端到端（真拖拽 + 真剪贴板）", "PASS" if ok else "FAIL",
            {"start": start, "end": end, "drag": drag_info, "shape": shape,
             "rectOk": rect_ok, "widths": widths, "rangeLines": lines,
             "rowSpan": row_span, "ranges": ranges[:4], "docLength": geom.get("docLength"),
             "clipboard": clip, "clipboardExpected": self.CLIP_EXPECTED,
             "clipboardMatch": clip == self.CLIP_EXPECTED, "copyAttempts": attempts,
             "dpr": start.get("dpr")},
        )
        return bool(ok)

    # ── H7：整轮零 http(s) 请求 ──
    def h7_offline(self, urls: list[str]) -> None:
        remote = [u for u in urls if u.startswith(("http://", "https://"))]
        self.report.add(
            "H7", "AC-42 ②：真机整轮运行零 http(s) 请求（渲染层无网络出口）",
            "PASS" if not remote else "FAIL",
            {"total": len(urls), "remote": remote[:10], "sample": urls[:6]},
        )


def fixture_text_len(text: str) -> int:
    return len(text)


# ─────────────────────────── 启动 ───────────────────────────


def make_recorder():
    from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

    class Recorder(QWebEngineUrlRequestInterceptor):
        def __init__(self) -> None:
            super().__init__()
            self.urls: list[str] = []

        def interceptRequest(self, info) -> None:  # noqa: N802 - Qt 虚函数名
            self.urls.append(info.requestUrl().toString())

    return Recorder()


def build():
    from src.bridge import Bridge
    from src.bridge import editor as editor_bridge
    from src.ui.web_shell import WebShell, build_app
    from src.utils.input_method import setup_input_method

    setup_input_method()
    app = build_app()
    app.setQuitOnLastWindowClosed(False)
    bridge = Bridge()
    shell = WebShell(objects={"bridge": bridge}, frameless=True,
                     publish_event=bridge.publish_event)
    editor_bridge.set_main_window(shell)
    try:
        from src.bridge import files as files_bridge

        files_bridge.set_main_window(shell)
    except Exception as exc:  # noqa: BLE001 - 文件桥缺席不影响编辑器链路
        print(f"[warn] files bridge 未接线：{exc}", flush=True)
    recorder = make_recorder()
    profile = shell.view.page().profile()
    profile.setUrlRequestInterceptor(recorder)
    downloads: list[str] = []
    profile.downloadRequested.connect(
        lambda item: (downloads.append(item.url().toString()), item.cancel())
    )
    load_ok = shell.load_ui()
    shell.show()
    return app, shell, recorder, load_ok, downloads


def main() -> int:
    parser = argparse.ArgumentParser(description="r11 真机自动化复核（P6-5）")
    parser.add_argument("--offscreen", action="store_true", help="无显示环境：几何/OS 按键判据标 SKIP")
    parser.add_argument("--out", default="", help="证据目录（默认 poc/editor-probes/out/r11-<时间戳>）")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else SCRIPT.parent / "out" / f"r11-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    real_window = not args.offscreen and not os.environ.get("QT_QPA_PLATFORM", "").startswith(
        "offscreen")

    # 用户目录沙箱：必须在导入 src.config.settings 之前改 APPDATA
    appdata = out_dir / "appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(appdata)
    os.environ["LOCALAPPDATA"] = str(appdata)

    report = Report(out_dir)
    report.say(f"r11 真机自动化复核 · 平台={os.environ.get('QT_QPA_PLATFORM', '(默认)')} · "
               f"真窗口={real_window}")
    report.say(f"证据目录：{out_dir}")

    fixtures = out_dir / "fixtures"
    fixtures.mkdir(exist_ok=True)
    wide_lines = ["ABCDEFGHIJKLMNOPQRSTUVWX", "abcdefghijklmnopqrstuvwx",
                  "0123456789ABCDEFGHIJKLMN", "ZYXWVUTSRQPONMLKJIHGFEDC"]
    wide_text = "\n".join(wide_lines) + "\n"
    long_text = "".join(f"L{index:03d}-" + "x" * 96 + "\n" for index in range(1, 4))
    # 每次运行都重写 fixture（上一轮可能被自动保存写回污染）
    wide = fixtures / "wide.txt"
    wide.write_bytes(wide_text.encode("utf-8"))
    rect = fixtures / "rect.txt"
    rect.write_bytes(wide_text.encode("utf-8"))
    long_file = fixtures / "long.txt"
    long_file.write_bytes(long_text.encode("utf-8"))
    # AC-11 的原样文本（三行、无末尾换行）
    ac11_file = fixtures / "ac11.txt"
    ac11_file.write_bytes(b"abcdef\ngh\nijklmn")

    try:
        app, shell, recorder, load_ok, downloads = build()
    except Exception as exc:  # noqa: BLE001 - 环境问题要能看清原因
        import traceback

        traceback.print_exc()
        report.add("H0", "真外壳可构造（QtWebEngine + 桥 + 前端产物）", "FAIL", {"error": str(exc)})
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", "")})
        return 2

    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest

    QTimer.singleShot(300000, lambda: os._exit(3))  # 看门狗

    probe = R11(report, shell, out_dir, real_window)
    probe.downloads = downloads

    if not load_ok:
        from src.ui.web_shell import resolve_index_html

        report.add("H0", "真外壳可构造（QtWebEngine + 桥 + 前端产物）", "FAIL",
                   {"load_ui": False, "index_html": resolve_index_html()})
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", "")})
        return 2

    try:
        probe.h1_shell(load_ok)
        probe.h2_open(wide)
        probe.h3_rectangle(rect, wide_text)
        probe.h4_wheel(long_file)
        probe.h5_os_keys()
        probe.h5_synthetic()
        probe.h6_mouse_rect(ac11_file)
        probe.h7_offline(recorder.urls)
    except Exception as exc:  # noqa: BLE001 - 任何未预期异常都留证
        import traceback

        traceback.print_exc()
        report.add("H9", "探针自身无未捕获异常", "FAIL", {"error": str(exc)})

    path = report.write({
        "platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "real_window": real_window,
        "requests": recorder.urls,
        "user_data_sandbox": str(appdata),
        "fixtures": {p.name: sha256_of(p) for p in (wide, rect, long_file)},
    })
    code = report.verdict()
    report.say(f"结果 JSON：{path}")
    report.say(f"退出码：{code}")

    shell.view.page().profile().setUrlRequestInterceptor(None)
    QTest.qWait(200)
    app.quit()
    return code


if __name__ == "__main__":
    sys.exit(main())

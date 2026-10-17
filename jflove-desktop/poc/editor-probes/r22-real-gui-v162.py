#!/usr/bin/env python3
"""r22 真机探针：v1.6.2 桌面端编辑器四件事的**真机判据**

## 覆盖的判据（逐条可失败）

| id | 判据 | 需求 |
|----|------|------|
| `P0` | 真外壳加载 v1.6.2 产物；初始态 = 未登录（无编辑器入口 / 无工作区 DOM） | 承接 |
| `P1` | 隔离服务端 + 服务层真登录 + 重载 ⇒ 渲染层已登录；经生产通路开出标签页 | 承接 |
| `M1` | **真鼠标**点「工具箱」⇒ 弹框出现且 12 项可见 | AC-80 前置 |
| `M2` | **真鼠标**点「排序」⇒ **正文真的变了**（`b\\na` → `a\\nb`） | **AC-80（本版核心）** |
| `M3` | **真鼠标**点「打开对比」⇒ 覆盖层出现，且**高度 ≥ 编辑面板内容区 90%** | AC-83 / AC-84 |
| `M4` | 动作后工具栏出现**中文结果提示**（含数字） | AC-82② |
| `K-*` | OS 级按 11 条工具箱快捷键 ⇒ 各自的首选动作真的生效 | AC-88 / AC-89① |
| `K-OUT` | 焦点在编辑面板**外**按同一条键 ⇒ 不接管（`defaultPrevented=false`）且正文不变 | AC-89② |
| `C-EDIT` | 真鼠标右键编辑器正文 ⇒ 出现**中文**原生菜单（0 英文项 / 0 浏览器导航项） | AC-85 / AC-86① |
| `C-PLAIN` | 真鼠标右键**非可编辑区**（标签条）⇒ **不出现任何菜单** | AC-86② |
| `H` | 相位内 47 项宿主默认动作增量 = 0、无远端请求、主窗口存活 | AC-99④ |

## 为什么必须真机（不能只靠 jsdom）

`LESSONS.md` **L30 / L40**：DOM 断言看不出"用户看不见效果"。本版修的缺陷恰恰是
**真实事件顺序**导致的（`pointerup` 关框吃掉 `click`）—— jsdom 里三个事件塞进同一个 `act()`
就"通过"。真机用 **OS 级鼠标**（`SetCursorPos` + `mouse_event`）与 **OS 级按键**
（`keybd_event`，复用 r13 原语）驱动，读数是渲染层 DOM 的真实结果。

## 怎么跑

    jflove-desktop\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r22-real-gui-v162.py
    ... --only M2,M3                     # 只跑某几条
    ... --webui ui\\dist                  # 指定前端产物根（默认 ui/dist）

## 退出码

- `0` 选中的**必过**判据全绿（SKIP 不算失败）
- `1` 有必过判据失败
- `2` 环境/脚本问题（QtWebEngine 起不来、前端产物缺失、r13 原语缺失、无真窗口）
- `3` 看门狗超时

## 边界

- 只在本次运行的 `out/r22-<时间戳>/` 里写证据；`APPDATA` / `LOCALAPPDATA` 指向 `out/.tmp_appdata`
  （`session.json` 里有真 JWT，`.tmp_` 前缀命中 `.gitignore`）；
- 不启动单实例守卫 / 托盘 / 媒体浮层；**不修改任何产品源码**；
- 鼠标坐标口径：`getBoundingClientRect()`（CSS px，zoom=1）→ 视口逻辑坐标 →
  `view.mapToGlobal()` × `devicePixelRatio`（物理像素）⇒ 证据里同时记录 dpr 与窗口原点，便于人工复算。
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
PROBES = SCRIPT.parent
DESKTOP = SCRIPT.parents[2]

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
sys.path.insert(0, str(DESKTOP))

#: 本探针自己的凭据（隔离库每次新建，账号只存在于本次运行的库里）
ADMIN_USER = "r22probe"
ADMIN_PASSWORD = "r22-Passw0rd!"

#: 补齐 r13 `_VK` 里没有的字母键（本版 11 条工具箱键用到）
VK_ADDITIONS = (
    ("f", 0x46), ("p", 0x50), ("u", 0x55), ("e", 0x45), ("b", 0x42), ("j", 0x4A),
    ("m", 0x4D), ("t", 0x54), ("q", 0x51), ("x", 0x58), ("o", 0x4F),
)

#: 11 条工具箱快捷键 → (修饰键, 键名, 工具箱项 id, 期望的可观测结果)
#: `expect` 的取值由 `R22.judge_key()` 解释：`text:<子串>` / `changed` / `view:<模式>` / `font:+`
TOOLBOX_KEYS: tuple[tuple[str, str, str, str, str], ...] = (
    ("toolbox.search", "f", "search", "查找 / 替换面板", "panel"),
    ("toolbox.lines", "p", "lines", "行排序", "changed"),
    ("toolbox.case", "u", "case", "转大写", "text:AB"),
    ("toolbox.escape", "e", "escape", "JSON 转义", "text:\\\""),
    ("toolbox.base64", "b", "base64", "Base64 编码", "text:YWI="),
    ("toolbox.json", "j", "json", "JSON 格式化", "changed"),
    ("toolbox.markdown", "m", "markdown", "Markdown 预览", "view:preview"),
    ("toolbox.csv", "t", "csv", "表格视图", "view:table"),
    ("toolbox.sql", "q", "sql", "SQL 美化", "changed"),
    ("toolbox.xml", "x", "xml", "XML 格式化", "changed"),
    ("toolbox.font", "o", "font", "放大字号", "font:+"),
)

#: 每条键自己的夹具（文件名 → 内容）：内容按动作的前置条件挑，保证"生效"可观测
KEY_FIXTURES: dict[str, tuple[str, str]] = {
    "search": ("k-search.txt", "alpha\nbeta\n"),
    "lines": ("k-lines.txt", "b\na"),
    "case": ("k-case.txt", "aB"),
    "escape": ("k-escape.txt", 'a"b'),
    "base64": ("k-base64.txt", "ab"),
    "json": ("k-json.txt", '{"a":1}'),
    "markdown": ("k-markdown.md", "# T\n"),
    "csv": ("k-csv.csv", "a,b\n1,2\n"),
    "sql": ("k-sql.sql", "select 1 from t"),
    "xml": ("k-xml.xml", "<a><b>1</b></a>"),
    "font": ("k-font.txt", "abc\n"),
}

# ── 渲染层读数（都限定在**激活面板**内，避免读到隐藏面板）─────────────────────

JS_ACTIVE = '[data-editor-panel-active="true"]'

JS_TEXT = f"""
(function () {{
  try {{
    var host = document.querySelector('{JS_ACTIVE} .cm-content');
    var raw = host ? (host.innerText || '') : '';
    return {{ text: raw.replace(/\\u00a0/g, ' '), len: raw.length }};
  }} catch (e) {{ return {{ r22Error: String(e) }}; }}
}})()
"""

JS_VIEW_MODE = f"""
(function () {{
  try {{
    var body = document.querySelector('{JS_ACTIVE} [data-pane-body]');
    return {{ viewMode: body ? body.getAttribute('data-pane-body') : null }};
  }} catch (e) {{ return {{ r22Error: String(e) }}; }}
}})()
"""

JS_FONT_SIZE = f"""
(function () {{
  try {{
    var host = document.querySelector('{JS_ACTIVE} .cm-content');
    if (!host) return {{ px: null }};
    var size = window.getComputedStyle(host).fontSize || '';
    var px = parseFloat(size);
    return {{ px: isNaN(px) ? null : px, raw: size }};
  }} catch (e) {{ return {{ r22Error: String(e) }}; }}
}})()
"""

JS_SEARCH_PANEL = """
(function () {
  try {
    var panels = document.querySelectorAll('.cm-panel.cm-search');
    return { openPanels: panels.length };
  } catch (e) { return { r22Error: String(e) }; }
})()
"""

JS_TOOLBOX_STATE = """
(function () {
  try {
    var menus = document.querySelectorAll('[data-toolbox-menu="true"]');
    var owned = menus.length ? menus[menus.length - 1] : null;
    var items = owned ? owned.querySelectorAll('[data-toolbox-item]') : [];
    var visible = 0;
    for (var i = 0; i < items.length; i++) {
      var r = items[i].getBoundingClientRect();
      if (r.width > 0 && r.height > 0) visible += 1;
    }
    var aria = null;
    var bar = owned ? owned.parentElement : null;
    if (bar) {
      var btns = bar.querySelectorAll('button[aria-expanded]');
      for (var j = 0; j < btns.length; j++) {
        if ((btns[j].textContent || '').indexOf('工具箱') >= 0) {
          aria = btns[j].getAttribute('aria-expanded');
        }
      }
    }
    return { menuCount: menus.length, itemCount: items.length, visibleItems: visible,
             triggerAriaExpanded: aria,
             menuInActivePanel: !!(owned && owned.closest
                                   && owned.closest('[data-editor-panel-active="true"]')) };
  } catch (e) { return { r22Error: String(e) }; }
})()
"""

#: 工具箱触发按钮（激活面板内、文案含「工具箱」）
JS_TOOLBOX_TRIGGER = f"""
(function () {{
  var bar = document.querySelector('{JS_ACTIVE}');
  if (!bar) return null;
  var btns = bar.querySelectorAll('button');
  for (var i = 0; i < btns.length; i++) {{
    if ((btns[i].textContent || '').indexOf('工具箱') >= 0) return btns[i];
  }}
  return null;
}})()
"""

#: 「行排序」按钮（`data-toolbox-item="lines"` 里的第一个按钮）
JS_SORT_BUTTON = """
(function () {
  var item = document.querySelector('[data-toolbox-menu="true"] [data-toolbox-item="lines"]');
  if (!item) return null;
  var btns = item.querySelectorAll('button');
  return btns.length ? btns[0] : null;
})()
"""

#: 「打开对比」按钮
JS_DIFF_BUTTON = """
(function () {
  var item = document.querySelector('[data-toolbox-menu="true"] [data-toolbox-item="diff"]');
  if (!item) return null;
  var btns = item.querySelectorAll('button');
  return btns.length ? btns[0] : null;
})()
"""

#: 元素中心点（CSS px）+ 命中自检
JS_RECT_OF = """
(function (node) {
  if (!node) return null;
  var r = node.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return null;
  var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  var hit = document.elementFromPoint(cx, cy);
  return { x: cx, y: cy, w: r.width, h: r.height,
           hitTag: hit ? hit.tagName : null,
           hitText: hit ? (hit.textContent || '').slice(0, 24) : null };
})(%s)
"""

#: 对比覆盖层 vs 编辑面板内容区几何（AC-83）
JS_OVERLAY_GEOMETRY = f"""
(function () {{
  try {{
    var overlay = document.querySelector('{JS_ACTIVE} [data-editor-overlay="diff"]');
    var panel = document.querySelector('{JS_ACTIVE}');
    if (!overlay || !panel) return {{ overlay: !!overlay, panel: !!panel }};
    var o = overlay.getBoundingClientRect();
    var p = panel.getBoundingClientRect();
    var toolbar = panel.firstElementChild ? panel.firstElementChild.getBoundingClientRect() : null;
    return {{ overlay: true, panel: true,
             overlayRect: [o.left, o.top, o.width, o.height],
             panelRect: [p.left, p.top, p.width, p.height],
             toolbarBottom: toolbar ? toolbar.bottom : null,
             heightRatio: p.height > 0 ? o.height / p.height : 0,
             coversToolbarArea: o.top >= (toolbar ? toolbar.bottom - 1 : p.top),
             closeButton: !!(overlay.textContent || '').indexOf('关闭对比') >= 0 }};
  }} catch (e) {{ return {{ r22Error: String(e) }}; }}
}})()
"""

#: 工具栏里的结果提示（AC-82②）
JS_STATUS_TEXT = f"""
(function () {{
  var node = document.querySelector('{JS_ACTIVE} [role="status"]');
  return {{ status: node ? (node.textContent || '') : null }};
}})()
"""

#: 把 DOM 焦点放进**激活面板内**（工具箱快捷键的作用域判据要求 `event.target` 落在面板内）。
#: 回退链：`.cm-content`（CM6 正文）→ 面板内第一个 `<select>` → 面板内第一个 `<button>`。
#: 为什么需要回退：`.csv` 夹具默认就是**表格视图**，此时 CM6 宿主是 `hidden` ⇒ 不可聚焦。
JS_FOCUS_PANEL = f"""
(function () {{
  try {{
    var panel = document.querySelector('{JS_ACTIVE}');
    if (!panel) return {{ focused: false, why: 'no-active-panel' }};
    function inside() {{
      var el = document.activeElement;
      return !!(el && el.closest && el.closest('[data-editor-panel]'));
    }}
    var host = panel.querySelector('.cm-content');
    if (host && typeof host.focus === 'function') host.focus();
    if (!inside()) {{
      var select = panel.querySelector('select');
      if (select) select.focus();
    }}
    if (!inside()) {{
      var button = panel.querySelector('button');
      if (button) button.focus();
    }}
    return {{ focused: inside(),
             activeTag: document.activeElement ? document.activeElement.tagName : null }};
  }} catch (e) {{ return {{ r22Error: String(e) }}; }}
}})()
"""

#: 激活面板里的「编辑」视图按钮（`.csv` 夹具要先从表格视图切回编辑态，判据才有区分度）
JS_EDIT_VIEW_BUTTON = f"""
(function () {{
  var panel = document.querySelector('{JS_ACTIVE}');
  if (!panel) return null;
  var btns = panel.querySelectorAll('button');
  for (var i = 0; i < btns.length; i++) {{
    if ((btns[i].textContent || '').trim() === '编辑') return btns[i];
  }}
  return null;
}})()
"""

#: 面板状态（诊断"读到的到底是哪个面板"：多个 `data-editor-panel-active` 说明读错了对象）
JS_PANEL_STATE = """
(function () {
  try {
    var actives = document.querySelectorAll('[data-editor-panel-active="true"]');
    return { activeCount: actives.length,
             activeIds: Array.prototype.map.call(actives, function (n) {
               return n.getAttribute('data-editor-panel');
             }),
             cmContentCount: document.querySelectorAll('.cm-content').length,
             panelCount: document.querySelectorAll('[data-editor-panel]').length,
             tabTitles: Array.prototype.map.call(
               document.querySelectorAll('.scratch-tab'), function (n) {
                 return (n.textContent || '').trim();
               }) };
  } catch (e) { return { r22Error: String(e) }; }
})()
"""

#: 离线包里 `modules.search` 的导出面（诊断"查找面板打不开"到底缺哪一环）
JS_CM_SEARCH_API = """
(function () {
  try {
    var api = window.__JFLOVE_CM__;
    if (!api || !api.modules || !api.modules.search) return { api: false };
    var keys = Object.keys(api.modules.search);
    return { api: true, keys: keys,
             openSearchPanelType: typeof api.modules.search.openSearchPanel,
             panelClassPresent: !!document.querySelector('[class*="cm-search"]'),
             panelCount: document.querySelectorAll('.cm-panel').length };
  } catch (e) { return { r22Error: String(e) }; }
})()
"""

#: 把焦点移出编辑面板（K-OUT 用）
JS_BLUR_PANEL = """
(function () {
  try {
    var active = document.activeElement;
    if (active && active.blur) active.blur();
    document.body.tabIndex = -1;
    document.body.focus();
    var panel = active && active.closest ? active.closest('[data-editor-panel]') : null;
    return { blurred: !panel,
             activeTag: document.activeElement ? document.activeElement.tagName : null };
  } catch (e) { return { r22Error: String(e) }; }
})()
"""


class Report:
    """收集判据结果并决定退出码（r13/r17/r20 同口径，独立实现）"""

    def __init__(self, out_dir: Path, only: tuple[str, ...], gating: set[str]) -> None:
        self.out = out_dir
        self.only = only
        self.gating = gating
        self.items: list[dict] = []
        self.notes: list[str] = []

    def say(self, message: str) -> None:
        print(f"[r22] {message}", flush=True)

    def note(self, message: str) -> None:
        self.notes.append(message)
        print(f"[r22] · {message}", flush=True)

    def add(self, cid: str, desc: str, status: str, detail: dict,
            gating: bool | None = None) -> None:
        is_gating = cid in self.gating if gating is None else gating
        item = {"id": cid, "desc": desc, "status": status, "gating": bool(is_gating),
                "detail": detail}
        self.items.append(item)
        icon = {"PASS": "OK  ", "FAIL": "FAIL", "SKIP": "SKIP"}.get(status, status)
        print(f"[r22] {icon} {cid}  {desc}", flush=True)

    def verdict(self) -> int:
        failures = [i for i in self.items if i["status"] == "FAIL" and i["gating"]]
        return 1 if failures else 0

    def write(self, extra: dict) -> None:
        payload = {"generatedAt": datetime.now().isoformat(timespec="seconds"),
                   "criteria": self.only, "results": self.items, "notes": self.notes}
        payload.update(extra)
        (self.out / "r22-result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_r13():
    """以自定义模块名加载 r13（文件名含连字符，不能直接 `import`）"""
    path = PROBES / "r13-ctrlw-takeover.py"
    if not path.exists():
        raise FileNotFoundError(f"未找到依赖的 r13 探针：{path}")
    spec = importlib.util.spec_from_file_location("r13_ref", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {path} 构造 import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules["r13_ref"] = module
    spec.loader.exec_module(module)
    return module


# ─────────────────────────── OS 级鼠标 ───────────────────────────

_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010


def os_click(x: int, y: int, button: str = "left") -> None:
    """真·OS 级鼠标（`SetCursorPos` + `mouse_event`）——Qt 合成事件到不了渲染层（r13 结论）"""
    from PySide6.QtTest import QTest

    user32 = ctypes.windll.user32
    down = _MOUSEEVENTF_LEFTDOWN if button == "left" else _MOUSEEVENTF_RIGHTDOWN
    up = _MOUSEEVENTF_LEFTUP if button == "left" else _MOUSEEVENTF_RIGHTUP
    user32.SetCursorPos(int(x), int(y))
    QTest.qWait(160)
    user32.mouse_event(down, 0, 0, 0, 0)
    QTest.qWait(90)
    user32.mouse_event(up, 0, 0, 0, 0)
    QTest.qWait(360)


class R22:
    def __init__(self, report: Report, driver, r13, shell, out_dir: Path, real_window: bool,
                 downloads: list, action_counts: dict) -> None:
        self.report = report
        self.d = driver          # r13.R13 实例：js() / wait_for() / activate() / press_and_read()
        self.r13 = r13
        self.shell = shell
        self.out = out_dir
        self.real_window = real_window
        self.downloads = downloads
        self.counts = action_counts
        self.current_tab_title = ""

    # ── 通用 ──
    def supported(self) -> bool:
        return bool(self.real_window) and sys.platform.startswith("win")

    def shot(self, name: str) -> str:
        try:
            path = self.out / f"r22-{name}.png"
            self.shell.view.grab().save(str(path))
            return path.name
        except Exception as exc:  # noqa: BLE001 - 截图失败不影响判据
            return f"shot-failed: {exc}"

    def skip(self, cid: str, desc: str, why: str, gating: bool = True) -> None:
        self.report.add(cid, desc, "SKIP", {"why": why}, gating=gating)

    def element_point(self, node_js: str) -> dict:
        """节点中心的**物理**屏幕坐标（含命中自检读数）"""
        from PySide6.QtCore import QPoint

        rect = self.d.js(JS_RECT_OF % node_js) or {}
        if not rect or rect.get("r22Error") or rect.get("w") is None:
            return {"ok": False, "rect": rect}
        view = self.shell.view
        origin = view.mapToGlobal(QPoint(0, 0))
        dpr = float(view.devicePixelRatioF() or 1.0)
        gx = origin.x() * dpr + float(rect["x"]) * dpr
        gy = origin.y() * dpr + float(rect["y"]) * dpr
        return {"ok": True, "x": int(round(gx)), "y": int(round(gy)), "rect": rect,
                "origin": [origin.x(), origin.y()], "dpr": dpr,
                "windowSize": [view.width(), view.height()]}

    def click_node(self, node_js: str, button: str = "left") -> dict:
        """真鼠标点某个 DOM 节点（中心点），返回坐标与命中自检读数"""
        self.d.activate()
        point = self.element_point(node_js)
        if not point.get("ok"):
            return {"clicked": False, "point": point}
        os_click(point["x"], point["y"], button)
        return {"clicked": True, "point": point, "button": button}

    def visible_menus(self) -> list:
        """当前可见的原生 `QMenu`（右击菜单判据的观测点）"""
        from PySide6.QtWidgets import QApplication, QMenu

        found = []
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMenu) and widget.isVisible():
                found.append(widget)
        return found

    # ── P1：经生产通路开一个夹具 ──
    def open_fixture(self, path: Path) -> bool:
        """
        经生产通路（`notify_external_paths`）开一个夹具文件，并**等到它真的成为激活面板**。

        ⚠ 判据必须是"新标签页出现且标题正确 + 恰有 1 个激活面板"，**不能**只写
        `tabCount >= 1` —— 那会被**上一个夹具的标签页**满足，于是本条判据读到的是旧面板的正文
        （首轮实测：K-L 的 `before` 读成了 K-F 夹具的 `alpha\\nbeta\\n`）。
        """
        from PySide6.QtTest import QTest

        pushed = self.shell.notify_external_paths([str(path)])
        QTest.qWait(600)
        state = self.d.wait_for(
            self.r13.JS_UI_STATE,
            lambda v: any((t or {}).get("title") == path.name for t in (v.get("tabs") or []))
            and v.get("activePanelCount", 0) >= 1,
            timeout_ms=20000, interval_ms=350) or {}
        QTest.qWait(400)
        titles = [(t or {}).get("title") for t in (state.get("tabs") or [])]
        self.current_tab_title = path.name
        return bool(pushed >= 1 and path.name in titles
                    and state.get("activePanelCount", 0) >= 1)

    def focus_panel(self) -> dict:
        """把 DOM 焦点放进激活面板（工具箱快捷键作用域判据的前置）"""
        result = self.d.js(JS_FOCUS_PANEL) or {}
        return result

    def read_text(self) -> str:
        return str((self.d.js(JS_TEXT) or {}).get("text") or "")

    def read_view_mode(self) -> str | None:
        return (self.d.js(JS_VIEW_MODE) or {}).get("viewMode")

    # ── M1..M4：真鼠标驱动工具箱 ──
    def m_toolbox_click(self) -> dict:
        opened = self.click_node(JS_TOOLBOX_TRIGGER)
        from PySide6.QtTest import QTest

        QTest.qWait(500)
        state = self.d.js(JS_TOOLBOX_STATE) or {}
        return {"click": opened, "state": state,
                "shot": self.shot("m1-toolbox-open")}

    def m_sort_click(self, before: str) -> dict:
        clicked = self.click_node(JS_SORT_BUTTON)
        from PySide6.QtTest import QTest

        QTest.qWait(700)
        after = self.read_text()
        status = (self.d.js(JS_STATUS_TEXT) or {}).get("status")
        state = self.d.js(JS_TOOLBOX_STATE) or {}
        return {"click": clicked, "before": before, "after": after,
                "changed": after != before, "status": status,
                "menuStillOpen": state.get("menuCount", 0) > 0,
                "shot": self.shot("m2-after-sort")}

    def m_diff_click(self) -> dict:
        opened = self.click_node(JS_TOOLBOX_TRIGGER)
        from PySide6.QtTest import QTest

        QTest.qWait(400)
        clicked = self.click_node(JS_DIFF_BUTTON)
        QTest.qWait(900)
        geometry = self.d.js(JS_OVERLAY_GEOMETRY) or {}
        return {"openMenu": opened, "click": clicked, "geometry": geometry,
                "shot": self.shot("m3-diff-overlay")}

    # ── K-*：11 条工具箱快捷键 ──
    def judge_key(self, item_id: str, expect: str, before: str, after: str,
                  view_mode: str | None, font_px: float | None, font_before: float | None,
                  panel: dict) -> tuple[bool, dict]:
        evidence = {"before": before, "after": after, "viewMode": view_mode,
                    "fontPx": font_px, "fontBefore": font_before, "panel": panel,
                    "expect": expect}
        if expect == "changed":
            ok = after != before
        elif expect == "panel":
            ok = bool(panel.get("openPanels"))
        elif expect.startswith("view:"):
            ok = view_mode == expect.split(":", 1)[1]
        elif expect.startswith("text:"):
            ok = expect.split(":", 1)[1] in after
        elif expect == "font:+":
            ok = bool(font_px and font_before and font_px > font_before)
        else:
            ok = False
        evidence["ok"] = ok
        return ok, evidence

    def k_toolbox_keys(self, only: tuple[str, ...]) -> None:
        for shortcut_id, key, item_id, label, expect in TOOLBOX_KEYS:
            cid = f"K-{key.upper()}"
            if f"K-{item_id.upper()}" not in only and cid not in only and "K-ALL" not in only:
                self.skip(cid, f"`Ctrl+Shift+{key.upper()}`（{label}）⇒ 首选动作生效", "未选中",
                          gating=False)
                continue
            if not self.supported():
                self.skip(cid, f"`Ctrl+Shift+{key.upper()}`（{label}）⇒ 首选动作生效",
                          "需要真窗口 + Windows（OS 级输入）")
                continue

            name, content = KEY_FIXTURES[item_id]
            fixture = self.out / name
            fixture.write_text(content, encoding="utf-8")
            if not self.open_fixture(fixture):
                self.report.add(cid, f"`Ctrl+Shift+{key.upper()}`（{label}）", "FAIL",
                                {"why": "夹具标签页没打开"})
                continue

            self.d.activate()
            self.shell.view.setFocus()
            # ⚠ `.csv` 夹具默认落在**表格视图**（`defaultViewMode: 'table'`）⇒ 先真鼠标点回「编辑」，
            #    否则"按 Ctrl+Shift+T 后视图 = table"这条判据**天然成立**、失去区分度（假通过）。
            pre_edit = None
            if item_id == "csv":
                pre_edit = self.click_node(JS_EDIT_VIEW_BUTTON)
                from PySide6.QtTest import QTest as _QTest

                _QTest.qWait(500)
            focus = self.focus_panel()
            before = self.read_text()
            mode_before = self.read_view_mode()
            font_before = (self.d.js(JS_FONT_SIZE) or {}).get("px")
            # 焦点确保落在面板内（打开标签页后面板即激活）
            press = self.d.press_and_read(["ctrl", "shift"], key, key, settle_ms=1100)
            after = self.read_text()
            view_mode = self.read_view_mode()
            font_px = (self.d.js(JS_FONT_SIZE) or {}).get("px")
            panel = self.d.js(JS_SEARCH_PANEL) or {}
            status = (self.d.js(JS_STATUS_TEXT) or {}).get("status")
            cm_api = self.d.js(JS_CM_SEARCH_API) or {}
            ok, evidence = self.judge_key(item_id, expect, before, after, view_mode,
                                          font_px, font_before, panel)
            # ⚠ 判据必须**同时**要求"键真的被我们接管了"（`prevented`）：否则像 `.csv` 默认就是
            #    表格视图那样，期望值会**天然成立**而掩盖"键根本没生效"（假通过）。
            ok = (
                bool(ok)
                and bool(press.get("prevented"))
                and bool(focus.get("focused"))
            )
            evidence["ok"] = ok
            evidence.update({
                "shortcutId": shortcut_id, "key": f"Ctrl+Shift+{key.upper()}",
                "reached": press.get("reached"), "prevented": press.get("prevented"),
                "focus": focus, "viewModeBefore": mode_before, "preEditClick": pre_edit,
                "focusInsidePanel": bool(focus.get("focused")),
                "panelState": self.d.js(JS_PANEL_STATE),
                "status": status, "cmSearchApi": cm_api,
                "hostActionDelta": sum(
                    (press.get("host") or {}).get("fired", {}).values()
                    if isinstance((press.get("host") or {}).get("fired"), dict) else [0]),
            })
            self.report.add(cid, f"`Ctrl+Shift+{key.upper()}`（{label}）⇒ 首选动作真的生效",
                            "PASS" if ok else "FAIL", evidence)

    def k_scope_out(self) -> None:
        """AC-89②：焦点在编辑面板外 ⇒ 不接管（不 preventDefault）且正文不变"""
        cid = "K-OUT"
        if "K-OUT" not in self.report.only:
            self.skip(cid, "面板外按工具箱键 ⇒ 不接管", "未选中", gating=False)
            return
        if not self.supported():
            self.skip(cid, "面板外按工具箱键 ⇒ 不接管", "需要真窗口 + Windows")
            return

        name, content = KEY_FIXTURES["case"]
        fixture = self.out / name
        fixture.write_text(content, encoding="utf-8")
        if not self.open_fixture(fixture):
            self.report.add(cid, "面板外按工具箱键 ⇒ 不接管", "FAIL", {"why": "夹具没打开"})
            return

        before = self.read_text()
        blur = self.d.js(JS_BLUR_PANEL) or {}
        self.d.activate()
        press = self.d.press_and_read(["ctrl", "shift"], "u", "u", settle_ms=900)
        after = self.read_text()
        ok = bool(press.get("reached")) and not press.get("prevented") and after == before
        self.report.add(
            cid, "焦点移出编辑面板后按 `Ctrl+Shift+U` ⇒ 不接管、不吞键、正文逐字符不变",
            "PASS" if ok else "FAIL",
            {"reach": press.get("reached"), "prevented": press.get("prevented"),
             "before": before, "after": after, "blur": blur},
        )

    # ── C-*：真鼠标右键（原生 QMenu 判读）──────────────────────────────
    def _right_click_and_read_menu(self, node_js: str, settle_ms: int = 900) -> dict:
        """
        右击 + 在**嵌套模态循环**里读菜单。

        `QMenu.exec()` 会阻塞在嵌套事件循环里 ⇒ 必须用 `QTimer` 在菜单打开期间取值并关闭它，
        否则外层代码永远等不到（这是**探针实现本身**的坑，写下来免得下次重踩）。
        """
        from PySide6.QtCore import QTimer
        from PySide6.QtTest import QTest

        box: dict = {"seen": False, "labels": [], "enabled": [], "menuCount": 0}

        def inspect() -> None:
            menus = self.visible_menus()
            box["menuCount"] = len(menus)
            if menus:
                menu = menus[0]
                box["seen"] = True
                box["labels"] = [a.text() for a in menu.actions()]
                box["enabled"] = [bool(a.isEnabled()) for a in menu.actions()]
                menu.close()

        self.d.activate()
        point = self.element_point(node_js)
        if not point.get("ok"):
            return {"clicked": False, "point": point, "seen": False}
        QTimer.singleShot(settle_ms, inspect)
        os_click(point["x"], point["y"], "right")
        QTest.qWait(220)          # 让 `inspect` 在菜单模态循环里跑完 + 菜单收尾
        box["point"] = point
        box["clicked"] = True
        return box

    def _menu_verdict(self, cid: str, desc: str, result: dict, expect_menu: bool) -> None:
        """右击菜单的公共判定（中文 / 0 英文项 / 有无菜单）"""
        labels = [str(x) for x in result.get("labels") or []]
        # 分隔符的文案是空串 ⇒ 只对**非空**项做"是否中文"判定
        visible = [label for label in labels if label.strip()]
        forbidden = ("Back", "Forward", "Reload", "Save page", "View page source", "Inspect",
                     "Copy", "Paste", "Cut", "Undo", "Redo", "Select all", "Delete")
        english = [label for label in visible if label.strip() in forbidden]
        all_cjk = bool(visible) and all(
            any("\u4e00" <= ch <= "\u9fff" for ch in label) for label in visible
        )
        seen = bool(result.get("seen"))
        if expect_menu:
            ok = seen and bool(visible) and not english and all_cjk
        else:
            ok = bool(result.get("clicked")) and not seen
        detail = {"seen": seen, "menuCount": result.get("menuCount"), "labels": labels,
                  "enabled": result.get("enabled"), "englishItems": english,
                  "allCjk": all_cjk, "expectMenu": expect_menu, "point": result.get("point")}
        self.report.add(cid, desc, "PASS" if ok else "FAIL", detail)

    def c_input_menu(self) -> None:
        """AC-86③：登录页输入框右击 ⇒ 仍给中文菜单（D35② 的唯一例外）"""
        cid = "C-INPUT"
        if "C-INPUT" not in self.report.only:
            self.skip(cid, "输入框右击 ⇒ 中文菜单", "未选中", gating=False)
            return
        if not self.supported():
            self.skip(cid, "输入框右击 ⇒ 中文菜单", "需要真窗口 + Windows")
            return
        js_input = ("document.querySelector('input[type=password]')"
                    " || document.querySelector('input[type=text]')"
                    " || document.querySelector('input')")
        result = self._right_click_and_read_menu(js_input, settle_ms=900)
        self._menu_verdict(cid, "输入框右击 ⇒ 仍给**中文**菜单（唯一例外，D35②）",
                           result, expect_menu=True)

    def c_edit_menu(self) -> None:
        cid = "C-EDIT"
        if "C-EDIT" not in self.report.only:
            self.skip(cid, "编辑器正文右击 ⇒ 中文原生菜单", "未选中", gating=False)
            return
        if not self.supported():
            self.skip(cid, "编辑器正文右击 ⇒ 中文原生菜单", "需要真窗口 + Windows")
            return
        name, content = KEY_FIXTURES["font"]
        fixture = self.out / name
        fixture.write_text(content, encoding="utf-8")
        if not self.open_fixture(fixture):
            self.report.add(cid, "编辑器正文右击 ⇒ 中文原生菜单", "FAIL", {"why": "夹具没打开"})
            return

        js_editor_body = f"document.querySelector('{JS_ACTIVE} .cm-content')"
        result = self._right_click_and_read_menu(js_editor_body)
        self._menu_verdict(
            cid, "编辑器正文右击 ⇒ 出现**中文**原生菜单（0 英文项 / 0 浏览器导航项）",
            result, expect_menu=True,
        )
        self.shot("c1-editor-context-menu")

    def c_plain_menu(self) -> None:
        cid = "C-PLAIN"
        if "C-PLAIN" not in self.report.only:
            self.skip(cid, "非可编辑区右击 ⇒ 不弹任何菜单", "未选中", gating=False)
            return
        if not self.supported():
            self.skip(cid, "非可编辑区右击 ⇒ 不弹任何菜单", "需要真窗口 + Windows")
            return
        # 标签条（`.scratch-tab-bar`）是不可编辑区域；退路用面板根（同样不可编辑）
        js_tab_bar = (
            "document.querySelector('.scratch-tab-bar')"
            " || document.querySelector('[data-editor-panel]')"
        )
        result = self._right_click_and_read_menu(js_tab_bar, settle_ms=800)
        self._menu_verdict(
            cid, "非可编辑区（标签条）右击 ⇒ **不出菜单**", result, expect_menu=False,
        )
        self.shot("c2-plain-context-menu")

    # ── H：宿主静默 + 零远端 ──
    def h_quiet(self, phase_before: dict, gating: bool) -> None:
        remote = [u for u in self.downloads if u]
        recorder_urls = getattr(self.d, "_recorder_urls", None)
        fired = {name: n for name, n in self.counts.items() if n > phase_before.get(name, 0)}
        ok = (not fired) and self.d.window_alive()
        detail = {"hostActionFired": fired, "downloads": remote,
                  "recorderUrls": recorder_urls, "windowAlive": self.d.window_alive()}
        if recorder_urls is not None:
            detail["remoteRequestCount"] = len([u for u in recorder_urls
                                                if not str(u).startswith("file://")])
            ok = ok and detail["remoteRequestCount"] == 0
        self.report.add("H", "相位内 47 项宿主默认动作增量 = 0、零远端请求、主窗口存活",
                        "PASS" if ok else "FAIL", detail, gating=gating)


def make_fixtures(out_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for _item_id, (name, content) in KEY_FIXTURES.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths


def main() -> int:
    all_criteria = ("P0", "P1", "M1", "M2", "M3", "M4", "K-OUT", "C-INPUT", "C-EDIT", "C-PLAIN",
                    "H") + tuple(
        f"K-{key.upper()}" for _sid, key, _iid, _lbl, _exp in TOOLBOX_KEYS)

    parser = argparse.ArgumentParser(description="r22 真机探针：v1.6.2 编辑器四件事")
    parser.add_argument("--only", default=",".join(all_criteria),
                        help="只跑指定判据（逗号分隔；默认全部）")
    parser.add_argument("--out", default="", help="证据目录（默认 poc/editor-probes/out/r22-<时间戳>）")
    parser.add_argument("--webui", default="", help="前端产物根（默认 ui/dist）")
    parser.add_argument("--offscreen", action="store_true", help="无显示环境：真机类判据标 SKIP")
    parser.add_argument("--diag-menu", action="store_true",
                        help="诊断：打印 `contextMenuEvent` 是否被调用 / 规格函数的入参与结果")
    args = parser.parse_args()

    only = tuple(t.strip().upper() for t in args.only.split(",") if t.strip())
    bad = [t for t in only if t not in all_criteria]
    if bad:
        print(f"--only 取值非法：{bad}")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else PROBES / "out" / f"r22-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    real_window = not args.offscreen and not os.environ.get(
        "QT_QPA_PLATFORM", "").startswith("offscreen")

    # 用户目录沙箱：**必须在导入 src.config.settings 之前**改 APPDATA（里面有 session.json）
    appdata = out_dir / ".tmp_appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(appdata)
    os.environ["LOCALAPPDATA"] = str(appdata)

    gating = {"P0", "P1", "M1", "M2", "M3", "M4", "H", "C-INPUT", "C-EDIT", "C-PLAIN", "K-OUT"} | {
        f"K-{key.upper()}" for _sid, key, _iid, _lbl, _exp in TOOLBOX_KEYS}
    report = Report(out_dir, only, gating)
    report.say("r22 真机探针 · v1.6.2 工具箱可用性 / 对比几何 / 工具箱快捷键 / 右击菜单")
    report.say(
        f"平台={os.environ.get('QT_QPA_PLATFORM', '(默认)')} · 真窗口={real_window} · "
        f"判据={list(only)}"
    )
    report.say(f"证据目录：{out_dir}")

    web_root_info: dict = {}
    if args.webui:
        from src.ui import web_shell

        original = web_shell.resolve_web_root()
        target = Path(args.webui).resolve()
        web_shell.resolve_web_root = lambda: str(target)  # type: ignore[assignment]
        import hashlib

        index = target / "index.html"
        web_root_info = {
            "originalWebRoot": original, "patchedWebRoot": str(target),
            "indexExists": index.exists(),
            "indexSha256Prefix": hashlib.sha256(index.read_bytes()).hexdigest()[:16]
            if index.exists() else None,
        }
        report.say(f"前端产物根（--webui）：{json.dumps(web_root_info, ensure_ascii=False)}")

    try:
        r13 = load_r13()
    except Exception as exc:  # noqa: BLE001 - 依赖缺失要看清
        report.add("P0", "复用 r13 工具（importlib 加载）", "FAIL", {"error": str(exc)})
        report.write({})
        return 2

    if args.diag_menu:
        # 诊断（只在本探针内）：确认外壳的 `contextMenuEvent` 覆写**真的被调用**，
        # 并把规格函数的入参（可编辑标志 / 可用动作）打出来 —— 否则"没菜单"无法区分
        # "覆写没生效"与"覆写生效但判定了不弹"。
        from src.ui import web_shell as ws

        original_spec = ws.context_menu_spec
        original_ctx = ws.DropAwareWebView.contextMenuEvent

        def logged_spec(editable, allowed):
            result = original_spec(editable, allowed)
            report.say(f"[diag] context_menu_spec(editable={editable}, "
                       f"allowed={sorted(allowed)}) -> {result}")
            return result

        original_inputs = ws._context_menu_inputs  # noqa: SLF001 - 诊断用

        def logged_inputs(page):
            try:
                out = original_inputs(page)
            except Exception as exc:  # noqa: BLE001 - 诊断要看到原因
                report.say(f"[diag] _context_menu_inputs 抛错：{type(exc).__name__}: {exc}")
                raise
            report.say(f"[diag] _context_menu_inputs -> {out}")
            return out

        def logged_ctx(self, event):  # noqa: ANN001 - Qt 事件
            try:
                page = self.page()
            except Exception as exc:  # noqa: BLE001 - 诊断要看到原因
                page = None
                report.say(f"[diag] self.page() 抛错：{type(exc).__name__}: {exc}")
            report.say(f"[diag] contextMenuEvent 被调用；page={type(page).__name__ if page else None}")
            result = original_ctx(self, event)
            report.say("[diag] 原始 contextMenuEvent 已返回")
            return result

        ws.context_menu_spec = logged_spec  # type: ignore[assignment]
        ws._context_menu_inputs = logged_inputs  # type: ignore[assignment]  # noqa: SLF001
        ws.DropAwareWebView.contextMenuEvent = logged_ctx  # type: ignore[assignment]

    r13.ADMIN_USER = ADMIN_USER
    r13.ADMIN_PASSWORD = ADMIN_PASSWORD
    for name, vk in VK_ADDITIONS:
        r13._VK.setdefault(name, vk)  # noqa: SLF001 - 复用 r13 的虚拟键表
    report.say(f"复用 r13 OS 级输入原语；补齐虚拟键：{[n for n, _ in VK_ADDITIONS]}")

    try:
        app, shell, recorder, load_ok, downloads = r13.build()
    except Exception as exc:  # noqa: BLE001 - 环境问题要能看清原因
        import traceback

        traceback.print_exc()
        report.add("P0", "真外壳可构造", "FAIL", {"error": str(exc)})
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", "")})
        return 2

    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest

    QTimer.singleShot(900000, lambda: os._exit(3))  # 看门狗（15 分钟）

    action_counts = r13.install_host_action_watch(shell.view.page())
    driver = r13.R13(report, shell, out_dir, real_window, downloads, action_counts)
    driver._recorder = recorder  # noqa: SLF001
    probe = R22(report, driver, r13, shell, out_dir, real_window, downloads, action_counts)
    server_proc = None
    exit_code = 1

    if not load_ok:
        from src.ui.web_shell import resolve_index_html

        report.add("P0", "真外壳可构造", "FAIL",
                   {"load_ui": False, "index_html": resolve_index_html()})
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", "")})
        return 2

    try:
        QTest.qWait(1200)
        initial = driver.wait_for(r13.JS_UI_STATE,
                                  lambda v: bool(v.get("hash")) or v.get("r13Error"),
                                  timeout_ms=25000) or {}
        p0_ok = (initial.get("navEditor") is False and initial.get("workspaceCount") == 0
                 and initial.get("tabCount") == 0 and real_window)
        report.add("P0", "真外壳加载 v1.6.2 产物；初始态 = 未登录",
                   "PASS" if p0_ok else "FAIL",
                   {"load_ui": load_ok, "realWindow": real_window,
                    "hostActions": len(action_counts),
                    "state": {k: initial.get(k) for k in
                              ("hash", "navEditor", "workspaceCount", "tabCount")}})

        if not real_window:
            for cid in all_criteria:
                if cid in ("P0",):
                    continue
                report.add(cid, "（无真窗口 ⇒ 真机判据无法执行）", "SKIP",
                           "offscreen / 非 Windows 环境", gating=False)
            report.write({"platform": os.environ.get("QT_QPA_PLATFORM", ""),
                          "realWindow": real_window, **web_root_info})
            return 2

        # ── C-INPUT：登录页输入框的右击菜单（**必须在登录之前**跑：登录后登录框就没了）──
        probe.c_input_menu()

        server_proc, port, _server_root = r13.start_isolated_server(out_dir, report)
        driver._server = server_proc  # noqa: SLF001 - 复用 r13 的收尾约定
        url = (
            f"http://127.0.0.1:{port}"
            if (server_proc and port and r13.wait_health(port).get("ok"))
            else ""
        )

        make_fixtures(out_dir)  # 夹具落盘（各判据自己 open_fixture）
        p1_ok = False
        if url:
            login_detail = r13.python_side_login(url)
            reload_result = driver.reload_and_wait_gate() or {}
            report.note("登录：" + json.dumps(
                {k: login_detail.get(k) for k in ("loggedIn", "hasToken", "sessionFileExists")},
                ensure_ascii=False))
            if reload_result.get("navEditor") is True:
                # P1 只要求"能经生产通路开出标签页"（后续每条判据自己开夹具）
                p1_ok = True
                report.add("P1", "服务层真登录 + 重载 ⇒ 渲染层已登录（编辑器入口可见）",
                           "PASS", {"navEditor": True, "tabCount": reload_result.get("tabCount")})
            else:
                report.add("P1", "服务层真登录 + 重载 ⇒ 渲染层已登录", "FAIL",
                           {"afterReload": {k: reload_result.get(k) for k in
                                            ("hash", "navEditor", "tabCount")}})
        else:
            report.add("P1", "隔离服务端起不来（见 server.log）", "FAIL", {"port": port})

        phase_before = dict(action_counts)

        if not p1_ok:
            for cid in all_criteria:
                if cid in ("P0", "P1", "H"):
                    continue
                report.add(cid, "（前置未建立：渲染层不在可用工作区）", "SKIP",
                           "P1 未通过 ⇒ 无从判定（不是 PASS）", gating=False)
            report.add("H", "相位内宿主静默 + 零远端", "SKIP", "未进入相位", gating=False)
        else:
            # ── M1/M2：真鼠标点工具箱 → 点排序 ──
            if "M1" in only or "M2" in only:
                name, content = KEY_FIXTURES["lines"]
                fixture = out_dir / name
                fixture.write_text(content, encoding="utf-8")
                if probe.open_fixture(fixture):
                    m1 = probe.m_toolbox_click()
                    state = m1.get("state") or {}
                    ok1 = bool(state.get("itemCount") == 12 and state.get("visibleItems") == 12
                               and state.get("triggerAriaExpanded") == "true"
                               and state.get("menuInActivePanel"))
                    if "M1" in only:
                        report.add("M1", "**真鼠标**点「工具箱」⇒ 弹框出现且 12 项可见",
                                   "PASS" if ok1 else "FAIL", m1)
                    before = probe.read_text()
                    m2 = probe.m_sort_click(before)
                    if "M2" in only:
                        ok2 = bool(m2.get("changed")) and m2.get("after") == "a\nb"
                        report.add(
                            "M2", "**真鼠标**点「排序」⇒ 正文真的变成 `a\\nb`（本版核心缺陷的回归判据）",
                            "PASS" if ok2 else "FAIL", m2)
                    if "M4" in only:
                        status = str(m2.get("status") or "")
                        ok4 = bool(status) and any(ch.isdigit() for ch in status) and (
                            "已" in status) and ("Error" not in status)
                        report.add("M4", "动作后工具栏出现**中文**结果提示（含数字）",
                                   "PASS" if ok4 else "FAIL",
                                   {"status": status, "menuClosed": not m2.get("menuStillOpen")})
                    if "M3" in only:
                        m3 = probe.m_diff_click()
                        geom = m3.get("geometry") or {}
                        ratio = float(geom.get("heightRatio") or 0)
                        ok3 = bool(geom.get("overlay")) and ratio >= 0.9
                        report.add(
                            "M3", "**真鼠标**点「打开对比」⇒ 覆盖层铺满编辑面板（高度 ≥ 90%）",
                            "PASS" if ok3 else "FAIL", m3)
                else:
                    for cid in ("M1", "M2", "M3", "M4"):
                        if cid in only:
                            report.add(cid, "（夹具标签页没打开）", "FAIL", {"fixture": str(fixture)})
            else:
                for cid in ("M1", "M2", "M3", "M4"):
                    report.add(cid, "（未选中）", "SKIP", "未选中", gating=False)

            probe.k_toolbox_keys(only)
            probe.k_scope_out()
            probe.c_edit_menu()
            probe.c_plain_menu()
            probe.h_quiet(phase_before, gating="H" in only)

        exit_code = report.verdict()
    finally:
        if server_proc is not None:
            try:
                server_proc.terminate()
            except Exception:  # noqa: BLE001 - 收尾失败不影响判据
                pass
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", ""),
                      "realWindow": real_window, "webui": web_root_info})
        report.say(f"退出码：{exit_code}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
r20 真机探针：**六项新默认键**逐项生效 + `Ctrl+D` 反向守门（v1.6.1 · 用户反馈 F3 / AC-75② / BL-51）

## 为什么是**新探针**，而不是扩 `r13-ctrlw-takeover.py`

`r13` 的 H3b / H3d 用 **OS 级 `Ctrl+Alt+N`** 新建标签页当作"接管前置的阳性对照"
（`r13-ctrlw-takeover.py` 的 `h3b_new_tab`）。本轮的 **F3 把 `pane.new` 的默认键从
`Ctrl+Alt+N` 改成 `Ctrl+N`** ⇒ 那两条既有判据的**期望值已被产品改动作废**：
在 `ui/dist`（本轮 `vite build` 产物，比 `ui/src/config/shortcuts.ts` 新）上跑 `r13`，
`Ctrl+Alt+N` 不再命中任何绑定（`RUNTIME_SHORTCUTS` 里 `pane.new` 现在是 `Ctrl+N`），
标签数不会 +1 ⇒ **`r13` 的 H3b/H3d 现在必然 FAIL**（详见回执的"未决问题"）。

而派活约定是**"既有判据语义与期望值不得改，只新增"** ⇒ 既不能改 `r13` 的期望值，
也不能把它当成"六键生效"的证据载体。故新建本探针：**复用 `r13` 已验证的原语**
（OS 级 `keybd_event`、标题通道 `js()`、47 项 `WebAction` 计数、隔离服务端 + 真登录、
原生模态读数），只写"新默认键"这一套判据，`r13` 一字不动。

## 判据（逐项都可失败）

| id | 判据 |
|----|------|
| `P0` | 真外壳加载前端产物；初始态 = 未登录 |
| `P1` | 隔离服务端 + 服务层真登录 + 重载 ⇒ 渲染层已登录；经生产通路开 **2 个**标签页 |
| `K-NEWTAB` | OS 级 `Ctrl+N` ⇒ 标签数 **+1** |
| `K-NEXTTAB` | OS 级 `Ctrl+PageDown` ⇒ 激活项变到**顺序里的下一个** |
| `K-PREVTAB` | OS 级 `Ctrl+PageUp` ⇒ 激活项**回到原来的那个** |
| `K-TOOLBOX` | OS 级 `Ctrl+K` ⇒ 工具箱弹框**真的出现**（`[data-toolbox-menu="true"]` + 12 项可见） |
| `K-DIFF` | OS 级 `Ctrl+Shift+D` ⇒ 两段文本对比视图**真的出现**（`第二段来源` + `关闭对比`） |
| `K-OPENFILE` | OS 级 `Ctrl+O`：渲染层**收到该键且被我们接管**（`defaultPrevented=true`）、
 宿主 47 项动作计数 = 0；**原生对话框本身 SKIP**（无自动化接口），但要证明它**确实是原生模态** |
| `K-CTRLD-REVERSE` | OS 级 `Ctrl+D` **不被我们抢走**：同一单词出现两次 + 1 个 range 的选区 ⇒
 按一次后 range 数 1 → 2（CM6 `selectNextOccurrence`），且 diff 面板**没有**被打开 |
| `H-NOHOST` | 整个按键相位内：47 项 `WebAction` 触发计数 = 0、无下载、无**残留**宿主模态、主窗口存活 |

每条都记录：`reached` / `ctrled` / `prevented`（我们有没有接管）/ 捕获到的 keydown 原文 /
宿主动作计数（要求 0）/ 主窗口存活 / DOM 前后读数。**只打长度、指纹、flavor 名、布尔**（§9.14）。

## 怎么跑

    jflove-desktop\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r20-real-gui-keys.py
    ... --only K-TOOLBOX,K-DIFF      # 只跑某几条（其余标 SKIP）
    ... --webui ui\\dist             # 指定前端产物根（默认走 ui/dist）

## 退出码

- `0` 必须通过的判据全过（SKIP 不算失败）
- `1` 有必须通过的判据失败
- `2` 环境 / 脚本错误（UI 未加载、QtWebEngine 起不来、r13 原语缺失）
- `3` 看门狗超时

## 边界

- 只在自己的 `out/r20-<时间戳>/` 里写可入库证据；
- 沙箱 `APPDATA` 与隔离服务端副本一律用 `.tmp_` 前缀（`.gitignore` 的
  `jflove-desktop/poc/**/.tmp_*`），因为里面有 `session.json`（真实 JWT）与测试库；
- **不**启动单实例守卫 / 托盘 / 媒体浮层；不改任何产品源码。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
PROBES = SCRIPT.parent
DESKTOP = SCRIPT.parents[2]
REPO = SCRIPT.parents[3]

# Windows 控制台是 GBK：日志里有 ⇒/· 等字符，不重配置会 UnicodeEncodeError 中断探针
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

sys.path.insert(0, str(DESKTOP))

#: 探针自建的管理员凭据（只存在于本次运行的隔离库里，不写入仓库任何文件）
ADMIN_USER = "r20probe"
ADMIN_PASSWORD = "r20-probe-Passw0rd"

#: 全部判据 id（`--only` 的取值域）
ALL_CRITERIA = ("P0", "P1", "K-NEWTAB", "K-NEXTTAB", "K-PREVTAB", "K-TOOLBOX",
                "K-DIFF", "K-OPENFILE", "K-CTRLD-REVERSE", "H-NOHOST")

#: r13 的 `_VK` 里缺的虚拟键（`os_press` 只认这张表）
VK_ADDITIONS = (("o", 0x4F), ("k", 0x4B), ("d", 0x44),
                ("pagedown", 0x22), ("pageup", 0x21))

#: `Ctrl+D` 反向守门用的正文：同一个单词出现两次（**不是用户正文**，探针自造短串）
REVERSE_WORD = "alpha"
REVERSE_DOC = "alpha beta alpha gamma\n"


# ─────────────────────────── 判定与留证 ───────────────────────────


class Report:
    """收集每条判据的结果，最后写 JSON + 决定退出码（与 r13/r17 同口径，独立实现）。"""

    def __init__(self, out_dir: Path, only: tuple[str, ...]) -> None:
        self.out = out_dir
        self.only = only
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
            self.say(f"       {json.dumps(detail, ensure_ascii=False)[:1200]}")

    def verdict(self) -> int:
        failed = [c for c in self.checks if c["gating"] and c["status"] == "FAIL"]
        return 1 if failed else 0

    def write(self, extra: dict) -> Path:
        payload = {
            "probe": "r20-real-gui-keys",
            "at": datetime.now().isoformat(timespec="seconds"),
            "only": list(self.only),
            "checks": self.checks,
            "notes": self.notes,
            **extra,
        }
        path = self.out / "r20-result.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# ─────────────────────────── 复用 r13 的原语（在 main() 里绑定真身） ───────────────────────────


def load_r13():
    """以自定义模块名加载 r13（文件名含连字符，不能直接 `import`）。"""
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


# ─────────────────────────── 渲染层读数 ───────────────────────────

#: 标签条 + 工作区 + 模态（复用 r13 的 `JS_UI_STATE`，不另起一套 DOM 契约）
JS_UI_STATE_FROM_R13 = None  # 在 main() 里绑定 r13.JS_UI_STATE 的真身

#: 工具箱弹框（`ScratchToolbar.tsx:608-664` 的 DOM 契约）
#:
#: ⚠ **读数必须限定在"打开了弹框的那个工具栏"里**：非激活面板**也**挂着工具栏
#: （`hidden` 状态），它们的触发按钮 `aria-expanded` 恒为 `"false"`；若在全文档里
#: `querySelectorAll('button[aria-expanded]')` 取**最后一个**，会取到隐藏面板的按钮
#: ⇒ 明明弹框开着却读出 `"false"`（本轮首轮实测的**探针缺陷**，非产品缺陷）。
JS_TOOLBOX_STATE = r"""
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
                                   && owned.closest('[data-editor-panel-active="true"]')),
             labels: Array.prototype.map.call(items, function (n) {
               return n.getAttribute('data-toolbox-item');
             }).slice(0, 12) };
  } catch (e) { return { r20Error: String(e) }; }
})()
"""

#: 两段文本对比覆盖层（`DiffView.tsx:178-231` 的 DOM 契约）
JS_DIFF_STATE = r"""
(function () {
  try {
    var body = document.body ? (document.body.innerText || '') : '';
    var closeBtn = null;
    var btns = document.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) {
      if ((btns[i].textContent || '').trim() === '关闭对比') closeBtn = true;
    }
    return { hasSourceHeader: body.indexOf('第二段来源') >= 0,
             hasCloseButton: !!closeBtn,
             hasDiffHint: body.indexOf('选一种来源载入第二段文本') >= 0,
             bodyChars: body.length };
  } catch (e) { return { r20Error: String(e) }; }
})()
"""

#: `Esc` 关工具箱（走产品自己的关闭通路：`ScratchToolbar.tsx:330-340` 的 window 捕获监听）
JS_ESC_CLOSE = r"""
(function () {
  try {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    return { ok: true };
  } catch (e) { return { r20Error: String(e) }; }
})()
"""

#: 点「关闭对比」（产品自己的关闭按钮）
JS_CLICK_CLOSE_DIFF = r"""
(function () {
  try {
    var btns = document.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) {
      if ((btns[i].textContent || '').trim() === '关闭对比') { btns[i].click(); return { ok: true }; }
    }
    return { ok: false, why: 'no-close-button' };
  } catch (e) { return { r20Error: String(e) }; }
})()
"""

#: 按标题点标签（激活它）—— 与 r15 `js_click_tab` 同一口径
JS_CLICK_TAB_BY_TITLE = r"""
(function () {
  try {
    var want = %s;
    var nodes = document.querySelectorAll('[data-scratch-tab]');
    for (var i = 0; i < nodes.length; i++) {
      var t = (nodes[i].textContent || '').replace(/\u25cf/g, '').trim();
      if (t === want) { nodes[i].click(); return { ok: true, clicked: want }; }
    }
    return { ok: false, why: 'no-tab', titles: Array.prototype.map.call(nodes, function (n) {
      return (n.textContent || '').trim(); }) };
  } catch (e) { return { r20Error: String(e) }; }
})()
"""


def js_click_tab_title(title: str) -> str:
    return JS_CLICK_TAB_BY_TITLE % json.dumps(title, ensure_ascii=False)


def js_reset_doc_and_select() -> str:
    """把激活面板正文换成自造短串，并把选区设为**第一个单词**（1 个 range）。

    用 CM6 自己的事务写入（消费方是真实 `updateListener`）；只回长度 / 指纹 / range 数。
    """
    return (
        "(function(){try{"
        "var api=window.__JFLOVE_CM__;if(!api)return {r20Error:'no-bundle'};"
        "var panel=document.querySelector('[data-editor-panel-active=\"true\"]')"
        "||document.querySelector('[data-editor-panel]');"
        "if(!panel)return {r20Error:'no-panel'};"
        "var content=panel.querySelector('.cm-content');"
        "if(!content)return {r20Error:'no-content'};"
        "var view=api.EditorView.findFromDOM(content);if(!view)return {r20Error:'no-view'};"
        "var doc=%s;var word=%s;"
        "view.dispatch({changes:{from:0,to:view.state.doc.length,insert:doc}});"
        "view.dispatch({selection:{anchor:0,head:word.length}});"
        "view.focus();"
        "return {ok:true,docCodePoints:view.state.doc.length,"
        "rangeCount:view.state.selection.ranges.length,"
        "selectedChars:view.state.selection.main.to-view.state.selection.main.from};"
        "}catch(e){return {r20Error:String(e)};}})()"
        % (json.dumps(REVERSE_DOC), json.dumps(REVERSE_WORD))
    )


#: 读"文档 + 选区"（`Ctrl+D` 反向守门的关键读数：range 数 1 → 2）
JS_DOC_SELECTION = r"""
(function () {
  try {
    var api = window.__JFLOVE_CM__;
    if (!api) return { r20Error: 'no-bundle' };
    var panel = document.querySelector('[data-editor-panel-active="true"]')
      || document.querySelector('[data-editor-panel]');
    if (!panel) return { r20Error: 'no-panel' };
    var content = panel.querySelector('.cm-content');
    if (!content) return { r20Error: 'no-content' };
    var view = api.EditorView.findFromDOM(content);
    if (!view) return { r20Error: 'no-view' };
    var ranges = [];
    for (var i = 0; i < view.state.selection.ranges.length; i++) {
      ranges.push({ from: view.state.selection.ranges[i].from,
                    to: view.state.selection.ranges[i].to });
    }
    var text = view.state.doc.toString();
    var bytes = new TextEncoder().encode(text);
    var h = 0x811c9dc5;
    for (var b = 0; b < bytes.length; b++) {
      h = h ^ bytes[b];
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    return { ok: true, rangeCount: ranges.length, ranges: ranges,
             docCodePoints: [...text].length,
             docFnv1a32: ('00000000' + h.toString(16)).slice(-8),
             hasFocus: !!view.hasFocus };
  } catch (e) { return { r20Error: String(e) }; }
})()
"""


# ─────────────────────────── 宿主动作计数 ───────────────────────────


def reset_counts(counts: dict) -> dict:
    """把计数清零，并回一份"清零前"的快照（留证"哪些动作是登录/重载引起的"）。"""
    before = {name: n for name, n in counts.items() if n}
    for name in counts:
        counts[name] = 0
    return before


def fired(counts: dict) -> dict:
    return {name: n for name, n in counts.items() if n}


def tab_titles(state: dict) -> list[str]:
    """标签条顺序里的标题（**去掉脏标记圆点**，与 `js_click_tab_title` 的匹配口径一致）。"""
    return [(t.get("title") or "").replace("\u25cf", "").strip()
            for t in (state.get("tabs") or [])]


def active_title_of(state: dict) -> str | None:
    """当前激活标签的标题（同上口径；没有激活项回 `None`）。"""
    for tab in (state.get("tabs") or []):
        if tab.get("active"):
            return (tab.get("title") or "").replace("\u25cf", "").strip()
    return None


def modal_name() -> str | None:
    from PySide6.QtWidgets import QApplication

    modal = QApplication.activeModalWidget()
    return type(modal).__name__ if modal is not None else None


def close_modal() -> dict:
    """把**探针自己**打开的宿主模态关掉（只用于 Ctrl+O 的原生文件对话框）。"""
    from PySide6.QtWidgets import QApplication

    modal = QApplication.activeModalWidget()
    if modal is None:
        return {"closed": False, "why": "no-modal"}
    name = type(modal).__name__
    methods: list[str] = []
    for attr in ("reject", "close"):
        fn = getattr(modal, attr, None)
        if callable(fn):
            try:
                fn()
                methods.append(attr)
                break
            except Exception as exc:  # noqa: BLE001 - 关不掉就如实回报
                methods.append(f"{attr}-failed: {type(exc).__name__}")
    from PySide6.QtTest import QTest

    QTest.qWait(400)
    return {"closed": True, "modalClass": name, "methods": methods,
            "modalAfter": modal_name()}


# ─────────────────────────── 探针主体 ───────────────────────────


class R20:
    def __init__(self, report: Report, driver, r13, shell, out_dir: Path, real_window: bool,
                 downloads: list, action_counts: dict) -> None:
        self.report = report
        self.d = driver  # r13.R13 的实例：复用 js() / wait_for() / activate() / press_and_read()
        self.r13 = r13
        self.shell = shell
        self.out = out_dir
        self.real_window = real_window
        self.downloads = downloads
        self.counts = action_counts

    # ── 通用：一次 OS 级按键 + 读数 ──
    def press(self, mods: list[str], key: str, wanted: str, settle_ms: int = 1000) -> dict:
        before = dict(self.counts)
        press = self.d.press_and_read(mods, key, wanted, settle_ms=settle_ms)
        # **本次按键的增量**（相位内累计值会被上一条按键的残留污染 ⇒ 判据取增量）
        delta = {name: n - before.get(name, 0) for name, n in self.counts.items()}
        state = self.d.js(JS_UI_STATE_FROM_R13) or {}
        return {
            "reached": press.get("reached"), "ctrled": press.get("ctrled"),
            "prevented": press.get("prevented"),
            "shifted": any(k.get("shift") for k in (press.get("keys") or [])),
            "keys": press.get("keys"), "attempts": press.get("diag", {}).get("hit"),
            "hostActionDeltaTotal": sum(delta.values()),
            "hostActionDeltaFired": {n: v for n, v in delta.items() if v},
            "hostActionTotalInPhase": sum(self.counts.values()),
            "downloads": list(self.downloads), "modalWidget": modal_name(),
            "windowAlive": bool(press.get("windowAlive")) and self.d.window_alive(),
            "state": {k: state.get(k) for k in
                      ("hash", "tabCount", "tabs", "activePanelCount", "dialogLabel",
                       "workspaceCount")},
        }

    def supported(self) -> bool:
        return bool(self.real_window) and sys.platform.startswith("win")

    def shot(self, name: str) -> str:
        """截图（文件名用 `r20-` 前缀；r13 的 `shot()` 固定打 `r13-` 前缀，会与本探针证据混淆）。"""
        try:
            path = self.out / f"r20-{name}.png"
            self.shell.view.grab().save(str(path))
            return path.name
        except Exception as exc:  # noqa: BLE001 - 截图失败不影响判据
            return f"shot-failed: {exc}"

    def skip(self, cid: str, desc: str, why: str) -> None:
        self.report.add(cid, desc, "SKIP", {"why": why})

    # ── P1 前置：开 2 个标签页（走生产通路，不用快捷键）──
    def open_two_tabs(self, fixtures: list[Path]) -> dict:
        from PySide6.QtTest import QTest

        pushed = self.shell.notify_external_paths([str(f) for f in fixtures])
        state = self.d.wait_for(
            JS_UI_STATE_FROM_R13,
            lambda v: v.get("tabCount", 0) == len(fixtures),
            timeout_ms=30000, interval_ms=400) or {}
        QTest.qWait(900)  # 等过外壳的延迟重推（`OPEN_PATHS_RETRY_MS`）
        state = self.d.js(JS_UI_STATE_FROM_R13) or state
        ok = bool(pushed >= 1 and state.get("tabCount") == len(fixtures)
                  and state.get("activePanelCount", 0) >= 1
                  and self.d.window_alive())
        self.report.add(
            "P1",
            f"服务层真登录 + 重载 ⇒ 渲染层已登录；经生产通路开 {len(fixtures)} 个标签页"
            "（1 个激活面板、主窗口存活）",
            "PASS" if ok else "FAIL",
            {"pushedCount": pushed, "expectedTabCount": len(fixtures),
             "tabCount": state.get("tabCount"), "order": [t.get("title")
                                                          for t in (state.get("tabs") or [])],
             "activeTabId": state.get("activeTabId"),
             "activePanelCount": state.get("activePanelCount"),
             "hash": state.get("hash"), "windowAlive": self.d.window_alive()},
        )
        return state

    # ── K-NEWTAB ──
    def k_newtab(self) -> None:
        desc = "`pane.new` = `Ctrl+N`（本轮新默认）：OS 级按键 ⇒ 标签数 +1"
        if not self.supported():
            self.skip("K-NEWTAB", desc, "需要真窗口 + Windows（Qt 合成事件到不了渲染层）")
            return
        before = self.d.js(JS_UI_STATE_FROM_R13) or {}
        press = self.press(["ctrl"], "n", "n")
        after = self.d.wait_for(JS_UI_STATE_FROM_R13,
                                lambda v: v.get("tabCount", 0) > before.get("tabCount", 0),
                                timeout_ms=8000, interval_ms=300) or {}
        after = self.d.js(JS_UI_STATE_FROM_R13) or after
        ok = (press["reached"] is True and press["prevented"] is True
              and after.get("tabCount") == before.get("tabCount", 0) + 1
              and press["hostActionDeltaTotal"] == 0 and press["modalWidget"] is None
              and press["windowAlive"])
        self.report.add(
            "K-NEWTAB", desc, "PASS" if ok else "FAIL",
            {"beforeTabCount": before.get("tabCount"), "afterTabCount": after.get("tabCount"),
             "expectedAfterTabCount": (before.get("tabCount") or 0) + 1,
             "reached": press["reached"], "defaultPrevented": press["prevented"],
             "hostActionDeltaTotal": press["hostActionDeltaTotal"],
             "hostActionDeltaFired": press["hostActionDeltaFired"],
             "modalWidget": press["modalWidget"], "windowAlive": press["windowAlive"],
             "keys": press["keys"], "tabs": after.get("tabs")},
        )

    # ── K-NEXTTAB / K-PREVTAB ──
    def k_tab_nav(self) -> None:
        desc_next = "`pane.next` = `Ctrl+PageDown`（本轮新默认）：激活项变到顺序里的下一个"
        desc_prev = "`pane.prev` = `Ctrl+PageUp`（本轮新默认）：激活项回到原来的那个"
        if not self.supported():
            self.skip("K-NEXTTAB", desc_next, "需要真窗口 + Windows")
            self.skip("K-PREVTAB", desc_prev, "需要真窗口 + Windows")
            return
        from PySide6.QtTest import QTest

        state = self.d.js(JS_UI_STATE_FROM_R13) or {}
        titles = tab_titles(state)
        active = active_title_of(state)
        if len(titles) < 2 or active is None:
            why = f"标签数不足 2 或没有激活项（titles={titles}）"
            self.skip("K-NEXTTAB", desc_next, why)
            self.skip("K-PREVTAB", desc_prev, why)
            return
        # 先把**第一个**标签设为激活 ⇒ "下一个"才有唯一确定的方向
        clicked = self.d.js(js_click_tab_title(titles[0])) or {}
        QTest.qWait(600)
        start = self.d.js(JS_UI_STATE_FROM_R13) or {}
        start_active = active_title_of(start)
        next_press = self.press(["ctrl"], "pagedown", "pagedown")
        QTest.qWait(500)
        after_next = self.d.js(JS_UI_STATE_FROM_R13) or {}
        next_active = active_title_of(after_next)
        prev_press = self.press(["ctrl"], "pageup", "pageup")
        QTest.qWait(500)
        after_prev = self.d.js(JS_UI_STATE_FROM_R13) or {}
        prev_active = active_title_of(after_prev)
        expected_next = titles[1]
        ok_next = (start_active == titles[0] and next_active == expected_next
                   and next_press["reached"] is True and next_press["prevented"] is True
                   and next_press["hostActionDeltaTotal"] == 0
                   and next_press["modalWidget"] is None and next_press["windowAlive"])
        ok_prev = (prev_active == titles[0] and prev_press["reached"] is True
                   and prev_press["prevented"] is True
                   and prev_press["hostActionDeltaTotal"] == 0
                   and prev_press["modalWidget"] is None and prev_press["windowAlive"])
        self.report.add(
            "K-NEXTTAB", desc_next, "PASS" if ok_next else "FAIL",
            {"order": titles, "clickFirst": clicked, "activeAtStart": start_active,
             "expectedActive": expected_next, "activeAfter": next_active,
             "reached": next_press["reached"], "defaultPrevented": next_press["prevented"],
             "hostActionDeltaTotal": next_press["hostActionDeltaTotal"],
             "modalWidget": next_press["modalWidget"],
             "windowAlive": next_press["windowAlive"], "keys": next_press["keys"]},
        )
        self.report.add(
            "K-PREVTAB", desc_prev, "PASS" if ok_prev else "FAIL",
            {"order": titles, "activeBefore": next_active, "expectedActive": titles[0],
             "activeAfter": prev_active, "reached": prev_press["reached"],
             "defaultPrevented": prev_press["prevented"],
             "hostActionDeltaTotal": prev_press["hostActionDeltaTotal"],
             "modalWidget": prev_press["modalWidget"],
             "windowAlive": prev_press["windowAlive"], "keys": prev_press["keys"]},
        )

    # ── K-TOOLBOX（AC-75② / BL-51「配了没反应」的回归守门）──
    def k_toolbox(self) -> None:
        desc = ("AC-75② / BL-51 守门：`view.toolbox` = `Ctrl+K`（本轮新默认）⇒ "
                "工具箱弹框**真的出现**（`[data-toolbox-menu=\"true\"]` + 12 项可见）")
        if not self.supported():
            self.skip("K-TOOLBOX", desc, "需要真窗口 + Windows")
            return
        from PySide6.QtTest import QTest

        before = self.d.js(JS_TOOLBOX_STATE) or {}
        press = self.press(["ctrl"], "k", "k")
        QTest.qWait(600)
        after = self.d.wait_for(JS_TOOLBOX_STATE,
                                lambda v: v.get("menuCount", 0) >= 1,
                                timeout_ms=8000, interval_ms=300) or {}
        after = self.d.js(JS_TOOLBOX_STATE) or after
        ok = (press["reached"] is True and press["prevented"] is True
              and after.get("menuCount") == 1 and after.get("itemCount") == 12
              and after.get("visibleItems") == 12
              and after.get("triggerAriaExpanded") == "true"
              and press["hostActionDeltaTotal"] == 0 and press["modalWidget"] is None
              and press["windowAlive"])
        self.report.add(
            "K-TOOLBOX", desc, "PASS" if ok else "FAIL",
            {"menuCountBefore": before.get("menuCount"), "menuCountAfter": after.get("menuCount"),
             "itemCountAfter": after.get("itemCount"),
             "visibleItemsAfter": after.get("visibleItems"),
             "triggerAriaExpandedAfter": after.get("triggerAriaExpanded"),
             "menuInActivePanel": after.get("menuInActivePanel"),
             "itemIds": after.get("labels"),
             "reached": press["reached"], "defaultPrevented": press["prevented"],
             "hostActionDeltaTotal": press["hostActionDeltaTotal"],
             "hostActionDeltaFired": press["hostActionDeltaFired"],
             "modalWidget": press["modalWidget"], "windowAlive": press["windowAlive"],
             "keys": press["keys"],
             "screenshot": self.shot("k-toolbox")},
        )
        # 收尾：走产品自己的 Esc 关闭通路，避免影响后面的判据
        esc = self.d.js(JS_ESC_CLOSE) or {}
        QTest.qWait(500)
        closed = self.d.js(JS_TOOLBOX_STATE) or {}
        self.report.add(
            "K-TOOLBOX-CLOSE", "收尾（非判据）：`Esc` 关闭工具箱弹框（产品自己的关闭通路）",
            "PASS" if closed.get("menuCount") == 0 else "FAIL",
            {"esc": esc, "menuCountAfter": closed.get("menuCount")}, gating=False,
        )

    # ── K-DIFF ──
    def k_diff(self) -> None:
        desc = ("`view.diff` = `Ctrl+Shift+D`（本轮新默认，**不是** `Ctrl+D`）⇒ "
                "两段文本对比视图**真的出现**（`第二段来源` + `关闭对比`）")
        if not self.supported():
            self.skip("K-DIFF", desc, "需要真窗口 + Windows")
            return
        from PySide6.QtTest import QTest

        before = self.d.js(JS_DIFF_STATE) or {}
        press = self.press(["ctrl", "shift"], "d", "d")
        QTest.qWait(700)
        after = self.d.wait_for(JS_DIFF_STATE,
                                lambda v: v.get("hasSourceHeader") is True,
                                timeout_ms=8000, interval_ms=300) or {}
        after = self.d.js(JS_DIFF_STATE) or after
        ok = (press["reached"] is True and press["prevented"] is True
              and press["shifted"] is True
              and after.get("hasSourceHeader") is True
              and after.get("hasCloseButton") is True
              and press["hostActionDeltaTotal"] == 0 and press["modalWidget"] is None
              and press["windowAlive"])
        self.report.add(
            "K-DIFF", desc, "PASS" if ok else "FAIL",
            {"before": before, "after": after, "shiftSeenInCapture": press["shifted"],
             "reached": press["reached"], "defaultPrevented": press["prevented"],
             "hostActionDeltaTotal": press["hostActionDeltaTotal"],
             "hostActionDeltaFired": press["hostActionDeltaFired"],
             "modalWidget": press["modalWidget"], "windowAlive": press["windowAlive"],
             "keys": press["keys"],
             "screenshot": self.shot("k-diff")},
        )

    # ── K-CTRLD-REVERSE（反向守门：`Ctrl+D` 必须仍归 CM6）──
    def k_ctrld_reverse(self) -> None:
        desc = ("反向守门：`Ctrl+D` **不被我们抢走** —— 同一单词出现两次 + 1 个 range 的选区 ⇒ "
                "按一次后 range 数 1 → 2（CM6 `selectNextOccurrence`），且 diff 面板未打开")
        if not self.supported():
            self.skip("K-CTRLD-REVERSE", desc, "需要真窗口 + Windows")
            return
        from PySide6.QtTest import QTest

        # 先关掉上一条留下的 diff 面板（若在），避免"面板已开"污染判据
        diff_now = self.d.js(JS_DIFF_STATE) or {}
        if diff_now.get("hasSourceHeader"):
            self.d.js(JS_CLICK_CLOSE_DIFF)
            QTest.qWait(500)
        diff_before = self.d.js(JS_DIFF_STATE) or {}
        setup = self.d.js(js_reset_doc_and_select()) or {}
        QTest.qWait(300)
        before = self.d.js(JS_DOC_SELECTION) or {}
        press = self.press(["ctrl"], "d", "d")
        QTest.qWait(500)
        after = self.d.js(JS_DOC_SELECTION) or {}
        diff_after = self.d.js(JS_DIFF_STATE) or {}
        ok = (setup.get("ok") is True and before.get("rangeCount") == 1
              and after.get("rangeCount") == 2 and press["reached"] is True
              and diff_after.get("hasSourceHeader") is not True
              and after.get("docCodePoints") == before.get("docCodePoints")
              and press["hostActionDeltaTotal"] == 0 and press["modalWidget"] is None
              and press["windowAlive"])
        self.report.add(
            "K-CTRLD-REVERSE", desc, "PASS" if ok else "FAIL",
            {"setup": setup, "rangeCountBefore": before.get("rangeCount"),
             "rangesBefore": before.get("ranges"),
             "rangeCountAfter": after.get("rangeCount"), "rangesAfter": after.get("ranges"),
             "docCodePointsBefore": before.get("docCodePoints"),
             "docCodePointsAfter": after.get("docCodePoints"),
             "docFnv1a32Before": before.get("docFnv1a32"),
             "docFnv1a32After": after.get("docFnv1a32"),
             "diffOpenedByCtrlD": diff_after.get("hasSourceHeader"),
             "diffBeforeCtrlD": diff_before.get("hasSourceHeader"),
             "reached": press["reached"], "defaultPrevented": press["prevented"],
             "hostActionDeltaTotal": press["hostActionDeltaTotal"],
             "modalWidget": press["modalWidget"], "windowAlive": press["windowAlive"],
             "keys": press["keys"],
             "note": "`defaultPrevented` 是**window 捕获阶段**的读数；CM6 在更里层处理该键，"
                     "故它可能为 false 而选区已扩到 2 个 range —— 判据取 range 数与"
                     "「diff 面板没被打开」两者",
             "screenshot": self.shot("k-ctrld-reverse")},
        )

    # ── K-OPENFILE（原生对话框：接管 + 宿主零动作可判；对话框本身 SKIP）──
    def install_open_dialog_seam(self) -> dict:
        """把 `src/bridge/editor.py` 的**文档化测试接缝** `_open_file_names` 换成"用户取消"替身。

        ## 为什么必须换（本轮实测的硬约束，不是偷懒）

        `Ctrl+O` 被我们接管后会一路上桥调用 `QFileDialog.getOpenFileNames`（**原生模态**），
        而它是从**按键事件的派发调用栈**里进的 `exec()`：
        `QTest.qWait` → `processEvents` → 桥槽（`editor.openDialog` 在 GUI 线程内联）→ `exec()`
        ⇒ **`QTest.qWait` 永远不会返回**，探针里后续的"关对话框"代码根本没机会执行
        （本轮首轮实测：卡死在 14:16:44 的 `editor.openDialog`，只能等 15 分钟看门狗自杀）。

        接缝本身就写在产品源码里、并被明确标注为"**测试接缝**：替换本模块属性即可"
        （`src/bridge/editor.py:185-195`；同一注释还写明"直接 patch `QFileDialog` 会改变进程退出期
        的 Qt 析构时序（`0xC0000409`）"）。替身**只在探针进程内**替换模块属性，**不改产品源码**，
        且它自己就是最强证据：`_open_file_names` 被调用 ⇒ 按键确实被我们接管、并一路走到了
        原生对话框的调用点。

        对话框里的"选路径 / 确认"仍然 **SKIP**（无自动化接口，与 r15 的 AC71-7 同一既有约定）。
        """
        from src.bridge import editor as editor_bridge

        self._open_dialog_calls: list[dict] = []
        original = editor_bridge._open_file_names  # noqa: SLF001 - 产品源码里公开标注的测试接缝

        def fake_open(parent, title, directory, name_filter):
            # 只记"被调用了 + 标题 + 有没有 parent"，不读任何路径 / 文件内容（§9.14）
            self._open_dialog_calls.append({
                "title": title, "hasParent": parent is not None,
                "hasFilter": bool(name_filter),
            })
            return [], ""  # 等价于"用户在原生对话框里点了取消"

        editor_bridge._open_file_names = fake_open  # noqa: SLF001
        return {"seamInstalled": True, "originalIsCallable": callable(original),
                "seam": "src/bridge/editor.py::_open_file_names"}

    def k_openfile(self) -> None:
        desc = ("`file.open` = `Ctrl+O`（本轮新默认）：渲染层**收到该键且被我们接管**"
                "（`defaultPrevented=true`）、**接管逻辑一路走到桥的原生对话框调用点**、"
                "宿主 47 项动作计数 = 0；**原生对话框里的选路径 SKIP**（无自动化接口）")
        if not self.supported():
            self.skip("K-OPENFILE", desc, "需要真窗口 + Windows")
            return
        from PySide6.QtTest import QTest

        seam = self.install_open_dialog_seam()
        press = self.press(["ctrl"], "o", "o", settle_ms=1500)
        QTest.qWait(600)
        calls = list(self._open_dialog_calls)
        state = self.d.js(JS_UI_STATE_FROM_R13) or {}
        took_over = press["reached"] is True and press["prevented"] is True
        ran_to_dialog = bool(calls)
        quiet = (press["hostActionDeltaTotal"] == 0 and not press["downloads"]
                 and press["modalWidget"] is None and press["windowAlive"])
        self.report.add(
            "K-OPENFILE", desc, "PASS" if (took_over and ran_to_dialog and quiet) else "FAIL",
            {"seam": seam,
             "seamCalled": ran_to_dialog, "seamCalls": calls,
             "reached": press["reached"], "defaultPrevented": press["prevented"],
             "hostActionDeltaTotal": press["hostActionDeltaTotal"],
             "hostActionDeltaFired": press["hostActionDeltaFired"],
             "downloads": press["downloads"], "modalWidget": press["modalWidget"],
             "tabCountAfter": state.get("tabCount"),
             "windowAlive": press["windowAlive"], "keys": press["keys"]},
        )
        self.report.add(
            "K-OPENFILE-DIALOG",
            "`Ctrl+O` 原生 `QFileDialog` 的**选路径 / 确认** —— **SKIP**：探针不注入产品源码、"
            "也不具备原生对话框自动化能力 ⇒ 不伪造成 PASS（与 r15 的 AC71-7 同一既有约定）",
            "SKIP",
            {"why": "原生文件对话框无自动化接口；且从按键派发栈进 `exec()` 会让 `QTest.qWait` "
                    "永不返回（本轮实测卡死）⇒ 本判据走产品源码里公开标注的测试接缝",
             "productCallSite": "src/bridge/editor.py:278 的 `_open_file_names`"
                                "（:195 → `QFileDialog.getOpenFileNames`）",
             "whatIsProven": "按键送达 + 我们接管（preventDefault）+ 接管逻辑一路走到对话框调用点"
                             "（接缝被调用）+ 宿主 47 项动作计数 = 0 + 无宿主模态 + 窗口存活",
             "whatIsSkipped": "对话框内部交互（选文件、点打开/取消）"},
            gating=False,
        )

    # ── H-NOHOST ──
    def h_nohost(self, phase_before: dict, gating: bool = True) -> None:
        state = self.d.js(JS_UI_STATE_FROM_R13) or {}
        total = sum(self.counts.values())
        modal = modal_name()
        alive = self.d.window_alive()
        ok = (total == 0 and not self.downloads and modal is None and alive
              and state.get("dialogLabel") is None)
        self.report.add(
            "H-NOHOST",
            "整个按键相位：宿主 47 项 `WebAction` 触发计数 = 0、无下载、无**残留**宿主模态、"
            "无 DOM 模态、主窗口存活（`Ctrl+O` 打开的原生对话框已由探针关闭，另见 K-OPENFILE）",
            "PASS" if ok else "FAIL",
            {"webActionTotalInKeyPhase": total,
             "webActionByActionInKeyPhase": fired(self.counts),
             "instrumentedActionCount": len(self.counts),
             "phaseBeforeReset": phase_before,
             "downloads": list(self.downloads), "modalWidget": modal,
             "domDialogLabel": state.get("dialogLabel"), "windowAlive": alive},
            gating=gating,
        )


# ─────────────────────────── 启动 ───────────────────────────


def make_fixtures(out_dir: Path) -> list[Path]:
    """两个短小 fixture（**不含用户内容**）：给标签导航判据用。"""
    fixtures = out_dir / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for name, text in (("r20-key-a.txt", "r20 key probe A\n"),
                       ("r20-key-b.txt", "r20 key probe B\n")):
        path = fixtures / name
        path.write_text(text, encoding="utf-8")
        made.append(path)
    return made


def main() -> int:
    parser = argparse.ArgumentParser(
        description="r20 真机探针：六项新默认键生效 + Ctrl+D 反向守门（v1.6.1 F3 / BL-51）")
    parser.add_argument("--only", default=",".join(ALL_CRITERIA),
                        help="只跑指定判据（逗号分隔；默认全部）")
    parser.add_argument("--out", default="", help="证据目录（默认 poc/editor-probes/out/r20-<时间戳>）")
    parser.add_argument("--webui", default="",
                        help="前端产物根目录（默认走 ui/dist；<dir>/index.html 必须存在）")
    parser.add_argument("--offscreen", action="store_true", help="无显示环境：OS 按键类判据标 SKIP")
    args = parser.parse_args()

    only = tuple(t.strip().upper() for t in args.only.split(",") if t.strip())
    bad = [t for t in only if t not in ALL_CRITERIA]
    if bad:
        print(f"--only 取值非法：{bad}（合法：{list(ALL_CRITERIA)}）")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else PROBES / "out" / f"r20-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    real_window = not args.offscreen and not os.environ.get("QT_QPA_PLATFORM", "").startswith(
        "offscreen")

    # 用户目录沙箱：**必须在导入 src.config.settings 之前**改 APPDATA（会话 / 快捷键都落这里）。
    # `.tmp_` 前缀命中 .gitignore:82（里面有 session.json，严禁入库）
    appdata = out_dir / ".tmp_appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(appdata)
    os.environ["LOCALAPPDATA"] = str(appdata)

    report = Report(out_dir, only)
    report.say("r20 真机探针 · 六项新默认键（F3）+ `Ctrl+D` 反向守门 · v1.6.1")
    report.say(f"平台={os.environ.get('QT_QPA_PLATFORM', '(默认)')} · 真窗口={real_window} · "
               f"判据={list(only)}")
    report.say(f"证据目录：{out_dir}")

    web_root_info: dict = {}
    if args.webui:
        from src.ui import web_shell

        original = web_shell.resolve_web_root()
        target = Path(args.webui).resolve()
        web_shell.resolve_web_root = lambda: str(target)  # type: ignore[assignment]
        index = target / "index.html"
        import hashlib

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
    # 用自己的凭据，避免与 r13 的证据混淆（隔离库每次新建，账号只存在于本次运行的库内）
    r13.ADMIN_USER = ADMIN_USER
    r13.ADMIN_PASSWORD = ADMIN_PASSWORD
    global JS_UI_STATE_FROM_R13
    JS_UI_STATE_FROM_R13 = r13.JS_UI_STATE
    vk_added = [name for name, vk in VK_ADDITIONS if name not in r13._VK]  # noqa: SLF001
    for name, vk in VK_ADDITIONS:
        r13._VK.setdefault(name, vk)  # noqa: SLF001
    report.say(f"复用 r13 OS 级按键原语；补齐虚拟键：{vk_added}")

    try:
        app, shell, recorder, load_ok, downloads = r13.build()
    except Exception as exc:  # noqa: BLE001 - 环境问题要能看清原因
        import traceback

        traceback.print_exc()
        report.add("P0", "真外壳可构造（QtWebEngine + 桥 + 前端产物）", "FAIL", {"error": str(exc)})
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", "")})
        return 2

    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest

    QTimer.singleShot(900000, lambda: os._exit(3))  # 看门狗（15 分钟）

    action_counts = r13.install_host_action_watch(shell.view.page())
    driver = r13.R13(report, shell, out_dir, real_window, downloads, action_counts)
    probe = R20(report, driver, r13, shell, out_dir, real_window, downloads, action_counts)
    server_proc = None
    exit_code = 1

    if not load_ok:
        from src.ui.web_shell import resolve_index_html

        report.add("P0", "真外壳可构造（QtWebEngine + 桥 + 前端产物）", "FAIL",
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
        report.add(
            "P0", "真外壳加载 v1.6.1 前端产物；初始态 = 未登录（无编辑器入口 / 无工作区 DOM）",
            "PASS" if p0_ok else "FAIL",
            {"load_ui": load_ok, "realWindow": real_window, "hostActions": len(action_counts),
             "state": {k: initial.get(k) for k in
                       ("hash", "navEditor", "workspaceCount", "tabCount", "hasLoginForm")}},
        )

        # ── 隔离服务端 + 真登录 + 重载 ⇒ 渲染层已登录 ──
        server_proc, port, server_root = r13.start_isolated_server(out_dir, report)
        driver._server = server_proc  # noqa: SLF001 - 复用 r13 的收尾约定
        url = ""
        if server_proc is not None and port and r13.wait_health(port).get("ok"):
            url = f"http://127.0.0.1:{port}"

        fixtures = make_fixtures(out_dir)
        p1_state: dict = {}
        if url:
            login_detail = r13.python_side_login(url)
            reload_result = driver.reload_and_wait_gate()
            state_after = reload_result or {}
            report.note(
                "登录：" + json.dumps(
                    {k: login_detail.get(k) for k in
                     ("loggedIn", "hasToken", "sessionFileExists", "role")},
                    ensure_ascii=False))
            report.note(
                "重载后：" + json.dumps(
                    {k: state_after.get(k) for k in
                     ("hash", "navEditor", "workspaceCount", "tabCount")},
                    ensure_ascii=False))
            if state_after.get("navEditor") is True:
                p1_state = probe.open_two_tabs(fixtures)
            else:
                report.add("P1", "服务层真登录 + 重载 ⇒ 渲染层已登录；开 2 个标签页", "FAIL",
                           {"why": "重载后仍未出现「文本编辑器」导航项",
                            "afterReload": {"hash": state_after.get("hash"),
                                            "navEditor": state_after.get("navEditor")},
                            "login": {k: login_detail.get(k) for k in
                                      ("loggedIn", "hasToken", "sessionFileExists")}})
        else:
            report.add("P1", "服务层真登录 + 重载 ⇒ 渲染层已登录；开 2 个标签页", "FAIL",
                       {"why": "隔离服务端起不来（见 server.log）", "port": port})

        ready = bool(p1_state.get("tabCount", 0) >= 2)
        phase_before = reset_counts(action_counts)
        report.note(f"H-NOHOST 计数清零：清零前 {json.dumps(phase_before, ensure_ascii=False)}")

        def need(cid: str) -> bool:
            return cid in only

        if not ready:
            for cid in ALL_CRITERIA:
                if cid in ("P0", "P1", "H-NOHOST"):
                    continue
                if need(cid):
                    report.add(cid, "（前置未建立：渲染层不在可用工作区）", "SKIP",
                               "P1 未通过 ⇒ 无从判定（不是 PASS）")
            report.add("H-NOHOST", "按键相位宿主零默认动作", "SKIP", "未进入按键相位",
                       gating=False)
        else:
            if need("K-NEWTAB"):
                probe.k_newtab()
            else:
                report.add("K-NEWTAB", "`Ctrl+N` ⇒ 标签数 +1", "SKIP", "未选中")
            if need("K-NEXTTAB") or need("K-PREVTAB"):
                probe.k_tab_nav()
            else:
                report.add("K-NEXTTAB", "`Ctrl+PageDown` ⇒ 激活下一个", "SKIP", "未选中")
                report.add("K-PREVTAB", "`Ctrl+PageUp` ⇒ 回到上一个", "SKIP", "未选中")
            if need("K-TOOLBOX"):
                probe.k_toolbox()
            else:
                report.add("K-TOOLBOX", "`Ctrl+K` ⇒ 工具箱出现", "SKIP", "未选中")
            if need("K-DIFF"):
                probe.k_diff()
            else:
                report.add("K-DIFF", "`Ctrl+Shift+D` ⇒ 对比视图出现", "SKIP", "未选中")
            if need("K-CTRLD-REVERSE"):
                probe.k_ctrld_reverse()
            else:
                report.add("K-CTRLD-REVERSE", "`Ctrl+D` 反向守门", "SKIP", "未选中")
            # `Ctrl+O` 放最后：它可能打开原生模态对话框（判据已把对话框本身标 SKIP）
            if need("K-OPENFILE"):
                probe.k_openfile()
            else:
                report.add("K-OPENFILE", "`Ctrl+O` ⇒ 我们接管、宿主零动作", "SKIP", "未选中")
            probe.h_nohost(phase_before, gating=need("H-NOHOST"))

        exit_code = report.verdict()
    except Exception as exc:  # noqa: BLE001 - 任何未预期异常都留证
        import traceback

        traceback.print_exc()
        report.add("H9", "探针自身无未捕获异常", "FAIL", {"error": f"{type(exc).__name__}: {exc}"})
        exit_code = report.verdict() or 1

    server = getattr(driver, "_server", None)
    path = report.write({
        "platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "real_window": real_window,
        "webui": web_root_info,
        "requests": recorder.urls,
        "hostActionNames": sorted(action_counts),
        "vkAdded": vk_added,
        "user_data_sandbox": str(appdata),
        "isolatedServer": {"pid": server.pid if server else None},
    })
    report.say(f"结果 JSON：{path}")
    report.say(f"退出码：{exit_code}")

    if server is not None and server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:  # noqa: BLE001 - 收尾尽力而为
            server.kill()
    try:
        shell.view.page().profile().setUrlRequestInterceptor(None)
    except Exception:  # noqa: BLE001 - 窗口已销毁时不必再摘拦截器
        pass
    QTest.qWait(200)
    app.quit()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

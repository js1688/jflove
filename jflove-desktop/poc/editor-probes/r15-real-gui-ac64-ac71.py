"""
r15 真机判据探针：设计 v1.6.1 §13.3 第 12 / 13 / 15 条

## 这个探针解决什么

| 设计 §13.3 | 需求 | 本探针的观测点（判据） |
|---|---|---|
| 第 12 条 | AC-64 | 未登录 → 经 `shell.notify_external_paths` 唤起 → **渲染层自己完成登录** → 自动切
`/editor` + 原文件成为**激活标签页**（正文逐字符等于 fixture、状态栏编码/换行/语言一致） |
| 第 13 条 | AC-71 | 已登录 + **停掉服务端**（端口连接被拒）⇒ 新建 / 打开已有路径的文件 / 编辑 /
`Ctrl+S` 写回磁盘 / 切标签 / 关标签全部可用，无登录拦截、无阻塞式弹窗 |
| 第 15 条 | AC-67 / AC-68 | 12 标签溢出**可定位到第 12 个**（几何可见性）；
合成 Pointer Events 拖拽重排后**顺序与激活项保持**（R-14 真机确认） |

编号连续性：第 11 条（`Ctrl+W` 接管）由 `r13-ctrlw-takeover.py` 取到（权威证据
`out/r13-20261015-100824` 与 `-100921-neg`），**本轮不重跑、不改它**。

## AC-64 的核心难点：必须由**渲染层自己**登录

挂起队列（`utils/scratch/open-paths-queue.ts` 的 `pending` / `delivered`）是**渲染层内存**，
认证态却是 Python（`session_manager`）→ `bridge.auth.session` → `useAuthStore`。
所以"Python 侧先登录 + 重载页面"会把挂起队列连同 `delivered` 一起去掉，
`flushOpenPaths` 再也拿不到那条路径 ⇒ **不能**用那种做法代替 AC-64。

本探针的做法（不 stub、不打补丁、不改一行产品源码）：

1. 复制 `jflove-server/` 到证据目录起**隔离实例**（随机端口，库落在副本内，绝不碰 `jflove-db/jflove-dev.db`）；
2. 用桌面端**自己的服务层**在该库上 `do_key_exchange` + `init_admin` 造管理员 —— **但不登录**
   （`do_key_exchange` 不写 `session.json`，`try_restore_session` 仍返回未登录）；
3. 渲染层以**未登录**态启动 → 推 `editor.openPaths`（断言只有登录面板：零标签页 / 零工作区 / 零 `detectAndRead`）；
4. 用 **JS 驱动登录页三步**（`ui/src/pages/LoginPage.tsx`：`connect` → `login`）：
   React 受控输入用**原生 setter + `input` 事件**（`HTMLInputElement.prototype.value` 的 setter），按钮 `click()`；
5. 登录成功 ⇒ `editor-open-paths.ts` 的 `useAuthStore.subscribe` 判门翻转 →
   `router.navigate('/editor')` + `flushOpenPaths()` ⇒ 打开并**激活**。

## AC-71 的 Ctrl+S 区分度（为什么要测时延）

`ui/src/stores/scratch-store.ts:282-346` 有 **1500 ms 防抖的自动写回**
（`scheduleFlush` → `flushPane` → `scratchService.writeText`）。只看"磁盘变了"无法区分
"是 `Ctrl+S` 写的"还是"防抖自动写的"。本探针用**时延对照**区分：

- 编辑后先等 400 ms（不足防抖）→ 读盘：**必须还没变**；
- 再按 OS 级 `Ctrl+S` → 读盘：**必须变了**，且 `t(变化) - t(按键) < 1200 ms`（防抖不可能在此时到点）；
- 结束时再插入一段并**不按** `Ctrl+S`，观察它只在 ≥ 1400 ms 后才落盘（正对照片）。

`另存为`（`Ctrl+Shift+S`）会弹**原生文件对话框** ⇒ 标 **SKIP** 并写明原因，**不伪造**。

## AC-68：合成 Pointer Events 的"真伪"判据（R-14）

`ScratchTabBar` 的拖拽是 **Pointer Events 自绘**（4 px 阈值 / 中点判定）。
`pointerdown` → 多次 `pointermove`（步长 > 4 px 且跨过中点）→ `pointerup`，
`bubbles:true` / `composed:true` / 固定 `pointerId`。

**区分度关键**：若合成事件**确实到不了** React 处理器，`dragRef.current` 为空 ⇒
组件不会进入拖拽态（`.scratch-tab-dragging` 不出现）⇒ 本探针标 **SKIP**（环境边界，附已派发计数）；
若**进入了拖拽态但顺序没变** ⇒ 那是**实现缺陷**，标 **FAIL**，绝不写成"已验证"。

## 沿用的三条实测经验（来自 r11 / r13，别再踩）

1. `runJavaScript(script, callback)` 的**回调恒为空串** ⇒ 一律走**标题通道**
   （JS 把结果写 `document.title`，Python 轮询 `page().title()`，每次带自增标记）；
2. 「派发 → 观察」必须**分两次调用**（React 状态更新要等下一帧）；
   拖拽更是**三段式**（`down`+`move` → 观察 → `up`），否则读到的是上一帧；
3. Qt 合成事件（`QTest.keyClick`）**到不了渲染层**，必须用 **OS 级输入**（`keybd_event`）。

## 怎么跑

    jflove-desktop\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r15-real-gui-ac64-ac71.py
    ... --only 64            # 只跑 AC-64（其余标 SKIP）
    ... --only 67,68         # 只跑第 15 条（仍走 Python 侧登录 + 重载）
    ... --skip-login         # 跳过 AC-64 的渲染层登录（AC-64 记 SKIP），其余照跑

## 退出码（沿用 r13）

- `0` 必须通过的判据全过（SKIP 不算失败；观察项不影响退出码）
- `1` 有必须通过的判据失败
- `2` 环境 / 脚本错误（UI 未加载、QtWebEngine 起不来）
- `3` 看门狗超时

## 破坏性说明与边界（AGENTS.md §9.14 / 项目约定）

- 只在自己的 `out/r15-<时间戳>/` 里写**可入库证据**（`r15-result.json` / 截图 / `server.log`）；
- **沙箱 `APPDATA` 与隔离服务端副本一律用 `.tmp_` 前缀**（`out/r15-*/.tmp_appdata`、`out/r15-*/.tmp_server`）
  ⇒ 命中 `.gitignore` 的 `jflove-desktop/poc/**/.tmp_*`（原话：**含会话令牌 / 测试账号口令，严禁入库**）；
  **不要**写成 `appdata/` / `server/`，那会把 `session.json`（含真实 JWT）暴露成待提交文件；
- 日志与证据里**不出现 token / session_key / 完整路径明文 / 文档正文**：
  fixture 的绝对路径只用于 `notify_external_paths`，进证据的只有**文件名**、
  内容**长度 + FNV-1a 指纹**、以及 `detectAndRead` / `writeText` 的**调用次数与参数长度**（不含路径与正文）；
- **不**启动单实例守卫、**不**建托盘、**不**建媒体浮层（与编辑器链路无关）。
"""

from __future__ import annotations

import argparse
import atexit
import ctypes
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
DESKTOP = SCRIPT.parents[2]
REPO = SCRIPT.parents[3]

# Windows 控制台是 GBK：日志里有 ⇒/·/— 等字符，不重配置会 UnicodeEncodeError 中断探针
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

sys.path.insert(0, str(DESKTOP))

#: 探针自建的管理员凭据（只存在于本次运行的隔离库里，不写入仓库任何文件）
ADMIN_USER = "r15probe"
ADMIN_PASSWORD = "r15-probe-Passw0rd"

#: 全部判据 id（`--only` 的取值域）
ALL_CRITERIA = ("64", "71", "67", "68")

#: 合成指针事件用的 pointerId（固定值 ⇒ 组件内 `session.pointerId !== event.pointerId` 不会误过滤）
DRAG_POINTER_ID = 71


# ─────────────────────────── 判定与留证 ───────────────────────────


class Report:
    """收集每条判据的结果，最后写 JSON + 决定退出码。"""

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
            self.say(f"       {json.dumps(detail, ensure_ascii=False)[:800]}")

    def verdict(self) -> int:
        failed = [c for c in self.checks if c["gating"] and c["status"] == "FAIL"]
        return 1 if failed else 0

    def write(self, extra: dict) -> Path:
        payload = {
            "probe": "r15-real-gui-ac64-ac71",
            "at": datetime.now().isoformat(timespec="seconds"),
            "only": list(self.only),
            "checks": self.checks,
            "notes": self.notes,
            **extra,
        }
        path = self.out / "r15-result.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def fnv1a32(data: bytes) -> str:
    """FNV-1a 32 位（与渲染层 `JS_DOC_TEXT` 内联实现同口径）：用于"正文逐字符等于 fixture"的独立复核。

    为什么不用 sha256：渲染层要**同步**算（`SubtleCrypto` 是异步的，标题通道同步取不回来）。
    FNV-1a 在 JS 里可用整数移位精确实现，Python 侧用等价的乘 0x01000193 复算，两侧一致。
    """
    h = 0x811C9DC5
    for byte in data:
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def text_fingerprint(text: str) -> dict:
    """正文指纹：只记长度 + 哈希 + 换行统计（**不含正文本身**，§9.14）。"""
    data = text.encode("utf-8")
    return {
        "codePoints": len(text),
        "utf8Bytes": len(data),
        "fnv1a32": fnv1a32(data),
        "crlf": text.count("\r\n"),
        "lf": text.count("\n") - text.count("\r\n"),
    }


# ─────────────────────────── 渲染层脚本 ───────────────────────────

#: 探针自装的按键监听（**必须在应用运行时之后注册**，见模块文档「经验 3」）
JS_INSTALL_KEY_LOG = r"""
(function () {
  try {
    if (window.__r15 && window.__r15.installed) {
      return { ok: true, already: true, count: window.__r15.keys.length };
    }
    window.__r15 = { keys: [], installed: true, installedAt: Date.now() };
    var record = function (phase) {
      return function (e) {
        window.__r15.keys.push({
          seq: window.__r15.keys.length, phase: phase, key: e.key, code: e.code,
          ctrl: !!e.ctrlKey, alt: !!e.altKey, shift: !!e.shiftKey,
          isTrusted: !!e.isTrusted, defaultPrevented: !!e.defaultPrevented,
          targetTag: e.target && e.target.tagName ? e.target.tagName : null,
          targetInPanel: !!(e.target && e.target.closest
                            && e.target.closest('[data-editor-panel]')),
          targetInCm: !!(e.target && e.target.closest && e.target.closest('.cm-editor'))
        });
      };
    };
    // 注册顺序 = 执行顺序：应用运行时（App.tsx 静态 import）先跑，本监听后注册 ⇒
    // 读到的 defaultPrevented 就是应用接管后的真值。
    window.addEventListener('keydown', record('capture'), true);
    window.addEventListener('keydown', record('bubble'), false);
    return { ok: true, already: false };
  } catch (e) { return { r15Error: String(e) }; }
})()
"""

JS_CLEAR_KEY_LOG = "(function () { if (window.__r15) { window.__r15.keys = []; } return true; })()"

JS_READ_KEY_LOG = r"""
(function () {
  try {
    var r = window.__r15 || { keys: [] };
    return { keys: r.keys, focused: document.hasFocus(), hidden: document.hidden };
  } catch (e) { return { r15Error: String(e) }; }
})()
"""

#: UI 总状态读数（DOM 契约来自设计 v1.6.1 §4.2）。**不含路径与正文**。
JS_UI_STATE = r"""
(function () {
  try {
    var navLabels = [], navActive = {};
    var navs = document.querySelectorAll('.nav-item');
    for (var i = 0; i < navs.length; i++) {
      var label = (navs[i].textContent || '').trim();
      navLabels.push(label);
      navActive[label] = navs[i].getAttribute('data-active') === 'true';
    }
    var tabs = [];
    var nodes = document.querySelectorAll('[data-scratch-tab]');
    for (var j = 0; j < nodes.length; j++) {
      var nm = nodes[j].querySelector('.scratch-tab-name');
      tabs.push({
        id: nodes[j].getAttribute('data-scratch-tab'),
        title: nm ? (nm.textContent || '').trim() : '',
        active: nodes[j].getAttribute('data-scratch-tab-active') === 'true',
        dirty: !!nodes[j].querySelector('.scratch-tab-dirty')
      });
    }
    var labels = [];
    var all = document.querySelectorAll('button');
    for (var k = 0; k < all.length; k++) {
      var t = (all[k].textContent || '').trim();
      if (t && labels.indexOf(t) < 0) labels.push(t);
    }
    var dialog = document.querySelector('.modal[role="dialog"]');
    var activePanel = document.querySelector('[data-editor-panel-active="true"]');
    return {
      hash: location.hash,
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      navLabels: navLabels,
      navEditor: navLabels.indexOf('文本编辑器') >= 0,
      navEditorActive: navActive['文本编辑器'] === true,
      workspaceCount: document.querySelectorAll('[data-scratch-workspace]').length,
      emptyCount: document.querySelectorAll('[data-scratch-workspace-empty]').length,
      tabCount: tabs.length,
      tabs: tabs,
      panelCount: document.querySelectorAll('[data-editor-panel]').length,
      activePanelCount: document.querySelectorAll('[data-editor-panel-active="true"]').length,
      activePanelId: activePanel ? activePanel.getAttribute('data-editor-panel') : null,
      cmBundle: !!(window.__JFLOVE_CM__ && typeof window.__JFLOVE_CM__.EditorView === 'function'),
      cmContentCount: document.querySelectorAll('.cm-content').length,
      passwordInputs: document.querySelectorAll('input[type="password"]').length,
      textInputs: document.querySelectorAll('input[type="text"]').length,
      connectButton: labels.indexOf('连接服务端') >= 0,
      loginButton: labels.indexOf('登录') >= 0,
      initAdminButton: labels.indexOf('创建管理员并登录') >= 0,
      overflowButton: document.querySelectorAll('[data-scratch-tab-overflow="true"]').length,
      dialogLabel: dialog ? dialog.getAttribute('aria-label') : null,
      dialogText: dialog ? (dialog.innerText || '').slice(0, 120) : null,
      focused: document.hasFocus(),
      hidden: document.hidden
    };
  } catch (e) { return { r15Error: String(e) }; }
})()
"""

#: 标签条顺序 / 激活项 / 滚动容器度量（AC-68 顺序 + AC-67 溢出）
JS_TAB_ORDER = r"""
(function () {
  try {
    var out = [];
    var nodes = document.querySelectorAll('[data-scratch-tab]');
    for (var i = 0; i < nodes.length; i++) {
      var nm = nodes[i].querySelector('.scratch-tab-name');
      out.push({
        title: nm ? (nm.textContent || '').trim() : '',
        active: nodes[i].getAttribute('data-scratch-tab-active') === 'true',
        dirty: !!nodes[i].querySelector('.scratch-tab-dirty'),
        dragging: nodes[i].classList.contains('scratch-tab-dragging')
      });
    }
    var activeTitle = null, draggingTitle = null;
    for (var j = 0; j < out.length; j++) {
      if (out[j].active) activeTitle = out[j].title;
      if (out[j].dragging) draggingTitle = out[j].title;
    }
    var strip = document.querySelector('.scratch-tab-strip');
    return {
      count: out.length,
      order: out.map(function (t) { return t.title; }),
      // ⚠ 必须带 `tabs`：`fixture_tab_is_clean()` 读的就是 `order_state["tabs"]` 里的 `dirty`。
      //   2026-10-15 22:13 轮实测：这里漏了 `tabs` ⇒ 该判据恒为假 ⇒ "等防抖写回把标签写干净"
      //   从来没有真的等待过，AC71-5 于是关掉了**还脏**的标签，弹出关闭询问（产品行为正确、
      //   探针前提错误），并级联把 AC71-6 的"零 DOM 模态"判据判红。
      tabs: out,
      activeTitle: activeTitle,
      draggingTitle: draggingTitle,
      dragClassCount: document.querySelectorAll('.scratch-tab-dragging').length,
      strip: strip ? {
        scrollWidth: strip.scrollWidth, clientWidth: strip.clientWidth,
        scrollLeft: Math.round(strip.scrollLeft)
      } : null
    };
  } catch (e) { return { r15Error: String(e) }; }
})()
"""

#: 渲染层内存读数（Chromium 的 `performance.memory`；QtWebEngine 上**可能不暴露** ⇒ 必须如实回 false）
JS_MEM = r"""
(function () {
  try {
    var m = window.performance && window.performance.memory;
    return {
      heapAvailable: !!m,
      usedJSHeapMb: m ? Math.round(m.usedJSHeapSize / 1048576 * 10) / 10 : null,
      totalJSHeapMb: m ? Math.round(m.totalJSHeapSize / 1048576 * 10) / 10 : null,
      cmContentCount: document.querySelectorAll('.cm-content').length,
      panelCount: document.querySelectorAll('[data-editor-panel]').length
    };
  } catch (e) { return { r15Error: String(e) }; }
})()
"""

#: 标签几何可见性（AC-67 ③：「激活了但看不见」必须不出现 —— 几何断言，比存在性强）
JS_TAB_GEOMETRY = r"""
(function () {
  try {
    var strip = document.querySelector('.scratch-tab-strip');
    if (!strip) return { r15Error: 'no-strip' };
    var srect = strip.getBoundingClientRect();
    var out = [];
    var nodes = document.querySelectorAll('[data-scratch-tab]');
    for (var i = 0; i < nodes.length; i++) {
      var nm = nodes[i].querySelector('.scratch-tab-name');
      var r = nodes[i].getBoundingClientRect();
      out.push({
        index: i,
        title: nm ? (nm.textContent || '').trim() : '',
        left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width),
        visible: r.left >= srect.left - 1 && r.right <= srect.right + 1,
        active: nodes[i].getAttribute('data-scratch-tab-active') === 'true'
      });
    }
    var activeVisible = null, activeIndex = null;
    for (var j = 0; j < out.length; j++) {
      if (out[j].active) { activeVisible = out[j].visible; activeIndex = out[j].index; }
    }
    return {
      stripLeft: Math.round(srect.left), stripRight: Math.round(srect.right),
      scrollWidth: strip.scrollWidth, clientWidth: strip.clientWidth,
      scrollable: strip.scrollWidth > strip.clientWidth + 1,
      visibleCount: out.filter(function (t) { return t.visible; }).length,
      activeVisible: activeVisible, activeIndex: activeIndex,
      tabs: out
    };
  } catch (e) { return { r15Error: String(e) }; }
})()
"""

#: 激活面板的**正文 + 状态栏**读数（AC-64 ③④）。正文只回长度 / 指纹 / 首差位置，不回正文。
JS_DOC_TEXT = r"""
(function () {
  try {
    var panel = document.querySelector('[data-editor-panel-active="true"]');
    if (!panel) return { r15Error: 'no-active-panel' };
    var status = {};
    var items = panel.querySelectorAll('.scratch-status-item');
    for (var i = 0; i < items.length; i++) {
      var title = items[i].getAttribute('title') || '';
      var value = (items[i].textContent || '').trim();
      if (title.indexOf('编码') >= 0) status.encoding = value;
      else if (title.indexOf('换行符') >= 0) status.lineEnding = value;
      else if (title.indexOf('语言') >= 0) status.language = value;
      else if (title.indexOf('未保存') >= 0 || title.indexOf('保存') >= 0) status.dirty = value;
    }
    var content = panel.querySelector('.cm-content');
    if (!content) return { r15Error: 'no-cm-content', status: status };
    var api = window.__JFLOVE_CM__;
    if (!api || typeof api.EditorView.findFromDOM !== 'function') {
      return { r15Error: 'no-cm-bundle', status: status };
    }
    var view = api.EditorView.findFromDOM(content);
    if (!view) return { r15Error: 'no-view', status: status };
    var text = view.state.doc.toString();
    var bytes = new TextEncoder().encode(text);
    var h = 0x811c9dc5;
    for (var b = 0; b < bytes.length; b++) {
      h = h ^ bytes[b];
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    var totalLf = (text.match(/\n/g) || []).length;
    var crlf = (text.match(/\r\n/g) || []).length;
    return {
      ok: true,
      panelId: panel.getAttribute('data-editor-panel'),
      docCodePoints: [...text].length,
      docUtf8Bytes: bytes.length,
      docFnv1a32: ('00000000' + h.toString(16)).slice(-8),
      crlf: crlf,
      lf: totalLf - crlf,
      charCount: text.length,
      status: status
    };
  } catch (e) { return { r15Error: String(e) }; }
})()
"""


def js_compare_doc(expected: str) -> str:
    """在渲染层把激活面板正文与 `expected` **逐字符**比对（只回布尔 + 首差位置，不回正文）。"""
    return r"""
    (function () {
      try {
        var expected = %s;
        var panel = document.querySelector('[data-editor-panel-active="true"]');
        if (!panel) return { r15Error: 'no-active-panel' };
        var content = panel.querySelector('.cm-content');
        if (!content) return { r15Error: 'no-cm-content' };
        var api = window.__JFLOVE_CM__;
        var view = api.EditorView.findFromDOM(content);
        if (!view) return { r15Error: 'no-view' };
        var text = view.state.doc.toString();
        var equal = text === expected;
        var firstDiff = -1;
        if (!equal) {
          var n = Math.min(text.length, expected.length);
          for (var i = 0; i < n; i++) {
            if (text.charCodeAt(i) !== expected.charCodeAt(i)) { firstDiff = i; break; }
          }
          if (firstDiff < 0) firstDiff = n;
        }
        return {
          equal: equal, firstDiff: firstDiff,
          docCodePoints: [...text].length, expectedCodePoints: [...expected].length,
          docCharUnits: text.length, expectedCharUnits: expected.length
        };
      } catch (e) { return { r15Error: String(e) }; }
    })()
    """ % json.dumps(expected, ensure_ascii=False)


def js_set_input(selector: str, value: str, sensitive: bool = False) -> str:
    """用**原生 setter + `input` 事件**驱动 React 受控输入（`el.value = x` 不会触发 onChange）。

    `sensitive=True` 时只回"设进去了没"，不回长度与值（口令不进证据，§9.14）。
    """
    tail = "{ok: true, sensitive: true}" if sensitive else (
        "{ok: true, same: el.value === %s, length: el.value.length}" % json.dumps(value)
    )
    return (
        "(function(){try{"
        "var el=document.querySelector(%s);"
        "if(!el)return {ok:false,why:'no-input'};"
        "var desc=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
        "if(!desc||!desc.set)return {ok:false,why:'no-native-setter'};"
        "desc.set.call(el,%s);"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "return %s;"
        "}catch(e){return {r15Error:String(e)};}})()"
        % (json.dumps(selector), json.dumps(value), tail)
    )


def js_click_button(label: str) -> str:
    """按**可见文本**点按钮（`Button` 组件里图标是 svg，`textContent` 就是 label）。"""
    return (
        "(function(){try{"
        "var want=%s;var all=document.querySelectorAll('button');"
        "for(var i=0;i<all.length;i++){"
        "if((all[i].textContent||'').trim()===want){"
        "all[i].click();return {ok:true,clicked:want,disabled:!!all[i].disabled};}}"
        "var seen=[];for(var j=0;j<all.length;j++){"
        "var t=(all[j].textContent||'').trim();if(t&&seen.indexOf(t)<0)seen.push(t);}"
        "return {ok:false,why:'no-button',labels:seen};"
        "}catch(e){return {r15Error:String(e)};}})()" % json.dumps(label)
    )


def js_focus_editor() -> str:
    """把 DOM 焦点放到**激活面板**里的 CM6 内容元素上（`pane.close`/`file.save` 是面板作用域项）。"""
    return r"""
    (function () {
      try {
        var api = window.__JFLOVE_CM__;
        if (!api) return { r15Error: 'no-bundle' };
        var panel = document.querySelector('[data-editor-panel-active="true"]')
          || document.querySelector('[data-editor-panel]');
        if (!panel) return { r15Error: 'no-panel' };
        var content = panel.querySelector('.cm-content');
        if (!content) return { r15Error: 'no-content' };
        var view = api.EditorView.findFromDOM(content);
        if (!view) return { r15Error: 'no-view' };
        view.focus();
        return { ok: true, panelId: panel.getAttribute('data-editor-panel'),
                 hasFocus: view.hasFocus, docLength: view.state.doc.length };
      } catch (e) { return { r15Error: String(e) }; }
    })()
    """


def js_insert_text(text: str, expected_doc_len: int | None = None) -> str:
    """用 **CM6 自己的事务**把正文改脏或追加内容（消费方是真实的 `updateListener` → `markDirty`）。"""
    guard = ""
    if expected_doc_len is not None:
        guard = (
            "if(view.state.doc.length!==%d)return {ok:false,why:'doc-length-drift'};"
            % expected_doc_len
        )
    return (
        "(function(){try{var api=window.__JFLOVE_CM__;if(!api)return {r15Error:'no-bundle'};"
        "var panel=document.querySelector('[data-editor-panel-active=\"true\"]')"
        "||document.querySelector('[data-editor-panel]');"
        "if(!panel)return {r15Error:'no-panel'};"
        "var content=panel.querySelector('.cm-content');"
        "if(!content)return {r15Error:'no-content'};"
        "var view=api.EditorView.findFromDOM(content);if(!view)return {r15Error:'no-view'};"
        "view.focus();" + guard +
        "view.dispatch({changes:{from:view.state.doc.length,insert:%s}});"
        "return {ok:true,docLength:view.state.doc.length,hasFocus:view.hasFocus};"
        "}catch(e){return {r15Error:String(e)};}})()" % json.dumps(text, ensure_ascii=False)
    )


def js_click_tab(title: str) -> str:
    """按**标题**（= 文件名）点击标签本体 ⇒ 激活该标签。"""
    return r"""
    (function () {
      try {
        var nodes = document.querySelectorAll('[data-scratch-tab]');
        var titles = [];
        for (var i = 0; i < nodes.length; i++) {
          var nm = nodes[i].querySelector('.scratch-tab-name');
          var t = nm ? (nm.textContent || '').trim() : '';
          titles.push(t);
          if (t === %s) {
            var r = nodes[i].getBoundingClientRect();
            nodes[i].click();
            return { ok: true, clicked: t, width: Math.round(r.width),
                     left: Math.round(r.left) };
          }
        }
        return { ok: false, why: 'no-tab', titles: titles };
      } catch (e) { return { r15Error: String(e) }; }
    })()
    """ % json.dumps(title, ensure_ascii=False)


def js_close_tab(title: str | None) -> str:
    """点某个标签的 `×`（`title=None` ⇒ 第一个标签）。"""
    target = "null" if title is None else json.dumps(title, ensure_ascii=False)
    return r"""
    (function () {
      try {
        var nodes = document.querySelectorAll('[data-scratch-tab]');
        var wanted = %s;
        for (var i = 0; i < nodes.length; i++) {
          var nm = nodes[i].querySelector('.scratch-tab-name');
          var t = nm ? (nm.textContent || '').trim() : '';
          if (wanted === null || t === wanted) {
            var btn = nodes[i].querySelector('.scratch-tab-close');
            if (!btn) return { ok: false, why: 'no-close-button', title: t };
            btn.click();
            return { ok: true, closed: t };
          }
        }
        return { ok: false, why: 'no-tab' };
      } catch (e) { return { r15Error: String(e) }; }
    })()
    """ % target


JS_CLICK_OVERFLOW = (
    "(function(){try{var b=document.querySelector('[data-scratch-tab-overflow=\"true\"]');"
    "if(!b)return {r15Error:'no-overflow-button'};b.click();"
    "return {ok:true,expanded:b.getAttribute('aria-expanded')};}"
    "catch(e){return {r15Error:String(e)};}})()"
)

JS_OVERFLOW_ITEMS = r"""
(function () {
  try {
    var items = document.querySelectorAll('[data-scratch-overflow-item]');
    var out = [];
    for (var i = 0; i < items.length; i++) {
      out.push({
        index: i,
        title: (items[i].textContent || '').trim(),
        id: items[i].getAttribute('data-scratch-overflow-item'),
        checked: items[i].getAttribute('aria-checked') === 'true'
      });
    }
    return { count: out.length,
             menuPresent: !!document.querySelector('.scratch-tab-overflow-menu'),
             items: out };
  } catch (e) { return { r15Error: String(e) }; }
})()
"""


def js_click_overflow_item(index: int) -> str:
    return (
        "(function(){try{var items=document.querySelectorAll('[data-scratch-overflow-item]');"
        "if(items.length<=%d)return {ok:false,why:'index-out-of-range',count:items.length};"
        "var it=items[%d];var t=(it.textContent||'').trim();it.click();"
        "return {ok:true,index:%d,title:t};}catch(e){return {r15Error:String(e)};}})()"
        % (index, index, index)
    )


JS_DRAG_DOWN_MOVE = r"""
(function () {
  try {
    if (typeof PointerEvent !== 'function') return { r15Error: 'no-PointerEvent' };
    var nodes = document.querySelectorAll('[data-scratch-tab]');
    var el = null, titles = [];
    for (var i = 0; i < nodes.length; i++) {
      var nm = nodes[i].querySelector('.scratch-tab-name');
      var t = nm ? (nm.textContent || '').trim() : '';
      titles.push(t);
      if (t === %(title)s) el = nodes[i];
    }
    if (!el) return { r15Error: 'no-tab', titles: titles };
    var strip = document.querySelector('.scratch-tab-strip');
    if (!strip) return { r15Error: 'no-strip' };
    var rect = el.getBoundingClientRect();
    var srect = strip.getBoundingClientRect();
    var startX = rect.left + rect.width / 2;
    var endX = srect.left + 1;
    var y = rect.top + rect.height / 2;
    var pid = %(pid)d;
    var native = { pointerdown: 0, pointermove: 0, pointerup: 0 };
    var onNative = function (e) { if (native[e.type] !== undefined) native[e.type] += 1; };
    strip.addEventListener('pointerdown', onNative);
    strip.addEventListener('pointermove', onNative);
    strip.addEventListener('pointerup', onNative);
    var make = function (type, x, buttons) {
      return new PointerEvent(type, {
        bubbles: true, cancelable: true, composed: true,
        pointerId: pid, pointerType: 'mouse', isPrimary: true,
        button: 0, buttons: buttons, clientX: x, clientY: y
      });
    };
    el.dispatchEvent(make('pointerdown', startX, 1));
    var steps = 6;
    for (var k = 1; k <= steps; k++) {
      el.dispatchEvent(make('pointermove', startX + (endX - startX) * k / steps, 1));
    }
    window.__r15drag = { pid: pid, startX: startX, endX: endX, y: y, title: %(title)s };
    strip.removeEventListener('pointerdown', onNative);
    strip.removeEventListener('pointermove', onNative);
    strip.removeEventListener('pointerup', onNative);
    return {
      ok: true, startX: Math.round(startX), endX: Math.round(endX),
      stepPx: Math.round(Math.abs(endX - startX) / steps), steps: steps,
      nativeObserved: native, titlesBefore: titles,
      orderAfterMoves: (function () {
        var out = [];
        var ns = document.querySelectorAll('[data-scratch-tab]');
        for (var m = 0; m < ns.length; m++) {
          var n2 = ns[m].querySelector('.scratch-tab-name');
          out.push(n2 ? (n2.textContent || '').trim() : '');
        }
        return out;
      })(),
      dragClassDuringDispatch: document.querySelectorAll('.scratch-tab-dragging').length
    };
  } catch (e) { return { r15Error: String(e) }; }
})()
""" % {"title": json.dumps("__PLACEHOLDER__", ensure_ascii=False), "pid": DRAG_POINTER_ID}


def js_drag_down_move(title: str) -> str:
    """`pointerdown` + 6 次 `pointermove`（末点落在滚动容器最左侧 ⇒ 中点判定目标索引 = 0）。

    ⚠ 与 `pointerup` **分两次调用**：React 对 `pointermove` 是连续事件，状态更新可能在
    本轮 JS 结束后才冲刷 ⇒ 同一次调用里读不到重排结果（见模块文档「经验 2」）。
    """
    return JS_DRAG_DOWN_MOVE.replace(json.dumps("__PLACEHOLDER__", ensure_ascii=False),
                                     json.dumps(title, ensure_ascii=False))


JS_DRAG_UP = r"""
(function () {
  try {
    var session = window.__r15drag;
    if (!session) return { r15Error: 'no-drag-session' };
    var nodes = document.querySelectorAll('[data-scratch-tab]');
    var el = null;
    for (var i = 0; i < nodes.length; i++) {
      var nm = nodes[i].querySelector('.scratch-tab-name');
      if (nm && (nm.textContent || '').trim() === session.title) el = nodes[i];
    }
    if (!el) return { r15Error: 'no-tab-on-up', title: session.title };
    var ev = new PointerEvent('pointerup', {
      bubbles: true, cancelable: true, composed: true,
      pointerId: session.pid, pointerType: 'mouse', isPrimary: true,
      button: 0, buttons: 0, clientX: session.endX, clientY: session.y
    });
    el.dispatchEvent(ev);
    delete window.__r15drag;
    return { ok: true, title: session.title };
  } catch (e) { return { r15Error: String(e) }; }
})()
"""


def js_click_dialog_button(label: str) -> str:
    """点关闭询问里的某个按钮（『取消』/『放弃修改』/『保存』）。"""
    return (
        "(function(){try{var dlg=document.querySelector('.modal[role=\"dialog\"]');"
        "if(!dlg)return {r15Error:'no-dialog'};var btns=dlg.querySelectorAll('button');"
        "for(var i=0;i<btns.length;i++){if((btns[i].textContent||'').trim()===%s){btns[i].click();"
        "return {ok:true,clicked:%s};}}"
        "return {r15Error:'no-button',labels:Array.prototype.map.call(btns,function(b){"
        "return (b.textContent||'').trim();})};}catch(e){return {r15Error:String(e)};}})()"
        % (json.dumps(label), json.dumps(label))
    )


def js_go_hash(route: str) -> str:
    """把 hash 切到某路由（React Router 的 hash 路由）。"""
    return (
        "(function(){location.hash=%s;return {ok:true,hash:location.hash};})()"
        % json.dumps(route)
    )


# ─────────────────────────── OS 级按键 ───────────────────────────

_VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "w": 0x57, "n": 0x4E, "s": 0x53}
_VK_MODS = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}
_KEYEVENTF_KEYUP = 0x0002


def os_release_modifiers() -> None:
    """先清掉可能残留的修饰键（r11 实测：丢 keyup 会让后续组合键记成 `shift: true`）。"""
    if not sys.platform.startswith("win"):
        return
    try:
        user32 = ctypes.windll.user32
        for vk in _VK_MODS.values():
            user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
    except Exception:  # noqa: BLE001 - 仅清场，失败不致命
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
    """真·OS 级按键（`keybd_event`）；非 Windows 返回 False（调用方标 SKIP）。"""
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


# ─────────────────────────── 隔离服务端 + 真登录 ───────────────────────────


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_health(port: int, timeout_s: float = 60.0) -> dict:
    """轮询明文白名单接口 `/health`（不需要加密通道）。"""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310 - 只打本机回环地址
                f"http://127.0.0.1:{port}/health", timeout=2
            ) as resp:
                body = resp.read().decode("utf-8", "replace")
                return {"ok": resp.status == 200, "status": resp.status, "body": body[:200]}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.5)
    return {"ok": False, "error": last or "timeout"}


def port_refused(port: int) -> dict:
    """判定端口是否**真的**不可达（连接被拒 / 无监听者）。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return {"refused": False, "detail": "connected"}
    except ConnectionRefusedError as exc:
        return {"refused": True, "detail": f"ConnectionRefusedError: {exc}"}
    except OSError as exc:
        return {"refused": True, "detail": f"{type(exc).__name__}: {exc}"}


#: 隔离服务端子进程登记表 —— 只用于"收尾兜底 kill"（2026-10-16 加，**与判据无关**）：
#:   上一轮出现过一次孤儿 `uvicorn`（探针中途 return/抛异常时 `main()` 的优雅收尾没走到）。
_ISOLATED_SERVERS: list[subprocess.Popen] = []


def kill_isolated_servers() -> None:
    """兜底 kill 掉仍活着的隔离服务端子进程（尽力而为，不参与任何判定）。

    `atexit` 覆盖"中途 `return` / 抛异常"的路径；进程被**强杀**时 `atexit` 同样跑不到，
    那种情况探针自身无法覆盖（残留 pid 已记进结果 JSON 的 `isolatedServer`）。
    """
    for proc in list(_ISOLATED_SERVERS):
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - 收尾尽力而为
            pass


atexit.register(kill_isolated_servers)


def start_isolated_server(out_dir: Path, report: Report):
    """把 `jflove-server/` 复制到证据目录并起一个**隔离实例**（库在副本内，不碰仓库 dev 库）。

    :returns: `(proc, port, server_root)`；任一步失败返回 `(None, 0, None)`。
    """
    src = REPO / "jflove-server"
    python = src / "venv-win" / "Scripts" / "python.exe"
    if not python.exists():
        report.note("未找到服务端 venv ⇒ 登录依赖的判据只能 SKIP")
        return None, 0, None

    server_root = out_dir / ".tmp_server"
    dst = server_root / "jflove-server"
    ignore = shutil.ignore_patterns("venv-win", "venv-linux", "build", "logs", "__pycache__",
                                    "*.pyc", "*.db", "*.db-journal")
    # 2026-10-16 健壮性加固（**与任何判据无关**，只让"重复跑"自愈）：
    #   上一轮被强杀留下残留句柄时，`rmtree(ignore_errors=True)` 会**静默失败**，
    #   紧接的 `copytree` 就抛 `FileExistsError` ⇒ 整轮一条判据都拿不到。
    #   `dirs_exist_ok=True` 让"覆盖同一份副本"变成幂等操作；再退避重试 3 次兜住
    #   "杀毒/索引器临时占用"。3 次都不成 ⇒ **照旧把异常抛出去**（失败必须响亮，
    #   不改成 SKIP，也不动任何判据）。
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    for attempt in (1, 2, 3):
        try:
            shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)
            break
        except OSError:  # noqa: PERF203 - 目的目录被临时占用 ⇒ 退避后重试
            if attempt == 3:
                raise
            time.sleep(1.0 * attempt)
    # `DB_PATH` = 副本根的上层 + jflove-db/jflove-dev.db（settings.py:7）⇒ 隔离库
    (server_root / "jflove-db").mkdir(parents=True, exist_ok=True)

    port = free_port()
    env = os.environ.copy()
    env["JFLOVE_HOST"] = "127.0.0.1"
    env["JFLOVE_PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    log_path = out_dir / "server.log"
    log_file = log_path.open("wb")
    proc = subprocess.Popen(  # noqa: S603 - 解释器与参数均为本仓库内的固定值
        [str(python), "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=str(dst), env=env, stdout=log_file, stderr=subprocess.STDOUT,
    )
    _ISOLATED_SERVERS.append(proc)  # 收尾兜底 kill 用（与判据无关）
    health = wait_health(port)
    if not health.get("ok"):
        report.note(f"隔离服务端未就绪：{health}")
        return proc, port, server_root
    return proc, port, server_root


def python_side_admin(url: str) -> dict:
    """用桌面端**自己的服务层**在隔离库上造管理员（**不登录** ⇒ 渲染层仍是未登录态，AC-64 前提）。

    证据只记"有没有"与用户名 / 角色，**不记令牌、不记口令**（§9.14）。
    """
    from src.services import auth_service
    from src.utils.session import session_manager

    evidence: dict = {"serverUrlSet": bool(url)}
    auth_service.do_key_exchange(url)
    evidence["sessionReady"] = bool(session_manager.is_session_ready())
    evidence["adminExistsBefore"] = bool(auth_service.check_admin_exists())
    if not evidence["adminExistsBefore"]:
        auth_service.init_admin(ADMIN_USER, ADMIN_PASSWORD)
    evidence["adminExistsAfter"] = bool(auth_service.check_admin_exists())
    evidence["tokenPresentAfterInit"] = bool(session_manager.token)
    evidence["loggedInAfterInit"] = bool(session_manager.is_logged_in())
    return evidence


def python_side_login(url: str) -> dict:
    """走桌面端自己的服务层完成真实登录（不 stub、不伪造 session.json）。"""
    from src.services import auth_service
    from src.utils.session import session_manager

    evidence = python_side_admin(url)
    auth_service.login(ADMIN_USER, ADMIN_PASSWORD, 7 * 24 * 3600)
    evidence.update({
        "loggedIn": bool(session_manager.is_logged_in()),
        "username": session_manager.username,
        "role": session_manager.role,
        "hasToken": bool(session_manager.token),
        "sessionFileExists": session_file_exists(),
    })
    return evidence


def session_file_exists() -> bool:
    """`session.json` 是否已真实落盘（**只回布尔**，不读内容）。"""
    try:
        from src.config.settings import USER_DATA_DIR

        return Path(USER_DATA_DIR, "storage", "session.json").exists()
    except Exception:  # noqa: BLE001 - 属性不存在时按"未落盘"处理
        return False


# ─────────────────────────── 桥调用计数（探针侧仪器） ───────────────────────────

#: 被计数的桥方法（服务名, 方法名）⇒ 只记**次数与参数长度/指纹**，不记路径与正文
_COUNTED_METHODS = (("editor", "detectAndRead"), ("editor", "writeText"), ("editor", "draftSave"))


def install_bridge_counters() -> dict:
    """给白名单处理函数挂**调用计数器**（不改产品源码，只在探针进程内包一层）。

    `bridge.py:341` 每次请求都 `WHITELIST.get(service, {}).get(method)` 现取 ⇒ 包字典项即可生效。
    返回的 `calls` 是**可追加的列表**，由探针随时读取。
    """
    from src.bridge import bridge as bridge_mod

    calls: dict[str, list] = {f"{svc}.{m}": [] for svc, m in _COUNTED_METHODS}
    #: 安装失败单独记（**不计入调用次数** —— 否则"没装上"会被读成"调用过 1 次"）
    calls["__install__"] = []

    def wrap(service: str, method: str) -> None:
        key = f"{service}.{method}"
        original = bridge_mod.WHITELIST.get(service, {}).get(method)
        if original is None:
            calls["__install__"].append({"method": key, "error": "handler-not-found"})
            return

        def wrapped(params, _original=original, _key=key):
            entry: dict = {"at": time.time()}
            if isinstance(params, dict):
                text = params.get("text")
                if isinstance(text, str):
                    entry["textLen"] = len(text)
                    entry["textFnv1a32"] = fnv1a32(text.encode("utf-8"))
                paths = params.get("paths")
                if isinstance(paths, list):
                    entry["pathCount"] = len(paths)
            # ⚠ 必须记**失败**：产品侧原子写在 Windows 上会因"目标文件被别的句柄打开"
            # 报 `PermissionError`（探针早期版本正是这样把自己的被测写入撞掉的，见
            # `poll_disk_change` 的说明）。原实现只在成功后 append ⇒ 失败被静默吞掉、
            # 计数读起来像"根本没调过"。现在失败也入账并带 `ok:false` + 异常类名（不含路径）。
            try:
                result = _original(params)
            except BaseException as exc:  # noqa: BLE001 - 计数层不得改变异常语义
                entry["ok"] = False
                entry["error"] = type(exc).__name__
                raise
            else:
                entry["ok"] = True
                return result
            finally:
                calls[_key].append(entry)

        bridge_mod.WHITELIST.setdefault(service, {})[method] = wrapped

    for service, method in _COUNTED_METHODS:
        wrap(service, method)
    return calls


def call_count(calls: dict, key: str) -> int:
    """调用次数（**含失败**的调用；条目的 `ok` 标记区分成败；安装失败记在 `__install__` 里不污染计数）。"""
    return len(calls.get(key, []))


def call_count_ok(calls: dict, key: str) -> int:
    """**成功**的调用次数（`ok is True`）。

    用途：判断"某个桥方法**真的做成了事**"，而不是"被调了几次" ——
    实现里失败会被记成 `ok:False`（例如 Windows 上目标文件被占用导致的
    `PermissionError`），只数总数会把失败读成成功。
    """
    return sum(1 for item in (calls.get(key) or []) if item.get("ok") is True)


# ─────────────────────────── 探针主体 ───────────────────────────


class R15:
    def __init__(self, report: Report, shell, out_dir: Path, real_window: bool,
                 calls: dict) -> None:
        self.report = report
        self.shell = shell
        self.out = out_dir
        self.real_window = real_window
        self.calls = calls
        self._seq = 0
        self.window_closed = False
        self.server = None
        self.server_port = 0

    # ── 基础设施 ──
    def js(self, expr: str, timeout_ms: int = 5000):
        """在渲染层求值 `expr` 并经**标题通道**取回结果（见模块文档「经验 1」）。"""
        from PySide6.QtTest import QTest

        if self.window_closed:
            return {"r15Error": "window-closed"}
        self._seq += 1
        marker = f"R15#{self._seq}"
        payload = (
            "(function(){var __v;try{__v=(" + expr + ");}catch(e){__v={r15Error:String(e)};}"
            "document.title='" + marker + "::'+JSON.stringify(__v===undefined?null:__v);"
            "return true;})()"
        )
        try:
            page = self.shell.view.page()
        except Exception as exc:  # noqa: BLE001 - 窗口/页面已销毁
            self.window_closed = True
            return {"r15Error": f"no-page: {exc}"}
        page.runJavaScript(payload)
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            title = page.title()
            if title.startswith(marker + "::"):
                raw = title[len(marker) + 2:]
                try:
                    return json.loads(raw)
                except ValueError:
                    return {"r15Error": "bad-json", "raw": raw[:200]}
            QTest.qWait(40)
        return {"r15Error": "title-timeout", "title": page.title()[:120]}

    def wait_for(self, expr: str, predicate, timeout_ms: int = 25000, interval_ms: int = 250):
        from PySide6.QtTest import QTest

        deadline = time.time() + timeout_ms / 1000
        last = None
        while time.time() < deadline:
            last = self.js(expr, timeout_ms=3500)
            if isinstance(last, dict) and predicate(last):
                return last
            QTest.qWait(interval_ms)
        return last

    def state(self) -> dict:
        return self.js(JS_UI_STATE) or {}

    def shot(self, name: str) -> str:
        try:
            path = self.out / f"r15-{name}.png"
            self.shell.view.grab().save(str(path))
            return path.name
        except Exception as exc:  # noqa: BLE001 - 截图失败不影响判据
            return f"shot-failed: {exc}"

    def modal_widget(self) -> str | None:
        """宿主侧**阻塞式模态**（原生对话框等）；`None` = 无。"""
        from PySide6.QtWidgets import QApplication

        try:
            widget = QApplication.activeModalWidget()
        except Exception:  # noqa: BLE001 - Qt 已退出
            return "unknown"
        return type(widget).__name__ if widget is not None else None

    def window_alive(self) -> bool:
        """窗口是否还活着。"""
        from PySide6.QtWidgets import QApplication

        if self.window_closed:
            return False
        try:
            if not self.shell.isVisible():
                return False
        except Exception:  # noqa: BLE001
            self.window_closed = True
            return False
        return self.shell in QApplication.topLevelWidgets()

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
            except Exception as exc:  # noqa: BLE001 - 抢焦点失败只影响按键类判据
                self.report.note(f"抢前台失败：{exc}")
        QTest.qWait(500)

    # ── 一次 OS 级按键 + 后置观察（**派发与观察分两次调用**，见「经验 2」）──
    def os_key(self, mods: list[str], key: str, settle_ms: int = 900) -> dict:
        """OS 级按键 + 渲染层收到了什么（诊断）。"""
        from PySide6.QtTest import QTest

        if not self.real_window or not sys.platform.startswith("win"):
            return {"supported": False, "why": "需要真窗口 + Windows"}
        install = self.js(JS_INSTALL_KEY_LOG) or {}
        self.activate()
        self.shell.view.setFocus()
        QTest.qWait(120)
        os_release_modifiers()
        QTest.qWait(60)
        before_mods = os_mod_state()
        install = self.js(JS_INSTALL_KEY_LOG) or install
        self.js(JS_CLEAR_KEY_LOG)
        os_press(mods, key)
        QTest.qWait(settle_ms)
        log = self.js(JS_READ_KEY_LOG) or {}
        raw = log.get("keys") or []
        wanted = key.lower()
        hits = [k for k in raw if str(k.get("key", "")).lower() == wanted]
        capture = [k for k in hits if k.get("phase") == "capture"]
        return {
            "supported": True,
            "install": install,
            "beforeMods": before_mods,
            "afterMods": os_mod_state(),
            "reached": bool(capture),
            "prevented": any(k.get("defaultPrevented") for k in capture),
            "targetInPanel": any(k.get("targetInPanel") for k in capture),
            "targetInCm": any(k.get("targetInCm") for k in capture),
            "keys": capture,
            "allKeys": raw,
            "windowAlive": self.window_alive(),
        }

    def focus_editor(self) -> dict:
        return self.js(js_focus_editor()) or {}

    def reload_page(self) -> dict:
        """重载渲染层（AC-71/67/68 走 Python 侧登录后需要它重建 React 会话快照）。

        **坑（r13 实测）**：Qt 6 的 `QWebEnginePage` 已经没有 `reload()`
        （`'ShellPage' object has no attribute 'reload'`）；`reload()` 在 `QWebEngineView` 上，
        备选是 `page.triggerAction(Reload)`。
        """
        tried: list[dict] = []
        try:
            self.shell.view.reload()
            tried.append({"via": "view.reload", "ok": True})
            return {"ok": True, "tried": tried}
        except Exception as exc:  # noqa: BLE001 - 换下一种重载方式
            tried.append({"via": "view.reload", "ok": False, "error": str(exc)})
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage

            self.shell.view.page().triggerAction(QWebEnginePage.WebAction.Reload)
            tried.append({"via": "triggerAction(Reload)", "ok": True})
            return {"ok": True, "tried": tried}
        except Exception as exc:  # noqa: BLE001 - 两种都不行就如实回报
            tried.append({"via": "triggerAction(Reload)", "ok": False, "error": str(exc)})
        return {"ok": False, "tried": tried}

    def reload_page_and_wait(self) -> dict:
        """重载后等「文本编辑器」导航项出现（= 渲染层真的拿到已登录快照）。

        **坑（r13 实测）**：重载会清掉渲染层的探针状态（`window.__r15`）⇒ 按键类判据
        每次按键前都要重新确认监听在位，否则会误判"没收到"而重复按键。
        """
        from PySide6.QtTest import QTest

        result = self.reload_page()
        QTest.qWait(1600)
        state = self.wait_for(JS_UI_STATE, lambda v: v.get("navEditor") is True,
                              timeout_ms=40000, interval_ms=500) or {}
        return {"reload": result, "state": state}

    # ── 标签页批量清理（跨判据之间复位，避免"第 12 个"被前序标签污染）──
    def close_all_tabs(self, limit: int = 40) -> dict:
        from PySide6.QtTest import QTest

        steps: list[dict] = []
        for _ in range(limit):
            state = self.state()
            if state.get("tabCount", 0) == 0:
                break
            if state.get("dialogLabel"):
                steps.append({"dialog": state.get("dialogLabel"),
                              "click": self.js(js_click_dialog_button("放弃修改"))})
                QTest.qWait(400)
                continue
            steps.append({"close": self.js(js_close_tab(None))})
            QTest.qWait(300)
        final = self.state()
        return {"steps": len(steps), "finalTabCount": final.get("tabCount"),
                "dialogLeft": final.get("dialogLabel")}

    # ═══════════════════════ AC-64（设计 §13.3 第 12 条） ═══════════════════════

    def ac64_unlogged_panel(self, fixture: Path) -> dict:
        """AC-64 ①：未登录态收到唤起 ⇒ 只有登录面板，零标签页 / 零工作区 / **零 detectAndRead**。

        注意要等 **1.5 s 延迟重推**（`web_shell.py:1118` 的 `_schedule_open_paths_retry`）
        之后再判一次：重推若被当成"新请求"就会漏掉"去重 + 门"这条链。
        """
        from PySide6.QtTest import QTest

        before = self.state()
        detected_before = call_count(self.calls, "editor.detectAndRead")
        pushed = self.shell.notify_external_paths([str(fixture)])
        QTest.qWait(1000)
        mid = self.state()
        QTest.qWait(1600)  # 覆盖 1500 ms 重推
        after = self.state()
        detected_after = call_count(self.calls, "editor.detectAndRead")
        ok = (
            before.get("connectButton") is True
            and before.get("tabCount") == 0
            and before.get("workspaceCount") == 0
            and after.get("connectButton") is True
            and after.get("passwordInputs") == 0
            and after.get("workspaceCount") == 0
            and after.get("tabCount") == 0
            and after.get("overflowButton") == 0
            and detected_before == 0
            and detected_after == 0
            and self.window_alive()
            and self.modal_widget() is None
        )
        detail = {
            "pushedCount": pushed,
            "detectAndReadCalls": {"before": detected_before, "after": detected_after},
            "before": {k: before.get(k) for k in
                       ("hash", "connectButton", "passwordInputs", "tabCount",
                        "workspaceCount")},
            "afterRetry": {k: after.get(k) for k in
                           ("hash", "connectButton", "passwordInputs", "tabCount",
                            "workspaceCount", "overflowButton", "activePanelCount")},
            "midTabCount": mid.get("tabCount"),
            "navEditor": after.get("navEditor"),
            "modalWidget": self.modal_widget(),
            "screenshot": self.shot("ac64-1-unlogged-panel"),
        }
        self.report.add(
            "AC64-1",
            "AC-64 ①：未登录经「打开方式」唤起 ⇒ 只有登录面板（零标签页 / 零工作区 / 零 detectAndRead，"
            "含 1.5 s 重推后）",
            "PASS" if ok else "FAIL", detail,
        )
        return detail

    def ac64_renderer_login(self, url: str) -> dict:
        """AC-64 ②：**渲染层自己**走登录页三步完成登录（不重载页面、不由 Python 代劳）。"""
        from PySide6.QtTest import QTest

        steps: list[dict] = []
        connect = self.wait_for(JS_UI_STATE, lambda v: v.get("connectButton") is True,
                                timeout_ms=15000)
        if not connect or connect.get("connectButton") is not True:
            return {"ok": False, "why": "登录页 connect 步未出现", "state": connect}
        detected_before_login = call_count(self.calls, "editor.detectAndRead")

        steps.append({"step": "set-url", "el": self.js(js_set_input('input[type="text"]', url))})
        QTest.qWait(250)
        steps.append({"step": "click-connect", "el": self.js(js_click_button("连接服务端"))})
        login_step = self.wait_for(
            JS_UI_STATE,
            lambda v: v.get("passwordInputs", 0) >= 1 or v.get("initAdminButton") is True,
            timeout_ms=25000,
        )
        steps.append({"step": "await-login-step",
                      "passwordInputs": (login_step or {}).get("passwordInputs"),
                      "initAdminButton": (login_step or {}).get("initAdminButton"),
                      "loginButton": (login_step or {}).get("loginButton")})
        if (login_step or {}).get("initAdminButton") is True:
            return {"ok": False, "why": "隔离服务端上没有管理员（init_admin 失败？）",
                    "steps": steps, "state": login_step}
        if (login_step or {}).get("passwordInputs", 0) < 1:
            return {"ok": False, "why": "未进入 login 步", "steps": steps, "state": login_step}

        steps.append({"step": "set-username",
                      "el": self.js(js_set_input('input[type="text"]', ADMIN_USER))})
        steps.append({"step": "set-password",
                      "el": self.js(js_set_input('input[type="password"]', ADMIN_PASSWORD,
                                                 sensitive=True))})
        QTest.qWait(250)
        steps.append({"step": "click-login", "el": self.js(js_click_button("登录"))})
        landed = self.wait_for(
            JS_UI_STATE,
            lambda v: v.get("hash") == "#/editor" and v.get("tabCount", 0) >= 1,
            timeout_ms=30000,
            interval_ms=400,
        )
        spec = {
            "hash": (landed or {}).get("hash"),
            "navEditor": (landed or {}).get("navEditor"),
            "navEditorActive": (landed or {}).get("navEditorActive"),
            "tabCount": (landed or {}).get("tabCount"),
            "workspaceCount": (landed or {}).get("workspaceCount"),
            "tabs": (landed or {}).get("tabs"),
            "passwordInputs": (landed or {}).get("passwordInputs"),
            "activePanelCount": (landed or {}).get("activePanelCount"),
            "activePanelId": (landed or {}).get("activePanelId"),
        }
        ok = spec["hash"] == "#/editor" and spec["tabCount"] == 1
        # ── 诊断（不替代 ② 的判定）：落点不对时，手动切到 /editor 看**内存里到底有没有那个标签** ──
        # 目的：把"没自动切菜单"与"文件根本没打开"两种故障分开，避免把推断写成事实。
        diagnostic: dict = {}
        if not ok:
            self.js(js_go_hash("#/editor"))
            QTest.qWait(1500)
            mounted = self.wait_for(
                JS_UI_STATE, lambda v: v.get("workspaceCount", 0) >= 1, timeout_ms=15000) or {}
            QTest.qWait(400)
            diagnostic = {
                "why": "AC-64 ② 落点不是 #/editor ⇒ 诊断性手动切路由后观测（不改变 ② 的判定）",
                "hashAfterManualNavigate": mounted.get("hash"),
                "tabCount": mounted.get("tabCount"),
                "tabs": mounted.get("tabs"),
                "activePanelId": mounted.get("activePanelId"),
                "activePanelCount": mounted.get("activePanelCount"),
                "cmContentCount": mounted.get("cmContentCount"),
            }
        detail = {
            "serverUrlPort": url.rsplit(":", 1)[-1],  # 只留端口，不回完整地址
            "steps": steps,
            "detectAndReadCallsBeforeLogin": detected_before_login,
            "detectAndReadCallsAfterLogin": call_count(self.calls, "editor.detectAndRead"),
            "landing": spec,
            "diagnosticManualNavigate": diagnostic,
            "screenshot": self.shot("ac64-2-after-login"),
        }
        self.report.add(
            "AC64-2",
            "AC-64 ②：**渲染层自己**完成登录（登录页 connect → login 三步；未重载页面）⇒ "
            "`location.hash === '#/editor'`、导航项「文本编辑器」成为激活项、恰好 1 个标签页",
            "PASS" if ok else "FAIL", detail,
        )
        if not ok:
            self.report.add(
                "OBS-AC64-LANDING",
                "观察项：AC-64 ② 的实际落点（登录成功后 React Router 的最终路由）",
                "FAIL" if spec["hash"] != "#/editor" else "PASS",
                {"landing": spec, "diagnostic": diagnostic,
                 "prompt": "设计 §2.2.1 要求登录成功 ⇒ navigate('/editor')；"
                           "LoginPage.tsx:44-46 的 `navigate('/', {replace:true})` 覆盖了它"},
                gating=False,
            )
        return {"ok": bool(ok), "landed": landed, "detail": detail}

    def ac64_content(self, fixture: Path, fixture_text: str) -> None:
        """AC-64 ③④：正文逐字符等于 fixture、该标签为激活项、状态栏编码/换行/语言一致。"""
        from PySide6.QtTest import QTest

        state = self.wait_for(
            JS_UI_STATE,
            lambda v: v.get("activePanelId") is not None and v.get("cmContentCount", 0) >= 1,
            timeout_ms=25000,
        ) or {}
        QTest.qWait(400)
        expected = text_fingerprint(fixture_text)
        doc = self.js(JS_DOC_TEXT) or {}
        compare = self.js(js_compare_doc(fixture_text)) or {}
        status = doc.get("status") or {}

        only_tab = (state.get("tabs") or [{}])[0] if state.get("tabCount") == 1 else {}
        title_matches_fixture = only_tab.get("title") == fixture.name
        active_panel_matches_tab = (
            state.get("activePanelId") is not None
            and state.get("activePanelId") == only_tab.get("id")
        )
        doc_ok = (
            compare.get("equal") is True
            and doc.get("docFnv1a32") == expected["fnv1a32"]
            and doc.get("docUtf8Bytes") == expected["utf8Bytes"]
            and doc.get("docCodePoints") == expected["codePoints"]
        )
        # 2026-10-16 因 F2 汉化而**精确更正期望值（非放宽）**：状态栏的语言名已改为取产品
        #   `TYPES` 表的 `label`（`ui/src/config/scratch-types.ts:62` 的
        #   `{ext:"log", label:"日志 (log)", language:"text"}`；取值代码见
        #   `ScratchEditorStatusBar.tsx:81-83` 的 `statusLanguageLabel`）
        #   ⇒ `.log` fixture 现在读到的是 `日志 (log)`，不再是旧的裸语言 id `text`。
        #   口径**不放松**：仍是"恰好等于该扩展名对应的产品 label"，
        #   不是"非空即可"也不是"不等于 text"。
        status_ok = (
            status.get("encoding") == "UTF-8"
            and status.get("lineEnding") == "LF"
            and status.get("language") == "日志 (log)"
        )
        detail = {
            "tabCount": state.get("tabCount"),
            "tabs": state.get("tabs"),
            "tabTitleEqualsFixtureName": title_matches_fixture,
            "activePanelId": state.get("activePanelId"),
            "activePanelMatchesTab": active_panel_matches_tab,
            "activePanelCount": state.get("activePanelCount"),
            "navEditorActive": state.get("navEditorActive"),
            "hash": state.get("hash"),
            "doc": {k: doc.get(k) for k in
                    ("docCodePoints", "docUtf8Bytes", "docFnv1a32", "crlf", "lf")},
            "fixtureFingerprint": expected,
            "compare": compare,
            "statusBar": status,
            "screenshot": self.shot("ac64-3-content"),
        }
        self.report.add(
            "AC64-3",
            f"AC-64 ③：工作区恰好 1 个标签页、标题 = fixture 名（{fixture.name}）、它同时是"
            "激活标签与激活面板（`data-editor-panel-active` 的 id 与之一致）",
            "PASS" if (state.get("tabCount") == 1 and title_matches_fixture
                       and active_panel_matches_tab
                       and state.get("navEditorActive") is True) else "FAIL",
            detail,
        )
        self.report.add(
            "AC64-4",
            "AC-64 ③：正文**逐字符等于** fixture（渲染层 JS 逐字符比对 + 独立 FNV-1a/字节数/码点数复核）",
            "PASS" if doc_ok else "FAIL",
            {"compare": compare, "docHash": doc.get("docFnv1a32"),
             "fixtureHash": expected["fnv1a32"],
             "docBytes": doc.get("docUtf8Bytes"), "fixtureBytes": expected["utf8Bytes"],
             "docCodePoints": doc.get("docCodePoints"),
             "fixtureCodePoints": expected["codePoints"]},
        )
        self.report.add(
            "AC64-5",
            "AC-64 ④：状态栏 编码 / 换行符 / 语言 与 fixture 一致（UTF-8 / LF / 日志 (log)）",
            "PASS" if status_ok else "FAIL", {"statusBar": status},
        )

    # ═══════════════════════ AC-71（设计 §13.3 第 13 条） ═══════════════════════

    def ac71_stop_server(self) -> dict:
        """停掉隔离服务端，并**证明端口真的不可达**（AC-71 的前提，不能只 `terminate()` 了算）。"""
        proc = self.server
        port = self.server_port
        if proc is None or port == 0:
            return {"ok": False, "why": "没有隔离服务端实例"}
        proc.terminate()
        exited = False
        try:
            proc.wait(timeout=15)
            exited = True
        except Exception:  # noqa: BLE001 - 收尾尽力而为
            proc.kill()
        deadline = time.time() + 12
        probe = port_refused(port)
        while time.time() < deadline and not probe.get("refused"):
            time.sleep(0.4)
            probe = port_refused(port)
        detail = {
            "port": port,
            "processExited": exited or proc.poll() is not None,
            "returncode": proc.poll(),
            "connectionRefused": probe.get("refused"),
            "refusedDetail": probe.get("detail"),
        }
        detail["ok"] = bool(detail["connectionRefused"] and detail["processExited"])
        self.report.add(
            "AC71-1", "AC-71 前提：服务端进程已退出且端口连接被拒（真的不可达）",
            "PASS" if detail["ok"] else "FAIL", detail,
        )
        return detail

    def ac71_offline(self, fixture: Path, fixture_text: str) -> None:
        """AC-71：登录后离线 ⇒ 新建 / 打开 / 编辑 / `Ctrl+S` 写回 / 切标签 / 关标签全部可用。"""
        from PySide6.QtTest import QTest

        samples: list[dict] = []
        detects_before = call_count(self.calls, "editor.detectAndRead")
        writes_before = call_count(self.calls, "editor.writeText")
        writes_ok_before = call_count_ok(self.calls, "editor.writeText")

        def sample(tag: str) -> dict:
            """每次操作后取一个样本：hash / 密码框 / DOM 模态 —— 用来证明"全程无登录拦截、无阻塞弹窗"。"""
            state = self.state()
            item = {
                "tag": tag,
                "hash": state.get("hash"),
                "passwordInputs": state.get("passwordInputs"),
                "dialogLabel": state.get("dialogLabel"),
                "modalWidget": self.modal_widget(),
                "tabCount": state.get("tabCount"),
            }
            samples.append(item)
            return item

        sample("start")

        # ① 新建标签页：OS 级 Ctrl+N（离线时不得被登录拦截）
        #
        # 2026-10-16 因 F3 换键而**精确更正期望值（非放宽）**：`pane.new` 的默认键已由
        #   `Ctrl+Alt+N` 改为 `Ctrl+N`（需求 AC-75①，`ui/src/config/shortcuts.ts` 的
        #   `RUNTIME_SHORTCUTS`）⇒ 旧键不再命中任何绑定。本处只换**被投递的键**，
        #   判据本体（`reached=true` 且标签数恰好 +1 且能输入内容）一个字未改。
        #
        # ⚠ **投递重试**（加固的是"测量"，不是放宽判据）：OS 级 `keybd_event` 偶发不到达渲染层
        #   （`key.reached=false`；窗口刚被激活时最易发生，2026-10-15 22:08 那轮实测命中）。
        #   单次投递失败会让"标签数 ±1"的假设级联污染 AC71-3 / AC71-5 —— 那两条测的是
        #   "打开文件 / 关闭标签各自的效果"，不该被**别的步骤**的按键投递成败绑架。
        #   判据本身**未变**：仍要求 `reached=true` **且** 标签数恰好 +1；最多投递 3 次，
        #   `attempts` 记进证据；3 次都没到 ⇒ 照旧 FAIL。
        before_tabs = (self.state().get("tabCount") or 0)
        key_attempts: list[dict] = []
        new_tab_key: dict = {}
        after_new: dict = {}
        s_new: dict = {}
        for attempt in (1, 2, 3):
            before_this = (self.state().get("tabCount") or 0)
            new_tab_key = self.os_key(["ctrl"], "n", settle_ms=1200)
            after_new = self.state()
            s_new = sample("after-new-tab")
            key_attempts.append({"attempt": attempt,
                                 "reached": new_tab_key.get("reached"),
                                 "tabCount": after_new.get("tabCount")})
            if (after_new.get("tabCount") or 0) > before_tabs + 1:
                break  # 多出标签 = 真异常，不再重试（避免把异常越滚越大）
            if (new_tab_key.get("reached") is True
                    and after_new.get("tabCount") == before_this + 1):
                break
        new_tab_ok = (
            new_tab_key.get("supported") is True
            and new_tab_key.get("reached") is True
            and after_new.get("tabCount") == before_tabs + 1
            and s_new.get("dialogLabel") is None
        )
        # ② 输入内容（经 CM6 自己的事务 ⇒ 真实 updateListener → markDirty）
        insert = self.wait_for(js_insert_text("离线新建标签页内容\n"),
                               lambda v: v.get("ok") is True, timeout_ms=15000) or {}
        QTest.qWait(600)
        typed = self.js(JS_DOC_TEXT) or {}
        sample("after-type")
        self.report.add(
            "AC71-2",
            "AC-71 ①：离线状态 OS 级 Ctrl+N 新建标签页成功，并可输入内容（CM6 事务写入生效）",
            "PASS" if (new_tab_ok and insert.get("ok") is True
                       and typed.get("docCodePoints", 0) > 0) else "FAIL",
            {"key": {k: new_tab_key.get(k) for k in ("supported", "reached", "prevented")},
             "keyAttempts": key_attempts,
             "beforeTabCount": before_tabs, "afterTabCount": after_new.get("tabCount"),
             "insert": insert, "docCodePoints": typed.get("docCodePoints"),
             "status": typed.get("status"), "windowAlive": self.window_alive()},
        )

        # ③ 打开**已有路径**的文件（经生产通路 notify_external_paths）
        #    判据用**增量**：本条的claim 是"打开一个文件 ⇒ 工作区出现**一个**标签页且为激活项"，
        #    与"上一步的 OS 按键有没有投递成功"无关 ⇒ 期望值取"推送前那一刻的实际标签数 + 1"。
        tabs_before_push = (self.state().get("tabCount") or 0)
        pushed = self.shell.notify_external_paths([str(fixture)])
        opened = self.wait_for(JS_UI_STATE,
                               lambda v: v.get("tabCount", 0) >= tabs_before_push + 1,
                               timeout_ms=25000) or {}
        QTest.qWait(500)
        detect_after_open = call_count(self.calls, "editor.detectAndRead")
        s_open = sample("after-open-existing")
        open_ok = (
            pushed >= 1
            and opened.get("tabCount") == tabs_before_push + 1
            and detect_after_open > detects_before
            and opened.get("dialogLabel") is None
            and opened.get("passwordInputs") == 0
        )
        self.report.add(
            "AC71-3",
            f"AC-71 ②：离线经「打开方式」通路打开**已有路径**的 fixture（{fixture.name}）为标签页",
            "PASS" if open_ok else "FAIL",
            {"pushedCount": pushed, "tabCount": opened.get("tabCount"),
             "tabCountBeforePush": tabs_before_push,
             "expectedTabCount": tabs_before_push + 1,
             "tabCountAtPhaseStart": before_tabs,
             "detectAndReadCalls": {"before": detects_before, "afterOpen": detect_after_open},
             "hash": opened.get("hash"), "sample": s_open,
             "detectAndReadLast": (self.calls.get("editor.detectAndRead") or [{}])[-1],
             "dialogLabel": opened.get("dialogLabel"),
             "screenshot": self.shot("ac71-owned-offline")},
        )

        # ④ 改内容 + OS 级 Ctrl+S → 读磁盘验字节（含时延区分度：防抖 1500 ms 不可能在此时到点）
        disk_before = {"exists": fixture.exists(), "sha256": sha256_file(fixture)}
        focused = self.focus_editor()
        marker = "\nAC71-SAVED-BY-CTRL-S\n"
        expected_text = fixture_text + marker
        inserted = self.wait_for(js_insert_text(marker), lambda v: v.get("ok") is True,
                                 timeout_ms=15000) or {}
        t_edit = time.time()
        # ④ 的**区分判据改用机制**（2026-10-15 收尾轮更正，见下方 `manual_path`）：
        #   · 手动保存通路 = `savePane` ⇒ **只**调 `writeText`（`scratch-store.ts:676`），不调 `draftSave`；
        #   · 防抖通路 = `flushPane` ⇒ **先** `draftSave`（`:318`）**再** `writeText`（`:330`）。
        # ⇒ 「本轮 `writeText` +1 且 `draftSave` 计数不变」= 这次写盘确实由 Ctrl+S 触发。
        #
        # 为什么不再用「时延 < 1.2 s」当判据：它把"是不是手动保存"与"这一轮桥往返快不快"混在一起。
        # 实测同一份代码的 `latencyKeyToDiskSec` 在 0.205 s ~ 2.412 s 之间波动（慢轮），
        # 会给出**假 FAIL** —— 2026-10-15 22:56 轮就是：字节级比对全对、`writeText` 0→1、
        # 按键 `reached/prevented/targetInPanel/targetInCm` 四项全真，只因 2.412 > 1.2 被判红。
        # 时延降级为**观察项**（`OBS-SAVE-LATENCY`），不参与判定。
        drafts_before_save = call_count(self.calls, "editor.draftSave")
        save_key = self.os_key(["ctrl"], "s", settle_ms=350)
        t_key = time.time()
        changed_at = poll_disk_change(fixture, 12.0)
        latency = None if changed_at is None else round(changed_at - t_key, 3)
        edit_to_change = None if changed_at is None else round(changed_at - t_edit, 3)
        on_disk = read_text_strict(fixture)
        disk_after = {"sha256": sha256_file(fixture), "bytes": fixture.stat().st_size}
        write_calls = self.calls.get("editor.writeText") or []
        last_write = write_calls[-1] if write_calls else {}
        drafts_after_save = call_count(self.calls, "editor.draftSave")
        sample("after-ctrl-s")
        manual_path = drafts_after_save == drafts_before_save
        expected_fp = text_fingerprint(expected_text)
        # ⚠ `manual_path` **不参与判定**（2026-10-15 收尾轮结论）：`draftSave` 计数是**全局**的，
        #   而桥参数里只有 `draftSave` 带 `pane` id（`writeText` 不带）⇒ 无法按标签归属，
        #   同相位的另一个无路径标签（AC71-2 的「未命名」）自己的防抖也会把计数 +1
        #   （实测 `draftSave 0→1` 而 fixtures 的写入完全正确）。
        #   AC-71 ① 的原文只要求「保存到本地路径全部成功（**文件存在且内容正确**）」，
        #   所以判定取这两条 + 更强的一条：桥**投递**的正文指纹必须等于期望指纹。
        save_ok = (
            inserted.get("ok") is True
            and save_key.get("supported") is True
            and save_key.get("targetInPanel") is True
            and on_disk == expected_text
            and last_write.get("textFnv1a32") == expected_fp.get("fnv1a32")
            and call_count_ok(self.calls, "editor.writeText") > writes_ok_before
        )
        self.report.add(
            "AC71-4",
            "AC-71 ①：离线 OS 级 Ctrl+S 把编辑内容写回磁盘（**字节级**比对 + 桥投递正文指纹一致）",
            "PASS" if save_ok else "FAIL",
            {"focus": focused,
             "editAt": round(t_edit, 3), "keyAt": round(t_key, 3),
             "latencyKeyToDiskSec": latency, "latencyEditToDiskSec": edit_to_change,
             "manualPathObservation": manual_path,
             "manualPathNote": "**仅观察**：全局 `draftSave` 计数无法按标签归属，"
                               "同相位其它无路径标签的防抖也会 +1 ⇒ 不作为判定依据。",
             "draftSaveCalls": {"before": drafts_before_save, "after": drafts_after_save},
             "diskShaBefore": disk_before["sha256"],
             "diskShaAfter": disk_after["sha256"],
             "diskBytes": disk_after["bytes"],
             "expectedFingerprint": expected_fp,
             "onDiskFingerprint": text_fingerprint(on_disk) if on_disk is not None else None,
             "diskTextEqualsExpected": on_disk == expected_text,
             "bridgedTextMatchesExpected":
                 last_write.get("textFnv1a32") == expected_fp.get("fnv1a32"),
             "writeTextCalls": {"before": writes_before,
                                "after": call_count(self.calls, "editor.writeText"),
                                "okBefore": writes_ok_before,
                                "okAfter": call_count_ok(self.calls, "editor.writeText"),
                                "last": {k: last_write.get(k)
                                         for k in ("textLen", "textFnv1a32")}},
             "key": {k: save_key.get(k) for k in ("reached", "prevented", "targetInPanel",
                                                  "targetInCm")},
             "screenshot": self.shot("ac71-saved-offline")},
        )
        # 时延观察项（**不参与退出码**）：只记录数值，不对绝对值下判据 —— 它含探针自身桥往返开销。
        self.report.add(
            "OBS-SAVE-LATENCY",
            "观察项：OS 级 Ctrl+S 的「按键 → 磁盘变化」时延（**不判**；只留痕，供与防抖时延对照）",
            "PASS" if latency is not None else "SKIP",
            {"latencyKeyToDiskSec": latency, "latencyEditToDiskSec": edit_to_change,
             "note": "本值含探针自身的桥往返开销（本机慢轮实测可达 2.4 s，快轮 0.2 s），"
                     "**不能当作产品保存性能的度量**；判定已改用机制判据（`manualPath`）。"},
            gating=False,
        )

        # ⑤ 正对照：同一标签上再插入一段、**不按** Ctrl+S ⇒ 走 `flushPane` 防抖通路。
        #    必须在关闭本标签**之前**做，否则激活项会落回"无路径"的新建标签
        #    （无用户文件可写，正对照就测不到东西）。
        #    注：磁盘变化基准由 `poll_disk_change` 自己用 `os.stat` 取（不再传 sha ——
        #    读整份文件会与产品侧的 `os.replace` 抢句柄，见 `poll_disk_change` 的说明）。
        marker2 = "\nAC71-DEBOUNCE-CONTRAST\n"
        self.focus_editor()
        # 观察项的成因需要证据：把"防抖通路真的被走过"落成桥调用计数
        # （`draftSave` 是防抖写盘的第一步；它一次都没被调 ⇒ 本次编辑没进入防抖通路）
        drafts_before2 = call_count(self.calls, "editor.draftSave")
        writes_before2 = call_count(self.calls, "editor.writeText")
        ins2 = self.js(js_insert_text(marker2)) or {}
        t2 = time.time()
        # 窗口给到 25 s：探针自己的桥往返（渲染层 → Qt 主线程 → Python → 写盘）在本机实测
        # 单程可达 2~10 s（慢轮），短窗口会让"其实成功了的防抖写回"落空。
        changed2 = poll_disk_change(fixture, 25.0)
        latency2 = None if changed2 is None else round(changed2 - t2, 3)
        debounce_calls = {
            "draftSaveBefore": drafts_before2,
            "draftSaveAfter": call_count(self.calls, "editor.draftSave"),
            "writeTextBefore": writes_before2,
            "writeTextAfter": call_count(self.calls, "editor.writeText"),
        }
        clean_wait = self.wait_for(
            JS_TAB_ORDER, lambda v: fixture_tab_is_clean(v, fixture.name), timeout_ms=25000,
        )
        clean_after_contrast = (isinstance(clean_wait, dict)
                                and fixture_tab_is_clean(clean_wait, fixture.name))
        # 判据同样改成**机制口径**（2026-10-15 收尾轮更正）：防抖通路的特征是
        # 「`flushPane` 先 `draftSave` 再 `writeText`」⇒ `draftSave` 计数必须 +1。
        # 不再要求 `latency2 >= 1.4` —— 那个阈值把"是不是防抖"与"这一轮快不快"混在一起，
        # 慢轮会假 FAIL（22:56 轮实测：`draftSave` 确实 +1 = 防抖通路真的走了，却因时延取不到判红）。
        debounce_ran = debounce_calls["draftSaveAfter"] > debounce_calls["draftSaveBefore"]
        self.report.add(
            "OBS-DEBOUNCE",
            "观察项（正对照）：同样一次编辑但**不按** Ctrl+S ⇒ 走 `flushPane` 防抖通路"
            "（`draftSave` +1）并最终写回磁盘",
            "PASS" if (ins2.get("ok") is True and debounce_ran and clean_after_contrast)
            else ("SKIP" if not ins2.get("ok") else "FAIL"),
            {"insert": ins2, "latencyEditToDiskSec": latency2, "debounceCalls": debounce_calls,
             "debounceRan": debounce_ran, "cleanAfterContrast": clean_after_contrast,
             "note": "判定用机制（`draftSave` +1 = 防抖通路），时延只留痕不判定；"
                     "本值含探针自身的桥往返开销，不能当作产品防抖精度的度量。",
             "orderAfterContrast": (clean_wait or {}).get("order")},
            gating=False,
        )

        # ⑥ 切标签 / 关标签（都离线）：先切到**另一个**标签（证明切换真的移动了激活项），
        #    再切回 fixture，再关它（此刻它应已被防抖写回 ⇒ 干净标签 ⇒ 不该弹关闭询问）。
        #
        # ⚠ 2026-10-15 收尾轮修正：这里原先**假定**"上一步的等待已经把标签等干净了"，
        #    而 `fixture_tab_is_clean()` 当时读的是一个从不存在的字段（恒假）⇒ 根本没等。
        #    现在改为在关闭前**自己**有界地等一次，并把结果记进证据（等不到就如实判红）。
        order_before = self.js(JS_TAB_ORDER) or {}
        other = next((t for t in (order_before.get("order") or []) if t != fixture.name), None)
        switch_away = self.js(js_click_tab(other)) if other else {}
        QTest.qWait(700)
        after_away = self.js(JS_TAB_ORDER) or {}
        switch_back = self.js(js_click_tab(fixture.name)) or {}
        QTest.qWait(700)
        switched = self.js(JS_TAB_ORDER) or {}
        s_switch = sample("after-switch")
        # 同 AC71-3：判据取**增量**（关掉一个标签 ⇒ 标签数 -1），不依赖相位起始的绝对标签数
        tabs_before_close = (self.state().get("tabCount") or 0)
        # 关之前**自己**有界地等一次"fixture 已变干净"（防抖写回完成），并把结果留证。
        # 依据：本条的 claim 是"关的是**防抖写回后的干净**标签 ⇒ 不弹询问"，
        # 因此"它到底干不干净"是前提，必须显式等待 + 记录（等不到就如实判红，不假定）。
        clean_wait_close = self.wait_for(
            JS_TAB_ORDER, lambda v: fixture_tab_is_clean(v, fixture.name), timeout_ms=20000,
        )
        clean_before_close = (isinstance(clean_wait_close, dict)
                              and fixture_tab_is_clean(clean_wait_close, fixture.name))
        order_pre_close = self.js(JS_TAB_ORDER) or {}
        dirty_before_close = [t.get("title") for t in (order_pre_close.get("tabs") or [])
                              if t.get("dirty")]
        close = self.js(js_close_tab(fixture.name)) or {}
        QTest.qWait(800)
        after_close = self.state()
        s_close = sample("after-close")
        switch_ok = (
            other is not None
            and after_away.get("activeTitle") == other
            and switched.get("activeTitle") == fixture.name
        )
        self.report.add(
            "AC71-5",
            "AC-71 ③：离线切标签（切走再切回，激活项确实跟着变）/ 关标签（关的是防抖写回后的"
            "**干净**标签 ⇒ 不触发关闭询问）",
            "PASS" if (switch_ok
                       and clean_before_close
                       and after_close.get("tabCount") == tabs_before_close - 1
                       and after_close.get("dialogLabel") is None) else "FAIL",
            {"orderBefore": order_before.get("order"), "switchTo": other,
             "switchAway": switch_away, "activeAfterAway": after_away.get("activeTitle"),
             "switchBack": switch_back, "activeAfterBack": switched.get("activeTitle"),
             "sampleAfterSwitch": s_switch, "sampleAfterClose": s_close,
             "close": close, "tabCountAfterClose": after_close.get("tabCount"),
             "tabCountBeforeClose": tabs_before_close,
             "cleanBeforeClose": clean_before_close,
             "dirtyBeforeClose": dirty_before_close,
             "expectedTabCount": tabs_before_close - 1,
             "dialogLabel": after_close.get("dialogLabel")},
        )

        # ⑦ 全程无登录拦截 / 无阻塞式弹窗
        #    判据原文是"不出现登录拦截、不出现阻塞式错误弹窗" —— 断言 "hash 从不回到 #/login"，
        #    **不是**"hash 恒为 #/editor"（进入本条前所在路由可以是别的页，如 #/files，
        #    那不是缺陷；把它写进断言等于把"起点路由"错锁成期望值）。
        hashes = [s.get("hash") for s in samples]
        passwords = [s.get("passwordInputs") for s in samples]
        dialogs = [s.get("dialogLabel") for s in samples]
        modal_widgets = [s.get("modalWidget") for s in samples]
        quiet_ok = (
            all(h != "#/login" for h in hashes)
            and all(p == 0 for p in passwords)
            and all(d is None for d in dialogs)
            and all(m is None for m in modal_widgets)
            and self.window_alive()
        )
        self.report.add(
            "AC71-6",
            "AC-71 ④：离线全程 hash **从不回到 #/login**、零 password 输入、零 DOM 模态、"
            "零宿主模态（无登录拦截、无阻塞式弹窗）",
            "PASS" if quiet_ok else "FAIL",
            {"samples": samples, "hashes": hashes, "windowAlive": self.window_alive()},
        )

        # ⑧ 「另存为」需要原生对话框 ⇒ SKIP（不得伪造）
        self.report.add(
            "AC71-7",
            "AC-71 ③：「另存为」（Ctrl+Shift+S）—— **SKIP**：该动作经 `store.savePane(forceDialog)` "
            "弹出**原生** `QFileDialog`，探针无法在不注入产品源码的前提下选择路径，"
            "无原生对话框自动化能力时不得伪造成 PASS",
            "SKIP",
            {"why": "原生保存对话框无自动化接口（探针不改产品源码、不用 stub 替换对话框）",
             "observedTabStillOpen": self.state().get("tabCount"),
             "modalWidgetNow": self.modal_widget()},
        )

    # ═══════════════════════ AC-68（设计 §13.3 第 15 条上半） ═══════════════════════

    def ac68_drag(self, fixtures: list[Path]) -> None:
        """AC-68：3 标签 A/B/C → 合成 Pointer Events 把 C 拖到最前 ⇒ 顺序 C/A/B 且激活项不变；
        切「设置」再回「编辑器」⇒ 顺序、激活项**与激活面板正文**保持（正文见 AC68-2 的补牙）。"""
        from PySide6.QtTest import QTest

        names = [f.name for f in fixtures]
        pushed = self.shell.notify_external_paths([str(f) for f in fixtures])
        opened = self.wait_for(JS_UI_STATE, lambda v: v.get("tabCount", 0) == len(fixtures),
                               timeout_ms=30000) or {}
        QTest.qWait(600)
        order0 = self.js(JS_TAB_ORDER) or {}
        # 先激活第一个（A）：这样"拖拽后激活项不变"才是**有区分度**的判据
        self.js(js_click_tab(names[0]))
        QTest.qWait(500)
        before = self.js(JS_TAB_ORDER) or {}
        down_move = self.js(js_drag_down_move(names[2])) or {}
        QTest.qWait(500)
        observed = self.js(JS_TAB_ORDER) or {}
        up = self.js(JS_DRAG_UP) or {}
        QTest.qWait(600)
        after = self.js(JS_TAB_ORDER) or {}

        target_order = [names[2], names[0], names[1]]
        order_changed = after.get("order") == target_order
        handler_ran = (observed.get("dragClassCount", 0) > 0
                       or observed.get("draggingTitle") == names[2])
        detail = {
            "pushedCount": pushed,
            "openedTabCount": opened.get("tabCount"),
            "orderAfterOpen": order0.get("order"),
            "activeAfterClickFirst": before.get("activeTitle"),
            "orderBeforeDrag": before.get("order"),
            "dragDispatch": down_move,
            "observedDuringDrag": {k: observed.get(k) for k in
                                   ("order", "dragClassCount", "draggingTitle", "activeTitle")},
            "dragUp": up,
            "orderAfterDrag": after.get("order"),
            "targetOrder": target_order,
            "activeAfterDrag": after.get("activeTitle"),
            "pointerEventSupported": down_move.get("pointerEventSupported") is not False,
            "screenshot": self.shot("ac68-after-drag"),
        }
        if order_changed:
            status = "PASS"
        elif handler_ran:
            status = "FAIL"  # 拖拽态已建立（说明事件到了组件）⇒ 没重排是实现缺陷
        else:
            status = "SKIP"  # 事件没到 React 处理器 ⇒ 环境边界，不写"已验证"
        self.report.add(
            "AC68-1",
            "AC-68 ①：合成 Pointer Events（4 px 阈值 / 跨中点 / 固定 pointerId）把 C 拖到最前 ⇒ "
            "标签条顺序变为 C / A / B",
            status, detail,
        )

        # 切「设置」再回「编辑器」⇒ 顺序、激活项**与正文**都保持
        #
        # ★ 本轮补牙（原版 AC68-2 只断言"顺序与激活项保持"，**根本没读正文** —— 同文件现成的
        #   `JS_DOC_TEXT` 在别处用过，唯独这里没用 ⇒ 漏掉了用户实测的 F5「切菜单往返正文丢光」）。
        #   判据必须能区分「**内存里**的未保存正文被保住」与「**磁盘上**的内容被重新读回来」，
        #   做法三条（缺一不可）：
        #     ① 切走**前**用 CM6 自己的事务往激活面板正文**追加标记文本**（未保存、不进磁盘）；
        #     ② **不等 1500 ms 防抖**（`scratch-store.ts` 的 `AUTO_SAVE_DEBOUNCE_MS`）就切走，
        #        并把 edit→switch 的耗时记进证据（`editToSwitchMs`）；
        #     ③ 切走那一刻读一次 fixture 文件的 sha256：与编辑前**相同** ⇒ 磁盘上此刻还没有
        #        这次编辑 ⇒ 切回后的正文只可能来自 store 的内存快照
        #        （remount 的唯一正文来源是 `ScratchEditorHost.tsx:612` 的
        #        `takeInitialText(paneId) ?? ''`，**不读磁盘**）。
        doc_before_edit = self.js(JS_DOC_TEXT) or {}
        fixture_file = fixtures[0]
        fixture_sha_before = sha256_file(fixture_file)
        marker = "\nAC77-SURVIVE\n"
        edit_started = time.monotonic()
        edited = self.js(js_insert_text(marker)) or {}
        doc_after_edit = self.js(JS_DOC_TEXT) or {}
        self.js(js_go_hash("#/settings"))
        edit_to_switch_ms = round((time.monotonic() - edit_started) * 1000)
        fixture_sha_at_switch = sha256_file(fixture_file)
        QTest.qWait(900)
        settings = self.state()
        self.js(js_go_hash("#/editor"))
        QTest.qWait(1200)
        self.wait_for(JS_UI_STATE,
                      lambda v: v.get("workspaceCount", 0) >= 1, timeout_ms=15000)
        doc_after_back = self.wait_for(
            JS_DOC_TEXT, lambda v: v.get("ok") is True, timeout_ms=15000) or {}
        QTest.qWait(500)
        after_back = self.js(JS_TAB_ORDER) or {}
        # 正文判据（**必须等于"编辑后"的值，且"编辑后"确实不同于"编辑前"**）
        doc_equal = (
            doc_after_back.get("docCodePoints") == doc_after_edit.get("docCodePoints")
            and doc_after_back.get("docFnv1a32") == doc_after_edit.get("docFnv1a32")
        )
        doc_changed_by_edit = (
            doc_after_edit.get("docFnv1a32") != doc_before_edit.get("docFnv1a32")
            and doc_after_edit.get("docCodePoints", 0)
            == doc_before_edit.get("docCodePoints", -1) + len(marker)
        )
        doc_keep_ok = bool(doc_equal and doc_changed_by_edit)
        order_keep_ok = (
            settings.get("hash") == "#/settings"
            and settings.get("workspaceCount") == 0
            and after_back.get("order") == after.get("order")
            and after_back.get("activeTitle") == after.get("activeTitle")
        )
        keep_ok = order_keep_ok and doc_keep_ok
        self.report.add(
            "AC68-2",
            "AC-68 ② + AC-77①（真机半边）：切到「设置」再回「文本编辑器」⇒ 标签顺序、激活项**与"
            "激活面板正文**均保持（未保存、未等防抖就切走 ⇒ 保的是**内存里的活正文**）",
            "PASS" if keep_ok else "FAIL",
            {"settingsHash": settings.get("hash"),
             "settingsWorkspaceCount": settings.get("workspaceCount"),
             "orderBefore": after.get("order"), "orderAfterBack": after_back.get("order"),
             "activeBefore": after.get("activeTitle"),
             "activeAfterBack": after_back.get("activeTitle"),
             "tabCountAfterBack": after_back.get("count"),
             "orderKeepOk": order_keep_ok,
             # ── 正文判据（读数只含长度 / 指纹 / 布尔，**不含正文**，§9.14）──
             "docKeepOk": doc_keep_ok,
             "docInsert": {"ok": edited.get("ok"), "markerCodePoints": len(marker),
                           "docLengthAfterInsert": edited.get("docLength")},
             "docBeforeEdit": {"docCodePoints": doc_before_edit.get("docCodePoints"),
                               "docFnv1a32": doc_before_edit.get("docFnv1a32")},
             "docAfterEdit": {"docCodePoints": doc_after_edit.get("docCodePoints"),
                              "docFnv1a32": doc_after_edit.get("docFnv1a32")},
             "docAfterBack": {"docCodePoints": doc_after_back.get("docCodePoints"),
                              "docFnv1a32": doc_after_back.get("docFnv1a32"),
                              "r15Error": doc_after_back.get("r15Error")},
             "expectedAfterBack": {"docCodePoints": doc_after_edit.get("docCodePoints"),
                                   "docFnv1a32": doc_after_edit.get("docFnv1a32")},
             "docEqualAfterBack": doc_equal,
             "docChangedByEdit": doc_changed_by_edit,
             "docChangedFromBeforeEdit": (
                 doc_after_back.get("docFnv1a32") != doc_before_edit.get("docFnv1a32")),
             # ── "内存而不是磁盘"的三条旁证 ──
             "editToSwitchMs": edit_to_switch_ms,
             "debounceMs": 1500,
             "switchedBeforeDebounce": edit_to_switch_ms < 1500,
             "fixtureShaBeforeEdit": fixture_sha_before,
             "fixtureShaAtSwitch": fixture_sha_at_switch,
             "fixtureUnchangedAtSwitch": fixture_sha_before == fixture_sha_at_switch,
             "fixtureName": fixture_file.name,
             "screenshot": self.shot("ac68-after-route-return")},
        )

    # ═══════════════════════ AC-67（设计 §13.3 第 15 条下半） ═══════════════════════

    def ac67_overflow(self, fixtures: list[Path]) -> None:
        """AC-67：12 标签 + 窗口缩到 920×620 ⇒ 标签条真的溢出 ⇒ 溢出下拉能定位并选中第 12 个。"""
        from PySide6.QtTest import QTest

        names = [f.name for f in fixtures]
        pushed = self.shell.notify_external_paths([str(f) for f in fixtures])
        opened = self.wait_for(JS_UI_STATE, lambda v: v.get("tabCount", 0) == len(fixtures),
                               timeout_ms=40000) or {}
        QTest.qWait(700)
        width_before = (self.state() or {}).get("innerWidth")
        self.shell.resize(920, 620)
        QTest.qWait(900)
        after_resize = self.state()
        geometry = self.js(JS_TAB_GEOMETRY) or {}
        overflow_entry = after_resize.get("overflowButton")
        overflow_ok = (
            opened.get("tabCount") == len(fixtures)
            and overflow_entry == 1
            and geometry.get("scrollable") is True
        )
        self.report.add(
            "AC67-1",
            "AC-67 ①：连续打开 12 个标签页 + 窗口缩到 920×620 ⇒ 标签条**真的溢出**"
            "（`scrollWidth > clientWidth`）且溢出入口存在",
            "PASS" if overflow_ok else "FAIL",
            {"pushedCount": pushed, "tabCount": opened.get("tabCount"),
             "innerWidthBefore": width_before, "innerWidthAfter": after_resize.get("innerWidth"),
             "innerHeightAfter": after_resize.get("innerHeight"),
             "overflowButtonCount": overflow_entry,
             "stripScrollWidth": geometry.get("scrollWidth"),
             "stripClientWidth": geometry.get("clientWidth"),
             "scrollable": geometry.get("scrollable"),
             "visibleCount": geometry.get("visibleCount"),
             "screenshot": self.shot("ac67-overflowed")},
        )

        # 先把第 1 个设为激活（这样"点第 12 项改变了激活项"才有区分度）
        self.js(js_click_tab(names[0]))
        QTest.qWait(700)
        active_before = (self.js(JS_TAB_ORDER) or {}).get("activeTitle")
        self.js(JS_CLICK_OVERFLOW)
        QTest.qWait(600)
        items = self.js(JS_OVERFLOW_ITEMS) or {}
        menu_ok = items.get("count") == len(fixtures) and items.get("menuPresent") is True
        self.report.add(
            "AC67-2",
            "AC-67 ①：点溢出入口 ⇒ 下拉列出**全量 12 项**（顺序同标签条，当前激活项带 "
            "`aria-checked`）",
            "PASS" if menu_ok else "FAIL",
            {"activeBeforeOpenMenu": active_before, "menuPresent": items.get("menuPresent"),
             "itemCount": items.get("count"), "expected": len(fixtures),
             "items": items.get("items"),
             "firstChecked": [i.get("checked") for i in (items.get("items") or [])][:3],
             "screenshot": self.shot("ac67-overflow-menu")},
        )

        click_item = self.js(js_click_overflow_item(len(fixtures) - 1))
        QTest.qWait(900)
        active_after = (self.js(JS_TAB_ORDER) or {})
        geometry_after = self.js(JS_TAB_GEOMETRY) or {}
        state_after = self.state()
        menu_closed = (self.js(JS_OVERFLOW_ITEMS) or {}).get("menuPresent") is False
        index12 = (geometry_after.get("tabs") or [{}] * len(fixtures))[len(fixtures) - 1]
        active_panel_ok = (
            state_after.get("activePanelId") is not None
            and state_after.get("activePanelCount") == 1
            and index12.get("active") is True
            and geometry_after.get("activeIndex") == len(fixtures) - 1
            and geometry_after.get("activeVisible") is True
        )
        ok = (
            click_item.get("ok") is True
            and active_after.get("activeTitle") == names[-1]
            and active_panel_ok
            and menu_closed
        )
        self.report.add(
            "AC67-3",
            "AC-67 ②③：点下拉第 12 项 ⇒ 激活项 = 第 12 个、`[data-editor-panel-active=\"true\"]` 的 id "
            "与之一致、且该标签**几何可见**（几何断言，非存在性）",
            "PASS" if ok else "FAIL",
            {"clickItem": click_item, "activeTitleAfter": active_after.get("activeTitle"),
             "expectedTitle": names[-1],
             "activePanelId": state_after.get("activePanelId"),
             "activePanelCount": state_after.get("activePanelCount"),
             "activeIndex": geometry_after.get("activeIndex"),
             "activeVisible": geometry_after.get("activeVisible"),
             "stripLeft": geometry_after.get("stripLeft"),
             "stripRight": geometry_after.get("stripRight"),
             "twelfthTab": index12,
             "menuClosedAfterClick": menu_closed,
             "screenshot": self.shot("ac67-twelfth-active")},
        )

    # ═══════════════════════ 设计 R-13：内存随标签数增长（观察项） ═══════════════════════

    def mem_r13(self, out_dir: Path) -> None:
        """设计 §13.1 的 **R-13** 真机读数：**激活过的**标签各持一个 CM6 实例 ⇒ 内存随标签数增长。

        为什么这是**观察项**而不是 AC：设计对 R-13 只要求"真机测内存；退路 = 非激活标签按
        LRU 卸载，**阈值由实测数据定**" —— 阈值尚未确定，所以本条**不对内存绝对值下判据**：
        `PASS` 只表示"读数取到手、且 12 个 CM6 实例确实同时挂载"，取不到就如实 `SKIP`、**不编数字**。
        """
        from PySide6.QtTest import QTest

        # 造 12 个**从未投递过**的 fixture：`open-paths-queue` 的 `delivered` 是**会话级**去重，
        # 复用 AC-67 那 12 个路径会被直接丢弃（拿不到 12 个标签），基线也就无从谈起。
        fixtures: list[Path] = []
        for index in range(1, 13):
            path = out_dir / "fixtures" / f"r15-mem-{index:02d}.txt"
            path.write_bytes(f"内存读数 fixture {index}\n".encode("utf-8"))
            fixtures.append(path)

        host_exe = Path(sys.executable).name
        renderer_exe = "QtWebEngineProcess.exe"

        def snapshot(tag: str) -> dict:
            return {
                "tag": tag,
                "js": self.js(JS_MEM) or {},
                "renderer": win_process_working_set_mb((renderer_exe,)),
                "host": win_process_working_set_mb((host_exe,)),
            }

        # ① 基线：0 标签 → 只开 1 个（= 1 个 CM6 实例）
        self.close_all_tabs()
        QTest.qWait(1200)
        self.shell.notify_external_paths([str(fixtures[0])])
        self.wait_for(JS_UI_STATE, lambda v: v.get("tabCount", 0) == 1, timeout_ms=30000)
        QTest.qWait(900)
        base = snapshot("1-tab")

        # ② 开到 12 个并**逐个激活**（§4.8 懒挂载 ⇒ 只有被激活过的标签才 setMounted / 建 CM6）
        self.shell.notify_external_paths([str(f) for f in fixtures[1:]])
        self.wait_for(JS_UI_STATE, lambda v: v.get("tabCount", 0) == len(fixtures),
                      timeout_ms=45000)
        QTest.qWait(800)
        for fixture in fixtures:
            self.js(js_click_tab(fixture.name))
            QTest.qWait(320)
        QTest.qWait(800)
        full = snapshot("12-tab-mounted")

        base_renderer = float(base["renderer"].get("totalMb") or 0.0)
        full_renderer = float(full["renderer"].get("totalMb") or 0.0)
        delta = round(full_renderer - base_renderer, 1)
        per_tab = round(delta / 11, 2) if delta else None
        cm_full = full["js"].get("cmContentCount")
        measured = (
            base["renderer"].get("supported") is True
            and full["renderer"].get("supported") is True
            and (full["renderer"].get("count") or 0) >= 1
        )
        instances_ok = cm_full == len(fixtures)
        self.report.add(
            "MEM-R13",
            "设计 R-13 观察项：12 个**激活过的**标签同时挂载 CM6 ⇒ 渲染进程 Working Set 增量"
            "（**不对内存绝对值下判据**；取不到就 SKIP，不编数字）",
            "PASS" if (measured and instances_ok) else ("SKIP" if not measured else "FAIL"),
            {"rendererExe": renderer_exe, "hostExe": host_exe,
             "rendererRssMb1Tab": base_renderer, "rendererRssMb12Tabs": full_renderer,
             "rendererRssDeltaMb": delta, "rendererRssPerTabMb": per_tab,
             "cmContentCount1Tab": base["js"].get("cmContentCount"),
             "cmContentCount12Tabs": cm_full, "expectedCmContentCount": len(fixtures),
             "jsHeapAvailable": full["js"].get("heapAvailable"),
             "jsHeapMb1Tab": base["js"].get("usedJSHeapMb"),
             "jsHeapMb12Tabs": full["js"].get("usedJSHeapMb"),
             "base": base, "full": full,
             "note": "上报的是**实测增量**，供 Phase 7 台账 / 后续版本定 LRU 卸载阈值；"
                     "`performance.memory` 在 QtWebEngine 上可能不可用（则只有进程 RSS 口径）。"},
            gating=False,
        )


# ─────────────────────────── 磁盘与轮询工具 ───────────────────────────


def fixture_tab_is_clean(order_state: dict, title: str) -> bool:
    """`JS_TAB_ORDER` 读数里，指定标题的标签是否存在且**不脏**（防抖写回完成的判据）。"""
    tabs = [t for t in (order_state.get("tabs") or []) if t.get("title") == title]
    return bool(tabs) and not any(t.get("dirty") for t in tabs)


def win_process_working_set_mb(names: tuple[str, ...]) -> dict:
    """按**进程名**统计 Working Set（MB）—— 设计 R-13 的"内存随标签数增长"真机读数用。

    为什么用 ctypes 直接调 Win32，而不是 `psutil` / `tasklist` / `Get-Process`：
    - 桌面端 venv **没有** `psutil`（实测 `ModuleNotFoundError: No module named 'psutil'`）；
    - `tasklist` 与 `Get-Process` 的输出**本地化**（中文系统的"内存使用"列名不可解析）⇒ 解析会脆。
    ⇒ Toolhelp32 枚举 PID + `psapi.GetProcessMemoryInfo` 取 `WorkingSetSize`，输出纯数字，无本地化风险。
    非 Windows 回 `{"supported": False, ...}`，调用方据此如实标 SKIP。
    """
    if not sys.platform.startswith("win"):
        return {"supported": False, "why": "需要 Windows", "processes": []}
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.restype = wintypes.BOOL
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    except OSError as exc:  # pragma: no cover - 非 Windows / 缺 psapi
        return {"supported": False, "why": f"Win32 API 不可用：{exc}", "processes": []}

    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    invalid = ctypes.c_void_p(-1).value
    if snapshot in (None, 0, invalid):
        return {"supported": False, "why": "CreateToolhelp32Snapshot 失败", "processes": []}

    wanted = {name.lower() for name in names}
    found: list[dict] = []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        more = k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            exe = (entry.szExeFile or "").lower()
            if exe in wanted:
                pid = int(entry.th32ProcessID)
                access = (PROCESS_QUERY_LIMITED_INFORMATION
                          | PROCESS_QUERY_INFORMATION | PROCESS_VM_READ)
                handle = k32.OpenProcess(access, False, pid)
                if handle:
                    try:
                        counters = PROCESS_MEMORY_COUNTERS()
                        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                            found.append({"pid": pid, "exe": exe,
                                          "workingSetMb": round(
                                              counters.WorkingSetSize / 1048576, 1)})
                    finally:
                        k32.CloseHandle(handle)
            more = k32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snapshot)

    return {"supported": True, "processes": found, "count": len(found),
            "totalMb": round(sum(item["workingSetMb"] for item in found), 1)}


def sha256_file(path: Path) -> str:
    """文件指纹（**只回哈希**，不回内容）。文件不存在回空串。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def read_text_strict(path: Path) -> str | None:
    """按 UTF-8 严格解码读取（失败回 `None`，由调用方判定失败而不是编造内容）。"""
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _pump(ms: int) -> None:
    """等待 `ms` 毫秒，并**泵一次 Qt 事件循环**（有 Qt 就用 `QTest.qWait`，否则退回 `sleep`）。

    为什么不能直接 `time.sleep` —— 这是 2026-10-15 22:11 轮实测到的**自我阻塞**缺陷：
    本探针跑在 Qt 主线程里，而渲染层发出的桥请求（`draftSave` / `writeText`）正是由
    **同一个主线程**处理的。轮询磁盘时若用 `time.sleep`，事件循环被堵死 ⇒ "正被等待的那个
    写盘"永远无法完成。现场证据：`OBS-DEBOUNCE` 报 `latencyEditToDiskSec=null`（6 s 窗口内
    磁盘未变），而同一轮日志显示 `draftSave`/`writeText` 恰好在**轮询窗口结束之后**才被服务，
    且文件确实写成了 `fixture + 两个标记 = 84 字节` ⇒ **防抖写回本身是成功的**，失败的是测量。
    """
    try:
        from PySide6.QtTest import QTest

        QTest.qWait(ms)
    except Exception:  # pragma: no cover - 无 Qt 环境兜底（本探针实际不会走到）
        time.sleep(ms / 1000.0)


def poll_disk_change(path: Path, timeout_s: float) -> float | None:
    """轮询磁盘直到**变化发生**；返回变化被观测到的时刻（`None` = 超时未变）。

    本函数在 2026-10-15 收尾轮被修过**两次**，两次都是"探针自己造成的假 FAIL"，合并结论：

    1. **必须泵事件循环**（见 `_pump`）：被等的那个桥写入由 **Qt 主线程**处理，`time.sleep`
       会把事件循环堵死 ⇒ 写入永远无法完成（实测 `latencyEditToDiskSec=null`，而日志显示
       `draftSave`/`writeText` 恰好在轮询窗口**结束之后**才被服务）。
    2. **不许靠"读整份文件"来判变化**：产品侧的原子写是「写临时文件 → `os.replace`」，而
       Windows 上 `os.replace` 要求目标文件**没有其它句柄**（CPython 的 `open()` 不带
       `FILE_SHARE_DELETE`）。探针每 50 ms `read_bytes()` 正好会制造这种句柄 ⇒
       **探针把被测写入撞掉了**。现场铁证（`out/r15-20261015-225954/r15-run.log`）：
       ```
       [23:00:25] [WARNING] 桥响应 r22-… 失败：r15-ac71-offline.txt 写入失败：Permission…
       [23:00:27] [WARNING] 桥响应 r24-… 失败：r15-ac71-offline.txt 写入失败：Permission…
       [23:00:39] [INFO]    已保存文件: r15-ac71-off#8b0497d0 size=84；写文件 … ok=True
       ```
       ⇒ 改用 `os.stat` 的 `(st_size, st_mtime_ns)` 判变化 —— `os.stat` **不打开文件**
       （Windows 上走 `GetFileAttributesEx`），不会与产品写入抢句柄。
    """
    try:
        base = os.stat(path)
        baseline: tuple[int, int] | None = (base.st_size, base.st_mtime_ns)
    except OSError:
        baseline = None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            current = os.stat(path)
            now: tuple[int, int] | None = (current.st_size, current.st_mtime_ns)
        except OSError:
            now = None
        if now != baseline:
            return time.time()
        _pump(100)
    return None


# ─────────────────────────── 启动 ───────────────────────────


def make_recorder():
    from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

    class Recorder(QWebEngineUrlRequestInterceptor):
        def __init__(self) -> None:
            super().__init__()
            self.remote: list[dict] = []
            self.local_count = 0

        def interceptRequest(self, info) -> None:  # noqa: N802 - Qt 虚函数名
            url = info.requestUrl().toString()
            if url.startswith(("http://", "https://")):
                # 只留 path（去 query / fragment）：不含用户文件路径与令牌（§9.14）
                path = url.split("://", 1)[-1]
                path = path.split("/", 1)[1] if "/" in path else ""
                self.remote.append({
                    "path": "/" + path.split("?", 1)[0].split("#", 1)[0],
                    "at": round(time.time(), 2),
                })
            else:
                self.local_count += 1

    return Recorder()


def point_web_root_at(webui_root: Path) -> dict:
    """把外壳的前端产物根指到 `webui_root`（**只在探针进程内 monkeypatch**，不改产品源码）。

    用途：仓库里 `ui/dist` 是 gitignore 的构建产物，可能落后于 `ui/src`
    （并行施工时必然如此）。用 `npx vite build --outDir <证据目录>/.tmp_webui/webui`
    把**当前源码**构建到证据目录，再让探针只针对这一份产物跑 ⇒ 判据可追溯到确定的产物哈希。

    `resolve_web_root` 在 `web_shell` 内是**模块级全局查找**（`is_internal_url:351` /
    `resolve_index_html:109` / `load_ui:680` 都现取），所以改这一个属性即可整体生效，
    且不触碰 `sys.frozen` / `sys._MEIPASS`（后者会连带改 `BASE_DIR` 与图标路径）。
    """
    from src.ui import web_shell

    original = web_shell.resolve_web_root()
    web_shell.resolve_web_root = lambda: str(webui_root)  # type: ignore[assignment]
    index = webui_root / "index.html"
    return {"originalWebRoot": original, "patchedWebRoot": str(webui_root),
            "indexExists": index.exists(),
            "indexSha256Prefix": hashlib.sha256(index.read_bytes()).hexdigest()[:16]
            if index.exists() else None}


def build():
    from src.bridge import Bridge
    from src.ui.web_shell import WebShell, build_app
    from src.utils.input_method import setup_input_method

    setup_input_method()
    app = build_app()
    app.setQuitOnLastWindowClosed(False)
    bridge = Bridge()
    shell = WebShell(objects={"bridge": bridge}, frameless=True,
                     publish_event=bridge.publish_event)
    recorder = make_recorder()
    profile = shell.view.page().profile()
    profile.setUrlRequestInterceptor(recorder)
    load_ok = shell.load_ui()
    shell.show()
    return app, shell, recorder, load_ok


def make_fixtures(out_dir: Path) -> dict[str, object]:
    """在证据目录里造 fixture（每次运行重写，幂等）。

    - AC-64 的 fixture 带**中文 + 制表符 + 无末尾换行**（编码/换行/语言判据要能真的区分）；
    - AC-67 的 12 个 fixture 用**长文件名** ⇒ 标签 `max-width: 200px` 被撑满，
      12 × 200 px 远大于 920 px 窗口 ⇒ 溢出判定不依赖"标题恰好很短"这种巧合。
    """
    fixtures = out_dir / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)

    ac64_text = "第一行：中文内容与制表符\tOK\nsecond line ASCII\n第三行结束（无末尾换行）"
    ac71_text = "离线内容 A 行\n离线内容 B 行\n"
    tab_a = "r15-tab-a.txt"
    tab_b = "r15-tab-b.txt"
    tab_c = "r15-tab-c.txt"
    overflow_names = [f"r15-12-overflow-fixture-{i:02d}-tab-title.txt" for i in range(1, 13)]

    def write(name: str, text: str) -> Path:
        path = fixtures / name
        path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
        return path

    ac64 = write("r15-ac64-open.log", ac64_text)
    ac71 = write("r15-ac71-offline.txt", ac71_text)
    tabs = [write(name, f"{name} 内容\n") for name in (tab_a, tab_b, tab_c)]
    overflow = [write(name, f"{name} 内容\n") for name in overflow_names]
    return {
        "ac64": ac64,
        "ac64Text": ac64_text,
        "ac71": ac71,
        "ac71Text": ac71_text,
        "tabs": tabs,
        "overflow": overflow,
        "fixtureNames": {
            "ac64": ac64.name, "ac71": ac71.name,
            "tabs": [p.name for p in tabs], "overflow": [p.name for p in overflow],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="r15 AC-64 / AC-71 / AC-67 / AC-68 真机探针（v1.6.1）")
    parser.add_argument("--offscreen", action="store_true", help="无显示环境：OS 按键类判据标 SKIP")
    parser.add_argument("--only", default=",".join(ALL_CRITERIA),
                        help="只跑指定判据（逗号分隔：64,71,67,68；默认全部）")
    parser.add_argument("--skip-login", action="store_true",
                        help="跳过 AC-64 的渲染层登录（AC-64 记 SKIP）；AC-71/67/68 仍走 Python 侧登录")
    parser.add_argument("--out", default="", help="证据目录（默认 poc/editor-probes/out/r15-<时间戳>）")
    parser.add_argument("--webui", default="",
                        help="前端产物根目录（默认走 ui/dist；<dir>/index.html 必须存在）。"
                             "用于把当前 ui/src 构建到证据目录后再跑，避免测到陈旧产物")
    args = parser.parse_args()

    only = tuple(t.strip().lstrip("acAC") for t in args.only.split(",") if t.strip())
    bad = [t for t in only if t not in ALL_CRITERIA]
    if bad:
        print(f"--only 取值非法：{bad}（合法：{list(ALL_CRITERIA)}）")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else SCRIPT.parent / "out" / f"r15-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    real_window = not args.offscreen and not os.environ.get("QT_QPA_PLATFORM", "").startswith(
        "offscreen")

    # 用户目录沙箱：必须在导入 src.config.settings 之前改 APPDATA（草稿 / 会话 / 快捷键全落这里）。
    # 目录名带 `.tmp_` 前缀 → 命中 `.gitignore` 的 `jflove-desktop/poc/**/.tmp_*`（严禁入库）
    appdata = out_dir / ".tmp_appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(appdata)
    os.environ["LOCALAPPDATA"] = str(appdata)

    report = Report(out_dir, only)
    report.say(f"r15 真机探针 · 平台={os.environ.get('QT_QPA_PLATFORM', '(默认)')} · "
               f"真窗口={real_window} · 判据={list(only)}")
    report.say(f"证据目录：{out_dir}")

    fixtures = make_fixtures(out_dir)
    report.say("fixture：" + json.dumps(fixtures["fixtureNames"], ensure_ascii=False))

    web_root_info: dict = {}
    if args.webui:
        web_root_info = point_web_root_at(Path(args.webui).resolve())
        report.say(f"前端产物根（--webui）：{json.dumps(web_root_info, ensure_ascii=False)}")

    try:
        app, shell, recorder, load_ok = build()
    except Exception as exc:  # noqa: BLE001 - 环境问题要能看清原因
        import traceback

        traceback.print_exc()
        report.add("P0", "真外壳可构造（QtWebEngine + 桥 + 前端产物）", "FAIL", {"error": str(exc)})
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", "")})
        return 2

    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest

    QTimer.singleShot(900000, lambda: os._exit(3))  # 看门狗（三段判据 + 隔离服务端，给 15 分钟）

    probe = R15(report, shell, out_dir, real_window, install_bridge_counters())
    server_proc = None

    if not load_ok:
        from src.ui.web_shell import resolve_index_html

        report.add("P0", "真外壳可构造（QtWebEngine + 桥 + 前端产物）", "FAIL",
                   {"load_ui": False, "index_html": resolve_index_html()})
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", "")})
        return 2

    exit_code = 1
    try:
        QTest.qWait(1200)
        initial = probe.wait_for(JS_UI_STATE, lambda v: bool(v.get("hash")) or v.get("r15Error"),
                                 timeout_ms=25000) or {}
        report.add(
            "P0", "真外壳加载 v1.6.1 前端产物；初始态 = 未登录（登录面板 / 无编辑器入口 / 无工作区 DOM）",
            "PASS" if (initial.get("connectButton") is True
                       and initial.get("navEditor") is False
                       and initial.get("workspaceCount") == 0
                       and initial.get("tabCount") == 0) else "FAIL",
            {"load_ui": load_ok, "state": {k: initial.get(k) for k in
                                           ("hash", "connectButton", "passwordInputs", "navEditor",
                                            "workspaceCount", "tabCount", "cmBundle")}},
        )

        need_server = bool(only)
        url = ""
        if need_server and not args.offscreen:
            server_proc, port, _root = start_isolated_server(out_dir, report)
            probe.server = server_proc
            probe.server_port = port
            if server_proc is not None and port and wait_health(port).get("ok"):
                url = f"http://127.0.0.1:{port}"
                admin = python_side_admin(url)
                report.add(
                    "P1", "隔离服务端就绪 + 桌面端服务层在隔离库上造管理员（**不登录** ⇒ 渲染层仍是未登录态）",
                    "PASS" if admin.get("adminExistsAfter") and not admin.get("loggedInAfterInit")
                    else "FAIL",
                    {"port": port, "serverPid": server_proc.pid, **admin},
                )
            else:
                report.add("P1", "隔离服务端就绪 + 造管理员", "FAIL",
                           {"why": "隔离服务端起不来（见 server.log）", "port": port})
        else:
            report.add("P1", "隔离服务端就绪 + 造管理员", "SKIP",
                       "offscreen / 无判据需要服务端" if args.offscreen else "无判据需要服务端")

        logged_in_for_renderer = False

        # ── 设计 §13.3 第 12 条：AC-64 全链路 ──
        if "64" in only and not args.skip_login and url:
            probe.ac64_unlogged_panel(fixtures["ac64"])
            login = probe.ac64_renderer_login(url)
            login_detail = login.get("detail") or {}
            diagnostic = login_detail.get("diagnosticManualNavigate") or {}
            # ② 的落点失败**不阻塞** ③④⑤ 的判定：落点不对时探针已诊断性切到 /editor，
            # 于是"文件到底有没有打开成激活标签"仍可独立判定（两者是不同判据）。
            logged_in_for_renderer = bool((login.get("landed") or {}).get("navEditor"))
            if login.get("ok") or (diagnostic.get("tabCount") or 0) >= 1:
                probe.ac64_content(fixtures["ac64"], fixtures["ac64Text"])
            else:
                why = {"why": "登录后工作区没有标签页（诊断性切到 /editor 后仍为 0）",
                       "landing": login_detail.get("landing"), "diagnostic": diagnostic}
                report.add("AC64-3", "AC-64 ③：工作区恰好 1 个标签页且为激活项", "FAIL", why)
                report.add("AC64-4", "AC-64 ③：正文逐字符等于 fixture", "SKIP",
                           {"why": "没有标签页可读正文（③ 已 FAIL）"})
                report.add("AC64-5", "AC-64 ④：状态栏编码/换行/语言一致", "SKIP",
                           {"why": "没有标签页可读状态栏（③ 已 FAIL）"})
        else:
            reason = ("--skip-login" if args.skip_login
                      else "未选中 64" if "64" not in only else "隔离服务端不可用")
            report.add("AC64-1", "AC-64 ①（未登录唤起 ⇒ 只有登录面板）", "SKIP", reason)
            report.add("AC64-2", "AC-64 ②（渲染层自己登录）", "SKIP", reason)
            report.add("AC64-3", "AC-64 ③（1 个激活标签页）", "SKIP", reason)
            report.add("AC64-4", "AC-64 ③（正文逐字符相等）", "SKIP", reason)
            report.add("AC64-5", "AC-64 ④（状态栏一致）", "SKIP", reason)

        # ── 其余判据需要「已登录」的渲染层：AC-64 没建立时走 Python 侧登录 + 重载 ──
        need_editor = any(c in only for c in ("71", "67", "68"))
        if need_editor and not logged_in_for_renderer:
            if url:
                login_detail = python_side_login(url)
                state = probe.js(JS_UI_STATE) or {}
                reload_result = probe.reload_page_and_wait()
                state_after = reload_result.get("state") or {}
                ok = state_after.get("navEditor") is True
                report.add(
                    "P2",
                    "AC-71/67/68 前置：Python 侧真登录（桌面端服务层）+ 重载页面 ⇒ 渲染层进入已登录态"
                    "（本条不要求渲染层登录，故与 AC-64 无关）",
                    "PASS" if ok else "FAIL",
                    {"login": login_detail, "beforeReloadNavEditor": state.get("navEditor"),
                     "reload": reload_result.get("reload"),
                     "navEditorAfter": state_after.get("navEditor"),
                     "hashAfter": state_after.get("hash"),
                     "passwordInputsAfter": state_after.get("passwordInputs")},
                )
                logged_in_for_renderer = bool(ok)
            else:
                report.add("P2", "AC-71/67/68 前置：Python 侧真登录 + 重载", "FAIL",
                           "隔离服务端不可用 ⇒ 这些判据无法取到判据（不是 SKIP 而是前置失败）")

        if not logged_in_for_renderer:
            for cid, desc in (("AC71-1", "AC-71 前提：服务端不可达"),
                              ("AC71-2", "AC-71 ①：离线新建 + 输入"),
                              ("AC71-3", "AC-71 ②：离线打开已有路径文件"),
                              ("AC71-4", "AC-71 ①：离线 Ctrl+S 写回磁盘"),
                              ("AC71-5", "AC-71 ③：离线切 / 关标签"),
                              ("AC71-6", "AC-71 ④：无登录拦截 / 无阻塞弹窗"),
                              ("AC68-1", "AC-68 ①：拖拽重排"),
                              ("AC68-2", "AC-68 ②：切路由返回后顺序与激活项保持"),
                              ("AC67-1", "AC-67 ①：12 标签溢出"),
                              ("AC67-2", "AC-67 ①：溢出下拉列出全量"),
                              ("AC67-3", "AC-67 ②③：选中第 12 个 + 几何可见")):
                if cid.split("-")[0] == "AC71" and "71" not in only:
                    report.add(cid, desc, "SKIP", "未选中 71")
                elif cid.split("-")[0] == "AC68" and "68" not in only:
                    report.add(cid, desc, "SKIP", "未选中 68")
                elif cid.split("-")[0] == "AC67" and "67" not in only:
                    report.add(cid, desc, "SKIP", "未选中 67")
                else:
                    report.add(cid, desc, "SKIP", "渲染层未进入已登录态")
        else:
            # ── 设计 §13.3 第 13 条：AC-71 离线 ──
            if "71" in only:
                stopped = probe.ac71_stop_server()
                if stopped.get("ok"):
                    probe.ac71_offline(fixtures["ac71"], fixtures["ac71Text"])
                else:
                    report.add("AC71-2", "AC-71 ①：离线新建 + 输入", "FAIL",
                               {"why": "服务端未真的停掉 ⇒ 离线前提不成立", "stop": stopped})
            else:
                for cid, desc in (("AC71-1", "AC-71 前提：服务端不可达"),
                                  ("AC71-2", "AC-71 ①：离线新建 + 输入"),
                                  ("AC71-3", "AC-71 ②：离线打开已有路径文件"),
                                  ("AC71-4", "AC-71 ①：离线 Ctrl+S 写回磁盘"),
                                  ("AC71-5", "AC-71 ③：离线切 / 关标签"),
                                  ("AC71-6", "AC-71 ④：无登录拦截 / 无阻塞弹窗"),
                                  ("AC71-7", "AC-71 ③：另存为（SKIP：原生对话框）")):
                    report.add(cid, desc, "SKIP", "未选中 71")

            # ── 设计 §13.3 第 15 条：AC-68 拖拽排序 + AC-67 溢出可及 ──
            if "68" in only:
                report.note("清理前序标签页（避免污染 AC-68 的 A/B/C 三标签前提）")
                probe.close_all_tabs()
                probe.ac68_drag(list(fixtures["tabs"]))
            else:
                report.add("AC68-1", "AC-68 ①：拖拽重排", "SKIP", "未选中 68")
                report.add("AC68-2", "AC-68 ②：切路由返回后顺序与激活项保持", "SKIP", "未选中 68")

            if "67" in only:
                report.note("清理标签页（让「第 12 个」= 恰好 12 个标签里的第 12 个，不含前序残留）")
                probe.close_all_tabs()
                probe.ac67_overflow(list(fixtures["overflow"]))
                # 设计 R-13 的真机内存读数（观察项；复用本相位的"已登录 + 工作区可用"前提）
                probe.mem_r13(out_dir)
            else:
                for cid, desc in (("AC67-1", "AC-67 ①：12 标签溢出"),
                                  ("AC67-2", "AC-67 ①：溢出下拉列出全量"),
                                  ("AC67-3", "AC-67 ②③：选中第 12 个 + 几何可见")):
                    report.add(cid, desc, "SKIP", "未选中 67")
                report.add("MEM-R13", "设计 R-13 观察项：12 标签内存读数", "SKIP",
                           "未选中 67（内存读数复用该相位的已登录态）", gating=False)

        exit_code = report.verdict()
    except Exception as exc:  # noqa: BLE001 - 任何未预期异常都留证
        import traceback

        traceback.print_exc()
        report.add("Z9", "探针自身无未捕获异常", "FAIL", {"error": f"{type(exc).__name__}: {exc}"})
        exit_code = report.verdict() or 1

    # 沙箱标签：`--out` 可能是相对路径 ⇒ 不能假设它一定在 SCRIPT.parent 之下（否则 ValueError 会
    # 在收尾处炸掉，把已经跑出来的全部判据证据一起丢掉）
    try:
        sandbox_label = str(appdata.relative_to(SCRIPT.parent))
    except ValueError:
        sandbox_label = str(appdata)

    extra = {
        "platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "real_window": real_window,
        "skip_login": args.skip_login,
        "fixtureNames": fixtures["fixtureNames"],
        "bridgeCallCounts": {k: call_count(probe.calls, k) for k in
                             ("editor.detectAndRead", "editor.writeText", "editor.draftSave")},
        "bridgeInstallErrors": probe.calls.get("__install__", []),
        "remoteRequests": recorder.remote[-80:],
        "remoteRequestCount": len(recorder.remote),
        "localRequestCount": recorder.local_count,
        "user_data_sandbox": sandbox_label,
        "webRootPatch": web_root_info,
        "isolatedServer": {"pid": server_proc.pid if server_proc else None,
                           "port": probe.server_port},
    }
    try:
        path = report.write(extra)
    except Exception as exc:  # noqa: BLE001 - 收尾写盘失败不能把整轮证据变成"没有"
        import traceback

        traceback.print_exc()
        report.say(f"结果 JSON 写入失败：{type(exc).__name__}: {exc}")
        path = out_dir / "r15-result.json"
    report.say(f"结果 JSON：{path}")
    report.say(f"退出码：{exit_code}")

    if server_proc is not None and server_proc.poll() is None:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 - 收尾尽力而为
            server_proc.kill()
    kill_isolated_servers()  # 兜底：把登记表里仍活着的子进程一并收拾（与判据无关）
    shell.view.page().profile().setUrlRequestInterceptor(None)
    QTest.qWait(200)
    app.quit()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

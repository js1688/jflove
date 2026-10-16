"""
r13 `Ctrl+W` 接管真机探针（Phase 4a 第 ⑧ 步 = 本版唯一阻塞项）

## 这个探针解决什么

设计 v1.6.1 §4.7（DS-24）把「`Ctrl+W` 必须被编辑器接管并**真实生效**」写成**真机判据**（需求 R-5 / AC-70）：

| # | 判据（设计 §4.7 第 2 条） | 本探针的观测点 |
|---|---|---|
| ① | 渲染层**收到**该键（关闭流程真的启动：脏标签弹出询问） | 探针自装的 window 捕获监听读到 keydown；脏标签按 `Ctrl+W` 后出现关闭询问 |
| ② | **宿主未执行默认动作**（动作零触发、无下载、窗口存活） | 48 个 `WebAction` 的 `triggered` 计数、下载记录、`activeModalWidget` |
| ③ | **干净标签被关闭** | `[data-scratch-tab]` 数量 1 → 0 |

同时补 **AC-63 ② 的真机 negative**（设计 v1.6.1 §13.3 第 11 条 + 需求 AC-63）：

- **H1**：OS 级 `Ctrl+W` 是否**投递到渲染层**（未登录态即可判定，不需要登录）；
- **H2**：未登录时快捷键运行时**不接管、也不 `preventDefault`**，且窗口**不关**、零标签页、零工作区 DOM。

## 为什么 H3 需要「真登录」，而伪造成本更高

`ui/src/utils/scratch/shortcut-runtime.ts:139-142` 的第一道前置是
`canUseEditor({isLoggedIn, tokenExpired})`；`isLoggedIn` 的唯一来源是 Python 侧的会话快照
（`src/bridge/bridge.py::_h_auth_session` → `session_manager.is_logged_in()`）。
而 `auth_service.try_restore_session()`（`src/services/auth_service.py:280`）会调
`_is_session_accepted_by_server()`（`:256`，内部 `file_service.list_accessible_disks()` 打服务端），
**服务端不可达就清会话** ⇒ 手写一份 `session.json` 离线伪造**走不通**。

本探针走的是**真实链路**（不 stub、不打补丁）：

1. 把 `jflove-server/`（去掉 `venv-win` / `build` / `logs`）复制到本次证据目录，用 `JFLOVE_PORT`
   起一个**隔离实例**（`settings.py:7` 的 `DB_PATH` 由文件位置派生 ⇒ 库落在副本目录，
   **绝不触碰仓库的 `jflove-db/jflove-dev.db`**，见 AGENTS.md §5.1）；
2. 用**桌面端自己的服务层** `src/services/auth_service.py`（`do_key_exchange` → `init_admin` → `login`）
   在该库上造管理员并完成登录 ⇒ 真实写 `session.json`（沙箱 `APPDATA` 内）；
3. `page().reload()` 让渲染层重新走 `main.tsx::bootstrap()` → 桥 `auth.restore_session` → 真会话；
4. 判定准入翻转（导航项出现）后，再用 **OS 级按键**验 `Ctrl+W` 接管。

## 沿用的三条实测经验（来自 r11，别再踩）

1. `runJavaScript(script, callback)` 的**回调恒为空串** ⇒ 一律走**标题通道**
   （JS 把结果写 `document.title`，Python 轮询 `page().title()`，每次带自增标记）；
2. 「派发 → 观察」必须**分两次调用**，中间 `QTest.qWait`（React 状态更新要等下一帧）；
3. Qt 合成事件（`QTest.keyClick`）**到不了渲染层**，必须用 **OS 级输入**（`keybd_event`）；
   并且探针自装的监听必须在**应用运行时之后**注册 —— 同一个 target 上同阶段的监听按注册顺序执行，
   注册更晚才能读到应用 `preventDefault()` 之后的 `event.defaultPrevented`。

## 怎么跑

    jflove-desktop\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r13-ctrlw-takeover.py
    ... --skip-login        # 只跑 H1/H2（不需要服务端；H3 记 SKIP）
    ... --negative-ac70     # AC-70 负向对照：注入"运行时不接管"后重跑接管观测点，须"判据失败"

## 退出码

- `0` 必须通过的判据全过（SKIP 不算失败；观察项不影响退出码）
- `1` 有必须通过的判据失败
- `2` 环境 / 脚本错误（UI 未加载、QtWebEngine 起不来）
- `3` 看门狗超时

## 破坏性说明与边界

- 只在自己的 `out/r13-<时间戳>/` 里写**可入库证据**（`r13-result.json` / 截图 / `server.log`）；
- **沙箱 `APPDATA` 与隔离服务端副本一律用 `.tmp_` 前缀**（`out/r13-*/.tmp_appdata`、`out/r13-*/.tmp_server`）——
  这正是仓库既有约定：`.gitignore:81-82` 写的是「桌面端 PoC：护栏临时文件（**含会话令牌 / 测试账号口令，严禁入库**）」
  ⇒ 规则 `jflove-desktop/poc/**/.tmp_*`。**不要**把它们写成 `appdata/` / `server/`，那会把
  `session.json`（含真实 JWT）与 isolate 库一起暴露成待提交文件；
- **不**启动单实例守卫、**不**建托盘、**不**建媒体浮层（与编辑器链路无关，见 r11 的边界说明）；
- 不打印 token / session_key / 正文内容；日志只出现"有没有"与计数（AGENTS.md §9.14）。
"""

from __future__ import annotations

import argparse
import ctypes
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

# Windows 控制台是 GBK：日志里有 ⇒/· 等字符，不重配置会 UnicodeEncodeError 中断探针
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

sys.path.insert(0, str(DESKTOP))

#: 探针自建的管理员凭据（只存在于本次运行的隔离库里，不写入仓库任何文件）
ADMIN_USER = "r13probe"
ADMIN_PASSWORD = "r13-probe-Passw0rd"

#: `HOST_ACTIONS` 里要统计 `triggered` 的动作名（`WebActionCount` 是哨兵，取 action 会崩）
_SENTINEL = "WebActionCount"


# ─────────────────────────── 判定与留证 ───────────────────────────


class Report:
    """收集每条判据的结果，最后写 JSON + 决定退出码。"""

    def __init__(self, out_dir: Path, negative: bool) -> None:
        self.out = out_dir
        self.negative = negative
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
            self.say(f"       {json.dumps(detail, ensure_ascii=False)[:700]}")

    def verdict(self) -> int:
        failed = [c for c in self.checks if c["gating"] and c["status"] == "FAIL"]
        return 1 if failed else 0

    def write(self, extra: dict) -> Path:
        payload = {
            "probe": "r13-ctrlw-takeover",
            "at": datetime.now().isoformat(timespec="seconds"),
            "negative_ac70": self.negative,
            "checks": self.checks,
            "notes": self.notes,
            **extra,
        }
        path = self.out / "r13-result.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# ─────────────────────────── 渲染层脚本 ───────────────────────────

#: 探针自装的按键监听（**必须在应用运行时之后注册**，见模块文档「经验 3」）
JS_INSTALL_KEY_LOG = r"""
(function () {
  try {
    if (window.__r13 && window.__r13.installed) {
      return { ok: true, already: true, count: window.__r13.keys.length,
               installedAt: window.__r13.installedAt };
    }
    window.__r13 = { keys: [], installed: true, installedAt: Date.now() };
    var record = function (phase) {
      return function (e) {
        window.__r13.keys.push({
          seq: window.__r13.keys.length,
          phase: phase,
          key: e.key,
          code: e.code,
          ctrl: !!e.ctrlKey,
          alt: !!e.altKey,
          shift: !!e.shiftKey,
          meta: !!e.metaKey,
          isTrusted: !!e.isTrusted,
          defaultPrevented: !!e.defaultPrevented,
          targetTag: e.target && e.target.tagName ? e.target.tagName : null,
          targetInPanel: !!(e.target && e.target.closest
                            && e.target.closest('[data-editor-panel]')),
          targetInCm: !!(e.target && e.target.closest && e.target.closest('.cm-editor')),
          targetAttached: !!(e.target && e.target.isConnected),
          targetPath: (function () {
            var out = [];
            var node = e.target;
            for (var i = 0; node && node.tagName && i < 6; i++) {
              var marks = '';
              if (node.hasAttribute && node.hasAttribute('data-editor-panel')) {
                marks += '[panel=' + node.getAttribute('data-editor-panel').slice(0, 8) + ']';
              }
              if (node.classList && node.classList.contains('cm-editor')) marks += '[cm-editor]';
              if (node.classList && node.classList.contains('cm-content')) marks += '[cm-content]';
              out.push(node.tagName + marks);
              node = node.parentElement;
            }
            return out;
          })(),
          bodyYield: !!(document.body && document.body.dataset.jfShortcutCapture)
        });
      };
    };
    // 注册顺序 = 执行顺序：应用运行时（App.tsx 静态 import，页面加载期注册）先跑，
    // 本监听后注册 ⇒ 读到的 defaultPrevented 就是应用接管后的真值。
    window.addEventListener('keydown', record('capture'), true);
    window.addEventListener('keydown', record('bubble'), false);
    return { ok: true, already: false, installedAt: window.__r13.installedAt };
  } catch (e) { return { r13Error: String(e) }; }
})()
"""

JS_CLEAR_KEY_LOG = """
(function () { if (window.__r13) { window.__r13.keys = []; } return true; })()
"""

JS_READ_KEY_LOG = r"""
(function () {
  try {
    var r = window.__r13 || { keys: [] };
    return { keys: r.keys, focused: document.hasFocus(), hidden: document.hidden,
             titleLen: (document.title || '').length };
  } catch (e) { return { r13Error: String(e) }; }
})()
"""

#: 未登录 / 已登录共用的 UI 状态读数（DOM 契约来自设计 v1.6.1 §4.2）
JS_UI_STATE = r"""
(function () {
  try {
    var navLabels = [];
    var navs = document.querySelectorAll('.nav-item');
    for (var i = 0; i < navs.length; i++) {
      navLabels.push((navs[i].textContent || '').trim());
    }
    var tabs = [];
    var nodes = document.querySelectorAll('[data-scratch-tab]');
    for (var j = 0; j < nodes.length; j++) {
      tabs.push({
        id: nodes[j].getAttribute('data-scratch-tab'),
        active: nodes[j].getAttribute('data-scratch-tab-active') === 'true',
        dirty: !!nodes[j].querySelector('.scratch-tab-dirty'),
        title: (nodes[j].textContent || '').trim().slice(0, 40)
      });
    }
    var dialog = document.querySelector('.modal[role="dialog"]');
    var api = window.__JFLOVE_CM__;
    return {
      hash: location.hash,
      navLabels: navLabels,
      navEditor: navLabels.indexOf('文本编辑器') >= 0,
      workspaceCount: document.querySelectorAll('[data-scratch-workspace]').length,
      emptyCount: document.querySelectorAll('[data-scratch-workspace-empty]').length,
      tabCount: tabs.length,
      tabs: tabs,
      panelCount: document.querySelectorAll('[data-editor-panel]').length,
      activePanelCount: document.querySelectorAll('[data-editor-panel-active="true"]').length,
      cmBundle: !!(api && typeof api.EditorView === 'function'),
      cmContentCount: document.querySelectorAll('.cm-content').length,
      dialogLabel: dialog ? dialog.getAttribute('aria-label') : null,
      dialogText: dialog ? (dialog.innerText || '').slice(0, 120) : null,
      hasLoginForm: !!document.querySelector('input[type="password"]'),
      focused: document.hasFocus(),
      hidden: document.hidden,
      bodyText: (document.body ? document.body.innerText.slice(0, 160) : '')
    };
  } catch (e) { return { r13Error: String(e) }; }
})()
"""


def js_go_editor() -> str:
    """把 hash 切到 `/editor`（React Router 的 hash 路由；未登录时会被 RequireAuth 送回 /login）。"""
    return "(function(){location.hash='#/editor';return {ok:true,hash:location.hash};})()"


def js_set_yield(on: bool) -> str:
    """AC-70 负向对照的注入点：置/清 `body.dataset.jfShortcutCapture`。

    `shortcut-runtime.ts:146` 的第三道前置是**让路**（快捷键设置对话框正在录制时放行）。
    用它做注入的好处：**不改一行源码**、不用重打包前端产物，就能得到"运行时**不接管**
    `Ctrl+W`、按键原样交给宿主"的可观测状态 —— 正是设计 §14.3 要求的负向注入语义。

    **坑（本轮实测）**：清理必须用 `delete`；写成 `dataset.x = undefined` 会得到
    **字符串 `"undefined"`**（真值！）⇒ 让路状态不会被解除，H5 会误报 FAIL。
    """
    if on:
        return (
            "(function(){if(document.body){document.body.dataset.jfShortcutCapture='1';}"
            "return {yield:document.body?document.body.dataset.jfShortcutCapture||null:null};})()"
        )
    return (
        "(function(){if(document.body){delete document.body.dataset.jfShortcutCapture;}"
        "return {yield:document.body?document.body.dataset.jfShortcutCapture||null:null};})()"
    )


def js_focus_editor() -> str:
    """把 DOM 焦点放到**激活面板**里的 CM6 内容元素上（`pane.close` 是面板作用域项）。"""
    return r"""
    (function () {
      try {
        var api = window.__JFLOVE_CM__;
        if (!api) return { r13Error: 'no-bundle' };
        var panel = document.querySelector('[data-editor-panel-active="true"]')
          || document.querySelector('[data-editor-panel]');
        if (!panel) return { r13Error: 'no-panel' };
        var content = panel.querySelector('.cm-content');
        if (!content) return { r13Error: 'no-content' };
        var view = api.EditorView.findFromDOM(content);
        if (!view) return { r13Error: 'no-view' };
        view.focus();
        return { ok: true, panelId: panel.getAttribute('data-editor-panel'),
                 activeElement: document.activeElement ? document.activeElement.className : null,
                 hasFocus: view.hasFocus, docLength: view.state.doc.length };
      } catch (e) { return { r13Error: String(e) }; }
    })()
    """


def js_insert_text(text: str) -> str:
    """用 **CM6 自己的事务**把正文改脏（消费方是真实的 `updateListener` → `markDirty`）。"""
    return (
        "(function(){try{var api=window.__JFLOVE_CM__;if(!api)return {r13Error:'no-bundle'};"
        "var panel=document.querySelector('[data-editor-panel-active=\"true\"]')"
        "||document.querySelector('[data-editor-panel]');"
        "if(!panel)return {r13Error:'no-panel'};"
        "var content=panel.querySelector('.cm-content');"
        "if(!content)return {r13Error:'no-content'};"
        "var view=api.EditorView.findFromDOM(content);if(!view)return {r13Error:'no-view'};"
        "view.focus();view.dispatch({changes:{from:view.state.doc.length,insert:%s}});"
        "return {ok:true,docLength:view.state.doc.length,hasFocus:view.hasFocus};"
        "}catch(e){return {r13Error:String(e)};}})()" % json.dumps(text)
    )


def js_read_doc_length() -> str:
    """读激活面板的正文长度（关闭询问点「取消」后正文必须逐字不变）。"""
    return r"""
    (function () {
      try {
        var api = window.__JFLOVE_CM__;
        if (!api) return { r13Error: 'no-bundle' };
        var panel = document.querySelector('[data-editor-panel-active="true"]')
          || document.querySelector('[data-editor-panel]');
        if (!panel) return { r13Error: 'no-panel' };
        var content = panel.querySelector('.cm-content');
        if (!content) return { r13Error: 'no-content' };
        var view = api.EditorView.findFromDOM(content);
        if (!view) return { r13Error: 'no-view' };
        return { ok: true, docLength: view.state.doc.length, text: view.state.doc.toString() };
      } catch (e) { return { r13Error: String(e) }; }
    })()
    """


def js_click_dialog_button(label: str) -> str:
    """点关闭询问里的某个按钮（『取消』/『放弃修改』/『保存』）。"""
    return (
        "(function(){try{var dlg=document.querySelector('.modal[role=\"dialog\"]');"
        "if(!dlg)return {r13Error:'no-dialog'};var btns=dlg.querySelectorAll('button');"
        "for(var i=0;i<btns.length;i++){if((btns[i].textContent||'').trim()===%s){btns[i].click();"
        "return {ok:true,clicked:%s};}}"
        "return {r13Error:'no-button',labels:Array.prototype.map.call(btns,function(b){"
        "return (b.textContent||'').trim();})};}catch(e){return {r13Error:String(e)};}})()"
        % (json.dumps(label), json.dumps(label))
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


# ─────────────────────────── 宿主默认动作计数 ───────────────────────────


def install_host_action_watch(page) -> dict:
    """给**每个真实存在的** `WebAction` 挂 `triggered` 计数器（哨兵除外，见 r5 结论）。

    本轮实测可枚举到 **47** 个非 None 动作（r5 报的 48 是 Qt 6.11 手工枚举口径；
    这里对枚举成员逐个取 `page.action()`，`WebActionCount` 哨兵与解析为 `None` 的成员都不计入）。
    报告里同时留 `hostActionNames`（实际挂上计数的名字），避免把数字当成断言。
    """
    from PySide6.QtWebEngineCore import QWebEnginePage

    counts: dict[str, int] = {}
    for member in QWebEnginePage.WebAction:
        name = member.name
        if name == _SENTINEL:
            continue  # 计数哨兵：对它取 action 会崩（r5 实测 0xC0000005）
        try:
            action = page.action(member)
        except Exception:  # noqa: BLE001 - 个别版本枚举项不存在
            continue
        if action is None:
            continue
        counts[name] = 0
        action.triggered.connect(
            lambda _checked=False, n=name: counts.__setitem__(n, counts[n] + 1)
        )
    return counts


def host_quiet_detail(counts: dict, downloads: list, shell) -> dict:
    """宿主侧"没有替我们干活"的汇总证据（设计 §4.7 判据 ②）。"""
    from PySide6.QtWidgets import QApplication

    fired = {name: n for name, n in counts.items() if n}
    modal = QApplication.activeModalWidget()
    try:
        visible = bool(shell.isVisible())
    except Exception:  # noqa: BLE001 - 窗口若已被销毁，这里会抛 RuntimeError
        visible = False
    return {
        "hostActionTotal": sum(counts.values()),
        "hostActionsFired": fired,
        "downloads": list(downloads),
        "modalWidget": type(modal).__name__ if modal is not None else None,
        "shellVisible": visible,
    }


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


def start_isolated_server(out_dir: Path, report: Report):
    """把 `jflove-server/` 复制到证据目录并起一个**隔离实例**（库在副本内，不碰仓库 dev 库）。

    :returns: `(proc, port, server_root) `；任一步失败返回 `(None, 0, None)`。
    """
    src = REPO / "jflove-server"
    python = src / "venv-win" / "Scripts" / "python.exe"
    if not python.exists():
        report.note(f"未找到服务端 venv：{python} ⇒ H3 只能 SKIP")
        return None, 0, None

    server_root = out_dir / ".tmp_server"
    dst = server_root / "jflove-server"
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    ignore = shutil.ignore_patterns("venv-win", "venv-linux", "build", "logs", "__pycache__",
                                    "*.pyc", "*.db", "*.db-journal")
    # 健壮性（2026-10-16，与 r15 同形修正）：上一轮被强杀时可能残留句柄 ⇒ `rmtree(ignore_errors)`
    # 静默失败、紧接的 `copytree` 会抛 `FileExistsError`。用 `dirs_exist_ok=True` + 退避重试自愈。
    # **与判据无关**：3 次仍失败照旧抛异常（不降级成 SKIP）。
    for attempt in (1, 2, 3):
        try:
            shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)
            break
        except OSError:
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
    health = wait_health(port)
    if not health.get("ok"):
        report.note(f"隔离服务端未就绪：{health}")
        return proc, port, server_root
    return proc, port, server_root


def python_side_login(url: str) -> dict:
    """走**桌面端自己的服务层**完成真实登录（不 stub、不伪造 session.json）。

    证据只记"有没有"（`has_token` / 用户名 / 角色 / 失效时间戳），**不记令牌**（§9.14）。
    """
    from src.services import auth_service
    from src.utils.session import session_manager

    evidence: dict = {"serverUrl": url}
    auth_service.do_key_exchange(url)
    evidence["sessionReady"] = bool(session_manager.is_session_ready())
    evidence["adminExistsBefore"] = bool(auth_service.check_admin_exists())
    if not evidence["adminExistsBefore"]:
        auth_service.init_admin(ADMIN_USER, ADMIN_PASSWORD)
    evidence["adminExistsAfter"] = bool(auth_service.check_admin_exists())
    auth_service.login(ADMIN_USER, ADMIN_PASSWORD, 7 * 24 * 3600)
    evidence.update({
        "loggedIn": bool(session_manager.is_logged_in()),
        "username": session_manager.username,
        "role": session_manager.role,
        "hasToken": bool(session_manager.token),
        "expiresAtSet": bool(session_manager.token_expires_at),
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


# ─────────────────────────── 探针主体 ───────────────────────────


class R13:
    def __init__(self, report: Report, shell, out_dir: Path, real_window: bool,
                 downloads: list, action_counts: dict) -> None:
        self.report = report
        self.shell = shell
        self.out = out_dir
        self.real_window = real_window
        self.downloads = downloads
        self.action_counts = action_counts
        self._seq = 0
        self.window_closed = False

    # ── 基础设施 ──
    def js(self, expr: str, timeout_ms: int = 4000):
        """在渲染层求值 `expr` 并经**标题通道**取回结果（见模块文档「经验 1」）。"""
        from PySide6.QtTest import QTest

        if self.window_closed:
            return {"r13Error": "window-closed"}
        self._seq += 1
        marker = f"R13#{self._seq}"
        payload = (
            "(function(){var __v;try{__v=(" + expr + ");}catch(e){__v={r13Error:String(e)};}"
            "document.title='" + marker + "::'+JSON.stringify(__v===undefined?null:__v);"
            "return true;})()"
        )
        try:
            page = self.shell.view.page()
        except Exception as exc:  # noqa: BLE001 - 窗口/页面已销毁
            self.window_closed = True
            return {"r13Error": f"no-page: {exc}"}
        page.runJavaScript(payload)
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            title = page.title()
            if title.startswith(marker + "::"):
                raw = title[len(marker) + 2:]
                try:
                    return json.loads(raw)
                except ValueError:
                    return {"r13Error": "bad-json", "raw": raw[:200]}
            QTest.qWait(40)
        return {"r13Error": "title-timeout", "title": page.title()[:120]}

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
        try:
            path = self.out / f"r13-{name}.png"
            self.shell.view.grab().save(str(path))
            return path.name
        except Exception as exc:  # noqa: BLE001 - 截图失败不影响判据
            return f"shot-failed: {exc}"

    def window_alive(self) -> bool:
        """窗口是否还活着（Ctrl+W 若被宿主处理，最直观的后果就是窗口没了）。"""
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
        QTest.qWait(600)

    # ── 一次 OS 级按键 + 后置观察（**派发与观察分两次调用**，见「经验 2」）──
    def ensure_key_log(self) -> dict:
        """保证探针监听在位，并回报本次是新建还是复用。

        **坑（本轮实测）**：H3 的 `page().reload()` 会把 `window.__r13` 一起清掉 ——
        只在 H1 装一次的话，登录后的按键"无人记录"，`press_and_read` 会误判为没收到而**重复按键**
        （实测 H3b 因此连开 2 个标签页）。所以每次按键前都重新确认（幂等安装）。
        """
        return self.js(JS_INSTALL_KEY_LOG) or {}

    def press_and_read(self, mods: list[str], key: str, wanted: str,
                       settle_ms: int = 900) -> dict:
        from PySide6.QtTest import QTest

        install = self.ensure_key_log()
        diag: dict = {"attempts": [], "install": install}
        keys: list[dict] = []
        for attempt in (1, 2):
            self.activate()
            self.shell.view.setFocus()
            QTest.qWait(150)
            os_release_modifiers()
            QTest.qWait(80)
            before_mods = os_mod_state()
            install = self.ensure_key_log()
            self.js(JS_CLEAR_KEY_LOG)
            os_press(mods, key)
            QTest.qWait(settle_ms)
            log = self.js(JS_READ_KEY_LOG) or {}
            raw = log.get("keys") or []
            diag["attempts"].append({"n": attempt, "beforeMods": before_mods,
                                     "afterMods": os_mod_state(), "all": raw,
                                     "logPresent": bool(raw) or log.get("focused") is not None})
            # 日志对象缺席（页面重载 / 监听没装上）⇒ 这一轮按下多少次都读不到，**不能重按**
            if log.get("r13Error") or (log.get("focused") is None and not raw):
                diag["aborted"] = "key-log-missing"
                break
            found = [k for k in raw if str(k.get("key", "")).lower() == wanted]
            if found:
                diag["hit"] = attempt
                keys = found
                break
        diag.setdefault("hit", 0)
        capture = [k for k in keys if k.get("phase") == "capture"]
        return {
            "reached": bool(capture),
            "ctrled": any(k.get("ctrl") for k in capture),
            "prevented": any(k.get("defaultPrevented") for k in capture),
            "keys": capture,
            "allKeys": keys,
            "diag": diag,
            "windowAlive": self.window_alive(),
            "host": host_quiet_detail(self.action_counts, self.downloads, self.shell),
        }

    # ── H0：外壳 + 初始未登录态 ──
    def h0_shell(self, load_ok: bool) -> dict:
        from PySide6.QtTest import QTest

        QTest.qWait(1200)
        state = self.wait_for(
            JS_UI_STATE,
            lambda v: bool(v.get("bodyText")) and not v.get("r13Error"),
            timeout_ms=20000,
        ) or {}
        ok = (
            bool(load_ok)
            and "r13Error" not in state
            and state.get("navEditor") is False
            and state.get("workspaceCount") == 0
            and state.get("tabCount") == 0
        )
        self.report.add(
            "H0", "真外壳加载 v1.6.1 前端产物；初始态 = 未登录（无编辑器入口 / 无工作区 DOM）",
            "PASS" if ok else "FAIL",
            {"load_ui": load_ok, "hostActions": len(self.action_counts), "state": state},
        )
        return state

    # ── H1：OS 级 Ctrl+W 是否投递到渲染层（未登录态即可判）──
    def h1_delivery(self) -> dict:
        if not self.real_window or not sys.platform.startswith("win"):
            self.report.add("H1", "OS 级 Ctrl+W 投递到渲染层", "SKIP",
                            "需要真窗口 + Windows：Qt 合成事件到不了渲染层（r5 已证）")
            return {"reached": False}
        installed = self.js(JS_INSTALL_KEY_LOG) or {}
        press = self.press_and_read(["ctrl"], "w", "w")
        self.report.add(
            "H1", "OS 级 Ctrl+W 投递到渲染层（探针监听读到 keydown；无需登录）",
            "PASS" if press["reached"] and press["ctrled"] else "FAIL",
            {"install": installed, "reached": press["reached"], "ctrled": press["ctrled"],
             "preventedWhenUnlogged": press["prevented"], "keys": press["keys"],
             "attempts": press["diag"].get("hit"), "diag": press["diag"]},
        )
        # H1 的按键就是 H2 的注入事件，结果留给 H2 复用（避免重复按键引入噪声）
        self._h1_press = press
        return press

    # ── H2：未登录时不接管、不 preventDefault、窗口不关（AC-63 ②）──
    def h2_unlogged_no_takeover(self) -> bool:
        press = getattr(self, "_h1_press", None)
        if not press or press.get("diag", {}).get("hit") in (0, None):
            self.report.add("H2", "未登录：Ctrl+W 不被接管、窗口不关（AC-63 ②）", "SKIP",
                            "H1 未取得投递证据 ⇒ 无法判定")
            return False
        state = self.js(JS_UI_STATE) or {}
        ok = (
            press["reached"]
            and not press["prevented"]                     # 未登录 ⇒ 绝不 preventDefault
            and press["windowAlive"]                       # 宿主不得顺手关掉窗口
            and state.get("tabCount") == 0
            and state.get("workspaceCount") == 0
            and state.get("dialogLabel") is None
            and press["host"]["hostActionTotal"] == 0
            and not press["host"]["downloads"]
            and press["host"]["modalWidget"] is None
            and self.window_alive()
        )
        self.report.add(
            "H2", "未登录：Ctrl+W 到达渲染层但**不接管**（不 preventDefault）、零标签页、窗口不关（AC-63 ②）",
            "PASS" if ok else "FAIL",
            {"reached": press["reached"], "defaultPrevented": press["prevented"],
             "windowAlive": press["windowAlive"], "state": state, "host": press["host"],
             "screenshot": self.shot("h2-unlogged-ctrlw")},
        )
        return bool(ok)

    # ── H2b：未登录时「新建标签页」快捷键同样不响应（AC-63 ② 的第二条通路）──
    #   ⚠ 2026-10-16 因 F3 换键而**精确更正期望值（非放宽）**：`pane.new` 的默认键已由
    #   `Ctrl+Alt+N` 改为 `Ctrl+N`（需求 AC-75①，真机由 `r20-real-gui-keys.py` 的 `K-NEWTAB` 证实）。
    #   若仍按旧键投递，本判据会退化成**空转 PASS**（该键不命中任何绑定 ⇒ 天然 `prevented=false`
    #   且不新建标签页，等于什么都没检验）—— 那正是"无法失败的断言"，必须避免。
    #   判据本体（到达渲染层 + 未被接管 + 标签数与工作区数均不变 + 窗口存活）**一个字未改**。
    def h2b_unlogged_new_tab(self) -> bool:
        if not self.real_window or not sys.platform.startswith("win"):
            self.report.add("H2b", "未登录：Ctrl+N 不响应", "SKIP", "需要真窗口 + Windows")
            return False
        before = self.js(JS_UI_STATE) or {}
        press = self.press_and_read(["ctrl"], "n", "n")
        after = self.js(JS_UI_STATE) or {}
        ok = (
            press["reached"]
            and not press["prevented"]
            and after.get("tabCount", 0) == before.get("tabCount", 0) == 0
            and after.get("workspaceCount") == 0
            and press["windowAlive"]
            and self.window_alive()
        )
        self.report.add(
            "H2b", "未登录：Ctrl+N 到达渲染层但零响应（不新建标签页、工作区不出现）",
            "PASS" if ok else "FAIL",
            {"reached": press["reached"], "defaultPrevented": press["prevented"],
             "before": {"tabCount": before.get("tabCount"),
                        "workspaceCount": before.get("workspaceCount")},
             "after": {"tabCount": after.get("tabCount"),
                       "workspaceCount": after.get("workspaceCount")},
             "windowAlive": press["windowAlive"], "keys": press["keys"]},
        )
        return bool(ok)

    # ── H3-pre：隔离服务端 + 真登录 ──
    def h3_pre_login(self, skip_login: bool) -> bool:
        if skip_login:
            self.report.add("H3pre", "隔离服务端 + 桌面端服务层真登录", "SKIP",
                            "--skip-login：本轮只验 H1/H2（AC-63 ②）；H3（AC-70 已登录态）未取到判据")
            return False
        proc, port, server_root = start_isolated_server(self.out, self.report)
        self._server = proc
        if proc is None or not wait_health(port).get("ok"):
            self.report.add("H3pre", "隔离服务端 + 桌面端服务层真登录", "SKIP",
                            {"why": "隔离服务端起不来（见 server.log）", "port": port})
            return False
        url = f"http://127.0.0.1:{port}"
        try:
            evidence = python_side_login(url)
        except Exception as exc:  # noqa: BLE001 - 登录失败必须留证而不是崩掉
            self.report.add("H3pre", "隔离服务端 + 桌面端服务层真登录", "FAIL",
                            {"serverUrl": url, "error": f"{type(exc).__name__}: {exc}"})
            return False
        ok = bool(evidence.get("loggedIn")) and bool(evidence.get("hasToken"))
        self.report.add(
            "H3pre", "隔离服务端 + 桌面端服务层真登录（key_exchange → init_admin → login）",
            "PASS" if ok else "FAIL",
            {"port": port, "serverRoot": str(server_root), "serverPid": proc.pid, **evidence},
        )
        return ok

    def reload_page(self) -> dict:
        """重载渲染层。

        **坑（本轮实测）**：Qt 6 的 `QWebEnginePage` 已经没有 `reload()`
        （`ShellPage` 继承自它，实测报 `'ShellPage' object has no attribute 'reload'`），
        `reload()` 在 `QWebEngineView` 上；备选是 `page.triggerAction(Reload)`。
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

    def reload_and_wait_gate(self) -> dict:
        """重载页面让渲染层重新走 `bootstrap()`（此时 Python 侧已是真会话）。"""
        from PySide6.QtTest import QTest

        reload_result = self.reload_page()
        QTest.qWait(1500)
        state = self.wait_for(
            JS_UI_STATE,
            lambda v: v.get("navEditor") is True,
            timeout_ms=40000,
            interval_ms=500,
        ) or {}
        state["reload"] = reload_result
        return state

    # ── H3a：登录后准入翻转 + 工作区可达 ──
    def h3a_gate_open(self) -> dict:
        state = self.reload_and_wait_gate()
        reached = state.get("navEditor") is True
        if not reached:
            self.report.add(
                "H3a", "登录后准入翻转：导航项出现、/editor 工作区可达", "FAIL",
                {"why": "重载后仍未出现「文本编辑器」导航项（会话未恢复？见 server.log）",
                 "state": state},
            )
            return state
        self.js(js_go_editor())
        editor = self.wait_for(
            JS_UI_STATE,
            lambda v: v.get("workspaceCount", 0) >= 1,
            timeout_ms=15000,
        ) or {}
        ok = editor.get("workspaceCount") == 1 and editor.get("emptyCount") == 1
        self.report.add(
            "H3a", "登录后准入翻转：导航项出现、/editor 工作区可达且显示空态",
            "PASS" if ok else "FAIL",
            {"afterReload": state, "editor": editor,
             "screenshot": self.shot("h3a-gate-open")},
        )
        return editor

    # ── H3b：已登录 + Ctrl+N ⇒ 新建干净标签页（OS 级）──
    def h3b_new_tab(self) -> dict:
        # 2026-10-16 因 F3 换键而**精确更正期望值（非放宽）**：`pane.new` 的默认键已由
        #   `Ctrl+Alt+N` 改为 `Ctrl+N`（需求 AC-75①，`ui/src/config/shortcuts.ts` 的
        #   `RUNTIME_SHORTCUTS`）⇒ 本处只换**被投递的键**，判据本体
        #   （`reached` + `prevented` + 标签数恰好 +1）一个字未改。
        before = self.js(JS_UI_STATE) or {}
        press = self.press_and_read(["ctrl"], "n", "n")
        from PySide6.QtTest import QTest

        QTest.qWait(600)
        after = self.js(JS_UI_STATE) or {}
        ok = (
            press["reached"]
            and press["prevented"]
            and after.get("tabCount", 0) == before.get("tabCount", 0) + 1
            and self.window_alive()
        )
        self.report.add(
            "H3b", "已登录：OS 级 Ctrl+N 被渲染层接管并新建 1 个标签页（接管前置的阳性对照）",
            "PASS" if ok else "FAIL",
            {"before": {"tabCount": before.get("tabCount")},
             "after": {"tabCount": after.get("tabCount"), "tabs": after.get("tabs")},
             "defaultPrevented": press["prevented"], "keys": press["keys"],
             "windowAlive": press["windowAlive"]},
        )
        return after

    # ── H3c：干净标签 + Ctrl+W ⇒ 标签被关闭、宿主无默认动作（AC-70 ②③）──
    def h3c_clean_close(self) -> dict:
        state = self.js(JS_UI_STATE) or {}
        focused = self.js(js_focus_editor()) or {}
        press = self.press_and_read(["ctrl"], "w", "w")
        from PySide6.QtTest import QTest

        QTest.qWait(600)
        after = self.js(JS_UI_STATE) or {}
        self._h3c = {
            "before": {"tabCount": state.get("tabCount"), "tabs": state.get("tabs"),
                       "dirty": [t.get("dirty") for t in (state.get("tabs") or [])]},
            "focus": focused,
            "press": press,
            "after": {"tabCount": after.get("tabCount"), "tabs": after.get("tabs"),
                      "dialogLabel": after.get("dialogLabel")},
        }
        return self._h3c

    # ── H3d：脏标签 + Ctrl+W ⇒ 关闭询问；点「取消」⇒ 标签与正文不变（AC-70 ①）──
    def h3d_dirty_prompt(self) -> dict:
        # 先补一个标签页并把它改脏（正文经 **CM6 自己的事务**写入 ⇒ 真实 updateListener 生效）
        # 2026-10-16 因 F3 换键而**精确更正期望值（非放宽）**：同 `h3b_new_tab`，
        #   `pane.new` 默认键 `Ctrl+Alt+N` → `Ctrl+N`（AC-75①）；只换键，判据强度不变。
        self.js(js_go_editor())
        self.press_and_read(["ctrl"], "n", "n")
        from PySide6.QtTest import QTest

        QTest.qWait(800)
        inserted = self.wait_for(
            js_insert_text("R13-DIRTY"),
            lambda v: v.get("ok") is True,
            timeout_ms=15000,
        ) or {}
        QTest.qWait(500)
        dirty_state = self.js(JS_UI_STATE) or {}
        doc_before = self.js(js_read_doc_length()) or {}
        focused = self.js(js_focus_editor()) or {}
        press = self.press_and_read(["ctrl"], "w", "w")
        QTest.qWait(700)
        prompted = self.js(JS_UI_STATE) or {}
        clicked = self.js(js_click_dialog_button("取消")) if prompted.get("dialogLabel") else {}
        QTest.qWait(600)
        after = self.js(JS_UI_STATE) or {}
        doc_after = self.js(js_read_doc_length()) or {}
        detail = {
            "insert": inserted,
            "dirtyTabs": [t.get("dirty") for t in (dirty_state.get("tabs") or [])],
            "focus": focused,
            "press": {"reached": press["reached"], "prevented": press["prevented"],
                      "windowAlive": press["windowAlive"], "host": press["host"],
                      "keys": press["keys"]},
            "promptLabel": prompted.get("dialogLabel"),
            "promptText": prompted.get("dialogText"),
            "cancelClick": clicked,
            "after": {"tabCount": after.get("tabCount"), "dialogLabel": after.get("dialogLabel"),
                      "tabs": after.get("tabs")},
            "docLengthBefore": doc_before.get("docLength"),
            "docLengthAfter": doc_after.get("docLength"),
            "screenshot": self.shot("h3d-dirty-ctrlw"),
        }
        ok = (
            prompted.get("dialogLabel") == "关闭标签页"
            and press["prevented"]
            and press["reached"]
            and after.get("tabCount") == prompted.get("tabCount")
            and after.get("dialogLabel") is None
            and doc_before.get("docLength") == doc_after.get("docLength")
            and self.window_alive()
        )
        detail["ok"] = bool(ok)
        self._h3d = detail
        return detail

    # ── 派发 H3c/H3d 的判定（与 negative 模式共用一套观测点）──
    def judge_h3cd(self, negative: bool) -> None:
        c = getattr(self, "_h3c", None)
        d = getattr(self, "_h3d", None)
        if not c or not d:
            self.report.add("H3c", "AC-70 ②/③：干净标签被 Ctrl+W 关闭、宿主无默认动作", "SKIP",
                            "未取得观测数据")
            self.report.add("H3d", "AC-70 ①：脏标签 Ctrl+W 弹询问、取消后内容不变", "SKIP",
                            "未取得观测数据")
            return

        if not negative:
            clean_ok = (
                c["before"]["tabCount"] == 1
                and c["after"]["tabCount"] == 0
                and c["press"]["prevented"]
                and c["press"]["reached"]
                and c["press"]["windowAlive"]
                and c["press"]["host"]["hostActionTotal"] == 0
                and not c["press"]["host"]["downloads"]
                and c["press"]["host"]["modalWidget"] is None
            )
            self.report.add(
                "H3c", "AC-70 ②③：OS 级 Ctrl+W 关闭**干净**标签页；宿主默认动作计数 = 0、窗口存活",
                "PASS" if clean_ok else "FAIL",
                {"before": c["before"], "focus": c["focus"],
                 "defaultPrevented": c["press"]["prevented"],
                 "reached": c["press"]["reached"], "after": c["after"], "host": c["press"]["host"],
                 "windowAlive": c["press"]["windowAlive"], "keys": c["press"]["keys"]},
            )
            self.report.add(
                "H3d", "AC-70 ①：OS 级 Ctrl+W 在**脏**标签上弹出关闭询问；选「取消」后标签与正文不变",
                "PASS" if d.get("ok") else "FAIL",
                {k: v for k, v in d.items() if k != "ok"},
            )
            return

        # ── 负向对照：注入了"运行时不接管（让路给宿主）"，上述判据**必须失败** ──
        clean_failed = not (
            c["before"]["tabCount"] == 1
            and c["after"]["tabCount"] == 0
            and c["press"]["prevented"]
        )
        dirty_failed = d.get("promptLabel") != "关闭标签页" or not d["press"]["prevented"]
        self.report.add(
            "H3c-neg", "负向对照：注入「不接管」后，干净标签**未被**关闭（判据按预期失败）",
            "PASS" if clean_failed else "FAIL",
            {"before": c["before"], "after": c["after"],
             "defaultPrevented": c["press"]["prevented"], "reached": c["press"]["reached"],
             "host": c["press"]["host"], "windowAlive": c["press"]["windowAlive"]},
        )
        self.report.add(
            "H3d-neg", "负向对照：注入「不接管」后，脏标签**未**弹出关闭询问（判据按预期失败）",
            "PASS" if dirty_failed else "FAIL",
            {"promptLabel": d.get("promptLabel"), "defaultPrevented": d["press"]["prevented"],
             "after": d["after"], "docLengthBefore": d.get("docLengthBefore"),
             "docLengthAfter": d.get("docLengthAfter")},
        )

    # ── H4：宿主默认动作汇总（贯穿 H1~H3 的按键）──
    def h4_host_quiet(self) -> None:
        detail = host_quiet_detail(self.action_counts, self.downloads, self.shell)
        ok = (
            detail["hostActionTotal"] == 0
            and not detail["downloads"]
            and detail["modalWidget"] is None
            and detail["shellVisible"]
        )
        self.report.add(
            "H4", f"宿主零默认动作：全部可枚举 WebAction（{len(self.action_counts)} 项）触发计数 = 0、"
                  "无下载、无宿主模态、主窗口存活",
            "PASS" if ok else "FAIL", detail,
        )

    # ── H5：负向对照的收尾（仅 --negative-ac70）──
    def h5_negative_report(self) -> None:
        yield_state = self.js(js_set_yield(False)) or {}
        self.report.add(
            "H5", "负向对照注入点还原（`body.dataset.jfShortcutCapture` 已清）",
            "PASS" if yield_state.get("yield") is None else "FAIL", yield_state,
        )


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
    downloads: list[str] = []
    profile.downloadRequested.connect(
        lambda item: (downloads.append(item.url().toString()), item.cancel())
    )
    load_ok = shell.load_ui()
    shell.show()
    return app, shell, recorder, load_ok, downloads


def main() -> int:
    parser = argparse.ArgumentParser(description="r13 Ctrl+W 接管真机探针（v1.6.1 ⑧）")
    parser.add_argument("--offscreen", action="store_true", help="无显示环境：OS 按键类判据标 SKIP")
    parser.add_argument("--skip-login", action="store_true",
                        help="只跑 H1/H2（不需要服务端）；H3 记 SKIP")
    parser.add_argument("--negative-ac70", action="store_true",
                        help="AC-70 负向对照：注入「运行时不接管」后重跑接管观测点，须判据失败")
    parser.add_argument("--out", default="", help="证据目录（默认 poc/editor-probes/out/r13-<时间戳>）")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "-neg" if args.negative_ac70 else ""
    out_dir = Path(args.out) if args.out else SCRIPT.parent / "out" / f"r13-{stamp}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    real_window = not args.offscreen and not os.environ.get("QT_QPA_PLATFORM", "").startswith(
        "offscreen")

    # 用户目录沙箱：必须在导入 src.config.settings 之前改 APPDATA（草稿 / 会话 / 快捷键全落这里）。
    # 目录名带 `.tmp_` 前缀 → 命中 `.gitignore` 的 `jflove-desktop/poc/**/.tmp_*`（严禁入库：里面有 session.json）
    appdata = out_dir / ".tmp_appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(appdata)
    os.environ["LOCALAPPDATA"] = str(appdata)

    report = Report(out_dir, args.negative_ac70)
    report.say(f"r13 Ctrl+W 接管探针 · 平台={os.environ.get('QT_QPA_PLATFORM', '(默认)')} · "
               f"真窗口={real_window} · 负向对照={args.negative_ac70}")
    report.say(f"证据目录：{out_dir}")

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

    QTimer.singleShot(600000, lambda: os._exit(3))  # 看门狗（登录 + 服务端启动较慢，给 10 分钟）

    action_counts = install_host_action_watch(shell.view.page())
    probe = R13(report, shell, out_dir, real_window, downloads, action_counts)

    if not load_ok:
        from src.ui.web_shell import resolve_index_html

        report.add("H0", "真外壳可构造（QtWebEngine + 桥 + 前端产物）", "FAIL",
                   {"load_ui": False, "index_html": resolve_index_html()})
        report.write({"platform": os.environ.get("QT_QPA_PLATFORM", "")})
        return 2

    exit_code = 1
    try:
        probe.h0_shell(load_ok)
        probe.h1_delivery()
        probe.h2_unlogged_no_takeover()
        probe.h2b_unlogged_new_tab()

        logged = probe.h3_pre_login(args.skip_login)
        if logged:
            probe.h3a_gate_open()
            probe.h3b_new_tab()
            if args.negative_ac70:
                injected = probe.js(js_set_yield(True)) or {}
                report.note(f"负向注入（让路给宿主）：{injected}")
            probe.h3c_clean_close()
            probe.h3d_dirty_prompt()
            probe.judge_h3cd(args.negative_ac70)
            if args.negative_ac70:
                probe.h5_negative_report()

        probe.h4_host_quiet()
        exit_code = report.verdict()
    except Exception as exc:  # noqa: BLE001 - 任何未预期异常都留证
        import traceback

        traceback.print_exc()
        report.add("H9", "探针自身无未捕获异常", "FAIL", {"error": f"{type(exc).__name__}: {exc}"})
        exit_code = report.verdict() or 1

    server = getattr(probe, "_server", None)
    path = report.write({
        "platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "real_window": real_window,
        "skip_login": args.skip_login,
        "requests": recorder.urls,
        "requestsRemote": [u for u in recorder.urls if u.startswith(("http://", "https://"))],
        "hostActionNames": sorted(action_counts),
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
    shell.view.page().profile().setUrlRequestInterceptor(None)
    QTest.qWait(200)
    app.quit()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

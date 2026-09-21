"""
登录页 + 业务面板骨架 的端到端冒烟测试

它验证的是交付节点「登录页 + 业务面板骨架」的验收标准：
  1. 前端产物能被 QtWebEngine 正确加载、React 能挂载（无白屏）
  2. 无边框窗口 + Web 自绘标题栏就位（三个窗口按钮真实可见）
  3. 连接服务端（真实 ECDH 密钥交换 + admin-exists）能进入登录步骤
  4. 用真实账号登录能成功，并**跳转到业务面板**（侧栏 4 项主导航 + 用户卡）

## 对用户环境的保护（强制）

登录会写会话文件，而桌面端的会话文件在用户的 `%APPDATA%\\JFLove\\storage\\session.json`
且**本机已存在**（用户处于登录态）。因此这里必须用
`poc/common/session_guard.py` 把 `auth_service._SESSION_FILE` 重定向到临时路径，
结束时校验真实文件哈希未变。**这条护栏是强制约定，详见计划 §10.4.1。**

临时账号由 server venv 的 `poc/bridge/_temp_account.py` 创建/回收
（它需要 bcrypt，只有 server venv 装了）。

运行：
    jflove-desktop\\venv-win\\Scripts\\python.exe poc\\login-shell\\test_headless.py
    ... --platform=windows    # 用真实窗口跑（能截到真图）
    ... --keep-account        # 调试时保留临时账号

退出码：0 = 全部通过；1 = 有失败项
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC_DIR = _HERE.parent
_REPO_ROOT = _HERE.parents[2]
_DESKTOP_ROOT = _REPO_ROOT / "jflove-desktop"
_SERVER_PY = _REPO_ROOT / "jflove-server" / "venv-win" / "Scripts" / "python.exe"
_TEMP_ACCOUNT_SCRIPT = _POC_DIR / "bridge" / "_temp_account.py"
_CRED_FILE = _POC_DIR / "bridge" / ".tmp_account.json"
_TMP_SESSION = _HERE / ".tmp_session.json"
_INDEX_HTML = _DESKTOP_ROOT / "ui" / "dist" / "index.html"
_OUT_DIR = _HERE / "out"

_parser = argparse.ArgumentParser(add_help=True)
_parser.add_argument("--platform", default="offscreen", help="Qt 平台插件名，默认 offscreen")
_parser.add_argument("--keep-account", action="store_true", help="结束后保留临时账号")
_parser.add_argument("--timeout", type=int, default=30, help="单步等待上限（秒）")
_args, _unknown = _parser.parse_known_args()

os.environ["QT_QPA_PLATFORM"] = _args.platform
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-dev-shm-usage --no-sandbox --in-process-gpu",
)

for _p in (str(_DESKTOP_ROOT), str(_POC_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from common.session_guard import SessionGuard  # noqa: E402
from src.bridge import Bridge  # noqa: E402
from src.ui.web_shell import WebShell, build_app  # noqa: E402

_results: list[tuple[str, bool, str]] = []


# ── 断言与工具 ────────────────────────────────────


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录一条断言结果"""
    _results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def info(text: str) -> None:
    """诊断信息（不计入通过率）"""
    print(f"[INFO] {text}")


def _history_snapshot() -> list[str]:
    """读取 Python 侧的服务端地址历史（护栏生效时读的是临时文件）"""
    from src.services import server_history_service

    return server_history_service.list_history()


def run_js(page, expr: str, timeout_ms: int = 10000):
    """同步求值一个 JS 表达式并取回结果"""
    loop = QEventLoop()
    box: dict = {}

    def _cb(result) -> None:
        box["value"] = result
        loop.quit()

    page.runJavaScript(expr, _cb)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return box.get("value")


def wait_until(page, expr: str, timeout_s: float | None = None) -> bool:
    """轮询等待 JS 表达式为真"""
    budget = _args.timeout if timeout_s is None else timeout_s
    deadline = time.time() + budget
    while time.time() < deadline:
        if run_js(page, f"Boolean({expr})") is True:
            return True
        time.sleep(0.05)
    return False


def _wait_python(predicate, timeout_s: float = 8.0, interval: float = 0.05) -> bool:
    """轮询等待一个 **Python 侧** 条件成立（用于断言托盘等原生对象的状态）"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def save_shot(window, name: str, page=None) -> str:
    """
    抓一张截图（诊断用，不计入通过率）。

    注意：`QWidget.grab()` 抓 QWebEngineView 会拿到**旧帧**（P0-1 已记录该坑）。
    因此这里先做一次渲染往返 + 轻微 resize 触发重绘，再抓。
    """
    try:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = _OUT_DIR / name
        if page is not None:
            run_js(page, "1")
        time.sleep(0.8)
        w, h = window.width(), window.height()
        window.resize(w + 1, h)
        window.resize(w, h)
        for _ in range(8):
            QApplication.processEvents()
            time.sleep(0.12)
        window.grab().save(str(path))
        return str(path)
    except Exception as exc:  # noqa: BLE001
        return f"失败（{exc}）"


# 供测试驱动 React 受控输入：必须用原生 setter + 派发 input 事件
_JS_HELPERS = """
window.__JF_TEST__ = {
  setInput: function (el, value) {
    var proto = (el.tagName === 'TEXTAREA')
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  },
  inputByPlaceholder: function (part) {
    var list = Array.prototype.slice.call(document.querySelectorAll('input,textarea'));
    return list.filter(function (e) {
      return (e.placeholder || '').indexOf(part) >= 0;
    })[0] || null;
  },
  inputByLabel: function (labelText) {
    /* 通过 label 文本找到紧随其后的 input */
    var labels = Array.prototype.slice.call(document.querySelectorAll('label'));
    var hit = labels.filter(function (l) { return l.textContent.trim() === labelText; })[0];
    if (!hit) return null;
    var box = hit.parentElement;
    return box ? box.querySelector('input,textarea,select') : null;
  },
  buttonByText: function (text) {
    var list = Array.prototype.slice.call(document.querySelectorAll('button'));
    return list.filter(function (b) {
      return (b.textContent || '').replace(/\\s+/g, '').indexOf(text) >= 0;
    })[0] || null;
  },
  /** 按按钮文字点击（先精确匹配，再退化为包含匹配，兼容带图标的分段控件） */
  clickButtonText: function (text) {
    var list = Array.prototype.slice.call(document.querySelectorAll('button'));
    var hit = list.filter(function (b) { return (b.textContent || '').trim() === text; })[0];
    if (!hit) {
      hit = list.filter(function (b) {
        return (b.textContent || '').replace(/\\s+/g, '').indexOf(text) >= 0;
      })[0];
    }
    if (!hit) return false;
    hit.click();
    return true;
  },
  /** 弹窗/卡片内的按钮文字（区分弹窗里有哪些可点项） */
  modalButtonTexts: function () {
    return Array.prototype.slice
      .call(document.querySelectorAll('.card button, .modal button'))
      .map(function (b) { return (b.textContent || '').trim(); })
      .filter(function (t) { return t.length > 0; });
  },
  /** 开关（Switch 是 role="switch" 的按钮）当前状态 */
  switchState: function () {
    var el = document.querySelector('[role="switch"]');
    return el ? el.getAttribute('aria-checked') === 'true' : null;
  },
  /** 点击开关 */
  toggleSwitch: function () {
    var el = document.querySelector('[role="switch"]');
    if (!el) return false;
    el.click();
    return true;
  },
  /** 设置数字输入框的值（走原生 setter，兼容 React 受控组件） */
  setNumberInput: function (value) {
    var el = document.querySelector('input[type="number"]');
    if (!el) return false;
    var setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value'
    ).set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  },
  /** 按 placeholder 设置输入框的值（React 受控组件需走原生 setter） */
  setInputByPlaceholder: function (part, value) {
    var list = Array.prototype.slice.call(document.querySelectorAll('input,textarea'));
    var el = list.filter(function (e) {
      return (e.placeholder || '').indexOf(part) >= 0;
    })[0];
    if (!el) return false;
    var proto = (el.tagName === 'TEXTAREA')
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  },
  /** 是否有弹窗打开（统一 Modal / ConfirmDialog 都渲染 .modal） */
  modalOpen: function () { return !!document.querySelector('.modal'); },
  /** 弹窗内输入框数量（用于断言表单字段已渲染，不依赖具体文案） */
  modalInputCount: function () {
    return document.querySelectorAll('.modal input, .modal textarea').length;
  },
  /** 点击弹窗内的按钮（优先精确匹配，退化到包含匹配） */
  clickModalButton: function (text) {
    var list = Array.prototype.slice.call(document.querySelectorAll('.modal button'));
    var hit = list.filter(function (b) {
      return (b.textContent || '').trim() === text;
    })[0] || list.filter(function (b) {
      return (b.textContent || '').replace(/\\s+/g, '').indexOf(text) >= 0;
    })[0];
    if (!hit) return false;
    hit.click();
    return true;
  },
  /**
   * 点击某一行的「操作菜单」按钮。
   *
   * 不依赖 `aria-label`：先找出**同时包含该行文本、且内部有按钮**的最小容器
   * （即那一行），再点它里面的最后一个按钮。
   * 上一版按 `button[aria-label="操作菜单"]` 查找，实测拿不到按钮（返回 False）。
   */
  clickRowMenu: function (rowText) {
    var all = Array.prototype.slice.call(document.querySelectorAll('tr, li, div'));
    var cands = all.filter(function (el) {
      return (el.textContent || '').indexOf(rowText) >= 0 && el.querySelector('button');
    });
    if (!cands.length) return false;
    cands.sort(function (a, b) {
      return a.querySelectorAll('*').length - b.querySelectorAll('*').length;
    });
    var btns = Array.prototype.slice.call(cands[0].querySelectorAll('button'));
    if (!btns.length) return false;
    btns[btns.length - 1].click();
    return true;
  },
  /**
   * 表格某一行的按钮文字清单。
   *
   * 重要：管理页的 **PC 表格**每行是内联按钮（用户行：启用徽标 / 改密 / 删除；
   * 磁盘行：编辑 / 删除），三点「操作菜单」只属于**移动端卡片**分支。
   * 测试曾按三点菜单查找，导致始终点不到（桌面端恒为 PC 布局）。
   */
  tableRowButtons: function (rowText) {
    var rows = Array.prototype.slice.call(document.querySelectorAll('table tr'));
    var row = rows.filter(function (r) {
      return (r.textContent || '').indexOf(rowText) >= 0;
    })[0];
    if (!row) return '[]';
    return JSON.stringify(
      Array.prototype.slice.call(row.querySelectorAll('button')).map(function (b) {
        return (b.textContent || '').trim() || ('[' + (b.title || '无文字按钮') + ']');
      })
    );
  },
  /** 点击表格某一行内文字为 buttonText 的按钮 */
  clickTableButton: function (rowText, buttonText) {
    var rows = Array.prototype.slice.call(document.querySelectorAll('table tr'));
    var row = rows.filter(function (r) {
      return (r.textContent || '').indexOf(rowText) >= 0;
    })[0];
    if (!row) return false;
    var hit = Array.prototype.slice.call(row.querySelectorAll('button')).filter(function (b) {
      return (b.textContent || '').trim() === buttonText;
    })[0];
    if (!hit) return false;
    hit.click();
    return true;
  },
  /** 点击文字恰好等于 text 的最内层元素（用于点击非 button 的可点行，如笔记行） */
  clickText: function (text) {
    var els = Array.prototype.slice.call(document.querySelectorAll('div,span,a,button,li'));
    var hit = els.filter(function (e) {
      return (e.textContent || '').trim() === text;
    })[0];
    if (!hit) return false;
    hit.click();
    return true;
  },
  /**
   * 点击「包含 itemText 的容器内、aria-label 含 labelPart」的按钮。
   *
   * 用于列表项里的图标按钮（如笔记行的「重命名 / 删除」）——
   * 它们平时 `display:none`（悬停才显形），但 `.click()` 仍会触发处理函数。
   */
  clickItemButton: function (itemText, labelPart) {
    var btns = Array.prototype.slice.call(document.querySelectorAll('button[aria-label]'));
    var hit = btns.filter(function (b) {
      if ((b.getAttribute('aria-label') || '').indexOf(labelPart) < 0) return false;
      var node = b;
      for (var i = 0; i < 8 && node; i++) {
        node = node.parentElement;
        if (node && (node.textContent || '').indexOf(itemText) >= 0) return true;
      }
      return false;
    })[0];
    if (!hit) return false;
    hit.click();
    return true;
  },
  /** 在文字为 text 的元素上派发右键事件（打开上下文菜单） */
  rightClickText: function (text) {
    var els = Array.prototype.slice.call(document.querySelectorAll('div,span,td,button,a'));
    var hit = els.filter(function (e) {
      return (e.textContent || '').trim() === text;
    })[0];
    if (!hit) return false;
    var r = hit.getBoundingClientRect();
    hit.dispatchEvent(new MouseEvent('contextmenu', {
      bubbles: true, cancelable: true,
      clientX: Math.round(r.left + r.width / 2),
      clientY: Math.round(r.top + r.height / 2)
    }));
    return true;
  },
  /** 上下文菜单里的项目文字 */
  contextMenuItems: function () {
    var nodes = document.querySelectorAll(
      '[role="menu"] button, [role="menu"] [role="menuitem"]'
    );
    return JSON.stringify(Array.prototype.slice.call(nodes).map(function (b) {
      return (b.textContent || '').trim();
    }));
  },
  /** 点击上下文菜单里的某一项 */
  clickContextMenuItem: function (text) {
    var nodes = document.querySelectorAll(
      '[role="menu"] button, [role="menu"] [role="menuitem"]'
    );
    var hit = Array.prototype.slice.call(nodes).filter(function (b) {
      return (b.textContent || '').trim() === text;
    })[0];
    if (!hit) return false;
    hit.click();
    return true;
  },
  /** 弹窗内的输入框 placeholder 清单（诊断用） */
  modalPlaceholders: function () {
    return JSON.stringify(Array.prototype.slice.call(
      document.querySelectorAll('.modal input, .modal textarea')
    ).map(function (i) { return i.placeholder || ''; }));
  },
  /** 弹窗内按钮文字清单（诊断用） */
  modalButtons: function () {
    return JSON.stringify(Array.prototype.slice.call(
      document.querySelectorAll('.modal button')
    ).map(function (b) { return (b.textContent || '').trim(); }));
  },
  /** 带 aria-label 的按钮清单（诊断用） */
  ariaButtons: function () {
    return JSON.stringify(
      Array.prototype.slice
        .call(document.querySelectorAll('button[aria-label]'))
        .map(function (b) { return b.getAttribute('aria-label'); })
        .slice(0, 20)
    );
  },
  /** 当前展开的行菜单里的菜单项文字 */
  menuActions: function () {
    return JSON.stringify(
      Array.prototype.slice.call(document.querySelectorAll('[role="menuitem"]'))
        .map(function (b) { return (b.textContent || '').trim(); })
    );
  },
  /** 点击行菜单里的某一项 */
  clickMenuAction: function (text) {
    var list = Array.prototype.slice.call(document.querySelectorAll('[role="menuitem"]'));
    var hit = list.filter(function (b) {
      return (b.textContent || '').trim().indexOf(text) >= 0;
    })[0];
    if (!hit) return false;
    hit.click();
    return true;
  },
  /** 权限页：按 aria-label 片段点击读/写/删开关 */
  clickPermissionToggle: function (labelPart) {
    var list = Array.prototype.slice.call(document.querySelectorAll('[role="checkbox"]'));
    var hit = list.filter(function (b) {
      return (b.getAttribute('aria-label') || '').indexOf(labelPart) >= 0;
    })[0];
    if (!hit) return false;
    hit.click();
    return true;
  },
  /** 权限页：全部开关的状态 */
  permissionToggleStates: function () {
    return JSON.stringify(
      Array.prototype.slice.call(document.querySelectorAll('[role="checkbox"]')).map(function (b) {
        return {
          label: b.getAttribute('aria-label') || '',
          on: b.getAttribute('aria-checked') === 'true'
        };
      })
    );
  },
  /**
   * 当前页面的 PageHeader 文本。
   *
   * **用它判断"是否已进入某页"**：侧栏导航项的文字与页面标题同名，
   * 用 bodyText 判断会误命中侧栏（测试曾因此在页面还没渲染时就继续操作）。
   * `.page-header` 只由页面组件的 PageHeader 渲染，侧栏没有这个类。
   */
  pageHeaderText: function () {
    var el = document.querySelector('.page-header');
    return el ? (el.textContent || '').trim() : '';
  },
  /** 是否存在文字完全等于 text 的按钮 */
  hasButtonText: function (text) {
    return Array.prototype.slice.call(document.querySelectorAll('button')).some(function (b) {
      return (b.textContent || '').trim() === text;
    });
  },
  /** DOM 概况（诊断表格/按钮是否真的渲染出来了） */
  domSummary: function () {
    return JSON.stringify({
      tables: document.querySelectorAll('table').length,
      rows: document.querySelectorAll('table tr').length,
      buttons: document.querySelectorAll('button').length,
      ariaLabels: Array.prototype.slice
        .call(document.querySelectorAll('button[aria-label]'))
        .map(function (b) { return b.getAttribute('aria-label'); }),
      menuitems: document.querySelectorAll('[role="menuitem"]').length,
      pageHeader: (function () {
        var el = document.querySelector('.page-header');
        return el ? (el.textContent || '').trim() : '';
      })()
    });
  },
  /** 含该文本且带按钮的「最小容器」的 HTML（诊断行为什么点不到） */
  rowHtml: function (rowText) {
    var all = Array.prototype.slice.call(document.querySelectorAll('tr, li, div'));
    var cands = all.filter(function (el) {
      return (el.textContent || '').indexOf(rowText) >= 0 && el.querySelector('button');
    });
    if (!cands.length) return '(没有「含该文本且有按钮」的容器)';
    cands.sort(function (a, b) {
      return a.querySelectorAll('*').length - b.querySelectorAll('*').length;
    });
    var html = cands[0].outerHTML || '';
    return html.length > 520 ? html.slice(0, 520) : html;
  },
  /** 带 data-active 的按钮（权限页左侧用户列表用它标记选中项） */
  markedButtons: function () {
    return JSON.stringify(
      Array.prototype.slice.call(document.querySelectorAll('button[data-active]')).map(
        function (b) {
          return {
            text: (b.textContent || '').trim(),
            active: b.getAttribute('data-active')
          };
        }
      )
    );
  },
  /** 点击标题栏窗口按钮（0=最小化 1=最大化/还原 2=关闭） */
  clickWindowButton: function (index) {
    var btns = document.querySelectorAll('.win-btn');
    if (!btns[index]) return false;
    btns[index].click();
    return true;
  },
  /** 主题模式（读 localStorage，与 theme-store 同一个键） */
  themeMode: function () { return localStorage.getItem('jflove.theme') || ''; },
  /** 侧栏当前处于激活态的导航项（DesktopLayout 用 data-active 标记） */
  activeNavItems: function () {
    return JSON.stringify(
      Array.prototype.slice
        .call(document.querySelectorAll('[data-active="true"]'))
        .map(function (e) { return (e.textContent || '').trim(); })
    );
  },
  /** 当前是否暗色（主题令牌挂在 <html> 上） */
  isDark: function () { return document.documentElement.classList.contains('dark'); },
  /** 取第一个 value 以给定前缀开头的输入框的值（input 的值不在 innerText 里） */
  inputValueStartingWith: function (prefix) {
    var list = Array.prototype.slice.call(document.querySelectorAll('input,textarea'));
    var hit = list.filter(function (e) {
      return (e.value || '').indexOf(prefix) === 0;
    })[0];
    return hit ? hit.value : '';
  },
  /** 聚焦该输入框（触发自绘下拉展开） */
  focusInputStartingWith: function (prefix) {
    var list = Array.prototype.slice.call(document.querySelectorAll('input,textarea'));
    var hit = list.filter(function (e) {
      return (e.value || '').indexOf(prefix) === 0;
    })[0];
    if (!hit) return false;
    hit.focus();
    return true;
  },
  /** 自绘下拉的状态快照（无面板时返回 null） */
  dropdownInfo: function () {
    var panel = document.querySelector('[role="listbox"]');
    if (!panel) return null;
    var items = Array.prototype.slice.call(panel.querySelectorAll('[role="option"]'));
    var rect = panel.getBoundingClientRect();
    return JSON.stringify({
      visible: rect.height > 0 && rect.width > 0,
      count: items.length,
      texts: items.map(function (i) { return (i.textContent || '').trim(); }),
      selected: items.findIndex(function (i) {
        return i.getAttribute('aria-selected') === 'true';
      }),
      boxShadow: getComputedStyle(panel).boxShadow,
      borderRadius: getComputedStyle(panel).borderTopLeftRadius,
    });
  },
  /** 在聚焦元素上派发一次按键（走 React 的合成事件） */
  pressKey: function (key) {
    var el = document.activeElement;
    if (!el) return false;
    el.dispatchEvent(new KeyboardEvent('keydown', { key: key, bubbles: true }));
    return true;
  },
  /** 点击下拉里的某一项 */
  clickOptionContaining: function (text) {
    var items = Array.prototype.slice.call(document.querySelectorAll('[role="option"]'));
    var hit = items.filter(function (i) {
      return (i.textContent || '').indexOf(text) >= 0;
    })[0];
    if (!hit) return false;
    hit.click();
    return true;
  },
  sidebarNav: function () {
    /* 注意：四个主导航与管理项在 <nav> 内，而「安全状态 / 设置」在侧栏底部的
       独立容器里（不在 <nav> 内），所以取整个 aside 的按钮 */
    var aside = document.querySelector('aside');
    if (!aside) return [];
    return Array.prototype.slice.call(aside.querySelectorAll('button'))
      .map(function (b) { return (b.textContent || '').trim(); })
      .filter(function (t) { return t.length > 0; });
  },
  bodyText: function () { return document.body ? document.body.innerText : ''; },
  /** 标题栏文字（只含当前页面名，不含窗口按钮） */
  titlebarText: function () {
    var h = document.querySelector('header');
    return h ? (h.textContent || '').trim() : '';
  },
  /** 页面里所有图片的加载情况（验证 logo 资源在 file:// 下能解析） */
  images: function () {
    return Array.prototype.slice.call(document.querySelectorAll('img')).map(function (i) {
      return {
        src: i.getAttribute('src'),
        natural: i.naturalWidth + 'x' + i.naturalHeight,
        rendered: Math.round(i.getBoundingClientRect().width) + 'px'
      };
    });
  },
  hash: function () { return location.hash; }
};
true;
"""


# ── 临时账号 ──────────────────────────────────────


def ensure_account() -> bool:
    """确保临时账号存在（复用已有凭据，否则创建）"""
    if _CRED_FILE.exists():
        info(f"复用已有临时账号凭据：{_CRED_FILE}")
        return True
    if not _SERVER_PY.exists():
        print(f"找不到 server venv 解释器：{_SERVER_PY}")
        return False
    proc = subprocess.run(
        [str(_SERVER_PY), str(_TEMP_ACCOUNT_SCRIPT), "create"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO_ROOT),
    )
    print((proc.stdout or "").rstrip())
    if proc.returncode != 0:
        print((proc.stderr or "").rstrip())
        return False
    return True


def drop_account() -> None:
    """回收临时账号与凭据文件"""
    if not _SERVER_PY.exists():
        return
    proc = subprocess.run(
        [str(_SERVER_PY), str(_TEMP_ACCOUNT_SCRIPT), "drop"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO_ROOT),
    )
    print((proc.stdout or "").rstrip())


def hard_delete_config_key(key: str) -> int:
    """
    物理删除一个 config 键。

    服务端没有"删除配置项"的接口，而测试为验证保存路径会新建
    `media_repair_max_concurrent`（原本不存在）。为做到**零残留**，
    测试结束时用 sqlite 直接删掉这一行。只会命中传入的 key。
    """
    import sqlite3

    db = _REPO_ROOT / "jflove-db" / "jflove-dev.db"
    con = sqlite3.connect(str(db))
    try:
        cur = con.execute("DELETE FROM config WHERE key = ?", (key,))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def hard_delete_probe_user(username: str) -> tuple[int, int]:
    """
    物理删除测试用探针账号及其权限行。

    服务端的删除接口是**软删除**（写 `deleted_at`），会在库里留行；
    测试要保证用户数据无残留，所以最后用 sqlite 直接清掉。
    只会命中传入的 username，绝不触碰其它行。

    :returns: (删除的用户行数, 删除的权限行数)
    """
    import sqlite3

    db = _REPO_ROOT / "jflove-db" / "jflove-dev.db"
    con = sqlite3.connect(str(db))
    try:
        uids = [r[0] for r in con.execute("SELECT id FROM users WHERE username = ?", (username,))]
        perm_count = 0
        for uid in uids:
            cur = con.execute("DELETE FROM user_permissions WHERE user_id = ?", (uid,))
            perm_count += cur.rowcount
        cur = con.execute("DELETE FROM users WHERE username = ?", (username,))
        user_count = cur.rowcount
        con.commit()
        return user_count, perm_count
    finally:
        con.close()


# ── 主流程 ────────────────────────────────────────


def _wait_python_pumping(app, predicate, timeout_s: float = 60.0) -> bool:
    """
    轮询等待 Python 侧条件成立，**同时持续泵 GUI 事件**。

    必须用它的场景：等待 `QMediaPlayer` 这类"靠 GUI 线程事件循环推进"的对象。
    纯 sleep 等待会把事件循环饿死 —— 实测表现为加载拖到几分钟、
    最后以 `Demuxing failed` 收场（真实应用里 `app.exec()` 一直在跑，不会有这问题）。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        app.processEvents()
        time.sleep(0.02)
    return False


def main() -> int:
    app: QApplication = build_app()
    app.setQuitOnLastWindowClosed(False)

    check("前端产物存在（ui/dist/index.html）", _INDEX_HTML.exists(), str(_INDEX_HTML))
    if not _INDEX_HTML.exists():
        print("请先执行：cd jflove-desktop/ui && npm install && npm run build")
        return 1

    # ── 护栏：必须在任何登录之前 ──
    guard = SessionGuard(_TMP_SESSION)
    guard.activate()
    info(f"会话文件护栏已激活：{guard.summary}")

    if not ensure_account():
        print("临时账号准备失败，后续断言跳过。")
        return 1

    try:
        cred = json.loads(_CRED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"读取凭据失败：{exc}")
        return 1

    bridge = Bridge()
    shell = WebShell(
        objects={"bridge": bridge},
        frameless=True,
        publish_event=bridge.publish_event,
    )
    # 与 src/main.py 保持一致：托盘 + 关闭最小化到托盘
    app.setQuitOnLastWindowClosed(False)
    shell.setup_tray(publish_event=bridge.publish_event)
    # 与 src/main.py 保持一致：原生媒体浮层 + 文件对话框父窗口
    # （漏掉这一步时 `media.open_stream` 会报"原生浮层未初始化"→ 点了视频没画面）
    from src.bridge import files as _files_bridge
    from src.bridge import media as _media_bridge_setup
    from src.ui.media_overlay import MediaOverlay

    _overlay_for_test = MediaOverlay(
        get_view=lambda: shell.view, publish_event=bridge.publish_event
    )
    _media_bridge_setup.setup(_overlay_for_test)
    _files_bridge.set_main_window(shell)
    # 与 main.py 一致：同步引擎与传输管理器（漏掉会让相关桥方法报"未初始化"）
    from src.bridge import sync as _sync_bridge_setup
    from src.bridge import transfer as _transfer_bridge_setup

    _sync_bridge_setup.setup(bridge.publish_event)
    _transfer_bridge_setup.setup(bridge.publish_event)
    if not shell.load_ui():
        check("加载前端产物", False, "WebShell.load_ui() 返回 False")
        return 1
    shell.show()
    page = shell.view.page()

    loop = QEventLoop()
    loaded = {"ok": False}

    def _on_load(ok: bool) -> None:
        loaded["ok"] = bool(ok)
        loop.quit()

    page.loadFinished.connect(_on_load)
    QTimer.singleShot(30000, loop.quit)
    loop.exec()
    check("页面加载完成", loaded["ok"])
    if not loaded["ok"]:
        return 1

    # ── 1. React 挂载 + 登录页渲染 ──
    mounted = wait_until(page, "document.querySelectorAll('#root *').length > 5")
    check("React 已挂载（非白屏）", mounted,
          f"#root 子节点数={run_js(page, 'document.querySelectorAll(\"#root *\").length')}")

    run_js(page, _JS_HELPERS)
    login_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check("登录页渲染出服务端地址表单", "服务端地址" in login_text and "连接服务端" in login_text,
          login_text.strip().splitlines()[:3])
    check("登录页显示品牌与版本", "JFLove" in login_text and "v1.5.0" in login_text)

    # ── 2. 无边框窗口 + Web 自绘标题栏 ──
    flags = int(shell.windowFlags())
    check("Qt 原生外框已移除", bool(flags & 0x00000800), f"windowFlags={flags}")

    bar_raw = run_js(
        page,
        "JSON.stringify(Array.prototype.slice.call("
        "document.querySelectorAll('.win-btn')).map(function(el){"
        "  var r=el.getBoundingClientRect(); var svg=el.querySelector('svg');"
        "  return {w:Math.round(r.width),h:Math.round(r.height),"
        "          x:Math.round(r.x),svg:svg?Math.round(svg.getBoundingClientRect().width):0};"
        "}))",
    )
    bars = json.loads(bar_raw) if bar_raw else []
    check(
        "Web 自绘标题栏的三个窗口按钮真实可见且有尺寸",
        len(bars) == 3 and all(b["w"] > 0 and b["h"] > 0 and b["svg"] > 0 for b in bars),
        json.dumps(bars, ensure_ascii=False),
    )
    check(
        "标题栏只显示当前页面名（登录页为「登录」，不再重复品牌）",
        run_js(page, "window.__JF_TEST__.titlebarText()") == "登录",
        f"标题栏文字={run_js(page, 'window.__JF_TEST__.titlebarText()')!r}",
    )
    login_logo = json.loads(run_js(page, "JSON.stringify(window.__JF_TEST__.images())") or "[]")
    check(
        "登录卡使用仓库 logo（图片已在 file:// 下成功解码）",
        any(
            str(i.get("src", "")).startswith("./logo-") and i.get("natural") not in ("0x0", "")
            for i in login_logo
        ),
        json.dumps(login_logo, ensure_ascii=False),
    )

    # ── 3. 连接服务端（真实 ECDH） ──
    server_url = "http://127.0.0.1:8989"
    run_js(
        page,
        "(function(){var el=window.__JF_TEST__.inputByPlaceholder('http://');"
        f"return el ? window.__JF_TEST__.setInput(el, {json.dumps(server_url)}) : false;}})()",
    )
    clicked = run_js(
        page,
        "(function(){var b=window.__JF_TEST__.buttonByText('连接服务端');"
        "if(!b) return false; b.click(); return true;})()",
    )
    check("点击「连接服务端」", clicked is True)

    reached_login = wait_until(page, "window.__JF_TEST__.bodyText().indexOf('登录有效期') >= 0")
    check(
        "密钥交换 + admin-exists 成功，进入登录步骤",
        reached_login,
        (run_js(page, "window.__JF_TEST__.bodyText()") or "").strip().splitlines()[:6],
    )
    check(
        "连接后地址历史已记录（Python 侧落盘，护栏内为临时文件）",
        any("127.0.0.1:8989" in str(u) for u in _history_snapshot()),
        str(_history_snapshot()),
    )

    # ── 4. 登录并跳转业务面板 ──
    run_js(
        page,
        "(function(){var u=window.__JF_TEST__.inputByLabel('用户名');"
        f"return u ? window.__JF_TEST__.setInput(u, {json.dumps(cred['username'])}) : false;}})()",
    )
    run_js(
        page,
        "(function(){var p=window.__JF_TEST__.inputByLabel('密码');"
        f"return p ? window.__JF_TEST__.setInput(p, {json.dumps(cred['password'])}) : false;}})()",
    )
    login_clicked = run_js(
        page,
        "(function(){var b=window.__JF_TEST__.buttonByText('登录');"
        "if(!b) return false; b.click(); return true;})()",
    )
    check("点击「登录」", login_clicked is True)

    # 诊断：登录点击后立刻看一次现场（失败时最能说明问题）
    time.sleep(1.5)
    diag = run_js(
        page,
        "JSON.stringify({"
        "  hash: location.hash,"
        "  body: (window.__JF_TEST__.bodyText()||'').replace(/\\s+/g,' ').slice(0,260),"
        "  inputs: Array.prototype.slice.call(document.querySelectorAll('input'))"
        "    .map(function(e){return e.type + '=' + e.value;}),"
        "  btnText: (window.__JF_TEST__.buttonByText('登录')||{}).textContent || null"
        "})",
    )
    info(f"登录后现场：{diag}")
    from src.utils.session import session_manager as _sm

    info(
        f"Python 侧会话：logged_in={_sm.is_logged_in()} user={_sm.username!r} "
        f"has_token={bool(_sm.token)} session_ready={_sm.is_session_ready()}"
    )

    jumped = wait_until(page, "window.__JF_TEST__.hash().indexOf('/files') >= 0")
    check(
        "登录成功并跳转到业务面板（#/files）",
        jumped,
        f"hash={run_js(page, 'window.__JF_TEST__.hash()')} · "
        f"错误提示={((run_js(page, 'window.__JF_TEST__.bodyText()') or '').strip()[:80])}",
    )

    nav = run_js(page, "JSON.stringify(window.__JF_TEST__.sidebarNav())") or "[]"
    nav_items = json.loads(nav)
    check(
        "左侧菜单呈现（主导航 4 项 + 底部 2 项）",
        all(x in nav_items for x in ["文件管理", "笔记管理", "同步管理", "传输任务", "安全状态", "设置"]),
        json.dumps(nav_items, ensure_ascii=False),
    )

    shell_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check("侧栏用户卡显示登录用户名", cred["username"] in shell_text)
    check(
        "标题栏切换为当前菜单名（文件管理）",
        run_js(page, "window.__JF_TEST__.titlebarText()") == "文件管理",
        f"标题栏文字={run_js(page, 'window.__JF_TEST__.titlebarText()')!r}",
    )
    shell_logo = json.loads(run_js(page, "JSON.stringify(window.__JF_TEST__.images())") or "[]")
    check(
        "侧栏品牌使用仓库 logo（图片已成功解码且有尺寸）",
        any(
            str(i.get("src", "")).startswith("./logo-")
            and i.get("natural") not in ("0x0", "")
            and i.get("rendered") not in ("0px", "")
            for i in shell_logo
        ),
        json.dumps(shell_logo, ensure_ascii=False),
    )
    # N7 交付后 /files 已是**真实页面**（不再是占位页）
    check(
        "业务面板显示文件管理页（真实磁盘列表，非占位）",
        "选择磁盘开始浏览" in shell_text and "尚未接入" not in shell_text,
        shell_text.strip().replace("\n", " ")[:140],
    )

    info(f"业务面板截图：{save_shot(shell, 'shell_after_login.png', page)}")

    # ── 8. 设置页（N12） ──────────────────────────
    run_js(page, "location.hash = '#/settings'")
    reached = wait_until(page, "window.__JF_TEST__.bodyText().indexOf('笔记目录') >= 0")
    check("进入设置页", reached)

    st = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check(
        "设置页渲染出全部区块（服务端 / 笔记目录 / 账号 / 外观 / 关于）",
        all(k in st for k in ["服务端", "笔记目录", "账号", "外观", "关于"]),
        st.strip().replace("\n", " ")[:160],
    )
    check(
        "标题栏随路由切到「设置」",
        run_js(page, "window.__JF_TEST__.titlebarText()") == "设置",
        f"标题栏文字={run_js(page, 'window.__JF_TEST__.titlebarText()')!r}",
    )
    server_input_value = run_js(page, "window.__JF_TEST__.inputValueStartingWith('http')")
    check(
        "服务端区块的输入框预填当前地址（来自已建立的会话）",
        server_input_value == server_url,
        f"输入框值={server_input_value!r}",
    )
    check(
        "账号区块显示用户名与角色",
        cred["username"] in st and "管理员" in st,
    )
    check("账号区块显示登录凭证过期时间与剩余时长", "登录凭证过期时间" in st and "剩余" in st)
    check("笔记目录区块显示「未配置」（临时账号未配）", "未配置" in st)
    check("关于区块显示版本与加密方案",
          "v1.5.0" in st and "X25519 ECDH + ChaCha20-Poly1305" in st)

    # 截图要放在「退出登录」之前 —— 否则拍到的是登出后的登录页
    info(f"设置页截图：{save_shot(shell, 'settings_page.png', page)}")

    # ── 自绘服务端地址下拉（替换原生 datalist，用户反馈"有点原始"） ──
    datalist_count = run_js(page, "document.querySelectorAll('datalist').length")
    check(
        "页面里已无原生 datalist",
        datalist_count == 0,
        f"datalist 数量={datalist_count}",
    )
    check(
        "聚焦服务端地址输入框",
        run_js(page, "window.__JF_TEST__.focusInputStartingWith('http')") is True,
    )
    # 等下拉真正渲染出来（原先固定 sleep 会抢跑：面板未渲染就读圆角/阴影 → None）
    dd_ready = wait_until(
        page,
        "(function(){var d=JSON.parse("
        "window.__JF_TEST__.dropdownInfo()||'null');"
        "return !!(d && d.visible && d.count >= 1);})()",
        timeout_s=10,
    )
    dd_raw = run_js(page, "window.__JF_TEST__.dropdownInfo()")
    dd = json.loads(dd_raw) if dd_raw else None
    check("展开自绘下拉并列出历史地址", dd_ready, json.dumps(dd, ensure_ascii=False))
    has_tokens = bool(
        dd
        and dd.get("borderRadius") not in ("0px", "")
        and "rgb" in str(dd.get("boxShadow"))
    )
    check(
        "下拉面板使用设计令牌（圆角 + --e3 阴影）",
        has_tokens,
        f"圆角={dd.get('borderRadius') if dd else None} 阴影={dd.get('boxShadow') if dd else None}",
    )
    info(f"下拉展开截图：{save_shot(shell, 'settings_dropdown.png', page)}")

    run_js(page, "window.__JF_TEST__.pressKey('ArrowDown')")
    # 等高亮真的生效（同样避免抢跑）
    wait_until(
        page,
        "(function(){var d=JSON.parse("
        "window.__JF_TEST__.dropdownInfo()||'null');"
        "return !!(d && typeof d.selected === 'number' && d.selected >= 0);})()",
        timeout_s=8,
    )
    dd2_raw = run_js(page, "window.__JF_TEST__.dropdownInfo()")
    dd2 = json.loads(dd2_raw) if dd2_raw else None
    check("↓ 键把第一项置为高亮", bool(dd2 and dd2.get("selected") == 0),
          json.dumps(dd2, ensure_ascii=False))

    first_url = (dd2 or {}).get("texts", [""])[0]
    if first_url:
        clicked_opt = run_js(
            page, f"window.__JF_TEST__.clickOptionContaining({json.dumps(first_url)})"
        )
        time.sleep(0.3)
        check(f"点击下拉项「{first_url}」回填输入框", clicked_opt is True)
        after_value = run_js(page, "window.__JF_TEST__.inputValueStartingWith('http')")
        after_panel = run_js(page, "window.__JF_TEST__.dropdownInfo()")
        check(
            "选择后输入框值为该项且面板已收起",
            # 注意：JS 的 null 经 runJavaScript 回传会被序列化成空串（不是 None）
            after_value == first_url and not after_panel,
            f"输入框={after_value!r} 面板={after_panel!r}",
        )

    # 主题三态（写入 <html> 的 class，持久化到 localStorage）
    # 先记住用户当前偏好，测试结束时还原（否则会把用户选的暗色改掉）
    original_theme_mode = run_js(page, "window.__JF_TEST__.themeMode()") or "system"
    info(f"测试前主题偏好 = {original_theme_mode!r}（结束时还原）")
    run_js(page, "window.__JF_TEST__.clickButtonText('暗色')")
    time.sleep(0.3)
    check("主题切到暗色（<html> 带 dark 类）", run_js(page, "window.__JF_TEST__.isDark()") is True)
    run_js(page, "window.__JF_TEST__.clickButtonText('亮色')")
    time.sleep(0.3)
    check("主题切回亮色", run_js(page, "window.__JF_TEST__.isDark()") is False)
    run_js(page, "window.__JF_TEST__.clickButtonText('跟随系统')")
    time.sleep(0.2)

    # 笔记目录 → 浏览选择（走桥读真实磁盘与目录）
    run_js(page, "window.__JF_TEST__.clickButtonText('浏览选择')")
    disk_dialog = wait_until(
        page, "window.__JF_TEST__.bodyText().indexOf('选择磁盘') >= 0"
    )
    check("「浏览选择」打开磁盘选择弹窗", disk_dialog)

    modal_btns_raw = run_js(page, "JSON.stringify(window.__JF_TEST__.modalButtonTexts())") or "[]"
    info(f"弹窗内可见按钮：{modal_btns_raw}")

    # 磁盘名从 Python 侧取（测试进程与桥共享同一会话），避免靠"猜按钮文字"
    # —— 上一版用 `.card button[0]` 误点了设置页卡片里的「保存并重新连接」，
    # 导致换了服务端地址、直接登出跳回登录页，后续断言连挂。
    from src.services import disk_service as _disk_service

    disk_rows = _disk_service.list_disks()
    disk_names = [
        str(row.get("name")) for row in disk_rows if isinstance(row, dict) and row.get("name")
    ]
    check("桥读到真实磁盘列表（disks.all）", len(disk_names) >= 1, str(disk_names))

    if disk_names:
        clicked_disk = run_js(
            page, f"window.__JF_TEST__.clickButtonText({json.dumps(disk_names[0])})"
        )
        check(f"点击磁盘「{disk_names[0]}」", clicked_disk is True)
        tree = wait_until(page, "window.__JF_TEST__.bodyText().indexOf('选择目录') >= 0")
        tree_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
        check("打开目录树（DirTreeModal）", tree)
        check(
            "目录树显示当前路径与「选择此目录」按钮",
            "路径" in tree_text and "选择此目录" in tree_text,
            tree_text.strip().replace("\n", " ")[-140:],
        )
        # 只验证导航与读接口，不落盘：点「取消」关闭
        run_js(page, "window.__JF_TEST__.clickButtonText('取消')")
        time.sleep(0.4)
        check(
            "取消后回到设置页（未写任何配置）",
            "笔记目录" in (run_js(page, "window.__JF_TEST__.bodyText()") or ""),
        )

    # ── 9. 安全状态（N11） ────────────────────────
    run_js(page, "location.hash = '#/security'")
    reached_sec = wait_until(page, "window.__JF_TEST__.bodyText().indexOf('会话状态') >= 0")
    check("进入安全状态页", reached_sec)

    sec = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check(
        "安全状态页渲染全部状态行",
        all(
            k in sec
            for k in ["会话状态", "Session ID", "密钥交换时间", "当前用户", "加密算法", "前向保密"]
        ),
        sec.strip().replace("\n", " ")[:170],
    )
    check("加密状态徽标为「已加密（ChaCha20-Poly1305）」", "已加密（ChaCha20-Poly1305）" in sec)
    check("当前用户显示登录账号", cred["username"] in sec)
    check("加密算法/前向保密为固定文案",
          "X25519 ECDH + HKDF-SHA256" in sec and "私钥用完即销毁" in sec)

    sid = _sm.session_id
    check(
        "Session ID 展示的是 Python 侧真实会话 ID（前 8 位一致）",
        bool(sid) and sid[:8] in sec,
        f"Python session_id={sid[:8]}… 页面包含={sid[:8] in sec}",
    )
    check("密钥交换时间已展示（本地时间 + 持续时长）", "持续" in sec)
    check(
        "侧栏激活态唯一且为「安全状态」（不串到相邻的「设置」）",
        json.loads(run_js(page, "window.__JF_TEST__.activeNavItems()") or "[]") == ["安全状态"],
        f"激活项={run_js(page, 'window.__JF_TEST__.activeNavItems()')}",
    )

    info(f"安全状态页截图：{save_shot(shell, 'security_page.png', page)}")

    # 刷新会话密钥：应真的重新做一次 ECDH（session_id 变化），而不是只弹个提示
    before_sid = _sm.session_id
    clicked_refresh = run_js(page, "window.__JF_TEST__.clickButtonText('刷新会话密钥')")
    check("点击「刷新会话密钥」", clicked_refresh is True)

    deadline = time.time() + 20
    while time.time() < deadline and _sm.session_id == before_sid:
        time.sleep(0.2)
    check(
        "刷新后 Python 侧确实建立了新会话（session_id 已变化）",
        bool(_sm.session_id) and _sm.session_id != before_sid,
        f"{before_sid[:8]}… → {_sm.session_id[:8]}…",
    )

    # 刷新是异步的：响应回到 JS 后组件才重渲染，因此用轮询而不是固定 sleep
    new_prefix = _sm.session_id[:8]
    sid_shown = wait_until(
        page, f"window.__JF_TEST__.bodyText().indexOf({json.dumps(new_prefix)}) >= 0",
        timeout_s=10,
    )
    check(
        "页面同步显示刷新后的新 Session ID",
        sid_shown,
        f"新前缀={new_prefix} 页面尾部="
        + (run_js(page, "window.__JF_TEST__.bodyText()") or "").strip().replace("\n", " ")[-110:],
    )
    toast_shown = wait_until(
        page, "window.__JF_TEST__.bodyText().indexOf('会话密钥已刷新') >= 0", timeout_s=10
    )
    check("刷新后弹出成功提示", toast_shown)

    # ── 10. 管理 · 系统设置（N16） ─────────────────
    from src.services import config_service as _config_service

    KEY_TRANSCODE = "media_repair_allow_transcode"
    KEY_CONCURRENT = "media_repair_max_concurrent"

    def server_config() -> dict:
        data = _config_service.get_all_config()
        return data if isinstance(data, dict) else {}

    original_cfg = server_config()
    info(f"测试前服务端 config = {json.dumps(original_cfg, ensure_ascii=False)}")

    run_js(page, "location.hash = '#/admin/system'")
    reached_sys = wait_until(page, "window.__JF_TEST__.bodyText().indexOf('离线媒体修复') >= 0")
    check("进入系统设置页（在 /admin 子路由下，继承 RequireAdmin 守卫）", reached_sys)

    sys_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check(
        "系统设置页渲染媒体修复配置项",
        all(k in sys_text for k in ["离线媒体修复", "允许重编码降级", "修复并发数", "保存"]),
        sys_text.strip().replace("\n", " ")[:150],
    )
    check(
        "标题栏显示「系统设置」",
        run_js(page, "window.__JF_TEST__.titlebarText()") == "系统设置",
    )
    check(
        "侧栏激活态为「系统设置」",
        json.loads(run_js(page, "window.__JF_TEST__.activeNavItems()") or "[]") == ["系统设置"],
        f"激活项={run_js(page, 'window.__JF_TEST__.activeNavItems()')}",
    )

    # 开关初值必须与服务端一致（不是写死的假状态）
    expect_switch = original_cfg.get(KEY_TRANSCODE) == "1"
    actual_switch = run_js(page, "window.__JF_TEST__.switchState()")
    check(
        "开关初值来自服务端真实配置",
        actual_switch is expect_switch,
        f"UI={actual_switch} 服务端 {KEY_TRANSCODE}={original_cfg.get(KEY_TRANSCODE)!r}",
    )

    # 切换开关 → 服务端配置应真的改变
    check("点击「允许重编码降级」开关", run_js(page, "window.__JF_TEST__.toggleSwitch()") is True)
    deadline = time.time() + 15
    while time.time() < deadline and server_config().get(KEY_TRANSCODE) == original_cfg.get(
        KEY_TRANSCODE
    ):
        time.sleep(0.2)
    after_toggle = server_config()
    check(
        "切换开关后服务端配置真的被改写",
        after_toggle.get(KEY_TRANSCODE) != original_cfg.get(KEY_TRANSCODE),
        f"{original_cfg.get(KEY_TRANSCODE)!r} → {after_toggle.get(KEY_TRANSCODE)!r}",
    )
    check(
        "保存成功弹出提示",
        wait_until(page, "window.__JF_TEST__.bodyText().indexOf('配置已保存') >= 0", timeout_s=8),
    )

    # 非法并发数应被前端拦下、且不写服务端
    # 注意：不能用「键是否存在」判断 —— 上一次跑测试的还原会留下空串键，
    # 因此改为断言**值未被这次操作改变**。
    concurrent_before = server_config().get(KEY_CONCURRENT)
    run_js(page, "window.__JF_TEST__.setNumberInput('99')")
    run_js(page, "window.__JF_TEST__.clickButtonText('保存')")
    check(
        "非法并发数（99）被拦下并提示",
        wait_until(page, "window.__JF_TEST__.bodyText().indexOf('并发数不合法') >= 0", timeout_s=8),
    )
    check(
        "非法值没有被写入服务端（值保持不变）",
        server_config().get(KEY_CONCURRENT) == concurrent_before,
        f"{concurrent_before!r} → {server_config().get(KEY_CONCURRENT)!r}",
    )

    # 合法并发数 → 真的写入服务端
    run_js(page, "window.__JF_TEST__.setNumberInput('3')")
    run_js(page, "window.__JF_TEST__.clickButtonText('保存')")
    deadline = time.time() + 15
    while time.time() < deadline and server_config().get(KEY_CONCURRENT) != "3":
        time.sleep(0.2)
    check(
        "合法并发数已写入服务端",
        server_config().get(KEY_CONCURRENT) == "3",
        f"{KEY_CONCURRENT}={server_config().get(KEY_CONCURRENT)!r}",
    )

    info(f"系统设置页截图：{save_shot(shell, 'admin_system_page.png', page)}")

    # ── 环境还原：把服务端配置改回测试前的值 ────────
    _config_service.update_config(KEY_TRANSCODE, original_cfg.get(KEY_TRANSCODE, "0"))
    _config_service.update_config(KEY_CONCURRENT, "")
    time.sleep(0.5)
    restored = server_config()
    check(
        f"服务端 {KEY_TRANSCODE} 已还原",
        restored.get(KEY_TRANSCODE) == original_cfg.get(KEY_TRANSCODE),
        f"{restored.get(KEY_TRANSCODE)!r}",
    )
    info(
        f"还原后服务端 config = {json.dumps(restored, ensure_ascii=False)}"
    )
    # 服务端没有删键接口：若该键原本不存在，用 sqlite 物理清理，做到零残留
    if KEY_CONCURRENT not in original_cfg:
        removed_rows = hard_delete_config_key(KEY_CONCURRENT)
        info(f"清理测试新建的配置键 {KEY_CONCURRENT}（删除 {removed_rows} 行）")
    check(
        "服务端配置已完全还原（含测试新建的键）",
        server_config() == original_cfg,
        f"{json.dumps(original_cfg, ensure_ascii=False)} → "
        f"{json.dumps(server_config(), ensure_ascii=False)}",
    )

    # ── 11. 管理 · 用户管理（N13） ─────────────────
    from src.services import permission_service as _permission_service
    from src.services import user_service as _user_service

    PROBE_USER = "poc_ui_probe"
    PROBE_PWD = "PocUiProbe@2026"

    def usernames() -> list[str]:
        return [str(u.get("username")) for u in _user_service.list_users()]

    # 先把上一次可能残留的探针账号清掉，保证初始状态可控
    leftover_users, leftover_perms = hard_delete_probe_user(PROBE_USER)
    if leftover_users or leftover_perms:
        info(f"清理上次残留：用户 {leftover_users} 行 / 权限 {leftover_perms} 行")

    run_js(page, "location.hash = '#/admin/users'")
    check(
        "进入用户管理页",
        wait_until(page, "window.__JF_TEST__.pageHeaderText().indexOf('用户管理') >= 0"),
    )
    # 等列表真正加载完：骨架屏消失（页头文字是立刻出现的）
    check(
        "列表加载完成（骨架屏消失）",
        wait_until(page, "document.querySelectorAll('.skeleton').length === 0", timeout_s=15),
    )
    users_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check("用户管理页渲染标题与添加入口", "用户管理" in users_text and "添加用户" in users_text)
    check(
        "侧栏激活态为「用户管理」",
        json.loads(run_js(page, "window.__JF_TEST__.activeNavItems()") or "[]") == ["用户管理"],
    )
    # 该页只列**普通用户**（源码：users.filter(u => u.role !== 'admin')）。
    # 注意：库里可能已有其它普通用户（用户手工测试留下的），所以这里按实际数据断言。
    expected_regular = [
        str(u.get("username"))
        for u in _user_service.list_users()
        if u.get("role") != "admin" and u.get("username") != PROBE_USER
    ]
    if expected_regular:
        check(
            "列出库里已有的普通用户",
            all(name in users_text for name in expected_regular),
            f"期望={expected_regular}",
        )
    else:
        check("无普通用户时显示空态", "暂无普通用户" in users_text)
    check("表中不出现管理员账号（按设计过滤）", "admin" not in users_text)

    # 建一个普通用户（权限配置页只列非管理员，后续测试需要它）
    run_js(page, "window.__JF_TEST__.clickButtonText('添加用户')")
    check("打开添加用户弹窗", wait_until(page, "window.__JF_TEST__.modalOpen()"))
    check(
        "弹窗渲染用户名与密码输入框",
        run_js(page, "window.__JF_TEST__.modalInputCount()") == 2,
        f"输入框数={run_js(page, 'window.__JF_TEST__.modalInputCount()')}",
    )

    # 空值不应提交（组件内 `if (!createUsername.trim() || !createPassword) return;`）
    run_js(page, "window.__JF_TEST__.clickModalButton('创建')")
    time.sleep(0.4)
    check(
        "空用户名不提交：弹窗保持打开且服务端无新增",
        run_js(page, "window.__JF_TEST__.modalOpen()") is True and PROBE_USER not in usernames(),
    )

    run_js(
        page,
        f"window.__JF_TEST__.setInputByPlaceholder('用户名', {json.dumps(PROBE_USER)})",
    )
    run_js(
        page,
        f"window.__JF_TEST__.setInputByPlaceholder('密码', {json.dumps(PROBE_PWD)})",
    )
    run_js(page, "window.__JF_TEST__.clickModalButton('创建')")
    deadline = time.time() + 15
    while time.time() < deadline and PROBE_USER not in usernames():
        time.sleep(0.2)
    check("通过界面创建用户 → 服务端真的新增该账号", PROBE_USER in usernames(), str(usernames()))
    check(
        "创建成功弹出提示",
        wait_until(page, "window.__JF_TEST__.bodyText().indexOf('用户已创建') >= 0", timeout_s=8),
    )
    check(
        "列表刷新后出现新用户",
        wait_until(
            page, f"window.__JF_TEST__.bodyText().indexOf({json.dumps(PROBE_USER)}) >= 0",
            timeout_s=8,
        ),
    )

    # 重复创建 → 服务端拒绝、弹窗保持打开（错误路径，且不产生额外数据）
    run_js(page, "window.__JF_TEST__.clickButtonText('添加用户')")
    wait_until(page, "window.__JF_TEST__.modalOpen()")
    run_js(
        page,
        f"window.__JF_TEST__.setInputByPlaceholder('用户名', {json.dumps(PROBE_USER)})",
    )
    run_js(
        page,
        f"window.__JF_TEST__.setInputByPlaceholder('密码', {json.dumps(PROBE_PWD)})",
    )
    run_js(page, "window.__JF_TEST__.clickModalButton('创建')")
    check(
        "重复用户名被服务端拒绝并提示",
        wait_until(page, "window.__JF_TEST__.bodyText().indexOf('添加用户失败') >= 0", timeout_s=8),
    )
    check(
        "失败时弹窗保持打开（可改名重试）",
        run_js(page, "window.__JF_TEST__.modalOpen()") is True,
    )
    run_js(page, "window.__JF_TEST__.clickModalButton('取消')")
    time.sleep(0.4)
    check("取消后弹窗关闭", run_js(page, "window.__JF_TEST__.modalOpen()") is False)

    info(f"用户管理页截图：{save_shot(shell, 'admin_users_page.png', page)}")

    # ── 12. 管理 · 权限配置（N15） ─────────────────
    probe_id = next(
        (int(u["id"]) for u in _user_service.list_users() if u.get("username") == PROBE_USER), None
    )
    check("从服务端取到探针账号 id", probe_id is not None, f"id={probe_id}")

    run_js(page, "location.hash = '#/admin/permissions'")
    check(
        "进入权限配置页",
        wait_until(page, "window.__JF_TEST__.pageHeaderText().indexOf('权限配置') >= 0"),
    )
    check(
        "左侧列出非管理员用户（含刚建的探针账号）",
        wait_until(
            page, f"window.__JF_TEST__.hasButtonText({json.dumps(PROBE_USER)})", timeout_s=15
        ),
    )

    clicked_probe = run_js(
        page, f"window.__JF_TEST__.clickButtonText({json.dumps(PROBE_USER)})"
    )
    check("点击左侧探针用户", clicked_probe is True, f"返回={clicked_probe}")
    time.sleep(1.2)
    info(f"左侧用户按钮状态：{run_js(page, 'window.__JF_TEST__.markedButtons()')}")
    check(
        "选中后右侧渲染读/写/删权限开关",
        wait_until(page, "window.__JF_TEST__.permissionToggleStates() !== '[]'", timeout_s=10),
        f"开关={run_js(page, 'window.__JF_TEST__.permissionToggleStates()')}",
    )
    states = json.loads(run_js(page, "window.__JF_TEST__.permissionToggleStates()") or "[]")
    check("权限开关数量为「磁盘数 × 3」", len(states) >= 3, json.dumps(states, ensure_ascii=False))

    # 授予「读」→ 服务端应真的写入
    run_js(page, "window.__JF_TEST__.clickPermissionToggle('读权限')")
    deadline = time.time() + 15
    while time.time() < deadline and not any(
        p.get("can_read") for p in (_permission_service.get_user_disk_permissions(probe_id) or [])
    ):
        time.sleep(0.2)
    perms = _permission_service.get_user_disk_permissions(probe_id) or []
    check(
        "点击「读」开关后服务端真的写入该权限",
        any(p.get("can_read") for p in perms),
        json.dumps(perms, ensure_ascii=False),
    )
    check(
        "页面开关同步为已勾选",
        wait_until(
            page,
            "window.__JF_TEST__.permissionToggleStates().indexOf('\\\"on\\\":true') >= 0",
            timeout_s=8,
        ),
    )

    # 取消「读」→ 三项全空 → 服务端删除该权限记录（还原）
    run_js(page, "window.__JF_TEST__.clickPermissionToggle('读权限')")
    deadline = time.time() + 15
    while time.time() < deadline:
        if not (_permission_service.get_user_disk_permissions(probe_id) or []):
            break
        time.sleep(0.2)
    remaining_perms = _permission_service.get_user_disk_permissions(probe_id) or []
    check(
        "三项全空后服务端删除该权限记录（已还原）",
        not remaining_perms,
        json.dumps(remaining_perms, ensure_ascii=False),
    )

    info(f"权限配置页截图：{save_shot(shell, 'admin_permissions_page.png', page)}")

    # ── 13. 管理 · 磁盘管理（N14，只读验证） ────────
    from src.services import disk_service as _disk_service_admin

    run_js(page, "location.hash = '#/admin/disks'")
    check(
        "进入磁盘管理页",
        wait_until(page, "window.__JF_TEST__.pageHeaderText().indexOf('磁盘管理') >= 0"),
    )
    disk_names = [
        str(d.get("name"))
        for d in _disk_service_admin.list_disks()
        if isinstance(d, dict) and d.get("name")
    ]
    # 等表格加载完（页头立刻出现，表格在骨架屏阶段）
    if disk_names:
        check(
            "磁盘表格加载完成",
            wait_until(
                page,
                f"window.__JF_TEST__.bodyText().indexOf({json.dumps(disk_names[0])}) >= 0",
                timeout_s=15,
            ),
        )
    disks_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check(
        "磁盘列表与服务端一致",
        bool(disk_names) and all(n in disks_text for n in disk_names),
        f"服务端={disk_names}",
    )

    # 只验证创建弹窗渲染，**不提交** —— 建磁盘会让服务端触碰真实磁盘路径
    run_js(page, "window.__JF_TEST__.clickButtonText('添加磁盘')")
    check("打开添加磁盘弹窗", wait_until(page, "window.__JF_TEST__.modalOpen()"))
    check(
        "弹窗渲染磁盘名称与磁盘路径字段",
        run_js(page, "window.__JF_TEST__.modalInputCount()") == 2,
        f"输入框数={run_js(page, 'window.__JF_TEST__.modalInputCount()')}",
    )
    run_js(page, "window.__JF_TEST__.clickModalButton('取消')")
    time.sleep(0.3)
    check("取消后弹窗关闭（未创建任何磁盘）", run_js(page, "window.__JF_TEST__.modalOpen()") is False)

    if disk_names:
        # 等表格行渲染出来（页面身份已由 page-header 确认）
        wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(disk_names[0])}) >= 0",
            timeout_s=15,
        )
        time.sleep(0.3)
        disk_row_btns = json.loads(
            run_js(
                page,
                f"window.__JF_TEST__.tableRowButtons({json.dumps(disk_names[0])})",
            )
            or "[]"
        )
        check(
            "磁盘行提供「编辑 / 删除」操作（PC 表格为内联按钮）",
            any("编辑" in b for b in disk_row_btns) and any("删除" in b for b in disk_row_btns),
            str(disk_row_btns),
        )

    info(f"磁盘管理页截图：{save_shot(shell, 'admin_disks_page.png', page)}")

    # ── 14. 回收：删掉探针账号（N13 的删除路径） ────
    run_js(page, "location.hash = '#/admin/users'")
    wait_until(page, "window.__JF_TEST__.pageHeaderText().indexOf('用户管理') >= 0")
    # 等探针行加载出来再点（表格是异步加载的；页面身份已由 page-header 确认）
    check(
        "列表已加载出探针账号行",
        wait_until(
            page, f"window.__JF_TEST__.bodyText().indexOf({json.dumps(PROBE_USER)}) >= 0",
            timeout_s=15,
        ),
    )
    wait_until(
        page,
        "document.querySelectorAll('button[aria-label=\"操作菜单\"]').length > 0",
        timeout_s=10,
    )
    time.sleep(0.3)
    user_row_btns = json.loads(
        run_js(page, f"window.__JF_TEST__.tableRowButtons({json.dumps(PROBE_USER)})") or "[]"
    )
    check(
        "用户行提供「改密 / 删除」操作（PC 表格为内联按钮）",
        any("改密" in b for b in user_row_btns) and any("删除" in b for b in user_row_btns),
        str(user_row_btns),
    )
    check(
        "点击行内「删除」",
        run_js(page, f"window.__JF_TEST__.clickTableButton({json.dumps(PROBE_USER)}, '删除')")
        is True,
    )
    check(
        "弹出删除确认框",
        wait_until(page, "window.__JF_TEST__.bodyText().indexOf('确定要删除用户') >= 0", timeout_s=8),
    )
    run_js(page, "window.__JF_TEST__.clickModalButton('删除')")
    deadline = time.time() + 15
    while time.time() < deadline and PROBE_USER in usernames():
        time.sleep(0.2)
    check("删除后服务端列表不再包含该账号", PROBE_USER not in usernames(), str(usernames()))

    # 服务端删除是软删除（写 deleted_at），测试要零残留 → sqlite 物理清理
    removed_users, removed_perms = hard_delete_probe_user(PROBE_USER)
    check(
        "探针账号及其权限行已物理清理（零残留）",
        removed_users == 1,
        f"删除用户 {removed_users} 行 / 权限 {removed_perms} 行",
    )

    # ── 17. N8：笔记管理 ──────────────────────────
    import shutil

    from src.services import disk_service as _disk_svc
    from src.services import note_service as _note_svc

    NOTES_SUBDIR = "poc-notes-tmp"
    PROBE_NOTE = "poc_probe_note"
    PROBE_NOTE_FILE = f"{PROBE_NOTE}.md"
    MD_CONTENT = (
        "# 探针笔记\n\n这是回归测试写入的内容。\n\n"
        "```mermaid\ngraph LR\n  A[开始] --> B{加密}\n  B --> C[完成]\n```\n"
    )

    disks_for_notes = _disk_svc.list_disks()
    disk_row = disks_for_notes[0] if disks_for_notes else None
    check("取到磁盘（用于挂载测试笔记目录）", bool(disk_row), str(disks_for_notes))
    disk_id = int(disk_row["id"]) if disk_row else 0
    disk_real_path = str(disk_row.get("real_path") or "") if disk_row else ""
    notes_abs = os.path.join(disk_real_path, NOTES_SUBDIR)
    if disk_real_path:
        os.makedirs(notes_abs, exist_ok=True)
    info(f"测试用笔记目录（隔离子目录）：{notes_abs}")

    # 只给**临时账号**挂这个隔离目录；账号在测试结束即删除，不影响任何真实用户
    if disk_id:
        _note_svc.set_notes_disk(disk_id, NOTES_SUBDIR)

    run_js(page, "location.hash = '#/notes'")
    check(
        "进入笔记管理页",
        wait_until(page, "window.__JF_TEST__.pageHeaderText().indexOf('笔记管理') >= 0"),
    )
    check(
        "笔记列表加载完成",
        wait_until(page, "document.querySelectorAll('.skeleton').length === 0", timeout_s=15),
    )
    notes_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check("标题栏为「笔记管理」", run_js(page, "window.__JF_TEST__.titlebarText()") == "笔记管理")
    check("空目录显示空态「暂无笔记」", "暂无笔记" in notes_text)

    # 新建笔记
    check("点击「新建」", run_js(page, "window.__JF_TEST__.clickButtonText('新建')") is True)
    check("打开新建笔记弹窗", wait_until(page, "window.__JF_TEST__.modalOpen()"))
    check(
        "填入笔记名称",
        run_js(
            page,
            "window.__JF_TEST__.setInputByPlaceholder('笔记名称', "
            f"{json.dumps(PROBE_NOTE)})",
        )
        is True,
    )
    run_js(page, "window.__JF_TEST__.clickModalButton('创建')")
    check(
        "新建后列表出现该笔记",
        wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(PROBE_NOTE_FILE)}) >= 0",
            timeout_s=15,
        ),
    )
    check("笔记文件已落盘", os.path.exists(os.path.join(notes_abs, PROBE_NOTE_FILE)))

    # 进入编辑器并写入 Markdown + Mermaid
    check(
        "点击笔记行进入编辑器",
        run_js(page, f"window.__JF_TEST__.clickText({json.dumps(PROBE_NOTE_FILE)})") is True,
    )
    check(
        "编辑器打开（PageHeader 显示文件名）",
        wait_until(
            page,
            "window.__JF_TEST__.pageHeaderText().indexOf("
            f"{json.dumps(PROBE_NOTE_FILE)}) >= 0",
            timeout_s=15,
        ),
    )
    check(
        "编辑区已渲染",
        wait_until(page, "document.querySelector('textarea') !== null", timeout_s=10),
    )
    check(
        "填入含 Mermaid 的 Markdown",
        run_js(
            page,
            "window.__JF_TEST__.setInputByPlaceholder('开始编写', "
            f"{json.dumps(MD_CONTENT)})",
        )
        is True,
    )
    time.sleep(0.5)
    check("点击「保存」", run_js(page, "window.__JF_TEST__.clickButtonText('保存')") is True)
    check(
        "保存成功提示",
        wait_until(page, "window.__JF_TEST__.bodyText().indexOf('笔记已保存') >= 0", timeout_s=10),
    )
    on_disk = ""
    probe_path = os.path.join(notes_abs, PROBE_NOTE_FILE)
    if os.path.exists(probe_path):
        with open(probe_path, encoding="utf-8") as fp:
            on_disk = fp.read()
    check(
        "磁盘上的文件内容与编辑内容一致（含 Mermaid 代码块）",
        "```mermaid" in on_disk and "graph LR" in on_disk and "探针笔记" in on_disk,
        on_disk.replace("\n", "⏎")[:110],
    )

    # 预览：Markdown 渲染 + Mermaid 真的画成图
    run_js(page, "window.__JF_TEST__.clickButtonText('预览')")
    check(
        "切到预览模式（Markdown 容器出现）",
        wait_until(page, "document.querySelector('.markdown-body') !== null", timeout_s=10),
    )
    check(
        "Mermaid 图表真的渲染成 SVG（不是源码块）",
        wait_until(page, "document.querySelector('.mmd-canvas svg') !== null", timeout_s=30),
        f"mmd 节点数={run_js(page, 'document.querySelectorAll(\".mmd\").length')}",
    )
    preview_diag = run_js(
        page,
        "JSON.stringify({"
        "  mmd: document.querySelectorAll('.mmd').length,"
        "  canvas: document.querySelectorAll('.mmd-canvas').length,"
        "  svg: document.querySelectorAll('.mmd-canvas svg').length,"
        "  err: document.querySelectorAll('.mmd-error').length,"
        "  mdBody: document.querySelectorAll('.markdown-body').length,"
        "  script: document.querySelectorAll('script[src*=mermaid]').length,"
        "  text: (document.querySelector('.markdown-body') || {innerText: ''})"
        "    .innerText.slice(0, 140).replace(/\\s+/g, ' ')"
        "})",
    )
    info(f"预览 DOM 诊断：{preview_diag}")
    check(
        "未落入错误降级（无 .mmd-error）",
        run_js(page, "document.querySelectorAll('.mmd-error').length") == 0,
        str(preview_diag),
    )
    info(f"笔记预览截图：{save_shot(shell, 'note_preview_page.png', page)}")

    # 回列表并删除探针笔记
    run_js(page, "location.hash = '#/notes'")
    check(
        "回到笔记列表",
        wait_until(page, "window.__JF_TEST__.pageHeaderText().indexOf('笔记管理') >= 0"),
    )
    check(
        "列表已加载出探针笔记",
        wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(PROBE_NOTE_FILE)}) >= 0",
            timeout_s=15,
        ),
    )
    check(
        "点击行内「删除」",
        run_js(
            page,
            "window.__JF_TEST__.clickItemButton("
            f"{json.dumps(PROBE_NOTE_FILE)}, '删除')",
        )
        is True,
    )
    check(
        "弹出删除确认框",
        wait_until(page, "window.__JF_TEST__.bodyText().indexOf('确认删除') >= 0", timeout_s=8),
    )
    run_js(page, "window.__JF_TEST__.clickModalButton('删除')")
    deadline = time.time() + 15
    while time.time() < deadline and os.path.exists(probe_path):
        time.sleep(0.2)
    check("删除后磁盘上的笔记文件已消失", not os.path.exists(probe_path))

    # 回收：删掉隔离目录（临时账号随测试结束一同删除）
    if disk_real_path:
        shutil.rmtree(notes_abs, ignore_errors=True)
    check("测试用笔记目录已清理（零残留）", not os.path.exists(notes_abs), notes_abs)

    # ── 17. N7-a：文件管理（磁盘列表 / 目录浏览 / 新建目录） ──
    from src.services import file_service as _file_svc

    PROBE_DIR = "poc-n7-tmp"
    disk_entries = _file_svc.list_accessible_disks()
    check("服务层读到可访问磁盘", bool(disk_entries), str([d.get("name") for d in disk_entries]))

    run_js(page, "location.hash = '#/files'")
    check(
        "进入文件管理页",
        wait_until(page, "window.__JF_TEST__.pageHeaderText().indexOf('文件管理') >= 0"),
    )
    check(
        "磁盘列表加载完成",
        wait_until(page, "document.querySelectorAll('.skeleton').length === 0", timeout_s=15),
    )
    files_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check(
        "列出全部可访问磁盘",
        all(str(d.get("name")) in files_text for d in disk_entries),
        f"服务端={[d.get('name') for d in disk_entries]}",
    )

    # 进入第一个磁盘
    first_disk = disk_entries[0]
    first_disk_id = int(first_disk["id"])
    check(
        "点击磁盘进入目录浏览",
        run_js(page, f"window.__JF_TEST__.clickText({json.dumps(str(first_disk['name']))})")
        is True,
    )
    check(
        "浏览器页已打开（出现「新建目录」与「上传」）",
        wait_until(
            page,
            "window.__JF_TEST__.bodyText().indexOf('新建目录') >= 0"
            " && window.__JF_TEST__.bodyText().indexOf('上传') >= 0",
            timeout_s=15,
        ),
    )
    check(
        "文件列表加载完成",
        wait_until(page, "document.querySelectorAll('.skeleton').length === 0", timeout_s=15),
    )

    # 与服务端逐项核对根目录内容
    server_files = _file_svc.list_files(first_disk_id, "")
    server_names = [str(f.get("name")) for f in server_files]
    dir_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    missing = [n for n in server_names if n not in dir_text]
    check(
        f"根目录内容与服务端一致（{len(server_names)} 项）",
        not missing,
        f"缺失={missing[:5]}",
    )

    # 新建目录（真写盘；结束后清理）
    # 注意：`files/disks`（面向文件浏览的接口）**不返回 real_path**，
    # 真实路径要从 admin 的 `disk_service.list_disks()` 取，否则会拼出相对路径。
    from src.services import disk_service as _disk_svc_n7

    real_path_of_disk = next(
        (
            str(d.get("real_path") or "")
            for d in _disk_svc_n7.list_disks()
            if int(d.get("id", 0)) == first_disk_id
        ),
        "",
    )
    check("取到磁盘真实路径（用于校验落盘）", bool(real_path_of_disk), real_path_of_disk)
    probe_dir_abs = os.path.join(real_path_of_disk, PROBE_DIR)
    check("点击「新建目录」", run_js(page, "window.__JF_TEST__.clickButtonText('新建目录')") is True)
    check("打开新建目录弹窗", wait_until(page, "window.__JF_TEST__.modalOpen()"))
    run_js(
        page,
        "window.__JF_TEST__.setInputByPlaceholder('目录', "
        f"{json.dumps(PROBE_DIR)})",
    )
    probe_input_ok = run_js(
        page,
        "window.__JF_TEST__.modalInputCount() > 0",
    )
    if probe_input_ok is not True:
        # 弹窗输入框 placeholder 可能不同：退化为直接填第一个输入框
        run_js(
            page,
            "(function(){var i=document.querySelector('.modal input');"
            "if(!i)return false;"
            "var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
            f"s.call(i, {json.dumps(PROBE_DIR)});"
            "i.dispatchEvent(new Event('input',{bubbles:true}));return true;})()",
        )
    run_js(page, "window.__JF_TEST__.clickModalButton('创建')")
    created = _wait_python(lambda: os.path.isdir(probe_dir_abs), timeout_s=15)
    check("新建目录真的写到磁盘", created, probe_dir_abs)
    check(
        "列表刷新后出现新目录",
        wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(PROBE_DIR)}) >= 0",
            timeout_s=10,
        ),
    )

    # 清理：直接用服务层删除（右键菜单路径留待 N7-b 一并覆盖）
    if created:
        _file_svc.delete_file(first_disk_id, PROBE_DIR)
    check("测试目录已清理（零残留）", not os.path.exists(probe_dir_abs))

    # ── N7-b：预览路由必须给出**友好提示**，绝不能落到 react-router 开发错误页 ──
    run_js(page, f"location.hash = '#/files/{first_disk_id}/preview'")
    preview_ok = wait_until(
        page,
        "(window.__JF_TEST__.bodyText().indexOf('没有可预览的文件') >= 0)"
        " || (window.__JF_TEST__.bodyText().indexOf('不支持预览此文件类型') >= 0)"
        " || (window.__JF_TEST__.bodyText().indexOf('由本地解码器渲染') >= 0)",
        timeout_s=15,
    )
    preview_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check("预览路由渲染友好页面（有明确中文提示）", preview_ok, preview_text[:120])
    check(
        "**没有**出现 react-router 开发错误页（用户反馈的问题）",
        "Unexpected Application Error" not in preview_text
        and "Hey developer" not in preview_text,
        preview_text[:120],
    )

    # ── N7-b：视频预览走**本地解码**（StreamProxy + QMediaPlayer） ──
    from src.bridge import media as _media_bridge

    video_name = next((n for n in server_names if n.lower().endswith('.mp4')), None)
    if video_name is None:
        info("磁盘里没有 mp4，跳过视频预览验证")
    else:
        run_js(page, f"location.hash = '#/files/{first_disk_id}'")
        wait_until(page, "window.__JF_TEST__.bodyText().indexOf('新建目录') >= 0", timeout_s=15)
        wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(video_name)}) >= 0",
            timeout_s=15,
        )
        check(
            f"点击视频「{video_name}」进入预览",
            run_js(page, f"window.__JF_TEST__.clickText({json.dumps(video_name)})") is True,
        )
        check(
            "预览页出现原生媒体舞台",
            wait_until(page, "document.querySelector('[data-testid=media-stage]') !== null",
                       timeout_s=15),
        )

        overlay = _media_bridge._overlay
        proxy = _media_bridge._proxy
        check("StreamProxy 已启动（本地解密 + Range）",
              bool(proxy and proxy._port > 0), f"port={getattr(proxy, '_port', None)}")
        check(
            "原生浮层已显示",
            _wait_python(lambda: bool(overlay and overlay.window.isVisible()), timeout_s=10),
            f"visible={overlay.window.isVisible() if overlay else None}",
        )
        check(
            "原生浮层尺寸与舞台一致（>0）",
            bool(overlay and overlay.window.width() > 50 and overlay.window.height() > 50),
            f"{overlay.window.width()}x{overlay.window.height()}" if overlay else "",
        )

        # OS 解码器是否真的认了这个流（LoadedMedia / BufferedMedia）
        loaded = _wait_python_pumping(
            app,
            lambda: overlay is not None
            and overlay.player.mediaStatus().name
            in ("LoadedMedia", "BufferedMedia", "EndOfMedia"),
            timeout_s=90,
        )
        check(
            "QMediaPlayer（OS 解码器）已加载媒体",
            loaded,
            f"status={overlay.player.mediaStatus().name if overlay else None} "
            f"error={overlay.player.errorString() if overlay else None}",
        )
        # 播放位置推进 = 真的在播
        check(
            "播放位置在推进（真的在播放）",
            _wait_python_pumping(
                app,
                lambda: overlay is not None
                and (
                    overlay.player.position() > 0
                    or overlay.player.playbackState()
                    == overlay.player.PlaybackState.PlayingState
                ),
                timeout_s=90,
            ),
            f"position={overlay.player.position() if overlay else None} "
            f"state={overlay.player.playbackState().name if overlay else None}",
        )
        # 音量：经桥驱动原生播放器
        _media_bridge._h_media_set_volume({"volume": 40})
        check(
            "音量经桥作用到原生播放器（40% → 0.4）",
            _wait_python(
                lambda: abs((overlay.audio.volume() if overlay else 0) - 0.4) < 0.02, timeout_s=8
            ),
            f"volume={overlay.audio.volume() if overlay else None}",
        )
        # 全屏：原生窗口铺满屏幕，退出后回到贴合状态
        _media_bridge._h_media_set_fullscreen({"fullscreen": True})
        check(
            "全屏后原生窗口进入全屏",
            _wait_python(
                lambda: bool(overlay and overlay.window.isFullScreen()), timeout_s=8
            ),
        )
        _media_bridge._h_media_set_fullscreen({"fullscreen": False})
        check(
            "退出全屏后重新贴合预留矩形",
            _wait_python(
                lambda: bool(
                    overlay
                    and not overlay.window.isFullScreen()
                    and overlay.window.width() < 1000
                ),
                timeout_s=8,
            ),
            f"{overlay.window.width()}x{overlay.window.height()}" if overlay else "",
        )

        info(f"视频预览截图：{save_shot(shell, 'file_preview_video.png', page)}")

        # ── 用户反馈：预览页点「返回」后文件列表不见了（要再点排序才出现） ──
        # 注意：要回到**目录浏览页**（/files/<id>），不是磁盘列表页（/files）
        run_js(page, f"location.hash = '#/files/{first_disk_id}'")
        wait_until(
            page, "window.__JF_TEST__.pageHeaderText().indexOf('文件管理') >= 0", timeout_s=15
        )
        wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(video_name)}) >= 0",
            timeout_s=15,
        )
        check(
            f"点视频「{video_name}」进入预览（第二次）",
            run_js(page, f"window.__JF_TEST__.clickText({json.dumps(video_name)})") is True,
        )
        back_btn_js = (
            "(function(){var bs=Array.prototype.slice.call("
            "document.querySelectorAll('button[aria-label]'));"
            "return bs.filter(function(x){"
            "return x.getAttribute('aria-label')==='返回';}).length;})()"
        )
        check(
            "预览页已打开（出现返回按钮）",
            wait_until(page, back_btn_js + " > 0", timeout_s=15),
        )
        check(
            "点击「返回」",
            run_js(
                page,
                "(function(){var bs=Array.prototype.slice.call("
                "document.querySelectorAll('button[aria-label]'));"
                "var b=bs.filter(function(x){"
                "return x.getAttribute('aria-label')==='返回';})[0];"
                "if(!b)return false;b.click();return true;})()",
            )
            is True,
        )
        back_ok = wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(video_name)}) >= 0",
            timeout_s=15,
        )
        back_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
        check(
            "返回后文件列表**重新出现**（用户反馈的问题）",
            back_ok,
            f"列表项缺失；页面文字片段={back_text[:150]!r}",
        )
        info(f"返回后截图：{save_shot(shell, 'after_back_from_preview.png', page)}")
        # DOM 断言看不出"没重绘"（DOM 是对的，是**像素**没补画），
        # 因此再抓一张**真实屏幕**区域：用户反馈"返回后列表空白，滚动才出现"。
        try:
            geo = shell.frameGeometry()
            screen = app.primaryScreen()
            shot = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
            out_png = _HERE / "out" / "after_back_screen.png"
            shot.save(str(out_png))
            info(f"返回后整屏像素证据：{out_png}  {shot.width()}x{shot.height()}")
        except Exception as exc:  # noqa: BLE001
            info(f"整屏抓取失败（不影响结论）：{exc}")
        run_js(page, "location.hash = '#/files'")

    # ── 18. N9：同步管理（规则 CRUD；不触发真实同步） ──
    from src.services import sync_service as _sync_svc

    PROBE_SYNC = "poc_n9_rule"
    probe_sync_dir = os.path.join(os.path.dirname(notes_abs), "poc-n9-local")
    os.makedirs(probe_sync_dir, exist_ok=True)
    # 清理可能残留的同名规则
    for cfg in _sync_svc.list_configs():
        if cfg.get("name") == PROBE_SYNC:
            _sync_svc.delete_config(str(cfg["id"]))

    run_js(page, "location.hash = '#/sync'")
    check(
        "进入同步管理页",
        wait_until(page, "window.__JF_TEST__.pageHeaderText().indexOf('同步管理') >= 0"),
    )
    check(
        "同步页加载完成",
        wait_until(page, "document.querySelectorAll('.skeleton').length === 0", timeout_s=15),
    )
    # 等页面真正渲染出内容（刚打开时 body 可能还没画完，直接断言会抖动）
    wait_until(
        page,
        "window.__JF_TEST__.bodyText().length > 120",
        timeout_s=15,
    )
    sync_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    # 规则文件可能残留了别的规则的（护栏内临时文件），因此对"空态 or 已有规则"都放行
    check(
        "同步页渲染出空态或已有规则",
        "还没有同步规则" in sync_text
        or "立即同步" in sync_text
        or PROBE_SYNC in sync_text,
        sync_text[:140],
    )

    check("点击「新建规则」", run_js(page, "window.__JF_TEST__.clickButtonText('新建规则')") is True)
    check("打开规则弹窗", wait_until(page, "window.__JF_TEST__.modalOpen()"))
    check(
        "填入别名",
        run_js(
            page,
            "window.__JF_TEST__.setInputByPlaceholder('例如', " f"{json.dumps(PROBE_SYNC)})",
        )
        is True,
    )
    check(
        "填入本地目录",
        run_js(
            page,
            "window.__JF_TEST__.setInputByPlaceholder('选择或粘贴', "
            f"{json.dumps(probe_sync_dir)})",
        )
        is True,
    )
    check("点击「保存」", run_js(page, "window.__JF_TEST__.clickModalButton('保存')") is True)
    rule_id = None
    deadline = time.time() + 15
    while time.time() < deadline:
        hit = [c for c in _sync_svc.list_configs() if c.get("name") == PROBE_SYNC]
        if hit:
            rule_id = str(hit[0]["id"])
            break
        time.sleep(0.2)
    check("规则真的写进本地配置", rule_id is not None, f"id={rule_id}")
    check(
        "列表刷新后出现该规则",
        wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(PROBE_SYNC)}) >= 0",
            timeout_s=10,
        ),
    )
    check(
        "规则卡片显示本地目录与「已启用」",
        probe_sync_dir in (run_js(page, "window.__JF_TEST__.bodyText()") or "")
        and "已启用" in (run_js(page, "window.__JF_TEST__.bodyText()") or ""),
    )

    # 删除规则（经界面）
    check(
        "点击行内「删除规则」",
        run_js(
            page,
            "window.__JF_TEST__.clickItemButton(" f"{json.dumps(PROBE_SYNC)}, '删除规则')",
        )
        is True,
    )
    check(
        "弹出删除确认框",
        wait_until(page, "window.__JF_TEST__.bodyText().indexOf('确认删除') >= 0", timeout_s=8),
    )
    run_js(page, "window.__JF_TEST__.clickModalButton('删除')")
    gone = _wait_python(
        lambda: not [c for c in _sync_svc.list_configs() if c.get("name") == PROBE_SYNC],
        timeout_s=15,
    )
    check("删除后本地配置里不再有该规则", gone)
    import shutil as _shutil

    _shutil.rmtree(probe_sync_dir, ignore_errors=True)
    check("测试目录已清理", not os.path.exists(probe_sync_dir))

    # ── 19. N10：传输任务（含修复中心页签） ──
    run_js(page, "location.hash = '#/transfer'")
    check(
        "进入传输任务页",
        wait_until(page, "window.__JF_TEST__.pageHeaderText().indexOf('传输任务') >= 0"),
    )
    check(
        "传输页加载完成",
        wait_until(page, "document.querySelectorAll('.skeleton').length === 0", timeout_s=15),
    )
    transfer_text = run_js(page, "window.__JF_TEST__.bodyText()") or ""
    check(
        "渲染传输区（空态或任务列表）与页签",
        ("暂无传输任务" in transfer_text or "共" in transfer_text) and "修复" in transfer_text,
        transfer_text[:120],
    )
    # 切到「修复」页签
    check("点击「修复」页签", run_js(page, "window.__JF_TEST__.clickButtonText('修复')") is True)
    check(
        "修复中心内容出现（#/repair 深链等价）",
        wait_until(
            page,
            "(window.__JF_TEST__.bodyText().indexOf('修复任务') >= 0)"
            " || (window.__JF_TEST__.bodyText().indexOf('暂无修复') >= 0)"
            " || (window.__JF_TEST__.bodyText().indexOf('损坏媒体') >= 0)",
            timeout_s=15,
        ),
        (run_js(page, "window.__JF_TEST__.bodyText()") or "")[:120],
    )
    info(f"传输任务页截图：{save_shot(shell, 'transfer_page.png', page)}")
    # 深链：#/repair 应落在同一页的修复标签
    run_js(page, "location.hash = '#/repair'")
    check(
        "深链 #/repair 落在修复标签",
        wait_until(page, "window.__JF_TEST__.pageHeaderText().indexOf('传输任务') >= 0", timeout_s=15)
        or wait_until(page, "window.__JF_TEST__.bodyText().indexOf('修复') >= 0", timeout_s=5),
    )

    # ── N7：右键上下文菜单（重命名 / 删除，真落盘 + 零残留） ──
    CTX_DIR = "poc-n7-ctx"
    CTX_DIR2 = "poc-n7-ctx-renamed"
    ctx_dir_abs = os.path.join(real_path_of_disk, CTX_DIR)
    ctx_dir2_abs = os.path.join(real_path_of_disk, CTX_DIR2)
    for name in (CTX_DIR, CTX_DIR2):
        try:
            _file_svc.delete_file(first_disk_id, name)
        except Exception:  # noqa: BLE001 - 不存在即可
            pass
    _file_svc.make_dir(first_disk_id, CTX_DIR)
    run_js(page, f"location.hash = '#/files/{first_disk_id}'")
    wait_until(page, "window.__JF_TEST__.bodyText().indexOf('新建目录') >= 0", timeout_s=15)
    check(
        "探针目录出现在列表",
        wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(CTX_DIR)}) >= 0",
            timeout_s=15,
        ),
    )

    # 目录行右键 → 菜单
    check(
        "在目录行上右键",
        run_js(page, f"window.__JF_TEST__.rightClickText({json.dumps(CTX_DIR)})") is True,
    )
    time.sleep(0.4)
    ctx_items = json.loads(run_js(page, "window.__JF_TEST__.contextMenuItems()") or "[]")
    check(
        "目录右键菜单含「重命名 / 移动到… / 删除」",
        {"重命名", "删除"} <= set(ctx_items) and any("移动" in i for i in ctx_items),
        str(ctx_items),
    )

    # 重命名（真落盘）
    check(
        "点击菜单项「重命名」",
        run_js(page, "window.__JF_TEST__.clickContextMenuItem('重命名')") is True,
    )
    check("重命名弹窗已打开", wait_until(page, "window.__JF_TEST__.modalOpen()", timeout_s=8))
    check(
        "填入新名称",
        run_js(
            page,
            "(function(){var i=document.querySelector('.modal input');"
            "if(!i)return false;"
            "var s=Object.getOwnPropertyDescriptor("
            "window.HTMLInputElement.prototype,'value').set;"
            f"s.call(i,{json.dumps(CTX_DIR2)});"
            "i.dispatchEvent(new Event('input',{bubbles:true}));return true;})()",
        )
        is True,
    )
    check("点击「确认」", run_js(page, "window.__JF_TEST__.clickModalButton('确认')") is True)
    renamed = _wait_python(
        lambda: os.path.isdir(ctx_dir2_abs) and not os.path.isdir(ctx_dir_abs), timeout_s=15
    )
    check("重命名真的落到磁盘（旧名消失、新名出现）", renamed, ctx_dir2_abs)
    check(
        "列表刷新后出现新名称",
        wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(CTX_DIR2)}) >= 0",
            timeout_s=10,
        ),
    )

    # 删除（经右键菜单 + 确认框）
    check(
        "在重命名后的目录上右键",
        run_js(page, f"window.__JF_TEST__.rightClickText({json.dumps(CTX_DIR2)})") is True,
    )
    time.sleep(0.4)
    check(
        "点击菜单项「删除」",
        run_js(page, "window.__JF_TEST__.clickContextMenuItem('删除')") is True,
    )
    check(
        "弹出删除确认框",
        wait_until(page, "window.__JF_TEST__.bodyText().indexOf('确认删除') >= 0", timeout_s=8),
    )
    run_js(page, "window.__JF_TEST__.clickModalButton('删除')")
    deleted = _wait_python(lambda: not os.path.exists(ctx_dir2_abs), timeout_s=15)
    check("删除后目录从磁盘消失（零残留）", deleted, ctx_dir2_abs)
    if not deleted:
        # 取证：直接调服务层删同一路径，区分"页面传错路径"与"接口本身有问题"
        try:
            _file_svc.delete_file(first_disk_id, CTX_DIR2)
            info(
                "取证：服务层用**同一路径**删除成功 → 说明接口没问题，"
                "是页面/确认框链路把路径传错了"
            )
        except Exception as exc:  # noqa: BLE001
            info(f"取证：服务层用同一路径删除也失败 → 接口侧问题：{exc}")
        check("取证后目录已清掉", not os.path.exists(ctx_dir2_abs))

    # 文件行右键：应额外提供「预览 / 下载」
    probe_file = next((n for n in server_names if n.lower().endswith('.md')), None)
    if probe_file:
        check(
            f"在文件行「{probe_file}」上右键",
            run_js(page, f"window.__JF_TEST__.rightClickText({json.dumps(probe_file)})") is True,
        )
        time.sleep(0.4)
        file_items = json.loads(run_js(page, "window.__JF_TEST__.contextMenuItems()") or "[]")
        check(
            "文件右键菜单含「预览 / 下载 / 重命名 / 删除」",
            {"预览", "下载", "重命名", "删除"} <= set(file_items),
            str(file_items),
        )
        run_js(page, "document.body.click()")
        time.sleep(0.3)

    # 媒体文件右键还应出现「修复损坏媒体」（v1.4.2，只对视频/音频显示）
    # 列表可能分页/需要滚动，逐个候选试到能点中的那个媒体文件为止
    media_candidates = [
        n for n in server_names if n.lower().endswith(('.mp4', '.mkv', '.mov', '.mp3', '.avi'))
    ]
    media_file = None
    for candidate in media_candidates:
        if run_js(page, f"window.__JF_TEST__.rightClickText({json.dumps(candidate)})") is True:
            media_file = candidate
            break
    if media_file:
        check(f"在媒体文件「{media_file}」上右键", True)
        time.sleep(0.4)
        media_items = json.loads(run_js(page, "window.__JF_TEST__.contextMenuItems()") or "[]")
        check(
            "媒体文件右键菜单含「修复」入口（只读断言，不真的发起修复）",
            any("修复" in i for i in media_items),
            str(media_items),
        )
        run_js(page, "document.body.click()")
        time.sleep(0.3)

    # ── N7 尾巴：原生拖放上传（合成真实的 drop 事件） ──
    drop_name = "poc-dnd-upload.txt"
    # 必须放在**被服务目录之外**：否则文件本来就会出现在远端列表里，
    # 断言"已上传"会变成假通过（实测踩过）
    drop_src = str(_HERE / "out" / drop_name)
    with open(drop_src, "w", encoding="utf-8") as fp:
        fp.write("拖放上传测试内容\n")

    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QDropEvent

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(drop_src)])
    drop_event = QDropEvent(
        QPointF(120, 120),
        _Qt.DropAction.CopyAction,
        mime,
        _Qt.MouseButton.LeftButton,
        _Qt.KeyboardModifier.NoModifier,
    )
    shell.view.dropEvent(drop_event)
    info("已合成 drop 事件（模拟从系统拖入文件）")
    time.sleep(1.5)
    info(
        "拖放诊断（渲染层视角）= "
        + str(run_js(page, "JSON.stringify(window.__JF_DND__ || null)"))
    )
    check(
        "拖放本地文件真的上传到远端",
        _wait_python(
            lambda: any(
                str(f.get("name")) == drop_name for f in _file_svc.list_files(first_disk_id, "")
            ),
            timeout_s=30,
        ),
    )
    check(
        "上传后列表出现该文件",
        wait_until(
            page,
            f"window.__JF_TEST__.bodyText().indexOf({json.dumps(drop_name)}) >= 0",
            timeout_s=15,
        ),
    )
    # 清理：远端文件 + 本地临时文件
    try:
        _file_svc.delete_file(first_disk_id, drop_name)
    except Exception as exc:  # noqa: BLE001
        info(f"清理远端拖放文件失败：{exc}")
    if os.path.exists(drop_src):
        os.remove(drop_src)
    check(
        "拖放测试零残留（远端与本地）",
        not any(
            str(f.get("name")) == drop_name for f in _file_svc.list_files(first_disk_id, "")
        )
        and not os.path.exists(drop_src),
    )

    # ── N10：**取消是真的**（切到 Python TransferManager 后的核心验证） ──
    from src.bridge import transfer as _transfer_bridge
    # 提交一个较大的上传 → 立刻取消 → 断言任务真的终止（而不是"界面说取消、文件还在传"）
    big_name = "poc-cancel-probe.bin"
    big_src = str(_HERE / "out" / big_name)
    with open(big_src, "wb") as fp:
        fp.write(b"0" * (24 * 1024 * 1024))  # 24MB：足够让任务停留在传输中
    cancel_id = str(
        _transfer_bridge._h_transfer_upload(
            {"disk_id": first_disk_id, "rel_dir": "", "local_path": big_src}
        )["id"]
    )
    check("已提交大文件上传（用于取消验证）", bool(cancel_id), cancel_id)

    # 等它真的开始跑（避免"还没启动就取消"这种假验证）
    started = _wait_python_pumping(
        app,
        lambda: next(
            (
                i["status"]
                for i in _transfer_bridge._h_transfer_list({})["tasks"]
                if i["id"] == cancel_id
            ),
            "",
        )
        in ("running", "hashing", "pending"),
        timeout_s=20,
    )
    check("任务进入运行态", started)

    check(
        "调用取消",
        _transfer_bridge._h_transfer_cancel({"task_id": cancel_id}) == {"ok": True},
    )
    cancelled = _wait_python_pumping(
        app,
        lambda: next(
            (
                i["status"]
                for i in _transfer_bridge._h_transfer_list({})["tasks"]
                if i["id"] == cancel_id
            ),
            "",
        )
        in ("cancelled", "failed"),
        timeout_s=60,
    )
    final_status = next(
        (
            i["status"]
            for i in _transfer_bridge._h_transfer_list({})["tasks"]
            if i["id"] == cancel_id
        ),
        "",
    )
    check(
        "取消后任务**真的终止**（未跑成 completed）",
        cancelled and final_status != "completed",
        f"status={final_status}",
    )

    # 清理：本地大文件 + 远端若已产生同名文件
    if os.path.exists(big_src):
        os.remove(big_src)
    try:
        _file_svc.delete_file(first_disk_id, big_name)
    except Exception:  # noqa: BLE001 - 没传上去就无需删
        pass
    check(
        "取消测试零残留",
        not os.path.exists(big_src)
        and not any(
            str(f.get("name")) == big_name for f in _file_svc.list_files(first_disk_id, "")
        ),
    )
    _transfer_bridge._h_transfer_clear({})

    # ── 字节级验证：StreamProxy 的 Range 是否**逐字节准确** ──
    # 背景：用户在真实使用中拖动进度后日志出现
    #   `Packet corrupt` / `Invalid NAL unit size` / `partial file`
    # → 怀疑按范围取回的字节与真实文件不一致。这里做决定性比对：
    #   代理按 Range 取回的一段 vs 直接解密整文件后的同一段。
    import urllib.request

    from src.components.stream_proxy import StreamProxy as _SP

    media_name = next((n for n in server_names if n.lower().endswith(".mp4")), None)
    if media_name is None:
        info("磁盘里没有 mp4，跳过 StreamProxy 字节比对")
    else:
        whole = _file_svc.get_preview_bytes(first_disk_id, media_name)
        info(f"整文件解密后大小 = {len(whole)} 字节")
        proxy = _SP(first_disk_id, "", media_name)
        proxy.start()
        try:
            cases = [(0, 4095), (1000, 1999), (len(whole) // 2, len(whole) // 2 + 999)]
            for start, end in cases:
                req = urllib.request.Request(proxy.url)
                req.add_header("Range", f"bytes={start}-{end}")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    got = resp.read()
                want = whole[start:end + 1]
                if got == want:
                    info(f"  Range {start}-{end}：逐字节一致 ✅（{len(got)} 字节）")
                else:
                    diff = next(
                        (i for i, (a, b) in enumerate(zip(got, want)) if a != b), None
                    )
                    info(
                        f"  Range {start}-{end}：**不一致** ❌ "
                        f"收到 {len(got)} 字节 / 期望 {len(want)}；首个不同位置={diff}"
                    )
            mismatches = []
            for start, end in cases:
                req2 = urllib.request.Request(proxy.url)
                req2.add_header("Range", f"bytes={start}-{end}")
                with urllib.request.urlopen(req2, timeout=30) as resp2:
                    got2 = resp2.read()
                if got2 != whole[start:end + 1]:
                    mismatches.append(f"{start}-{end}")
            check(
                "StreamProxy 的 Range 返回与真实文件逐字节一致",
                not mismatches,
                f"不一致的区间={mismatches}",
            )
        finally:
            proxy.close()

    # ── N10 尾巴：传输任务经桥交给 Python 的 TransferManager ──
    from src.bridge import transfer as _transfer_bridge

    check("桥已挂上传输管理器", _transfer_bridge._manager is not None)
    check("初始任务列表为空", _transfer_bridge._h_transfer_list({})["tasks"] == [])

    # 源文件放在**被服务目录之外**（否则"已上传"会假通过）
    t_name = "poc-transfer-upload.txt"
    t_src = str(_HERE / "out" / t_name)
    with open(t_src, "w", encoding="utf-8") as fp:
        fp.write("传输管理器测试内容\n")

    submitted = _transfer_bridge._h_transfer_upload(
        {"disk_id": first_disk_id, "rel_dir": "", "local_path": t_src}
    )
    task_id = str(submitted["id"])
    check("提交上传任务返回任务 id", bool(task_id), task_id)

    def _task_state() -> str:
        for item in _transfer_bridge._h_transfer_list({})["tasks"]:
            if item["id"] == task_id:
                return str(item["status"])
        return ""

    # 必须**边等边泵事件**：工作线程的完成信号经 GUI 事件循环投递，
    # 纯 sleep 会把它饿死（实测任务会一直停在 running）—— 与 QMediaPlayer 同一类陷阱
    check(
        "任务经管理器跑到 completed",
        _wait_python_pumping(
            app, lambda: _task_state() in ("completed", "failed"), timeout_s=60
        ),
        f"status={_task_state()}",
    )
    task_row = next(
        (i for i in _transfer_bridge._h_transfer_list({})["tasks"] if i["id"] == task_id), {}
    )
    check("任务状态为 completed（无错误）", task_row.get("status") == "completed", str(task_row))
    check(
        "任务携带文件名与百分比",
        task_row.get("filename") == t_name and task_row.get("percent") == 100,
        str(task_row),
    )
    check(
        "远端真的出现该文件",
        any(str(f.get("name")) == t_name for f in _file_svc.list_files(first_disk_id, "")),
    )

    # 清空已结束任务
    cleared = _transfer_bridge._h_transfer_clear({})
    check("清空已结束任务后列表为空", _transfer_bridge._h_transfer_list({})["tasks"] == [], str(cleared))

    # 清理：远端文件 + 本地临时文件
    try:
        _file_svc.delete_file(first_disk_id, t_name)
    except Exception as exc:  # noqa: BLE001
        info(f"清理远端传输测试文件失败：{exc}")
    if os.path.exists(t_src):
        os.remove(t_src)
    check(
        "传输测试零残留",
        not os.path.exists(t_src)
        and not any(str(f.get("name")) == t_name for f in _file_svc.list_files(first_disk_id, "")),
    )

    # ── N18：免登录恢复必须经**服务端**确认（不能只看本地过期时间） ──
    from src.services import auth_service as _auth_svc

    session_path = Path(_auth_svc._SESSION_FILE)
    original_session = session_path.read_text(encoding="utf-8")

    check(
        "有效会话可免登录恢复（且经服务端确认）",
        _auth_svc.try_restore_session() is True,
    )

    # 篡改 token：模拟"服务端已吊销 / 换库重建"的会话
    tampered = json.loads(original_session)
    tampered["token"] = "tampered.invalid.token"
    session_path.write_text(json.dumps(tampered), encoding="utf-8")

    check(
        "**已吊销/伪造的 token 不再被接受**（不会假装已登录进主界面）",
        _auth_svc.try_restore_session() is False,
    )
    check(
        "失效会话已被清理（回登录页，而不是带着坏会话进主界面）",
        not session_path.exists()
        or not json.loads(session_path.read_text(encoding="utf-8")).get("token"),
    )

    # 还原会话，保证后续步骤不受影响
    session_path.write_text(original_session, encoding="utf-8")
    check("还原后仍可正常免登录恢复", _auth_svc.try_restore_session() is True)

    # ── 15. 退出登录（N12 的账号区块） ─────────────
    # 注意：登出按钮在设置页，刷新密钥后会停在安全状态页，必须先切回去
    run_js(page, "location.hash = '#/settings'")
    wait_until(page, "window.__JF_TEST__.bodyText().indexOf('笔记目录') >= 0")
    run_js(page, "window.__JF_TEST__.clickButtonText('退出登录')")
    confirm = wait_until(page, "window.__JF_TEST__.bodyText().indexOf('确定要退出登录吗') >= 0")
    check("「退出登录」弹出确认框", confirm)
    if confirm:
        run_js(page, "window.__JF_TEST__.clickButtonText('退出')")
        back_to_login = wait_until(
            page, "window.__JF_TEST__.hash().indexOf('/login') >= 0"
        )
        check("确认后回到登录页（应用内可以登出了）", back_to_login,
              f"hash={run_js(page, 'window.__JF_TEST__.hash()')}")

    # ── 5. 窗口控制桥可用 ──
    check(
        "qwebchannel.js 已注入主世界",
        run_js(page, "typeof window.QWebChannel") == "function",
        f"typeof QWebChannel = {run_js(page, 'typeof window.QWebChannel')}",
    )

    max_raw = run_js(page, "document.querySelectorAll('.win-btn')[1].getAttribute('title')")
    check("最大化按钮标题为「最大化」（初始未最大化）", max_raw == "最大化", str(max_raw))

    # ── 16. N17：系统托盘 ─────────────────────────
    tray = getattr(shell, "tray", None)
    check("托盘图标已创建且可见", bool(tray and tray.tray.isVisible()))

    tray_menu = tray.tray.contextMenu() if tray else None
    tray_actions = [a.text() for a in tray_menu.actions()] if tray_menu else []
    check(
        "托盘菜单含「显示窗口 / 退出」和主题三态（平铺，不用子菜单）",
        all(k in tray_actions for k in ("显示窗口", "跟随系统", "亮色", "暗色", "退出")),
        str(tray_actions),
    )
    # 注：主题项刻意**平铺**而非二级子菜单 —— PySide6 6.11 下
    # `addMenu()` 返回的子菜单一旦加入 action，其 Python 包装对象即失效
    # （`Internal C++ object (QMenu) already deleted`，四种写法全部复现）。
    theme_actions = tray.theme_actions if tray else {}
    check(
        "主题三项为可勾选菜单项",
        set(theme_actions.keys()) == {"system", "light", "dark"}
        and all(a.isCheckable() for a in theme_actions.values()),
        str({m: a.isCheckable() for m, a in theme_actions.items()}),
    )

    # 关闭 → 最小化到托盘（不是退出）
    shell.show()
    app.processEvents()
    check("测试前窗口可见", shell.isVisible() is True)
    check("点击标题栏 ✕（走 JS → 桥 → Python）",
          run_js(page, "window.__JF_TEST__.clickWindowButton(2)") is True)
    deadline = time.time() + 8
    while time.time() < deadline and shell.isVisible():
        app.processEvents()
        time.sleep(0.1)
    check(
        "✕ 后窗口隐藏到托盘（应用未退出、托盘仍在）",
        shell.isVisible() is False
        and shell._quitting is False
        and bool(tray and tray.tray.isVisible()),
        f"visible={shell.isVisible()} quitting={shell._quitting}",
    )

    shell.restore_from_tray()
    app.processEvents()
    time.sleep(0.4)
    check("双击托盘（restore_from_tray）恢复窗口", shell.isVisible() is True)

    # 托盘主题遥控：Python 发意图 → 渲染层应用 → 回执 Python 同步勾选
    bridge.publish_event("theme.set", {"mode": "dark"})
    check(
        "托盘主题意图被渲染层应用（<html> 带上 dark）",
        wait_until(page, "window.__JF_TEST__.isDark()", timeout_s=8),
    )
    check(
        "渲染层回执后托盘菜单记录为「暗色」",
        _wait_python(lambda: tray.theme_mode == "dark", timeout_s=8),
        f"托盘记录的模式={tray.theme_mode!r}",
    )
    dark_action = tray.theme_actions.get("dark") if tray else None
    check("「暗色」菜单项处于勾选态", bool(dark_action and dark_action.isChecked()))

    # 还原用户原本的主题偏好（测试里点过主题按钮，不还原会改掉用户的选择）
    bridge.publish_event("theme.set", {"mode": original_theme_mode})
    check(
        "已还原测试前的主题偏好",
        _wait_python(
            lambda: run_js(page, "window.__JF_TEST__.themeMode()") == original_theme_mode,
            timeout_s=8,
        ),
        f"期望={original_theme_mode!r} 实际={run_js(page, 'window.__JF_TEST__.themeMode()')!r}",
    )

    # 退出：置位退出标志并收起托盘图标（放最后：会调用 QApplication.quit）
    shell.quit_application()
    check(
        "托盘「退出」置位退出标志并收起托盘图标",
        shell._quitting is True and tray.tray.isVisible() is False,
        f"quitting={shell._quitting} trayVisible={tray.tray.isVisible()}",
    )

    # ── 6. 收尾：登出 + 环境校验 ──
    try:
        from src.services import auth_service
        auth_service.logout()
        info("已在测试进程内调用 auth_service.logout()（作用于临时会话文件）")
    except Exception as exc:  # noqa: BLE001
        info(f"登出清理失败（不影响结论）：{exc}")

    shell.close()
    app.processEvents()

    real_ok, real_detail = guard.verify_unchanged()
    check("用户真实会话文件全程未被改动", real_ok, real_detail)
    guard.cleanup()

    if _args.keep_account:
        info(f"按 --keep-account 保留临时账号与凭据：{_CRED_FILE}")
    else:
        drop_account()

    failed = [name for name, ok, _ in _results if not ok]
    print("\n" + "=" * 70)
    print(f"登录页 + 业务面板 冒烟：{len(_results) - len(failed)}/{len(_results)} 通过")
    if failed:
        print("失败项：")
        for name in failed:
            print(f"  - {name}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

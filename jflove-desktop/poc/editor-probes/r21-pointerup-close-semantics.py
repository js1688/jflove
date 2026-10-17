#!/usr/bin/env python3
"""
r21 语义探针：**「在 `pointerup` 监听里关掉弹框」会不会吃掉这次 `click`**（v1.6.2 Phase 1 取证）

## 为什么需要它

`ScratchToolbar.tsx` 的工具箱把"选完即关"挂在弹框容器的 `onPointerUpCapture` 上；
而 jsdom 用例（`scratch-toolbar-labels-menu-ac74-ac76.test.tsx` 的 `pressButton`）把
`pointerdown / pointerup / click` 三事件塞进**同一个 `act()`** —— 于是关框提交被推迟到
`click` 之后，动作"看起来生效"。真实浏览器里 `pointerup` 与 `click` 是两个独立 task，
React 的离散事件同步提交发生在 `pointerup` 之后 ⇒ **按钮已被卸载** ⇒ `onClick` 不再触发。

本探针**不含 React、不含应用**，只用最小 DOM + 真鼠标（`QMouseEvent` 投给 QtWebEngine 的
输入 widget），直接回答一个问题：**Chromium 会不会把 `click` 交给一个已在 `pointerup`
监听里被移除的元素。**

## 三种模式（同一页面结构，只改关框挂在哪）

| 模式 | 关框挂点 | 期望（若机制成立） |
|------|---------|------------------|
| `no-close` | 不关框（**守门对照**） | `CLICKED` + `button-click` —— 证明合成鼠标真的产生了 `click` |
| `pointerup-capture` | `menu` 的 `pointerup` **捕获** | 按钮被移除 ⇒ `button-click` **不出现** |
| `click-bubble` | `menu` 的 `click` **冒泡**（拟修法） | 按钮的 `click` 先跑、随后关框 ⇒ `CLICKED` |

## 怎么跑

    jflove-desktop\\venv-win\\Scripts\\python.exe ^
        poc\\editor-probes\\r21-pointerup-close-semantics.py

## 退出码

- `0` 三个模式都成功测到（守门对照必须为 `CLICKED`）
- `2` 环境/脚本问题：QtWebEngine 起不来，或**守门对照都没产生 click**（⇒ 合成鼠标没到渲染层，
  三种模式的读数都不可采信）

## 边界

- 只在本次运行的 `out/r21-<时间戳>/` 下写证据；用**默认（off-the-record）profile**，不碰用户数据目录；
- 页面是探针自己生成的最小 HTML，**不加载产品产物**、不读任何业务文件。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
PROBES = SCRIPT.parent
DESKTOP = SCRIPT.parents[2]
REPO = SCRIPT.parents[3]

# Windows 控制台是 GBK：日志里的 → / ⇒ 等字符不重配置会 UnicodeEncodeError 中断探针
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

sys.path.insert(0, str(DESKTOP))

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QEvent, QEventLoop, QPointF, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# ⚠ 顺序：QtWebEngineWidgets 必须在 QApplication 之前导入（Qt 6 的共享 OpenGL 上下文）
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

VIEW_W, VIEW_H = 900, 620

#: 三种模式：`MODE_JS` 就是"关框挂在哪"的唯一差别
MODES: dict[str, str] = {
    "no-close": "/* 守门对照：不关框 */",
    "pointerup-capture": "menu.addEventListener('pointerup', closeMenu, true);",
    "click-bubble": "menu.addEventListener('click', closeMenu, false);",
}

#: 页面：整屏按钮（**不依赖坐标换算** —— 视口中心恒在按钮内），菜单容器包住按钮
HTML_TEMPLATE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>r21</title>
<style>
  html, body {{ margin: 0; height: 100%; }}
  #menu {{ position: fixed; inset: 0; }}
  #b {{ position: absolute; inset: 0; width: 100%; height: 100%; font-size: 28px; }}
  #out {{ position: fixed; left: 8px; bottom: 8px; font-size: 20px; }}
</style></head>
<body>
<div id="menu"><button id="b">排序</button></div>
<div id="out">NOT-CLICKED</div>
<script>
  window.__log = [];
  var MODE = {mode_json};
  var menu = document.getElementById('menu');
  var b = document.getElementById('b');
  var out = document.getElementById('out');
  function closeMenu() {{
    window.__log.push('close');
    menu.remove();
  }}
  {mode_js}
  b.addEventListener('click', function () {{
    window.__log.push('button-click');
    out.textContent = 'CLICKED';
  }});
  document.addEventListener('click', function (e) {{
    var t = e.target;
    window.__log.push('doc-click:' + (t && t.id ? t.id : (t && t.tagName ? t.tagName : 'null')));
  }}, true);
  document.addEventListener('pointerup', function () {{
    window.__log.push('doc-pointerup');
  }}, true);
</script>
</body></html>
"""


def build_html(mode: str) -> str:
    """按模式生成页面（唯一差别 = 关框挂点）"""
    return HTML_TEMPLATE.format(mode_json=json.dumps(mode), mode_js=MODES[mode])


def pump(ms: int) -> None:
    """泵事件循环（Chromium 的渲染/输入投递都在下一帧）"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def js_eval(page, script: str, timeout_ms: int = 3000) -> object:
    """同步执行 JS 并取回 JSON 化结果（`runJavaScript` 是异步回调 ⇒ 自己泵循环等）"""
    box: dict[str, object] = {"done": False, "value": None}

    def _cb(value: object) -> None:
        box["value"] = value
        box["done"] = True

    page.runJavaScript(script, _cb)
    waited = 0
    while not box["done"] and waited < timeout_ms:
        pump(50)
        waited += 50
    if not box["done"]:
        raise TimeoutError(f"runJavaScript 超时：{script[:60]}")
    raw = box["value"]
    return json.loads(raw) if isinstance(raw, str) else raw


def os_center_of_window(view: QWebEngineView) -> tuple[int, int]:
    """
    **物理像素**的窗口中心（`GetWindowRect`）。

    口径（照抄 r13 的 OS 级输入结论）：Qt 合成事件到不了渲染层，必须用 OS 级输入；
    而 `SetCursorPos` / `mouse_event` 吃的是物理屏幕像素 ⇒ 不能拿 Qt 的逻辑坐标直接喂
    （高 DPI 缩放下会偏到窗口外）。窗口中心对高 DPI 免疫，且页面被 100% 视口的按钮占满。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = int(view.winId())
    user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)


def real_click(view: QWebEngineView, input_mode: str = "os") -> dict:
    """在视口**中心**发一次真鼠标 press/release（`os` = OS 级输入；`qt` = Qt 合成，仅留对照）"""
    center_os = os_center_of_window(view)
    pump(250)

    if input_mode == "os":
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetCursorPos(center_os[0], center_os[1])
        pump(150)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        pump(80)
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        pump(400)
        return {"inputMode": "os", "screenCenter": list(center_os)}

    from PySide6.QtGui import QMouseEvent

    # 照抄 r11 的前置：真输入要求窗口已暴露 + 输入 widget 拿到焦点
    view.setFocus()
    proxy = view.focusProxy()
    target = proxy if proxy is not None else view
    target.setFocus()
    pump(200)
    center = QPointF(target.width() / 2.0, target.height() / 2.0)
    global_pt = QPointF(target.mapToGlobal(center.toPoint()))

    def _send(kind: QEvent.Type, button: Qt.MouseButton, buttons: Qt.MouseButtons) -> None:
        QApplication.sendEvent(
            target,
            QMouseEvent(kind, center, global_pt, button, buttons, Qt.KeyboardModifier.NoModifier),
        )

    _send(QEvent.Type.MouseMove, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton)
    pump(80)
    _send(QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    pump(80)
    _send(QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton)
    pump(400)
    return {
        "inputMode": "qt",
        "inputWidget": type(target).__name__,
        "widgetSize": [target.width(), target.height()],
        "center": [center.x(), center.y()],
        "targetVisible": bool(target.isVisible()),
        "targetHasFocus": bool(target.hasFocus()),
    }


def run_mode(mode: str, out_dir: Path, view: QWebEngineView, input_mode: str) -> dict:
    """加载页面 → 真鼠标点中心 → 读回观测值"""
    html_path = out_dir / f"r21-{mode}.html"
    html_path.write_text(build_html(mode), encoding="utf-8")

    loop = QEventLoop()
    view.loadFinished.connect(lambda ok: loop.quit())
    view.load(QUrl.fromLocalFile(str(html_path)))
    QTimer.singleShot(15000, loop.quit)
    loop.exec()
    pump(400)

    click_info = real_click(view, input_mode)
    observed = js_eval(
        view.page(),
        "JSON.stringify({out: (document.getElementById('out')||{}).textContent || null,"
        " log: window.__log, buttonStillConnected: !!(document.getElementById('b')"
        " && document.getElementById('b').isConnected)})",
    )
    return {"mode": mode, "click": click_info, "observed": observed}


def main() -> int:
    parser = argparse.ArgumentParser(description="r21：pointerup 关框是否吃掉 click")
    parser.add_argument("--out", default=None, help="证据目录（默认 out/r21-<时间戳>）")
    parser.add_argument(
        "--input",
        choices=["os", "qt"],
        default="os",
        help="os = OS 级鼠标（默认，唯一能到渲染层的）；qt = Qt 合成（仅留对照）",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else PROBES / "out" / f"r21-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 必须先有 QApplication 才能创建 QWebEngineView（复用已有实例也走同一条）
    QApplication.instance() or QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(VIEW_W, VIEW_H)
    view.setWindowTitle("r21-pointerup-close-semantics")
    view.show()
    view.raise_()
    view.activateWindow()
    from PySide6.QtTest import QTest

    QTest.qWaitForWindowExposed(view)  # 真输入前置：窗口必须已暴露（照抄 r11 口径）
    pump(900)

    results: list[dict] = []
    try:
        for mode in MODES:
            results.append(run_mode(mode, out_dir, view, args.input))
    except Exception as exc:  # noqa: BLE001 —— 探针要把环境问题与结论分开
        print(f"!! 环境/脚本问题：{type(exc).__name__}: {exc}", flush=True)
        (out_dir / "r21-result.json").write_text(
            json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 2
    finally:
        view.hide()

    by_mode = {item["mode"]: item for item in results}
    sanity = by_mode["no-close"]["observed"]
    sanity_ok = sanity.get("out") == "CLICKED" and "button-click" in (sanity.get("log") or [])

    verdict = {
        "stamp": stamp,
        "viewSize": [VIEW_W, VIEW_H],
        "modes": by_mode,
        "sanityGate": {
            "mode": "no-close",
            "expect": "out=CLICKED 且 log 含 button-click",
            "ok": sanity_ok,
        },
        "conclusion": {},
    }
    if sanity_ok:
        cap = by_mode["pointerup-capture"]["observed"]
        bubble = by_mode["click-bubble"]["observed"]
        verdict["conclusion"] = {
            "pointerupCaptureLosesClick": not (
                cap.get("out") == "CLICKED" and "button-click" in (cap.get("log") or [])
            ),
            "clickBubbleKeepsClick": bubble.get("out") == "CLICKED"
            and "button-click" in (bubble.get("log") or []),
            "clickRetargeted": [
                entry for entry in (cap.get("log") or []) if str(entry).startswith("doc-click:")
            ],
        }

    (out_dir / "r21-result.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[r21] 证据目录：{out_dir}", flush=True)
    for item in results:
        obs = item["observed"]
        print(
            f"[r21] {item['mode']:18s} out={obs.get('out')!r:14s} "
            f"log={obs.get('log')} 按钮仍挂载={obs.get('buttonStillConnected')}",
            flush=True,
        )
    print(f"[r21] 守门对照（no-close 必须 CLICKED）：{'PASS' if sanity_ok else 'FAIL'}", flush=True)
    if sanity_ok:
        print(f"[r21] 结论：{json.dumps(verdict['conclusion'], ensure_ascii=False)}", flush=True)
    else:
        print("!! 守门对照未产生 click ⇒ 合成鼠标没到渲染层，三模式读数不可采信", flush=True)
    return 0 if sanity_ok else 2


if __name__ == "__main__":
    sys.exit(main())

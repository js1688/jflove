"""
P0-1 自动化自检：无边框外壳 + Web 标题栏 + 桥通路

用途：在没有真实显示器 / 不方便人工点窗口的环境里，把 P0-1 的关键行为
      （桥往返、窗口动作、最大化状态回推、边缘热区算法）跑成断言。

人工验收（拖拽手感、缩放跟手、双击最大化）仍需本人在真机执行 —— 见 README.md。

运行：
    jflove-desktop/venv-win/Scripts/python.exe poc/frameless/test_headless.py
    # 若要在真实平台（如 Linux 强制 XWayland）上跑：
    jflove-desktop/venv-win/Scripts/python.exe poc/frameless/test_headless.py --platform=xcb

退出码：0 = 全部通过；1 = 有失败项
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# 必须在创建 QApplication 之前设置平台与 Chromium 参数
_parser = argparse.ArgumentParser(add_help=True)
_parser.add_argument("--platform", default="offscreen",
                     help="Qt 平台插件名，默认 offscreen；真机截图用 windows / xcb")
_parser.add_argument("--skip-move", action="store_true",
                     help="跳过 startSystemMove/startSystemResize 调用。"
                          "真机平台下这两个动作可能进入系统模态等待，自动跑会卡住")
_parser.add_argument("--keep-open", action="store_true", help="跑完不退出（人工观察用）")
_args, _unknown = _parser.parse_known_args()

os.environ["QT_QPA_PLATFORM"] = _args.platform
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-dev-shm-usage --no-sandbox --in-process-gpu",
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from main import ShellWindow, build_app  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

_PASS = "PASS"
_FAIL = "FAIL"
_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录一条断言结果"""
    _results.append((name, bool(ok), detail))
    flag = _PASS if ok else _FAIL
    print(f"[{flag}] {name}" + (f" — {detail}" if detail else ""))


def run_js(page, expr: str, timeout_ms: int = 8000):
    """
    在页面里同步求值一个表达式并取回结果。

    用嵌套事件循环等待回调 —— QWebChannel 的往返依赖事件循环转动。
    """
    loop = QEventLoop()
    box: dict = {}

    def _cb(result) -> None:
        box["value"] = result
        loop.quit()

    page.runJavaScript(expr, _cb)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return box.get("value")


def run_promise(page, expr: str, timeout_ms: int = 10000):
    """
    求值一个返回 Promise<string> 的表达式并取回结果。

    页面侧把结果写进 window.__JF_POC_RESULT__，本函数轮询取值
    （runJavaScript 无法直接序列化 Promise）。
    """
    page.runJavaScript(
        "window.__JF_POC_RESULT__ = '';"
        f"({expr}).then(function(r){{ window.__JF_POC_RESULT__ = r; }})"
        ".catch(function(e){ window.__JF_POC_RESULT__ = 'ERR:' + e; });"
    )
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        value = run_js(page, "window.__JF_POC_RESULT__", timeout_ms=2000)
        if value:
            return value
        time.sleep(0.02)
    return None


def probe_page(page) -> dict:
    """取回页面结构快照（页面侧返回 JSON 字符串）"""
    raw = run_js(page, "window.__JF_POC__ ? window.__JF_POC__.probe() : ''")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def bridge_diagnostics(page) -> str:
    """桥不通时抓一份现场，避免靠猜"""
    expr = (
        "JSON.stringify({"
        "qwebchannel: typeof QWebChannel,"
        "qt: typeof qt,"
        "transport: (typeof qt !== 'undefined' && qt && qt.webChannelTransport)"
        " ? typeof qt.webChannelTransport : 'MISSING',"
        "status: (document.getElementById('bridge-status')||{}).textContent || '',"
        "info: (document.getElementById('bridge-info')||{}).textContent || ''"
        "})"
    )
    return run_js(page, expr) or "(无返回)"


def save_shot(window, name: str) -> str:
    """
    抓一张窗口截图并返回描述。

    注意：QWebEngineView 的内容由 Chromium 独立进程渲染，QWidget.grab() 在
    离屏平台上通常只能抓到背景；真机平台可以抓到，但**刚做过最小化/还原后
    可能拿到尚未重绘的旧帧** —— 所以本函数只作诊断，不计入通过率。
    """
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, name)
        pixmap = window.grab()
        pixmap.save(path)
        img = pixmap.toImage()
        colors = set()
        for yy in range(0, img.height(), 24):
            for xx in range(0, img.width(), 24):
                colors.add(img.pixel(xx, yy))
                if len(colors) > 3:
                    break
            if len(colors) > 3:
                break
        return f"{pixmap.width()}x{pixmap.height()} 采样 {len(colors)} 色 → {path}"
    except Exception as exc:  # noqa: BLE001 - PoC 脚本，任何异常都只记录
        return f"失败（{exc}）"


def wait_for_load(page, timeout_ms: int = 30000) -> bool:
    """等待页面加载完成"""
    loop = QEventLoop()
    state = {"loaded": False}

    def _on_load(ok: bool) -> None:
        state["loaded"] = bool(ok)
        loop.quit()

    page.loadFinished.connect(_on_load)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    try:
        page.loadFinished.disconnect(_on_load)
    except (RuntimeError, TypeError):
        pass
    return state["loaded"]


def main() -> int:
    app: QApplication = build_app()
    app.setQuitOnLastWindowClosed(False)

    window = ShellWindow()
    window.show()

    page = window.view.page()
    loaded = wait_for_load(page)
    check("页面加载完成", loaded)
    if not loaded:
        print("\n页面未加载成功，后续断言跳过。")
        return 1

    # 给 QWebChannel 一点时间完成握手
    for _ in range(60):
        probe_raw = run_js(page, "window.__JF_POC__ ? window.__JF_POC__.probe() : ''")
        if probe_raw:
            break
        time.sleep(0.05)

    # ── 1. 页面结构 ────────────────────────────────
    probe = probe_page(page)
    check("标题栏存在（Web 自绘）", probe.get("titlebar") is True)
    check(
        "三个窗口按钮齐备",
        sorted(probe.get("buttons") or []) == ["btn-close", "btn-max", "btn-min"],
        str(probe.get("buttons")),
    )
    check("桥已连通（JS 侧持有 bridge 对象）", probe.get("bridgeReady") is True,
          "" if probe.get("bridgeReady") else f"诊断：{bridge_diagnostics(page)}")
    check("标题栏高度 = Web 端令牌 --header-h", probe.get("headerHeight") == "56px",
          f"实际 {probe.get('headerHeight')}")
    check("侧栏宽度 = Web 端令牌 --sidebar-w", probe.get("sidebarWidth") == "248px",
          f"实际 {probe.get('sidebarWidth')}")
    flags = int(window.windowFlags())
    check("Qt 原生外框已移除（FramelessWindowHint 已设置）", bool(flags & 0x00000800),
          f"windowFlags={flags}")

    # 窗口按钮必须「真的画出来」：DOM 存在但零尺寸/在视口外/被遮挡都不算
    btn_raw = run_js(
        page,
        "JSON.stringify(['btn-min','btn-max','btn-close'].map(function(id){"
        "  var el=document.getElementById(id);"
        "  var r=el.getBoundingClientRect();"
        "  var cs=getComputedStyle(el);"
        "  var svg=el.querySelector('svg');"
        "  var sr=svg?svg.getBoundingClientRect():null;"
        "  var root=document.documentElement.getBoundingClientRect();"
        "  return {id:id,x:Math.round(r.x),y:Math.round(r.y),"
        "          w:Math.round(r.width),h:Math.round(r.height),"
        "          inView:r.right<=root.right+1 && r.left>=0,"
        "          color:cs.color,opacity:cs.opacity,vis:cs.visibility,"
        "          svg:el.querySelectorAll('svg').length,"
        "          svgW:svg?Math.round(sr.width):0,"
        "          svgH:svg?Math.round(sr.height):0};"
        "}))",
    )
    btns = json.loads(btn_raw) if btn_raw else []
    for item in btns:
        ok = (
            item.get("w", 0) > 0
            and item.get("h", 0) > 0
            and item.get("inView") is True
            and item.get("vis") == "visible"
            and float(item.get("opacity", 0)) > 0.05
            and item.get("svg", 0) >= 1
            and item.get("svgW", 0) > 0
            and item.get("svgH", 0) > 0
        )
        check(
            f"窗口按钮 {item.get('id')} 真实可见且图标有实际尺寸",
            ok,
            json.dumps(item, ensure_ascii=False),
        )

    # 结构断言跑完立刻抓一张「设计原貌」截图（此时还没做过最大化/最小化）
    time.sleep(0.6)
    app.processEvents()
    print(f"[INFO] 初始截图：{save_shot(window, 'shell_initial.png')}")

    # ── 2. 桥往返（JS → Python → JS） ──────────────
    info_raw = run_promise(page, "window.__JF_POC__.call('platformInfo')")
    info = json.loads(info_raw) if info_raw and not str(info_raw).startswith("ERR") else {}
    check("桥方法返回平台信息（往返可用）", bool(info.get("ok")), str(info)[:160])
    if info.get("ok"):
        try:
            payload = json.loads(info["value"])
            check(
                "平台信息内容完整",
                all(k in payload for k in ("platform", "qt", "python", "os")),
                json.dumps(payload, ensure_ascii=False),
            )
        except (TypeError, ValueError) as exc:
            check("平台信息内容完整", False, f"解析失败 {exc}")

    # ── 3. 最大化 / 还原 + 状态回推 ─────────────────
    before = run_promise(page, "window.__JF_POC__.call('isMaximized')")
    check("初始未最大化", json.loads(before).get("value") is False, str(before))

    run_js(page, "window.__JF_POC__.callVoid('toggleMaximize')")
    time.sleep(0.4)
    app.processEvents()
    after = json.loads(run_promise(page, "window.__JF_POC__.call('isMaximized')"))
    check("toggleMaximize → 已最大化", after.get("value") is True, str(after))

    pushed = probe_page(page)
    check(
        "最大化状态已由 Python 推回 Web（maximizedChanged）",
        pushed.get("maximized") is True,
        f"Web 侧 maximized={pushed.get('maximized')}",
    )
    check("最大化时按钮仍存在（图标应切为还原）",
          run_js(page, "document.getElementById('btn-max').title") == "还原",
          str(run_js(page, "document.getElementById('btn-max').title")))

    run_js(page, "window.__JF_POC__.callVoid('toggleMaximize')")
    time.sleep(0.4)
    app.processEvents()
    back = json.loads(run_promise(page, "window.__JF_POC__.call('isMaximized')"))
    check("再次 toggleMaximize → 已还原", back.get("value") is False, str(back))
    check("还原后按钮标题复位",
          run_js(page, "document.getElementById('btn-max').title") == "最大化")

    # ── 4. 系统移动 / 缩放移交 ──────────────────────
    if _args.skip_move:
        print("[INFO] 已按 --skip-move 跳过 startSystemMove / startSystemResize 调用")
    else:
        move_raw = run_promise(page, "window.__JF_POC__.call('startSystemMove')")
        move = json.loads(move_raw) if move_raw else {}
        check(
            "startSystemMove 可调用且未抛异常",
            move.get("ok") is True,
            f"返回值={move.get('value')}（离屏平台为 False 属预期）",
        )

        resize_raw = run_promise(page, "window.__JF_POC__.call('startSystemResize', 3)")
        resize = json.loads(resize_raw) if resize_raw else {}
        check(
            "startSystemResize(Left|Top=3) 可调用且未抛异常",
            resize.get("ok") is True,
            f"返回值={resize.get('value')}（离屏平台为 False 属预期）",
        )

    zero_raw = run_promise(page, "window.__JF_POC__.call('startSystemResize', 0)")
    zero = json.loads(zero_raw) if zero_raw else {}
    check("startSystemResize(0) 被拒绝", zero.get("value") is False, str(zero_raw))

    # ── 5. 边缘热区算法 ────────────────────────────
    w = probe.get("viewport", [0, 0])[0]
    h = probe.get("viewport", [0, 0])[1]
    check("热区宽度与 Python 常量一致", probe.get("resizeMargin") == 6)
    check("左上角命中 Left|Top (1|2=3)",
          run_js(page, "window.__JF_POC__.edgesAt(0, 0)") == 3)
    check("右下角命中 Right|Bottom (4|8=12)",
          run_js(page, f"window.__JF_POC__.edgesAt({w - 1}, {h - 1})") == 12)
    check("窗口正中不命中热区",
          run_js(page, f"window.__JF_POC__.edgesAt({w // 2}, {h // 2})") == 0)
    check("左边缘光标为 ew-resize",
          run_js(page, f"window.__JF_POC__.cursorAt(0, {h // 2})") == "ew-resize")
    check("右下角光标为 nwse-resize",
          run_js(page, f"window.__JF_POC__.cursorAt({w - 1}, {h - 1})") == "nwse-resize")
    check("上边缘光标为 ns-resize",
          run_js(page, f"window.__JF_POC__.cursorAt({w // 2}, 0)") == "ns-resize")

    # ── 6. 最小化 ──────────────────────────────────
    run_js(page, "window.__JF_POC__.callVoid('minimize')")
    time.sleep(0.5)
    app.processEvents()
    check("minimize 生效（窗口进入最小化态）", window.isMinimized(),
          f"isMinimized={window.isMinimized()}（离屏平台可能不支持）")
    window.showNormal()
    app.processEvents()

    # ── 7. 截图（诊断项，不计入通过率） ─────────────
    # 这一步在 minimize/restore 之后，Chromium 可能尚未重绘，抓到旧帧属正常。
    time.sleep(0.6)
    app.processEvents()
    print(f"[INFO] 复位后截图：{save_shot(window, 'shell.png')}")

    # ── 8. 关闭（放最后，会真正关窗） ───────────────
    run_js(page, "window.__JF_POC__.callVoid('close')")
    time.sleep(0.5)
    app.processEvents()
    check("close 生效（窗口已关闭）", not window.isVisible(),
          f"isVisible={window.isVisible()}")

    # ── 汇总 ──────────────────────────────────────
    failed = [name for name, ok, _ in _results if not ok]
    print("\n" + "=" * 68)
    print(f"P0-1 自检：{len(_results) - len(failed)}/{len(_results)} 通过")
    if failed:
        print("失败项：")
        for name in failed:
            print(f"  - {name}")
    print("=" * 68)

    if _args.keep_open:
        QTimer.singleShot(0, app.quit)
        app.exec()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

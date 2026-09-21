"""
P0-4 自检：原生浮层能否准确贴合 Web 预留矩形（含移动 / 缩放 / 滚动）

判据（全部为硬断言）：
  1. 浮层可见，且**屏幕几何 == 页面预留矩形的换算结果**（容差 2px）
  2. 主窗口**移动**后浮层跟随（容差 2px）
  3. 主窗口**缩放**后浮层跟随，且尺寸 == 预留矩形尺寸
  4. 页面**滚动**后浮层跟随
  5. 预留区完全滚出视口后浮层**自动隐藏**
  6. 记录本机 dpr —— 换算中**不做 dpr 缩放**，若对齐即证明该约定成立

运行：
    jflove-desktop\\venv-win\\Scripts\\python.exe poc\\media-overlay\\test_headless.py
    ... --platform=offscreen    # 无显示器时（窗口几何仍可断言）

退出码：0 = 全部通过；1 = 有失败项
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC_DIR = _HERE.parent
_DESKTOP_ROOT = _POC_DIR.parent

_parser = argparse.ArgumentParser(add_help=True)
_parser.add_argument("--platform", default="offscreen", help="Qt 平台插件名")
_args, _unknown = _parser.parse_known_args()

os.environ["QT_QPA_PLATFORM"] = _args.platform
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

for _p in (str(_DESKTOP_ROOT), str(_POC_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import QEventLoop, QPoint, QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from main import OverlayController, make_overlay  # noqa: E402
from src.ui.web_shell import WebShell, build_app  # noqa: E402

_results: list[tuple[str, bool, str]] = []
TOLERANCE = 2  # 允许的像素误差


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def info(text: str) -> None:
    print(f"[INFO] {text}")


def run_js(page, expr: str, timeout_ms: int = 8000):
    loop = QEventLoop()
    box: dict = {}

    def _cb(result) -> None:
        box["value"] = result
        loop.quit()

    page.runJavaScript(expr, _cb)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return box.get("value")


def wait_until(predicate, timeout_s: float = 8.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def pump(app, seconds: float = 0.6) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)


def expected_geo(shell: WebShell, rect: dict) -> tuple[int, int, int, int]:
    """按 PoC 约定把页面矩形换算成屏幕几何（不做 dpr 缩放）"""
    view = shell.view
    top_left = view.mapToGlobal(QPoint(int(rect["x"]), int(rect["y"])))
    return (
        top_left.x(),
        top_left.y(),
        int(round(rect["w"])),
        int(round(rect["h"])),
    )


def near(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return all(abs(x - y) <= TOLERANCE for x, y in zip(a, b))


def main() -> int:
    app: QApplication = build_app()
    app.setQuitOnLastWindowClosed(False)

    overlay = make_overlay()
    shell = WebShell(objects={}, frameless=True)
    controller = OverlayController(shell, overlay)
    shell.channel.registerObject("overlay", controller)

    shell.resize(1000, 700)
    shell.move(120, 90)
    shell.show()

    page = shell.view.page()
    loop = QEventLoop()
    loaded = {"ok": False}

    def _on_load(ok: bool) -> None:
        loaded["ok"] = bool(ok)
        loop.quit()

    page.loadFinished.connect(_on_load)
    QTimer.singleShot(20000, loop.quit)
    page.load(QUrl.fromLocalFile(str(_HERE / "web" / "index.html")))
    loop.exec()
    check("PoC 页面加载完成", loaded["ok"])
    if not loaded["ok"]:
        return 1

    info(f"平台={_args.platform} devicePixelRatio={shell.devicePixelRatio()}")

    # ── 1. 桥与上报通路 ────────────────────────────
    flowing = wait_until(
        lambda: int(
            json.loads(run_js(page, "JSON.stringify(window.__JF_POC_DIAG__)") or "{}").get(
                "reports", 0
            )
        )
        > 0,
        timeout_s=15,
    )
    check(
        "页面通过 QWebChannel 上报通路已通",
        flowing,
        f"diag={run_js(page, 'JSON.stringify(window.__JF_POC_DIAG__)')}",
    )
    if not flowing:
        return 1

    # 预留框初始在首屏下方 → 原生浮层必须保持隐藏（不能留残影）
    pump(app, 0.6)
    check(
        "预留区在视口外时浮层保持隐藏",
        bool(controller.last_rect.get("visible")) is False and not overlay.isVisible(),
        f"last_rect={json.dumps(controller.last_rect)}",
    )

    # 滚到预留区
    scrolled_to = run_js(page, "window.__JF_POC__.scrollSlotIntoView()")
    pump(app, 0.8)
    reported = wait_until(lambda: bool(controller.last_rect.get("visible")), timeout_s=10)
    check(
        "滚到预留区后判定为可见并上报",
        reported,
        f"scrollTop={scrolled_to} last_rect={json.dumps(controller.last_rect)}",
    )
    if not reported:
        return 1

    pump(app)
    expected = expected_geo(shell, controller.last_rect)
    actual = (
        overlay.geometry().x(),
        overlay.geometry().y(),
        overlay.geometry().width(),
        overlay.geometry().height(),
    )
    check(
        "原生浮层精确贴合预留矩形（含 dpr=1.25 场景）",
        near(actual, expected),
        f"浮层={actual} 期望={expected}",
    )
    check("浮层已显示", overlay.isVisible() is True)

    # ── 2. 移动主窗口后跟随 ────────────────────────
    shell.move(shell.x() + 140, shell.y() + 70)
    pump(app, 1.0)
    run_js(page, "window.__JF_POC__.report()")
    pump(app, 0.5)
    expected = expected_geo(shell, controller.last_rect)
    actual = (
        overlay.geometry().x(),
        overlay.geometry().y(),
        overlay.geometry().width(),
        overlay.geometry().height(),
    )
    check("主窗口移动后浮层跟随", near(actual, expected), f"浮层={actual} 期望={expected}")

    # ── 3. 缩放主窗口后跟随 ────────────────────────
    shell.resize(1180, 820)
    pump(app, 1.2)
    run_js(page, "window.__JF_POC__.report()")
    pump(app, 0.5)
    expected = expected_geo(shell, controller.last_rect)
    actual = (
        overlay.geometry().x(),
        overlay.geometry().y(),
        overlay.geometry().width(),
        overlay.geometry().height(),
    )
    check("主窗口缩放后浮层跟随且尺寸一致", near(actual, expected), f"浮层={actual} 期望={expected}")
    check(
        "浮层尺寸等于预留矩形尺寸（480×270）",
        abs(overlay.width() - 480) <= TOLERANCE and abs(overlay.height() - 270) <= TOLERANCE,
        f"{overlay.width()}x{overlay.height()}",
    )

    # ── 4. 页面滚动后跟随 ──────────────────────────
    run_js(page, "window.__JF_POC__.scrollTo(120)")
    pump(app, 1.0)
    run_js(page, "window.__JF_POC__.report()")
    pump(app, 0.5)
    scrolled = int(run_js(page, "window.__JF_POC__.scrollTop()") or 0)
    expected = expected_geo(shell, controller.last_rect)
    actual = (
        overlay.geometry().x(),
        overlay.geometry().y(),
        overlay.geometry().width(),
        overlay.geometry().height(),
    )
    check(
        f"页面滚动 {scrolled}px 后浮层跟随",
        scrolled > 0 and near(actual, expected),
        f"浮层={actual} 期望={expected}",
    )

    # ── 5. 滚出视口后自动隐藏 ──────────────────────
    run_js(page, "window.__JF_POC__.scrollSlotIntoView()")
    pump(app, 0.5)
    run_js(page, "window.__JF_POC__.report()")
    pump(app, 0.4)
    check("滚回原位后浮层重新显示", overlay.isVisible() is True)

    run_js(page, "window.__JF_POC__.scrollTo(0)")
    pump(app, 0.6)
    run_js(page, "window.__JF_POC__.report()")
    pump(app, 0.6)
    check(
        "预留区滚出视口后浮层自动隐藏（不留原生画面残影）",
        overlay.isVisible() is False,
        f"visible={overlay.isVisible()} last_rect={json.dumps(controller.last_rect)}",
    )

    # ── 6. 肉眼证据：截「主窗口 ∪ 浮层」的屏幕区域（不拍桌面其它内容） ──
    try:
        run_js(page, "window.__JF_POC__.scrollSlotIntoView()")
        pump(app, 0.8)
        run_js(page, "window.__JF_POC__.report()")
        pump(app, 0.6)
        out_dir = _HERE / "out"
        out_dir.mkdir(exist_ok=True)
        # 取两者外接矩形（各留 8px 余量）
        left = min(shell.frameGeometry().x(), overlay.geometry().x()) - 8
        top = min(shell.frameGeometry().y(), overlay.geometry().y()) - 8
        right = max(
            shell.frameGeometry().x() + shell.frameGeometry().width(),
            overlay.geometry().x() + overlay.geometry().width(),
        ) + 8
        bottom = max(
            shell.frameGeometry().y() + shell.frameGeometry().height(),
            overlay.geometry().y() + overlay.geometry().height(),
        ) + 8
        screen = QApplication.primaryScreen()
        shot = screen.grabWindow(0, left, top, right - left, bottom - top)
        path = out_dir / "overlay_on_screen.png"
        shot.save(str(path))
        info(f"整屏截图（仅主窗口 ∪ 浮层区域）：{path}  {shot.width()}x{shot.height()}")
    except Exception as exc:  # noqa: BLE001 - 截图只是证据，失败不影响结论
        info(f"整屏截图失败（不影响结论）：{exc}")

    # ── 汇总 ──────────────────────────────────────
    overlay.close()
    shell.close()
    failed = [name for name, ok, _ in _results if not ok]
    print("\n" + "=" * 70)
    print(f"P0-4 自检：{len(_results) - len(failed)}/{len(_results)} 通过")
    if failed:
        print("失败项：")
        for name in failed:
            print(f"  - {name}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

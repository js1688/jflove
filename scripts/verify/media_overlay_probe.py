#!/usr/bin/env python3
"""
F40 媒体浮层几何取证探针（v1.8.2 / issue #10）—— X11 与 Wayland 双会话

## 作用

用**当前源码**里的生产实现 `jflove-desktop/src/ui/media_overlay.py::MediaOverlay`
搭一个可控舞台（默认 `QMainWindow` + `QWebEngineView`，可选真实 `WebShell`），
按需求 AC-289～293 的口径取证：

1. 浮层是不是 **QWebEngineView 的 native 子控件**（`parent() is view`、`isWindow()==False`）；
2. 上报矩形是不是**父控件相对坐标**（`setGeometry` 直接用 `rect`，不经过 `mapToGlobal`）；
3. 主窗移动/缩放后**相对 geometry 不变**（X11 上另外断言全局位置跟随、像素可见）；
4. 全屏 = **铺满视口**（`(0, 0, view.width(), view.height())`，不脱离父窗）；
5. `--mode window` 时可对照 v1.8.1 回退实现（顶层窗口 + 全局坐标），
   用于证明 `JFLOVE_MEDIA_OVERLAY=window` 开关仍然可用。

判据：进程退出码 0（全部断言通过）并落一份 JSON 证据；
X11 会话额外落前后两张截图（overlay 中心像素为红色 ⇒ 浮层真的可见且在最上层）。

## 用法（两平台都必须实际跑）

```bash
source scripts/verify/env.sh
PY=jflove-desktop/venv-linux/bin/python

# X11：Xvnc + kwin_x11（锁屏用户会话也不影响）
DISP=$(jf_start_xvnc :99); jf_start_wm "$DISP"
DISPLAY=$DISP QT_QPA_PLATFORM=xcb QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox" \
  $PY scripts/verify/media_overlay_probe.py --shell web --load-ui \
     --json-out /tmp/jf40-x11.json --screenshot-prefix /tmp/jf40-x11

# Wayland：kwin_wayland --virtual（结构性判据；不依赖全局坐标）
dbus-run-session -- bash -c '
  kwin_wayland --virtual --socket jfwl42 --width 1440 --height 900 & sleep 2
  WAYLAND_DISPLAY=jfwl42 QT_QPA_PLATFORM=wayland \
    QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox" \
    '"$PY"' scripts/verify/media_overlay_probe.py \
      --json-out /tmp/jf40-wayland.json --screenshot-prefix /tmp/jf40-wayland'
```

> `--shell web` 用真实 `WebShell`（无桥注入）承载页面，更接近生产装配；
> `--load-ui` 额外加载 `ui/dist` 构建产物（需先 `npm run build`）。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "jflove-desktop"
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

# QWebEngineView 必须在 QApplication 之前 import（与 src/ui/web_shell.py 同口径）
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

from PySide6.QtCore import QPoint, Qt, qVersion  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from src.ui.media_overlay import MODE_CHILD, MODE_WINDOW, MediaOverlay  # noqa: E402

#: 页面背景色（用于"浮层移走后原位置恢复页面颜色"的像素判据）
PAGE_BACKGROUND = QColor("#123456")
#: 浮层填充色（红：与页面背景反差极大，便于像素判定）
OVERLAY_FILL = QColor("#e53935")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="F40 媒体浮层几何取证探针")
    parser.add_argument(
        "--mode",
        choices=[MODE_CHILD, MODE_WINDOW],
        default=None,
        help="显式实现模式；缺省读 JFLOVE_MEDIA_OVERLAY（默认 child）",
    )
    parser.add_argument(
        "--shell",
        choices=["plain", "web"],
        default="plain",
        help="plain = QMainWindow + QWebEngineView；web = 真实 WebShell（生产外壳）",
    )
    parser.add_argument(
        "--load-ui",
        action="store_true",
        help="配合 --shell web：加载 ui/dist 构建产物（否则显示空白页）",
    )
    parser.add_argument(
        "--rect",
        default="60,80,360,220",
        help="上报给浮层的预留矩形 x,y,w,h（相对视口，默认 60,80,360,220）",
    )
    parser.add_argument(
        "--move", default="200,120", help="主窗移动量 dx,dy（默认 200,120）"
    )
    parser.add_argument(
        "--resize", default="120,80", help="主窗缩放宽高 dw,dh（默认 120,80）"
    )
    parser.add_argument("--json-out", default="", help="JSON 证据输出路径")
    parser.add_argument(
        "--screenshot-prefix",
        default="",
        help="截图前缀；会生成 <prefix>-before.png / -after-move.png / -fullscreen.png",
    )
    return parser.parse_args()


def _parse_ints(text: str, count: int) -> list[int]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) != count:
        raise SystemExit(f"参数格式错误：需要 {count} 个整数，实际 {text!r}")
    return [int(p) for p in parts]


def _make_stage(args: argparse.Namespace, app: QApplication):
    """搭舞台：返回 (window, view, notes)"""
    notes: list[str] = []
    if args.shell == "web":
        from src.ui.web_shell import WebShell

        window = WebShell(objects={}, frameless=True, title="JFLOVE F40 OVERLAY PROBE")
        window.resize(900, 600)
        if args.load_ui:
            loaded = bool(window.load_ui())
            notes.append(f"WebShell.load_ui()={loaded}")
        view = window.view
    else:
        window = QMainWindow()
        window.setWindowTitle("JFLOVE F40 OVERLAY PROBE")
        window.resize(900, 600)
        view = QWebEngineView(window)
        view.setHtml(
            "<html><body style='margin:0;background:#123456;color:#e2e8f0;"
            "font:20px sans-serif'>JFLOVE F40 OVERLAY PROBE</body></html>"
        )
        window.setCentralWidget(view)
    window.move(60, 40)
    window.show()
    QTest.qWait(1500)
    return window, view, notes


def _state(overlay: MediaOverlay, window) -> dict:
    view = overlay._get_view()
    geo = overlay.window.geometry()
    overlay_global = overlay.window.mapToGlobal(QPoint(0, 0))
    view_global = view.mapToGlobal(QPoint(0, 0))
    frame = window.frameGeometry()
    return {
        "mode": overlay.mode,
        "overlay_geometry": [geo.x(), geo.y(), geo.width(), geo.height()],
        "overlay_global": [overlay_global.x(), overlay_global.y()],
        "overlay_parent_is_view": overlay.window.parent() is view,
        "overlay_is_window": bool(overlay.window.isWindow()),
        "overlay_visible": bool(overlay.window.isVisible()),
        "overlay_fullscreen": bool(overlay.isFullScreen()),
        "view_size": [view.width(), view.height()],
        "view_global": [view_global.x(), view_global.y()],
        "window_pos": [window.x(), window.y()],
        "window_frame": [frame.x(), frame.y()],
    }


def _grab(path: Path, overlay: MediaOverlay, screen) -> dict:
    """抓整屏 + 采样浮层中心像素（X11 可判"可见且不被盖住"；Wayland 可能不可用）"""
    result: dict = {"path": None, "ok": False, "pixel_at_overlay_center": None}
    shot = screen.grabWindow(0)
    if shot.isNull():
        return result
    result["ok"] = bool(shot.save(str(path)))
    if not result["ok"]:
        return result
    result["path"] = str(path)
    image = shot.toImage()
    dpr = screen.devicePixelRatio() or 1.0
    center = overlay.window.mapToGlobal(
        QPoint(overlay.window.width() // 2, overlay.window.height() // 2)
    )
    x = int(round(center.x() * dpr))
    y = int(round(center.y() * dpr))
    if 0 <= x < image.width() and 0 <= y < image.height():
        color = image.pixelColor(x, y)
        result["pixel_at_overlay_center"] = [color.red(), color.green(), color.blue()]
        result["pixel_at_overlay_center_hex"] = color.name()
    return result


def _grab_named(prefix: Path, suffix: str, overlay: MediaOverlay, screen) -> dict:
    """按 `<prefix>-<suffix>.png` 命名抓一帧"""
    return _grab(prefix.with_name(f"{prefix.name}-{suffix}.png"), overlay, screen)


def _pixel_at(screen, x_global: int, y_global: int) -> list[int] | None:
    """采样整屏上某个全局坐标的像素（用于"移动后旧位置恢复页面颜色"判据）"""
    shot = screen.grabWindow(0)
    if shot.isNull():
        return None
    image = shot.toImage()
    dpr = screen.devicePixelRatio() or 1.0
    x = int(round(x_global * dpr))
    y = int(round(y_global * dpr))
    if not (0 <= x < image.width() and 0 <= y < image.height()):
        return None
    color = image.pixelColor(x, y)
    return [color.red(), color.green(), color.blue()]


def _is_red(pixel: list[int] | None) -> bool:
    if not pixel:
        return False
    red, green, blue = pixel
    return red >= 180 and green <= 110 and blue <= 110


def main() -> int:
    args = _parse_args()
    rect = dict(zip(("x", "y", "w", "h"), _parse_ints(args.rect, 4)))
    dx, dy = _parse_ints(args.move, 2)
    dw, dh = _parse_ints(args.resize, 2)
    display_rect = {"x": rect["x"], "y": rect["y"], "w": rect["w"], "h": rect["h"]}

    app = QApplication(sys.argv[:1])
    app.setApplicationName("jf40-overlay-probe")

    window, view, notes = _make_stage(args, app)
    overlay = MediaOverlay(get_view=lambda: view, publish_event=None, mode=args.mode)

    tmpdir = Path(tempfile.mkdtemp(prefix="jf40-overlay-probe-"))
    image_path = tmpdir / "overlay-fill.png"
    pixmap = QPixmap(display_rect["w"], display_rect["h"])
    pixmap.fill(OVERLAY_FILL)
    if not pixmap.save(str(image_path)):
        print("[probe] 无法生成图片样本", file=sys.stderr)
        return 2

    overlay.setRect(json.dumps({**display_rect, "visible": True}))
    QTest.qWait(300)
    open_result = json.loads(
        overlay.open(json.dumps({"kind": "image", "local_path": str(image_path)}))
    )
    QTest.qWait(900)

    checks: list[dict] = []

    def check(name: str, ok: bool, detail: object) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    screen = QApplication.primaryScreen()
    platform = app.platformName()
    is_x11 = platform in ("xcb", "x11")
    prefix = Path(args.screenshot_prefix) if args.screenshot_prefix else None
    screenshots: dict = {}

    def overlay_center() -> QPoint:
        return overlay.window.mapToGlobal(
            QPoint(overlay.window.width() // 2, overlay.window.height() // 2)
        )

    def wait_pixel(predicate, timeout_ms: int = 8000) -> tuple[bool, int]:
        """
        轮询屏幕像素直到条件成立。

        为什么需要：Xvnc + llvmpipe 下原生子窗口/窗口搬移在屏幕上生效可能比 Qt
        的几何更新滞后数百毫秒（实测 300ms 内仍可能看到旧位置），固定 sleep 会假红。
        """
        waited = 0
        while waited <= timeout_ms:
            if predicate():
                return True, waited
            QTest.qWait(200)
            waited += 200
        return False, waited

    before = _state(overlay, window)
    check("media_opened", bool(open_result.get("ok")), open_result)
    check("overlay_visible", before["overlay_visible"], before["overlay_visible"])
    check(
        "mode_as_requested",
        before["mode"] == (args.mode or before["mode"]),
        {"expected": args.mode, "actual": before["mode"]},
    )

    if is_x11:
        center = overlay_center()
        painted, waited = wait_pixel(
            lambda: _is_red(_pixel_at(screen, center.x(), center.y()))
        )
        check(
            "overlay_painted_on_screen",
            painted,
            {"waited_ms": waited, "pixel": _pixel_at(screen, center.x(), center.y())},
        )

    if prefix is not None:
        screenshots["before"] = _grab_named(prefix, "before", overlay, screen)

    if before["mode"] == MODE_CHILD:
        check("child_parent_is_view", before["overlay_parent_is_view"], before)
        check("child_not_toplevel_window", before["overlay_is_window"] is False, before)
        check(
            "geometry_is_parent_relative",
            before["overlay_geometry"] == [rect["x"], rect["y"], rect["w"], rect["h"]],
            before["overlay_geometry"],
        )
        check(
            "native_window_attribute",
            overlay.window.testAttribute(Qt.WidgetAttribute.WA_NativeWindow),
            "WA_NativeWindow",
        )

    # ── 拖动主窗 ────────────────────────────────────
    old_center = overlay_center()
    old_frame = window.frameGeometry()
    window.move(window.x() + dx, window.y() + dy)
    QTest.qWait(300)
    if before["mode"] == MODE_WINDOW:
        # 回退实现是独立顶层窗口，靠外壳几何事件驱动重贴合；plain 舞台没有外壳，
        # 这里显式模拟 `WebShell.moveEvent → refresh_geometry` 的接线（--shell web 时重复调用无害）。
        overlay.refresh_geometry()
        QTest.qWait(300)
    if is_x11:
        new_center = overlay_center()
        followed, waited = wait_pixel(
            lambda: _is_red(_pixel_at(screen, new_center.x(), new_center.y()))
            and not _is_red(_pixel_at(screen, old_center.x(), old_center.y()))
        )
        check("overlay_followed_on_screen", followed, {"waited_ms": waited})
    after_move = _state(overlay, window)
    frame_delta = [
        after_move["window_frame"][0] - old_frame.x(),
        after_move["window_frame"][1] - old_frame.y(),
    ]
    global_delta = [
        after_move["overlay_global"][0] - before["overlay_global"][0],
        after_move["overlay_global"][1] - before["overlay_global"][1],
    ]
    check(
        "window_moved",
        frame_delta == [dx, dy],
        {"requested": [dx, dy], "frame_delta": frame_delta},
    )

    if before["mode"] == MODE_CHILD:
        check(
            "relative_geometry_unchanged_after_move",
            after_move["overlay_geometry"] == before["overlay_geometry"],
            {"before": before["overlay_geometry"], "after": after_move["overlay_geometry"]},
        )
        if platform in ("xcb", "x11"):
            check(
                "global_follows_window_x11",
                all(abs(a - b) <= 1 for a, b in zip(global_delta, frame_delta)),
                {"global_delta": global_delta, "frame_delta": frame_delta},
            )
            check(
                "global_offset_matches_rect",
                after_move["overlay_global"][0] - after_move["view_global"][0] == rect["x"]
                and after_move["overlay_global"][1] - after_move["view_global"][1] == rect["y"],
                {
                    "overlay_global": after_move["overlay_global"],
                    "view_global": after_move["view_global"],
                },
            )

    if prefix is not None:
        screenshots["after_move"] = _grab_named(prefix, "after-move", overlay, screen)
    if is_x11:
        moved_center = overlay_center()
        if prefix is not None:
            check(
                "pixel_visible_before_move",
                _is_red(screenshots.get("before", {}).get("pixel_at_overlay_center")),
                screenshots.get("before", {}).get("pixel_at_overlay_center"),
            )
            check(
                "pixel_visible_after_move",
                _is_red(screenshots.get("after_move", {}).get("pixel_at_overlay_center")),
                screenshots.get("after_move", {}).get("pixel_at_overlay_center"),
            )
        check(
            "overlay_really_moved",
            (moved_center.x(), moved_center.y()) != (old_center.x(), old_center.y()),
            {
                "old_center": [old_center.x(), old_center.y()],
                "new_center": [moved_center.x(), moved_center.y()],
            },
        )
        old_pixel = _pixel_at(screen, old_center.x(), old_center.y())
        check(
            "old_position_repainted",
            old_pixel is not None and not _is_red(old_pixel),
            old_pixel,
        )

    # ── 缩放主窗 ────────────────────────────────────
    window.resize(window.width() + dw, window.height() + dh)
    QTest.qWait(700)
    after_resize = _state(overlay, window)
    if before["mode"] == MODE_CHILD:
        check(
            "relative_geometry_unchanged_after_resize",
            after_resize["overlay_geometry"] == [rect["x"], rect["y"], rect["w"], rect["h"]],
            after_resize["overlay_geometry"],
        )
        check(
            "view_size_followed",
            after_resize["view_size"][0] >= before["view_size"][0]
            and after_resize["view_size"][1] >= before["view_size"][1],
            {"before": before["view_size"], "after": after_resize["view_size"]},
        )

    # ── 全屏（子控件模式 = 铺满视口） ────────────────
    overlay.setFullScreen(True)
    QTest.qWait(600)
    fullscreen = _state(overlay, window)
    check(
        "fullscreen_flag",
        fullscreen["overlay_fullscreen"] is True,
        fullscreen["overlay_fullscreen"],
    )
    if before["mode"] == MODE_CHILD:
        check(
            "fullscreen_fills_view",
            fullscreen["overlay_geometry"]
            == [0, 0, fullscreen["view_size"][0], fullscreen["view_size"][1]],
            {"geometry": fullscreen["overlay_geometry"], "view": fullscreen["view_size"]},
        )
        check(
            "fullscreen_stays_child",
            fullscreen["overlay_is_window"] is False and fullscreen["overlay_parent_is_view"],
            {"is_window": fullscreen["overlay_is_window"],
             "parent_is_view": fullscreen["overlay_parent_is_view"]},
        )
    else:
        check(
            "legacy_fullscreen_is_toplevel",
            fullscreen["overlay_is_window"] is True,
            fullscreen["overlay_is_window"],
        )

    if is_x11:
        fs_center = overlay_center()
        fs_painted, fs_waited = wait_pixel(
            lambda: _is_red(_pixel_at(screen, fs_center.x(), fs_center.y()))
        )
        check("fullscreen_painted_on_screen", fs_painted, {"waited_ms": fs_waited})

    if prefix is not None:
        screenshots["fullscreen"] = _grab_named(prefix, "fullscreen", overlay, screen)

    overlay.setFullScreen(False)
    QTest.qWait(600)
    restored = _state(overlay, window)
    if before["mode"] == MODE_CHILD:
        check(
            "exit_fullscreen_restores_rect",
            restored["overlay_geometry"] == [rect["x"], rect["y"], rect["w"], rect["h"]],
            restored["overlay_geometry"],
        )

    if before["mode"] == MODE_WINDOW:
        # 回退实现：独立顶层窗口 + 全局坐标
        check(
            "legacy_is_toplevel_window",
            before["overlay_is_window"] is True,
            before["overlay_is_window"],
        )
        check("legacy_no_parent", overlay.window.parent() is None, str(overlay.window.parent()))
        check(
            "legacy_geometry_size",
            before["overlay_geometry"][2:] == [rect["w"], rect["h"]],
            before["overlay_geometry"],
        )
        if platform in ("xcb", "x11"):
            check(
                "legacy_global_matches_view_offset",
                before["overlay_global"][0] - before["view_global"][0] == rect["x"]
                and before["overlay_global"][1] - before["view_global"][1] == rect["y"],
                {"overlay_global": before["overlay_global"], "view_global": before["view_global"]},
            )

    ok = all(item["ok"] for item in checks)
    payload = {
        "ok": ok,
        "platform": platform,
        "qt_version": qVersion(),
        "shell": args.shell,
        "requested": {"rect": display_rect, "move": [dx, dy], "resize": [dw, dh]},
        "notes": notes,
        "states": {
            "before": before,
            "after_move": after_move,
            "after_resize": after_resize,
            "fullscreen": fullscreen,
            "restored": restored,
        },
        "screenshots": screenshots,
        "checks": checks,
    }
    print(
        f"[probe] platform={platform} qt={qVersion()} shell={args.shell} mode={before['mode']} "
        f"ok={ok} checks={len(checks)} failed={sum(1 for c in checks if not c['ok'])}"
    )
    for item in checks:
        if not item["ok"]:
            print(f"[probe] FAIL {item['name']}: {item['detail']}", file=sys.stderr)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[probe] JSON → {out}")
    else:
        print("[probe] " + json.dumps(payload, ensure_ascii=False))

    overlay.close()
    window.hide()
    QTest.qWait(200)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

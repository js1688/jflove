"""
P0-4 PoC：媒体原生浮层（把 OS 解码器的画面贴进 Web 页面）

## 要回答的问题

N7 文件管理的预览必须用**本地解码**（`StreamProxy` + `QMediaPlayer` / Qt 图像解码），
不能学 Web 端那套 `<video>` + Chromium 解码。于是核心问题是：

  **原生画面（QVideoWidget / QLabel）能不能准确贴在 Web 页面里预留的矩形上，
  并在窗口移动 / 缩放 / 页面滚动时保持贴合？**

## 难点与对策

- `QWebEngineView` 内部是 Chromium 的原生渲染窗口，**普通子控件会被它盖住**；
- 因此浮层用**独立顶层窗口**（`Qt.Tool | FramelessWindowHint`），由 Python 主动摆放；
- 页面侧用 `getBoundingClientRect()` 上报预留矩形的 CSS 像素坐标，
  经 `view.mapToGlobal()` 换算成屏幕坐标后 `setGeometry`；
- CSS 像素与 Qt 的设备无关像素（DIP）在 Chromium 里一一对应，
  本机 dpr=1.25，正好用来验证缩放是否正确（本文件不做任何 dpr 换算，若对齐即证明无需换算）。

## 这个 PoC 不做

不播放真实媒体（那部分原桌面端已跑通：`StreamProxy` + `QMediaPlayer` 是既有能力），
只验证**浮层贴合与同步**这一唯一未知量。
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POC_DIR = os.path.dirname(_HERE)
_DESKTOP_ROOT = os.path.dirname(_POC_DIR)
for _p in (_DESKTOP_ROOT, _POC_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import QObject, QPoint, QSize, Qt, Slot  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPalette  # noqa: E402
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget  # noqa: E402

from src.ui.web_shell import WebShell, build_app  # noqa: E402

#: 浮层窗口（模拟"原生渲染区"：真实实现里这里是 QVideoWidget / 显示 QPixmap 的 QLabel）
OVERLAY_WIDGET: QWidget | None = None


class OverlayController(QObject):
    """
    把页面上报的矩形换算成屏幕坐标，并摆放原生浮层。

    `rectJson` 由页面给出：`{"x":..,"y":..,"w":..,"h":..,"visible":bool}`，
    坐标是**相对视口的 CSS 像素**。
    """

    def __init__(self, window: WebShell, overlay: QWidget) -> None:
        super().__init__()
        self._window = window
        self._overlay = overlay
        self.last_rect: dict = {}
        self.applied_geo: tuple[int, int, int, int] | None = None

    @Slot(str)
    def setTargetRect(self, rect_json: str) -> None:
        """页面调用：上报预留矩形（相对视口，CSS 像素）"""
        try:
            rect = json.loads(rect_json)
        except (TypeError, ValueError):
            return
        self.last_rect = rect

        if not rect.get("visible"):
            self._overlay.hide()
            self.applied_geo = None
            return

        view = self._window.view
        dpr = self._window.devicePixelRatio()

        # 视口内的 CSS 像素 → 视口控件的本地坐标（**不做 dpr 换算**：Chromium 的 CSS
        # 像素与 Qt 的设备无关像素一致；若此处对齐，即证明换算方式正确）
        local = QPoint(int(round(rect["x"])), int(round(rect["y"])))
        top_left = view.mapToGlobal(local)
        size = QSize(max(1, int(round(rect["w"]))), max(1, int(round(rect["h"]))))

        self._overlay.setGeometry(top_left.x(), top_left.y(), size.width(), size.height())
        self._overlay.show()
        self._overlay.raise_()
        self.applied_geo = (
            top_left.x(),
            top_left.y(),
            size.width(),
            size.height(),
        )
        _ = dpr  # 仅为说明"刻意不使用"，见类文档

    @Slot(result=str)
    def debugInfo(self) -> str:
        """自检用：回传浮层实际屏幕几何与页面最近一次上报值"""
        geo = self._overlay.geometry()
        return json.dumps(
            {
                "overlay": [geo.x(), geo.y(), geo.width(), geo.height()],
                "visible": self._overlay.isVisible(),
                "last_rect": self.last_rect,
                "window_geo": [
                    self._window.geometry().x(),
                    self._window.geometry().y(),
                    self._window.geometry().width(),
                    self._window.geometry().height(),
                ],
            },
            ensure_ascii=False,
        )


def make_overlay() -> QWidget:
    """
    创建模拟的原生渲染浮层。

    真实实现里这里是 `QVideoWidget`（视频）或显示 `QPixmap` 的 `QLabel`（图片），
    本 PoC 用一个高对比度色块 + 文字，便于肉眼与断言确认"贴合"。
    """
    overlay = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    overlay.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    palette = overlay.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#22d3ee"))
    overlay.setPalette(palette)
    overlay.setAutoFillBackground(True)

    layout = QVBoxLayout(overlay)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel("NATIVE OVERLAY（OS 解码器画面）")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    font = QFont()
    font.setPointSize(11)
    font.setBold(True)
    label.setFont(font)
    label.setStyleSheet("color: #083344;")
    layout.addWidget(label)
    return overlay


def main() -> int:
    """PoC 入口：无边框外壳 + 原生浮层"""
    from PySide6.QtCore import QUrl

    app = build_app()
    overlay = make_overlay()
    global OVERLAY_WIDGET
    OVERLAY_WIDGET = overlay

    shell = WebShell(objects={}, frameless=True)
    controller = OverlayController(shell, overlay)
    shell.channel.registerObject("overlay", controller)

    shell.resize(1000, 700)
    shell.show()
    # 注意：加载的是 PoC 自带页面，**不是**真实前端产物
    shell.view.load(QUrl.fromLocalFile(os.path.join(_HERE, "web", "index.html")))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

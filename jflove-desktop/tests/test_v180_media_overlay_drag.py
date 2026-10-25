"""
v1.8.0 issue #7 回归：拖动主窗后原生视频 surface 必须被显式重绘。

背景：`QVideoWidget` 是原生子窗口，`setGeometry` 只移动窗口，不保证合成器
重新绘制视频 surface；Linux 上表现为拖拽时画面不跟随/黑屏（issue #7）。
本用例用假窗口/假 video 验证 `_apply_geometry()` 会在几何变化后触发
`update()/repaint()`（不需要真实播放器）。

v1.8.2（F40）后默认实现改为 native 子控件；本文件守住的是**保留的回退实现**
（`JFLOVE_MEDIA_OVERLAY=window` 的顶层窗口 + `mapToGlobal` 全局坐标），
故假对象显式声明 `mode = MODE_WINDOW`，断言口径与 v1.8.0 完全一致。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint

from src.ui.media_overlay import MODE_WINDOW, MediaOverlay


class _FakeView:
    def mapToGlobal(self, point: QPoint) -> QPoint:
        return QPoint(point.x() + 100, point.y() + 200)


class _FakeVideo:
    def __init__(self) -> None:
        self.updates = 0
        self.repaints = 0

    def update(self) -> None:
        self.updates += 1

    def repaint(self) -> None:
        self.repaints += 1


class _FakeWindow:
    def __init__(self) -> None:
        self.geometry: tuple[int, int, int, int] | None = None
        self.updates = 0

    def setGeometry(self, x: int, y: int, w: int, h: int) -> None:  # noqa: N802
        self.geometry = (x, y, w, h)

    def isVisible(self) -> bool:  # noqa: N802
        return True

    def update(self) -> None:
        self.updates += 1


def _overlay() -> tuple[MediaOverlay, _FakeVideo, _FakeWindow]:
    overlay = MediaOverlay.__new__(MediaOverlay)
    overlay.mode = MODE_WINDOW  # type: ignore[attr-defined]  # 回退实现：全局坐标
    overlay._get_view = lambda: _FakeView()  # type: ignore[attr-defined]
    overlay._rect = {  # type: ignore[attr-defined]
        "x": 10, "y": 20, "w": 300, "h": 200, "visible": True,
    }
    overlay._kind = "video"  # type: ignore[attr-defined]
    video = _FakeVideo()
    window = _FakeWindow()
    overlay.video = video  # type: ignore[attr-defined]
    overlay.window = window  # type: ignore[attr-defined]
    return overlay, video, window


def test_apply_geometry_updates_video_surface_after_move() -> None:
    """几何更新后必须 update+repaint 视频 surface（issue #7 根因）。"""
    overlay, video, window = _overlay()
    overlay._apply_geometry()
    assert window.geometry == (110, 220, 300, 200)
    assert video.updates == 1
    assert video.repaints == 1
    assert window.updates == 1


def test_apply_geometry_skips_repaint_for_image() -> None:
    """图片预览不需要刷视频 surface（避免无意义重绘）。"""
    overlay, video, _window = _overlay()
    overlay._kind = "image"  # type: ignore[attr-defined]
    overlay._apply_geometry()
    assert video.updates == 0
    assert video.repaints == 0

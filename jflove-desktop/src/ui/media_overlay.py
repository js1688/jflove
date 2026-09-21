"""
原生媒体浮层（N7-b）：把 OS 解码器的画面贴进 Web 页面预留矩形

## 为什么不用 Web 的 `<video>` / `<img>`

用户明确要求：**解码能力必须吊起本地解码**，底层沿用原桌面端那套
（`StreamProxy` + `QMediaPlayer` / Qt 图像解码）。Chromium 内置解码器格式受限，
而 OS 解码器（Windows: Media Foundation；Linux: GStreamer）兼容性强得多。

## 方案（P0-4 已实测验证，12/12 断言通过）

    Web 画播放器外壳（工具栏/进度条），中间留一个空矩形
      ↓ 上报 getBoundingClientRect()
    Python：StreamProxy（127.0.0.1 + 一次性 token + Range 206）
            + QMediaPlayer（视频/音频）/ QLabel 显示 QPixmap（图片）
      ↓ 独立顶层原生窗口 setGeometry()（QWebEngineView 是原生窗口，子控件会被盖住）
    原生画面贴在预留矩形上；窗口移动/缩放/滚动全程同步

实测要点：**CSS 像素与 Qt 设备无关像素一一对应，无需任何 dpr 换算**
（本机 dpr=1.25 下贴合误差仅 1px 舍入）。
"""

from __future__ import annotations

import os
from PySide6.QtCore import QObject, QPoint, QSize, Qt, QUrl, Slot
from PySide6.QtGui import QColor, QPalette, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 图片预览时限制解码尺寸（避免超大图吃内存；等比缩放，不放大）
MAX_IMAGE_SIDE = 4096


class MediaOverlay(QObject):
    """
    原生媒体浮层：一个独立的无边框顶层窗口 + 播放/显示内容。

    :param get_view: 返回承载页面的 QWebEngineView（用于把视口坐标换算成屏幕坐标）
    :param publish_event: 向渲染层推事件的回调（进度/状态）
    """

    def __init__(self, get_view, publish_event=None) -> None:
        super().__init__()
        self._get_view = get_view
        self._publish = publish_event

        # 独立顶层窗口：QWebEngineView 是原生窗口，普通子控件会被它盖住
        self.window = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        palette = self.window.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#000000"))
        self.window.setPalette(palette)
        self.window.setAutoFillBackground(True)

        layout = QVBoxLayout(self.window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.video = QVideoWidget(self.window)
        layout.addWidget(self.video)
        self.image = QLabel(self.window)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setStyleSheet("background: #0b1220;")
        self.image.hide()
        layout.addWidget(self.image)

        self.player = QMediaPlayer(self.window)
        self.audio = QAudioOutput(self.window)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(self._on_error)
        # 诊断用：媒体加载状态（定位"点了视频没播放"这类问题；不含任何用户内容）
        self.player.mediaStatusChanged.connect(self._on_media_status)

        self._rect: dict = {}
        self._kind: str = ""  # video / audio / image
        self._visible = False
        self._fullscreen = False

    # ── 渲染层调用（桥，主线程） ────────────────────

    @Slot(str, result=str)
    def open(self, params_json: str) -> str:
        """
        打开一个媒体：视频/音频交给 `QMediaPlayer`，图片交给 Qt 解码。

        :param params_json: `{kind, url, name}`；`url` 是 StreamProxy 的本地地址
        :returns: 状态 JSON
        """
        import json

        params = json.loads(params_json or "{}")
        kind = str(params.get("kind", "video"))
        url = str(params.get("url", ""))
        self._kind = kind

        if kind == "image":
            self.player.stop()
            self.video.hide()
            pixmap = QPixmap()
            path = params.get("local_path") or ""
            if path and os.path.exists(path):
                pixmap.load(path)
            if pixmap.isNull():
                self.window.hide()
                return json.dumps({"ok": False, "error": "图片解码失败"}, ensure_ascii=False)
            if max(pixmap.width(), pixmap.height()) > MAX_IMAGE_SIDE:
                pixmap = pixmap.scaled(
                    MAX_IMAGE_SIDE,
                    MAX_IMAGE_SIDE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self.image.setPixmap(pixmap)
            self.image.show()
            self._apply_geometry()
            self.window.show()
            self.window.raise_()
            return json.dumps(
                {"ok": True, "kind": kind, "w": pixmap.width(), "h": pixmap.height()}
            )

        # 视频 / 音频：StreamProxy 地址交给 QMediaPlayer（OS 解码器）
        self.image.hide()
        self.video.show()
        self.player.setSource(QUrl(url))
        self.player.play()
        self._apply_geometry()
        self.window.show()
        self.window.raise_()
        return json.dumps({"ok": True, "kind": kind, "url": url})

    @Slot(str)
    def setRect(self, rect_json: str) -> None:
        """渲染层上报预留矩形（相对视口，CSS 像素）"""
        import json

        try:
            self._rect = json.loads(rect_json or "{}")
        except (TypeError, ValueError):
            return
        self._apply_geometry()

    @Slot(bool)
    def setVisible(self, visible: bool) -> None:
        """渲染层要求显示/隐藏浮层（例如滚出视口时）"""
        self._visible = bool(visible)
        if not self._visible:
            self.window.hide()
            self._force_repaint()
        elif self._rect:
            self.window.show()
            self.window.raise_()

    @Slot()
    def play(self) -> None:
        self.player.play()

    @Slot()
    def pause(self) -> None:
        self.player.pause()

    @Slot(int)
    def seek(self, position_ms: int) -> None:
        self.player.setPosition(int(position_ms))

    @Slot(bool)
    def setFullScreen(self, fullscreen: bool) -> None:
        """
        全屏：把**原生窗口**铺满屏幕（画面是原生渲染的，因此"全屏"就是铺满屏幕）。

        退出全屏后重新按页面矩形贴合（否则会停在屏幕尺寸上）。
        """
        self._fullscreen = bool(fullscreen)
        if self._fullscreen:
            self.window.showFullScreen()
        else:
            self.window.showNormal()
            self._apply_geometry()
            self.window.show()
            self.window.raise_()

    @Slot(result=bool)
    def isFullScreen(self) -> bool:
        """当前是否全屏（供渲染层同步按钮状态）"""
        return bool(getattr(self, "_fullscreen", False))

    @Slot(int)
    def setVolume(self, volume_0_100: int) -> None:
        self.audio.setVolume(max(0.0, min(1.0, volume_0_100 / 100.0)))

    @Slot(result=str)
    def state(self) -> str:
        """当前播放状态（供渲染层同步 UI）"""
        import json

        return json.dumps(
            {
                "position": self.player.position(),
                "duration": self.player.duration(),
                "playing": self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                "kind": self._kind,
            },
            ensure_ascii=False,
        )

    @Slot()
    def close(self) -> None:
        """关闭预览：停止播放并释放 StreamProxy 使用的连接"""
        self.player.stop()
        self.player.setSource(QUrl())
        self.window.hide()
        self._kind = ""
        self._rect = {}
        self._force_repaint()

    # ── 内部 ──────────────────────────────────────

    def _force_repaint(self) -> None:
        """
        强制让 Chromium 重绘。

        原生浮层是**独立顶层窗口**，它覆盖的那块区域在浮层隐藏后 Chromium
        **并不知道需要重绘** —— 表现为"从预览返回后，文件列表那块是空白/旧画面，
        滚动一下才出现"（用户实测反馈）。这里显式请求重绘，并让渲染层派发一次
        `resize`（触发重新布局与合成），把被盖住的区域补画回来。
        """
        view = self._get_view()
        if view is None:
            return
        try:
            view.update()
            view.repaint()
            view.page().runJavaScript("window.dispatchEvent(new Event('resize'));")
        except Exception as exc:  # noqa: BLE001 - 重绘失败不影响功能，仅观感
            logger.debug("强制重绘失败（忽略）：%s", exc)

    def _apply_geometry(self) -> None:
        """按页面矩形摆放原生浮层（不做 dpr 换算，见模块文档）"""
        rect = self._rect
        if not rect or not rect.get("visible", True):
            if rect and not rect.get("visible", True):
                self.window.hide()
            return
        view = self._get_view()
        if view is None:
            return
        top_left = view.mapToGlobal(
            QPoint(int(round(rect.get("x", 0))), int(round(rect.get("y", 0))))
        )
        size = QSize(max(1, int(round(rect.get("w", 1)))), max(1, int(round(rect.get("h", 1)))))
        self.window.setGeometry(top_left.x(), top_left.y(), size.width(), size.height())

    def _on_position(self, position: int) -> None:
        if self._publish and self._visible:
            self._publish("media.position", {"position": position})

    def _on_duration(self, duration: int) -> None:
        if self._publish:
            self._publish("media.duration", {"duration": duration})

    def _on_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if self._publish:
            self._publish(
                "media.state",
                {"playing": state == QMediaPlayer.PlaybackState.PlayingState},
            )

    def _on_error(self, error: QMediaPlayer.Error, error_string: str = "") -> None:
        logger.warning("媒体播放错误: %s %s", error, error_string)
        if self._publish:
            self._publish("media.error", {"error": error_string or str(error)})

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        """记录加载状态：`LoadedMedia`/`BufferedMedia` 说明 OS 解码器认了这个流"""
        logger.info(
            "媒体状态: %s（error=%r）",
            status.name if hasattr(status, "name") else status,
            self.player.errorString(),
        )

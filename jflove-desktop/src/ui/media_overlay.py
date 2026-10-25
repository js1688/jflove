"""
原生媒体浮层：把 OS 解码器的画面贴进 Web 页面预留矩形（v1.8.2 / F40 子控件化）

## 为什么不用 Web 的 `<video>` / `<img>`

用户明确要求：**解码能力必须吊起本地解码**，底层沿用原桌面端那套
（`StreamProxy` + `QMediaPlayer` / Qt 图像解码）。Chromium 内置解码器格式受限，
而 OS 解码器（Windows: Media Foundation；Linux: GStreamer）兼容性强得多。

## 结构（v1.8.2 F40：native 子控件，坐标相对父控件）

    Web 画播放器外壳（工具栏/进度条），中间留一个空矩形
      ↓ 上报 getBoundingClientRect()
    Python：StreamProxy（127.0.0.1 + 一次性 token + Range 206）
            + QMediaPlayer（视频/音频）/ QLabel 显示 QPixmap（图片）
      ↓ MediaOverlay.window = QWidget(view)，即 QWebEngineView 的 native 子控件
    原生画面贴在预留矩形上；主窗移动/缩放/全屏/托盘恢复全部由 Qt 父子关系跟随，
    不再需要 `mapToGlobal` 全局坐标反算，也不再有"主窗动、浮层慢一帧"的错位。

设计依据：`文档记录/设计文档/v1.8.2.md` §4.1（含 P2 原型实测：PySide6 6.11 +
X11/Wayland 下 native 子控件可见且跟随；`QWindow.setParent()` 返回 false 不可用）。

## 坐标口径

渲染层上报的是**相对视口**的 CSS 像素，而视口就是 `QWebEngineView` 的客户区，
因此子控件模式下 `rect` 可直接作为 `setGeometry(x, y, w, h)` 使用。
**CSS 像素与 Qt 设备无关像素一一对应，无需任何 dpr 换算**（P0-4 实测，dpr=1.25
下贴合误差仅 1px 舍入）。

## 回退：`JFLOVE_MEDIA_OVERLAY=window`

排障用开关：置为 `window` 时恢复 v1.8.1 的「独立无边框顶层窗口 + `mapToGlobal`
全局坐标」实现（某些平台的合成器若不支持 native 子控件，可临时切回）。
子控件创建失败（`get_view()` 尚不可用）时也会自动回退并记 warning，
保证「不得出现无法播放/无法关闭」（需求 F40.2）。
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

#: 浮层实现模式的环境变量（排障回退用；默认 `child`）
OVERLAY_MODE_ENV = "JFLOVE_MEDIA_OVERLAY"

#: F40 默认实现：QWebEngineView 的 native 子控件（父控件相对坐标）
MODE_CHILD = "child"

#: v1.8.1 旧实现：独立顶层窗口（全局坐标反算），仅排障回退
MODE_WINDOW = "window"


def resolve_overlay_mode(explicit: str | None = None) -> str:
    """
    解析浮层实现模式。

    :param explicit: 显式模式（构造参数/测试注入）；`None` 时读环境变量
    :returns: `MODE_CHILD` 或 `MODE_WINDOW`；**只认 `window`**，其余一律回落 `child`
    """
    raw = explicit if explicit is not None else os.environ.get(OVERLAY_MODE_ENV, "")
    return MODE_WINDOW if str(raw).strip().lower() == MODE_WINDOW else MODE_CHILD


class MediaOverlay(QObject):
    """
    原生媒体浮层：一块贴住页面预留矩形的原生画面 + 播放/显示内容。

    :param get_view: 返回承载页面的 QWebEngineView（子控件模式的父控件；
        顶层窗口模式用它把视口坐标换算成屏幕坐标）
    :param publish_event: 向渲染层推事件的回调（进度/状态）
    :param mode: 显式指定实现模式（`child`/`window`，默认读 `JFLOVE_MEDIA_OVERLAY`）

    ## 不变式：主窗隐藏期间，除 `open()` 外不得把浮层摆出来

    主窗隐藏（= 最小化到系统托盘）后，浮层必须留在隐藏状态，屏幕上不得残留画面
    （issue #3 / AC-12）。子控件模式下父窗隐藏时子控件本来就不可见，但**显示意图**
    仍需按下，否则恢复时会被渲染层迟到的 `setVisible(true)` 抢跑。因此：

    - `on_shell_hidden()` 置位 `_shell_hidden` 并隐藏窗口；
    - `setVisible(True)` / `setFullScreen(True)` 在此状态下**只记录意图、不显示窗口**；
    - `setVisible(False)` 照常隐藏（方向与不变式一致，无需拦截）；
    - **唯一例外是 `open()`**：那是用户在渲染层主动发起的"打开新预览"，
      视为显式意图，清 `_shell_hidden` 并正常显示；
    - 真正恢复显示只发生在 `refresh_geometry()` 的"关窗态恢复"分支里
      （由 `WebShell.showEvent` / 主窗几何事件驱动）。

    ## `_shell_hidden` 的准确语义（R5-T1：名实不符的澄清）

    `_shell_hidden` 表示"**主窗隐藏期间被压下的显示意图**"，
    **不是**"主窗当前是否隐藏"这个实时事实查询。差别在两个边界上：

    - **预览被 `close()` 之后它会长期停留在 `True`**（属**无害残留**，
      不表示主窗仍隐藏）：因为 `refresh_geometry()` 的恢复分支在
      `if not self._kind` 处就返回了 —— 此时没有媒体源可恢复，该分支不做任何
      显示动作，所以这个残留状态不会被外界观察到；
    - 它会在下一次"主窗隐藏 → 恢复"周期里**自然复位**：`on_shell_hidden()`
      再置一次 `True`，随后恢复分支在**真的把浮层恢复出来之前**把它清成 `False`
      （全屏路径与非全屏路径各一处）。此外 `open()`（用户显式打开新预览）
      也会直接清掉它。

    因此"无害"的判据是：**所有读取它的地方都同时校验了可恢复的前提**
    （有媒体源 `_kind`、有预留矩形 `_rect` 且 `visible` 不为假）。
    残留的 `True` 最多让下一次恢复多走一次无害的判定，绝不会凭空显示浮层。
    """

    def __init__(self, get_view, publish_event=None, mode: str | None = None) -> None:
        super().__init__()
        self._get_view = get_view
        self._publish = publish_event

        #: 实际生效的实现模式（子控件创建失败时会从 child 降级为 window）
        self.mode = resolve_overlay_mode(mode)
        self.window = self._build_window()

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
        #: 主窗隐藏期间**被压下的显示意图**（不是"主窗当前是否隐藏"的实时查询，
        #: 见类 docstring 的 R5-T1 澄清）。`on_shell_hidden()` 置位；
        #: `open()` 或 `refresh_geometry()` 的恢复分支复位 —— 且**只有真的恢复显示
        #: 了才复位**（M-5：提前消费该标志会导致"恢复永不发生"）。
        #: 预览 `close()` 后可能长期为 True，属**无害残留**（恢复分支会因 `_kind`
        #: 为空而提前返回，不产生任何显示动作）。
        self._shell_hidden = False

    def _build_window(self) -> QWidget:
        """
        创建承载画面的窗口：子控件（默认）或独立顶层窗口（回退）。

        子控件模式要求 `get_view()` 已返回一个 QWidget（`QWebEngineView`）；
        取不到时**自动回退**顶层窗口并记 warning（需求 F40.2：不得出现无法播放）。

        :returns: 已设置黑底/外观、但尚未创建子控件的窗口 QWidget
        """
        view = None
        if self.mode == MODE_CHILD:
            try:
                view = self._get_view()
            except Exception as exc:  # noqa: BLE001 - 取视口失败只影响浮层模式
                logger.warning("媒体浮层取视口失败，回退顶层窗口模式：%s", exc)
                view = None
            if not isinstance(view, QWidget):
                logger.warning(
                    "媒体浮层子控件创建失败（QWebEngineView 尚不可用），"
                    "自动回退顶层窗口模式（排障可用 %s=window 显式指定）",
                    OVERLAY_MODE_ENV,
                )
                self.mode = MODE_WINDOW

        if self.mode == MODE_CHILD:
            window = QWidget(view)
            # native 子控件：只有独立 native window 才不会被 QtWebEngine 的
            # 合成层盖住（P2 原型 X11/Wayland 实测通过）
            window.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            logger.info("媒体浮层模式：child（QWebEngineView 原生子控件，坐标相对父控件）")
        else:
            window = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            logger.info(
                "媒体浮层模式：window（独立顶层窗口 + 全局坐标；由 %s=window 回退）",
                OVERLAY_MODE_ENV,
            )

        palette = window.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#000000"))
        window.setPalette(palette)
        window.setAutoFillBackground(True)
        return window

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
        # 用户显式打开预览：无论主窗此前是否隐藏过，浮层都该正常显示
        self._shell_hidden = False

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
            self._show_overlay()
            return json.dumps(
                {"ok": True, "kind": kind, "w": pixmap.width(), "h": pixmap.height()}
            )

        # 视频 / 音频：StreamProxy 地址交给 QMediaPlayer（OS 解码器）
        self.image.hide()
        self.video.show()
        self.player.setSource(QUrl(url))
        self.player.play()
        self._apply_geometry()
        self._show_overlay()
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
        """
        渲染层要求显示/隐藏浮层（例如滚出视口时）。

        **主窗隐藏期间（`_shell_hidden`）只记录意图、不显示**（类 docstring 的不变式）：
        否则渲染层一次迟到的 `setVisible(true)` 就能把浮层重新摆到屏幕上，
        #3 的"关窗无残留"随即失效。真正恢复由 `refresh_geometry()` 的恢复分支负责。
        """
        self._visible = bool(visible)
        if not self._visible:
            self.window.hide()
            self._force_repaint()
        elif self._rect and not self._shell_hidden:
            self._show_overlay()

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
        切换全屏。

        子控件模式：全屏 = 铺满 **`QWebEngineView` 客户区**（`view.width()/height()`），
        不再调用 `showFullScreen()` —— 子控件不是窗口，脱离父窗去铺满屏幕既无意义，
        也会破坏"随主窗"的父子关系。
        顶层窗口（回退）模式：仍按屏幕全屏（v1.8.1 行为）。

        退出全屏后重新按页面矩形贴合（否则会停在铺满尺寸上）。

        **主窗隐藏期间（`_shell_hidden`）只记录全屏意图、不显示**（类 docstring 的
        不变式）；恢复窗口时由 `refresh_geometry()` 的恢复分支按 `_fullscreen`
        还原，不会丢意图也不会"弹出全屏黑窗"。
        """
        self._fullscreen = bool(fullscreen)
        if self._shell_hidden:
            return
        if self._fullscreen:
            self._enter_fullscreen()
        else:
            self._exit_fullscreen()

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
                "mode": self.mode,
            },
            ensure_ascii=False,
        )

    @Slot()
    def close(self) -> None:
        """
        关闭预览：停止播放并释放 StreamProxy 使用的连接。

        同时**复位 `_fullscreen`**（M-3）：若不复位，"全屏 → 关窗到托盘 → 托盘期间
        预览被关闭"会留下陈旧的 `_fullscreen=True`，恢复窗口时
        `refresh_geometry()` 的恢复分支会重新铺满，把一个**没有媒体源**的浮层
        铺满视口（全屏黑窗），违背 AC-12 的关窗语义。
        复位后即便还有陈旧的 `_kind`，恢复分支的 `if not self._kind` 也兜得住。
        """
        self.player.stop()
        self.player.setSource(QUrl())
        self.window.hide()
        self._kind = ""
        self._rect = {}
        self._fullscreen = False
        self._force_repaint()

    # ── 主窗几何 / 生命周期联动（由 WebShell 调用，主线程） ──────

    def refresh_geometry(self) -> None:
        """
        按**当前** `_rect` + 主窗当前状态重算浮层几何。

        子控件模式下主窗移动/缩放由 Qt 父子关系自动跟随，本方法只在
        **需要重贴合**的场景执行：渲染层重新上报矩形、主窗从托盘恢复、
        视口尺寸变化（全屏铺满时）。

        早退顺序（**顺序本身就是正确性的一部分**）：

        0. `_shell_hidden` 为真 ⇒ 这是"**从托盘恢复**"路径，按**当前模式**恢复显示 ——
           全屏态重新铺满（子控件铺满视口 / 回退模式 `showFullScreen()`）、
           非全屏态按 `_rect` 重新贴合后 `show()`。这一步**必须排在全屏早退之前**：
           否则"全屏看视频 → 关窗到托盘 → 恢复窗口"会因为全屏早退而直接返回，
           浮层**永久不再出现**（只剩音频在响）。两个额外守卫：
           - `if not self._kind: return` —— 没有媒体源（预览已被 `close()`）就不要
             摆出来，更不要铺满视口弹黑窗（M-3）；
           - **只有真的恢复了显示才清 `_shell_hidden`**（M-5）。若继续留着标志，
             下一次有效上报/几何事件还能再试；若提前清掉，而渲染层因
             `MediaStage.tsx` 的 `lastKey` 去重不再上报，恢复就**永久不会发生**；
        1. `_fullscreen` 为真时：回退模式直接返回（全屏浮层是铺满屏幕的顶层窗口，
           不能因为主窗动了一下就被拉回小矩形）；子控件模式只重铺视口尺寸
           （主窗缩放后全屏仍应铺满），不改矩形来源；
        2. `_rect` 为空时返回：还没有人上报过预留矩形，没有可换算的基准；
        3. 探测到 `visible=false` 时返回：渲染层已声明"滚出视口/不应显示"，
           此时重贴合会把刚隐藏的浮层又摆出来。

        内部复用既有 `_apply_geometry()`（CSS 像素 == Qt 逻辑像素，
        子控件模式直接用相对坐标、回退模式用 `mapToGlobal`，**不做 dpr 换算**，
        见模块文档与 `LESSONS.md` L33 的口径纪律），不新写第二套坐标计算。

        可由 `QTimer.singleShot(0, ...)` 延迟一帧调用：拖动过程中顶层窗口模式
        `mapToGlobal` 可能拿到尚未生效的窗口坐标（见 `WebShell._schedule_overlay_refresh`）。
        注意 `WebShell` 在**主窗不可见**时不会排队该调用（避免隐藏期间被重新摆出来）。
        """
        if self._shell_hidden:
            # 主窗从托盘恢复：按当前模式重新贴合 + 重新显示。
            # 注意：标志只在**真的恢复成功**时清除（M-5），见下方各处 return 前的注释。
            if not self._kind:
                # 没有媒体源（预览已 close）：不要恢复，也不要弹全屏黑窗（M-3）
                return
            if self._fullscreen:
                # 全屏态恢复：子控件铺满视口 / 回退模式铺满屏幕（不得降级成小矩形）
                self._shell_hidden = False
                self._enter_fullscreen()
                return
            if not self._rect or not self._rect.get("visible", True):
                # 暂时无法恢复（无预留矩形 / 渲染层声明不可见）：
                # **保持 `_shell_hidden=True`**，等下一次有效上报再恢复（M-5）
                return
            self._shell_hidden = False
            self._apply_geometry()
            if not self.window.isVisible():
                self._show_overlay()
            return
        if self._fullscreen:
            # 全屏态优先：不得把全屏浮层拉回小矩形；子控件模式只跟随视口尺寸
            if self.mode == MODE_CHILD:
                self._apply_fullscreen_geometry()
            return
        rect = self._rect
        if not rect:
            return
        if not rect.get("visible", True):
            return
        self._apply_geometry()

    def on_shell_hidden(self) -> None:
        """
        主窗隐藏（= 最小化到系统托盘）时的浮层联动。

        浮层必须显式 `hide()`：回退模式下它是**独立顶层窗口**，不随主窗隐藏，
        不隐藏就会在点 ✕ 关窗后残留播放画面；子控件模式下父窗隐藏时它本不可见，
        但显式隐藏能保证"主窗恢复时不会先闪一下旧位置"。

        三条**刻意不做**的事（关窗 = 后台继续运行，不是关闭预览）：

        - **不** `player.stop()`：托盘语义就是"后台继续播放"（音频继续）；
        - **不**清空 `_rect`：从托盘恢复窗口时可直接按原矩形贴合；
        - **不**清 `_kind`：媒体类型仍有效，`state()` 桥方法要继续能答对。

        隐藏后**必须**调 `_force_repaint()`：原生画面盖住的区域在它隐藏后
        Chromium 并不知道要重画（`LESSONS.md` L40），不重绘就是"关窗后那块是旧画面"。

        另外记下 `_shell_hidden=True`，让随后到来的几何事件（`showEvent` 触发）
        知道该把浮层重新显示出来。
        """
        self._shell_hidden = True
        self.window.hide()
        self._force_repaint()

    # ── 内部 ──────────────────────────────────────

    def _force_repaint(self) -> None:
        """
        强制让 Chromium 重绘。

        原生浮层（顶层窗口或 native 子控件）覆盖的那块区域在浮层隐藏后 Chromium
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

    def _show_overlay(self) -> None:
        """显示并置顶原生浮层（子控件模式用 `raise_()` 压住同级原生子窗口）"""
        self.window.show()
        self.window.raise_()

    def _enter_fullscreen(self) -> None:
        """进入全屏：子控件铺满视口 / 回退模式铺满屏幕"""
        if self.mode == MODE_CHILD:
            self._apply_fullscreen_geometry()
        else:
            self.window.showFullScreen()

    def _exit_fullscreen(self) -> None:
        """退出全屏：回退模式先复原成普通窗口，再按页面矩形贴合"""
        if self.mode == MODE_WINDOW:
            self.window.showNormal()
        self._apply_geometry()
        self._show_overlay()

    def _apply_fullscreen_geometry(self) -> None:
        """把子控件铺满视口（全屏口径；回退模式由 `showFullScreen()` 负责）"""
        view = self._get_view()
        if view is None:
            return
        self.window.setGeometry(0, 0, max(1, view.width()), max(1, view.height()))
        # L-8：全屏后仍要压住 QWebEngineView 的合成层（与 open/切回时一致）
        self.window.raise_()

    def _apply_geometry(self) -> None:
        """
        按页面矩形摆放原生浮层（不做 dpr 换算，见模块文档）。

        - 子控件模式：`rect` 相对视口 = 相对父控件，**直接** `setGeometry`；
        - 顶层窗口（回退）模式：把视口内坐标经 `mapToGlobal` 换成屏幕坐标。
        """
        rect = self._rect
        if not rect or not rect.get("visible", True):
            if rect and not rect.get("visible", True):
                self.window.hide()
            return
        view = self._get_view()
        if view is None:
            return
        x = int(round(rect.get("x", 0)))
        y = int(round(rect.get("y", 0)))
        size = QSize(max(1, int(round(rect.get("w", 1)))), max(1, int(round(rect.get("h", 1)))))
        if self.mode == MODE_CHILD:
            self.window.setGeometry(x, y, size.width(), size.height())
        else:
            top_left = view.mapToGlobal(QPoint(x, y))
            self.window.setGeometry(top_left.x(), top_left.y(), size.width(), size.height())
        # issue #7：QVideoWidget 是原生子窗口，主窗移动时合成器可能来不及给它
        # 重绘（拖动中画面滞后/黑屏）。几何更新后显式刷新视频 surface。
        if self._kind == "video" and self.window.isVisible():
            self.video.update()
            self.video.repaint()
            self.window.update()

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

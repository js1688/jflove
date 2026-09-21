"""
桌面端 Web UI 外壳：无边框窗口 + QWebEngineView + QWebChannel 注入

## 为什么不用 Qt 原生外框

标题栏（最小化 / 最大化 / 还原 / 关闭）由 Web 自绘，视觉与三端设计系统一致。
Qt 只负责窗口标志与系统交互（拖拽、缩放、贴靠）。

## 两条必须踩准的点（P0-1 实测，见 plans §10）

1. `:/qtwebchannel/qwebchannel.js` 只有在 `import PySide6.QtWebChannel` 之后才注册；
2. `QWebEngineScript` 的 worldId **默认是 ApplicationWorld(1)**，必须显式设为
   `MainWorld(0)`，否则页面主世界拿不到 `window.QWebChannel`
   （症状：`qt.webChannelTransport` 存在但 `QWebChannel` 为 undefined）。

## 拖拽/缩放为什么必须走桥

`-webkit-app-region: drag` 是 Electron 私有扩展，QtWebEngine 不支持。
因此由 JS 判定意图后通知 Python 调 `QWindow.startSystemMove/startSystemResize`。
"""

from __future__ import annotations

import os
import sys

from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401  (须早于 QApplication)

from PySide6.QtCore import (
    QEvent,
    QFile,
    QIODevice,
    QObject,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

from src.utils.icon import get_app_icon
from src.utils.logger import get_logger

logger = get_logger(__name__)


def resolve_web_root() -> str:
    """
    定位前端产物目录。

    - 开发运行：`jflove-desktop/ui/dist`
    - PyInstaller 打包后：解包目录下的 `webui/`（由 build.py 的 --add-data 提供）

    注意层级：本文件在 `src/ui/` 下，要连退三级才到 `jflove-desktop/`
    （`src/ui` → `src` → `jflove-desktop`）。
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, "webui")
    desktop_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(desktop_root, "ui", "dist")


def resolve_index_html() -> str:
    """前端入口 HTML 的绝对路径"""
    return os.path.join(resolve_web_root(), "index.html")


class ShellControls(QObject):
    """窗口控制桥（QWebChannel 注册名 `shell`），供 Web 自绘标题栏调用"""

    #: 最大化状态变化（Python → JS，用于切换"最大化/还原"图标）
    maximizedChanged = Signal(bool)

    def __init__(self, window: "WebShell") -> None:
        super().__init__()
        self._window = window

    @Slot(result=bool)
    def isMaximized(self) -> bool:
        return self._window.isMaximized()

    @Slot()
    def minimize(self) -> None:
        self._window.showMinimized()

    @Slot()
    def toggleMaximize(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    @Slot()
    def close(self) -> None:
        """
        标题栏 ✕：**最小化到系统托盘**（不退出应用）。

        正式退出走托盘菜单的「退出」（见 `src/ui/tray.py`）。
        """
        self._window.hide_to_tray()

    @Slot(str)
    def reportTheme(self, mode: str) -> None:
        """
        渲染层回执当前主题模式，用于同步托盘菜单的勾选状态。

        （主题真相在 Web 的 theme-store，Python 只做展示同步。）
        """
        tray = getattr(self._window, "tray", None)
        if tray is not None:
            tray.set_theme_mode(str(mode))

    @Slot(result=bool)
    def startSystemMove(self) -> bool:
        """把拖拽交给系统（Windows 上同时获得贴靠/Aero Snap）"""
        handle = self._window.windowHandle()
        return bool(handle.startSystemMove()) if handle else False

    @Slot(int, result=bool)
    def startSystemResize(self, edges: int) -> bool:
        """
        把缩放交给系统。

        :param edges: Qt::Edges 位掩码 —— LeftEdge=1 / TopEdge=2 / RightEdge=4 / BottomEdge=8
        """
        if not edges:
            return False
        handle = self._window.windowHandle()
        return bool(handle.startSystemResize(Qt.Edge(edges))) if handle else False

    @Slot(result=str)
    def platformInfo(self) -> str:
        """平台信息（自检用，不出网）"""
        import json

        import PySide6

        return json.dumps(
            {
                "platform": QGuiApplication.platformName(),
                "qt": PySide6.__version__,
                "python": sys.version.split()[0],
                "os": sys.platform,
                "dpr": round(self._window.devicePixelRatio(), 3),
            },
            ensure_ascii=False,
        )


class DropAwareWebView(QWebEngineView):
    """
    能拿到**拖入文件的真实本地路径**的 WebEngine 视图。

    浏览器安全模型下，渲染层只能拿到 `File` 对象、**拿不到路径**，因此无法把文件交给
    Python 上传。原生侧的 `QMimeData.urls()` 才有真实路径，所以在视图上拦截拖放：

        拖入文件 → 这里读出本地路径 → 回调 → 桥推 `dnd.files_dropped` → 页面按当前目录上传

    GUI 线程回调（`dropEvent` 本就在 GUI 线程）。
    """

    def __init__(self, parent=None, on_files_dropped=None) -> None:
        super().__init__(parent)
        self._on_files_dropped = on_files_dropped
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件
        mime = event.mimeData()
        if not mime.hasUrls() or self._on_files_dropped is None:
            super().dropEvent(event)
            return
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        if paths:
            # 自己处理，别让 Chromium 再走它的默认拖放（否则渲染层会拿到无路径的 File）
            event.acceptProposedAction()
            self._on_files_dropped(paths)
            return
        super().dropEvent(event)


class WebShell(QMainWindow):
    """
    承载 Web UI 的无边框窗口。

    :param objects: {注册名: QObject}，注册进 QWebChannel（`shell` 由本类自动注册）
    :param frameless: 是否去掉 Qt 原生外框（去框后由页面自绘标题栏）
    """

    def __init__(
        self,
        objects: dict[str, QObject] | None = None,
        frameless: bool = True,
        title: str = "JFLove",
        publish_event=None,
        size: tuple[int, int] = (1180, 760),
    ) -> None:
        super().__init__()

        flags = Qt.WindowType.Window
        if frameless:
            flags |= Qt.WindowType.FramelessWindowHint
        self.setWindowFlags(flags)
        self.setWindowTitle(title)
        self.setWindowIcon(get_app_icon())
        self.setMinimumSize(900, 600)
        self.resize(*size)

        #: 事件发布器（拖放上传等主动推送用）
        self._publish_event = publish_event
        # 退出标志：只有托盘菜单的「退出」会置位（见 closeEvent）
        self._quitting = False
        #: 托盘控制器（`setup_tray()` 后可用）
        self.tray = None

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.view = DropAwareWebView(
            container, on_files_dropped=self._emit_files_dropped
        )
        self._harden_settings(self.view)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(container)

        self.shell_controls = ShellControls(self)

        self.channel = QWebChannel(self)
        self.channel.registerObject("shell", self.shell_controls)
        for name, obj in (objects or {}).items():
            self.channel.registerObject(name, obj)
        self.view.page().setWebChannel(self.channel)

        self._inject_qwebchannel_js()

    @staticmethod
    def _harden_settings(view: QWebEngineView) -> None:
        """
        最小安全基线。

        - 允许本地页面读取同目录资源（css/js）
        - **禁止访问远程 URL**：renderer 不应有任何网络出口
        """
        settings = view.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )

    def _inject_qwebchannel_js(self) -> None:
        """注入 Qt 自带的 qwebchannel.js（必须指定 MainWorld，见模块文档）"""
        source = QFile(":/qtwebchannel/qwebchannel.js")
        if not source.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError(
                "无法读取 :/qtwebchannel/qwebchannel.js"
                "（需先 import PySide6.QtWebChannel 以注册该资源）"
            )
        code = bytes(source.readAll().data()).decode("utf-8")
        source.close()

        script = QWebEngineScript()
        script.setName("qwebchannel.js")
        script.setSourceCode(code)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        self.view.page().scripts().insert(script)

    def load_ui(self) -> bool:
        """
        加载前端产物。

        :returns: True 表示找到了 index.html；False 表示前端还没构建
        """
        index = resolve_index_html()
        if not os.path.exists(index):
            logger.error("找不到前端产物：%s", index)
            return False
        self.view.load(QUrl.fromLocalFile(index))
        return True

    def _emit_files_dropped(self, paths: list) -> None:
        """拖入本地文件：把真实路径推给渲染层（由它按当前目录上传）"""
        if self._publish_event is None:
            logger.warning("拖放上传：未注入事件发布器，忽略")
            return
        import os as _os

        files = []
        for path in paths:
            try:
                size = _os.path.getsize(path)
            except OSError:
                size = 0
            files.append(
                {"path": str(path), "name": _os.path.basename(str(path)), "size": size}
            )
        self._publish_event("dnd.files_dropped", {"files": files})
        logger.info("拖放上传：收到 %d 个本地文件", len(paths))

    # ── 托盘（N17） ───────────────────────────────

    def setup_tray(self, publish_event=None) -> None:
        """
        创建系统托盘。

        :param publish_event: 向渲染层推事件的回调（`bridge.publish_event`），
                              用于把托盘的"切换主题"意图交给 Web 侧
        """
        from src.ui.tray import TrayController  # 局部导入，避免循环依赖

        self.tray = TrayController(self, publish_event=publish_event)

    def restore_from_tray(self) -> None:
        """从托盘恢复窗口"""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def hide_to_tray(self) -> None:
        """隐藏到托盘并气泡提示（应用继续在后台运行）"""
        self.hide()
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.notify_minimized()
        logger.info("窗口已最小化到系统托盘")

    def quit_application(self) -> None:
        """真正退出应用（仅托盘菜单的「退出」走这里）"""
        self._quitting = True
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.hide()
        QApplication.quit()
        logger.info("用户从托盘退出应用")

    # ── 关闭语义 ──────────────────────────────────

    def closeEvent(self, event: QEvent) -> None:
        """
        关闭事件。

        - **退出流程中**（`quit_application` 已置位）：接受事件，真正关闭
        - **普通关闭**（标题栏 ✕ / 系统关闭）：忽略事件，最小化到托盘
        """
        if getattr(self, "_quitting", False):
            event.accept()
            return
        event.ignore()
        self.hide_to_tray()

    def changeEvent(self, event: QEvent) -> None:
        """窗口状态变化时把「是否最大化」推回页面（用于切换标题栏图标）"""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.shell_controls.maximizedChanged.emit(self.isMaximized())


def build_app() -> QApplication:
    """创建 QApplication（高 DPI 策略须在实例化前设置）"""
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app

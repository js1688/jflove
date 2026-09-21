"""
PoC 共用外壳：QWebEngineView 承载页面 + QWebChannel 注入

从 P0-2 开始，各 PoC 复用本模块，不再各自复制窗口代码。
（P0-1 的 `poc/frameless/main.py` 是已验证产物，保持原样不动。）

两条实测得来的硬约束（见 P0-1 README §5）：
  1. `:/qtwebchannel/qwebchannel.js` 必须先 `import PySide6.QtWebChannel` 才注册；
  2. `QWebEngineScript` 的 worldId 默认是 ApplicationWorld(1)，**必须显式设为
     MainWorld(0)**，否则页面主世界拿不到 `window.QWebChannel`。
"""

from __future__ import annotations

import os
import sys

# QtWebEngineWidgets 必须在 QApplication 之前导入
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

from PySide6.QtCore import (  # noqa: E402
    QEvent,
    QFile,
    QIODevice,
    QObject,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWebChannel import QWebChannel  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget  # noqa: E402


class ShellControls(QObject):
    """
    窗口控制桥（注册名 `shell`）。

    P0-2 的窗口带系统标题栏，用不到这些方法；保留是为了让后续 PoC 能直接
    切到无边框形态（P0-1 已验证过这套调用）。
    """

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
        self._window.close()

    @Slot(result=bool)
    def startSystemMove(self) -> bool:
        handle = self._window.windowHandle()
        return bool(handle.startSystemMove()) if handle else False

    @Slot(int, result=bool)
    def startSystemResize(self, edges: int) -> bool:
        if not edges:
            return False
        handle = self._window.windowHandle()
        return bool(handle.startSystemResize(Qt.Edge(edges))) if handle else False


class WebShell(QMainWindow):
    """
    承载 Web UI 的窗口外壳。

    :param page_file: 本地 HTML 绝对路径
    :param objects: {注册名: QObject}，注册进 QWebChannel
    :param frameless: 是否去掉 Qt 原生外框（去框后需页面自绘标题栏）
    """

    def __init__(
        self,
        page_file: str,
        objects: dict[str, QObject] | None = None,
        frameless: bool = False,
        title: str = "JFLove PoC",
        size: tuple[int, int] = (1180, 760),
    ) -> None:
        super().__init__()

        flags = Qt.WindowType.Window
        if frameless:
            flags |= Qt.WindowType.FramelessWindowHint
        self.setWindowFlags(flags)
        self.setWindowTitle(title)
        self.setMinimumSize(900, 600)
        self.resize(*size)

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.view = QWebEngineView(container)
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
        self.view.load(QUrl.fromLocalFile(page_file))

    @staticmethod
    def _harden_settings(view: QWebEngineView) -> None:
        """最小安全基线：允许本地页面读同目录资源，禁止访问远程 URL"""
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

    def changeEvent(self, event: QEvent) -> None:
        """把最大化状态推回页面（无边框标题栏需要它切换图标）"""
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
    app.setApplicationName("JFLove-PoC")
    return app


def page_dir(*parts: str) -> str:
    """拼出 PoC 页面目录下的绝对路径"""
    return os.path.join(*parts)


def platform_summary() -> dict:
    """平台信息（自检用）"""
    import PySide6

    return {
        "platform": QGuiApplication.platformName(),
        "qt": PySide6.__version__,
        "python": sys.version.split()[0],
        "os": sys.platform,
    }

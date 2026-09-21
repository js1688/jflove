"""
P0-1 PoC：无边框窗口 + Web 自绘标题栏

验证目标（对应 plans/desktop-webui-migration-v1.5.0.md §3 Phase 0 P0-1）：
  1. 去掉 Qt 原生外框（FramelessWindowHint），窗口外观完全由 Web 绘制
  2. Web 自绘标题栏的「最小化 / 最大化-还原 / 关闭」三个按钮可用
  3. 标题栏拖拽移动窗口（startSystemMove，交由系统接管 → Windows 上同时获得贴靠）
  4. 8 方向边缘缩放（startSystemResize）
  5. 双击标题栏最大化 / 还原（不依赖浏览器 dblclick —— 系统移动会吞掉该事件）
  6. 最大化状态由 Python 推回 Web，最大化/还原图标随状态切换

本文件是 PoC，不是最终形态：
  - 只含「窗口控制」这一条桥通路；业务桥见 P0-2 / N4
  - 本地资源走 file://（正式方案在 N1 改为自定义 scheme + CSP）

运行（Windows / Linux 均适用）：
    jflove-desktop/venv-win/Scripts/python.exe poc/frameless/main.py

自动化自检（无显示环境亦可）：
    jflove-desktop/venv-win/Scripts/python.exe poc/frameless/test_headless.py
"""

from __future__ import annotations

import json
import os
import sys

# QtWebEngineWidgets 必须在 QApplication 之前导入（Qt 6 的初始化要求）
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
# 注意：导入 QtWebChannel 之后，资源 :/qtwebchannel/qwebchannel.js 才会注册
from PySide6.QtWebChannel import QWebChannel  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget  # noqa: E402

# 本文件所在目录（poc/frameless/）
POC_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(POC_DIR, "web")

# 桥对象在 JS 侧的名字：channel.objects.bridge
BRIDGE_NAME = "bridge"


class WindowBridge(QObject):
    """
    暴露给 Web 的窗口控制接口。

    说明：JS 侧不直接操作窗口，全部经此对象调用 Python —— 这与正式方案
    「renderer 不碰系统能力，一律经桥」的约束一致（见计划 §2.2）。
    """

    #: 最大化状态变化（Python → JS 推送）
    maximizedChanged = Signal(bool)

    def __init__(self, window: "ShellWindow") -> None:
        super().__init__()
        self._window = window

    # ── 状态查询 ──────────────────────────────────

    @Slot(result=bool)
    def isMaximized(self) -> bool:
        """当前是否最大化"""
        return self._window.isMaximized()

    @Slot(result=str)
    def platformInfo(self) -> str:
        """桥自检用：返回平台信息 JSON，证明 JS → Python → JS 往返通路可用"""
        import PySide6  # 局部导入，避免模块级循环

        info = {
            "platform": QGuiApplication.platformName(),
            "qt": PySide6.__version__,
            "python": sys.version.split()[0],
            "os": sys.platform,
            "dpr": round(self._window.devicePixelRatio(), 3),
        }
        return json.dumps(info, ensure_ascii=False)

    # ── 窗口动作 ──────────────────────────────────

    @Slot()
    def minimize(self) -> None:
        """最小化到任务栏"""
        self._window.showMinimized()

    @Slot()
    def toggleMaximize(self) -> None:
        """最大化 / 还原（状态变化由 changeEvent 推回 JS）"""
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    @Slot()
    def close(self) -> None:
        """关闭窗口（正式形态下此动作会改为「最小化到托盘」，见 N17）"""
        self._window.close()

    @Slot(result=bool)
    def startSystemMove(self) -> bool:
        """
        把拖拽交给系统接管。

        QtWebEngine 不支持 Electron 的 `-webkit-app-region: drag`，
        所以只能由 JS 在按下标题栏时通知 Python 调用本方法。

        :return: 是否成功移交（无 windowHandle 或平台不支持时返回 False）
        """
        handle = self._window.windowHandle()
        if handle is None:
            return False
        return bool(handle.startSystemMove())

    @Slot(int, result=bool)
    def startSystemResize(self, edges: int) -> bool:
        """
        把缩放交给系统接管。

        :param edges: Qt::Edges 位掩码 —— LeftEdge=1 / TopEdge=2 / RightEdge=4 / BottomEdge=8
        :return: 是否成功移交
        """
        if not edges:
            return False
        handle = self._window.windowHandle()
        if handle is None:
            return False
        return bool(handle.startSystemResize(Qt.Edge(edges)))


class ShellWindow(QMainWindow):
    """
    无边框外壳窗口。

    Qt 侧只负责：窗口标志、承载 WebEngine 视图、承接桥调用。
    标题栏/最小化/最大化/关闭按钮的**外观与交互全部由 Web 绘制**。
    """

    def __init__(self) -> None:
        super().__init__()
        # 去原生外框：整个窗口外观由 Web 决定
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("JFLove — P0-1 无边框外壳 PoC")
        self.setMinimumSize(900, 600)
        self.resize(1180, 760)

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.view = QWebEngineView(container)
        self._harden_settings(self.view)

        layout.addWidget(self.view, 1)
        self.setCentralWidget(container)

        # ── 桥 ──
        self.bridge = WindowBridge(self)
        self.channel = QWebChannel(self)
        self.channel.registerObject(BRIDGE_NAME, self.bridge)
        self.view.page().setWebChannel(self.channel)

        self._inject_qwebchannel_js()
        self.view.load(QUrl.fromLocalFile(os.path.join(WEB_DIR, "index.html")))

    # ── 初始化 ────────────────────────────────────

    @staticmethod
    def _harden_settings(view: QWebEngineView) -> None:
        """
        WebEngine 安全设置（PoC 阶段先立最小基线）。

        - 允许本地页面读取同目录资源（file:// 相对路径的 css/js）
        - 禁止本地页面访问远程 URL（renderer 内不应有任何网络出口）
        """
        settings = view.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )

    def _inject_qwebchannel_js(self) -> None:
        """
        注入 Qt 自带的 qwebchannel.js。

        两个必须踩准的点（都实测过，写反了就静默失效）：
          1. 该资源只有在 `import PySide6.QtWebChannel` 之后才存在于
             `:/qtwebchannel/qwebchannel.js`，否则 QFile.exists() 为 False。
          2. **QWebEngineScript 的 worldId 默认是 ApplicationWorld(1)，不是
             MainWorld(0)** —— 不显式设为 MainWorld 的话，脚本会跑在隔离世界里，
             页面主世界的 app.js 根本看不到 window.QWebChannel，
             表现为「qt.webChannelTransport 存在但 QWebChannel undefined」。
        """
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

    # ── 事件 ──────────────────────────────────────

    def changeEvent(self, event: QEvent) -> None:
        """窗口状态变化时把「是否最大化」推回 Web（用于切换最大化/还原图标）"""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.bridge.maximizedChanged.emit(self.isMaximized())


def build_app() -> QApplication:
    """创建 QApplication（属性须在实例化前设置）"""
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("JFLove-PoC-Frameless")
    return app


def main() -> int:
    """PoC 入口"""
    app = build_app()
    window = ShellWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

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

## 退出握手（设计 v1.6.0 §6.4 / AC-56 ~ AC-59）

`✕` 仍是「最小化到托盘」；**唯一**的退出入口是托盘的「退出」→ `request_quit()`。
有未保存内容时先推 `shell.quitRequested {timeoutMs}` 等渲染层裁决
（`confirm_quit` / `cancel_quit`，由 `bridge/editor.py` 转发），
5 s 内没有回执（或桥不可用）则由 GUI 线程弹**原生**确认框兜底，默认「取消」。
任何路径都**不清空**编辑器内容与草稿（设计 §6.4 末行红线）。

## 宿主默认动作处置（设计 §4.2.3 / AC-10，R-5 实测结论）

Qt 6.11 **没有** `QWebEnginePage.actions()`；`QWebEngineView.actions()` 实测长度为 0。
且 `WebAction` 枚举共 **49 项 = 48 个真实动作 + 1 个计数哨兵 `WebActionCount`**，
**对哨兵取 action 会崩（Windows 0xC0000005）**。
因此本模块**只**按**名字白名单**逐项 `view.pageAction(...)`，绝不按下标遍历 `actions()`。
"""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401  (须早于 QApplication)

from PySide6.QtCore import (
    QEvent,
    QFile,
    QIODevice,
    QObject,
    QThread,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineScript,
    QWebEngineSettings,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

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


# ── 宿主默认动作白名单（设计 §4.2.3 / R-5 实测结论） ─────────────

#: 需要逐项禁用的宿主默认动作（**按枚举成员名**，不是下标）。
#:
#: 为什么按名字：Qt 6.11 实测 `QWebEnginePage.actions()` **不存在**、
#: `QWebEngineView.actions()` 长度为 0；唯一可行做法是 `view.pageAction(枚举成员)`。
#: 而枚举里的 `WebActionCount` 是**计数哨兵**，对它取 action 会崩（0xC0000005），
#: 所以这里只保留**确定存在的真实动作名**，名字取不到就跳过（版本差异兜底）。
HOST_ACTIONS_TO_DISABLE: tuple[str, ...] = (
    "SavePage",  # 另存为
    "ViewSource",  # 查看源码
    "RequestClose",  # Ctrl+W 关窗（应用级不接管，见 §4.2.1）
    "InspectElement",  # 开发者工具
    "ExitFullScreen",
    "Reload",
    "ReloadAndBypassCache",
    "Back",
    "Forward",
    "Stop",
    # Qt5 时代的打印 / 打印预览 / 打开：Qt 6.11 枚举里**不存在**（R-5 已证），
    # 按名字取不到即跳过；保留是为了「宿主版本变化时自动生效」的兜底。
    "Print",
    "PrintPreview",
    "Open",
)


def resolve_host_actions() -> list[tuple[str, QWebEnginePage.WebAction]]:
    """
    把白名单里的**名字**解析成当前 Qt 版本真实存在的 `WebAction` 枚举成员。

    - 名字在本版本不存在（如 Qt6 没有 `Print`）⇒ 跳过，**不报错**
    - **绝不**返回 `WebActionCount` 哨兵（对它取 action 会崩 0xC0000005）
    - 去重：避免同一枚举值被处置两次

    :returns: `[(名字, 枚举成员), ...]`，顺序与白名单一致
    """
    enum_cls = QWebEnginePage.WebAction
    sentinel = getattr(enum_cls, "WebActionCount", None)
    resolved: list[tuple[str, QWebEnginePage.WebAction]] = []
    seen: set = set()
    for name in HOST_ACTIONS_TO_DISABLE:
        member = getattr(enum_cls, name, None)
        if member is None:
            continue
        # PySide6 6.11 的枚举成员是 Python enum（`int(member)` 会 TypeError），
        # 因此哨兵判定用 **同一性**、去重用成员自身（枚举成员可哈希）。
        if sentinel is not None and member is sentinel:
            continue  # 防御：白名单若被误改成哨兵名，也必须挡住
        if member in seen:
            continue
        seen.add(member)
        resolved.append((name, member))
    return resolved


def disable_host_actions(view) -> int:
    """
    逐个禁用宿主默认动作：`setShortcuts([])` + `setEnabled(False)`（R-5 实测均有效）。

    ⚠ **绝不遍历 `view.actions()` 取下标**：Qt 6.11 实测 `view.actions()` 长度为 0，
    且页面级枚举含计数哨兵 `WebActionCount`，对哨兵取 action 会崩溃。

    参数用鸭子类型（任何提供 `pageAction(WebAction)` 的对象），便于用假 view 断言调用序列。

    :param view: 通常为 `QWebEngineView`
    :returns: 实际完成处置的动作数
    """
    handled = 0
    for name, action_enum in resolve_host_actions():
        try:
            action = view.pageAction(action_enum)
        except Exception as exc:  # noqa: BLE001 - 单个动作不可用不应影响其余处置
            logger.debug("宿主动作 %s 取不到（%s），跳过", name, type(exc).__name__)
            continue
        if action is None:
            continue
        try:
            action.setShortcuts([])  # 清空快捷键（实测：读回为空、触发归零）
            action.setEnabled(False)  # 一并禁用（实测有效）
        except Exception as exc:  # noqa: BLE001 - 同上
            logger.debug("宿主动作 %s 处置失败（%s），跳过", name, type(exc).__name__)
            continue
        handled += 1
    logger.info(
        "宿主默认动作处置完成：%d/%d 项（白名单按名解析）",
        handled,
        len(HOST_ACTIONS_TO_DISABLE),
    )
    return handled


# ── 退出握手常量（设计 §6.4） ─────────────────────────────────

#: 等待渲染层裁决的时长（`shell.quitRequested.payload.timeoutMs`）
QUIT_REQUEST_TIMEOUT_MS = 5000

#: 退出状态机取值
QUIT_STATE_IDLE = "idle"  # 常驻，没有进行中的退出流程
QUIT_STATE_PENDING = "pending"  # 已推 quitRequested，等渲染层回执
QUIT_STATE_DIALOG = "dialog"  # 正在弹原生兜底确认框（此间忽略渲染层回执）
QUIT_STATE_QUITTING = "quitting"  # 已决定退出，正在收尾

# ── 外部路径推送（设计 §7.1 事件 #1 / §7.5） ───────────────────

#: `editor.openPaths.payload.source` 取值（与 `utils/single_instance.py` 一致）
SOURCE_CLI = "cli"
SOURCE_SINGLE_INSTANCE = "single_instance"

#: 待推送队列上限（条数）。与单实例协议的单次上限（64）对齐
OPEN_PATHS_QUEUE_LIMIT = 64

#: §7.5 ③ 的**一次**延迟重推间隔（渲染层按 `(path, size, mtime)` 去重）
OPEN_PATHS_RETRY_MS = 1500


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

    #: 内部信号：把退出入口调度到 **GUI 线程**（工作线程调用 `request_quit` 等时排队）
    _quitRequestedInternal = Signal()
    _quitConfirmedInternal = Signal(bool)
    _quitCancelledInternal = Signal()

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
        # 退出标志：只有退出流程真正放行后置位（见 closeEvent）
        self._quitting = False
        #: 退出状态机（`QUIT_STATE_*`）
        self._quit_state = QUIT_STATE_IDLE
        #: 渲染层上报的脏计数（`editor.reportDirty`，轻量、无内容）
        self._dirty_count = 0
        #: 用户历史上对「是否保留下次恢复」的选择；None = 未知 ⇒ **保留**（数据安全优先）
        self._retain_preference: bool | None = None
        #: 等待渲染层裁决的定时器（懒建，单飞）
        self._quit_timer: QTimer | None = None
        #: 前端产物是否已成功加载（`load_ui()` 成功置位，用于路径推送的就绪判定）
        self._ui_loaded = False
        #: 渲染层桥就绪的**显式**回执；None = 未声明（退回 `_ui_loaded` 启发式）
        self._renderer_ready: bool | None = None
        #: 待推送的外部路径队列（FIFO）：`[(paths, source), ...]`
        self._open_paths_queue: list[tuple[list[str], str]] = []
        #: 队列内去重键（`normcase(normpath(path))`）
        self._open_paths_queued: set[str] = set()
        #: §7.5 ③ 的一次延迟重推：待重推的批次与是否已排队
        self._open_paths_retry_batches: list[tuple[list[str], str]] = []
        self._open_paths_retry_pending = False
        #: 托盘控制器（`setup_tray()` 后可用）
        self.tray = None
        #: 原生媒体浮层引用（`attach_overlay()` 注入；**鸭子类型**，不 import 浮层模块）
        self._overlay = None
        #: 浮层重贴合是否已排队（合并同一帧内的多次 move/resize，见 _schedule_overlay_refresh）
        self._overlay_refresh_pending = False

        # 退出入口信号：接收者（self）活在 GUI 线程 ⇒ 跨线程发射自动排队，
        # 同线程发射为直接调用（同步、可断言返回值）。
        self._quitRequestedInternal.connect(self._do_request_quit)
        self._quitConfirmedInternal.connect(self._do_confirm_quit)
        self._quitCancelledInternal.connect(self._do_cancel_quit)

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.view = DropAwareWebView(
            container, on_files_dropped=self._emit_files_dropped
        )
        self._harden_settings(self.view)
        self._disable_host_actions()
        layout.addWidget(self.view, 1)
        self.setCentralWidget(container)

        self.shell_controls = ShellControls(self)

        self.channel = QWebChannel(self)
        self.channel.registerObject("shell", self.shell_controls)
        for name, obj in (objects or {}).items():
            self.channel.registerObject(name, obj)
        self.view.page().setWebChannel(self.channel)

        self._inject_qwebchannel_js()

    # ── 原生媒体浮层联动（#3） ────────────────────

    def attach_overlay(self, overlay) -> None:
        """
        挂上原生媒体浮层，让主窗几何事件（拖动/缩放）能驱动它重新贴合。

        只保存引用、**不 import 浮层模块** —— 用鸭子类型调用
        `overlay.refresh_geometry()` / `overlay.on_shell_hidden()`，
        保持"原生外壳不依赖浮层实现"的依赖方向（`main.py` 负责装配）。

        :param overlay: 任意带 `refresh_geometry()` / `on_shell_hidden()` 的对象
        """
        self._overlay = overlay

    def _schedule_overlay_refresh(self) -> None:
        """
        把一次拖动/缩放产生的**连续几何事件合并成事件循环末尾的一次重贴合**。

        拖动主窗会连发大量 `moveEvent`/`resizeEvent`，逐次重算屏幕坐标既浪费又会让
        浮层抖动；`QTimer.singleShot(0, ...)` + pending 标志只保留一次回调。

        为什么延迟到当前事件循环之后：Windows 拖动期间 `moveEvent` 可能早于窗口几何
        真正生效，此时 `mapToGlobal` 拿到的是旧坐标（表现为画面滞后一格）。
        延后一帧再取坐标即可拿到最终几何（真机验证项见开发记录「遗留问题」）。

        为什么**主窗不可见时不排队**：`hide_to_tray()` 隐藏主窗的过程中，平台仍可能
        投递几何事件；若照常排队，回调会走到 `refresh_geometry()` 的
        "`_shell_hidden` 恢复"分支，**立刻把刚隐藏的浮层又摆出来**，等于修复失效。
        从托盘恢复走的是 `showEvent`（那时主窗已可见）直接调用，不受这条早退影响。
        """
        if (
            self._overlay is None
            or self._overlay_refresh_pending
            or not self.isVisible()
        ):
            return
        self._overlay_refresh_pending = True
        QTimer.singleShot(0, self._on_overlay_refresh)

    def _safe_overlay_refresh(self) -> None:
        """
        安全地让浮层重贴合一次（异常只记 debug）。

        为什么吞异常：几何同步是**纯观感**优化 —— 浮层偏一格不会损坏数据，但如果
        让它抛出去，一个平台相关的 `QWidget` 异常就能把主窗的拖动/缩放
        （乃至 `showEvent`）交互打断。故只 `logger.debug` 记录后继续。

        为什么抽成方法：`showEvent` 与 `_on_overlay_refresh` 两处需要**完全相同**的
        容错口径，重复写两份容易在后续修改中漂移。
        """
        overlay = self._overlay
        if overlay is None:
            return
        try:
            overlay.refresh_geometry()
        except Exception as exc:  # noqa: BLE001 - 几何同步失败不应影响主窗交互
            logger.debug("浮层几何重贴合失败（忽略）：%s", exc)

    def _on_overlay_refresh(self) -> None:
        """合并后的延迟回调：真正执行一次浮层重贴合"""
        self._overlay_refresh_pending = False
        self._safe_overlay_refresh()

    def moveEvent(self, event: QEvent) -> None:
        """主窗被拖动：交回基类处理后触发浮层重贴合"""
        super().moveEvent(event)
        self._schedule_overlay_refresh()

    def resizeEvent(self, event: QEvent) -> None:
        """主窗被缩放：交回基类处理后触发浮层重贴合"""
        super().resizeEvent(event)
        self._schedule_overlay_refresh()

    def showEvent(self, event: QEvent) -> None:
        """主窗显示（含从托盘恢复）：立即按当前几何重新贴合一次"""
        super().showEvent(event)
        self._safe_overlay_refresh()

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

    def _disable_host_actions(self) -> int:
        """
        清理宿主（WebEngine）默认动作（设计 §4.2.3 / AC-10）。

        只走 `view.pageAction(具名枚举成员)` 白名单，见模块级
        `HOST_ACTIONS_TO_DISABLE` / `disable_host_actions()`；
        **绝不**遍历 `view.actions()`（含计数哨兵，取 action 会崩）。

        :returns: 实际处置的动作数
        """
        return disable_host_actions(self.view)

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
        self._ui_loaded = True
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
        # 浮层是独立顶层窗口，不随主窗隐藏 —— 必须显式让它一起隐藏（#3：
        # 否则点 ✕ 关窗后画面上还留着播放器）。播放状态不在这里动（后台继续播放）。
        overlay = self._overlay
        if overlay is not None:
            overlay.on_shell_hidden()
        self.hide()
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.notify_minimized()
        logger.info("窗口已最小化到系统托盘")

    def quit_application(self) -> None:
        """真正退出应用（退出握手的**唯一**收尾动作；必须在 GUI 线程调用）"""
        self._quitting = True
        self._quit_state = QUIT_STATE_QUITTING
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.hide()
        QApplication.quit()
        logger.info("退出流程已放行，正在关闭应用")

    # ── 退出握手（设计 §6.4 / AC-56 ~ AC-59） ──────────

    @property
    def dirty_count(self) -> int:
        """渲染层上报的脏计数（`quit.pending.payload.dirtyCount`，无内容）"""
        return self._dirty_count

    @property
    def quit_pending(self) -> bool:
        """是否有进行中的退出握手（`quit.pending.payload.pending`）"""
        return self._quit_state == QUIT_STATE_PENDING

    @property
    def quit_state(self) -> str:
        """当前退出状态机取值（`QUIT_STATE_*`，供诊断/测试）"""
        return self._quit_state

    def report_dirty(self, count) -> None:
        """
        记录渲染层上报的脏计数（`editor.reportDirty`，§7.1 #10 / §6.4）。

        **轻量、无内容**：只存一个整数，可被桥的工作线程调用（纯赋值，不需 GUI 线程）；
        非法值按 0 处理（宁可"少问一次"也不因为解析失败而卡住退出）。

        :param count: 未保存窗格数
        """
        try:
            value = int(count)
        except (TypeError, ValueError):
            value = 0
        self._dirty_count = max(0, value)

    def set_retain_preference(self, retain: bool | None) -> None:
        """
        记录用户对「是否保留下次恢复」的历史选择（§6.4「全局一次性」）。

        **只对"零脏直接退"生效**（有脏时以渲染层回执的 `retain` 为准）。
        `None` = 未知 ⇒ 按**保留**处理（数据安全优先：宁可多留草稿，也不清用户内容）。

        :param retain: True 保留 / False 不保留 / None 未知
        """
        self._retain_preference = None if retain is None else bool(retain)

    def request_quit(self) -> bool:
        """
        请求退出（**托盘「退出」的唯一入口**，§6.4 触发环节）。

        状态机：`idle` → （有脏则 `pending`，零脏则直接收尾）。
        **幂等**：非 `idle` 状态下的重复请求一律忽略并返回 False（避免双弹窗/双退出）。

        跨线程安全：工作线程调用时经内部信号排队到 GUI 线程执行（`QApplication.quit()`
        必须发生在 GUI 线程）。

        :returns: True 表示本次真的发起了退出流程
        """
        if self._quit_state != QUIT_STATE_IDLE:
            logger.info("退出握手：已有退出流程（%s），忽略重复请求", self._quit_state)
            return False
        if QThread.currentThread() is not self.thread():
            self._quitRequestedInternal.emit()  # 排队到 GUI 线程
            return True
        return self._do_request_quit()

    def confirm_quit(self, retain: bool = True) -> bool:
        """
        渲染层裁决「退出」（对应 `editor.confirmQuit`，§7.1 #11 / AC-56 / AC-57）。

        `clean_exit=true` 落盘；`retain is False` ⇒ 清空草稿（用户选择"不保留"），
        随后 `quit_application()` 收尾（GUI 线程）。

        :param retain: 是否保留下次恢复（渲染层确认框里的"全局一次性"勾选）
        :returns: True 表示本回执被采纳；非 `pending` 状态一律忽略（幂等）
        """
        if QThread.currentThread() is not self.thread():
            self._quitConfirmedInternal.emit(bool(retain))
            return True
        return self._do_confirm_quit(bool(retain))

    def cancel_quit(self) -> bool:
        """
        渲染层裁决「取消退出」（对应 `editor.cancelQuit`，§7.1 #12 / AC-56）。

        **进程不退出**：清 `pending`，编辑内容与草稿一律不动。

        :returns: True 表示确实取消了进行中的握手；非 `pending` 状态返回 False（幂等）
        """
        if QThread.currentThread() is not self.thread():
            self._quitCancelledInternal.emit()
            return True
        return self._do_cancel_quit()

    def _do_request_quit(self) -> bool:
        """（GUI 线程）退出请求的实际处理"""
        if self._quit_state != QUIT_STATE_IDLE:
            return False
        dirty_count = self._resolve_dirty_count()
        if dirty_count <= 0:
            # 零脏直接退：不弹确认框（§6.4）；用户历史上选过"不保留"才清草稿
            logger.info("退出握手：无未保存内容，直接退出")
            self._save_exit_marks(
                clean_exit=True, clear_drafts=self._retain_preference is False
            )
            self.quit_application()
            return True

        self._quit_state = QUIT_STATE_PENDING
        if not self._notify_renderer_quit(QUIT_REQUEST_TIMEOUT_MS):
            # 桥不可用 ⇒ 渲染层不可能回执：直接走原生兜底（§6.4 超时兜底口径）
            logger.warning("退出握手：事件发布器不可用，直接走原生兜底确认")
            self._fallback_confirm_quit()
            return True

        logger.info(
            "退出握手：已请求渲染层裁决（dirtyCount=%d，timeoutMs=%d）",
            dirty_count,
            QUIT_REQUEST_TIMEOUT_MS,
        )
        self._start_quit_timer(QUIT_REQUEST_TIMEOUT_MS)
        return True

    def _do_confirm_quit(self, retain: bool) -> bool:
        """（GUI 线程）渲染层回执「退出」的实际处理"""
        if self._quit_state != QUIT_STATE_PENDING:
            logger.info("退出握手：当前状态 %s 不接受 confirmQuit，忽略", self._quit_state)
            return False
        self._stop_quit_timer()
        self._clear_bridge_pending()
        self._retain_preference = bool(retain)
        self._save_exit_marks(clean_exit=True, clear_drafts=not retain)
        logger.info("退出握手：渲染层确认退出（retain=%s）", bool(retain))
        self.quit_application()
        return True

    def _do_cancel_quit(self) -> bool:
        """（GUI 线程）渲染层回执「取消」的实际处理"""
        if self._quit_state != QUIT_STATE_PENDING:
            logger.info("退出握手：当前状态 %s 不接受 cancelQuit，忽略", self._quit_state)
            return False
        self._stop_quit_timer()
        self._clear_bridge_pending()
        self._quit_state = QUIT_STATE_IDLE
        logger.info("退出握手：渲染层取消退出，进程继续运行")
        return True

    # ── 与 `bridge/editor.py` 的退出状态对接（§6.4） ─────

    @staticmethod
    def _editor_bridge():
        """
        惰性取 `src.bridge.editor`（dirtyCount 的**真相**在那边，见其模块文档）。

        取不到（未接线 / 模块缺失）⇒ 返回 None，本模块退回自己记录的
        `_dirty_count`（单测与未接线场景仍可用）。不抛异常。
        """
        module = sys.modules.get("src.bridge.editor")
        if module is not None:
            return module
        try:
            from src.bridge import editor as editor_bridge
        except Exception as exc:  # noqa: BLE001 - 桥未就绪不应影响退出判定
            logger.debug("退出握手：编辑器桥不可用（%s）", type(exc).__name__)
            return None
        return editor_bridge

    def _resolve_dirty_count(self) -> int:
        """
        取脏计数：桥（`editor.reportDirty` 的落点）优先，本地记录兜底。

        两者取 **max**：桥未接线时本地值生效；两者都在时以更"脏"的为准
        （宁可多问一次，也不静默丢掉用户的未保存内容）。
        """
        local = self._dirty_count
        bridge_module = self._editor_bridge()
        getter = getattr(bridge_module, "get_quit_state", None) if bridge_module else None
        if callable(getter):
            try:
                state = getter() or {}
                return max(local, max(0, int(state.get("dirtyCount", 0))))
            except Exception as exc:  # noqa: BLE001 - 读状态失败退回本地值
                logger.debug("退出握手：读脏计数失败（%s）", type(exc).__name__)
        return local

    def _notify_renderer_quit(self, timeout_ms: int) -> bool:
        """
        向渲染层发 `shell.quitRequested {timeoutMs}`，并把桥的 pending 置位。

        为什么优先自己发而不是直接调 `editor_bridge.notify_quit_requested()`：
        本模块持有 `WebShell` 注入的发布器（生产环境就是 `bridge.publish_event`），
        自己发能保证"**恰好一次**"（调用桥的函数会再发一次，造成双事件）。
        只有在**本模块没有发布器**而桥可用时才退回桥函数。

        :returns: True 表示事件已发出（可以开始等回执）
        """
        bridge_module = self._editor_bridge()
        setter = getattr(bridge_module, "set_quit_pending", None) if bridge_module else None
        if callable(setter):
            try:
                setter(True)  # 让 `quit.pending` 与本次握手一致
            except Exception as exc:  # noqa: BLE001 - 状态同步失败不影响主流程
                logger.debug("退出握手：同步桥 pending 失败（%s）", type(exc).__name__)
        if self._publish_event is None:
            notify = (
                getattr(bridge_module, "notify_quit_requested", None)
                if bridge_module
                else None
            )
            if callable(notify):
                notify(int(timeout_ms))
                return True
            return False
        self._publish_event(
            "shell.quitRequested", {"timeoutMs": int(timeout_ms)}
        )
        return True

    def _clear_bridge_pending(self) -> None:
        """清桥侧的 pending 标记（取消 / 原生兜底取消后 `quit.pending` 必须为 False）"""
        bridge_module = self._editor_bridge()
        setter = getattr(bridge_module, "set_quit_pending", None) if bridge_module else None
        if callable(setter):
            try:
                setter(False)
            except Exception as exc:  # noqa: BLE001 - 状态同步失败不影响主流程
                logger.debug("退出握手：清桥 pending 失败（%s）", type(exc).__name__)

    def _start_quit_timer(self, timeout_ms: int) -> None:
        """启动/重启等待回执的定时器（单飞；超时即走原生兜底）"""
        if self._quit_timer is None:
            self._quit_timer = QTimer(self)
            self._quit_timer.setSingleShot(True)
            self._quit_timer.timeout.connect(self._on_quit_timeout)
        self._quit_timer.start(int(timeout_ms))

    def _stop_quit_timer(self) -> None:
        """停止等待回执的定时器（没有则不做事）"""
        if self._quit_timer is not None:
            self._quit_timer.stop()

    def _on_quit_timeout(self) -> None:
        """超时兜底：5 s 内没有渲染层回执（或桥不可用）⇒ GUI 线程的原生确认框"""
        if self._quit_state != QUIT_STATE_PENDING:
            return
        logger.warning(
            "退出握手：%d ms 内未收到渲染层回执，改走原生兜底确认",
            QUIT_REQUEST_TIMEOUT_MS,
        )
        self._fallback_confirm_quit()

    def _fallback_confirm_quit(self) -> None:
        """
        原生兜底确认框（§6.4 超时兜底）：`退出 / 取消`，**默认「取消」**。

        选「退出」⇒ `clean_exit=false`（**保留**草稿，下次启动自动还原）后退出；
        选「取消」⇒ 进程常驻，内容与草稿均不变。
        """
        self._stop_quit_timer()  # 兜底框取代"等回执"，定时器必须先停
        self._quit_state = QUIT_STATE_DIALOG
        accepted = False
        try:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(self.windowTitle())
            box.setText("有未保存的编辑内容，是否退出？")
            box.setInformativeText(
                "未保存内容将保留为草稿，下次启动自动还原。"
            )
            exit_button = box.addButton("退出", QMessageBox.ButtonRole.DestructiveRole)
            cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            # 默认 + Esc 都是「取消」：误触不会丢内容
            box.setDefaultButton(cancel_button)
            box.setEscapeButton(cancel_button)
            box.exec()
            accepted = box.clickedButton() is exit_button
        finally:
            if not accepted:
                self._quit_state = QUIT_STATE_IDLE
                self._clear_bridge_pending()
        if not accepted:
            logger.info("退出握手：用户在原生兜底确认框中选择取消，进程继续运行")
            return
        logger.info("退出握手：用户在原生兜底确认框中选择退出（保留草稿）")
        self._save_exit_marks(clean_exit=False, clear_drafts=False)
        self.quit_application()

    def _save_exit_marks(self, *, clean_exit: bool, clear_drafts: bool) -> None:
        """
        落盘退出标记（`clean_exit` 与草稿清理），**全部失败只记日志**。

        为什么吞异常：退出流程不能被索引写失败卡死（"绝不清空内容"是红线，
        清不掉草稿只是多留一份草稿，**不是**数据损坏）。
        日志/错误文案**不含完整路径与文档内容**（`AGENTS.md §9.4.13-14`）。

        :param clean_exit: True 写干净退出标记；False 写"非干净退出"（保留草稿待还原）
        :param clear_drafts: 是否清空草稿库（仅当用户明确选择"不保留"）
        """
        try:
            from src.services import scratch_service

            if clean_exit:
                scratch_service.mark_clean_exit()
            else:
                self._mark_abnormal_exit(scratch_service)
            if clear_drafts:
                result = scratch_service.clear_all_drafts()
                logger.info(
                    "退出握手：已按用户选择清空草稿（移除 %s 个内容文件）",
                    result.get("removed", 0),
                )
        except Exception as exc:  # noqa: BLE001 - 退出流程不得被落盘失败卡死
            logger.error("退出握手：退出标记落盘失败（%s: %s）", type(exc).__name__, exc)

    @staticmethod
    def _mark_abnormal_exit(scratch_service) -> None:
        """
        标记「非干净退出」：`clean_exit=false`（保留草稿，下次启动自动还原）。

        优先调用 `mark_abnormal_exit()`（若 `scratch_service` 以后提供该公开函数）；
        否则退回该模块**自身的**索引读写原语（同一把单飞锁 + 同一原子写），
        保证不绕开它的不变式（不新增文件、不改他人模块）。

        ⚠ 建议（已回报）：由草稿服务的负责人在 `scratch_service.py` 补一个公开的
        `mark_abnormal_exit()`；本方法会自动优先使用它。
        """
        hook = getattr(scratch_service, "mark_abnormal_exit", None)
        if callable(hook):
            hook()
            return
        with scratch_service._index_lock:
            scratch, _tmp_dir = scratch_service._ensure_dirs()
            index, _warnings = scratch_service._load_index(
                scratch, validate_content=False
            )
            index["clean_exit"] = False
            index["updated_at"] = time.time()
            scratch_service._write_index(scratch, index)
        logger.info("退出握手：已写 clean_exit=false（保留草稿待还原）")

    # ── 外部路径推送（设计 §7.1 事件 #1 / §7.5） ────────

    def notify_external_paths(self, paths, source: str = SOURCE_CLI) -> int:
        """
        把外部路径（命令行 / 单实例转发）交给渲染层（`editor.openPaths`）。

        **不丢事件**（§7.5 · Q-2 遗留问题）：
          - 桥未注入 / 前端产物未加载（`load_ui()` 未成功）⇒ **入队**（有上限、去重），
            待 `set_renderer_ready(True)` 或 `flush_pending_open_paths()` 时**按序**冲出；
          - 已就绪 ⇒ 立即推送，并按 §7.5 ③ 安排**一次**延迟重推（渲染层自行去重）。

        空 `paths` 不做入队：它只表示"第二实例不带参数"（窗口可见由调用方负责），
        事件本身没有可打开内容。

        :param paths: 路径序列（字符串）
        :param source: `SOURCE_CLI` / `SOURCE_SINGLE_INSTANCE`（未知取值按 CLI 处理）
        :returns: 本次**立即推送**的路径条数（入队时为 0）
        """
        items = self._dedupe_paths(paths)
        src = str(source) if str(source) in (
            SOURCE_CLI,
            SOURCE_SINGLE_INSTANCE,
        ) else SOURCE_CLI
        if src != str(source):
            logger.warning("外部路径：未知来源取值 %r，按 %s 处理", str(source), SOURCE_CLI)

        if not self._is_renderer_ready():
            if not items:
                return 0
            self._enqueue_open_paths(items, src)
            return 0
        self._publish_open_paths(items, src)
        self._schedule_open_paths_retry(items, src)
        return len(items)

    def set_renderer_ready(self, ready: bool = True) -> int:
        """
        渲染层桥就绪回执（可由 `main.py` / 桥在握手完成后调用）。

        :param ready: True = 就绪（顺带按序冲出待推送队列）；False = 回到"未就绪"
        :returns: 本次冲出的路径条数
        """
        self._renderer_ready = bool(ready)
        if not ready:
            return 0
        return self.flush_pending_open_paths()

    def flush_pending_open_paths(self) -> int:
        """
        按 **FIFO** 顺序冲出待推送队列（幂等：队列空则什么也不做）。

        :returns: 冲出的路径条数
        """
        if self._publish_event is None or not self._open_paths_queue:
            return 0
        pending, self._open_paths_queue = self._open_paths_queue, []
        self._open_paths_queued.clear()
        pushed = 0
        for items, src in pending:
            self._publish_open_paths(items, src)
            pushed += len(items)
            self._schedule_open_paths_retry(items, src)
        return pushed

    @staticmethod
    def _dedupe_paths(paths) -> list[str]:
        """批内去重（保持顺序）；非字符串项按字符串化处理，空串丢弃"""
        items: list[str] = []
        seen: set[str] = set()
        for raw in paths or []:
            text = str(raw)
            if not text:
                continue
            key = os.path.normcase(os.path.normpath(text))
            if key in seen:
                continue
            seen.add(key)
            items.append(text)
        return items

    def _is_renderer_ready(self) -> bool:
        """
        就绪判定：显式回执优先，否则退回 `load_ui()` 是否成功。

        为什么这样兜底：`main.py` 在 `load_ui()` 成功后才推首个实例的路径（§7.5 ③），
        若没有人显式回执，也必须**照旧立即推送**（不能把既有行为变成"永远入队"）。
        """
        if self._publish_event is None:
            return False
        if self._renderer_ready is not None:
            return self._renderer_ready
        return self._ui_loaded

    def _enqueue_open_paths(self, items: list[str], source: str) -> None:
        """入队（队列内去重 + 上限；超出部分丢弃**新**条目并只记条数）"""
        room = OPEN_PATHS_QUEUE_LIMIT - len(self._open_paths_queued)
        if room <= 0:
            logger.warning(
                "外部路径：待推送队列已满（上限 %d），本次 %d 条未入队",
                OPEN_PATHS_QUEUE_LIMIT,
                len(items),
            )
            return
        fresh: list[str] = []
        for path in items:
            key = os.path.normcase(os.path.normpath(path))
            if key in self._open_paths_queued:
                continue
            if len(fresh) >= room:
                break
            self._open_paths_queued.add(key)
            fresh.append(path)
        if not fresh:
            return
        self._open_paths_queue.append((fresh, source))
        logger.info(
            "外部路径：渲染层未就绪，%d 条路径入队（队列共 %d 条）",
            len(fresh),
            len(self._open_paths_queued),
        )

    def _publish_open_paths(self, items: list[str], source: str) -> None:
        """真正推送 `editor.openPaths`（只记条数与来源，不记路径）"""
        if self._publish_event is None:
            return
        self._publish_event(
            "editor.openPaths", {"paths": list(items), "source": source}
        )
        logger.info("外部路径：已推送 editor.openPaths（%d 条，来源=%s）", len(items), source)

    def _schedule_open_paths_retry(self, items: list[str], source: str) -> None:
        """
        §7.5 ③ 的**一次**延迟重推（`OPEN_PATHS_RETRY_MS`）。

        覆盖"`load_ui()` 返回后页面还在加载"的长尾；渲染层按 `(path,size,mtime)` 去重，
        所以重推不会重复开窗。若期间渲染层已显式回执就绪，则不再重推。
        同一次 flush 里的多个批次共用一个定时器（按序重推）。
        """
        if not items:
            return
        self._open_paths_retry_batches.append((list(items), source))
        if self._open_paths_retry_pending:
            return
        self._open_paths_retry_pending = True
        QTimer.singleShot(OPEN_PATHS_RETRY_MS, self._on_open_paths_retry)

    def _on_open_paths_retry(self) -> None:
        """延迟重推回调（渲染层已显式回执则跳过）"""
        self._open_paths_retry_pending = False
        batches, self._open_paths_retry_batches = self._open_paths_retry_batches, []
        if self._renderer_ready is True or self._publish_event is None:
            return
        total = sum(len(items) for items, _src in batches)
        if total <= 0:
            return
        logger.info("外部路径：就绪回执未到，按序重推一次（共 %d 条）", total)
        for items, source in batches:
            self._publish_open_paths(items, source)

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

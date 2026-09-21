"""
系统托盘（N17）

桌面端专属能力：**关闭窗口 → 最小化到托盘**、托盘菜单显示/隐藏窗口、主题切换、退出。

## 主题为什么走"事件推送"而不是 Python 直接改

主题状态的**真相在渲染层**（Web 的 `theme-store`，持久化在 localStorage），
Python 不持有它。所以托盘只负责**发意图**：

    Python 托盘点击「暗色」 → 桥推 `theme.set` 事件 → Web 侧 setMode('dark')
    Web 侧主题变化 → 调 `shell.reportTheme(mode)` → Python 同步菜单勾选

这样主题只有一份真相，托盘只是个遥控器。

## 关闭语义

按计划 §2.3 / N17：标题栏的 ✕ 与窗口关闭事件都**不退出应用**，
而是隐藏到托盘 + 气泡提示；真正退出只能走托盘菜单的「退出」。
（Qt 侧需配合 `app.setQuitOnLastWindowClosed(False)`，否则隐藏唯一窗口会直接退出。）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from src.utils.icon import get_app_icon
from src.utils.logger import get_logger

if TYPE_CHECKING:  # 仅类型检查用，避免与 web_shell 形成循环导入
    from src.ui.web_shell import WebShell

logger = get_logger(__name__)

#: 主题模式 → 菜单文案（与 Web 端 theme-store 的取值一致）
THEME_MODES = (("system", "跟随系统"), ("light", "亮色"), ("dark", "暗色"))


class TrayController:
    """
    托盘图标与菜单。

    :param window: 主窗口（需提供 restore_from_tray / quit_application）
    :param publish_event: 向渲染层推事件的回调（通常是 `bridge.publish_event`）
    """

    def __init__(
        self,
        window: "WebShell",
        publish_event: Callable[[str, object], None] | None = None,
    ) -> None:
        self._window = window
        self._publish_event = publish_event
        self._theme_mode = "system"
        self._theme_actions: dict[str, QAction] = {}

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(get_app_icon())
        self.tray.setToolTip("JFLove")

        # 必须持有 QMenu 的 Python 引用：
        # `QSystemTrayIcon.setContextMenu()` **不接管 menu 的所有权**，
        # 若只把 menu 放在局部变量里，Python 回收后底层 C++ 对象会随之销毁。
        self._menu = QMenu()
        menu = self._menu
        show_action = menu.addAction("显示窗口")
        show_action.triggered.connect(self._window.restore_from_tray)

        # 主题三项**直接平铺在顶层菜单**，不使用二级子菜单。
        #
        # 为什么不用 `menu.addMenu("主题")`：实测 PySide6 6.11 下，
        # 只要往该子菜单里加 action，`QMenu` 的 Python 包装对象立刻失效
        # （报 `Internal C++ object (QMenu) already deleted`；
        #   空子菜单正常，`addMenu(str)` / `addMenu(QMenu)` / `QAction.setMenu`
        #   三种写法 + 是否持有引用，四种组合全部复现）。
        # 平铺既绕开该缺陷，也少一层悬停展开，托盘菜单更好用。
        menu.addSeparator()
        for mode, label in THEME_MODES:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, m=mode: self._on_theme_selected(m)
            )
            self._theme_actions[mode] = action

        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self._window.quit_application)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()
        self.set_theme_mode("system")
        logger.info("系统托盘已就绪")

    # ── 供外部调用 ────────────────────────────────

    def set_theme_mode(self, mode: str) -> None:
        """同步菜单勾选（由渲染层回执 `shell.reportTheme` 触发）"""
        self._theme_mode = mode
        for candidate, action in self._theme_actions.items():
            action.setChecked(candidate == mode)

    @property
    def theme_actions(self) -> dict:
        """主题菜单项（`{mode: QAction}`，供测试/诊断读取勾选状态）"""
        return dict(self._theme_actions)

    def notify_minimized(self) -> None:
        """隐藏到托盘时弹一条气泡提示"""
        self.tray.showMessage(
            "JFLove",
            "程序已最小化到系统托盘，双击图标可恢复窗口。",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def hide(self) -> None:
        """退出流程中收起托盘图标"""
        self.tray.hide()

    @property
    def theme_mode(self) -> str:
        """当前（Python 侧已知的）主题模式"""
        return self._theme_mode

    # ── 内部 ──────────────────────────────────────

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """双击托盘图标恢复窗口"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._window.restore_from_tray()

    def _on_theme_selected(self, mode: str) -> None:
        """托盘里选了主题：先乐观更新勾选，再把意图推给渲染层"""
        self.set_theme_mode(mode)
        if self._publish_event is not None:
            self._publish_event("theme.set", {"mode": mode})
        logger.info("托盘请求切换主题: %s", mode)

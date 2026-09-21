"""
JFLove 桌面应用入口（Web UI 版）

启动流程：
  1. 平台兼容性环境变量（Linux 走 XWayland / 中文输入法）
  2. 创建 QApplication
  3. 旧版本数据迁移 + 读取本地登录有效期上限
  4. 创建无边框 WebShell（QtWebEngine 渲染前端产物）+ JS↔Python 桥
  5. 免登录恢复由前端在启动引导里经桥询问 Python（`auth.restore_session`）

## 与旧入口的关系

旧的 Qt/Fluent 界面（`ui/login_window.py`、`ui/main_window.py`、`ui/pages/*`、
`components/preview_dialog.py`、`components/markdown_view.py`）**已不再被本入口使用**，
它们会在 N20 节点统一清理。当前保留在树上是为了让迁移期的 diff 可读。

## Linux 为什么强制 XWayland

QtWebEngine 在 Wayland 下有崩溃前科（PySide6 6.11 + Qt 6.10 + Wayland 的
PYSIDE-2700 系列；详见 `components/markdown_view.py` 历史注释与计划 §10）。
用户已确认接受"Linux 强制走 XWayland"这一取舍。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform.startswith("linux") and "QT_QPA_PLATFORM" not in os.environ:
    # 强制 X11/XWayland：绕开 QtWebEngine 在 Wayland 下的已知崩溃
    os.environ["QT_QPA_PLATFORM"] = "xcb"

# 配置中文输入法环境变量（必须在 QApplication 之前；Windows/macOS 自动跳过）
# 详见 src/utils/input_method.py 的模块文档
from src.utils.input_method import setup_input_method  # noqa: E402
setup_input_method()

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from src.bridge import Bridge  # noqa: E402
from src.config.settings import APP_NAME, APP_ORG, APP_VERSION  # noqa: E402
from src.ui.web_shell import (  # noqa: E402
    WebShell,
    build_app,
    resolve_index_html,
    resolve_web_root,
)
from src.ui.media_overlay import MediaOverlay  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402
from src.utils.icon import get_app_icon  # noqa: E402

logger = get_logger(__name__)

# 全局窗口引用（防止 GC 回收）
_shell = None


def _warn_missing_frontend() -> None:
    """前端产物缺失时给出明确指引，而不是白屏"""
    index = resolve_index_html()
    message = (
        f"找不到前端产物：\n{index}\n\n"
        "请先在 jflove-desktop/ui/ 下执行：\n"
        "    npm install\n"
        "    npm run build\n\n"
        f"产物目录应为：\n{resolve_web_root()}"
    )
    logger.error(message.replace("\n", " "))
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("JFLove 启动失败")
    box.setText("前端产物缺失")
    box.setInformativeText(message)
    box.exec()


def main() -> int:
    """应用程序入口函数"""
    app = build_app()
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    # 任务栏 / 托盘 / 通知中心的应用图标（仓库 images/icon.png|ico）
    app.setWindowIcon(get_app_icon())

    # v1.1.5：执行旧版本数据迁移（QSettings → 用户目录 JSON 文件）
    from src.services.auth_service import _migrate_legacy_data
    _migrate_legacy_data()

    # 登录有效期上限（用户上次选择），供登录页作为下拉默认值
    from src.services.auth_service import load_local_session_max_seconds
    from src.utils.session import session_manager
    session_manager.local_session_max_seconds = load_local_session_max_seconds()

    global _shell
    bridge = Bridge()
    _shell = WebShell(
        objects={"bridge": bridge},
        frameless=True,
        publish_event=bridge.publish_event,
    )

    # 原生媒体浮层（N7-b）：StreamProxy + QMediaPlayer / Qt 原生解码
    # —— 解码能力走本地（OS 解码器），不用 Chromium 的 <video>/<img>
    overlay = MediaOverlay(get_view=lambda: _shell.view, publish_event=bridge.publish_event)
    from src.bridge import files as files_bridge
    from src.bridge import media as media_bridge

    media_bridge.setup(overlay)
    files_bridge.set_main_window(_shell)

    # 同步引擎（N9）：把引擎信号转成桥事件推给渲染层
    from src.bridge import sync as sync_bridge

    sync_bridge.setup(bridge.publish_event)

    # 传输管理器（N10 尾巴）：任务真相在 Python，渲染层只做展示
    from src.bridge import transfer as transfer_bridge

    transfer_bridge.setup(bridge.publish_event)

    # 关闭窗口 = 最小化到托盘（N17）：必须关掉"最后一个窗口关闭即退出"，
    # 否则隐藏唯一窗口会直接把应用结束掉。
    app.setQuitOnLastWindowClosed(False)
    _shell.setup_tray(publish_event=bridge.publish_event)

    if not _shell.load_ui():
        _warn_missing_frontend()
        return 2

    _shell.show()
    logger.info("JFLove 桌面应用已启动（v%s，Web UI）", APP_VERSION)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

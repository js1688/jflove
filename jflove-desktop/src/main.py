"""
JFLove 桌面应用入口

启动流程：
  1. 设置平台兼容性环境变量（Wayland 平台插件 + 输入法模块）
  2. 创建 QApplication
  3. 尝试免登录恢复（token 在有效期内直接进主界面）
  4. 若无有效 token，显示登录窗口
  5. 登录成功后切换到主窗口
  6. 退出登录后关闭主窗口并重新显示登录窗口
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.environ.get("WAYLAND_DISPLAY") and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "wayland"

# 配置中文输入法环境变量（必须在 QApplication 之前；Windows/macOS 自动跳过）
# 详见 src/utils/input_method.py 的模块文档
from src.utils.input_method import setup_input_method  # noqa: E402
setup_input_method()

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from qfluentwidgets import setTheme, Theme  # noqa: E402

from src.config.settings import APP_NAME, APP_ORG, APP_VERSION  # noqa: E402
from src.utils.icon import get_app_icon  # noqa: E402
from src.utils.session import session_manager  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

# 全局窗口引用（防止 GC 回收）
# v1.1.1：明确"任意时刻最多一个 LoginWindow + 一个 MainWindow"。
# 切换前先 close 上一个窗口并置 None，杜绝两个登录窗 / 两个主窗口并存的情况。
_login_window = None
_main_window = None


def _show_login_window() -> None:
    """创建并显示登录窗口（若已存在则只激活，不重复创建）"""
    global _login_window
    from src.ui.login_window import LoginWindow
    # 兜底：发现已有 _login_window 则复用，避免在异常路径产生多个登录窗
    if _login_window is not None:
        try:
            _login_window.show()
            _login_window.raise_()
            _login_window.activateWindow()
        except RuntimeError:
            # 底层 C++ 对象已销毁的情况，重新创建
            _login_window = None
    if _login_window is None:
        _login_window = LoginWindow()
        _login_window.login_success.connect(_on_login_success)
        _login_window.show()


def _on_login_success(role: str) -> None:
    """
    登录成功回调：关闭登录窗口，打开主窗口。

    :param role: 登录用户的角色（admin / user）
    """
    global _login_window, _main_window

    from src.ui.main_window import MainWindow

    # 防御：若已存在主窗口（异常路径下可能发生），先销毁旧实例
    if _main_window is not None:
        try:
            _main_window.close()
        except RuntimeError:
            pass
        _main_window = None

    _main_window = MainWindow(role=role)
    _main_window.logout_requested.connect(_on_logout)
    _main_window.show()

    if _login_window:
        try:
            _login_window.close()
        except RuntimeError:
            pass
        _login_window = None

    logger.info("主窗口已打开，角色=%s", role)


def _on_logout() -> None:
    """
    退出登录回调：关闭主窗口，重新显示登录窗口。

    main_window 端的 _logout_in_flight 互斥保证此回调每次会话最多被触发一次；
    本函数自身仍做防御性清理，确保不会出现两个登录窗或两个主窗并存。

    v1.1.4 修正：登出前先设置 _logout_in_flight=True，确保 closeEvent 走"接受"分支
    而非"隐藏到托盘"；然后 setParent(None) 解绑父子关系，close() 触发 closeEvent →
    accept，最后 deleteLater() 标记 Qt 延迟删除对象——三重保障杜绝旧窗口残留。
    """
    global _main_window
    if _main_window is not None:
        try:
            # 确保 closeEvent 走"登出→接受并销毁"分支，而非"隐藏到托盘"
            _main_window._logout_in_flight = True
            _main_window.setParent(None)
            _main_window.close()
            _main_window.deleteLater()
        except RuntimeError:
            pass
        _main_window = None
    _show_login_window()
    logger.info("已退出登录，返回登录页")


def main() -> None:
    """应用程序入口函数"""
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    # 应用全局图标（任务栏 / Dock / 通知中心默认使用此图标）
    app.setWindowIcon(get_app_icon())

    setTheme(Theme.AUTO)

    # v1.1.5：执行旧版本数据迁移（QSettings → 用户目录 JSON 文件）
    from src.services.auth_service import _migrate_legacy_data
    _migrate_legacy_data()

    # v1.1.5 修复：从 session.json 读取上次保存的服务端地址，
    # 替代之前通过 QSettings 读取 "server_url" 的方式（与 auth_service
    # 存储的 "session/server_url" 键名不一致导致永远读不到）。
    # 免登录恢复时 do_key_exchange 会设置 session_manager.server_url，
    # 这里只用于登录页初始化前的默认值。
    from src.services.auth_service import load_local_session_max_seconds
    session_manager.local_session_max_seconds = load_local_session_max_seconds()

    # 显示登录窗口（内部会尝试免登录自动跳转）
    _show_login_window()

    logger.info("JFLove 桌面应用已启动（v%s）", APP_VERSION)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

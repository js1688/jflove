"""
JFLove 桌面应用入口（Web UI 版）

启动流程：
  1. 平台兼容性环境变量（Linux 走 XWayland / 中文输入法）
  2. 创建 QApplication
  3. 旧版本数据迁移 + 读取本地登录有效期上限
  4. 创建无边框 WebShell（QtWebEngine 渲染前端产物）+ JS↔Python 桥
  5. 免登录恢复由前端在启动引导里经桥询问 Python（`auth.restore_session`）

## 与旧入口的关系

v1.5.0 起桌面端界面换轨为 Web UI。旧的 Qt/Fluent 界面（登录窗口 / 主窗口 / `ui/pages/*` /
`ui/theme/*` / `ui/kit/*` / 预览对话框 / Markdown 视图等）**已从仓库物理删除**（提交 962062f），
磁盘上不再有这些文件，不必也不能再按旧路径去找它们；需要查阅当时的实现请走 git 历史
（`git show 962062f^:<旧路径>`，路径可从 `git show --stat 962062f` 里取）。

## Linux 为什么强制 XWayland

QtWebEngine 在 Wayland 下有崩溃前科（PySide6 6.11 + Qt 6.10 + Wayland 的
PYSIDE-2700 系列；原始排查记录随已删除的原生界面留在 git 历史里，另见计划 §10）。
用户已确认接受"Linux 强制走 XWayland"这一取舍。
"""

import sys
import os
import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Linux 平台与输入法必须**成对**决定，且都要在 QApplication 之前：
#   - Wayland 会话 + Wayland 输入法前端 ⇒ wayland 平台 + QT_IM_MODULE=wayland
#     （xcb 下 PySide6 没有可用的 fcitx 插件，会彻底失去中文输入）
#   - 其余情况 ⇒ xcb（XWayland 更稳）
# 需要强制回退时设置 JFLOVE_QPA_PLATFORM=xcb。
from src.utils.input_method import (  # noqa: E402
    configure_linux_qt_platform,
    setup_input_method,
)
configure_linux_qt_platform()
setup_input_method()

from PySide6.QtCore import QTimer  # noqa: E402
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

# 启动期路径交付要用到来源常量；`bootstrap_single_instance` / `build_paths_sink`
# 是**模块级**函数，故这里必须是模块级导入（其余单实例符号仍按文件风格延迟导入）。
from src.utils.single_instance import SOURCE_CLI  # noqa: E402

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


def start_mount_supervisor(app) -> QTimer:
    """
    周期性保证「已启用的挂载点」处于挂载 + 自动同步状态（v1.7.0 修复）。

    为什么放在外壳而不是挂载页：同步是**后台能力**，用户不打开挂载页也必须生效
    （否则「我往文件夹里放了文件却没上传」）。挂载动作会阻塞（等待 FUSE 就绪），
    因此放到工作线程，避免卡住 UI。

    :param app: `QApplication`
    :returns: 定时器（父对象为 app）
    """
    import threading

    lock = threading.Lock()

    def _tick() -> None:
        if not lock.acquire(blocking=False):
            return  # 上一轮还没跑完（例如 FUSE 等待中）
        try:
            threading.Thread(
                target=lambda: _restore_and_release(lock),
                name="jflove-mount-supervisor",
                daemon=True,
            ).start()
        except Exception:  # noqa: BLE001 - 兜底任务失败不能影响应用
            lock.release()
            logger.warning("挂载监督任务启动失败", exc_info=True)

    def _restore_and_release(held: threading.Lock) -> None:
        try:
            from src.services import mount_sync_service

            mount_sync_service.ensure_auto_sync()
        except Exception:  # noqa: BLE001
            logger.warning("恢复挂载点失败", exc_info=True)
        finally:
            held.release()

    timer = QTimer(app)
    timer.timeout.connect(_tick)
    timer.start(10_000)
    QTimer.singleShot(2_000, _tick)  # 启动后尽快恢复一次
    return timer


def install_termination_hook(app) -> QTimer:
    """
    让 `SIGTERM` / `SIGINT` 走正常退出路径（v1.7.0 Linux 修复）。

    QtWebEngine（Chromium）会接管 `SIGTERM`，进程被"收尸"的时机不可控，
    Qt 事件循环也可能不返回 ⇒ `main()` 的 `finally` 不执行 ⇒ FUSE 挂载点残留成
    「传输端点尚未连接」的死挂载（Windows CfAPI 同理不会注销）。
    这里注册信号处理器，并用低频定时器把控制权周期性交回解释器
    （Qt 事件循环在 C 里跑时，Python 信号处理器不会被调度）。

    :param app: `QApplication`
    :returns: 定时器（父对象为 app，无需额外持有；返回值便于测试）
    """
    def _quit(*_args) -> None:
        logger.info("收到退出信号，走正常退出流程（会解除系统挂载）")
        app.quit()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _quit)
        except (ValueError, OSError):  # 非主线程 / 平台不支持
            logger.warning("注册信号处理器失败：%s", sig)

    timer = QTimer(app)
    timer.timeout.connect(lambda: None)
    timer.start(200)
    return timer


def bootstrap_single_instance(guard, cli_paths: list[str]) -> bool:
    """
    单实例接入（设计 v1.6.0 §6.6.1 / §6.6.3，AC-46 / AC-48）。

    为什么抽成函数：这是**启动期的分支决策**，纯逻辑（只依赖 `guard` 的两个方法与
    `logger`），不碰 Qt ⇒ 可以在单测里用假 guard 跑全部分支（原先直接写在 `main()`
    里，只能靠人工点界面验，M-6 就是在这种"没人测得到"的缝里漏掉的）。

    :param guard: `SingleInstanceGuard`（或同接口替身）
    :param cli_paths: 已规范化的命令行路径
    :returns: True = 继续启动本进程；False = 路径已成功转交给运行中的实例，本进程应退出
    """
    if not guard.try_become_primary():
        # 非首实例：把路径转交首实例后退出。转交失败**降级为本地启动**，
        # 绝不静默丢弃用户"用 JFLove 打开"的意图（§6.6.1）。
        if guard.forward(cli_paths, cwd=os.getcwd()):
            logger.info("已转交 %d 个待打开文件给运行中的实例，本进程退出", len(cli_paths))
            return False
        logger.warning("单实例转交失败，本进程降级为本地启动（可能出现两个实例）")
    # 首实例，**以及转交失败降级的第二实例**，都要把路径交给待交付队列：
    # 为什么不能在这里直接交付：渲染层此刻还不存在，交付即丢失（§7.5）。
    # （M-6：原先只有"首实例"一支会进这里 ⇒ 降级分支把用户双击的文件全丢了。）
    if cli_paths:
        guard.notify_external_paths(cli_paths, source=SOURCE_CLI)
    return True


def build_paths_sink(shell):
    """
    构造"待打开路径交付给渲染层"的接入点（设计 §6.6.3 / §7.5③）。

    顺序是硬要求：**先让主窗口可见，再交付路径**。否则主实例在托盘/最小化状态下被
    「用 JFLove 打开」唤起时，窗格确实建出来了，但窗口还藏在托盘里，用户以为没反应
    （AC-46②；S-4）。

    :param shell: `WebShell`（或同接口替身：需有 `restore_from_tray()` /
                  `notify_external_paths(paths, source)`）
    :returns: `(paths, source) -> None`，可直接交给 `SingleInstanceGuard.set_paths_sink`
    """

    def _sink(paths, source) -> None:
        try:
            shell.restore_from_tray()
        except Exception as exc:  # noqa: BLE001 - 可见性失败不应阻断"打开文件"本身
            logger.warning("恢复主窗口可见性失败（继续交付路径）：%s", exc)
        shell.notify_external_paths(paths, source)

    return _sink


def main() -> int:
    """应用程序入口函数"""
    app = build_app()
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    # 任务栏 / 托盘 / 通知中心的应用图标（仓库 images/icon.png|ico）
    app.setWindowIcon(get_app_icon())
    install_termination_hook(app)
    start_mount_supervisor(app)

    # ── v1.6.0 单实例 + 命令行路径（F7.4 / AC-46，设计 §6.6.3）──────────────
    # 判定时机必须在**创建 WebShell 之前**：否则第二实例会先闪一个窗口、再建出第二个
    # 托盘图标（tray.py 每进程各建一个），然后才退出 —— 那正是单实例要消灭的现象。
    from src.utils.single_instance import (  # noqa: E402 - 与文件既有风格一致（延迟导入）
        SingleInstanceGuard,
        build_server_name,
        normalize_paths,
    )

    cli_paths = normalize_paths(sys.argv[1:])
    guard = SingleInstanceGuard(build_server_name(APP_ORG, APP_NAME))
    if not bootstrap_single_instance(guard, cli_paths):
        # 路径已成功转交给运行中的实例：本进程**不**建窗口、不建托盘图标，直接退出
        return 0

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
    # 把浮层挂到外壳上（#3）：主窗拖动/缩放 → 浮层重贴合；主窗隐藏到托盘 → 浮层一起隐藏
    _shell.attach_overlay(overlay)
    from src.bridge import files as files_bridge
    from src.bridge import media as media_bridge

    media_bridge.setup(overlay)
    files_bridge.set_main_window(_shell)

    # 文本编辑器桥（v1.6.0 §7.1 依赖注入）：QFileDialog 需要主窗口作 parent，
    # 故与 files_bridge 同样注入；缺它会直接抛 RuntimeError（不静默降级为无 parent 对话框）。
    from src.bridge import editor as editor_bridge

    editor_bridge.set_main_window(_shell)

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

    # 单实例：**UI 就绪之后**才注入接入点 ⇒ 队列里（命令行 / 另一实例转交）的路径
    # 此刻按序一次性交付渲染层（设计 §6.6.3 / §7.5）。接入点只应有这一个（避免重复打开）。
    # sink 内部**先恢复窗口可见性再交付**（AC-46②；S-4）。
    guard.set_paths_sink(build_paths_sink(_shell))

    _shell.show()
    logger.info("JFLove 桌面应用已启动（v%s，Web UI）", APP_VERSION)
    try:
        return app.exec()
    finally:
        # 退出前关掉监听，避免在磁盘上留下陈旧 socket（下次启动由 §6.6.1 的兜底清理）
        guard.close()
        # v1.7.0 修复：退出前必须解除 CfAPI 挂载。
        # 只结束进程而不注销，sync root 会继续被 Cloud Filter 接管 ⇒
        # 目录进不去、也删不掉（用户实测缺陷）。
        try:
            from src.services import mount_sync_service

            mount_sync_service.disable_all_vfs()
        except Exception:  # noqa: BLE001 - 退出路径不能再抛
            logger.warning("退出时解除 CfAPI 挂载失败", exc_info=True)


if __name__ == "__main__":
    sys.exit(main())

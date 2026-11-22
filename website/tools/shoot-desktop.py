#!/usr/bin/env python3
"""桌面端真实截图器（官网操作指引专用）。

为什么需要单独一个工具：
    仓库自带的 `scripts/verify/desktop_shot.py` 只构造 `WebShell(objects={})`，
    **不注入 bridge**，因此只能截到登录页 —— 登录页之后的每一个动作
    （选磁盘、传文件、写笔记）都走 QWebChannel 桥，没有桥就没有数据。

本工具复现生产启动时的 bridge 注入（`src/main.py:254-292` 做的事），
再用 `runJavaScript` 在渲染层自动填表登录，然后逐路由 `grab()` 落盘。

用法：
    # 先起一个临时后端（截图需要真实数据）
    bash scripts/verify/server.sh start
    # 再跑本工具
    jflove-desktop/venv-linux/bin/python website/tools/shoot-desktop.py \\
        --user admin --pass <口令> --out website/design/v2/screenshots/desktop

注意：
    - 默认 `QT_QPA_PLATFORM=offscreen`，无显示器也能跑；
    - 脚本**不修改**产品任何源码，只在内存里构造窗口；
    - 截图后的 PNG 由调用方决定放哪（官网放 `website/`）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

parser = argparse.ArgumentParser(description="桌面端真实截图（注入 bridge，可登录）")
parser.add_argument("--out", default="/tmp/jf-desktop-shots")
parser.add_argument("--width", type=int, default=1440)
parser.add_argument("--height", type=int, default=900)
parser.add_argument("--user", default="admin")
parser.add_argument("--pass", dest="password", default="")
parser.add_argument("--server", default="http://127.0.0.1:8989")
parser.add_argument("--wait", type=int, default=4000, help="每步等待毫秒")
args = parser.parse_args()

# 必须在导入 PySide6 之前设置
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox --disable-dev-shm-usage")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 关键：用**干净的临时用户数据目录**，否则会读到本机残留的旧会话
# （实测：~/.local/share/JFLove/storage 里留着 vmtest 的旧 JWT，
#   渲染层拿它发请求 → 401「令牌无效或已过期」→ 截到的全是错误页）。
# Linux 的数据目录由 XDG_DATA_HOME 决定，改它即可完全隔离，且不动真实用户数据。
_CLEAN_DATA_HOME = Path(args.out).resolve().parent / "_xdg"
(_CLEAN_DATA_HOME / "JFLove").mkdir(parents=True, exist_ok=True)
os.environ["XDG_DATA_HOME"] = str(_CLEAN_DATA_HOME)

sys.path.insert(0, str(REPO_ROOT / "jflove-desktop"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT_DIR = Path(args.out)
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 要截的页面：(路由 hash, 文件名)
# 桌面端用 hash 路由（createHashRouter），与 Web 端的 history 路由不同
PAGES: list[tuple[str, str]] = [
    ("#/files", "files-disks"),
    ("#/notes", "notes"),
    ("#/editor", "editor"),
    ("#/sync", "sync"),
    ("#/transfer", "transfer"),
    ("#/offline", "offline"),
    ("#/remote", "remote"),
    ("#/security", "security"),
    ("#/settings", "settings"),
    ("#/admin/users", "admin-users"),
    ("#/admin/disks", "admin-disks"),
    ("#/admin/permissions", "admin-permissions"),
    ("#/admin/system", "admin-system"),
]


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # 复现生产启动的注入顺序（src/main.py:254-292）
    from src.bridge import Bridge
    from src.ui.web_shell import WebShell

    bridge = Bridge()
    shell = WebShell(
        objects={"bridge": bridge},
        frameless=False,  # 截图要带标题栏，便于看出这是桌面客户端
        publish_event=bridge.publish_event,
    )
    shell.resize(args.width, args.height)
    shell.show()

    from src.bridge import files as files_bridge
    from src.bridge import editor as editor_bridge
    from src.bridge import sync as sync_bridge
    from src.bridge import transfer as transfer_bridge
    from src.bridge import remote as remote_bridge

    files_bridge.set_main_window(shell)
    editor_bridge.set_main_window(shell)
    sync_bridge.setup(bridge.publish_event)
    transfer_bridge.setup(bridge.publish_event)
    remote_bridge.init_host()

    if not shell.load_ui():
        print("[shoot-desktop] 找不到前端产物（先 cd jflove-desktop/ui && npm run build）", file=sys.stderr)
        return 2

    state = {"stage": "login", "page": 0, "failures": []}

    def js(code: str) -> None:
        shell.view.page().runJavaScript(code)

    def shot(name: str) -> None:
        pixmap = shell.grab()
        path = OUT_DIR / f"{name}.png"
        ok = pixmap.save(str(path))
        print(f"[shoot-desktop] grab {pixmap.width()}x{pixmap.height()} save={ok} -> {path}")
        if not ok:
            state["failures"].append(name)

    def step_login() -> None:
        """① 填服务端地址 → 点「连接服务端」。"""
        print("[shoot-desktop] 步骤 1/3：连接服务端")
        js(f"""
          (() => {{
            const inputs = [...document.querySelectorAll('input')];
            if (!inputs.length) return 'no-input';
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(inputs[0], {args.server!r});
            inputs[0].dispatchEvent(new Event('input', {{ bubbles: true }}));
            inputs[0].dispatchEvent(new Event('change', {{ bubbles: true }}));
            const btns = [...document.querySelectorAll('button')];
            const b = btns.find((n) => (n.textContent || '').includes('连接服务端'));
            if (b) {{ b.click(); return 'connected'; }}
            return 'no-button';
          }})()
        """)
        QTimer.singleShot(args.wait, step_fill)

    def step_fill() -> None:
        """② 填账号密码 → 点「登录」。"""
        print("[shoot-desktop] 步骤 2/3：填账号密码并登录")
        js(f"""
          (() => {{
            const inputs = [...document.querySelectorAll('input')];
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            const pwd = inputs.find((el) => (el.getAttribute('type') || '').toLowerCase() === 'password');
            const usr = inputs.find((el) => (el.getAttribute('type') || '').toLowerCase() !== 'password');
            if (!usr || !pwd) return 'no-login-form';
            setter.call(usr, {args.user!r});
            usr.dispatchEvent(new Event('input', {{ bubbles: true }}));
            setter.call(pwd, {args.password!r});
            pwd.dispatchEvent(new Event('input', {{ bubbles: true }}));
            const btns = [...document.querySelectorAll('button')];
            const b = btns.find((n) => /登录/.test(n.textContent || ''));
            if (b) {{ b.click(); return 'submitted'; }}
            return 'no-login-button';
          }})()
        """)
        QTimer.singleShot(args.wait * 2, check_login)

    def check_login() -> None:
        """③ 确认进了主界面，然后开始逐页截图。"""
        shell.view.page().runJavaScript(
            "location.hash", 0, lambda v: after_login(v)
        )

    def after_login(hash_value) -> None:
        print(f"[shoot-desktop] 登录后路由：{hash_value}")
        if not hash_value or 'login' in str(hash_value):
            state["failures"].append("login")
            print("[shoot-desktop] ❌ 登录未成功（可能口令错误或后端未启动）", file=sys.stderr)
            shot("login-failed")
            app.quit()
            return
        state["stage"] = "pages"
        capture_next()

    def capture_next() -> None:
        idx = state["page"]
        if idx >= len(PAGES):
            print(f"[shoot-desktop] 完成，失败项：{state['failures'] or '无'}")
            app.quit()
            return
        route, name = PAGES[idx]
        print(f"[shoot-desktop] 截第 {idx + 1}/{len(PAGES)} 页：{name} ({route})")
        shell.view.page().runJavaScript(f"location.hash = {route!r}; 0")

        def do_shot() -> None:
            shot(name)
            state["page"] += 1
            QTimer.singleShot(600, capture_next)

        QTimer.singleShot(args.wait, do_shot)

    # 先截登录页（未登录状态）
    def first_shot() -> None:
        print("[shoot-desktop] 步骤 0/3：截登录页")
        shot("login")
        QTimer.singleShot(500, step_login)

    QTimer.singleShot(args.wait, first_shot)

    code = app.exec()
    remote_bridge.shutdown()
    return 1 if state["failures"] else code


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""桌面端 Web UI 离屏抓帧（无需显示器）。

用途：真实进程启动靠日志 + 窗口存在性判定；本脚本负责「像素证据」——
在 QtWebEngine 里加载 `ui/dist`，等页面稳定后 `QWidget.grab()` 落盘。

用法：
    jflove-desktop/venv-linux/bin/python scripts/verify/desktop_shot.py
    ... --out /tmp/desktop.png --width 1180 --height 760 --wait 9000

说明：
- 默认 `QT_QPA_PLATFORM=offscreen`，可在锁屏/无显示器环境跑；
- 只构造 `WebShell`（不注入 Bridge），验证的是前端产物渲染，不是完整业务链路；
- 完整链路用 `jflove-desktop/src/main.py` 真实启动 + 日志/接口判定。
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

parser = argparse.ArgumentParser(description="桌面端 Web UI 离屏抓帧")
parser.add_argument("--out", default="/tmp/jf-desktop.png")
parser.add_argument("--width", type=int, default=1180)
parser.add_argument("--height", type=int, default=760)
parser.add_argument("--wait", type=int, default=9000, help="等页面加载的毫秒数")
parser.add_argument("--platform", default="offscreen", help="offscreen / xcb")
args = parser.parse_args()

os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --no-sandbox --disable-dev-shm-usage",
)
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
if args.platform:
    os.environ["QT_QPA_PLATFORM"] = args.platform

sys.path.insert(0, os.path.join(ROOT, "jflove-desktop"))

from PySide6.QtCore import QTimer  # noqa: E402
from src.ui.web_shell import WebShell, build_app  # noqa: E402


def main() -> int:
    app = build_app()
    shell = WebShell(objects={})
    shell.resize(args.width, args.height)
    shell.show()
    if not shell.load_ui():
        print("[verify] 找不到前端产物（先 cd jflove-desktop/ui && npm run build）", file=sys.stderr)
        return 2

    def shot() -> None:
        pixmap = shell.grab()
        ok = pixmap.save(args.out)
        print(f"[verify] grab {pixmap.width()}x{pixmap.height()} save={ok} out={args.out}")
        app.quit()
        if not ok:
            raise SystemExit(1)

    QTimer.singleShot(args.wait, shot)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

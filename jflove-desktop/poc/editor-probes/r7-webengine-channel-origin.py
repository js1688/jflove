# -*- coding: utf-8 -*-
"""R-7 实测：WebChannel / 桥是否只暴露给 `file://` 自家产物？

背景（v1.6.0 安全边界）：本版给渲染层新增了 `editor.readTextOnly` /
`editor.detectAndRead` / `editor.writeText`，渲染层因此获得**任意路径本地文件读写**。
这条能力的安全前提是设计 §8.2 的假设——「渲染层只加载自家本地产物」。
但外壳 `src/ui/web_shell.py` 只做了两件事：
    1. `settings.LocalContentCanAccessRemoteUrls = False`（只管**子资源/fetch**，不管顶层导航）
    2. `page().setWebChannel(channel)` + 以 `DocumentCreation`/`MainWorld` 注入
       `:/qtwebchannel/qwebchannel.js`（**对该 page 的每个文档生效**，不分来源）
全仓 `src/` 无 `acceptNavigationRequest` / `createWindow` / `newWindowRequested`（grep 0 命中），
即**没有任何导航策略**。于是"渲染层只能加载自家产物"仅靠 Qt 默认行为，未被固化。

本探针回答三个问题（全部为可复现的进程内实测，不改任何项目源码）：
    T1 对照：`file://` 自家页能否拿到 `qt.webChannelTransport` 并真正调用到 Python 槽？
    T2 关键：`http://127.0.0.1:<port>` 的非本地源文档（模拟"被导航到的远端页"，
       该页自带一份 `qwebchannel.js`，与真实攻击页做法一致）能否同样调用到 Python 槽？
    T3 导航：从 `file://` 页用 JS `location.href = http://...` 能否真的改写顶层导航？
    T4 新窗：`<a target="_blank">` 外链点击时，Qt 默认行为是**导航本 view** /
       什么都不做 / 开新窗？（本项目未覆写 `createWindow`，故走的正是默认路径）

判据：T1 为必须成立的对照（否则本探针的观测链路无效，T2 结论作废）。
T2 若成立 ⇒ 桥暴露与文档来源无关，"只加载自家产物"这条边界一旦被任何导航路径打破，
远端页即可直接调用桥方法（本版可写任意本地文件）。

原始输出写本目录 out/：
    out/r7-<platform>.json   结构化证据
    out/r7-<platform>.log    完整原始 stdout/stderr

运行：
    jflove-desktop/venv-win/Scripts/python.exe jflove-desktop/poc/editor-probes/r7-webengine-channel-origin.py --platform=offscreen
    jflove-desktop/venv-win/Scripts/python.exe jflove-desktop/poc/editor-probes/r7-webengine-channel-origin.py --platform=windows
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

# 原始输出统一 UTF-8（Windows 控制台默认 GBK）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:  # noqa: BLE001
        pass

# ── 平台与 Chromium 开关必须在 import QtWebEngineWidgets 之前设好 ──────────────
_parser = argparse.ArgumentParser(add_help=True)
_parser.add_argument("--platform", default=os.environ.get("R7_PLATFORM", "offscreen"))
_parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
_ARGS, _ = _parser.parse_known_args()

os.environ["QT_QPA_PLATFORM"] = _ARGS.platform
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

import PySide6  # noqa: E402
from PySide6.QtCore import QFile, QIODevice, QObject, QTimer, QUrl, Slot  # noqa: E402
from PySide6.QtWebChannel import QWebChannel  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineScript  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT_DIR = Path(_ARGS.out)
OUT_DIR.mkdir(parents=True, exist_ok=True)
PLATFORM = _ARGS.platform

# ── 被测页面：自带 qwebchannel.js（远端页的真实做法）────────────────────────

PAGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>r7 probe</title>
<script src="qwebchannel.js"></script>
<script>
window.__detect = function (tag) {
  var r = {tag: tag, url: location.href, origin: location.origin,
           scheme: location.protocol, hasQt: (typeof qt !== 'undefined'),
           hasTransport: false, hasQWebChannelClass: (typeof QWebChannel !== 'undefined'),
           objects: [], pinged: false, err: ''};
  try { r.hasTransport = !!(window.qt && qt.webChannelTransport); }
  catch (e) { r.err += 'T:' + e; }
  if (!r.hasTransport || typeof QWebChannel === 'undefined') {
    window.__last = r;
    return JSON.stringify(r);
  }
  try {
    new QWebChannel(qt.webChannelTransport, function (ch) {
      r.objects = Object.keys((ch && ch.objects) || {});
      var api = (ch.objects || {}).probe;
      if (api && api.ping) {
        r.pinged = true;
        api.ping('origin=' + location.origin + ' scheme=' + location.protocol + ' tag=' + tag);
      }
      window.__last = r;
    });
  } catch (e) { r.err += 'C:' + e; window.__last = r; }
  return JSON.stringify(r);
};
</script></head>
<body style="font-family: sans-serif">
<a id="lnk" href="http://127.0.0.1:1/never" target="_blank">external</a>
</body></html>
"""


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # 静音
        return


def _read_qwebchannel_js() -> str:
    source = QFile(":/qtwebchannel/qwebchannel.js")
    if not source.open(QIODevice.OpenModeFlag.ReadOnly):
        raise SystemExit("无法读取 :/qtwebchannel/qwebchannel.js（需先 import PySide6.QtWebChannel）")
    code = bytes(source.readAll().data()).decode("utf-8")
    source.close()
    return code


def _run_probe(app: QApplication) -> dict:
    results: dict = {"platform": PLATFORM, "qt": PySide6.__version__, "phases": {}}
    calls: list[str] = []

    class Probe(QObject):
        """桥上的一个可调用对象（对应真实项目里的 shell / editor 命名空间）"""

        @Slot(str)
        def ping(self, msg: str) -> None:  # noqa: D102
            calls.append(msg)

    # 临时目录：control.html（file:// 对照）+ probe.html & qwebchannel.js（本地 http 服务）
    workdir = Path(tempfile.mkdtemp(prefix="r7-probe-"))
    (workdir / "qwebchannel.js").write_text(_read_qwebchannel_js(), encoding="utf-8")
    (workdir / "probe.html").write_text(PAGE_HTML, encoding="utf-8")
    (workdir / "control.html").write_text(PAGE_HTML, encoding="utf-8")

    handler = functools.partial(_Handler, directory=str(workdir))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    http_url = f"http://127.0.0.1:{port}/probe.html"
    file_url = (workdir / "control.html").as_uri()
    results["http_url"] = http_url
    results["file_url"] = file_url

    view = QWebEngineView()
    view.resize(900, 600)
    channel = QWebChannel()
    # 必须保住 Python 侧强引用：registerObject 不转移所有权，临时对象会被 GC 掉，
    # 之后渲染层调用 `ping` 就是打在已析构的 C++ 对象上（实测直接 0xC0000005）。
    probe_api = Probe()
    channel.registerObject("probe", probe_api)
    page = view.page()
    # 与 src/ui/web_shell.py:426-432 + :540-556 完全一致的接线
    page.setWebChannel(channel)
    script = QWebEngineScript()
    script.setName("qwebchannel.js")
    script.setSourceCode(_read_qwebchannel_js())
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    page.scripts().insert(script)
    view.show()

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def load(url: str, timeout: float = 20.0) -> bool:
        box = {"done": False, "ok": False}

        def on_finished(ok: bool) -> None:
            box["done"] = True
            box["ok"] = bool(ok)

        page.loadFinished.connect(on_finished)
        view.load(QUrl(url))
        end = time.time() + timeout
        while not box["done"] and time.time() < end:
            app.processEvents()
            time.sleep(0.02)
        try:
            page.loadFinished.disconnect(on_finished)
        except Exception:  # noqa: BLE001
            pass
        pump(0.2)
        return box["ok"]

    def js(code: str, timeout: float = 8.0):
        box: dict = {"done": False, "val": None}

        def cb(value) -> None:
            box["val"] = value
            box["done"] = True

        page.runJavaScript(code, cb)
        end = time.time() + timeout
        while not box["done"] and time.time() < end:
            app.processEvents()
            time.sleep(0.02)
        return box["val"]

    def detect(tag: str) -> dict:
        """同步取一次检测结果，再等异步 QWebChannel 回调后的 `__last`"""
        sync_raw = js(f"window.__detect({json.dumps(tag)})")
        pump(1.0)
        late_raw = js("window.__last ? JSON.stringify(window.__last) : null")
        out: dict = {"sync": None, "async": None, "page_url": page.url().toString()}
        for key, raw in (("sync", sync_raw), ("async", late_raw)):
            if isinstance(raw, str) and raw:
                try:
                    out[key] = json.loads(raw)
                except Exception as exc:  # noqa: BLE001
                    out[key] = {"parse_error": str(exc), "raw": raw[:400]}
        return out

    def snapshot() -> dict:
        return {
            "page_url": page.url().toString(),
            "scheme": page.url().scheme(),
            "top_level_widgets": len(app.topLevelWidgets()),
        }

    # ── T1 对照：file:// 自家页 ────────────────────────────────────────────
    calls.clear()
    t1_load = load(file_url)
    t1 = detect("T1-file")
    t1["load_ok"] = t1_load
    t1["bridge_calls"] = list(calls)
    results["phases"]["T1_file_control"] = t1
    print(f"[T1] file:// bridge_calls={calls!r}", flush=True)

    # ── T2 关键：http://127.0.0.1 远端源文档 ──────────────────────────────
    calls.clear()
    t2_load = load(http_url)
    t2 = detect("T2-http")
    t2["load_ok"] = t2_load
    t2["bridge_calls"] = list(calls)
    results["phases"]["T2_http_direct"] = t2
    print(f"[T2] http:// bridge_calls={calls!r}", flush=True)

    # ── T3 JS 顶层导航：file:// → http:// 是否真的发生 ─────────────────────
    calls.clear()
    load(file_url)
    before = snapshot()
    js(f"location.href = {json.dumps(http_url)}; 'nav'")
    pump(1.5)
    t3_detect = detect("T3-after-js-nav")
    after = snapshot()
    t3 = {
        "before": before,
        "after": after,
        "navigated_to_remote": after["scheme"] in ("http", "https"),
        "detect": t3_detect,
        "bridge_calls": list(calls),
    }
    results["phases"]["T3_js_top_level_nav"] = t3
    print(
        f"[T3] {before['page_url']} -> {after['page_url']} "
        f"nav={t3['navigated_to_remote']} calls={calls!r}",
        flush=True,
    )

    # ── T4 target=_blank 外链点击（走 Qt 默认 createWindow 路径）──────────
    calls.clear()
    load(file_url)
    before = snapshot()
    js("document.getElementById('lnk').click(); 'clicked'")
    pump(2.0)
    after = snapshot()
    t4 = {
        "before": before,
        "after": after,
        "bridge_calls": list(calls),
        "new_top_level_widget": after["top_level_widgets"] > before["top_level_widgets"],
        "same_view_navigated": after["page_url"] != before["page_url"],
    }
    results["phases"]["T4_blank_link_click"] = t4
    print(
        f"[T4] url {before['page_url']} -> {after['page_url']} "
        f"new_window={t4['new_top_level_widget']} calls={calls!r}",
        flush=True,
    )

    httpd.shutdown()
    return results


def main() -> int:
    app = QApplication(sys.argv)
    results = _run_probe(app)

    # 判据小结（自动生成的结论必须能一眼看出哪条对照成立）
    t1 = results["phases"]["T1_file_control"]
    t2 = results["phases"]["T2_http_direct"]
    t3 = results["phases"]["T3_js_top_level_nav"]
    t4 = results["phases"]["T4_blank_link_click"]
    results["verdict"] = {
        "T1_file_page_reached_bridge": bool(t1.get("bridge_calls")),
        "T2_http_page_reached_bridge": bool(t2.get("bridge_calls")),
        "T3_js_can_navigate_to_remote": bool(t3.get("navigated_to_remote")),
        "T4_blank_click_new_window": bool(t4.get("new_top_level_widget")),
        "T4_blank_click_navigated_same_view": bool(t4.get("same_view_navigated")),
    }
    text = json.dumps(results, ensure_ascii=False, indent=2)
    (OUT_DIR / f"r7-{PLATFORM}.json").write_text(text, encoding="utf-8")
    print("=== r7 verdict ===", flush=True)
    print(json.dumps(results["verdict"], ensure_ascii=False, indent=2), flush=True)
    print(f"（完整证据：{OUT_DIR / f'r7-{PLATFORM}.json'}）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

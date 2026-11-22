#!/usr/bin/env python3
"""官网截图用的静态服务：服务 dist + SPA fallback + /api 反代到后端。

用途：官网「操作指引」需要当前版本产品的真实界面截图（方案 B）。
它只在截图期间临时运行，不属于产品、不参与构建。

用法：
    python website/tools/proxy-dist.py --dist jflove-web/dist --port 8099 --backend http://127.0.0.1:8989
"""

from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import urllib.error
import urllib.request


def build_handler(root: str, backend: str):
    """构造带 SPA fallback 与 API 反代的请求处理器。"""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=root, **kwargs)

        def _proxy(self) -> None:
            """把 /api 与 /health 原样转发给后端（保持方法与请求头）。"""
            url = backend + self.path
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            req = urllib.request.Request(url, data=body, method=self.command)
            for key, value in self.headers.items():
                if key.lower() not in ("host", "content-length"):
                    req.add_header(key, value)
            try:
                resp = urllib.request.urlopen(req, timeout=30)
            except urllib.error.HTTPError as exc:
                resp = exc
            except urllib.error.URLError as exc:
                self.send_error(502, f"后端不可达：{exc.reason}")
                return
            self.send_response(resp.status)
            for key, value in resp.headers.items():
                if key.lower() not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(resp.read())

        def _serve_spa(self) -> None:
            """SPA fallback：真实文件按路径返回，其余一律回 index.html。"""
            rel = self.path.split("?")[0].lstrip("/")
            target = os.path.join(root, rel)
            if rel and os.path.exists(target) and not os.path.isdir(target):
                return super().do_GET()
            self.path = "/index.html"
            return super().do_GET()

        def do_GET(self) -> None:  # noqa: N802 - http.server 约定
            if self.path.startswith("/api/") or self.path.startswith("/health"):
                return self._proxy()
            return self._serve_spa()

        def do_POST(self) -> None:  # noqa: N802
            return self._proxy()

        def do_PUT(self) -> None:  # noqa: N802
            return self._proxy()

        def do_DELETE(self) -> None:  # noqa: N802
            return self._proxy()

        def log_message(self, *args):  # noqa: D401 - 静默，避免刷屏
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="截图用静态服务（SPA + API 反代）")
    parser.add_argument("--dist", default="jflove-web/dist")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--backend", default="http://127.0.0.1:8989")
    args = parser.parse_args()

    root = os.path.abspath(args.dist)
    if not os.path.isdir(root):
        raise SystemExit(f"[proxy] 缺少构建产物目录：{root}")

    handler = build_handler(root, args.backend)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as server:
        print(f"[proxy] {root} → :{args.port}，API 反代 {args.backend}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()

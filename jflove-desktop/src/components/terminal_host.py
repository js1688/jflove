"""
终端 WebSocket 通道（v1.10.0 起；v1.10.8 重建输出管道）

桌面端是「Python 外壳 + QtWebEngine 渲染层」两层结构：渲染层是 JS，拿不到
SSH/PTY 句柄，必须由 Python 侧把输出字节喂给 xterm.js。本模块就是这条管道。

**只监听 127.0.0.1**，每个会话一个随机 token；与 SSH/SFTP 直连链路无关，
不经过服务端（`文档记录/设计文档/v1.10.0.md` §1.1 / §3.4）。

## 输出管道契约（v1.10.8）

```
PTY/SSH 读线程 ──feed()──▶ Qt 队列 ──▶ GUI 线程：
                                        ① 追加待发缓冲
                                        ② 16ms 定时器合并成**一条**二进制帧下发
```

- **为什么合并**：TUI（vim / htop / dsh）每帧会拆成几十个小 chunk，逐条
  `sendBinaryMessage` 会把 GUI 线程和渲染层都拖垮；合并后是「1 帧/16ms」。
- **为什么不回放历史**：回放原始输出会让终端**重新应答** TUI 的能力查询
  （DA1 / OSC 10 / OSC 11 …），应答被 shell 回显成垃圾字符，形成自我污染的循环。
  渲染层的做法是把 xterm 实例挂在模块级注册表里，路由切换只挪 DOM 不销毁实例
  （见 `ui/src/components/remote/TerminalView.tsx`）。
- **控制帧**（文本帧 JSON，渲染层按 `type` 分派）：
  - 下行 `exit`（进程退出 / SSH 断开）
  - 上行 `input` / `resize` / `close` / `redraw`
"""

from __future__ import annotations

import hmac
import json
import secrets
from typing import Any

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrlQuery, Signal
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 输出合并间隔（毫秒）：一个刷新周期只发一帧
FLUSH_INTERVAL_MS = 16


class TerminalHost(QObject):
    """
    终端会话的本地 WebSocket 服务。

    - 下行：文本帧（控制消息）+ 二进制帧（UTF-8 原始字节）
    - 上行：文本帧 JSON（input / resize / close / redraw）

    读线程通过 `feed()` 投递数据，内部用 Qt Signal 切回 GUI 线程再发送。
    """

    #: (session_id, bytes) —— 跨线程投递输出
    _dataReady = Signal(str, bytes)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sessions: dict[str, Any] = {}
        self._sockets: dict[str, QWebSocket] = {}
        self._tokens: dict[str, str] = {}
        #: 会话 → 待发送输出（16ms 合并）
        self._pending: dict[str, bytearray] = {}
        self._server: QWebSocketServer | None = None
        self._port = 0
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(FLUSH_INTERVAL_MS)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_pending)
        self._dataReady.connect(self._on_output)
        self._start_server()

    # ── 对外 ──────────────────────────────────────────

    @property
    def port(self) -> int:
        """监听端口（未启动为 0）"""
        return self._port

    def register(self, session_id: str, session: Any) -> str:
        """
        登记一个会话并返回访问 token。

        :param session_id: 会话 ID
        :param session: 具备 `write/resize/close` 的会话对象
        :returns: 32 位十六进制 token
        """
        token = secrets.token_hex(16)
        self._sessions[session_id] = session
        self._tokens[session_id] = token
        self._pending.setdefault(session_id, bytearray())
        return token

    def feed(self, session_id: str, data: bytes) -> None:
        """把输出投递给渲染层（可从任意线程调用）"""
        if not data:
            return
        self._dataReady.emit(session_id, data)

    def notify_status(self, session_id: str, phase: str, message: str = "") -> None:
        """
        把「会话结束」推给渲染层（GUI 线程调用）。

        进程退出 / SSH 断开后画面不会再更新，前端据此显示「已退出」并给出重开入口，
        而不是留一块永远静止的黑屏。
        """
        if phase not in ("closed", "failed"):
            return
        self._send_text(
            session_id,
            {"type": "exit", "message": message, "phase": phase},
        )

    def close_session(self, session_id: str) -> None:
        """关闭会话并清理通道"""
        session = self._sessions.pop(session_id, None)
        if session is not None:
            try:
                session.close()
            except Exception as exc:  # noqa: BLE001 - 关闭阶段忽略
                logger.debug("关闭终端会话失败：%s", exc)
        sock = self._sockets.pop(session_id, None)
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
        self._tokens.pop(session_id, None)
        self._pending.pop(session_id, None)

    def shutdown(self) -> None:
        """关闭全部会话与监听（退出时调用）"""
        for sid in list(self._sessions.keys()):
            self.close_session(sid)
        if self._server is not None:
            self._server.close()
            self._server = None
        self._port = 0

    # ── 内部：服务端 ──────────────────────────────────

    def _start_server(self) -> None:
        server = QWebSocketServer("jflove-term", QWebSocketServer.SslMode.NonSecureMode)
        server.newConnection.connect(self._on_new_connection)
        # 显式 IPv4：Windows 上 LocalHost 可能绑定到 ::1，而前端连的是 127.0.0.1
        if not server.listen(QHostAddress("127.0.0.1"), 0):
            logger.error("终端通道监听失败：%s", server.errorString())
            return
        self._server = server
        self._port = int(server.serverPort())
        logger.info("终端通道已启动：127.0.0.1:%s", self._port)

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        url = sock.requestUrl()
        path = url.path()
        token = QUrlQuery(url).queryItemValue("token")
        parts = [p for p in path.split("/") if p]
        if len(parts) != 2 or parts[0] != "term":
            sock.close()
            return
        session_id = parts[1]
        expected = self._tokens.get(session_id, "")
        if not expected or not hmac.compare_digest(token, expected):
            logger.warning("终端通道拒绝非法 token：sid=%s", session_id)
            sock.close()
            return

        logger.info("终端通道已连接：sid=%s", session_id)
        self._sockets[session_id] = sock
        sock.textMessageReceived.connect(
            lambda text, sid=session_id: self._on_text(sid, text)
        )
        sock.disconnected.connect(
            lambda sid=session_id: self._on_disconnected(sid)
        )
        self._flush_session(session_id)

    def _on_disconnected(self, session_id: str) -> None:
        """通道断开：只清 socket，不关闭会话（允许前端重连/切标签回来）"""
        self._sockets.pop(session_id, None)
        logger.info("终端通道已断开：sid=%s", session_id)

    def _on_text(self, session_id: str, text: str) -> None:
        session = self._sessions.get(session_id)
        try:
            message = json.loads(text)
        except (TypeError, ValueError):
            return
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        try:
            if kind == "input":
                if session is not None:
                    session.write(str(message.get("data", "")).encode("utf-8"))
            elif kind == "resize":
                if session is not None:
                    cols = int(message.get("cols", 80))
                    rows = int(message.get("rows", 24))
                    logger.debug("终端尺寸：sid=%s %d×%d", session_id, cols, rows)
                    session.resize(cols, rows)
            elif kind == "close":
                self.close_session(session_id)
            elif kind == "redraw":
                # 让 TUI 整屏重绘：备用屏应用不响应普通重绘请求，靠 SIGWINCH 触发
                nudge = getattr(session, "nudge_redraw", None)
                if callable(nudge):
                    nudge()
        except Exception as exc:  # noqa: BLE001 - 单条消息失败不影响会话
            logger.debug("处理终端消息失败：%s", exc)

    # ── 内部：输出管道 ────────────────────────────────

    def _on_output(self, session_id: str, data: bytes) -> None:
        """GUI 线程：写入回放缓冲 + 待发缓冲，并安排合并刷新"""
        pending = self._pending.setdefault(session_id, bytearray())
        pending.extend(data)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending(self) -> None:
        """GUI 线程：把各会话的待发缓冲合并成一条二进制帧"""
        for session_id in list(self._pending.keys()):
            self._flush_session(session_id)
        if any(self._pending.values()):
            self._flush_timer.start()

    def _flush_session(self, session_id: str) -> None:
        pending = self._pending.get(session_id)
        if not pending:
            return
        sock = self._sockets.get(session_id)
        if sock is not None:
            try:
                sock.sendBinaryMessage(QByteArray(bytes(pending)))
            except Exception as exc:  # noqa: BLE001
                logger.debug("发送终端输出失败：%s", exc)
        pending.clear()

    def _send_text(self, session_id: str, payload: dict[str, Any]) -> None:
        sock = self._sockets.get(session_id)
        if sock is None:
            return
        try:
            sock.sendTextMessage(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.debug("发送终端控制帧失败：%s", exc)

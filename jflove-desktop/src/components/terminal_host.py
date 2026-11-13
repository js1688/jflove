"""
终端 WebSocket 通道（v1.10.0）

桌面端是「Python 外壳 + QtWebEngine 渲染层」两层结构：渲染层是 JS，拿不到
SSH/PTY 句柄，必须由 Python 侧把输出字节喂给 xterm.js。本模块就是这条管道。

**只监听 127.0.0.1**，每个会话一个随机 token；与 SSH/SFTP 直连链路无关，
不经过服务端（`文档记录/设计文档/v1.10.0.md` §1.1 / §3.4）。
"""

from __future__ import annotations

import hmac
import json
import secrets
from typing import Any

from PySide6.QtCore import QByteArray, QObject, QUrlQuery, Signal
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from src.utils.logger import get_logger

logger = get_logger(__name__)


class TerminalHost(QObject):
    """
    终端会话的本地 WebSocket 服务。

    - 下行：二进制帧（UTF-8 原始字节）
    - 上行：文本帧 JSON（input / resize / close）

    读线程通过 `feed()` 投递数据，内部用 Qt Signal 切回 GUI 线程再发送。
    """

    #: (session_id, bytes) —— 跨线程投递输出
    _dataReady = Signal(str, bytes)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sessions: dict[str, Any] = {}
        self._sockets: dict[str, QWebSocket] = {}
        self._tokens: dict[str, str] = {}
        self._server: QWebSocketServer | None = None
        self._port = 0
        self._dataReady.connect(self._flush_output)
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
        return token

    def feed(self, session_id: str, data: bytes) -> None:
        """把输出投递给渲染层（可从任意线程调用）"""
        self._dataReady.emit(session_id, data)

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

    def shutdown(self) -> None:
        """关闭全部会话与监听（退出时调用）"""
        for sid in list(self._sessions.keys()):
            self.close_session(sid)
        if self._server is not None:
            self._server.close()
            self._server = None
        self._port = 0

    # ── 内部 ──────────────────────────────────────────

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

    def _on_disconnected(self, session_id: str) -> None:
        """通道断开：只清 socket，不关闭会话（允许前端重连）"""
        self._sockets.pop(session_id, None)
        logger.info("终端通道已断开：sid=%s", session_id)

    def _on_text(self, session_id: str, text: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        try:
            message = json.loads(text)
        except (TypeError, ValueError):
            return
        kind = message.get("type")
        try:
            if kind == "input":
                session.write(str(message.get("data", "")).encode("utf-8"))
            elif kind == "resize":
                session.resize(int(message.get("cols", 80)), int(message.get("rows", 24)))
            elif kind == "close":
                self.close_session(session_id)
        except Exception as exc:  # noqa: BLE001 - 单条消息失败不影响会话
            logger.debug("处理终端消息失败：%s", exc)

    def _flush_output(self, session_id: str, data: bytes) -> None:
        sock = self._sockets.get(session_id)
        if sock is None:
            return
        try:
            sock.sendBinaryMessage(QByteArray(data))
        except Exception as exc:  # noqa: BLE001
            logger.debug("发送终端输出失败：%s", exc)

"""
JS ↔ Python 桥（QWebChannel）—— 桌面端 UI 的唯一能力出口

设计约束（P0-2 已验证，见 `plans/desktop-webui-migration-v1.5.0.md`）：

1. **JS 不碰网络**：所有数据经此桥交给 `src/services/*` 与
   `src/utils/http_client.py`（宪法 §9.5.16）。renderer 没有网络出口。
2. **必须异步**：`services/` 是同步阻塞的 `requests` 调用，在 GUI 线程里执行会冻结界面。
   复用既有的 `src/utils/worker.py::Worker`（QThread），完成后经信号回推。
3. **白名单而非反射**：`WHITELIST` 显式登记；未登记一律拒绝。
4. **令牌不出 Python**：会话快照只含 `has_token` 这类布尔/文案字段。
5. **日志不记参数值**（宪法 §9.14）：只记 `service.method` 与参数**名**。

协议：

```
下行 JS → Python：  bridge.request(requestId, service, method, paramsJson)   void
上行 Python → JS：  responseReady(requestId, payloadJson)
                    eventPushed(topic, payloadJson)
```
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from src.bridge import api_gateway
from src.bridge import files as files_bridge
from src.bridge import media as media_bridge
from src.bridge import sync as sync_bridge
from src.bridge import transfer as transfer_bridge
from src.services import auth_service, server_history_service
from src.utils.logger import get_logger
from src.utils.session import session_manager
from src.utils.worker import Worker

logger = get_logger(__name__)


# ── 结果封装 ──────────────────────────────────────


def _ok(result: Any) -> str:
    """成功结果 → JSON 字符串"""
    return json.dumps({"ok": True, "result": result}, ensure_ascii=False, default=str)


def _err(message: Any) -> str:
    """失败结果 → JSON 字符串"""
    return json.dumps({"ok": False, "error": str(message)}, ensure_ascii=False)


def _session_view() -> dict:
    """
    会话快照 —— 给渲染层用的「可公开视图」。

    **刻意不含 token 与 session_key**：只回答"有没有"，不回答"是什么"（宪法 §9.3）。
    `expires_at` 直接给 Python 算好的实际失效时间
    （`min(JWT exp, key_exchange_time + 本地上限)`），渲染层不需要重算。

    `session_id` / `key_exchange_time` 供安全状态页展示 —— **session_id 不是密钥**，
    泄漏它也解不开密文（解密需要 session_key，只在 Python 侧）。
    """
    return {
        "logged_in": session_manager.is_logged_in(),
        "username": session_manager.username,
        "role": session_manager.role,
        "user_id": session_manager.user_id,
        "is_admin": session_manager.is_admin(),
        "server_url": session_manager.server_url,
        "session_ready": session_manager.is_session_ready(),
        "expires_at": session_manager.effective_expire_at(),
        "has_token": bool(session_manager.token),
        "session_id": session_manager.session_id,
        "key_exchange_time": session_manager.key_exchange_time,
    }


# ── 事件推送（Python → JS） ────────────────────────


class _EventSink:
    """
    把工作线程里产生的事件转交给 Bridge 的信号。

    工作线程不能直接操作 GUI 对象，但**发射信号是线程安全的**，Qt 会把投递
    排到接收者所在线程，因此这里只做一个薄转发。
    """

    def __init__(self) -> None:
        self._bridge: "Bridge | None" = None

    def attach(self, bridge: "Bridge") -> None:
        self._bridge = bridge

    def publish(self, topic: str, payload: Any) -> None:
        if self._bridge is not None:
            self._bridge.publish_event(topic, payload)


_EVENTS = _EventSink()


def publish_event(topic: str, payload: Any) -> None:
    """
    供各服务处理函数使用：向渲染层推事件。

    可从工作线程调用（信号发射线程安全，见 `_EventSink` 文档）。
    """
    _EVENTS.publish(topic, payload)


# ── 白名单处理函数 ────────────────────────────────


def _default_server_url() -> str:
    """服务端地址：会话里的优先，其次地址历史首项"""
    return session_manager.server_url or server_history_service.get_default()


def _h_meta_ping(_p: dict) -> dict:
    """连通性自检（不出网）"""
    import PySide6

    from PySide6.QtGui import QGuiApplication

    return {
        "platform": QGuiApplication.platformName(),
        "qt": PySide6.__version__,
        "python": sys.version.split()[0],
        "os": sys.platform,
        "bridge_version": "n4",
        "server_url": session_manager.server_url,
    }


def _h_server_history_list(_p: dict) -> list[str]:
    """服务端地址历史（纯本地文件，不出网）"""
    return server_history_service.list_history()


def _h_server_history_record(p: dict) -> list[str]:
    """记录服务端地址（去重 + 置顶 + 限 10 条）"""
    server_history_service.record(str(p.get("url", "")))
    return server_history_service.list_history()


def _h_server_history_delete(p: dict) -> list[str]:
    """删除某条地址历史"""
    server_history_service.delete(str(p.get("url", "")))
    return server_history_service.list_history()


def _h_auth_login(p: dict) -> dict:
    """
    登录（**必须走服务层**，不能经 `api.request` 透传）。

    为什么：会话的唯一真相是 `session_manager`。透传 `/api/v1/auth/login`
    只会拿到一个 HTTP 响应体，`session_manager.token` 不会被赋值，
    结果就是"HTTP 成功但桥回答未登录"（2026-09-20 实测踩到过）。
    `auth_service.login()` 会同时完成：写 session_manager + 持久化 session.json。
    """
    max_seconds = p.get("local_max_seconds")
    auth_service.login(
        str(p.get("username", "")),
        str(p.get("password", "")),
        int(max_seconds) if max_seconds else None,
    )
    _EVENTS.publish("auth.login", {"username": session_manager.username})
    return _session_view()


def _h_auth_init_admin(p: dict) -> dict:
    """初始化管理员账号（服务层负责写入 users 表）"""
    auth_service.init_admin(str(p.get("username", "")), str(p.get("password", "")))
    return {"ok": True}


def _h_auth_admin_exists(_p: dict) -> bool:
    """检查服务端是否已有管理员（明文白名单接口）"""
    return bool(auth_service.check_admin_exists())


def _h_auth_key_exchange(p: dict) -> dict:
    """ECDH 密钥交换（明文白名单接口）"""
    url = str(p.get("server_url") or "") or _default_server_url()
    if not url:
        raise ValueError("缺少服务端地址")
    auth_service.do_key_exchange(url)
    _EVENTS.publish("auth.key_exchange", {"server_url": session_manager.server_url})
    return _session_view()


def _h_auth_restore_session(_p: dict) -> dict:
    """
    启动时恢复会话（免登录）。

    服务端不可达等异常**不外抛**，而是放进 `restore_error` —— 否则应用启动
    就会失败，用户连登录页都看不到。
    """
    try:
        auth_service.try_restore_session()
    except Exception as exc:  # noqa: BLE001 - 恢复失败必须降级为"未登录"
        logger.info("会话恢复失败，按未登录处理: %s", exc)
        return {**_session_view(), "restore_error": str(exc)}
    return _session_view()


def _h_auth_ensure_session(_p: dict) -> dict:
    """确保加密会话可用（没有就建一个）"""
    if not session_manager.is_session_ready():
        url = _default_server_url()
        if url:
            auth_service.do_key_exchange(url)
    return _session_view()


def _h_auth_session(_p: dict) -> dict:
    """查询当前会话（不含令牌内容）"""
    return _session_view()


def _h_auth_refresh_key_exchange(_p: dict) -> dict:
    """
    刷新会话密钥（安全状态页的「刷新会话密钥」按钮）。

    由服务层执行：旧 session_key 立即失效并重新完成 ECDH。
    """
    auth_service.refresh_key_exchange()
    _EVENTS.publish("auth.key_exchange_refreshed", {})
    return _session_view()


def _h_auth_logout(_p: dict) -> dict:
    """登出：清空 Python 侧会话与持久化数据"""
    auth_service.logout()
    _EVENTS.publish("auth.logout", {})
    return _session_view()


def _h_demo_progress(p: dict) -> dict:
    """
    事件推送演示：在工作线程里持续发事件，最后返回汇总。

    用来证明「Python → JS 的主动推送」可用（后续的传输/同步进度就靠它）。
    """
    steps = max(1, min(int(p.get("steps", 5)), 50))
    for i in range(1, steps + 1):
        time.sleep(0.12)
        _EVENTS.publish("demo.progress", {"step": i, "total": steps})
    return {"steps": steps, "note": "事件已逐步推送完毕"}


#: 白名单：{service: {method: 处理函数}}
WHITELIST: dict[str, dict[str, Callable[[dict], Any]]] = {
    "api": {"request": api_gateway.handle_api_request},
    "meta": {
        "ping": _h_meta_ping,
        "server_history": _h_server_history_list,
        "server_history_record": _h_server_history_record,
        "server_history_delete": _h_server_history_delete,
    },
    "auth": {
        "login": _h_auth_login,
        "init_admin": _h_auth_init_admin,
        "admin_exists": _h_auth_admin_exists,
        "key_exchange": _h_auth_key_exchange,
        "restore_session": _h_auth_restore_session,
        "ensure_session": _h_auth_ensure_session,
        "session": _h_auth_session,
        "refresh_key_exchange": _h_auth_refresh_key_exchange,
        "logout": _h_auth_logout,
    },
    "demo": {"progress": _h_demo_progress},
    # 文件操作 + 原生对话框（N7-a）：上传/下载/加密全部在 Python 服务层完成
    **files_bridge.HANDLERS,
    # 媒体预览（N7-b）：StreamProxy + QMediaPlayer / Qt 原生解码 + 原生浮层
    **media_bridge.HANDLERS,
    # 同步管理（N9）：原桌面端的同步引擎 + 规则管理
    **sync_bridge.HANDLERS,
    # 传输任务（N10 尾巴）：原桌面端的 TransferManager（任务真相回到 Python）
    **transfer_bridge.HANDLERS,
}


def _invoke(handler: Callable[[dict], Any], params: dict) -> Any:
    """在工作线程里执行白名单处理函数"""
    return handler(params)


# ── 桥本体 ────────────────────────────────────────


class Bridge(QObject):
    """
    JS ↔ Python 桥。注册进 QWebChannel 的名字是 `bridge`。

    :signal responseReady: (requestId, payloadJson) —— 请求完成（成功或失败）
    :signal eventPushed:   (topic, payloadJson)     —— Python 主动推事件
    """

    responseReady = Signal(str, str)
    eventPushed = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._inflight: dict[str, Worker] = {}
        _EVENTS.attach(self)

    # ── 供处理函数（工作线程）回调 ─────────────────

    def publish_event(self, topic: str, payload: Any) -> None:
        """推事件给 JS（可从工作线程调用：信号发射是线程安全的）"""
        self.eventPushed.emit(
            str(topic), json.dumps(payload, ensure_ascii=False, default=str)
        )

    # ── JS 入口 ────────────────────────────────────

    @Slot(str, str, str, str)
    def request(
        self, request_id: str, service: str, method: str, params_json: str
    ) -> None:
        """
        执行一次白名单请求（void：结果经 `responseReady` 异步回推）。

        :param request_id: JS 侧生成的请求 id（用于把响应配回 Promise）
        :param service: 服务名（白名单一级键）
        :param method: 方法名（白名单二级键）
        :param params_json: 参数 JSON 字符串
        """
        rid = str(request_id)
        handler = WHITELIST.get(service, {}).get(method)

        if handler is None:
            logger.warning("桥拒绝未登记方法：%s.%s", service, method)
            self.responseReady.emit(rid, _err(f"未登记的方法：{service}.{method}"))
            return

        try:
            params = json.loads(params_json) if params_json else {}
            if not isinstance(params, dict):
                raise ValueError("params 必须是 JSON 对象")
        except (TypeError, ValueError) as exc:
            self.responseReady.emit(rid, _err(f"参数解析失败：{exc}"))
            return

        # 只记方法名与参数名：参数值可能含口令/令牌（宪法 §9.14）
        logger.debug("桥请求 %s.%s 参数键=%s", service, method, sorted(params.keys()))

        # 原生窗口 / 播放器 / 文件对话框**只能在 GUI 线程**创建，
        # 因此这类方法不走工作线程，直接在本槽内联执行
        # （模态对话框本来就会阻塞界面，语义正确；它们也都不做耗时网络 I/O）。
        if (service, method) in (
            media_bridge.MAIN_THREAD_METHODS
            | sync_bridge.MAIN_THREAD_METHODS
            | transfer_bridge.MAIN_THREAD_METHODS
        ):
            try:
                result = handler(params)
                self.responseReady.emit(rid, _ok(result))
            except Exception as exc:  # noqa: BLE001 - 统一转成错误响应
                logger.warning("桥（GUI 线程）%s.%s 失败：%s", service, method, exc)
                self.responseReady.emit(rid, _err(str(exc)))
            return

        worker = Worker(_invoke, handler, params)
        worker.finished.connect(
            lambda result, rid=rid: self._on_finished(rid, result),
            Qt.ConnectionType.QueuedConnection,
        )
        worker.error.connect(
            lambda message, rid=rid: self._on_error(rid, message),
            Qt.ConnectionType.QueuedConnection,
        )
        self._inflight[rid] = worker
        worker.start()

    # ── 内部 ──────────────────────────────────────

    def _on_finished(self, request_id: str, result: Any) -> None:
        """工作线程成功返回（本槽在主线程执行）"""
        self._inflight.pop(request_id, None)
        self.responseReady.emit(request_id, _ok(result))

    def _on_error(self, request_id: str, message: str) -> None:
        """工作线程抛异常（本槽在主线程执行）"""
        self._inflight.pop(request_id, None)
        logger.warning("桥响应 %s 失败：%s", request_id, message)
        self.responseReady.emit(request_id, _err(message))

"""
P0-2 PoC：JS ↔ Python 桥（QWebChannel）

本文件是 N4「桥的正式形态」的原型，设计约束如下。

## 1. 为什么不能让 JS 直接发网络请求

安全宪法 §9.5.16 要求桌面端所有 HTTP 只走 `src/utils/http_client.py`。
因此桥的定位是**唯一的能力出口**：JS 想拿数据只能 `bridge.request(...)`，
由 Python 去调用既有的 `services/` 层。这样：

  - 加密链路仍然只有一份实现（X25519 + HKDF + ChaCha20-Poly1305）
  - `session_key` / JWT **只存在于 Python 侧**，永不下发到 JS（也不进 DOM）
  - Chromium 里没有网络出口，CSP 可以收到最紧

## 2. 为什么必须异步

`services/` 里的函数是**同步阻塞**的网络调用（`requests`）。若在 GUI 线程里直接执行，
整个界面会冻结。所以：

  - JS 自己生成 requestId，调 `bridge.request(id, service, method, paramsJson)`（void，立即返回）
  - Python 用既有的 `src/utils/worker.py::Worker`（QThread）在工作线程里执行
  - 完成后经 `responseReady(requestId, payloadJson)` 信号回推 JS
  - 错误也走同一条通路，JS 侧统一 `{ok, result} | {ok:false, error}`

> 不复用 `Worker` 之外的东西：`worker.py` 属于「零改动」清单，直接拿来用。

## 3. 白名单而非反射

`WHITELIST` 是显式的 `{service: {method: 处理函数}}` 表。未登记一律拒绝 ——
bridge 绝不能变成「JS 可调用任意 Python 函数」的后门。处理函数只做
「取参数 → 调服务层 → 返回可 JSON 化结果」，不含业务逻辑。

## 4. 日志纪律（宪法 §9.14）

**绝不记录参数值**（登录参数含明文口令、token 同理）。只记录
`service.method` 与**参数名**。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any, Callable

# 让 `src.*` 可导入（本文件在 jflove-desktop/poc/bridge/ 下）
_DESKTOP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _DESKTOP_ROOT not in sys.path:
    sys.path.insert(0, _DESKTOP_ROOT)

from PySide6.QtCore import QObject, Qt, Signal, Slot  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from src.services import (  # noqa: E402
    auth_service,
    config_service,
    disk_service,
    file_service,
    note_service,
    server_history_service,
    user_service,
)
from src.utils.logger import get_logger  # noqa: E402
from src.utils.session import session_manager  # noqa: E402
from src.utils.worker import Worker  # noqa: E402

logger = get_logger(__name__)


# ── 结果封装 ──────────────────────────────────────


def _ok(result: Any) -> str:
    """成功结果 → JSON 字符串"""
    return json.dumps({"ok": True, "result": result}, ensure_ascii=False, default=str)


def _err(message: Any) -> str:
    """失败结果 → JSON 字符串"""
    return json.dumps({"ok": False, "error": str(message)}, ensure_ascii=False)


def _session_public() -> dict:
    """
    会话的「可公开视图」。

    **刻意不含 token 与 session_key** —— 只回答"有没有"，不回答"是什么"。
    这是宪法 §9.3/§9.14 在桥这一层的落点。
    """
    return {
        "logged_in": session_manager.is_logged_in(),
        "username": session_manager.username,
        "role": session_manager.role,
        "is_admin": session_manager.is_admin(),
        "server_url": session_manager.server_url,
        "session_ready": session_manager.is_session_ready(),
        "has_token": bool(session_manager.token),
    }


# ── 事件推送（Python → JS） ────────────────────────


class _EventSink:
    """
    把「工作线程里产生的事件」转交给 Bridge 的信号。

    为什么绕这一层：工作线程不能直接操作 GUI 对象，但**发射信号是线程安全的**，
    Qt 会把投递排到接收者所在线程。Demo 的进度事件就走这条路。
    """

    def __init__(self) -> None:
        self._bridge: "Bridge | None" = None

    def attach(self, bridge: "Bridge") -> None:
        self._bridge = bridge

    def publish(self, topic: str, payload: Any) -> None:
        if self._bridge is not None:
            self._bridge.publish_event(topic, payload)


_EVENTS = _EventSink()


# ── 白名单处理函数 ────────────────────────────────
# 约定：入参 dict，返回可 JSON 化的结果；异常由 Worker 捕获后回推 error。


def _h_meta_ping(_p: dict) -> dict:
    """连通性自检（不出网）"""
    import PySide6

    return {
        "platform": QGuiApplication.platformName(),
        "qt": PySide6.__version__,
        "python": sys.version.split()[0],
        "os": sys.platform,
        "bridge_version": "p0-2",
        "server_url": session_manager.server_url,
    }


def _h_meta_methods(_p: dict) -> list[dict]:
    """列出白名单里的全部方法（页面据此自渲染控制台）"""
    return [
        {"service": svc, "method": m, "summary": (fn.__doc__ or "").strip()}
        for svc, methods in sorted(WHITELIST.items())
        for m, fn in sorted(methods.items())
    ]


def _h_server_history_list(_p: dict) -> list[str]:
    """服务端地址历史（纯本地文件，不出网）"""
    return server_history_service.list_history()


def _h_auth_key_exchange(p: dict) -> dict:
    """ECDH 密钥交换（明文白名单接口）"""
    auth_service.do_key_exchange(str(p["server_url"]))
    _EVENTS.publish("auth.key_exchange", {"server_url": session_manager.server_url})
    return _session_public()


def _h_auth_login(p: dict) -> dict:
    """登录（加密接口；口令只在 Python 侧使用，不回传）"""
    auth_service.login(
        str(p["username"]),
        str(p["password"]),
        int(p["local_max_seconds"]) if p.get("local_max_seconds") else None,
    )
    _EVENTS.publish("auth.login", {"username": session_manager.username})
    return _session_public()


def _h_auth_logout(_p: dict) -> dict:
    """登出"""
    auth_service.logout()
    _EVENTS.publish("auth.logout", {})
    return _session_public()


def _h_auth_session(_p: dict) -> dict:
    """查询当前会话（不含令牌内容）"""
    return _session_public()


def _h_users_list(_p: dict) -> list[dict]:
    """用户列表（管理员，加密接口）"""
    return user_service.list_users()


def _h_disks_accessible(_p: dict) -> list[dict]:
    """当前用户可访问的磁盘（加密接口）"""
    return file_service.list_accessible_disks()


def _h_disks_all(_p: dict) -> list[dict]:
    """全部虚拟磁盘（管理员，加密接口）"""
    return disk_service.list_disks()


def _h_files_list(p: dict) -> list[dict]:
    """目录列表（加密接口）"""
    return file_service.list_files(int(p["disk_id"]), str(p.get("rel_path", "")))


def _h_notes_list(_p: dict) -> list[dict]:
    """笔记列表（加密接口）"""
    return note_service.list_notes()


def _h_config_all(_p: dict) -> list[dict]:
    """全部系统配置（加密接口）"""
    return config_service.get_all_config()


def _h_demo_progress(p: dict) -> dict:
    """
    事件推送演示：在工作线程里持续发事件，最后返回汇总。

    用来证明「Python → JS 的主动推送」这条路是通的（不是只靠请求-响应）。
    """
    steps = max(1, min(int(p.get("steps", 5)), 50))
    for i in range(1, steps + 1):
        time.sleep(0.12)
        _EVENTS.publish("demo.progress", {"step": i, "total": steps})
    return {"steps": steps, "note": "事件已逐步推送完毕"}


#: 临时账号凭据文件（由 _temp_account.py 生成，用完即删）
_TEMP_CRED_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".tmp_account.json"
)


def _h_demo_login_temp_account(p: dict) -> dict:
    """
    **PoC 专用**：用 `_temp_account.py` 建的临时账号一键登录。

    为什么要有它：临时账号的口令是随机生成的、只写在 Python 侧的凭据文件里，
    交互页面无从填写。本方法在 **Python 侧**读文件并登录，
    **口令全程不下发到 JS**。

    > N4 正式形态**不应保留**此类方法 —— 它属于 PoC 脚手架，
    > 正式实现里登录口令必须由用户在页面输入，经桥传给 Python 后立即使用、不留存。
    """
    if not os.path.exists(_TEMP_CRED_FILE):
        raise RuntimeError(
            "找不到临时账号凭据文件。请先用 server venv 执行：\n"
            "  jflove-server\\venv-win\\Scripts\\python.exe "
            "jflove-desktop\\poc\\bridge\\_temp_account.py create"
        )
    with open(_TEMP_CRED_FILE, encoding="utf-8") as fp:
        cred = json.load(fp)

    server_url = str(p.get("server_url") or session_manager.server_url or "")
    if not session_manager.is_session_ready():
        auth_service.do_key_exchange(server_url)
    auth_service.login(str(cred["username"]), str(cred["password"]))
    _EVENTS.publish("auth.login", {"username": session_manager.username, "source": "temp"})
    return _session_public()


#: 白名单：{service: {method: 处理函数}}
WHITELIST: dict[str, dict[str, Callable[[dict], Any]]] = {
    "meta": {
        "ping": _h_meta_ping,
        "methods": _h_meta_methods,
        "server_history": _h_server_history_list,
    },
    "auth": {
        "key_exchange": _h_auth_key_exchange,
        "login": _h_auth_login,
        "logout": _h_auth_logout,
        "session": _h_auth_session,
    },
    "users": {"list": _h_users_list},
    "disks": {"accessible": _h_disks_accessible, "all": _h_disks_all},
    "files": {"list": _h_files_list},
    "notes": {"list": _h_notes_list},
    "config": {"all": _h_config_all},
    "demo": {
        "progress": _h_demo_progress,
        "login_temp_account": _h_demo_login_temp_account,
    },
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
        # 持有运行中的 Worker 引用：Worker 内部已有活跃集合，这里再留一份便于排查
        self._inflight: dict[str, Worker] = {}
        _EVENTS.attach(self)

    # ── 供处理函数（工作线程）回调 ─────────────────

    def publish_event(self, topic: str, payload: Any) -> None:
        """推事件给 JS（可从工作线程调用：信号发射是线程安全的）"""
        self.eventPushed.emit(str(topic), json.dumps(payload, ensure_ascii=False, default=str))

    # ── JS 入口 ────────────────────────────────────

    @Slot(str, str, str, str)
    def request(self, request_id: str, service: str, method: str, params_json: str) -> None:
        """
        执行一次白名单请求（void：结果经 responseReady 异步回推）。

        :param request_id: JS 侧生成的请求 id（用于把响应配回 Promise）
        :param service: 服务名（白名单一级键）
        :param method: 方法名（白名单二级键）
        :param params_json: 参数 JSON 字符串
        """
        rid = str(request_id)
        handler = WHITELIST.get(service, {}).get(method)

        if handler is None:
            logger.warning("桥拒绝未登记方法: %s.%s", service, method)
            self.responseReady.emit(rid, _err(f"未登记的方法：{service}.{method}"))
            return

        try:
            params = json.loads(params_json) if params_json else {}
            if not isinstance(params, dict):
                raise ValueError("params 必须是 JSON 对象")
        except (TypeError, ValueError) as exc:
            self.responseReady.emit(rid, _err(f"参数解析失败：{exc}"))
            return

        # 日志只记方法名与参数名，绝不记参数值（可能含口令/令牌）
        logger.info("桥请求 %s.%s 参数键=%s", service, method, sorted(params.keys()))

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
        logger.info("桥响应 %s ok", request_id)
        self.responseReady.emit(request_id, _ok(result))

    def _on_error(self, request_id: str, message: str) -> None:
        """工作线程抛异常（本槽在主线程执行）"""
        self._inflight.pop(request_id, None)
        logger.warning("桥响应 %s 失败: %s", request_id, message)
        self.responseReady.emit(request_id, _err(message))

    @Slot(result=str)
    def describe(self) -> str:
        """同步返回白名单摘要（便于 JS 初始化时一次性拿到，无需异步往返）"""
        return _ok(_h_meta_methods({}))


def new_request_id() -> str:
    """Python 侧生成请求 id（仅自检脚本用；JS 侧自己生成）"""
    return uuid.uuid4().hex[:12]

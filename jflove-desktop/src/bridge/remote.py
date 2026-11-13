"""
远程连接桥（v1.10.0）

- 配置 CRUD：转发到 `remote_service`（Python 侧直接调服务端，凭据不回渲染层）。
- 会话：SSH / 本机终端都由 Python 侧建立，输出经 `TerminalHost` 的 WebSocket
  喂给 xterm.js；状态通过 `publish_event` 逐跳上报。
- 私钥/密码原文**只在本模块与 `SshSession` 之间流转**，不进入渲染层。
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

from src.components.terminal_host import TerminalHost
from src.services import local_terminal_service, remote_service
from src.services import ssh_session as ssh_session_module
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 终端通道实例（必须在 GUI 线程创建）
_host: TerminalHost | None = None

#: 会话表：session_id → SshSession / LocalSession
_sessions: dict[str, Any] = {}

#: 每个 SSH 会话复用一个 SFTP client；取消传输时直接关闭它即可中断
_sftp_clients: dict[str, Any] = {}

#: 主机指纹确认等待
_hostkey_events: dict[str, threading.Event] = {}
_hostkey_answers: dict[str, bool] = {}

#: OTP 输入等待
_otp_events: dict[str, threading.Event] = {}
_otp_answers: dict[str, str] = {}

#: 主机指纹确认超时（秒）
HOSTKEY_TIMEOUT = 30

#: OTP 输入超时（秒）
OTP_TIMEOUT = 60


def init_host() -> None:
    """在 GUI 线程创建终端 WebSocket 通道（由 `main.py` 调用）"""
    global _host
    if _host is None:
        _host = TerminalHost()


def shutdown() -> None:
    """退出时关闭全部会话与通道"""
    if _host is not None:
        _host.shutdown()
    for session in list(_sessions.values()):
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass
    _sessions.clear()


def _publish(topic: str, payload: dict) -> None:
    """向渲染层推事件（惰性导入避免循环依赖）"""
    from src.bridge import bridge as bridge_module

    bridge_module.publish_event(topic, payload)


def _require_host() -> TerminalHost:
    if _host is None:
        raise RuntimeError("终端通道尚未初始化")
    return _host


# ── 配置 CRUD ────────────────────────────────────────


def _h_connections_list(params: dict) -> list[dict]:
    return remote_service.list_connections(str(params.get("keyword") or ""))


def _h_connections_get(params: dict) -> dict:
    """
    读取连接详情。

    **凭据原文不回渲染层**：只返回 `has_*` 与私钥指纹，编辑表单靠「留空 = 不改」。
    """
    conn = remote_service.get_connection(int(params["id"]))
    return {
        "connection": {
            key: value
            for key, value in conn.items()
            if key not in ("password", "private_key", "passphrase")
        }
    }


def _h_connections_create(params: dict) -> dict:
    payload = dict(params)
    payload.pop("token", None)
    return {"id": remote_service.create_connection(payload)}


def _h_connections_update(params: dict) -> dict:
    payload = dict(params)
    connection_id = int(payload.pop("id"))
    payload.pop("token", None)
    remote_service.update_connection(connection_id, payload)
    return {"ok": True}


def _h_connections_delete(params: dict) -> dict:
    remote_service.delete_connection(int(params["id"]))
    return {"ok": True}


def _h_connections_duplicate(params: dict) -> dict:
    return {"id": remote_service.duplicate_connection(int(params["id"]))}


def _h_groups_list(params: dict) -> list[dict]:
    return remote_service.list_groups()


def _h_groups_create(params: dict) -> dict:
    return {
        "id": remote_service.create_group(
            str(params.get("name") or ""), int(params.get("sort_order") or 0)
        )
    }


def _h_groups_update(params: dict) -> dict:
    remote_service.update_group(
        int(params["id"]),
        params.get("name"),
        int(params["sort_order"]) if params.get("sort_order") is not None else None,
    )
    return {"ok": True}


def _h_groups_delete(params: dict) -> dict:
    remote_service.delete_group(int(params["id"]))
    return {"ok": True}


# ── SSH 会话 ─────────────────────────────────────────


def _ask_hostkey(session_id: str) -> Callable[[dict, str], bool]:
    """构造主机指纹确认回调（阻塞等待渲染层回答）"""

    def ask(row: dict, fingerprint: str) -> bool:
        event = threading.Event()
        _hostkey_events[session_id] = event
        _publish(
            "remote.hostkey_ask",
            {
                "session_id": session_id,
                "connection_id": int(row.get("id") or 0),
                "host": row.get("host", ""),
                "fingerprint": fingerprint,
            },
        )
        answered = event.wait(HOSTKEY_TIMEOUT)
        accepted = answered and _hostkey_answers.pop(session_id, False)
        _hostkey_events.pop(session_id, None)
        return bool(accepted)

    return ask


def _ask_otp(session_id: str) -> Callable[[str], str]:
    """构造 OTP 输入回调（阻塞等待渲染层回答）"""

    def ask(prompt: str) -> str:
        event = threading.Event()
        _otp_events[session_id] = event
        _publish("remote.otp_ask", {"session_id": session_id, "prompt": prompt})
        answered = event.wait(OTP_TIMEOUT)
        _otp_events.pop(session_id, None)
        return _otp_answers.pop(session_id, "") if answered else ""

    return ask


def _run_session(session_id: str, session: Any) -> None:
    """后台建立会话，结果通过事件上报"""
    try:
        session.open()
        _publish("remote.session_ready", {"session_id": session_id})
        connection_id = int(getattr(session, "connection", {}).get("id") or 0)
        if connection_id:
            remote_service.touch_connection(connection_id)
    except ssh_session_module.HostKeyError as exc:
        _publish(
            "remote.session_failed",
            {"session_id": session_id, "phase": "hostkey", "message": str(exc)},
        )
    except ssh_session_module.AuthError as exc:
        _publish(
            "remote.session_failed",
            {"session_id": session_id, "phase": "auth", "message": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001 - 统一转成事件
        _publish(
            "remote.session_failed",
            {"session_id": session_id, "phase": "connect", "message": str(exc)},
        )


def _h_session_open(params: dict) -> dict:
    """打开 SSH 会话：立即返回通道信息，建链在后台进行"""
    host = _require_host()
    connection_id = int(params["connection_id"])
    connection = remote_service.get_connection(connection_id)
    session_id = uuid.uuid4().hex
    cols = int(params.get("cols") or 80)
    rows = int(params.get("rows") or 24)

    session = ssh_session_module.SshSession(
        connection,
        cols=cols,
        rows=rows,
        on_output=lambda data: host.feed(session_id, data),
        on_status=lambda phase, index, message: _publish(
            "remote.session_status",
            {
                "session_id": session_id,
                "phase": phase,
                "jump_index": index,
                "message": message,
            },
        ),
        hostkey_ask=_ask_hostkey(session_id),
        otp_ask=_ask_otp(session_id),
    )
    token = host.register(session_id, session)
    _sessions[session_id] = session
    threading.Thread(
        target=_run_session, args=(session_id, session), name="ssh-open", daemon=True
    ).start()
    return {"session_id": session_id, "port": host.port, "token": token}


def _h_session_close(params: dict) -> dict:
    session_id = str(params.get("session_id") or "")
    client = _sftp_clients.pop(session_id, None)
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    session = _sessions.pop(session_id, None)
    if session is not None:
        session.close()
    if _host is not None:
        _host.close_session(session_id)
    return {"ok": True}


def _h_hostkey_confirm(params: dict) -> dict:
    """渲染层回答主机指纹确认"""
    session_id = str(params.get("session_id") or "")
    _hostkey_answers[session_id] = bool(params.get("accept"))
    event = _hostkey_events.get(session_id)
    if event is not None:
        event.set()
    return {"ok": True}


def _h_otp_confirm(params: dict) -> dict:
    """渲染层回答 OTP 输入"""
    session_id = str(params.get("session_id") or "")
    _otp_answers[session_id] = str(params.get("code") or "")
    event = _otp_events.get(session_id)
    if event is not None:
        event.set()
    return {"ok": True}


# ── SFTP ─────────────────────────────────────────────


def _friendly_sftp_error(exc: Exception) -> str:
    """把 paramiko 的英文异常映射成可读中文（用户看到的第一现场）"""
    text = str(exc)
    mapping = {
        "garbage packet received": "远端连接被中断（网络或 SSH 服务异常）",
        "authentication failed": "认证失败，请检查账号或密钥",
        "no such file": "文件或目录不存在",
        "permission denied": "权限不足",
        "connection reset": "连接被远端重置",
        "socket is closed": "连接已关闭，请重新打开会话",
        "timed out": "操作超时",
    }
    lowered = text.lower()
    for key, value in mapping.items():
        if key in lowered:
            return value
    return f"远端操作失败：{text}"


def _sftp(session_id: str):
    """取（或建立）该会话的 SFTP client；断开时由 `_h_session_close` 统一关闭"""
    client = _sftp_clients.get(session_id)
    if client is not None:
        return client
    session = _sessions.get(session_id)
    if session is None or not hasattr(session, "open_sftp"):
        raise RuntimeError("会话不存在或不是 SSH 会话")
    client = session.open_sftp()
    _sftp_clients[session_id] = client
    return client


def _h_sftp_list(params: dict) -> dict:
    """列出远端目录（只返回本层条目，不递归）"""
    sftp = _sftp(str(params.get("session_id") or ""))
    path = str(params.get("path") or ".")
    entries: list[dict] = []
    try:
        attrs = sftp.listdir_attr(path)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(_friendly_sftp_error(exc)) from exc
    for attr in attrs:
        entries.append(
            {
                "name": attr.filename,
                "size": int(attr.st_size or 0),
                "mode": int(attr.st_mode or 0),
                "is_dir": attr.st_mode is not None and (attr.st_mode & 0o170000) == 0o040000,
                "mtime": int(attr.st_mtime or 0),
            }
        )
    entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    return {"entries": entries}


def _h_sftp_mkdir(params: dict) -> dict:
    _sftp(str(params["session_id"])).mkdir(str(params["path"]))
    return {"ok": True}


def _h_sftp_rename(params: dict) -> dict:
    _sftp(str(params["session_id"])).rename(str(params["old"]), str(params["new"]))
    return {"ok": True}


def _h_sftp_delete(params: dict) -> dict:
    sftp = _sftp(str(params["session_id"]))
    path = str(params["path"])
    if params.get("is_dir"):
        sftp.rmdir(path)
    else:
        sftp.remove(path)
    return {"ok": True}


def _h_sftp_download(params: dict) -> dict:
    """后台下载（进度经 `remote.sftp_progress` 推送）"""
    session_id = str(params["session_id"])
    sftp = _sftp(session_id)
    remote_path = str(params["remote_path"])
    local_path = str(params["local_path"])
    transfer_id = uuid.uuid4().hex
    last = {"value": 0}

    def progress(done: int, total: int) -> None:
        if done - last["value"] < 256 * 1024 and done != total:
            return
        last["value"] = done
        _publish(
            "remote.sftp_progress",
            {
                "transfer_id": transfer_id,
                "bytes_done": int(done),
                "bytes_total": int(total),
                "direction": "download",
            },
        )

    def run() -> None:
        try:
            sftp.get(remote_path, local_path, callback=progress)
            _publish(
                "remote.sftp_done",
                {"transfer_id": transfer_id, "local_path": local_path},
            )
        except Exception as exc:  # noqa: BLE001
            _publish(
                "remote.sftp_failed",
                {"transfer_id": transfer_id, "message": _friendly_sftp_error(exc)},
            )

    threading.Thread(target=run, name="sftp-download", daemon=True).start()
    return {"transfer_id": transfer_id}


def _h_sftp_upload(params: dict) -> dict:
    """后台上传（进度经 `remote.sftp_progress` 推送）"""
    session_id = str(params["session_id"])
    sftp = _sftp(session_id)
    local_path = str(params["local_path"])
    remote_path = str(params["remote_path"])
    transfer_id = uuid.uuid4().hex
    last = {"value": 0}

    def progress(done: int, total: int) -> None:
        if done - last["value"] < 256 * 1024 and done != total:
            return
        last["value"] = done
        _publish(
            "remote.sftp_progress",
            {
                "transfer_id": transfer_id,
                "bytes_done": int(done),
                "bytes_total": int(total),
                "direction": "upload",
            },
        )

    def run() -> None:
        try:
            sftp.put(local_path, remote_path, callback=progress)
            _publish(
                "remote.sftp_done",
                {"transfer_id": transfer_id, "remote_path": remote_path},
            )
        except Exception as exc:  # noqa: BLE001
            _publish(
                "remote.sftp_failed",
                {"transfer_id": transfer_id, "message": _friendly_sftp_error(exc)},
            )

    threading.Thread(target=run, name="sftp-upload", daemon=True).start()
    return {"transfer_id": transfer_id}


def _h_sftp_cancel(params: dict) -> dict:
    """取消传输：关闭该会话的 SFTP client（会中断正在进行的 get/put）"""
    session_id = str(params.get("session_id") or "")
    client = _sftp_clients.pop(session_id, None)
    if client is None:
        return {"ok": False}
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


# ── 本机终端 ─────────────────────────────────────────


def _h_local_detect_shells(params: dict) -> dict:
    return {"shells": local_terminal_service.detect_shells()}


def _h_local_groups_list(params: dict) -> dict:
    return {"groups": local_terminal_service.list_groups()}


def _h_local_groups_create(params: dict) -> dict:
    return {
        "id": local_terminal_service.create_group(
            str(params.get("name") or ""), int(params.get("sort_order") or 0)
        )
    }


def _h_local_groups_update(params: dict) -> dict:
    local_terminal_service.update_group(
        int(params["id"]),
        params.get("name"),
        int(params["sort_order"]) if params.get("sort_order") is not None else None,
    )
    return {"ok": True}


def _h_local_groups_delete(params: dict) -> dict:
    local_terminal_service.delete_group(int(params["id"]))
    return {"ok": True}


def _h_local_profiles_list(params: dict) -> dict:
    return {"profiles": local_terminal_service.list_profiles()}


def _h_local_profiles_get(params: dict) -> dict:
    return {"profile": local_terminal_service.get_profile(int(params["id"]))}


def _h_local_profiles_create(params: dict) -> dict:
    payload = dict(params)
    return {"id": local_terminal_service.create_profile(payload)}


def _h_local_profiles_update(params: dict) -> dict:
    profile_id = int(params.pop("id"))
    local_terminal_service.update_profile(profile_id, params)
    return {"ok": True}


def _h_local_profiles_delete(params: dict) -> dict:
    local_terminal_service.delete_profile(int(params["id"]))
    return {"ok": True}


def _h_local_open(params: dict) -> dict:
    """打开本机终端：立即返回通道信息，进程在后台启动"""
    host = _require_host()
    profile = None
    profile_id = int(params.get("profile_id") or 0)
    if profile_id:
        profile = local_terminal_service.get_profile(profile_id)
    session_id = uuid.uuid4().hex
    session = local_terminal_service.LocalSession(
        profile,
        cols=int(params.get("cols") or 80),
        rows=int(params.get("rows") or 24),
        on_output=lambda data: host.feed(session_id, data),
        on_status=lambda phase, index, message: _publish(
            "local_term.session_status",
            {"session_id": session_id, "phase": phase, "message": message},
        ),
    )
    token = host.register(session_id, session)
    _sessions[session_id] = session

    def run() -> None:
        try:
            session.open()
            _publish("local_term.session_ready", {"session_id": session_id})
        except Exception as exc:  # noqa: BLE001
            _publish(
                "local_term.session_failed",
                {"session_id": session_id, "message": str(exc)},
            )

    threading.Thread(target=run, name="local-term-open", daemon=True).start()
    return {"session_id": session_id, "port": host.port, "token": token}


HANDLERS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "remote": {
        "connections_list": _h_connections_list,
        "connections_get": _h_connections_get,
        "connections_create": _h_connections_create,
        "connections_update": _h_connections_update,
        "connections_delete": _h_connections_delete,
        "connections_duplicate": _h_connections_duplicate,
        "groups_list": _h_groups_list,
        "groups_create": _h_groups_create,
        "groups_update": _h_groups_update,
        "groups_delete": _h_groups_delete,
        "session_open": _h_session_open,
        "session_close": _h_session_close,
        "hostkey_confirm": _h_hostkey_confirm,
        "otp_confirm": _h_otp_confirm,
        "sftp_list": _h_sftp_list,
        "sftp_mkdir": _h_sftp_mkdir,
        "sftp_rename": _h_sftp_rename,
        "sftp_delete": _h_sftp_delete,
        "sftp_download": _h_sftp_download,
        "sftp_upload": _h_sftp_upload,
        "sftp_cancel": _h_sftp_cancel,
    },
    "local_term": {
        "detect_shells": _h_local_detect_shells,
        "groups_list": _h_local_groups_list,
        "groups_create": _h_local_groups_create,
        "groups_update": _h_local_groups_update,
        "groups_delete": _h_local_groups_delete,
        "profiles_list": _h_local_profiles_list,
        "profiles_get": _h_local_profiles_get,
        "profiles_create": _h_local_profiles_create,
        "profiles_update": _h_local_profiles_update,
        "profiles_delete": _h_local_profiles_delete,
        "open": _h_local_open,
    },
}

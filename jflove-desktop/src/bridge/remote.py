"""
远程连接桥（v1.10.0）

- 配置 CRUD：转发到 `remote_service`（Python 侧直接调服务端，凭据不回渲染层）。
- 会话：SSH / 本机终端都由 Python 侧建立，输出经 `TerminalHost` 的 WebSocket
  喂给 xterm.js；状态通过 `publish_event` 逐跳上报。
- 私钥/密码原文**只在本模块与 `SshSession` 之间流转**，不进入渲染层。
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
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

#: 每个会话的 SFTP 元操作锁（重入）。
#:
#: 桥的每个请求都在独立 Worker 线程执行，而 paramiko 的 SFTPClient **不支持**
#: 多个同步请求并发（响应可能被另一个线程读走）。连接期间 `SftpView` 每秒探活 +
#: 就绪后首次加载可能同时发生，必须把 listdir/mkdir/rename/delete 串行化。
_sftp_locks: dict[str, threading.RLock] = {}

#: 传输专用 SFTP client（每笔上传/下载一条独立 subsystem 通道，不与会话级 client 共用）。
#: 取消/断开时统一关闭，避免「传大文件时目录刷不出来」。
_sftp_transfer_clients: dict[str, set[Any]] = {}

#: 正在使用**会话级 client** 的共享传输计数。
#:
#: 受限服务器会让传输回退到会话级 client；此时「取消目录加载」（`sftp_abort`）**不能**
#: 关闭该 client，否则会误伤正在进行的下载/上传（v1.10.13 的语义必须保住）。
_sftp_shared_transfers: dict[str, int] = {}

#: 每个会话的「取消传输」代次。
#:
#: 通道建立已移到后台线程：用户可能在「开通道」或「排队等锁」阶段点取消。
#: 传输线程在关键节点比较代次，变化即中止，避免取消后仍继续下载/上传。
_sftp_cancel_generations: dict[str, int] = {}

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

#: 选文件后暂存的私钥内容：token → {content, name}。
#: 私钥字节只在 Python 进程内流转，渲染层只拿到 token / 文件名。
_pending_private_keys: dict[str, dict[str, str]] = {}

#: 暂存上限（防止反复选择文件后内存无界增长）
MAX_PENDING_PRIVATE_KEYS = 16


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


def _resolve_private_key_payload(payload: dict) -> tuple[str, str]:
    """
    取出本次提交的私钥内容。

    :returns: `(私钥文本, token)`；用了「选择文件」时 token 非空
    :raises RuntimeError: token 已失效（应用重启 / 已保存过）
    """
    token = str(payload.pop("private_key_file_token", "") or "")
    if not token:
        content = str(payload.get("private_key") or "")
        # 连接配置只存私钥内容，不存本机路径（用户要求：全放服务端数据库）
        if content and not any(
            marker in content for marker in ("-----BEGIN", "PuTTY-User-Key-File")
        ):
            raise RuntimeError(
                "私钥字段需要粘贴私钥文本，或用「选择文件…」选择文件"
                "（连接配置不保存本地路径）"
            )
        return content, ""
    entry = _pending_private_keys.get(token)
    if entry is None:
        raise RuntimeError("私钥文件内容已失效，请重新选择文件")
    return entry["content"], token


def _h_connections_create(params: dict) -> dict:
    payload = dict(params)
    payload.pop("token", None)
    content, key_token = _resolve_private_key_payload(payload)
    if key_token:
        # 选文件 = 把文件内容存进服务端数据库，之后连接不再依赖本地文件
        payload["private_key"] = content
    created = remote_service.create_connection(payload)
    if key_token:
        _pending_private_keys.pop(key_token, None)
    return {"id": created}


def _h_connections_update(params: dict) -> dict:
    payload = dict(params)
    connection_id = int(payload.pop("id"))
    payload.pop("token", None)
    content, key_token = _resolve_private_key_payload(payload)
    if key_token:
        payload["private_key"] = content
    remote_service.update_connection(connection_id, payload)
    if key_token:
        _pending_private_keys.pop(key_token, None)
    return {"ok": True}


#: 私钥文件选择过滤器。
#:
#: **必须包含 `*.ppk`**：过滤器里没有的扩展名，原生对话框会显示成灰色不可选，
#: 用户点了没有任何反应 —— 这正是「选 ppk 毫无反应」的第一层原因（v1.10.12 修复）。
#: 选中 `.ppk` 后仍按既有口径拒绝并给出转换指引（见 `_h_pick_private_key`）。
PRIVATE_KEY_FILTER = (
    "私钥文件 (*.pem *.key *.ppk *.rsa id_rsa* id_ed25519* id_ecdsa*);;所有文件 (*)"
)


def _h_pick_private_key(_params: dict) -> dict:
    """
    选择私钥文件：Python 侧读取内容、预校验并暂存，渲染层只拿 token。

    **私钥字节不回渲染层**（安全宪法 §9.6）；保存连接时由桥把内容发给服务端。
    """
    from PySide6.QtWidgets import QFileDialog

    from src.bridge import files as files_bridge

    path, _filter = QFileDialog.getOpenFileName(
        files_bridge._main_window,
        "选择私钥文件",
        "",
        PRIVATE_KEY_FILTER,
    )
    if not path:
        return {"cancelled": True}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"读取私钥文件失败：{Path(path).name}（{exc}）") from exc
    if "PuTTY-User-Key-File" in text:
        raise RuntimeError(
            "这是 PuTTY 私钥（.ppk），当前版本不直接支持。"
            "请用 PuTTYgen 打开它并执行 Conversions → Export OpenSSH key 导出成 .pem 后再选择；"
            "或从发行方重新获取 OpenSSH 格式的私钥"
        )
    encrypted = False
    fingerprint = ""
    try:
        key = ssh_session_module.load_private_key(text, "")
        fingerprint = ssh_session_module.fetch_fingerprint(key)
    except ssh_session_module.AuthError as exc:
        if "已加密" in str(exc):
            encrypted = True
        else:
            raise RuntimeError(str(exc)) from exc

    token = uuid.uuid4().hex
    _pending_private_keys[token] = {"content": text, "name": Path(path).name}
    while len(_pending_private_keys) > MAX_PENDING_PRIVATE_KEYS:
        _pending_private_keys.pop(next(iter(_pending_private_keys)), None)
    return {
        "token": token,
        "name": Path(path).name,
        "size": len(text.encode("utf-8")),
        "encrypted": encrypted,
        "fingerprint": fingerprint,
    }


def _h_validate_private_key(params: dict) -> dict:
    """保存前校验私钥（文本或选中的文件），失败直接给可读原因"""
    content, _token = _resolve_private_key_payload(dict(params))
    if not content:
        raise RuntimeError("私钥内容为空：请粘贴私钥文本，或选择私钥文件")
    key = ssh_session_module.load_private_key(
        content, str(params.get("passphrase") or "")
    )
    return {
        "ok": True,
        "key_type": type(key).__name__,
        "fingerprint": ssh_session_module.fetch_fingerprint(key),
    }


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


def _ssh_status(host: TerminalHost, session_id: str):
    """SSH 会话状态：桥事件（链路逐跳进度） + 终端通道（退出/断开）双通道上报"""

    def report(phase: str, index: int, message: str) -> None:
        _publish(
            "remote.session_status",
            {
                "session_id": session_id,
                "phase": phase,
                "jump_index": index,
                "message": message,
            },
        )
        host.notify_status(session_id, phase, message)

    return report


def _local_status(host: TerminalHost, session_id: str):
    """本机会话状态：同上（本机没有跳板，index 不使用）"""

    def report(phase: str, index: int, message: str) -> None:
        _publish(
            "local_term.session_status",
            {"session_id": session_id, "phase": phase, "message": message},
        )
        host.notify_status(session_id, phase, message)

    return report


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
        on_status=_ssh_status(host, session_id),
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
    _sftp_locks.pop(session_id, None)
    _sftp_shared_transfers.pop(session_id, None)
    _sftp_cancel_generations.pop(session_id, None)
    _close_transfer_clients(session_id)
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
        "timed out": "远端 SFTP 响应超时（服务器长时间无响应，请重试或检查网络）",
        "timeout opening channel": "远端未响应（打开 SFTP 通道超时）",
        "server connection dropped": "远端 SFTP 连接已断开，请刷新重试",
        # 受限服务器（MaxSessions / 堡垒机）拒绝第二条 subsystem 通道
        "connect failed": "服务器拒绝打开新的 SFTP 通道（可能限制了并发会话数）",
        "channel open failed": "服务器拒绝打开新的 SFTP 通道（可能限制了并发会话数）",
    }
    lowered = text.lower()
    for key, value in mapping.items():
        if key in lowered:
            return value
    return f"远端操作失败：{text}"


def _sftp_lock(session_id: str) -> threading.RLock:
    """取该会话的 SFTP 元操作锁（重入；首次使用时创建）"""
    lock = _sftp_locks.get(session_id)
    if lock is None:
        lock = threading.RLock()
        _sftp_locks[session_id] = lock
    return lock


def _transfer_cancelled(session_id: str, generation: int) -> bool:
    """该传输是否已被 `sftp_cancel` 取消（代次变化）"""
    return _sftp_cancel_generations.get(session_id, 0) != generation


class _TransferCancelled(Exception):
    """内部信号：传输在开通道 / 等锁阶段被用户取消"""


@contextmanager
def _sftp_guard(session_id: str):
    """
    元操作（列目录 / 新建 / 改名 / 删除）的 SFTP 锁。

    受限服务器上传输会长时间持有该锁；这里用**有上限的等待**，避免 UI
    「刷新」永远卡在锁上（45s 后给出可读原因，可稍后重试）。
    """
    lock = _sftp_lock(session_id)
    if not lock.acquire(timeout=float(ssh_session_module.SFTP_OP_TIMEOUT)):
        raise RuntimeError("有文件正在传输，暂不能加载或修改目录；请等传输结束后重试")
    try:
        yield
    finally:
        lock.release()


def _close_transfer_clients(session_id: str) -> None:
    """关闭该会话的全部传输专用 SFTP client"""
    clients = _sftp_transfer_clients.pop(session_id, set())
    session = _sessions.get(session_id)
    for client in list(clients):
        if session is not None and hasattr(session, "release_sftp"):
            try:
                session.release_sftp(client)
            except Exception:  # noqa: BLE001
                pass
        try:
            client.close()
        except Exception:  # noqa: BLE001 - 关闭阶段忽略异常
            pass


def _drop_sftp(session_id: str) -> None:
    """
    丢弃该会话缓存的 SFTP client（会话侧缓存一起清）。

    超时/协议错后 channel 可能已不可用，必须重建；否则后续「刷新」会继续拿到坏
    client，表现为「点刷新也没反应」。
    """
    client = _sftp_clients.pop(session_id, None)
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001 - 丢弃阶段忽略异常
            pass
    session = _sessions.get(session_id)
    if session is not None and hasattr(session, "drop_sftp"):
        try:
            session.drop_sftp()
        except Exception:  # noqa: BLE001
            pass


def _sftp(session_id: str):
    """取（或建立）该会话的 SFTP client；断开时由 `_h_session_close` 统一关闭"""
    with _sftp_lock(session_id):
        client = _sftp_clients.get(session_id)
        if client is not None:
            return client
        session = _sessions.get(session_id)
        if session is None or not hasattr(session, "open_sftp"):
            raise RuntimeError("会话不存在或不是 SSH 会话（可能已断开，请重新打开标签）")
        client = session.open_sftp()
        _sftp_clients[session_id] = client
        return client


def _h_sftp_list(params: dict) -> dict:
    """列出远端目录（只返回本层条目，不递归；带耗时日志，慢目录可定位）"""
    session_id = str(params.get("session_id") or "")
    path = str(params.get("path") or ".")
    entries: list[dict] = []
    with _sftp_guard(session_id):
        sftp = _sftp(session_id)
        started = time.monotonic()
        try:
            attrs = sftp.listdir_attr(path)
        except Exception as exc:  # noqa: BLE001
            _drop_sftp(session_id)
            logger.warning(
                "SFTP listdir 失败：%s（%.0f ms）",
                type(exc).__name__,
                (time.monotonic() - started) * 1000,
            )
            raise RuntimeError(_friendly_sftp_error(exc)) from exc
        elapsed_ms = (time.monotonic() - started) * 1000
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
    logger.info("SFTP listdir 完成：%d 项，%.0f ms", len(entries), elapsed_ms)
    return {"entries": entries}


def _h_sftp_mkdir(params: dict) -> dict:
    session_id = str(params["session_id"])
    with _sftp_guard(session_id):
        try:
            _sftp(session_id).mkdir(str(params["path"]))
        except Exception as exc:  # noqa: BLE001
            _drop_sftp(session_id)
            raise RuntimeError(_friendly_sftp_error(exc)) from exc
    return {"ok": True}


def _h_sftp_rename(params: dict) -> dict:
    session_id = str(params["session_id"])
    with _sftp_guard(session_id):
        try:
            _sftp(session_id).rename(str(params["old"]), str(params["new"]))
        except Exception as exc:  # noqa: BLE001
            _drop_sftp(session_id)
            raise RuntimeError(_friendly_sftp_error(exc)) from exc
    return {"ok": True}


def _h_sftp_delete(params: dict) -> dict:
    session_id = str(params["session_id"])
    path = str(params["path"])
    with _sftp_guard(session_id):
        sftp = _sftp(session_id)
        try:
            if params.get("is_dir"):
                sftp.rmdir(path)
            else:
                sftp.remove(path)
        except Exception as exc:  # noqa: BLE001
            _drop_sftp(session_id)
            raise RuntimeError(_friendly_sftp_error(exc)) from exc
    return {"ok": True}


def _open_transfer_sftp(
    session_id: str,
) -> tuple[Any, bool, threading.RLock | None]:
    """
    为一次传输取 SFTP client。

    优先开**独立 subsystem 通道**（传输不占目录浏览的 client）；但部分服务器 /
    堡垒机限制单连接并发会话（OpenSSH `MaxSessions 1` 即此行为），第二条通道会被
    拒绝（`ChannelException(2, 'Connect failed')`）。此时退回**复用会话级 client**，
    并由调用方在整笔传输期间持有 `_sftp_lock`（paramiko 的 client 不支持并发同步请求）。

    :returns: `(client, shared, lock)`；`shared=True` 时调用方必须释放传入的锁，
              且**不得关闭**该 client（它属于会话缓存）。
    """
    session = _sessions.get(session_id)
    if session is None or not hasattr(session, "open_sftp"):
        raise RuntimeError("会话不存在或不是 SSH 会话（可能已断开，请重新打开标签）")
    try:
        client = session.open_sftp(cached=False)
    except Exception as exc:  # noqa: BLE001 - 失败要回退，不静默
        # 只记异常类型：异常文本可能带远端路径（AGENTS §9.14 日志不记用户内容）
        logger.warning(
            "传输专用 SFTP 通道打开失败（%s），退回复用会话级 client",
            type(exc).__name__,
        )
        lock = _sftp_lock(session_id)
        lock.acquire()
        # 计数在**打开之前**加：打开期间用户点「取消加载」也不能关掉它
        _sftp_shared_transfers[session_id] = (
            _sftp_shared_transfers.get(session_id, 0) + 1
        )
        try:
            client = _sftp(session_id)
        except Exception:
            remaining = _sftp_shared_transfers.get(session_id, 0) - 1
            if remaining > 0:
                _sftp_shared_transfers[session_id] = remaining
            else:
                _sftp_shared_transfers.pop(session_id, None)
            lock.release()
            raise
        return client, True, lock
    _sftp_transfer_clients.setdefault(session_id, set()).add(client)
    return client, False, None


def _finish_transfer_sftp(
    session_id: str,
    client: Any,
    shared: bool = False,
    lock: threading.RLock | None = None,
) -> None:
    """
    传输结束：共享 client 只释放锁（**不关闭**，否则目录浏览会一起坏掉）；
    专用 client 注销并关闭。
    """
    if shared:
        # 先减计数再放锁：保证 abort 看到 0 时传输已结束，不会误关正在用的 client
        remaining = _sftp_shared_transfers.get(session_id, 0) - 1
        if remaining > 0:
            _sftp_shared_transfers[session_id] = remaining
        else:
            _sftp_shared_transfers.pop(session_id, None)
    if lock is not None:
        try:
            lock.release()
        except Exception:  # noqa: BLE001 - 释放失败不应掩盖传输结果
            pass
    if shared or client is None:
        return
    clients = _sftp_transfer_clients.get(session_id)
    if clients is not None:
        clients.discard(client)
        if not clients:
            _sftp_transfer_clients.pop(session_id, None)
    session = _sessions.get(session_id)
    if session is not None and hasattr(session, "release_sftp"):
        try:
            session.release_sftp(client)
        except Exception:  # noqa: BLE001
            pass
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass


def _h_sftp_download(params: dict) -> dict:
    """
    后台下载（进度经 `remote.sftp_progress` 推送）。

    传输通道在**后台线程**里建立：受限服务器拒绝第二条 SFTP 通道时会回退复用
    会话级 client；两条路都失败也会发 `remote.sftp_failed`（带文件名），
    不会再出现「点了下载什么都没有」的静默失败。
    """
    session_id = str(params["session_id"])
    remote_path = str(params["remote_path"])
    local_path = str(params["local_path"])
    name = remote_path.replace("\\", "/").rsplit("/", 1)[-1]
    bytes_total = max(0, int(params.get("bytes_total") or 0))
    generation = _sftp_cancel_generations.get(session_id, 0)
    transfer_id = uuid.uuid4().hex
    last = {"value": 0}

    def progress(done: int, total: int) -> None:
        nonlocal bytes_total
        bytes_total = max(bytes_total, int(total))
        if done - last["value"] < 256 * 1024 and done != total:
            return
        last["value"] = done
        _publish(
            "remote.sftp_progress",
            {
                "transfer_id": transfer_id,
                "session_id": session_id,
                "bytes_done": int(done),
                "bytes_total": int(total),
                "name": name,
                "direction": "download",
            },
        )

    def run() -> None:
        sftp: Any = None
        shared = False
        lock: threading.RLock | None = None
        ok = False
        message = ""
        try:
            if _transfer_cancelled(session_id, generation):
                raise _TransferCancelled()
            sftp, shared, lock = _open_transfer_sftp(session_id)
            if _transfer_cancelled(session_id, generation):
                raise _TransferCancelled()
            sftp.get(remote_path, local_path, callback=progress)
            ok = True
        except _TransferCancelled:
            message = "已取消传输"
        except Exception as exc:  # noqa: BLE001
            message = (
                "已取消传输"
                if _transfer_cancelled(session_id, generation)
                else _friendly_sftp_error(exc)
            )
        finally:
            _finish_transfer_sftp(session_id, sftp, shared, lock)

        payload = {
            "transfer_id": transfer_id,
            "session_id": session_id,
            "name": name,
            "direction": "download",
            "bytes_total": bytes_total,
        }
        if ok:
            _publish("remote.sftp_done", {**payload, "local_path": local_path})
        else:
            _publish("remote.sftp_failed", {**payload, "message": message})

    threading.Thread(target=run, name="sftp-download", daemon=True).start()
    return {"transfer_id": transfer_id}


def _h_sftp_upload(params: dict) -> dict:
    """后台上传（进度经 `remote.sftp_progress` 推送；通道建立同样在后台线程）"""
    session_id = str(params["session_id"])
    local_path = str(params["local_path"])
    remote_path = str(params["remote_path"])
    name = local_path.replace("\\", "/").rsplit("/", 1)[-1]
    bytes_total = max(0, int(params.get("bytes_total") or 0))
    generation = _sftp_cancel_generations.get(session_id, 0)
    transfer_id = uuid.uuid4().hex
    last = {"value": 0}

    def progress(done: int, total: int) -> None:
        nonlocal bytes_total
        bytes_total = max(bytes_total, int(total))
        if done - last["value"] < 256 * 1024 and done != total:
            return
        last["value"] = done
        _publish(
            "remote.sftp_progress",
            {
                "transfer_id": transfer_id,
                "session_id": session_id,
                "bytes_done": int(done),
                "bytes_total": int(total),
                "name": name,
                "direction": "upload",
            },
        )

    def run() -> None:
        sftp: Any = None
        shared = False
        lock: threading.RLock | None = None
        ok = False
        message = ""
        try:
            if _transfer_cancelled(session_id, generation):
                raise _TransferCancelled()
            sftp, shared, lock = _open_transfer_sftp(session_id)
            if _transfer_cancelled(session_id, generation):
                raise _TransferCancelled()
            sftp.put(local_path, remote_path, callback=progress)
            ok = True
        except _TransferCancelled:
            message = "已取消传输"
        except Exception as exc:  # noqa: BLE001
            message = (
                "已取消传输"
                if _transfer_cancelled(session_id, generation)
                else _friendly_sftp_error(exc)
            )
        finally:
            _finish_transfer_sftp(session_id, sftp, shared, lock)

        payload = {
            "transfer_id": transfer_id,
            "session_id": session_id,
            "name": name,
            "direction": "upload",
            "bytes_total": bytes_total,
        }
        if ok:
            _publish("remote.sftp_done", {**payload, "remote_path": remote_path})
        else:
            _publish("remote.sftp_failed", {**payload, "message": message})

    threading.Thread(target=run, name="sftp-upload", daemon=True).start()
    return {"transfer_id": transfer_id}


def _h_sftp_abort(params: dict) -> dict:
    """
    取消**目录加载**：只丢弃会话级 SFTP client，不影响正在进行的传输。

    与 `sftp_cancel` 分开：后者是「取消传输」，会把传输专用 client 也关掉；
    在目录加载中点取消不应误伤同时进行的上传/下载。

    受限服务器上传输会回退到**会话级 client**（`_sftp_shared_transfers` 计数 > 0），
    此时关闭它会直接打断传输 —— 必须跳过，等传输结束再说。
    """
    session_id = str(params.get("session_id") or "")
    if _sftp_shared_transfers.get(session_id, 0) > 0:
        logger.info("取消目录加载跳过：该会话有共享 client 传输进行中，关闭会误伤传输")
        return {"ok": False}
    client = _sftp_clients.pop(session_id, None)
    closed_any = client is not None
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    session = _sessions.get(session_id)
    if session is not None and hasattr(session, "drop_sftp"):
        try:
            session.drop_sftp()
        except Exception:  # noqa: BLE001
            pass
        closed_any = True
    return {"ok": closed_any}


def _h_sftp_cancel(params: dict) -> dict:
    """
    取消传输：关闭会话级 client + 全部传输专用 client，并推进取消代次。

    代次用于覆盖「通道还在后台打开 / 传输排在共享锁后」的窗口：仅关闭已有 client
    挡不住尚未开始的传输；传输线程在开通道前后、真正读写前都会比较代次。

    同时让 `SshSession` 清缓存，否则下一次 `open_sftp()` 会返回已关闭的同一个 client。
    只取消目录加载请用 `sftp_abort`（不会误伤正在进行的上传/下载）。
    """
    session_id = str(params.get("session_id") or "")
    _sftp_cancel_generations[session_id] = (
        _sftp_cancel_generations.get(session_id, 0) + 1
    )
    client = _sftp_clients.pop(session_id, None)
    closed_any = client is not None
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    session = _sessions.get(session_id)
    if session is not None and hasattr(session, "drop_sftp"):
        try:
            session.drop_sftp()
        except Exception:  # noqa: BLE001
            pass
    closed_any = bool(_sftp_transfer_clients.get(session_id)) or closed_any
    _close_transfer_clients(session_id)
    return {"ok": closed_any}


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
        on_status=_local_status(host, session_id),
    )
    token = host.register(session_id, session)
    _sessions[session_id] = session

    def run() -> None:
        try:
            session.open()
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            # 失败必须"留痕"：事件可能早于渲染层建标签页到达（审查 S1），
            # 渲染层建好标签页后会调 local_term.session_status 补查一次。
            local_terminal_service.remember_open_state(session_id, "failed", message)
            # 失败不回收会持续泄漏会话/通道（审查 M4）
            _sessions.pop(session_id, None)
            try:
                host.close_session(session_id)
            except Exception:  # noqa: BLE001 - 回收阶段忽略
                pass
            _publish(
                "local_term.session_failed",
                {"session_id": session_id, "message": message},
            )
            return
        local_terminal_service.remember_open_state(session_id, "ready")
        _publish("local_term.session_ready", {"session_id": session_id})

    threading.Thread(target=run, name="local-term-open", daemon=True).start()
    return {"session_id": session_id, "port": host.port, "token": token}


def _h_clipboard_read(_params: dict) -> dict:
    """读系统剪贴板（终端粘贴兜底；GUI 线程执行，见 MAIN_THREAD_METHODS）"""
    from PySide6.QtGui import QGuiApplication

    clipboard = QGuiApplication.clipboard()
    return {"text": clipboard.text() if clipboard is not None else ""}


def _h_clipboard_write(params: dict) -> dict:
    """写系统剪贴板（终端复制兜底；GUI 线程执行）"""
    from PySide6.QtGui import QGuiApplication

    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(str(params.get("text") or ""))
    return {"ok": True}


def _h_local_session_status(params: dict) -> dict:
    """查询本机终端打开结果（ready / failed / pending），供渲染层补查"""
    return local_terminal_service.get_open_state(str(params.get("session_id") or ""))


#: 必须跑在 GUI 线程的方法（QClipboard 不是线程安全的）
MAIN_THREAD_METHODS: set[tuple[str, str]] = {
    ("local_term", "clipboard_read"),
    ("local_term", "clipboard_write"),
    # QFileDialog 必须在 GUI 线程
    ("remote", "pick_private_key"),
}


HANDLERS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "remote": {
        "connections_list": _h_connections_list,
        "connections_get": _h_connections_get,
        "connections_create": _h_connections_create,
        "connections_update": _h_connections_update,
        "pick_private_key": _h_pick_private_key,
        "validate_private_key": _h_validate_private_key,
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
        "sftp_abort": _h_sftp_abort,
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
        "session_status": _h_local_session_status,
        "clipboard_read": _h_clipboard_read,
        "clipboard_write": _h_clipboard_write,
    },
}

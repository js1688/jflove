"""
远程连接多因素认证 / SFTP 打开边界回归（v1.10.13）

## 钉住的两类根因

1. **paramiko 的部分认证语义**：`auth_publickey` / `auth_password` 在服务器
   要求继续认证时**返回剩余方式列表**（不抛异常）。旧实现忽略返回值，直接发
   `phase=ready`，界面显示「已连接」但 `transport.is_authenticated()` 为假，
   SFTP 目录永远出不来。
2. **SFTP 子系统打开无超时**：paramiko 的 `invoke_subsystem("sftp")` 内部是
   无超时 `event.wait()`，服务端不回应就永久卡在「加载中」。

本文件用「真实返回列表」的假 transport + 本地 paramiko 测试 SSH 服务
（`AUTH_PARTIALLY_SUCCESSFUL` + 键盘交互）覆盖这两条。
"""

from __future__ import annotations

import socket
import stat
import threading
import time
from io import StringIO
from typing import Any

import paramiko
import pytest

import src.services.ssh_session as ssh_module
from src.services.ssh_session import AuthError, SshError, SshSession


def _private_key_pem(passphrase: str = "") -> str:
    """生成一把 paramiko 可解析的 RSA 私钥文本"""
    key = paramiko.RSAKey.generate(bits=1024)
    buffer = StringIO()
    key.write_private_key(buffer, password=passphrase or None)
    return buffer.getvalue()


def _row(auth_type: str, password: str = "", private_key: str = "") -> dict:
    return {
        "id": 1,
        "auth_type": auth_type,
        "username": "tester",
        "password": password,
        "private_key": private_key or _private_key_pem(),
        "passphrase": "",
        "host_key_fingerprint": "",
    }


# ── 假 transport：按脚本复刻 paramiko 的真实返回语义 ─────


class _ScriptedTransport:
    """
    只实现认证流程用到的接口。

    脚本项：
    - `("publickey", "ok"|"partial"|"reject"|"fail", allowed)`
    - `("password", "ok"|"partial"|"reject"|"fail", allowed)`
    - `("keyboard-interactive", "ok"|"reject"|"fail", prompts)`
    """

    def __init__(self, script: list[tuple[Any, ...]]) -> None:
        self.script = list(script)
        self.calls: list[str] = []
        self.authenticated = False
        self.interactive_prompts: list[str] = []
        self.interactive_answers: list[str] = []

    def is_authenticated(self) -> bool:
        return self.authenticated

    def _pop(self, method: str) -> tuple[Any, ...]:
        self.calls.append(method)
        if not self.script:
            self.authenticated = True
            return ("ok", [])
        step = self.script.pop(0)
        assert str(step[0]) == method, f"脚本顺序不符：期望 {step[0]}，实际 {method}"
        return step

    def auth_publickey(self, _username: str, _key: paramiko.PKey) -> list[str]:
        _m, result, *rest = self._pop("publickey")
        allowed = list(rest[0]) if rest else []
        if result == "ok":
            self.authenticated = True
            return allowed
        if result == "partial":
            return allowed
        if result == "reject":
            raise paramiko.BadAuthenticationType("Bad authentication type", allowed)
        raise paramiko.AuthenticationException("Authentication failed.")

    def auth_password(self, _username: str, _password: str) -> list[str]:
        _m, result, *rest = self._pop("password")
        allowed = list(rest[0]) if rest else []
        if result == "ok":
            self.authenticated = True
            return allowed
        if result == "partial":
            return allowed
        if result == "reject":
            raise paramiko.BadAuthenticationType("Bad authentication type", allowed)
        raise paramiko.AuthenticationException("Authentication failed.")

    def auth_interactive(self, _username: str, handler: Any) -> list[str]:
        _m, result, *rest = self._pop("keyboard-interactive")
        prompts = list(rest[0]) if rest else [("Password: ", False)]
        if result == "reject":
            raise paramiko.BadAuthenticationType(
                "Bad authentication type", ["keyboard-interactive"]
            )
        if result == "fail":
            raise paramiko.AuthenticationException("Authentication failed.")
        self.interactive_prompts = [str(item[0]) for item in prompts]
        self.interactive_answers = list(handler("JFLove", "continue", prompts))
        self.authenticated = True
        return []


# ── 真实语义：partial = 返回列表，不抛异常 ───────────────


def test_publickey_partial_then_password() -> None:
    """密钥通过 → 服务器返回 [password] → 补账号密码并真正认证"""
    session = SshSession(_row("password_publickey", password="login-pw"))
    transport = _ScriptedTransport([("publickey", "partial", ["password"]), ("password", "ok")])
    session._authenticate(transport, session.connection)
    assert transport.calls == ["publickey", "password"]
    assert transport.is_authenticated() is True


def test_publickey_partial_then_keyboard_interactive_uses_account_password() -> None:
    """密钥通过 → 服务器只要 keyboard-interactive → 用账号密码代答 Password:"""
    session = SshSession(_row("password_publickey", password="login-pw"))
    transport = _ScriptedTransport(
        [
            ("publickey", "partial", ["keyboard-interactive"]),
            ("keyboard-interactive", "ok", [("Password: ", False)]),
        ]
    )
    session._authenticate(transport, session.connection)
    assert transport.calls == ["publickey", "keyboard-interactive"]
    assert transport.interactive_prompts == ["Password: "]
    assert transport.interactive_answers == ["login-pw"]
    assert transport.is_authenticated() is True


def test_publickey_partial_interactive_without_stored_password_prompts_ui() -> None:
    """只配了密钥、服务器临时要密码：UI 弹层回答一次也要能完成认证"""
    answers = ["typed-pw"]
    session = SshSession(
        _row("publickey"),
        otp_ask=lambda _prompt: answers.pop(0) if answers else "",
    )
    transport = _ScriptedTransport(
        [
            ("publickey", "partial", ["keyboard-interactive"]),
            ("keyboard-interactive", "ok", [("Password: ", False)]),
        ]
    )
    session._authenticate(transport, session.connection)
    assert transport.interactive_answers == ["typed-pw"]
    assert transport.is_authenticated() is True


def test_password_partial_then_publickey() -> None:
    """password,publickey 顺序：密钥不被允许 → 密码 partial → 再补密钥"""
    session = SshSession(_row("password_publickey", password="login-pw"))
    transport = _ScriptedTransport(
        [
            ("publickey", "reject", ["password"]),
            ("password", "partial", ["publickey"]),
            ("publickey", "ok"),
        ]
    )
    session._authenticate(transport, session.connection)
    assert transport.calls == ["publickey", "password", "publickey"]
    assert transport.is_authenticated() is True


def test_partial_without_any_followup_raises() -> None:
    """密钥 partial 后补密码也失败 → AuthError（不能当成已连接）"""
    session = SshSession(_row("password_publickey", password="login-pw"))
    transport = _ScriptedTransport(
        [("publickey", "partial", []), ("password", "fail")]
    )
    with pytest.raises(AuthError, match="认证未完成"):
        session._authenticate(transport, session.connection)
    assert transport.is_authenticated() is False


def test_key_rejected_server_only_allows_key_does_not_try_password() -> None:
    """服务器明确只认密钥且密钥失败：不再无谓试密码"""
    session = SshSession(_row("password_publickey", password="login-pw"))
    transport = _ScriptedTransport([("publickey", "reject", ["publickey"])])
    with pytest.raises(AuthError, match="密钥"):
        session._authenticate(transport, session.connection)
    assert transport.calls == ["publickey"]


def test_password_rejected_switches_to_keyboard_interactive() -> None:
    """密码方式不被允许、但服务器支持 keyboard-interactive：自动切过去"""
    session = SshSession(_row("password_keyboard", password="login-pw"))
    transport = _ScriptedTransport(
        [
            ("password", "reject", ["keyboard-interactive"]),
            ("keyboard-interactive", "ok", [("Password: ", False)]),
        ]
    )
    session._authenticate(transport, session.connection)
    assert transport.calls == ["password", "keyboard-interactive"]
    assert transport.interactive_answers == ["login-pw"]
    assert transport.is_authenticated() is True


# ── SFTP 打开边界 ─────────────────────────────────────


class _BlockingChannel:
    """永不回应的 SFTP channel：invoke_subsystem 阻塞，close 才返回"""

    def __init__(self) -> None:
        self.closed = threading.Event()
        self.timeout: float | None = None

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def invoke_subsystem(self, _name: str) -> None:
        self.closed.wait(10)
        raise paramiko.SSHException("Channel closed.")

    def close(self) -> None:
        self.closed.set()


class _SftpTransport:
    """只用于 open_sftp 边界的假 transport"""

    def __init__(self, authenticated: bool = True) -> None:
        self.authenticated = authenticated
        self.last_timeout: float | None = None

    def is_authenticated(self) -> bool:
        return self.authenticated

    def open_session(self, timeout: float | None = None) -> _BlockingChannel:
        self.last_timeout = timeout
        return _BlockingChannel()


def test_open_sftp_times_out_instead_of_hanging(monkeypatch) -> None:
    """服务端不回应 sftp 子系统：必须在阈值内失败，并关闭 channel"""
    monkeypatch.setattr(ssh_module, "SFTP_OPEN_TIMEOUT", 0.2)
    session = SshSession({"mode": "sftp"})
    session._transports = [_SftpTransport()]  # type: ignore[list-item]
    started = time.monotonic()
    with pytest.raises(SshError, match="无响应"):
        session.open_sftp()
    assert time.monotonic() - started < 3.0


def test_open_sftp_rejects_unauthenticated_session() -> None:
    """未认证会话不得伪装成已连接去开 SFTP，必须给明确原因"""
    session = SshSession({"mode": "sftp"})
    session._transports = [_SftpTransport(authenticated=False)]  # type: ignore[list-item]
    with pytest.raises(AuthError, match="认证"):
        session.open_sftp()


class _CloseableClient:
    """记录 close 调用的假 SFTP client"""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_drop_sftp_clears_session_cache() -> None:
    """取消加载后必须清会话侧缓存，否则下一次会拿到已关闭的 client"""
    session = SshSession({"mode": "sftp"})
    fake = _CloseableClient()
    session._sftp_client = fake  # type: ignore[assignment]
    session.drop_sftp()
    assert session._sftp_client is None
    assert fake.closed is True


# ── 真实 paramiko 测试 SSH 服务（双因素） ────────────────

_USER = "tester"
_PASSWORD = "login-pw"


class _TestServer(paramiko.ServerInterface):
    """
    真实 SSH 服务端。

    :param partial: True 时 publickey 返回 AUTH_PARTIALLY_SUCCESSFUL 并继续要求键盘交互；
                    False 时公钥一次通过（覆盖简单密钥场景）
    """

    def __init__(self, partial: bool = True) -> None:
        self.partial = partial
        self.key_seen = False
        self.interactive_answers: list[str] = []
        self.interactive_ok = False

    def check_auth_publickey(self, username: str, _key: paramiko.PKey) -> int:
        if username != _USER:
            return paramiko.AUTH_FAILED
        self.key_seen = True
        if self.partial:
            return paramiko.AUTH_PARTIALLY_SUCCESSFUL
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, _username: str) -> str:
        return "keyboard-interactive" if self.key_seen else "publickey"

    def check_auth_interactive(self, username: str, _submethods: str) -> Any:
        if username != _USER:
            return paramiko.AUTH_FAILED
        return paramiko.InteractiveQuery(
            "JFLove test", "Password required", ("Password: ", False)
        )

    def check_auth_interactive_response(self, responses: list[str]) -> int:
        self.interactive_answers = list(responses or [])
        self.interactive_ok = bool(responses and responses[0] == _PASSWORD)
        if self.interactive_ok:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, _chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_subsystem_request(self, channel: paramiko.Channel, name: str) -> bool:
        """接受 sftp 子系统请求，并交给 paramiko 启动已注册的子系统处理器"""
        if name != "sftp":
            return False
        return super().check_channel_subsystem_request(channel, name)


class _StubSFTP(paramiko.SFTPServerInterface):
    """最小 SFTP 服务：只实现列目录，返回固定两项"""

    def list_folder(self, _path: str) -> list[paramiko.SFTPAttributes]:
        folder = paramiko.SFTPAttributes()
        folder.filename = "opt"
        folder.st_mode = stat.S_IFDIR | 0o755
        folder.st_mtime = 1700000000
        file_attr = paramiko.SFTPAttributes()
        file_attr.filename = "readme.txt"
        file_attr.st_mode = stat.S_IFREG | 0o644
        file_attr.st_size = 12
        file_attr.st_mtime = 1700000000
        return [folder, file_attr]

    def stat(self, _path: str) -> paramiko.SFTPAttributes:
        """目录本身用 '.' 表示；返回一个目录属性"""
        attr = paramiko.SFTPAttributes()
        attr.filename = "."
        attr.st_mode = stat.S_IFDIR | 0o755
        attr.st_mtime = 1700000000
        return attr

    def lstat(self, path: str) -> paramiko.SFTPAttributes:
        return self.stat(path)


class _LocalSshServer:
    """本地真实 SSH 服务：绑定 127.0.0.1 随机端口，支持 sftp 子系统"""

    def __init__(self, partial: bool = True) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.server_key = paramiko.RSAKey.generate(2048)
        self.interface = _TestServer(partial=partial)
        self.transport: paramiko.Transport | None = None
        self.thread = threading.Thread(target=self._run, name="test-ssh-server", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            client, _addr = self.sock.accept()
        except OSError:
            return
        self.transport = paramiko.Transport(client)
        self.transport.add_server_key(self.server_key)
        self.transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _StubSFTP)
        try:
            self.transport.start_server(server=self.interface)
        except Exception:  # noqa: BLE001 - 测试线程静默退出
            return

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
        if self.transport is not None:
            self.transport.close()
        self.thread.join(timeout=2)


def _open_session_to(server: "_LocalSshServer", auth_type: str, password: str = "") -> SshSession:
    """连到本地测试 SSH 服务（测试里复用，减少重复字典）"""
    session = SshSession(
        {
            "id": 9,
            "host": "127.0.0.1",
            "port": server.port,
            "username": _USER,
            "auth_type": auth_type,
            "password": password,
            "private_key": _private_key_pem(),
            "passphrase": "",
            "jump_connection_id": 0,
            "host_key_fingerprint": "",
            "mode": "sftp",
        },
        hostkey_ask=lambda _row, _fp: True,
    )
    session.open()
    return session


def test_real_paramiko_server_multifactor_auth(monkeypatch) -> None:
    """真实 paramiko 语义端到端：密钥 partial → keyboard-interactive 补账号密码"""
    server = _LocalSshServer()
    monkeypatch.setattr(
        ssh_module.remote_service, "save_host_key_fingerprint", lambda *a, **k: None
    )
    try:
        session = SshSession(
            {
                "id": 9,
                "host": "127.0.0.1",
                "port": server.port,
                "username": _USER,
                "auth_type": "password_publickey",
                "password": _PASSWORD,
                "private_key": _private_key_pem(),
                "passphrase": "",
                "jump_connection_id": 0,
                "host_key_fingerprint": "",
                "mode": "sftp",
            },
            hostkey_ask=lambda _row, _fp: True,
        )
        session.open()
        # 真正认证通过：服务端收到了密码（且客户端不会再误报 ready 后卡住）
        assert server.interface.interactive_ok is True
        assert server.interface.interactive_answers == [_PASSWORD]
        assert session._transports[-1].is_authenticated() is True
        session.close()
    finally:
        server.close()


def test_real_paramiko_server_key_auth_and_sftp_list(monkeypatch) -> None:
    """简单密钥场景 + 真实 SFTP 子系统：认证后能列出目录（问题 2 的回归）"""
    server = _LocalSshServer(partial=False)
    monkeypatch.setattr(
        ssh_module.remote_service, "save_host_key_fingerprint", lambda *a, **k: None
    )
    try:
        session = _open_session_to(server, auth_type="publickey")
        assert session._transports[-1].is_authenticated() is True
        cached = session.open_sftp()
        names = [attr.filename for attr in cached.listdir_attr(".")]
        assert set(names) == {"opt", "readme.txt"}

        # 传输专用 client 不得覆盖会话级缓存（否则传输结束会连带关掉目录浏览）
        dedicated = session.open_sftp(cached=False)
        assert dedicated is not cached
        assert session._sftp_client is cached
        assert [attr.filename for attr in dedicated.listdir_attr(".")] == ["opt", "readme.txt"]
        dedicated.close()
        session.close()
    finally:
        server.close()

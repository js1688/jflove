"""
网关「公钥 + 账号密码」双因子回归（v1.10.19，按 RFC 4252 重写）

## 前提修正（2026-11-20 17:10 外部客户端 Debug 2 日志）

本文件 v1.10.16 版本曾假设「服务器 authlist 只列 password、真正的第二因子在
keyboard-interactive 里」，据此让客户端在服务器**未列出** keyboard-interactive 时也发它。
该假设**已被日志证伪**：

```text
Server offered these authentication methods: publickey,password
Offer of public key accepted
Server offered these authentication methods: password
Prompt (password, "SSH password", ...)
Sent password
Access granted
```

服务器第二因子就是 `password` 方法；标准客户端是**本地弹框收集密码，再用
`password` 方法发送**。全程没有 keyboard-interactive。

因此本文件改为钉住标准行为：

1. 服务器列 `password` 且本地无密码 → 弹框 → **以 password 方法发送** → 通过；
2. 密钥被接受后**不得**再用同一把钥反复重发（避免被网关当成「还差一把钥」）；
3. **绝不发送服务器未列出的方法**（kbd 只在服务器列出时才能用）。
"""

from __future__ import annotations

import socket
import threading
from io import StringIO

import paramiko
import pytest

import src.services.ssh_session as ssh_module
from src.services.ssh_session import AuthError, SshSession

_USER = "INGW-KPAYS001"
_PASSWORD = "ServerLoginPw!"


def _private_key_pem(key: paramiko.PKey) -> str:
    """把 RSA 私钥导出成 PEM 文本"""
    buffer = StringIO()
    key.write_private_key(buffer, password=None)
    return buffer.getvalue()


def _row(private_key: str, *, password: str = "", auth_type: str = "password_publickey") -> dict:
    return {
        "id": 21,
        "host": "127.0.0.1",
        "port": 0,
        "username": _USER,
        "auth_type": auth_type,
        "password": password,
        "private_key": private_key,
        "passphrase": "",
        "jump_connection_id": 0,
        "host_key_fingerprint": "",
        "mode": "sftp",
    }


class _KbdFactorGateway(paramiko.ServerInterface):
    """
    复刻用户那台网关：公钥通过后只列 `password`（与外部客户端 Debug 2 日志一致）。

    :param advertise_kbd: 额外把 `keyboard-interactive` 也列出来（与 password 并列）
    :param kbd_only: 公钥通过后**只**列 `keyboard-interactive`（验证真的按列表走）
    :param extra_publickey_after_reject: 密码被拒后把 authlist 交回 publickey
                                        （复刻旧实现重发密钥的死循环条件）
    """

    def __init__(
        self,
        authorized_base64: str,
        *,
        advertise_kbd: bool = False,
        kbd_only: bool = False,
        extra_publickey_after_reject: bool = True,
    ) -> None:
        self.authorized_base64 = authorized_base64
        self.advertise_kbd = advertise_kbd
        self.kbd_only = kbd_only
        self.extra_publickey_after_reject = extra_publickey_after_reject
        self.key_ok = False
        self.need_password = False
        self.trace: list[str] = []

    def get_allowed_auths(self, _username: str) -> str:
        if not self.key_ok:
            return "publickey,password"
        if self.need_password:
            if self.kbd_only:
                return "keyboard-interactive"
            return "password,keyboard-interactive" if self.advertise_kbd else "password"
        return "publickey" if self.extra_publickey_after_reject else ""

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        self.trace.append("publickey")
        if username != _USER or key.get_base64() != self.authorized_base64:
            return paramiko.AUTH_FAILED
        if self.key_ok and not self.need_password:
            # 密钥已通过还要再来一次 ⇒ 网关认为还缺一把密钥
            self.trace.append("partial(publickey)")
            return paramiko.AUTH_PARTIALLY_SUCCESSFUL
        self.key_ok = True
        self.need_password = True
        self.trace.append("partial(password)")
        return paramiko.AUTH_PARTIALLY_SUCCESSFUL

    def check_auth_password(self, _username: str, password: str) -> int:
        self.trace.append(f"password({password!r})")
        if password == _PASSWORD:
            return paramiko.AUTH_SUCCESSFUL
        self.need_password = False
        return paramiko.AUTH_FAILED

    def check_auth_interactive(self, _username: str, _submethods: str):
        self.trace.append("keyboard-interactive")
        return paramiko.InteractiveQuery(
            "Further authentication required", "", ("Password: ", False)
        )

    def check_auth_interactive_response(self, responses) -> int:
        self.trace.append(f"kbd({list(responses)!r})")
        return (
            paramiko.AUTH_SUCCESSFUL
            if list(responses) == [_PASSWORD]
            else paramiko.AUTH_FAILED
        )

    def check_channel_request(self, kind: str, _chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _LocalGateway:
    """本地真实 SSH 服务（主机密钥 ssh-rsa，贴近旧网关）"""

    def __init__(self, authorized_base64: str, **options) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.interface = _KbdFactorGateway(authorized_base64, **options)
        self.thread = threading.Thread(target=self._run, name="kbd-gw", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            client, _addr = self.sock.accept()
        except OSError:
            return
        transport = paramiko.Transport(client)
        transport.add_server_key(paramiko.RSAKey.generate(2048))
        transport._preferred_pubkeys = ("ssh-rsa", "rsa-sha2-512", "rsa-sha2-256")
        try:
            transport.start_server(server=self.interface)
            transport.join()
        except Exception:  # noqa: BLE001 - 测试线程静默退出
            return

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
        self.thread.join(timeout=2)


@pytest.fixture(autouse=True)
def _no_fingerprint_save(monkeypatch):
    """主机指纹落库对断言无意义"""
    monkeypatch.setattr(
        ssh_module.remote_service, "save_host_key_fingerprint", lambda *a, **k: None
    )


def _open(row: dict, gateway: _LocalGateway, *, answers: list[str] | None = None) -> SshSession:
    """打开会话；`answers` 为键盘交互弹层的应答序列"""
    row["port"] = gateway.port
    pending = list(answers or [])
    session = SshSession(
        row,
        hostkey_ask=lambda _row, _fp: True,
        otp_ask=lambda _prompt: pending.pop(0) if pending else "",
    )
    session.open()
    return session


def test_no_local_password_prompts_then_sends_password_method() -> None:
    """
    服务器列 `password`、本地没有密码 ⇒ 弹框收集，**以 password 方法发送**并连上。

    这正是外部客户端 Debug 2 日志里的标准流程（`Prompt (password, ...)` → `Sent password`）。
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        session = _open(_row(_private_key_pem(key)), gateway, answers=[_PASSWORD])
        assert session._transports[-1].is_authenticated() is True
        trace = gateway.interface.trace
        assert trace.count("publickey") == 1
        #: 第二因子必须用服务器列出的 password 方法，且只发一次
        assert any(item.startswith("password(") for item in trace), f"未以 password 方法发送：{trace}"
        assert not any(item.startswith("keyboard-interactive") for item in trace), (
            f"发了服务器未列出的方法：{trace}"
        )
        session.close()
    finally:
        gateway.close()


def test_prefers_password_when_server_lists_both() -> None:
    """
    服务器同时列 `password` 与 `keyboard-interactive` 时优先走 `password`。

    依据外部客户端日志：真实会话里第二因子就是 password（`Sent password`），
    keyboard-interactive 只是它「也允许」的备选。
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64(), advertise_kbd=True)
    try:
        session = _open(_row(_private_key_pem(key)), gateway, answers=[_PASSWORD])
        assert session._transports[-1].is_authenticated() is True
        trace = gateway.interface.trace
        assert any(item.startswith("password(") for item in trace), f"未用 password：{trace}"
        assert not any(item.startswith("keyboard-interactive") for item in trace), trace
        session.close()
    finally:
        gateway.close()


def test_keyboard_interactive_used_when_server_lists_only_it() -> None:
    """服务器**只**列 keyboard-interactive 时，走键盘交互并按其 prompt 应答"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64(), kbd_only=True)
    try:
        session = _open(_row(_private_key_pem(key)), gateway, answers=[_PASSWORD])
        assert session._transports[-1].is_authenticated() is True
        assert any(
            item.startswith("keyboard-interactive") for item in gateway.interface.trace
        )
        session.close()
    finally:
        gateway.close()


def test_key_accepted_never_resent_in_dead_loop() -> None:
    """密钥被接受后不得反复重发同一把钥（避免被网关当成「还差一把钥」）"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        try:
            _open(_row(_private_key_pem(key), password="wrong"), gateway, answers=["bad"])
        except AuthError:
            pass
        trace = gateway.interface.trace
        assert trace.count("publickey") <= 1, f"密钥被重发了：{trace}"
    finally:
        gateway.close()


def test_failure_message_points_at_account_password() -> None:
    """密码确实不对：文案指向账号密码，且带实际序列、不含用户输入"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(_row(_private_key_pem(key)), gateway, answers=["also-wrong"])
        message = str(excinfo.value)
        assert "账号密码" in message
        assert "认证序列=" in message
        assert "also-wrong" not in message
    finally:
        gateway.close()

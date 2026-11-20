"""
网关「公钥 + 账号密码」弹框回归（v1.10.19，按 RFC 4252）

## 本文件钉住的行为

用户实测的外部客户端 Debug 2 日志给出了标准答案：

```text
Server offered these authentication methods: publickey,password
Offer of public key accepted
Server offered these authentication methods: password
Prompt (password, "SSH password", ...)
Sent password
Access granted
```

即：公钥通过后，服务器**只列 `password`**，客户端**本地弹框收集密码，再用 `password`
方法发送**。全程没有 keyboard-interactive。

据此本文件断言：

1. 只配私钥（没填账号密码）时，服务器列出 `password` 必须**弹框**，且用 `password` 方法发送；
2. 已存密码时直接用它发送，不弹框；
3. 密码不对时，报错要说是**账号密码**问题，**不得**说「密钥被拒」；
4. 弹框没拿到输入时，报错要明确说「没有收到输入」；
5. 报错里不得出现用户输入的原文（安全宪法 §9.4）。
"""

from __future__ import annotations

import socket
import threading
from io import StringIO

import paramiko
import pytest

import src.services.ssh_session as ssh_module
from src.services.ssh_session import AuthError, SshSession

_USER = "gateway-user"
_PASSWORD = "CorrectServerPassword!"


def _private_key_pem(key: paramiko.PKey) -> str:
    """把 RSA 私钥导出成 PEM 文本"""
    buffer = StringIO()
    key.write_private_key(buffer, password=None)
    return buffer.getvalue()


def _row(private_key: str, *, auth_type: str = "publickey", password: str = "") -> dict:
    return {
        "id": 41,
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


class _PasswordGateway(paramiko.ServerInterface):
    """复刻用户那台网关：公钥通过后只列 `password`（与外部客户端日志一致）"""

    def __init__(self, authorized_base64: str) -> None:
        self.authorized_base64 = authorized_base64
        self.key_ok = False
        self.trace: list[str] = []

    def get_allowed_auths(self, _username: str) -> str:
        return "publickey,password" if not self.key_ok else "password"

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        self.trace.append("publickey")
        if username != _USER or key.get_base64() != self.authorized_base64:
            return paramiko.AUTH_FAILED
        self.key_ok = True
        return paramiko.AUTH_PARTIALLY_SUCCESSFUL

    def check_auth_password(self, _username: str, password: str) -> int:
        self.trace.append("password")
        if password == _PASSWORD:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, _chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _LocalGateway:
    """本地真实 SSH 服务（主机密钥 ssh-rsa，贴近旧网关）"""

    def __init__(self, authorized_base64: str) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.interface = _PasswordGateway(authorized_base64)
        self.thread = threading.Thread(target=self._run, name="pw-gw", daemon=True)
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


def _open(
    row: dict, gateway: _LocalGateway, *, answers: list[str] | None = None
) -> SshSession:
    """打开会话；`answers` 是弹框应答序列（空列表 = 用户没填 / 超时）"""
    row["port"] = gateway.port
    pending = list(answers or [])
    session = SshSession(
        row,
        hostkey_ask=lambda _row, _fp: True,
        otp_ask=lambda _prompt: pending.pop(0) if pending else "",
    )
    session.open()
    return session


def test_publickey_only_prompts_and_sends_password_method() -> None:
    """
    只配私钥、服务器列出 `password` ⇒ 弹框收集，**以 password 方法发送**并连上。

    （对应外部客户端日志：`Prompt (password, ...)` → `Sent password` → `Access granted`）
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        session = _open(_row(_private_key_pem(key)), gateway, answers=[_PASSWORD])
        assert session._transports[-1].is_authenticated() is True
        trace = gateway.interface.trace
        assert trace.count("publickey") == 1
        assert trace.count("password") == 1
        session.close()
    finally:
        gateway.close()


def test_wrong_password_blames_password_not_private_key() -> None:
    """密码错误：文案指向账号密码，**不得**出现「未接受当前密钥」这类结论"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(_row(_private_key_pem(key)), gateway, answers=["wrong-secret"])
        message = str(excinfo.value)
        assert "账号密码" in message
        assert "服务器未接受当前密钥" not in message
        assert "认证序列=" in message
        # §9.4：报错不得回显用户输入
        assert "wrong-secret" not in message
    finally:
        gateway.close()


def test_empty_prompt_is_reported_explicitly() -> None:
    """弹框没拿到输入（超时 / 未点确定）：文案必须说「没有收到」"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(_row(_private_key_pem(key)), gateway, answers=[])
        message = str(excinfo.value)
        assert "没有收到你输入的账号密码" in message
    finally:
        gateway.close()


def test_stored_password_is_used_without_prompt() -> None:
    """配置里存了密码时直接用 `password` 方法发送，不弹框"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    asked: list[str] = []
    row = _row(
        _private_key_pem(key), auth_type="password_publickey", password=_PASSWORD
    )
    row["port"] = gateway.port
    session = SshSession(
        row,
        hostkey_ask=lambda _row, _fp: True,
        otp_ask=lambda prompt: (asked.append(prompt), "")[1],
    )
    try:
        session.open()
        assert session._transports[-1].is_authenticated() is True
        assert asked == [], "已存密码时不应弹框"
        session.close()
    finally:
        gateway.close()

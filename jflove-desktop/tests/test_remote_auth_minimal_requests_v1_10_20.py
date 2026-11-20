"""
严格限次网关回归（v1.10.20）：认证请求必须最少，不得多发探测

## 钉住的问题

用户那台网关（Maverick 一类）只允许极少的认证尝试次数（实测等价于 `MaxAuthTries=2`）：
**公钥 1 次 + 密码 1 次，刚好用完**。此时任何多余的请求都会让正确的密码被拒。

v1.10.19 曾为了「先看方法列表」而发一次 `none` 探测，结果：

```text
【发 none】      none#1 → publickey#2 → password#3 → DENIED（超过 MaxAuthTries）
【JSch/WinSCP】  publickey#1 → password#2 → 成功
```

同一个正确密码，前者失败、后者成功 —— 这就是用户「只有某些客户端能连」的根因。

## 本文件判据

1. 标准场景只发 **2 次**认证请求（`publickey` + `password`），**不得**出现 `none`；
2. 服务器限次为 2 时仍能连通；
3. 去掉探测后，密码首次被拒仍有**重试**机会（本次改动不削弱容错）。
"""

from __future__ import annotations

import socket
import threading
from io import StringIO

import paramiko
import pytest

import src.services.ssh_session as ssh_module
from src.services.ssh_session import AuthError, SshSession

_USER = "strict-user"
_PASSWORD = "CorrectServerPassword!"


def _private_key_pem(key: paramiko.PKey) -> str:
    """把 RSA 私钥导出成 PEM 文本"""
    buffer = StringIO()
    key.write_private_key(buffer, password=None)
    return buffer.getvalue()


def _row(private_key: str, *, auth_type: str = "publickey", password: str = "") -> dict:
    return {
        "id": 51,
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


class _StrictGateway(paramiko.ServerInterface):
    """
    限制认证尝试次数的网关（复刻用户那台）。

    :param max_tries: 允许的认证请求总数（正确密码也计入）；0 表示不限制
    :param reject_first_password: 第一次密码请求故意拒绝（验证重试仍有效）
    """

    def __init__(
        self,
        authorized_base64: str,
        *,
        max_tries: int = 0,
        reject_first_password: bool = False,
    ) -> None:
        self.authorized_base64 = authorized_base64
        self.max_tries = max_tries
        self.reject_first_password = reject_first_password
        self.key_ok = False
        self.tries = 0
        self.password_count = 0
        #: 服务器实际收到的认证请求（按顺序，含被限次拒绝的）
        self.log: list[str] = []

    def _spend(self, kind: str) -> bool:
        """记一次认证尝试；超过上限返回 False（该请求被拒绝）"""
        self.tries += 1
        self.log.append(f"{kind}#{self.tries}")
        if self.max_tries and self.tries > self.max_tries:
            self.log.append(f"{kind}:DENIED")
            return False
        return True

    def get_allowed_auths(self, _username: str) -> str:
        return "publickey,password" if not self.key_ok else "password"

    def check_auth_none(self, _username: str) -> int:
        """探测请求同样计入次数（这正是问题所在）"""
        self._spend("none")
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        if not self._spend("publickey"):
            return paramiko.AUTH_FAILED
        if username != _USER or key.get_base64() != self.authorized_base64:
            return paramiko.AUTH_FAILED
        self.key_ok = True
        return paramiko.AUTH_PARTIALLY_SUCCESSFUL

    def check_auth_password(self, _username: str, password: str) -> int:
        if not self._spend("password"):
            return paramiko.AUTH_FAILED
        self.password_count += 1
        if self.reject_first_password and self.password_count == 1:
            return paramiko.AUTH_FAILED
        return (
            paramiko.AUTH_SUCCESSFUL
            if password == _PASSWORD
            else paramiko.AUTH_FAILED
        )

    def check_channel_request(self, kind: str, _chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _LocalStrictGateway:
    """本地真实 SSH 服务"""

    def __init__(self, authorized_base64: str, **options) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.interface = _StrictGateway(authorized_base64, **options)
        self.thread = threading.Thread(target=self._run, name="strict-gw", daemon=True)
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
    row: dict, gateway: _LocalStrictGateway, *, answers: list[str] | None = None
) -> SshSession:
    """打开会话；`answers` 为弹框应答序列"""
    row["port"] = gateway.port
    pending = list(answers or [])
    session = SshSession(
        row,
        hostkey_ask=lambda _row, _fp: True,
        otp_ask=lambda _prompt: pending.pop(0) if pending else "",
    )
    session.open()
    return session


def test_no_none_probe_before_publickey() -> None:
    """
    **不得**先发 `none` 探测：标准场景只应出现 `publickey` 与 `password`。

    这是 v1.10.20 的核心判据 —— 多一次探测就会吃掉限次网关的最后一次密码机会。
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalStrictGateway(key.get_base64())
    try:
        session = _open(_row(_private_key_pem(key)), gateway, answers=[_PASSWORD])
        assert session._transports[-1].is_authenticated() is True
        log = gateway.interface.log
        assert not any(item.startswith("none") for item in log), f"发了探测：{log}"
        assert log[:2] == ["publickey#1", "password#2"], f"请求序列异常：{log}"
        session.close()
    finally:
        gateway.close()


def test_connects_when_server_allows_only_two_tries() -> None:
    """
    服务器只允许 2 次认证请求（公钥 + 密码）时仍必须连通。

    这正是用户那台网关的形态：任何多余请求都会让正确密码被拒。
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalStrictGateway(key.get_base64(), max_tries=2)
    try:
        session = _open(_row(_private_key_pem(key)), gateway, answers=[_PASSWORD])
        assert session._transports[-1].is_authenticated() is True
        assert gateway.interface.log == ["publickey#1", "password#2"]
        session.close()
    finally:
        gateway.close()


def test_stored_password_also_within_two_tries() -> None:
    """配置里已存密码（不弹框）时同样只发 2 次请求"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalStrictGateway(key.get_base64(), max_tries=2)
    asked: list[str] = []
    row = _row(_private_key_pem(key), auth_type="password_publickey", password=_PASSWORD)
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
        assert gateway.interface.log == ["publickey#1", "password#2"]
        session.close()
    finally:
        gateway.close()


def test_password_retry_still_works_without_probe() -> None:
    """
    去掉探测后，密码首次被拒仍可重试（不削弱容错）。

    服务器允许 3 次（公钥 + 两次密码）；首次密码故意被拒，
    第二次（弹框重填）应当成功。限次为 2 的网关本身不提供重试机会。
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalStrictGateway(
        key.get_base64(), max_tries=3, reject_first_password=True
    )
    try:
        session = _open(
            _row(_private_key_pem(key)), gateway, answers=[_PASSWORD, _PASSWORD]
        )
        assert session._transports[-1].is_authenticated() is True
        log = gateway.interface.log
        assert not any(item.startswith("none") for item in log), f"发了探测：{log}"
        #: 公钥 + 两次密码（第二次才成功）
        assert log == ["publickey#1", "password#2", "password#3"], log
        session.close()
    finally:
        gateway.close()


def test_limiting_gateway_reports_password_rejected() -> None:
    """限次网关的失败文案应指向账号密码，且带认证序列"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalStrictGateway(key.get_base64(), max_tries=2)
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(_row(_private_key_pem(key)), gateway, answers=["wrong-password"])
        message = str(excinfo.value)
        assert "账号密码" in message
        assert "认证序列=" in message
        assert "wrong-password" not in message
    finally:
        gateway.close()

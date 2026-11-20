"""
网关第二因子「authlist 只说 password、实际走键盘交互」回归（v1.10.16 补丁）

## 钉住的问题（用户 2026-11-20 实测，`SSH-2.0-Maverick_SSHD`，`16.162.59.156:6522`）

用户 WinSCP 日志：

```
使用用户名"INGW-KPAYS001"。
使用公钥"imported-openssh-key"进行认证。
Further authentication required      ← 这是 keyboard-interactive 的提示
```

即：**服务器 authlist 只列 `password`，但真正的第二个因子在键盘交互里问密码**。
客户端当时的三处错误行为：

1. 只认 authlist ⇒ 永远只发 `password` 认证，永远试不到键盘交互；
2. 密码被拒后服务器把 authlist 交回 `publickey`，`_continue_auth` 又发一次公钥，
   形成 `publickey→password→publickey→password→publickey` 的死循环（用户看到的认证序列）；
3. 表单把「私钥口令」用 `has_private_key` 推断成「已设置」，用户误以为库里有个
   自己没设过的口令。口令只用于解私钥，**从不进 SSH 认证**。

## 本文件的判据

- 网关「只列 password、第二因子走键盘交互」时，必须能连上（含本地无密码的情形）；
- 密钥被接受后，**不得**再发 publickey（除旧网关 ssh-rsa 补签名那种合法情形）；
- 失败文案要指向「键盘交互里输入的是账号密码」，不再让用户改认证方式。
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
    模拟用户那台网关：authlist 只说 password，第二个因子实际在键盘交互里。

    :param reject_password_method: `password` 认证一律拒绝（网关只在键盘交互里收密码）
    :param extra_publickey_after_reject: 密码被拒后把 authlist 交回 publickey
                                        （复刻死循环触发条件）
    """

    def __init__(
        self,
        authorized_base64: str,
        *,
        reject_password_method: bool = True,
        extra_publickey_after_reject: bool = True,
    ) -> None:
        self.authorized_base64 = authorized_base64
        self.reject_password_method = reject_password_method
        self.extra_publickey_after_reject = extra_publickey_after_reject
        self.key_ok = False
        self.need_password = False
        self.trace: list[str] = []

    def get_allowed_auths(self, _username: str) -> str:
        if not self.key_ok:
            return "publickey,password"
        if self.need_password:
            return "password"
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
        if self.reject_password_method:
            # 关键：方式被允许，但凭据经这个方式永远不通过（第二因子在交互里）
            self.need_password = False
            return paramiko.AUTH_FAILED
        return paramiko.AUTH_SUCCESSFUL if password == _PASSWORD else paramiko.AUTH_FAILED

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


def test_no_local_password_still_connects_via_keyboard_interactive() -> None:
    """
    用户真实形态：**没有填账号密码**，连上后由弹框输入。

    服务器 authlist 只列 `password`，第二个因子实际在键盘交互里 → 必须连上。
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        session = _open(_row(_private_key_pem(key)), gateway, answers=[_PASSWORD])
        assert session._transports[-1].is_authenticated() is True
        trace = gateway.interface.trace
        # 密钥只发一次，随后直接键盘交互（不再有第二次 publickey）
        assert trace.count("publickey") == 1
        assert "keyboard-interactive" in trace
        assert f"kbd(['{_PASSWORD}'])" in trace
        session.close()
    finally:
        gateway.close()


def test_local_password_tries_password_then_keyboard_interactive() -> None:
    """库里存了密码但网关只认键盘交互：password 失败后补键盘交互，仍能连上"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        session = _open(_row(_private_key_pem(key), password=_PASSWORD), gateway)
        assert session._transports[-1].is_authenticated() is True
        trace = gateway.interface.trace
        assert trace.count("publickey") == 1
        assert "keyboard-interactive" in trace
        session.close()
    finally:
        gateway.close()


def test_key_accepted_never_resent_in_dead_loop() -> None:
    """
    密码被拒后服务器把 authlist 交回 `publickey`：密钥**不得**再发。

    v1.10.16 首版在 `known == {publickey}` 时不设 `skip_key`，于是形成
    `publickey→password→publickey→password→publickey` 的死循环。
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        session = _open(_row(_private_key_pem(key), password="wrong"), gateway, answers=["bad"])
        assert session  # 连接失败也无妨，关键是不得重发密钥
    except AuthError:
        pass
    finally:
        trace = gateway.interface.trace
        gateway.close()
    assert trace.count("publickey") == 1, f"密钥被重发了：{trace}"


def test_failure_message_points_at_keyboard_interactive() -> None:
    """补过键盘交互仍失败：文案要说明「弹框里要填账号密码」，且带实际序列、不含凭据"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(_row(_private_key_pem(key)), gateway, answers=["also-wrong"])
        message = str(excinfo.value)
        assert "键盘交互" in message
        assert "认证序列=" in message
        assert "also-wrong" not in message
        assert "BEGIN RSA PRIVATE KEY" not in message
    finally:
        gateway.close()

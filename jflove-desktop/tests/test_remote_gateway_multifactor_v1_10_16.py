"""
网关多因素认证序列回归（v1.10.16）

## 钉住的问题（用户 2026-11-19 在 v1.10.15 上的两种报错）

1. **「服务器已接受密钥，但仍要求再次进行密钥认证（常见于要求两把不同私钥的网关）」**
   根因不在服务器，而在客户端：首轮 publickey 虽然已经被服务器接受（partial +
   「允许：password」），`_can_retry_legacy_rsa` 仍因 `status != "rejected"` 触发了
   `ssh-rsa` 重发，网关把它当成「还差一把密钥」，于是把「请输入账号密码」误报成
   「请给第二把私钥」。
2. **「账号密码没有被服务器接受（服务器允许：password）」**（而 WinSCP 同一账号能连）
   根因：网关的第二个因子走 `keyboard-interactive`；paramiko 在「方式被允许、凭据不对」时
   **不回传 authlist**，`allowed` 变空集，客户端于是放弃键盘交互这条路。

本文件用本地真实 paramiko 服务复刻这两类网关，断言**实际发出的认证序列**，
不靠文案猜测。
"""

from __future__ import annotations

import socket
import threading
from io import StringIO

import paramiko
import pytest

import src.services.ssh_session as ssh_module
from src.services.ssh_session import AuthError, SshSession

_USER = "tester"
_PASSWORD = "S3cret!"


def _private_key_pem(key: paramiko.PKey) -> str:
    """把 RSA 私钥导出成 PEM 文本（表单/服务端都按文本存）"""
    buffer = StringIO()
    key.write_private_key(buffer, password=None)
    return buffer.getvalue()


def _row(
    private_key: str,
    *,
    auth_type: str = "password_publickey",
    password: str = _PASSWORD,
) -> dict:
    return {
        "id": 11,
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


class _Gateway(paramiko.ServerInterface):
    """
    模拟「密钥通过 → 第二个因子」的网关。

    :param second_factor: `password`（网关接受密码认证）/ `keyboard-interactive`
                          （网关只把密码放在键盘交互提示里）
    :param reject_extra_publickey: 密钥已通过后再收到 publickey 时回「还要 publickey」
                                   （复刻用户那台网关把「多余的一次密钥」当成还差一把）
    """

    def __init__(
        self,
        authorized_base64: str,
        *,
        second_factor: str = "password",
        reject_extra_publickey: bool = True,
    ) -> None:
        self.authorized_base64 = authorized_base64
        self.second_factor = second_factor
        self.reject_extra_publickey = reject_extra_publickey
        #: 客户端实际发出的方法序列（断言用）
        self.trace: list[str] = []
        self.publickey_calls = 0
        self.key_accepted = False

    def get_allowed_auths(self, _username: str) -> str:
        if not self.key_accepted:
            return "publickey,password"
        # 密钥已通过：只剩第二个因子
        return "password,keyboard-interactive"

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        self.publickey_calls += 1
        self.trace.append(f"publickey#{self.publickey_calls}")
        if username != _USER or key.get_base64() != self.authorized_base64:
            return paramiko.AUTH_FAILED
        if self.key_accepted and self.reject_extra_publickey:
            # 密钥已经过了还要再来一次 ⇒ 网关认为还缺一把密钥
            self.trace.append("partial(publickey)")
            return paramiko.AUTH_PARTIALLY_SUCCESSFUL
        self.key_accepted = True
        self.trace.append("partial(password)")
        return paramiko.AUTH_PARTIALLY_SUCCESSFUL

    def check_auth_password(self, username: str, password: str) -> int:
        self.trace.append("password")
        if self.second_factor == "password" and username == _USER and password == _PASSWORD:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_interactive(self, _username: str, _submethods: str):
        self.trace.append("keyboard-interactive")
        if self.second_factor != "keyboard-interactive":
            return paramiko.AUTH_FAILED
        return paramiko.InteractiveQuery(
            "Further authentication required", "", ("Password: ", False)
        )

    def check_auth_interactive_response(self, responses) -> int:
        self.trace.append(f"kbd-answer({list(responses)!r})")
        if list(responses) == [_PASSWORD]:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, _chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _LocalGateway:
    """本地真实 SSH 服务（主机密钥固定 ssh-rsa，贴近旧网关）"""

    def __init__(self, authorized_base64: str, **options) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.interface = _Gateway(authorized_base64, **options)
        self.transport: paramiko.Transport | None = None
        self.thread = threading.Thread(target=self._run, name="gateway-ssh", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            client, _addr = self.sock.accept()
        except OSError:
            return
        transport = paramiko.Transport(client)
        self.transport = transport
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
        if self.transport is not None:
            self.transport.close()
        self.thread.join(timeout=2)


def _open(session_row: dict, gateway: _LocalGateway) -> SshSession:
    session_row["port"] = gateway.port
    session = SshSession(session_row, hostkey_ask=lambda _row, _fp: True)
    session.open()
    return session


@pytest.fixture(autouse=True)
def _no_fingerprint_save(monkeypatch):
    """主机指纹落库对断言无意义，且会碰真实数据目录"""
    monkeypatch.setattr(
        ssh_module.remote_service, "save_host_key_fingerprint", lambda *a, **k: None
    )


def test_gateway_partial_publickey_goes_straight_to_password() -> None:
    """
    首轮 publickey 已被接受（服务器允许 password）时，**不能再发一次 publickey**。

    v1.10.15 在这里多发了一次（回退 ssh-rsa），网关于是把「请给密码」变成
    「请给第二把私钥」——正是用户看到的第一条报错。
    """
    user_key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(user_key.get_base64(), second_factor="password")
    try:
        session = _open(_row(_private_key_pem(user_key)), gateway)
        assert session._transports[-1].is_authenticated() is True
        # 关键断言：一次 publickey 之后直接走密码，没有第二次密钥
        assert gateway.interface.trace == ["publickey#1", "partial(password)", "password"]
        session.close()
    finally:
        gateway.close()


def test_gateway_second_factor_only_keyboard_interactive() -> None:
    """
    网关第二个因子只在键盘交互里（密码认证被拒）：客户端必须改用 keyboard-interactive。

    paramiko 在「方式被允许、凭据不对」时不回传 authlist，客户端若只认返回列表
    就会停在「密码没有被接受」——用户看到的第二条报错。
    """
    user_key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(user_key.get_base64(), second_factor="keyboard-interactive")
    try:
        session = _open(_row(_private_key_pem(user_key)), gateway)
        assert session._transports[-1].is_authenticated() is True
        trace = gateway.interface.trace
        assert trace[0] == "publickey#1"
        assert "keyboard-interactive" in trace
        assert f"kbd-answer(['{_PASSWORD}'])" in trace
        session.close()
    finally:
        gateway.close()


def test_gateway_wrong_password_message_points_at_password() -> None:
    """密码确实不对时，文案要指向「密码」，并带上实际认证序列"""
    user_key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(user_key.get_base64(), second_factor="password")
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(
                _row(_private_key_pem(user_key), password="wrong-password"),
                gateway,
            )
        message = str(excinfo.value)
        assert "账号密码没有被服务器接受" in message
        assert "认证序列=" in message
        # 诊断里不得出现凭据
        assert "wrong-password" not in message
        assert "BEGIN RSA PRIVATE KEY" not in message
    finally:
        gateway.close()


def test_gateway_never_asked_key_twice_when_password_present() -> None:
    """有密码时不得因为「密钥已通过」再补一次 publickey（防止回归到 v1.10.15 行为）"""
    user_key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(user_key.get_base64(), second_factor="password")
    try:
        session = _open(_row(_private_key_pem(user_key)), gateway)
        assert gateway.interface.publickey_calls == 1
        session.close()
    finally:
        gateway.close()


def test_legacy_retry_gate_requires_no_next_methods() -> None:
    """门槛单测：服务器给出了非密钥的下一步方式时，绝不回退 ssh-rsa 重发公钥"""
    key = paramiko.RSAKey.generate(2048)

    class _LiveTransport(paramiko.Transport):
        def __init__(self) -> None:  # noqa: D107 - 类型/属性替身
            pass

        def is_active(self) -> bool:  # noqa: D102
            return True

        def is_authenticated(self) -> bool:  # noqa: D102
            return False

    transport = _LiveTransport()
    # 服务器说「还要 password」：密钥这步已过，不再回退
    assert SshSession._can_retry_legacy_rsa(transport, key, "ok", {"password"}) is False
    assert (
        SshSession._can_retry_legacy_rsa(transport, key, "ok", {"keyboard-interactive"})
        is False
    )
    # 服务器没给下一步（裸失败 / 只列 publickey）：值得用 ssh-rsa 再试
    assert SshSession._can_retry_legacy_rsa(transport, key, "failed", set()) is True
    assert SshSession._can_retry_legacy_rsa(transport, key, "failed", {"publickey"}) is True
    # 已被拒的方式不再重试
    assert SshSession._can_retry_legacy_rsa(transport, key, "rejected", set()) is False

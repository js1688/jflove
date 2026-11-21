"""v1.10.21 回归：整次认证只能开一条 `ssh-userauth` 会话。

## 钉住的问题

用户那台 `SSH-2.0-UNLAWFUL ENTRY…` 网关要求「公钥 + 账号密码」两因子认证，
WinSCP / JSch 都能连，JFLove 从 v1.10.13 到 v1.10.20 八个版本都连不上，报：

```text
服务器已接受这把密钥，但账号密码没有被接受（本次已发送：publickey→password；
服务器当前的列表：publickey）
诊断：…；首轮结果=ok（服务器允许：password）；认证序列=publickey→password；
服务器仍要求：publickey
```

## 根因（报文级实测）

`paramiko.Transport.auth_publickey` / `auth_password` **每调用一次就新建 AuthHandler
并重发一次 `SSH_MSG_SERVICE_REQUEST("ssh-userauth")`**。该网关把「新的服务请求」
当作认证进度重置，于是：

```text
publickey  → partial：允许 password   （公钥过了）
重发服务请求 → 网关把「公钥已通过」清掉
password   → 网关认为公钥没过 ⇒ 拒绝，只列 publickey
```

## 判据

1. 一次完整认证中 `SERVICE_REQUEST(ssh-userauth)` 只出现 **1 次**；
2. 「新服务请求即重置」的网关必须能连通；
3. 其他形态（单步密码、单步密钥、键盘交互）不得回归。
"""

from __future__ import annotations

import socket
import threading
from io import StringIO

import paramiko
import pytest
from paramiko.message import Message

import src.services.ssh_session as ssh_module
from src.services.ssh_session import AuthError, SshSession

_USER = "INGW-KPAYS001"
_PASSWORD = "ServerLoginPw!"


def _private_key_pem(key: paramiko.PKey) -> str:
    """把 RSA 私钥导出成 PEM 文本"""
    buffer = StringIO()
    key.write_private_key(buffer, password=None)
    return buffer.getvalue()


def _row(private_key: str, *, auth_type: str, password: str = "") -> dict:
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


class _Gateway(paramiko.ServerInterface):
    """
    复刻用户那台网关。

    :param reset_on_service: 收到新的 `ssh-userauth` 服务请求是否清空认证进度
                             （用户网关的真实行为；置 False 则退化成宽松服务器）
    :param second_factor: `password` / `keyboard-interactive`
    """

    def __init__(
        self,
        authorized_base64: str,
        *,
        reset_on_service: bool = True,
        second_factor: str = "password",
    ) -> None:
        self.authorized_base64 = authorized_base64
        self.reset_on_service = reset_on_service
        self.second_factor = second_factor
        self.key_ok = False
        self.service_requests = 0
        self.events: list[str] = []

    def on_service_request(self) -> None:
        """记录并（按需）重置认证进度"""
        self.service_requests += 1
        if self.reset_on_service and self.key_ok:
            self.events.append("认证进度被新的服务请求重置")
            self.key_ok = False

    def get_allowed_auths(self, _username: str) -> str:
        if not self.key_ok:
            return "publickey,password"
        # 密钥已通过：第二因子方式决定后续列表
        # （第二因子在键盘交互的网关不会把 password 列为可继续的方法）
        return "password" if self.second_factor == "password" else "keyboard-interactive"

    def check_auth_publickey(self, _username: str, key: paramiko.PKey) -> int:
        self.events.append("publickey")
        if key.get_base64() != self.authorized_base64:
            return paramiko.AUTH_FAILED
        self.key_ok = True
        return paramiko.AUTH_PARTIALLY_SUCCESSFUL

    def check_auth_password(self, _username: str, password: str) -> int:
        self.events.append("password")
        if not self.key_ok:
            # 公钥进度丢了：服务器认为还缺公钥，只列 publickey
            return paramiko.AUTH_FAILED
        if self.second_factor != "password":
            return paramiko.AUTH_FAILED
        return paramiko.AUTH_SUCCESSFUL if password == _PASSWORD else paramiko.AUTH_FAILED

    def check_auth_interactive(self, _username: str, _submethods: str):
        self.events.append("keyboard-interactive")
        if self.second_factor != "keyboard-interactive":
            return paramiko.AUTH_FAILED
        return paramiko.InteractiveQuery(
            "Further authentication required", "", ("Password: ", False)
        )

    def check_auth_interactive_response(self, responses) -> int:
        self.events.append(f"kbd-answer({list(responses)!r})")
        if list(responses) == [_PASSWORD]:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

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
        self.interface = _Gateway(authorized_base64, **options)
        #: 客户端发出的报文序列（断言「服务请求只发一次」用）
        self.sent: list[str] = []
        self.thread = threading.Thread(target=self._run, name="gw-v11021", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            client, _addr = self.sock.accept()
        except OSError:
            return
        transport = paramiko.Transport(client)
        transport.add_server_key(paramiko.RSAKey.generate(2048))
        transport._preferred_pubkeys = ("ssh-rsa", "rsa-sha2-512", "rsa-sha2-256")
        paramiko.auth_handler.AuthHandler._jf_gateway = self.interface
        try:
            transport.start_server(server=self.interface)
            transport.join()
        except Exception:  # noqa: BLE001 - 测试线程静默退出
            pass

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
        self.thread.join(timeout=2)


@pytest.fixture(autouse=True)
def _hooks(monkeypatch):
    """把「服务请求」计数钩上，并屏蔽主机指纹落库"""
    monkeypatch.setattr(
        ssh_module.remote_service, "save_host_key_fingerprint", lambda *a, **k: None
    )
    original = paramiko.auth_handler.AuthHandler._parse_service_request

    def patched(self, message: Message) -> None:
        clone = Message(message.asbytes())
        if clone.get_text() == "ssh-userauth":
            gateway = getattr(paramiko.auth_handler.AuthHandler, "_jf_gateway", None)
            if gateway is not None:
                gateway.on_service_request()
        original(self, message)

    monkeypatch.setattr(paramiko.auth_handler.AuthHandler, "_parse_service_request", patched)


def _open(row: dict, gateway: _LocalGateway, *, answer: str = _PASSWORD) -> SshSession:
    row["port"] = gateway.port
    session = SshSession(
        row, hostkey_ask=lambda _row, _fp: True, otp_ask=lambda _prompt: answer
    )
    session.open()
    return session


def test_key_then_password_resets_gateway_now_connects() -> None:
    """
    **本版核心用例**：网关把「新的 ssh-userauth 服务请求」当作认证进度重置。

    修复前（每步重发服务请求）：`publickey→password` 但密码被拒，
    服务器只列 `publickey` —— 与用户报错逐字一致。
    修复后：整次认证只有 **1 次** 服务请求，密码在同一会话内提交，连通。
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64(), reset_on_service=True)
    try:
        session = _open(_row(_private_key_pem(key), auth_type="password_publickey",
                             password=_PASSWORD), gateway)
        assert session._transports[-1].is_authenticated() is True
        # 判据 1：服务请求只能有一次（关键断言，防止回退成「每步重开」）
        assert gateway.interface.service_requests == 1
        # 判据 2：公钥进度不得被重置
        assert "认证进度被新的服务请求重置" not in gateway.interface.events
        assert gateway.interface.events[:2] == ["publickey", "password"]
        session.close()
    finally:
        gateway.close()


def test_key_then_password_with_prompt_connects() -> None:
    """密码留空 ⇒ 弹框输入，也必须在同一条 userauth 会话内完成"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64(), reset_on_service=True)
    try:
        session = _open(
            _row(_private_key_pem(key), auth_type="publickey"), gateway, answer=_PASSWORD
        )
        assert session._transports[-1].is_authenticated() is True
        assert gateway.interface.service_requests == 1
        session.close()
    finally:
        gateway.close()


def test_keyboard_interactive_also_single_service_request() -> None:
    """第二因子只能走键盘交互的网关：同样只能开一次 userauth 会话"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(
        key.get_base64(), reset_on_service=True, second_factor="keyboard-interactive"
    )
    try:
        session = _open(
            _row(_private_key_pem(key), auth_type="publickey"), gateway, answer=_PASSWORD
        )
        assert session._transports[-1].is_authenticated() is True
        assert gateway.interface.service_requests == 1
        session.close()
    finally:
        gateway.close()


def test_wrong_password_message_unchanged() -> None:
    """
    密码确实不对时，文案必须指向账号密码并带认证序列，且不得回显口令原文。

    （网关始终拒绝时客户端会按既有设计重试一次，因此这里同时断言
    「失败的整个过程也只有一次服务请求」。）
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64(), reset_on_service=False)
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(
                _row(_private_key_pem(key), auth_type="password_publickey",
                     password="definitely-wrong"),
                gateway,
                answer="",  # 弹框也不再给新口令 ⇒ 认证必然失败
            )
        message = str(excinfo.value)
        assert "认证序列=" in message
        assert "definitely-wrong" not in message
        # 失败路径同样只能开一次 userauth 会话
        assert gateway.interface.service_requests == 1
    finally:
        gateway.close()


def test_lax_gateway_unchanged() -> None:
    """不重置状态的常规服务器：行为与之前完全一致（只发一次服务请求）"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64(), reset_on_service=False)
    try:
        session = _open(_row(_private_key_pem(key), auth_type="password_publickey",
                             password=_PASSWORD), gateway)
        assert session._transports[-1].is_authenticated() is True
        assert gateway.interface.service_requests == 1
        session.close()
    finally:
        gateway.close()

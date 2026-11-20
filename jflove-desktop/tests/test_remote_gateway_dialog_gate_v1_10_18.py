"""
用户实测反馈回归（v1.10.18）：弹框门槛、误导文案、空输入诊断

## 钉住的问题（用户 2026-11-20 14:52–15:00 的日志）

同一台网关（`SSH-2.0-Maverick_SSHD` 一类，authlist 只列 `password`、第二因子在键盘交互里）
被用户反复试了四次，得到四种结果，看起来「不稳定」，其实是三个独立缺陷：

| # | 用户动作 | 实际发生 | 旧报错说 |
| --- | --- | --- | --- |
| 1 | 切到认证方式「密钥」 | 密钥通过 → 服务器要密码 → **直接放弃，弹框都不出现** | 「请把密码填到「密码」」 |
| 2 | 密码栏留了错密码 | 密钥通过 → 密码被拒 → 补键盘交互（用错密码代答）→ 失败 | **「服务器未接受当前密钥（私钥）」**（误导用户去换私钥） |
| 3 | 弹框弹出但没及时填 | 键盘交互拿到空串 → 认证失败 | 同上，看不出「输入没送达」 |

## 判据

- **认证方式「密钥」也必须弹框**（不能因为没选「密码 + 密钥」就把用户挡在门外）；
- **密钥已被接受时，报错绝不能说「密钥被拒」**；
- **弹框没收到输入时，报错必须说明这一点**（而不是让用户怀疑私钥或密码错）。
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


def _row(private_key: str, *, auth_type: str, password: str = "") -> dict:
    return {
        "id": 31,
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

    :param accept_kbd: 键盘交互里给出的密码是否正确才算通过
    :param authlist_after_kbd: 键盘交互失败后返回的 authlist（复刻「服务器仍要求：publickey」）
    """

    def __init__(
        self,
        authorized_base64: str,
        *,
        accept_kbd: bool = True,
        authlist_after_kbd: str = "publickey",
    ) -> None:
        self.authorized_base64 = authorized_base64
        self.accept_kbd = accept_kbd
        self.authlist_after_kbd = authlist_after_kbd
        self.key_ok = False
        self.kbd_failed = False
        self.trace: list[str] = []

    def get_allowed_auths(self, _username: str) -> str:
        if not self.key_ok:
            return "publickey,password"
        if self.kbd_failed:
            return self.authlist_after_kbd
        return "password"

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        self.trace.append("publickey")
        if username != _USER or key.get_base64() != self.authorized_base64:
            return paramiko.AUTH_FAILED
        self.key_ok = True
        return paramiko.AUTH_PARTIALLY_SUCCESSFUL

    def check_auth_password(self, _username: str, _password: str) -> int:
        # 网关的 password 方式永远不通过（第二因子只在键盘交互里）
        self.trace.append("password")
        return paramiko.AUTH_FAILED

    def check_auth_interactive(self, _username: str, _submethods: str):
        self.trace.append("keyboard-interactive")
        return paramiko.InteractiveQuery(
            "Further authentication required", "", ("Password: ", False)
        )

    def check_auth_interactive_response(self, responses) -> int:
        self.kbd_failed = True
        self.trace.append(f"kbd({list(responses)!r})")
        if self.accept_kbd and list(responses) == [_PASSWORD]:
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
        self.thread = threading.Thread(target=self._run, name="gw-v11018", daemon=True)
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


def _open(row: dict, gateway: _LocalGateway, *, answer: str = "") -> SshSession:
    """打开会话；`answer` 为键盘交互弹层的输入（空串 = 用户没填/超时）"""
    row["port"] = gateway.port
    session = SshSession(
        row,
        hostkey_ask=lambda _row, _fp: True,
        otp_ask=lambda _prompt: answer,
    )
    session.open()
    return session


def test_auth_type_publickey_still_prompts_keyboard_interactive() -> None:
    """
    **认证方式选「密钥」也必须弹框**（用户 14:57 那条「连弹框都不出现」）。

    以前 `_pick_next_method` 的例外分支要求认证方式含密码语义，
    于是选「密钥」时服务器要密码直接放弃 —— 用户只能靠不停改配置碰运气。
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64())
    try:
        session = _open(
            _row(_private_key_pem(key), auth_type="publickey"), gateway, answer=_PASSWORD
        )
        assert session._transports[-1].is_authenticated() is True
        assert "keyboard-interactive" in gateway.interface.trace
        session.close()
    finally:
        gateway.close()


def test_key_accepted_never_claims_key_rejected() -> None:
    """
    密钥已被接受、只是后面的密码没通过时，报错**不得**说「密钥被拒」。

    用户就是被这句话引去反复换私钥（实际私钥完全正确）。
    """
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64(), accept_kbd=False)
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(
                _row(_private_key_pem(key), auth_type="password_publickey", password="wrong"),
                gateway,
            )
        message = str(excinfo.value)
        assert "已接受密钥" in message
        assert "私钥本身没有问题" in message
        # 不得出现「未接受当前密钥」这类把人引向私钥的结论
        assert "服务器未接受当前密钥" not in message
    finally:
        gateway.close()


def test_empty_interactive_answer_is_reported_explicitly() -> None:
    """弹框没收到输入（超时/未点确定）：文案必须说清「没收到输入」"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64(), accept_kbd=False)
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(_row(_private_key_pem(key), auth_type="publickey"), gateway, answer="")
        message = str(excinfo.value)
        assert "没有收到你输入的账号密码" in message
        assert "私钥本身没有问题" in message
    finally:
        gateway.close()


def test_wrong_password_message_does_not_blame_private_key() -> None:
    """密码确实错误时：文案指向账号密码，且带实际认证序列"""
    key = paramiko.RSAKey.generate(2048)
    gateway = _LocalGateway(key.get_base64(), accept_kbd=False)
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(
                _row(_private_key_pem(key), auth_type="password_publickey"),
                gateway,
                answer="definitely-wrong",
            )
        message = str(excinfo.value)
        assert "已接受密钥" in message or "账号密码没有被服务器接受" in message
        assert "认证序列=" in message
        assert "definitely-wrong" not in message
    finally:
        gateway.close()

"""
旧服务器 SSH 算法兼容回归（v1.10.14）

## 钉住的问题

1. `paramiko 5.x` 移除了 `ssh-rsa` 主机密钥 / RSA-SHA1 签名 / SHA-1 KEX，
   连接只提供 `ssh-rsa` 的旧 SFTP 网关时直接报
   `Incompatible ssh peer (no acceptable host key)`。
2. 旧服务器只接受 `ssh-rsa` 公钥签名、且可能不发 `server-sig-algs`；
   `paramiko 4.x` 默认会先试 `rsa-sha2-512`，被拒后需要回退到 `ssh-rsa`。

本文件用本地真实 paramiko 服务覆盖「仅 ssh-rsa」与「现代算法」两条链路，
并断言依赖能力，防止后续误把 paramiko 升回 5.x。
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


def _private_key_pem(key: paramiko.PKey) -> str:
    """把 RSA 私钥导出成 PEM 文本（表单/服务端都按文本存）"""
    buffer = StringIO()
    key.write_private_key(buffer, password=None)
    return buffer.getvalue()


def _row(private_key: str, auth_type: str = "publickey") -> dict:
    return {
        "id": 7,
        "host": "127.0.0.1",
        "port": 0,
        "username": _USER,
        "auth_type": auth_type,
        "password": "",
        "private_key": private_key,
        "passphrase": "",
        "jump_connection_id": 0,
        "host_key_fingerprint": "",
        "mode": "sftp",
    }


class _PublicKeyServer(paramiko.ServerInterface):
    """只接受公钥认证的最小 SSH 服务"""

    def __init__(self, authorized_base64: str) -> None:
        self.authorized_base64 = authorized_base64
        self.seen_key_type = ""

    def get_allowed_auths(self, _username: str) -> str:
        return "publickey"

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        self.seen_key_type = key.get_name()
        if username != _USER:
            return paramiko.AUTH_FAILED
        if key.get_base64() == self.authorized_base64:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, _chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _LocalServer:
    """
    本地真实 SSH 服务。

    :param host_key: 服务端主机密钥（RSA 时可通过 legacy 限制为 ssh-rsa）
    :param legacy: True = 只提供 ssh-rsa 主机密钥
    :param pubkey_algorithms: 覆盖服务端公钥算法列表（None = 跟随 legacy）
    """

    def __init__(
        self,
        host_key: paramiko.PKey,
        *,
        legacy: bool = False,
        pubkey_algorithms: tuple[str, ...] | None = None,
    ) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.host_key = host_key
        self.legacy = legacy
        self.pubkey_algorithms = pubkey_algorithms
        self.interface = _PublicKeyServer("")
        self.transport: paramiko.Transport | None = None
        self.thread = threading.Thread(target=self._run, name="legacy-ssh-server", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            client, _addr = self.sock.accept()
        except OSError:
            return
        self.transport = paramiko.Transport(client)
        self.transport.add_server_key(self.host_key)
        if self.legacy:
            # 模拟旧网关：KEXINIT 只列 ssh-rsa 主机密钥
            self.transport._preferred_keys = ("ssh-rsa",)
        if self.pubkey_algorithms is not None:
            self.transport._preferred_pubkeys = self.pubkey_algorithms
        elif self.legacy:
            self.transport._preferred_pubkeys = ("ssh-rsa",)
        try:
            self.transport.start_server(server=self.interface)
            # start_server 返回后仍要 join，保持服务端 transport 存活（认证失败后可重试）
            self.transport.join()
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


def _open(session_row: dict, server: _LocalServer) -> SshSession:
    session_row["port"] = server.port
    session = SshSession(session_row, hostkey_ask=lambda _row, _fp: True)
    session.open()
    return session


def _no_fingerprint_save(monkeypatch) -> None:
    monkeypatch.setattr(
        ssh_module.remote_service, "save_host_key_fingerprint", lambda *a, **k: None
    )


def test_paramiko_keeps_legacy_rsa_algorithms() -> None:
    """依赖必须保留 ssh-rsa 与 SHA-1 KEX；误升到 5.x 时此用例会失败"""
    assert "ssh-rsa" in paramiko.Transport._preferred_keys
    assert "ssh-rsa" in paramiko.Transport._preferred_pubkeys
    assert "ssh-rsa" in paramiko.RSAKey.HASHES
    assert "diffie-hellman-group14-sha1" in paramiko.Transport._preferred_kex


def test_legacy_ssh_rsa_host_key_and_auth(monkeypatch) -> None:
    """旧服务器：只提供 ssh-rsa 主机密钥 + 只接受 ssh-rsa 公钥认证"""
    _no_fingerprint_save(monkeypatch)
    user_key = paramiko.RSAKey.generate(2048)
    server = _LocalServer(paramiko.RSAKey.generate(2048), legacy=True)
    server.interface.authorized_base64 = user_key.get_base64()
    try:
        session = _open(_row(_private_key_pem(user_key)), server)
        assert session._transports[-1].is_authenticated() is True
        assert session._transports[-1].get_remote_server_key().get_name() == "ssh-rsa"
        assert server.interface.seen_key_type == "ssh-rsa"
        session.close()
    finally:
        server.close()


def test_legacy_rsa_signature_fallback(monkeypatch) -> None:
    """旧服务器只认 ssh-rsa 签名：rsa-sha2 失败后自动回退 ssh-rsa"""
    _no_fingerprint_save(monkeypatch)
    user_key = paramiko.RSAKey.generate(2048)
    # 服务端保留 rsa-sha2 的公钥算法（避免 paramiko 服务端因“算法不支持”直接断开），
    # 但在签名校验阶段拒绝 rsa-sha2，模拟「只认 RSA-SHA1」的旧服务器。
    server = _LocalServer(
        paramiko.RSAKey.generate(2048),
        legacy=True,
        pubkey_algorithms=("ssh-rsa", "rsa-sha2-512", "rsa-sha2-256"),
    )
    server.interface.authorized_base64 = user_key.get_base64()

    original_verify = SshSession._verify_host_key
    original_verify_sig = paramiko.RSAKey.verify_ssh_sig
    fallback_calls: list[str] = []

    def verify_without_sig_algs(self, transport, row):
        original_verify(self, transport, row)
        # 模拟旧服务器：KEX 后没有 EXT_INFO/server-sig-algs
        transport.server_extensions.pop("server-sig-algs", None)

    def legacy_verify_sig(self, data, msg):
        algorithm = paramiko.Message(msg.packet.getvalue()).get_text()
        if algorithm.startswith("rsa-sha2"):
            return False
        return original_verify_sig(self, data, msg)

    original_legacy = SshSession._attempt_legacy_rsa

    def tracked_legacy(self, transport, username, key):
        fallback_calls.append(key.get_name())
        return original_legacy(self, transport, username, key)

    monkeypatch.setattr(SshSession, "_verify_host_key", verify_without_sig_algs)
    monkeypatch.setattr(paramiko.RSAKey, "verify_ssh_sig", legacy_verify_sig)
    monkeypatch.setattr(SshSession, "_attempt_legacy_rsa", tracked_legacy)
    try:
        session = _open(_row(_private_key_pem(user_key)), server)
        assert session._transports[-1].is_authenticated() is True
        assert fallback_calls == ["ssh-rsa"]
        session.close()
    finally:
        server.close()


def test_modern_ecdsa_host_key_still_works(monkeypatch) -> None:
    """现代服务器回归：ECDSA 主机密钥（默认现代算法）不受旧兼容影响"""
    _no_fingerprint_save(monkeypatch)
    user_key = paramiko.RSAKey.generate(2048)
    server = _LocalServer(paramiko.ECDSAKey.generate(), legacy=False)
    server.interface.authorized_base64 = user_key.get_base64()
    try:
        session = _open(_row(_private_key_pem(user_key)), server)
        transport = session._transports[-1]
        assert transport.is_authenticated() is True
        assert transport.get_remote_server_key().get_name().startswith("ecdsa-sha2-")
        session.close()
    finally:
        server.close()


class _PasswordOnlyTransport:
    """假 transport：服务器只允许 publickey，密码方式直接拒绝"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def is_authenticated(self) -> bool:
        return False

    def auth_password(self, _username: str, _password: str) -> list[str]:
        self.calls.append("password")
        raise paramiko.BadAuthenticationType("bad", ["publickey"])


def test_password_auth_on_key_only_server_gives_actionable_hint() -> None:
    """认证方式选错：必须明确告诉用户改「密钥 / 密码 + 密钥」"""
    row = _row("", auth_type="password")
    # v1.10.19：登录密码不再是「弹框索取」路径 —— 这里配一个已存密码，
    # 让流程走到「password 被拒 → 服务器只列 publickey」这一分支
    row["password"] = "any-stored-password"
    session = SshSession(row)
    session_key = session
    transport = _PasswordOnlyTransport()
    with pytest.raises(AuthError) as excinfo:
        session_key._authenticate(transport, session_key.connection)
    message = str(excinfo.value)
    assert "只接受密钥认证" in message
    assert "认证方式改为" in message


class _RejectedKeyTransport:
    """假 transport：公钥方式被拒，且没有任何密码可回退"""

    def __init__(self, key_type: str) -> None:
        self.key_type = key_type

    def is_authenticated(self) -> bool:
        return False

    def auth_publickey(self, _username: str, _key: paramiko.PKey) -> list[str]:
        raise paramiko.AuthenticationException("key rejected")


def test_rejected_ed25519_key_mentions_rsa_compatibility() -> None:
    """密钥被拒时要提示 authorized_keys 与「旧服务器可能只接受 RSA」"""
    user_key = paramiko.RSAKey.generate(2048)
    session = SshSession(_row(_private_key_pem(user_key), auth_type="publickey"))
    transport = _RejectedKeyTransport("ssh-ed25519")
    with pytest.raises(AuthError) as excinfo:
        session._authenticate(transport, session.connection)
    message = str(excinfo.value)
    assert "authorized_keys" in message
    assert "旧服务器可能只支持 ssh-rsa" in message

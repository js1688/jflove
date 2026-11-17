"""
远程连接私钥与认证回归用例（v1.10.11）

覆盖用户实测反馈的两类问题：

1. 私钥字段填的是**文件路径**（WinSCP 习惯）或 PKCS#8 私钥时不能报「无法解析」；
2. 「用户名 + 密码 + 私钥 + 私钥口令」双因素认证需要按服务器要求分两步完成。

不连接真实 SSH 服务器：认证流程用假的 transport 驱动。
"""

from __future__ import annotations

from io import StringIO
from typing import Any

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from paramiko.ssh_exception import (
    AuthenticationException,
    BadAuthenticationType,
    PartialAuthentication,
)

from src.services.ssh_session import AuthError, SshSession, load_private_key


def _private_key_pem(passphrase: str = "") -> str:
    """生成一把 paramiko 可解析的 RSA 私钥文本（RSA PRIVATE KEY）"""
    key = paramiko.RSAKey.generate(bits=1024)
    buffer = StringIO()
    key.write_private_key(buffer, password=passphrase or None)
    return buffer.getvalue()


def _write(tmp_path: Any, name: str, text: str | bytes) -> str:
    path = tmp_path / name
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.fixture()
def rsa_key() -> Any:
    """2048 位 RSA 私钥对象（用于导出各种格式）"""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_load_private_key_from_path(tmp_path) -> None:
    """填文件路径（而不是私钥文本）必须能加载"""
    path = _write(tmp_path, "id_rsa", _private_key_pem())
    key = load_private_key(path)
    assert isinstance(key, paramiko.RSAKey)


def test_load_private_key_from_relative_path(tmp_path, monkeypatch) -> None:
    """相对路径按「当前工作目录」解析（Windows 用户常直接粘相对路径）"""
    (tmp_path / "keys").mkdir()
    _write(tmp_path / "keys", "id_rsa", _private_key_pem())
    monkeypatch.chdir(tmp_path)
    assert isinstance(load_private_key("keys/id_rsa"), paramiko.RSAKey)


def test_load_private_key_encrypted_path(tmp_path) -> None:
    """加密私钥 + 口令：路径与文本两种输入都要成功"""
    text = _private_key_pem("zC5+aL4$lV1=")
    path = _write(tmp_path, "id_rsa_enc", text)
    assert isinstance(load_private_key(path, "zC5+aL4$lV1="), paramiko.RSAKey)
    assert isinstance(load_private_key(text, "zC5+aL4$lV1="), paramiko.RSAKey)


def test_load_private_key_pkcs8(tmp_path, rsa_key) -> None:
    """OpenSSL 3 常见的 PKCS#8（BEGIN PRIVATE KEY）要能解析"""
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    assert "BEGIN PRIVATE KEY" in pem
    assert isinstance(load_private_key(pem), paramiko.RSAKey)
    assert isinstance(load_private_key(_write(tmp_path, "pkcs8.pem", pem)), paramiko.RSAKey)


def test_load_private_key_pkcs8_encrypted(tmp_path, rsa_key) -> None:
    """PKCS#8 加密私钥：正确口令通过，错误口令给中文提示"""
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"zC5+aL4$lV1="),
    ).decode()
    path = _write(tmp_path, "pkcs8_enc.pem", pem)
    assert isinstance(load_private_key(path, "zC5+aL4$lV1="), paramiko.RSAKey)
    with pytest.raises(AuthError, match="私钥口令错误"):
        load_private_key(path, "wrong")


def test_load_private_key_ed25519_pkcs8() -> None:
    """Ed25519 的 PKCS#8 也要能解析"""
    pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    assert isinstance(load_private_key(pem), paramiko.Ed25519Key)


def test_load_private_key_missing_path(tmp_path) -> None:
    """路径不存在时提示「文件不存在」并列出已尝试的位置"""
    missing = str(tmp_path / "not-exists" / "id_rsa")
    with pytest.raises(AuthError, match="私钥文件不存在"):
        load_private_key(missing)


def test_load_private_key_ppk_rejected() -> None:
    """PuTTY .ppk 给出转换指引，而不是 paramiko 英文原文"""
    with pytest.raises(AuthError, match="PuTTY"):
        load_private_key("PuTTY-User-Key-File-3: ssh-rsa\nEncryption: none\n")


def test_load_private_key_encrypted_without_passphrase(tmp_path) -> None:
    """加密私钥没填口令 → 明确提示填「私钥口令」"""
    path = _write(tmp_path, "id_rsa_enc", _private_key_pem("pw"))
    with pytest.raises(AuthError, match="私钥口令"):
        load_private_key(path)


def test_load_private_key_wrong_passphrase(tmp_path) -> None:
    """口令错误 → 提示口令错误或文件损坏"""
    path = _write(tmp_path, "id_rsa_enc", _private_key_pem("right"))
    with pytest.raises(AuthError, match="私钥口令错误"):
        load_private_key(path, "wrong")


class _FakeTransport:
    """只实现认证流程用到的接口，按脚本逐步返回结果"""

    def __init__(self, steps: list[Any]) -> None:
        self.steps = list(steps)
        self.calls: list[str] = []
        self.authenticated = False

    def is_authenticated(self) -> bool:
        return self.authenticated

    def auth_publickey(self, _username: str, _key: paramiko.PKey) -> None:
        self.calls.append("publickey")
        self._consume()

    def auth_password(self, _username: str, _password: str) -> None:
        self.calls.append("password")
        self._consume()

    def _consume(self) -> None:
        step = self.steps.pop(0) if self.steps else "ok"
        if step == "ok":
            self.authenticated = True
            return
        raise step


def _row(auth_type: str, password: str = "", private_key: str = "") -> dict:
    return {
        "auth_type": auth_type,
        "username": "APGW-KPAYG001",
        "password": password,
        "private_key": private_key or _private_key_pem(),
        "passphrase": "",
    }


def test_authenticate_key_then_password() -> None:
    """OpenSSH 的 publickey,password 顺序：密钥 partial → 再补密码"""
    session = SshSession(_row("password_publickey", password="login-pw"))
    transport = _FakeTransport([PartialAuthentication(["password"]), "ok"])
    session._authenticate(transport, session.connection)
    assert transport.calls == ["publickey", "password"]
    assert transport.is_authenticated()


def test_authenticate_password_then_key() -> None:
    """password,publickey 顺序：密钥不被允许 → 密码 partial → 再补密钥"""
    session = SshSession(_row("password_publickey", password="login-pw"))
    transport = _FakeTransport(
        [
            BadAuthenticationType("bad", ["password"]),
            PartialAuthentication(["publickey"]),
            "ok",
        ]
    )
    session._authenticate(transport, session.connection)
    assert transport.calls == ["publickey", "password", "publickey"]
    assert transport.is_authenticated()


def test_authenticate_key_only_server_ignores_password() -> None:
    """服务器只认密钥：即使配置了密码也能一次密钥通过"""
    session = SshSession(_row("password_publickey", password="login-pw"))
    transport = _FakeTransport(["ok"])
    session._authenticate(transport, session.connection)
    assert transport.calls == ["publickey"]


def test_authenticate_key_only_with_password_field() -> None:
    """publickey 模式里存了密码：服务器要求双因素时也能自动补密码（兼容旧配置）"""
    session = SshSession(_row("publickey", password="login-pw"))
    transport = _FakeTransport([PartialAuthentication(["password"]), "ok"])
    session._authenticate(transport, session.connection)
    assert transport.calls == ["publickey", "password"]


def test_authenticate_key_requires_password_hint() -> None:
    """只配密钥但服务器还要密码：提示切换「密码 + 密钥」"""
    session = SshSession(_row("publickey"))
    transport = _FakeTransport([PartialAuthentication(["password"])])
    with pytest.raises(AuthError, match="密码"):
        session._authenticate(transport, session.connection)


def test_authenticate_key_failure_falls_back_to_password() -> None:
    """服务器同时允许密码/密钥、但未给方法列表：密钥失败时再试密码（更宽容）"""
    session = SshSession(_row("password_publickey", password="login-pw"))
    transport = _FakeTransport([AuthenticationException("Authentication failed."), "ok"])
    session._authenticate(transport, session.connection)
    assert transport.calls == ["publickey", "password"]


def test_authenticate_key_only_server_skips_password() -> None:
    """服务器明确只认密钥：密钥失败后不再无谓地试密码"""
    session = SshSession(_row("password_publickey", password="login-pw"))
    transport = _FakeTransport([BadAuthenticationType("bad", ["publickey"])])
    with pytest.raises(AuthError, match="密钥"):
        session._authenticate(transport, session.connection)
    assert transport.calls == ["publickey"]


@pytest.mark.parametrize(
    "transform",
    [
        lambda text: "\ufeff" + text,                                  # 记事本 UTF-8 BOM
        lambda text: "\n".join("    " + line for line in text.splitlines()),  # 整体缩进
        lambda text: text.replace("\n", ""),                          # 被折叠成一行
        lambda text: text.replace("\n", "\\n"),                       # 字面量 \n
        lambda text: text.replace("\n", "\r\n"),                      # Windows 换行
        lambda text: "\n\n  " + text,                                # 前后空白
    ],
)
def test_paste_shapes_are_normalized(transform) -> None:
    """用户从记事本/配置里粘出来的各种形态都要能解析（历史报错正是这一层）"""
    text = transform(_private_key_pem())
    assert isinstance(load_private_key(text), paramiko.RSAKey)


def test_encrypted_pkcs8_without_passphrase_hint() -> None:
    """PKCS#8 加密私钥没填口令 → 明确提示补口令"""
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_module

    key = rsa_module.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"pw"),
    ).decode()
    with pytest.raises(AuthError, match="私钥口令"):
        load_private_key(pem)


# ── 桥：选文件 → 内容存服务端（不保存路径） ──────────────


def test_bridge_create_uploads_file_content(monkeypatch) -> None:
    """选文件后创建连接：发给服务端的是**内容**，token 用完即清"""
    from src.bridge import remote as remote_bridge

    captured: dict = {}
    monkeypatch.setattr(
        remote_bridge.remote_service,
        "create_connection",
        lambda payload: captured.update(payload) or 7,
    )
    content = _private_key_pem()
    remote_bridge._pending_private_keys["tok"] = {"content": content, "name": "id_rsa"}

    result = remote_bridge._h_connections_create(
        {"name": "x", "host": "h", "private_key_file_token": "tok", "passphrase": "pw"}
    )

    assert result == {"id": 7}
    assert captured["private_key"] == content
    assert captured["passphrase"] == "pw"
    assert "private_key_file_token" not in captured
    assert "tok" not in remote_bridge._pending_private_keys


def test_bridge_update_uploads_file_content(monkeypatch) -> None:
    """编辑连接同样按 token 换成内容"""
    from src.bridge import remote as remote_bridge

    captured: dict = {}
    monkeypatch.setattr(
        remote_bridge.remote_service,
        "update_connection",
        lambda connection_id, payload: captured.update(payload) or True,
    )
    content = _private_key_pem()
    remote_bridge._pending_private_keys["tok2"] = {"content": content, "name": "id_rsa"}

    assert remote_bridge._h_connections_update({"id": 3, "private_key_file_token": "tok2"}) == {
        "ok": True
    }
    assert captured["private_key"] == content
    assert "tok2" not in remote_bridge._pending_private_keys


def test_bridge_rejects_expired_token() -> None:
    """token 失效（重启 / 已保存）要提示重新选择，而不是静默保存空私钥"""
    from src.bridge import remote as remote_bridge

    with pytest.raises(RuntimeError, match="失效"):
        remote_bridge._h_connections_create({"private_key_file_token": "missing"})


def test_bridge_rejects_local_path_in_field() -> None:
    """连接配置只存内容；字段里塞本地路径必须被拦下"""
    from src.bridge import remote as remote_bridge

    with pytest.raises(RuntimeError, match="不保存本地路径"):
        remote_bridge._h_validate_private_key({"private_key": "/tmp/keys/id_rsa"})


def test_bridge_validate_private_key() -> None:
    """保存前校验：合法内容给出指纹；错误口令给出可读原因"""
    from src.bridge import remote as remote_bridge

    content = _private_key_pem()
    result = remote_bridge._h_validate_private_key({"private_key": content})
    assert result["ok"] is True
    assert result["fingerprint"].startswith("SHA256:")

    remote_bridge._pending_private_keys["tok3"] = {"content": content, "name": "id_rsa"}
    result = remote_bridge._h_validate_private_key({"private_key_file_token": "tok3"})
    assert result["fingerprint"].startswith("SHA256:")

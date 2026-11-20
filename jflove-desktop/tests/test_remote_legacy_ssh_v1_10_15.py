"""
旧服务器 SSH 认证残余失败回归（v1.10.15）

## 钉住的问题

v1.10.14 恢复了 `paramiko==4.0.0` + `ssh-rsa`(RSA-SHA1) 回退，但仍有一台旧网关报
`服务器未接受当前密钥（私钥；服务器允许：publickey）`。本文件钉住三种残余形态：

1. 网关的 `server-sig-algs` 与实际能力不符（写着 `rsa-sha2-*`，实际只认 `ssh-rsa`）：
   v1.10.14 以「扩展里必须出现 ssh-rsa」为回退门槛，恰好被挡住；
   回退时还必须临时摘掉该扩展，否则 paramiko 会抛「Unable to agree on a pubkey algorithm」。
2. 网关把「签名算法不匹配」回成**部分认证**（partial=True，仍只要 publickey）：
   回退条件必须放宽到「首轮未认证完成」，否则不会换 `ssh-rsa`。
3. 认证失败信息必须带**不含凭据**的诊断行（远端版本 / 主机密钥 / 签名算法 / 回退结果），
   否则下一轮只能靠猜。

测试用本地真实 paramiko 服务，服务端签名校验被 monkeypatch 成「拒绝 rsa-sha2」，
模拟只认 RSA-SHA1 的旧网关。
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


class _KeyServer(paramiko.ServerInterface):
    """只接受公钥认证；可切换成「永远部分成功」（模拟要求二次密钥的网关）"""

    def __init__(
        self,
        authorized_base64: str,
        *,
        partial: bool = False,
        partial_times: int = 0,
    ) -> None:
        self.authorized_base64 = authorized_base64
        self.partial = partial
        #: 前 N 次「密钥已接受」回部分认证，第 N+1 次才真正放行（模拟 Maverick 一类服务端）
        self.partial_times = partial_times
        self.publickey_calls = 0
        self.seen_key_types: list[str] = []

    def get_allowed_auths(self, _username: str) -> str:
        return "publickey"

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        self.seen_key_types.append(key.get_name())
        if username != _USER or key.get_base64() != self.authorized_base64:
            return paramiko.AUTH_FAILED
        self.publickey_calls += 1
        if self.partial or self.publickey_calls <= self.partial_times:
            return paramiko.AUTH_PARTIALLY_SUCCESSFUL
        return paramiko.AUTH_SUCCESSFUL

    def check_channel_request(self, kind: str, _chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _LocalServer:
    """本地真实 SSH 服务（主机密钥固定为 ssh-rsa，贴近旧网关）"""

    def __init__(self, *, partial: bool = False, partial_times: int = 0) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.host_key = paramiko.RSAKey.generate(2048)
        self.interface = _KeyServer("", partial=partial, partial_times=partial_times)
        self.transport: paramiko.Transport | None = None
        self.thread = threading.Thread(target=self._run, name="legacy-ssh-server", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            client, _addr = self.sock.accept()
        except OSError:
            return
        transport = paramiko.Transport(client)
        self.transport = transport
        transport.add_server_key(self.host_key)
        # 保留 ssh-rsa + rsa-sha2 的服务端算法列表（客户端首轮会选 rsa-sha2）
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


def _open(session_row: dict, server: _LocalServer) -> SshSession:
    session_row["port"] = server.port
    session = SshSession(session_row, hostkey_ask=lambda _row, _fp: True)
    session.open()
    return session


def _no_fingerprint_save(monkeypatch) -> None:
    monkeypatch.setattr(
        ssh_module.remote_service, "save_host_key_fingerprint", lambda *a, **k: None
    )


def _reject_rsa_sha2_signature(monkeypatch, user_key: paramiko.RSAKey):
    """
    服务端只认 RSA-SHA1：签名校验阶段拒绝**用户公钥**的 rsa-sha2* 签名。

    必须按公钥过滤——主机密钥签名也走 `RSAKey.verify_ssh_sig`，
    一刀切会让 KEX 阶段的主机签名校验先失败。
    """
    original = paramiko.RSAKey.verify_ssh_sig
    user_base64 = user_key.get_base64()

    def legacy_verify(self, data, msg):
        algorithm = paramiko.Message(msg.packet.getvalue()).get_text()
        if algorithm.startswith("rsa-sha2") and self.get_base64() == user_base64:
            return False
        return original(self, data, msg)

    monkeypatch.setattr(paramiko.RSAKey, "verify_ssh_sig", legacy_verify)
    return original


def test_sig_algs_without_ssh_rsa_still_falls_back(monkeypatch) -> None:
    """server-sig-algs 与实际能力不符：扩展里没有 ssh-rsa，也必须能回退成功"""
    _no_fingerprint_save(monkeypatch)
    user_key = paramiko.RSAKey.generate(2048)
    _reject_rsa_sha2_signature(monkeypatch, user_key)
    server = _LocalServer()
    server.interface.authorized_base64 = user_key.get_base64()

    original_verify = SshSession._verify_host_key

    def lying_server_extensions(self, transport, row):
        original_verify(self, transport, row)
        # 旧网关谎报扩展：只写 rsa-sha2，实际只认 ssh-rsa
        transport.server_extensions["server-sig-algs"] = "rsa-sha2-512,rsa-sha2-256"

    monkeypatch.setattr(SshSession, "_verify_host_key", lying_server_extensions)
    try:
        session = _open(_row(_private_key_pem(user_key)), server)
        assert session._transports[-1].is_authenticated() is True
        assert server.interface.seen_key_types  # 服务端确实收到了公钥认证
        session.close()
    finally:
        server.close()


def test_partial_publickey_reports_second_key_requirement(monkeypatch) -> None:
    """网关回「部分认证 + 仍要 publickey」：换 ssh-rsa 重试一次后给出二次密钥文案"""
    _no_fingerprint_save(monkeypatch)
    user_key = paramiko.RSAKey.generate(2048)
    server = _LocalServer(partial=True)
    server.interface.authorized_base64 = user_key.get_base64()
    legacy_calls: list[str] = []
    original_legacy = SshSession._attempt_legacy_rsa

    def tracked_legacy(self, transport, username, key):
        legacy_calls.append(key.get_name())
        return original_legacy(self, transport, username, key)

    monkeypatch.setattr(SshSession, "_attempt_legacy_rsa", tracked_legacy)
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(_row(_private_key_pem(user_key)), server)
        message = str(excinfo.value)
        assert "仍要求继续用密钥认证" in message
        assert "两把不同私钥" in message
        # 部分认证（status=ok）也必须触发一次旧式 ssh-rsa 重试
        assert legacy_calls == ["ssh-rsa"]
    finally:
        server.close()


def test_partial_publickey_then_ssh_rsa_completes(monkeypatch) -> None:
    """
    旧网关真实形态（用户 2026-11-19 日志：`SSH-2.0-Maverick_SSHD`）：
    第 1 次签名算法不匹配 → 失败；第 2 次 ssh-rsa 验签成功但服务器回「部分认证，继续 publickey」；
    第 3 次必须**继续用 ssh-rsa** 才能完成 —— v1.10.14 在回退后立刻恢复 rsa-sha2，
    且 publickey 只允许试 2 次，正好卡死在这一步。
    """
    _no_fingerprint_save(monkeypatch)
    user_key = paramiko.RSAKey.generate(2048)
    used_algos: list[str] = []
    original_verify = paramiko.RSAKey.verify_ssh_sig
    user_base64 = user_key.get_base64()

    def record_and_reject_rsa_sha2(self, data, msg):
        algorithm = paramiko.Message(msg.packet.getvalue()).get_text()
        if self.get_base64() == user_base64:
            used_algos.append(algorithm)
            if algorithm.startswith("rsa-sha2"):
                return False
        return original_verify(self, data, msg)

    monkeypatch.setattr(paramiko.RSAKey, "verify_ssh_sig", record_and_reject_rsa_sha2)
    server = _LocalServer(partial_times=2)
    server.interface.authorized_base64 = user_key.get_base64()
    try:
        session = _open(_row(_private_key_pem(user_key)), server)
        assert session._transports[-1].is_authenticated() is True
        # 第 1 次 rsa-sha2 被拒；第 2、3 次都用 ssh-rsa
        assert used_algos == ["rsa-sha2-512", "ssh-rsa", "ssh-rsa"]
        assert server.interface.seen_key_types == ["ssh-rsa"] * 3
        session.close()
    finally:
        server.close()


def test_auth_failure_carries_diagnosis(monkeypatch) -> None:
    """失败信息必须带诊断行（远端版本 / 签名算法），且不含私钥内容"""
    _no_fingerprint_save(monkeypatch)
    user_key = paramiko.RSAKey.generate(2048)
    server = _LocalServer()
    # 服务器不认识这把公钥 ⇒ 认证被拒（不需要签名校验 monkeypatch）
    server.interface.authorized_base64 = paramiko.RSAKey.generate(2048).get_base64()
    try:
        with pytest.raises(AuthError) as excinfo:
            _open(_row(_private_key_pem(user_key)), server)
        message = str(excinfo.value)
        assert "诊断：" in message
        assert "远端=" in message
        assert "服务器签名算法=" in message
        # 诊断不含凭据：私钥 PEM 正文 / 口令都不应出现
        assert "BEGIN RSA PRIVATE KEY" not in message
    finally:
        server.close()


def test_legacy_retry_skipped_when_transport_dead(monkeypatch) -> None:
    """传输层已断开时不再对 ssh-rsa 回退（回退必然失败，只多一次无谓等待）"""

    class _DeadTransport(paramiko.Transport):
        def __init__(self) -> None:  # noqa: D107 - 只做类型/属性替身
            pass

        def is_active(self) -> bool:  # noqa: D102
            return False

        def is_authenticated(self) -> bool:  # noqa: D102
            return False

    key = paramiko.RSAKey.generate(2048)
    assert SshSession._can_retry_legacy_rsa(_DeadTransport(), key, "failed") is False
    assert SshSession._can_retry_legacy_rsa(_DeadTransport(), key, "rejected") is False

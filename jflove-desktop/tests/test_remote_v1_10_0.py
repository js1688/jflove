"""
远程 SSH / 本机终端 Python 层单测（v1.10.0）

不依赖真实 SSH 服务器，重点钉住：
1. 主机指纹格式与私钥解析失败路径；
2. 跳板链的环检测；
3. 本机终端配置的本地 CRUD 与「删组不删条目」。
"""

from __future__ import annotations

import paramiko
import pytest

from src.services import local_terminal_service
from src.services import ssh_session as ssh_session_module
from src.services.ssh_session import (
    AuthError,
    JumpError,
    SshSession,
    fetch_fingerprint,
    load_private_key,
)


def test_fingerprint_format() -> None:
    """指纹是 OpenSSH 风格 `SHA256:<base64 无填充>`"""
    key = paramiko.RSAKey.generate(bits=1024)
    fingerprint = fetch_fingerprint(key)
    assert fingerprint.startswith("SHA256:")
    assert "=" not in fingerprint


def test_load_private_key_rejects_garbage() -> None:
    """无法解析的私钥要给出可读错误，而不是抛 paramiko 原始异常"""
    with pytest.raises(AuthError):
        load_private_key("not a key")


def test_detect_shells_not_empty() -> None:
    """当前系统至少探测到一个可用 shell"""
    shells = local_terminal_service.detect_shells()
    assert shells
    assert any(shell["available"] for shell in shells)


@pytest.fixture()
def local_db(tmp_path, monkeypatch):
    """把本地库重定向到临时目录"""
    monkeypatch.setattr(local_terminal_service, "_DB_PATH", str(tmp_path / "local.db"))
    return tmp_path


def test_local_group_and_profile_crud(local_db) -> None:
    """分组与条目的增删改查；删除分组后条目落回未分组"""
    group_id = local_terminal_service.create_group("开发")
    assert group_id > 0
    assert local_terminal_service.list_groups()[0]["name"] == "开发"

    profile_id = local_terminal_service.create_profile(
        {
            "group_id": group_id,
            "name": "WSL",
            "shell_kind": "wsl",
            "shell_path": "wsl.exe",
            "cwd": "/home",
            "init_command": "cd /opt && ls",
        }
    )
    assert profile_id > 0
    profile = local_terminal_service.get_profile(profile_id)
    assert profile is not None
    assert profile["init_command"] == "cd /opt && ls"

    local_terminal_service.update_group(group_id, name="开发环境")
    assert local_terminal_service.list_groups()[0]["name"] == "开发环境"

    local_terminal_service.delete_group(group_id)
    profile = local_terminal_service.get_profile(profile_id)
    assert profile is not None
    assert profile["group_id"] == 0

    local_terminal_service.update_profile(profile_id, {"name": "WSL2", "shell_path": "wsl.exe"})
    assert local_terminal_service.get_profile(profile_id)["name"] == "WSL2"

    local_terminal_service.delete_profile(profile_id)
    assert local_terminal_service.get_profile(profile_id) is None


def test_resolve_chain_detects_cycle(monkeypatch) -> None:
    """A→B→A 的跳板链必须被客户端也拦下（不能只依赖服务端）"""
    rows = {
        1: {"id": 1, "host": "a", "port": 22, "jump_connection_id": 2},
        2: {"id": 2, "host": "b", "port": 22, "jump_connection_id": 1},
    }

    monkeypatch.setattr(
        ssh_session_module.remote_service,
        "get_connection",
        lambda conn_id: rows.get(conn_id),
    )
    session = SshSession(rows[1])
    with pytest.raises(JumpError, match="循环"):
        session._resolve_chain()


def test_resolve_chain_orders_from_outermost(monkeypatch) -> None:
    """链路顺序必须是「最外层跳板 → 目标」"""
    rows = {
        1: {"id": 1, "host": "target", "port": 22, "jump_connection_id": 2},
        2: {"id": 2, "host": "jump", "port": 22, "jump_connection_id": 0},
    }

    monkeypatch.setattr(
        ssh_session_module.remote_service,
        "get_connection",
        lambda conn_id: rows.get(conn_id),
    )
    session = SshSession(rows[1])
    chain = session._resolve_chain()
    assert [row["id"] for row in chain] == [2, 1]

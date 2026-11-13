#!/usr/bin/env python3
"""
远程 SSH 真实联调（v1.10.0 验证入口）

用**本机 sshd** 验证桌面端 Python 会话层：直连 + 一级跳板 + 启动命令。
不依赖 JFLove 服务端（`save_host_key_fingerprint` / `get_connection` 在脚本内打桩）。

前置（脚本不自动改系统配置，需手动准备一次）：
    ssh-keygen -t ed25519 -f /tmp/jf-ssh-test/id_ed25519 -N ''
    cat /tmp/jf-ssh-test/id_ed25519.pub >> ~/.ssh/authorized_keys

用法：
    jflove-desktop/venv-linux/bin/python scripts/verify/ssh_smoke.py

判据：打印 `{"status":"ok", ...}` 且退出码 0。
"""

from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "jflove-desktop"))

from src.services import ssh_session as ssh_module  # noqa: E402

KEY_PATH = "/tmp/jf-ssh-test/id_ed25519"
HOST = "127.0.0.1"
PORT = 22
USER = os.environ.get("USER") or "tanjun"


def _wait_output(chunks: list[bytes], needle: str, timeout: float) -> bool:
    """轮询等待输出里出现指定文本"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if needle in b"".join(chunks).decode("utf-8", "replace"):
            return True
        time.sleep(0.1)
    return False


def _make_row(
    connection_id: int,
    jump_id: int = 0,
    init_command: str = "",
    port: int = PORT,
) -> dict:
    with open(KEY_PATH, encoding="utf-8") as fh:
        pem = fh.read()
    return {
        "id": connection_id,
        "host": HOST,
        "port": port,
        "username": USER,
        "auth_type": "publickey",
        "password": "",
        "private_key": pem,
        "passphrase": "",
        "jump_connection_id": jump_id,
        "terminal_type": "xterm-256color",
        "charset": "UTF-8",
        "init_command": init_command,
        "host_key_fingerprint": "",
    }


def main() -> int:
    if not os.path.exists(KEY_PATH):
        print(json.dumps({"status": "skip", "reason": "缺少临时密钥，先跑 ssh-keygen"}))
        return 2

    # 打桩：不连 JFLove 服务端（本脚本只验 SSH 会话层）
    ssh_module.remote_service.save_host_key_fingerprint = lambda *_a, **_k: None

    rows = {
        1: _make_row(1, init_command="echo DIRECT_CMD_OK"),
        2: _make_row(2, jump_id=1, init_command="echo JUMP_CMD_OK"),
        # 两级跳板：第一跳连 2222（第二个 sshd 实例），第二跳连 22，目标再回 2222
        3: _make_row(3, jump_id=2, init_command="echo LEVEL2_CMD_OK", port=2222),
        4: _make_row(4, jump_id=3, init_command="echo LEVEL2_CMD_OK", port=2222),
    }
    ssh_module.remote_service.get_connection = lambda conn_id: rows.get(conn_id)

    results: dict[str, object] = {}

    # ── 1) 直连 + 启动命令 ──
    direct_chunks: list[bytes] = []
    direct = ssh_module.SshSession(
        rows[1],
        cols=100,
        rows=30,
        on_output=direct_chunks.append,
        hostkey_ask=lambda _row, _fp: True,
    )
    direct.open()
    results["direct_startup_command"] = _wait_output(direct_chunks, "DIRECT_CMD_OK", 5)
    direct.write(b"echo SHELL_OK\n")
    results["direct_shell_io"] = _wait_output(direct_chunks, "SHELL_OK", 5)
    direct.close()
    time.sleep(0.3)

    # ── 2) 一级跳板：本机 → 本机 → 本机 ──
    jump_chunks: list[bytes] = []
    jumped = ssh_module.SshSession(
        rows[2],
        cols=100,
        rows=30,
        on_output=jump_chunks.append,
        hostkey_ask=lambda _row, _fp: True,
    )
    jumped.open()
    results["jump_startup_command"] = _wait_output(jump_chunks, "JUMP_CMD_OK", 8)
    jumped.write(b"echo JUMP_OK\n")
    results["jump_shell_io"] = _wait_output(jump_chunks, "JUMP_OK", 5)
    jumped.close()
    time.sleep(0.3)

    # ── 3) 两级跳板：2222 → 22 → 2222（跨两个 sshd 实例）──
    level2_chunks: list[bytes] = []
    level2 = ssh_module.SshSession(
        rows[4],
        cols=100,
        rows=30,
        on_output=level2_chunks.append,
        hostkey_ask=lambda _row, _fp: True,
    )
    level2.open()
    results["level2_startup_command"] = _wait_output(level2_chunks, "LEVEL2_CMD_OK", 10)
    level2.write(b"echo LEVEL2_IO_OK\n")
    results["level2_shell_io"] = _wait_output(level2_chunks, "LEVEL2_IO_OK", 5)
    level2.close()

    ok = all(bool(value) for value in results.values())
    print(json.dumps({"status": "ok" if ok else "fail", **results}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

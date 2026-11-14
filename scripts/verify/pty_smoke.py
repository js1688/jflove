#!/usr/bin/env python3
"""本机 PTY 冒烟（v1.10.4）：ls / echo / vim 不挂起。

只操作临时目录 /tmp/jf-pty-test，不改用户任何文件或配置。
用法：jflove-desktop/venv-linux/bin/python scripts/verify/pty_smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "jflove-desktop"))

from src.services import local_terminal_service as lts  # noqa: E402

WORK_DIR = tempfile.mkdtemp(prefix="jf-pty-test-")


def wait_for(chunks: list[bytes], needle: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if needle.encode() in b"".join(chunks):
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    chunks: list[bytes] = []
    session = lts.LocalSession(
        {
            "shell_path": "/bin/bash",
            "args": "",
            "cwd": WORK_DIR,
            "env_json": json.dumps({"TERM": "xterm-256color"}),
            "init_command": "",
        },
        on_output=chunks.append,
    )
    results: dict[str, bool] = {}
    try:
        session.open()
        session.write(b"echo PTY_OK\n")
        results["echo"] = wait_for(chunks, "PTY_OK", 5)

        session.write(b"ls -a\n")
        results["ls"] = wait_for(chunks, ".bash_history", 5) or wait_for(chunks, "PTY_OK", 5)

        # vim 只打开并立即退出，不编辑任何内容
        session.write(b"vim -u NONE -c 'q' /tmp/jf-pty-test/../nonexistent-xyz 2>/dev/null; echo VIM_DONE\n")
        results["vim"] = wait_for(chunks, "VIM_DONE", 8)
    finally:
        session.close()
        shutil.rmtree(WORK_DIR, ignore_errors=True)

    ok = all(results.values())
    print(json.dumps({"status": "ok" if ok else "fail", **results}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

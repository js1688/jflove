"""
本机终端（v1.10.0）

- 配置与分组只存**本地** SQLite（`LOCAL_STORAGE_DIR/jflove-local.db`），不进服务端。
- POSIX 用 `pty` + `subprocess`；Windows 用 `pywinpty`（ConPTY）。
- 与远程终端共用同一套输出/输入接口，由 `terminal_host` 统一对外暴露。
"""

from __future__ import annotations

import json
import os
import platform
import pty
import shutil
import sqlite3
import struct
import subprocess
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from src.config.settings import LOCAL_STORAGE_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 本地库路径（与 mount_index.db 同目录）
_DB_PATH = os.path.join(LOCAL_STORAGE_DIR, "jflove-local.db")

#: 输出块大小
READ_CHUNK = 4096

_CREATE_GROUPS = """
CREATE TABLE IF NOT EXISTS local_terminal_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
)
"""

_CREATE_PROFILES = """
CREATE TABLE IF NOT EXISTS local_terminal_profiles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      INTEGER NOT NULL DEFAULT 0,
    name          TEXT    NOT NULL,
    shell_kind    TEXT    NOT NULL,
    shell_path    TEXT    NOT NULL DEFAULT '',
    args          TEXT    NOT NULL DEFAULT '',
    cwd           TEXT    NOT NULL DEFAULT '',
    env_json      TEXT    NOT NULL DEFAULT '{}',
    init_command  TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
)
"""


def _now() -> str:
    """当前 UTC 时间的 ISO 字符串"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """本地库连接（建表 + 提交/回滚 + 关闭）"""
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_GROUPS)
    conn.execute(_CREATE_PROFILES)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


# ── Shell 探测 ───────────────────────────────────────


def detect_shells() -> list[dict[str, Any]]:
    """
    探测当前系统可用的 shell。

    :returns: `[{kind, label, path, args, available}]`
    """
    system = platform.system()
    result: list[dict[str, Any]] = []
    if system == "Windows":
        candidates = [
            ("powershell", "PowerShell 7", "pwsh.exe"),
            ("powershell", "Windows PowerShell", "powershell.exe"),
            ("cmd", "命令提示符", "cmd.exe"),
        ]
        for kind, label, exe in candidates:
            path = shutil.which(exe) or ""
            result.append(
                {"kind": kind, "label": label, "path": path or exe, "args": "",
                 "available": bool(path)}
            )
        if shutil.which("wsl.exe"):
            result.append(
                {"kind": "wsl", "label": "WSL", "path": "wsl.exe", "args": "",
                 "available": True}
            )
        return result

    # POSIX：/etc/shells + 当前登录 shell
    paths: list[str] = []
    try:
        with open("/etc/shells", encoding="utf-8") as fh:
            paths = [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    except OSError:
        pass
    env_shell = os.environ.get("SHELL", "")
    if env_shell and env_shell not in paths:
        paths.insert(0, env_shell)
    for path in paths:
        if not os.path.exists(path):
            continue
        result.append(
            {"kind": "posix", "label": os.path.basename(path), "path": path, "args": "",
             "available": True}
        )
    if not result:
        result.append(
            {"kind": "posix", "label": "sh", "path": "/bin/sh", "args": "",
             "available": os.path.exists("/bin/sh")}
        )
    return result


# ── 本地配置 CRUD ────────────────────────────────────


def list_groups() -> list[dict[str, Any]]:
    """列出本地终端分组"""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, sort_order FROM local_terminal_groups "
            "ORDER BY sort_order ASC, id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_group(name: str, sort_order: int = 0) -> int:
    """新建本地分组"""
    now = _now()
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO local_terminal_groups (name, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (name.strip(), sort_order, now, now),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def update_group(group_id: int, name: str | None = None, sort_order: int | None = None) -> None:
    """更新本地分组"""
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        params.append(name.strip())
    if sort_order is not None:
        sets.append("sort_order = ?")
        params.append(sort_order)
    if not sets:
        return
    params.extend([_now(), group_id])
    with _db() as conn:
        conn.execute(
            f"UPDATE local_terminal_groups SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
            params,
        )
        conn.commit()


def delete_group(group_id: int) -> None:
    """删除本地分组（组内条目落回未分组）"""
    with _db() as conn:
        conn.execute(
            "UPDATE local_terminal_profiles SET group_id = 0 WHERE group_id = ?",
            (group_id,),
        )
        conn.execute("DELETE FROM local_terminal_groups WHERE id = ?", (group_id,))
        conn.commit()


def list_profiles() -> list[dict[str, Any]]:
    """列出本地终端条目"""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, group_id, name, shell_kind, shell_path, args, cwd, env_json, "
            "init_command FROM local_terminal_profiles ORDER BY id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_profile(profile_id: int) -> dict[str, Any] | None:
    """读取单个本地终端条目"""
    with _db() as conn:
        row = conn.execute(
            "SELECT id, group_id, name, shell_kind, shell_path, args, cwd, env_json, "
            "init_command FROM local_terminal_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
    return dict(row) if row else None


def create_profile(data: dict[str, Any]) -> int:
    """新建本地终端条目"""
    now = _now()
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO local_terminal_profiles "
            "(group_id, name, shell_kind, shell_path, args, cwd, env_json, init_command, "
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(data.get("group_id") or 0),
                str(data.get("name") or "").strip(),
                str(data.get("shell_kind") or "posix"),
                str(data.get("shell_path") or ""),
                str(data.get("args") or ""),
                str(data.get("cwd") or ""),
                json.dumps(data.get("env") or {}, ensure_ascii=False),
                str(data.get("init_command") or ""),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def update_profile(profile_id: int, data: dict[str, Any]) -> None:
    """更新本地终端条目"""
    fields = {
        "group_id": int(data.get("group_id") or 0),
        "name": str(data.get("name") or "").strip(),
        "shell_kind": str(data.get("shell_kind") or "posix"),
        "shell_path": str(data.get("shell_path") or ""),
        "args": str(data.get("args") or ""),
        "cwd": str(data.get("cwd") or ""),
        "env_json": json.dumps(data.get("env") or {}, ensure_ascii=False),
        "init_command": str(data.get("init_command") or ""),
    }
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _db() as conn:
        conn.execute(
            f"UPDATE local_terminal_profiles SET {sets}, updated_at = ? WHERE id = ?",
            list(fields.values()) + [_now(), profile_id],
        )
        conn.commit()


def delete_profile(profile_id: int) -> None:
    """删除本地终端条目"""
    with _db() as conn:
        conn.execute("DELETE FROM local_terminal_profiles WHERE id = ?", (profile_id,))
        conn.commit()


# ── 本地会话 ─────────────────────────────────────────


class LocalSession:
    """
    本机终端会话。

    :param profile: 终端条目（可为 None，表示用系统默认 shell）
    :param cols: 初始列数
    :param rows: 初始行数
    :param on_output: 输出回调 `(bytes) -> None`
    :param on_status: 状态回调 `(phase, index, message) -> None`
    """

    def __init__(
        self,
        profile: dict[str, Any] | None,
        cols: int = 80,
        rows: int = 24,
        on_output: Callable[[bytes], None] | None = None,
        on_status: Callable[[str, int, str], None] | None = None,
    ) -> None:
        self.profile = profile or {}
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        self.on_output = on_output
        self.on_status = on_status
        self._closed = False
        self._fd: int | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._pty: Any = None
        self._reader: threading.Thread | None = None

    def open(self) -> None:
        """启动本地 shell 并开始读输出"""
        if os.name == "nt":
            self._open_windows()
        else:
            self._open_posix()
        self._status("ready", 0, "已打开")
        self._reader = threading.Thread(
            target=self._read_loop, name="local-term-reader", daemon=True
        )
        self._reader.start()
        init_command = str(self.profile.get("init_command") or "").strip()
        if init_command:
            self.write((init_command + "\n").encode("utf-8"))

    def close(self) -> None:
        """关闭本地 shell"""
        if self._closed:
            return
        self._closed = True
        try:
            if self._pty is not None:
                self._pty.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._fd is not None:
                os.close(self._fd)
        except OSError:
            pass
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        self._status("closed", 0, "已关闭")

    def write(self, data: bytes) -> None:
        """写入输入"""
        if self._closed:
            return
        try:
            if self._pty is not None:
                self._pty.write(data.decode("utf-8", "replace"))
            elif self._fd is not None:
                os.write(self._fd, data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("写入本机终端失败：%s", exc)

    def resize(self, cols: int, rows: int) -> None:
        """调整 PTY 尺寸"""
        self.cols, self.rows = max(1, int(cols)), max(1, int(rows))
        if self._pty is not None:
            try:
                self._pty.set_size(self.cols, self.rows)
            except Exception:  # noqa: BLE001
                pass
            return
        if self._fd is None:
            return
        try:
            import fcntl
            import termios

            fcntl.ioctl(
                self._fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", self.rows, self.cols, 0, 0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("调整本机 PTY 尺寸失败：%s", exc)

    # ── 内部 ──────────────────────────────────────────

    def _status(self, phase: str, index: int, message: str) -> None:
        if self.on_status is None:
            return
        try:
            self.on_status(phase, index, message)
        except Exception:  # noqa: BLE001
            pass

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        extra = self.profile.get("env_json") or "{}"
        try:
            parsed = json.loads(extra) if isinstance(extra, str) else dict(extra)
            env.update({str(k): str(v) for k, v in parsed.items()})
        except Exception:  # noqa: BLE001
            pass
        return env

    def _open_posix(self) -> None:
        shell_path = str(self.profile.get("shell_path") or os.environ.get("SHELL") or "/bin/sh")
        args = str(self.profile.get("args") or "").split()
        cwd = str(self.profile.get("cwd") or "") or None
        master, slave = pty.openpty()
        self._proc = subprocess.Popen(
            [shell_path] + args,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            env=self._env(),
            preexec_fn=os.setsid,
            close_fds=True,
        )
        os.close(slave)
        self._fd = master
        self.resize(self.cols, self.rows)

    def _open_windows(self) -> None:
        try:
            from winpty import PTY  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "本机终端缺少 pywinpty 依赖（Windows 需要 ConPTY），"
                "请安装 pywinpty 后重试"
            ) from exc
        shell_path = str(self.profile.get("shell_path") or "powershell.exe")
        args = str(self.profile.get("args") or "")
        cmdline = f"{shell_path} {args}".strip()
        pty_obj = PTY(self.cols, self.rows)
        pty_obj.spawn(cmdline, cwd=str(self.profile.get("cwd") or "") or None, env=self._env())
        self._pty = pty_obj

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                if self._pty is not None:
                    chunk = self._pty.read()
                    if not chunk:
                        break
                    data = chunk.encode("utf-8", "replace") if isinstance(chunk, str) else chunk
                else:
                    if self._fd is None:
                        break
                    data = os.read(self._fd, READ_CHUNK)
            except OSError:
                break
            except Exception:  # noqa: BLE001
                break
            if not data:
                break
            if self.on_output is not None:
                try:
                    self.on_output(data)
                except Exception:  # noqa: BLE001
                    break
        if not self._closed:
            self._status("closed", 0, "本机终端已退出")

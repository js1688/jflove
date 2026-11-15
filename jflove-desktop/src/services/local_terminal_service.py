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
import shutil
import sqlite3
import struct
import subprocess
import threading
import time
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


# ── 最近一次打开结果（渲染层补查用）──────────────────

#: session_id → {"status": "ready" | "failed", "message": str}
_OPEN_STATE: dict[str, dict[str, str]] = {}
#: 只保留最近若干条，避免长期运行无限增长
_OPEN_STATE_LIMIT = 32


def remember_open_state(session_id: str, status: str, message: str = "") -> None:
    """
    记住本机终端最近一次打开结果，供渲染层**补查**。

    为什么需要：Python 侧先返回 `{session_id, port, token}`、后台线程再启动 shell，
    失败事件可能**早于**渲染层建立标签页到达，那样 `patchTab` 会找不到标签页而丢弃
    （v1.10.7 审查 S1）。渲染层建好标签页后查一次本状态即可补齐。
    """
    _OPEN_STATE[session_id] = {"status": status, "message": message}
    while len(_OPEN_STATE) > _OPEN_STATE_LIMIT:
        _OPEN_STATE.pop(next(iter(_OPEN_STATE)))


def get_open_state(session_id: str) -> dict[str, str]:
    """查询打开结果；尚未上报时返回 pending"""
    return dict(_OPEN_STATE.get(session_id) or {"status": "pending", "message": ""})


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
        logger.info(
            "本机终端启动：shell=%s args=%r cwd=%r",
            self.profile.get("shell_path") or "(default)",
            self.profile.get("args") or "",
            self.profile.get("cwd") or "",
        )
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

    def _env_extra(self) -> dict[str, str]:
        """解析 profile 里的自定义环境变量（坏配置按"无自定义"处理）"""
        extra = self.profile.get("env_json") or "{}"
        try:
            parsed = json.loads(extra) if isinstance(extra, str) else dict(extra)
            return {str(k): str(v) for k, v in parsed.items()}
        except Exception:  # noqa: BLE001
            return {}

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self._env_extra())
        return env

    def _open_posix(self) -> None:
        # `pty` 只在 POSIX 存在（Windows 无 termios）；延迟到此处导入，
        # 避免打包后的 Windows 版在启动阶段就 ModuleNotFoundError。
        import pty

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

    def _winpty_cwd(self) -> str:
        """
        pywinpty 的 `cwd` 必须是**存在的绝对目录**（CreateProcess 直接用）。

        配置里留空时用用户主目录；填了但不存在时回退主目录并告警
        （比让 CreateProcess 直接失败更容易排查）。
        """
        raw = str(self.profile.get("cwd") or "").strip()
        if raw:
            candidate = os.path.abspath(os.path.expandvars(raw))
            if os.path.isdir(candidate):
                return candidate
            logger.warning("本机终端工作目录不存在，回退到用户主目录：%s", raw)
        return os.path.abspath(os.path.expanduser("~"))

    def _winpty_env(self) -> str | None:
        """
        pywinpty 的 `env` 是 `K=V` 以 `\\0` 分隔的**字符串**（不是 dict）。

        没有自定义环境变量时返回 None（子进程继承本进程环境），
        避免把整份 `os.environ` 塞进 ConPTY 环境块。
        末尾那个 `\\0` 与 pywinpty 自己的 `PtyProcess.spawn()` 写法一致。
        """
        extra = self._env_extra()
        if not extra:
            return None
        merged = os.environ.copy()
        merged.update(extra)
        return "\0".join(f"{k}={v}" for k, v in merged.items()) + "\0"

    def _open_windows(self) -> None:
        """
        启动 Windows 本机 shell（pywinpty / ConPTY）。

        契约（pywinpty 3.x `_winpty.PTY.spawn`）：
        `spawn(appname, cmdline=None, cwd=None, env=None) -> bool`

        - `appname`：可执行文件路径（**不是**整条命令行；整条塞进去 ConPTY 起不来）
        - `cmdline`：参数串（可为 None）
        - `cwd`：存在的绝对目录
        - `env`：`K=V` 以 `\\0` 分隔的字符串（传 dict 直接 TypeError）
        - **返回值 False = 没起来**（ConPTY 失败不抛异常，只会返回 False）；
          构造 `PTY` 与 `spawn` 仍可能抛 `WinptyError`（Rust 英文文案）

        真机事故（v1.10.6）：打包漏了 pywinpty 的 `OpenConsole.exe` /
        `winpty-agent.exe` ⇒ spawn 返回 False，旧实现忽略返回值并打印"已启动"
        ⇒ 前端绿点、画面全黑、日志无异常。
        """
        try:
            from winpty import PTY  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "本机终端缺少 pywinpty 依赖（Windows 需要 ConPTY），"
                "请安装 pywinpty 后重试"
            ) from exc
        shell_path = str(self.profile.get("shell_path") or "powershell.exe").strip()
        args = str(self.profile.get("args") or "").strip()
        cwd = self._winpty_cwd()
        try:
            pty_obj = PTY(self.cols, self.rows)
            spawned = pty_obj.spawn(
                shell_path,
                cmdline=(" " + args) if args else None,
                cwd=cwd,
                env=self._winpty_env(),
            )
        except Exception as exc:  # noqa: BLE001 - WinptyError 的英文文案对用户无意义
            raise RuntimeError(
                f"本机终端启动失败：{shell_path}（{exc}）；"
                "常见原因是安装包缺少 pywinpty 的 OpenConsole.exe / winpty-agent.exe"
            ) from exc
        if not spawned:
            raise RuntimeError(
                f"本机终端启动失败：{shell_path}"
                "（ConPTY 未能启动进程；常见原因是安装包缺少 pywinpty 的 "
                "OpenConsole.exe / winpty-agent.exe）"
            )
        self._pty = pty_obj
        logger.info("本机终端已启动（pywinpty）：%s args=%r cwd=%s", shell_path, args, cwd)

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                if self._pty is not None:
                    chunk = self._pty.read()
                    # pywinpty 在暂无数据时会返回空串，**不等于 EOF**；
                    # 旧实现直接 break 会让会话立刻"关闭"→ 前端反复重开，表现为闪屏。
                    if not chunk:
                        alive = getattr(self._pty, "isalive", lambda: True)()
                        if not alive:
                            break
                        time.sleep(0.01)
                        continue
                    data = chunk.encode("utf-8", "replace") if isinstance(chunk, str) else chunk
                else:
                    if self._fd is None:
                        break
                    data = os.read(self._fd, READ_CHUNK)
            except OSError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("本机终端读循环异常：%s", exc)
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

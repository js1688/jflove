"""
SSH 会话（v1.10.0）

用 `paramiko` 建立 SSH 通道；跳板链由客户端自行展开（服务端只存档配置）。
所有阻塞操作都在桥的工作线程执行，输出通过回调交给终端通道。

关键约束（`文档记录/设计文档/v1.10.0.md` §3.1）：

- **主机指纹 TOFU**：首次连接暂停认证、请用户确认；之后不匹配一律拒绝。
- **跳板**：`open_channel("direct-tcpip", ...)` 串成链，最多 16 级。
- **凭据只在内存**：私钥用 `StringIO` 加载，不写临时文件、不落日志。
"""

from __future__ import annotations

import base64
import hashlib
import socket
import threading
from io import StringIO
from typing import Any, Callable

import paramiko

from src.services import remote_service
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 单跳 TCP 连接超时（秒）
CONNECT_TIMEOUT = 15.0

#: 认证超时（秒）
AUTH_TIMEOUT = 20.0

#: 跳板链最大级数（与服务端校验一致）
MAX_JUMP_DEPTH = 16

#: 输出块大小
READ_CHUNK = 4096


class SshError(Exception):
    """SSH 会话错误基类"""


class AuthError(SshError):
    """认证失败"""


class HostKeyError(SshError):
    """主机指纹未确认或不匹配"""


class JumpError(SshError):
    """跳板链路失败（message 里带具体跳）"""


def fetch_fingerprint(key: paramiko.PKey) -> str:
    """
    计算 OpenSSH 风格的主机指纹。

    :param key: paramiko 公钥对象
    :returns: `SHA256:<base64 无填充>`
    """
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def load_private_key(pem: str, passphrase: str = "") -> paramiko.PKey:
    """
    从 PEM 文本加载私钥（支持 RSA / Ed25519 / ECDSA）。

    :raises AuthError: 格式不支持或口令错误
    """
    last_error: Exception | None = None
    for key_type in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_type.from_private_key(StringIO(pem), password=passphrase or None)
        except Exception as exc:  # noqa: BLE001 - 依次尝试，最后统一报错
            last_error = exc
    raise AuthError(f"私钥无法解析（支持 OpenSSH/PEM，不支持 PuTTY .ppk）：{last_error}")


class SshSession:
    """
    一条 SSH 会话（含跳板链）。

    :param connection: 服务端返回的连接全量字典（含凭据）
    :param cols: 初始终端列数
    :param rows: 初始终端行数
    :param on_output: 输出回调 `(bytes) -> None`
    :param on_status: 状态回调 `(phase, jump_index, message) -> None`
    :param hostkey_ask: 主机指纹确认回调 `(connection_row, fingerprint) -> bool`
    :param otp_ask: 键盘交互回调 `(prompt) -> str`
    """

    def __init__(
        self,
        connection: dict[str, Any],
        cols: int = 80,
        rows: int = 24,
        on_output: Callable[[bytes], None] | None = None,
        on_status: Callable[[str, int, str], None] | None = None,
        hostkey_ask: Callable[[dict, str], bool] | None = None,
        otp_ask: Callable[[str], str] | None = None,
    ) -> None:
        self.connection = connection
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        self.on_output = on_output
        self.on_status = on_status
        self.hostkey_ask = hostkey_ask
        self.otp_ask = otp_ask

        self._transports: list[paramiko.Transport] = []
        self._sock: socket.socket | None = None
        self._chan: paramiko.Channel | None = None
        self._closed = False
        self._write_lock = threading.Lock()
        self._reader: threading.Thread | None = None

    # ── 生命周期 ──────────────────────────────────────

    def open(self) -> None:
        """
        建立完整链路并打开交互式 shell。

        :raises HostKeyError: 指纹被拒绝或不匹配
        :raises AuthError: 认证失败
        :raises JumpError: 跳板某一跳失败
        """
        chain = self._resolve_chain()
        last_index = len(chain) - 1
        sock: socket.socket | paramiko.Channel | None = None
        for index, row in enumerate(chain):
            label = "目标" if index == last_index else f"跳板 {index + 1}"
            self._status("jump_connecting", index, f"{label} {row['host']}:{row['port']}")
            try:
                if sock is None:
                    sock = socket.create_connection(
                        (row["host"], int(row["port"])), timeout=CONNECT_TIMEOUT
                    )
                transport = paramiko.Transport(sock)
                transport.banner_timeout = CONNECT_TIMEOUT
                transport.auth_timeout = AUTH_TIMEOUT
                transport.start_client(timeout=CONNECT_TIMEOUT)
                self._transports.append(transport)
                self._verify_host_key(transport, row)
                self._authenticate(transport, row)
            except (HostKeyError, AuthError):
                self.close()
                raise
            except Exception as exc:  # noqa: BLE001 - 统一转成跳板错误
                self.close()
                raise JumpError(f"{label} {row['host']} 连接失败：{exc}")
            self._status("jump_ready", index, f"{label} 已就绪")
            if index < last_index:
                nxt = chain[index + 1]
                sock = transport.open_channel(
                    "direct-tcpip",
                    (nxt["host"], int(nxt["port"])),
                    ("127.0.0.1", 0),
                )

        transport = self._transports[-1]
        chan = transport.open_session(timeout=CONNECT_TIMEOUT)
        chan.get_pty(
            term=self.connection.get("terminal_type") or "xterm-256color",
            width=self.cols,
            height=self.rows,
        )
        chan.invoke_shell()
        chan.settimeout(1.0)
        self._chan = chan
        self._status("ready", last_index, "已连接")

        self._reader = threading.Thread(
            target=self._read_loop, name="ssh-reader", daemon=True
        )
        self._reader.start()

        init_command = str(self.connection.get("init_command") or "").strip()
        if init_command:
            self.write((init_command + "\n").encode("utf-8"))

    def close(self) -> None:
        """关闭会话与全部跳板（逆序）"""
        if self._closed:
            return
        self._closed = True
        try:
            if self._chan is not None:
                self._chan.close()
        except Exception:  # noqa: BLE001 - 关闭阶段忽略异常
            pass
        for transport in reversed(self._transports):
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:  # noqa: BLE001
                pass
        self._status("closed", 0, "已断开")

    # ── 数据 ──────────────────────────────────────────

    def write(self, data: bytes) -> None:
        """向 shell 写入输入（线程安全）"""
        if self._chan is None or self._closed:
            return
        with self._write_lock:
            try:
                self._chan.sendall(data)
            except Exception as exc:  # noqa: BLE001 - 断线后忽略输入
                logger.debug("写入 SSH 通道失败：%s", exc)

    def resize(self, cols: int, rows: int) -> None:
        """调整远端 PTY 尺寸"""
        self.cols, self.rows = max(1, int(cols)), max(1, int(rows))
        if self._chan is None or self._closed:
            return
        try:
            self._chan.resize_pty(width=self.cols, height=self.rows)
        except Exception as exc:  # noqa: BLE001
            logger.debug("调整 PTY 尺寸失败：%s", exc)

    def open_sftp(self) -> paramiko.SFTPClient:
        """在同一连接上打开 SFTP 通道（复用认证与跳板链）"""
        if not self._transports:
            raise SshError("会话尚未建立")
        return self._transports[-1].open_sftp_client()

    # ── 内部 ──────────────────────────────────────────

    def _status(self, phase: str, index: int, message: str) -> None:
        """上报状态（回调异常不影响主流程）"""
        if self.on_status is None:
            return
        try:
            self.on_status(phase, index, message)
        except Exception:  # noqa: BLE001
            logger.debug("状态回调失败：%s", message)

    def _resolve_chain(self) -> list[dict[str, Any]]:
        """解析跳板链（从最外层跳板到目标），并做环检测"""
        chain: list[dict[str, Any]] = []
        seen: set[int] = set()
        current = self.connection
        while True:
            cid = int(current.get("id") or 0)
            if cid in seen:
                raise JumpError("跳板链路存在循环")
            seen.add(cid)
            chain.append(current)
            jump_id = int(current.get("jump_connection_id") or 0)
            if not jump_id:
                break
            parent = remote_service.get_connection(jump_id)
            if not parent:
                raise JumpError("跳板连接不存在")
            current = parent
            if len(chain) > MAX_JUMP_DEPTH:
                raise JumpError(f"跳板链路超过 {MAX_JUMP_DEPTH} 级")
        chain.reverse()
        return chain

    def _verify_host_key(self, transport: paramiko.Transport, row: dict) -> None:
        """TOFU：首连请用户确认；已记录则严格比对"""
        actual = fetch_fingerprint(transport.get_remote_server_key())
        expected = str(row.get("host_key_fingerprint") or "")
        if expected:
            if expected != actual:
                raise HostKeyError(
                    f"主机指纹不匹配（期望 {expected}，实际 {actual}），"
                    "可能有人劫持连接，已中止"
                )
            return
        if self.hostkey_ask is None or not self.hostkey_ask(row, actual):
            raise HostKeyError("主机指纹未确认，已取消连接")
        remote_service.save_host_key_fingerprint(int(row["id"]), actual)

    def _authenticate(self, transport: paramiko.Transport, row: dict) -> None:
        """按配置的认证方式登录"""
        username = str(row.get("username") or "")
        auth_type = str(row.get("auth_type") or "password")
        password = str(row.get("password") or "")
        try:
            if auth_type == "publickey":
                key = load_private_key(
                    str(row.get("private_key") or ""), str(row.get("passphrase") or "")
                )
                transport.auth_publickey(username, key)
                return
            transport.auth_password(username, password)
        except paramiko.AuthenticationException as exc:
            # 密码 + 键盘交互：密码通过后仍可能要求 OTP
            if auth_type == "password_keyboard" and self.otp_ask is not None:
                try:
                    transport.auth_interactive(username, self._interactive_handler)
                    return
                except paramiko.AuthenticationException:
                    raise AuthError("认证失败（密码或验证码错误）")
            raise AuthError(f"认证失败：{exc}")
        except SshError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AuthError(f"认证失败：{exc}")

    def _interactive_handler(
        self, title: str, instructions: str, prompts: list[tuple[str, bool]]
    ) -> list[str]:
        """键盘交互：逐项向 UI 要答案"""
        answers: list[str] = []
        for prompt, _echo in prompts:
            answers.append(self.otp_ask(prompt) if self.otp_ask else "")
        return answers

    def _read_loop(self) -> None:
        """持续读取远端输出并交给回调"""
        chan = self._chan
        while not self._closed and chan is not None:
            try:
                data = chan.recv(READ_CHUNK)
            except socket.timeout:
                continue
            except Exception:  # noqa: BLE001 - 断线即退出
                break
            if not data:
                break
            if self.on_output is not None:
                try:
                    self.on_output(data)
                except Exception:  # noqa: BLE001
                    break
        if not self._closed:
            self._status("closed", 0, "远端已断开")

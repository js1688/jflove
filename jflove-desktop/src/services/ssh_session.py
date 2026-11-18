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
import os
import re
import socket
import sys
import threading
from io import StringIO
from pathlib import Path
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


def _auth_error(exc: paramiko.AuthenticationException) -> AuthError:
    """把 paramiko 认证异常转成可读中文（带服务器允许的认证方式）"""
    # paramiko 5.x：BadAuthenticationType 与 PartialAuthentication 是**平级**的两种异常
    # （partial 不继承 BadAuthenticationType），两者都带 `allowed_types`
    allowed_types = [str(item) for item in (getattr(exc, "allowed_types", None) or ())]
    if allowed_types:
        allowed = "、".join(allowed_types)
        if "password" in allowed_types:
            return AuthError(
                f"服务器还要求密码认证（服务器允许：{allowed}），"
                "请在认证方式里选择「密码 + 密钥」并填写密码"
            )
        if "publickey" in allowed_types:
            return AuthError(
                f"服务器要求密钥认证（服务器允许：{allowed}），请检查私钥与认证方式"
            )
        return AuthError(f"服务器不接受当前认证方式（服务器允许：{allowed}）")
    return AuthError(f"认证失败：{exc}")


def fetch_fingerprint(key: paramiko.PKey) -> str:
    """
    计算 OpenSSH 风格的主机指纹。

    :param key: paramiko 公钥对象
    :returns: `SHA256:<base64 无填充>`
    """
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def load_private_key(source: str, passphrase: str = "") -> paramiko.PKey:
    """
    从私钥文本**或私钥文件路径**加载私钥。

    支持 OpenSSH / PEM（PKCS#1）/ PKCS#8（`BEGIN PRIVATE KEY`、`BEGIN ENCRYPTED PRIVATE KEY`），
    以及 RSA / Ed25519 / ECDSA 三种密钥。PuTTY `.ppk` 不支持，会提示先用 PuTTYgen 转换。

    :param source: 私钥文本或本机文件路径（支持 `~`、环境变量与相对路径）
    :param passphrase: 私钥口令（未加密时留空）
    :raises AuthError: 文件不存在、格式不支持或口令错误
    """
    text = _read_private_key_text(source)
    password = passphrase or None

    errors: list[Exception] = []
    for key_type in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_type.from_private_key(StringIO(text), password=password)
        except paramiko.PasswordRequiredException:
            raise AuthError("私钥已加密，请填写「私钥口令」") from None
        except Exception as exc:  # noqa: BLE001 - 依次尝试，最后统一报错
            errors.append(exc)

    # paramiko 不认 PKCS#8（OpenSSL 3 / 部分网关工具导出的常见格式），用 cryptography 兜底
    try:
        return _load_pkcs8_key(text, passphrase)
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - 归一化失败则继续走统一报错
        errors.append(exc)

    detail = "；".join(str(item) for item in errors[-3:])
    lowered = detail.lower()
    if any(
        marker in lowered
        for marker in ("bad password", "incorrect", "checkint", "corrupt")
    ):
        raise AuthError("私钥口令错误，或私钥文件已损坏（请重新导出一次私钥）")
    last_error = errors[-1] if errors else "未知错误"
    raise AuthError(f"私钥无法解析（支持 OpenSSH/PEM，不支持 PuTTY .ppk）：{last_error}")


#: PEM/OpenSSH 私钥文本的起始标记
_PEM_MARKER = "-----BEGIN"


def _read_private_key_text(source: str) -> str:
    """
    统一「私钥文本 / 私钥文件路径」两种输入。

    :param source: 用户输入（表单里的私钥内容或文件路径）
    :raises AuthError: 内容为空、.ppk、路径不存在或文件读不了
    """
    text = str(source or "").lstrip("\ufeff").strip()
    # 从配置/文档里复制路径时常带引号（"C:/keys/id_rsa"），一并去掉
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    if not text:
        raise AuthError("私钥内容为空：请粘贴私钥文本，或填写私钥文件路径")
    if "PuTTY-User-Key-File" in text:
        raise AuthError(
            "这是 PuTTY 私钥（.ppk，WinSCP 转换产物）。请改用原始 PEM 文件"
            "（-----BEGIN RSA PRIVATE KEY-----），或用 PuTTYgen 执行 "
            "Conversions → Export OpenSSH key 后再选择"
        )
    if _PEM_MARKER in text:
        return _normalize_pem_text(text)

    path, _tried = _resolve_private_key_path(text)
    if path is None:
        # 安全宪法 §9.13：错误文案不回显完整路径，只留文件名 + 查找范围
        file_name = Path(text).name or "（未提供文件名）"
        looks_like_path = any(sep in text for sep in ("/", "\\")) or text.lower().endswith(
            (".pem", ".key", ".ppk", ".rsa")
        )
        if looks_like_path:
            raise AuthError(
                f"私钥文件不存在：{file_name}（已按当前目录、用户主目录、程序目录查找）。"
                "请检查路径，或用「选择文件…」重新选择"
            )
        raise AuthError(
            "私钥无法解析：既不是 PEM/OpenSSH 私钥文本，也不是可访问的文件路径。"
            "请粘贴 -----BEGIN ... PRIVATE KEY----- 内容，"
            "或用「选择文件…」选择私钥文件"
        )
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise AuthError(f"私钥文件读取失败：{path.name}（{exc}）") from exc


def _normalize_pem_text(text: str) -> str:
    """
    把私钥文本整理成 paramiko 认得的 PEM/OpenSSH 形态。

    真实用户可能：

    - 从记事本复制时带上 UTF-8 BOM / 整体缩进；
    - 从 YAML/JSON 里复制成**一行**（换行变成字面量 `\\n` 或被折叠掉）；
    - Windows 换行（CRLF）。

    paramiko 要求 `-----BEGIN` 出现在**行首**、且至少两行，否则统一报
    「not a valid ... private key file」——这正是用户看到的那条错误。
    这里只做格式规范化，不改私钥内容本身。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    # JSON/YAML 里粘出来的字面量换行（base64 不可能含反斜杠，替换是安全的）
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    match = re.search(
        r"^\s*-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----\s*$",
        text,
        re.S | re.M,
    )
    if not match:
        return text.strip()
    label = match.group(1).strip()
    body = match.group(2)
    if label == "OPENSSH":
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if len(lines) <= 1:
            raw = "".join(lines)
            lines = [raw[index:index + 70] for index in range(0, len(raw), 70)]
        return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"

    # PEM（RSA / EC / PKCS#8）：`Proc-Type` 等头部保留，base64 按 64 列重排
    headers: list[str] = []
    chunks: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not chunks and ":" in stripped:
            headers.append(stripped)
        else:
            chunks.append(stripped)
    raw = "".join(chunks)
    wrapped = [raw[index:index + 64] for index in range(0, len(raw), 64)]
    output = [f"-----BEGIN {label}-----", *headers, *wrapped, f"-----END {label}-----"]
    return "\n".join(output) + "\n"


def _resolve_private_key_path(raw: str) -> tuple[Path | None, list[Path]]:
    """
    把路径输入解析成真实文件。

    绝对路径直接判断；相对路径依次尝试：当前工作目录 → 用户主目录 → 程序所在目录。
    返回命中的文件与全部候选（后者用于错误提示）。
    """
    expanded = os.path.expandvars(os.path.expanduser(raw))
    path = Path(expanded)
    if path.is_absolute():
        candidates = [path]
    else:
        roots = [Path.cwd(), Path.home()]
        try:
            roots.append(Path(sys.argv[0]).resolve().parent)
        except (OSError, ValueError):
            pass  # 取不到程序目录时只用前两个根
        candidates = []
        seen: set[str] = set()
        for root in roots:
            candidate = root / path
            if str(candidate) in seen:
                continue
            seen.add(str(candidate))
            candidates.append(candidate)
    for candidate in candidates:
        if candidate.is_file():
            return candidate, candidates
    return None, candidates


def _load_pkcs8_key(text: str, passphrase: str) -> paramiko.PKey:
    """
    用 cryptography 解析 PKCS#8 私钥（含加密），再转成 OpenSSH 文本交给 paramiko。

    :raises AuthError: 口令错误
    :raises Exception: 不是 PKCS#8 或转换失败（由调用方统一报错）
    """
    from cryptography.hazmat.primitives import serialization

    try:
        key = serialization.load_pem_private_key(
            text.encode("utf-8"),
            password=passphrase.encode("utf-8") if passphrase else None,
        )
    except TypeError as exc:
        if "password was not given" in str(exc).lower():
            raise AuthError("私钥已加密，请填写「私钥口令」") from exc
        raise
    normalized = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode("ascii")
    for key_type in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_type.from_private_key(StringIO(normalized))
        except Exception:  # noqa: BLE001 - 归一化后的格式只可能命中一种
            continue
    raise paramiko.SSHException("PKCS#8 私钥转换失败")


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
        #: 连接类型：`ssh` = 终端 + 可选 SFTP；`sftp` = 只开 SFTP（不依赖 shell）
        self.mode = str(connection.get("mode") or "ssh")
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
        if self.mode == "sftp":
            # 纯 SFTP：只建传输层，不开 shell（部分服务器没有 shell）
            self._status("ready", last_index, "SFTP 已连接")
            return
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

    def nudge_redraw(self) -> None:
        """
        触发一次 SIGWINCH，让全屏 TUI 整屏重绘（终端"重绘"按钮）。

        远端 PTY 只有尺寸变化才会收到 SIGWINCH，所以先缩一列再恢复；
        放后台线程做，避免阻塞调用方。
        """

        def run() -> None:
            try:
                if self._chan is None or self._closed:
                    return
                self._chan.resize_pty(width=max(2, self.cols - 1), height=self.rows)
                import time as _time

                _time.sleep(0.06)
                self._chan.resize_pty(width=self.cols, height=self.rows)
            except Exception as exc:  # noqa: BLE001 - 重绘失败不影响会话
                logger.debug("请求远端重绘失败：%s", exc)

        threading.Thread(target=run, name="ssh-redraw", daemon=True).start()

    def open_sftp(self) -> paramiko.SFTPClient:
        """
        在同一连接上打开 SFTP 通道（复用认证与跳板链）。

        :raises SshError: 链路尚未建好（此时应等待就绪事件，而不是把它当成目录错误）
        """
        if not self._transports:
            raise SshError("会话尚未建立（正在连接中，请稍候）")
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
        """按配置的认证方式登录；密钥方式支持「密钥 + 密码」双因素"""
        username = str(row.get("username") or "")
        auth_type = str(row.get("auth_type") or "password")
        password = str(row.get("password") or "")
        try:
            if auth_type in ("publickey", "password_publickey"):
                key = load_private_key(
                    str(row.get("private_key") or ""), str(row.get("passphrase") or "")
                )
                # publickey 模式若恰好也存了密码，按双因素尝试（兼容旧配置与 API 直写）
                self._auth_publickey(transport, username, key, password)
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
            # 没有 allowed_types 的裸失败：按认证方式给中文原因，不把 paramiko 英文原文抛给用户
            if not getattr(exc, "allowed_types", None):
                if auth_type == "password":
                    raise AuthError("认证失败：用户名或密码错误") from exc
                if auth_type in ("publickey", "password_publickey"):
                    raise AuthError("认证失败：密钥或密码不被服务器接受") from exc
            raise _auth_error(exc)
        except SshError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AuthError(f"认证失败：{exc}")

    def _auth_publickey(
        self,
        transport: paramiko.Transport,
        username: str,
        key: paramiko.PKey,
        password: str,
    ) -> None:
        """
        密钥认证；服务器要求多因素时继续用密码完成。

        兼容两种顺序：

        - `publickey,password`：密钥通过后服务器回 partial，再补密码；
        - `password,publickey`：服务器直接不认密钥，先走密码，再补密钥。
        """
        try:
            transport.auth_publickey(username, key)
            return
        except paramiko.AuthenticationException as exc:
            if transport.is_authenticated():
                return
            allowed = set(getattr(exc, "allowed_types", None) or ())
            if not password:
                if "password" in allowed:
                    raise AuthError(
                        "服务器还要求密码认证（服务器允许：password）；"
                        "如需「密码 + 密钥」双因素，请切换该认证方式并填写密码"
                    ) from exc
                raise
            if allowed and "password" not in allowed:
                # 服务器明确不接受密码（只认密钥）：密钥失败就没必要再试密码
                raise
            # allowed 为空 = 密钥被拒但服务器未给出方法列表：
            # 退回密码再试一次（服务器允许密码/密钥二选一时更宽容）

        # 密钥未被接受（或需要继续），尝试密码阶段
        try:
            transport.auth_password(username, password)
            return
        except paramiko.AuthenticationException as exc:
            if transport.is_authenticated():
                return
            allowed = set(getattr(exc, "allowed_types", None) or ())
            if "publickey" not in allowed:
                raise AuthError("认证失败：密码或密钥不被服务器接受") from exc

        # 密码通过后服务器仍要求密钥（password,publickey 顺序）
        transport.auth_publickey(username, key)

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
            except Exception as exc:  # noqa: BLE001 - 断线即退出
                logger.info("SSH 读循环结束：%s", exc)
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

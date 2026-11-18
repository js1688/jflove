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
from paramiko.ssh_exception import PartialAuthentication

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

#: 一次认证流程里最多再尝试几轮（服务器多因素认证）
MAX_AUTH_ROUNDS = 5

#: 单次认证方法最多尝试次数（密钥/密码可能因服务器顺序被要求两遍）
MAX_METHOD_ATTEMPTS = {"password": 2, "publickey": 2, "keyboard-interactive": 1}


def _env_seconds(name: str, default: float) -> float:
    """读取可覆盖的超时环境变量（非法值回落到默认，避免配置把连接拖死）"""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("环境变量 %s 不是数字，使用默认 %s 秒", name, default)
        return default
    return value if value > 0 else default


#: 打开 SFTP 子系统（channel + subsystem 握手）的超时（秒）
SFTP_OPEN_TIMEOUT = _env_seconds("JFLOVE_SFTP_OPEN_TIMEOUT", 15.0)

#: SFTP 单次操作（listdir / read / write 等）超时（秒）
SFTP_OP_TIMEOUT = _env_seconds("JFLOVE_SFTP_OP_TIMEOUT", 45.0)


class SshError(Exception):
    """SSH 会话错误基类"""


class AuthError(SshError):
    """认证失败"""


class HostKeyError(SshError):
    """主机指纹未确认或不匹配"""


class JumpError(SshError):
    """跳板链路失败（message 里带具体跳）"""


def _auth_hint(allowed: set[str], fallback: str) -> AuthError:
    """
    把服务器允许的认证方式转成可执行的中文提示。

    :param allowed: 服务器允许的认证方式集合（空集表示服务器未给出方法列表）
    :param fallback: 没有方法列表时的兜底文案
    """
    if not allowed:
        return AuthError(fallback)
    allowed_text = "、".join(sorted(allowed))
    if "password" in allowed:
        return AuthError(
            f"服务器还要求账号密码认证（服务器允许：{allowed_text}）。"
            "请把**服务器登录密码**填到「密码」，认证方式选择「密码 + 密钥」"
        )
    if "keyboard-interactive" in allowed:
        return AuthError(
            f"服务器还要求继续认证（服务器允许：{allowed_text}）。"
            "请使用「密码 + 密钥」或「密码 + OTP」，按弹层提示补下一次认证"
        )
    if "publickey" in allowed:
        return AuthError(
            f"服务器要求密钥认证（服务器允许：{allowed_text}），请检查私钥与认证方式"
        )
    return AuthError(f"服务器不接受当前认证方式（服务器允许：{allowed_text}）")


def _auth_error(exc: paramiko.AuthenticationException) -> AuthError:
    """把 paramiko 认证异常转成可读中文（带服务器允许的认证方式）"""
    # paramiko 5.x：BadAuthenticationType 与 PartialAuthentication 是**平级**的两种异常
    # （partial 不继承 BadAuthenticationType），两者都带 `allowed_types`
    allowed_types = {str(item) for item in (getattr(exc, "allowed_types", None) or ())}
    if allowed_types:
        return _auth_hint(allowed_types, str(exc))
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
        self._sftp_client: paramiko.SFTPClient | None = None
        #: 传输专用 client（`open_sftp(cached=False)`）；会话关闭时一并清理，避免句柄泄漏
        self._sftp_extra_clients: set[paramiko.SFTPClient] = set()
        #: 正在握手/协商中的 SFTP channel（取消加载时要能立刻关掉）
        self._sftp_opening_chan: paramiko.Channel | None = None
        self._sftp_open_lock = threading.Lock()
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
        if self._sftp_opening_chan is not None:
            try:
                self._sftp_opening_chan.close()
            except Exception:  # noqa: BLE001
                pass
            self._sftp_opening_chan = None
        try:
            if self._sftp_client is not None:
                self._sftp_client.close()
        except Exception:  # noqa: BLE001 - 关闭阶段忽略异常
            pass
        self._sftp_client = None
        for client in list(self._sftp_extra_clients):
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        self._sftp_extra_clients.clear()
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

    def open_sftp(self, cached: bool = True) -> paramiko.SFTPClient:
        """
        在同一连接上打开 SFTP 通道（复用认证与跳板链）。

        :param cached: True = 复用/缓存会话级 client（元数据操作）；
                       False = 为一次传输单独开一条 subsystem 通道（不缓存）。
                       paramiko 的 SFTPClient 不支持并发同步请求，传输与目录浏览
                       必须各用各的 client，否则响应可能被另一个线程读走。

        三层边界，避免「目录一直加载中」：

        1. 未认证直接报可读错误（不把未认证会话伪装成已连接）；
        2. `open_session` 有超时；
        3. `invoke_subsystem("sftp")` 由定时器兜底关闭 channel —— paramiko 的这个
           调用内部是无超时 `event.wait()`，服务端不回应会永久阻塞；
        4. 建立后的 SFTP 通道统一设置操作超时。

        :raises SshError: 链路未建立 / 未认证 / 远端 SFTP 子系统无响应
        """
        if self._closed:
            raise SshError("会话已关闭，请重新连接")
        if not self._transports:
            raise SshError("会话尚未建立（正在连接中，请稍候）")
        transport = self._transports[-1]
        if not transport.is_authenticated():
            raise AuthError(
                "SSH 认证尚未完成（服务器还要求继续认证），已停止打开 SFTP；"
                "请检查认证方式与登录密码，或在会话页按提示完成后续认证"
            )
        with self._sftp_open_lock:
            if cached and self._sftp_client is not None:
                return self._sftp_client
            chan: paramiko.Channel | None = None
            timed_out = threading.Event()
            try:
                chan = transport.open_session(timeout=CONNECT_TIMEOUT)
                chan.settimeout(SFTP_OP_TIMEOUT)
                self._sftp_opening_chan = chan

                def abort() -> None:
                    """到点仍未拿到子系统响应：关闭 channel 让阻塞的 wait 立即失败"""
                    timed_out.set()
                    try:
                        chan.close()
                    except Exception:  # noqa: BLE001 - 兜底关闭忽略异常
                        pass

                timer = threading.Timer(SFTP_OPEN_TIMEOUT, abort)
                timer.daemon = True
                timer.start()
                try:
                    chan.invoke_subsystem("sftp")
                except Exception as exc:
                    if timed_out.is_set():
                        raise SshError(
                            f"远端 SFTP 子系统无响应（超过 {SFTP_OPEN_TIMEOUT:g} 秒）。"
                            "请确认服务器启用了 sftp 子系统，或检查网络/跳板链路"
                        ) from exc
                    raise
                finally:
                    timer.cancel()
                client = paramiko.SFTPClient(chan)
                self._sftp_opening_chan = None
                if cached:
                    self._sftp_client = client
                else:
                    self._sftp_extra_clients.add(client)
                logger.info(
                    "SFTP 通道已打开（%s，超时 %.0f 秒）",
                    "会话级" if cached else "传输专用",
                    SFTP_OP_TIMEOUT,
                )
                return client
            except SshError:
                if chan is not None:
                    try:
                        chan.close()
                    except Exception:  # noqa: BLE001
                        pass
                raise
            except socket.timeout as exc:
                if chan is not None:
                    try:
                        chan.close()
                    except Exception:  # noqa: BLE001
                        pass
                raise SshError(
                    f"远端 SFTP 握手超时（超过 {SFTP_OP_TIMEOUT:g} 秒），服务器未响应"
                ) from exc
            except Exception as exc:  # noqa: BLE001 - 统一转成可读原因
                if chan is not None:
                    try:
                        chan.close()
                    except Exception:  # noqa: BLE001
                        pass
                if timed_out.is_set():
                    raise SshError(
                        f"远端 SFTP 子系统无响应（超过 {SFTP_OPEN_TIMEOUT:g} 秒）"
                    ) from exc
                raise SshError(f"无法打开 SFTP 通道：{exc}") from exc
            finally:
                self._sftp_opening_chan = None

    def release_sftp(self, client: paramiko.SFTPClient) -> None:
        """传输结束：从追踪集合移除专用 client（关闭由调用方负责）"""
        self._sftp_extra_clients.discard(client)

    def drop_sftp(self) -> None:
        """
        丢弃会话级 SFTP client（取消加载 / 超时 / 协议出错后调用）。

        必须在会话侧也清掉缓存：桥里 `_sftp_clients` 只是副本，若只关桥里的那把，
        下一次 `open_sftp()` 会拿到已关闭的同一个 client，表现为「点刷新也没反应」。
        """
        opening = self._sftp_opening_chan
        if opening is not None:
            # 首次打开还没完成：关掉 channel 让 invoke_subsystem/SFTPClient 立刻失败
            try:
                opening.close()
            except Exception:  # noqa: BLE001
                pass
        client = self._sftp_client
        self._sftp_client = None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - 丢弃阶段忽略异常
                pass

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
        """
        完成认证（支持服务器多步/多因素认证）。

        paramiko 的关键语义：`auth_publickey` / `auth_password` / `auth_interactive`
        在**部分认证通过**时不会抛异常，而是**返回本轮仍允许的认证方式列表**；
        只有 `transport.is_authenticated()` 为真才算真正登录成功。

        本方法按服务器返回的列表继续推进（密钥 → 密码 / 键盘交互 / 再补密钥），
        结束时硬校验 `is_authenticated()` —— 未完成绝不返回，避免会话把
        「未认证」误报成「已连接」。
        """
        username = str(row.get("username") or "")
        auth_type = str(row.get("auth_type") or "password")
        password = str(row.get("password") or "")
        key: paramiko.PKey | None = None
        if auth_type in ("publickey", "password_publickey"):
            key = load_private_key(
                str(row.get("private_key") or ""), str(row.get("passphrase") or "")
            )
        attempts: dict[str, int] = {}
        try:
            if key is not None:
                status, allowed = self._attempt_auth(
                    transport, "publickey", username, key=key
                )
                attempts["publickey"] = 1
                initial_allowed = set(allowed)
                if status == "rejected":
                    # BadAuthenticationType 的语义就是「这个方式被拒」，别再原地重试
                    allowed.discard("publickey")
                if transport.is_authenticated():
                    return
                if not allowed:
                    # 裸失败（服务器没给方法列表）：有密码就退回密码，保持旧兼容
                    if password and status == "failed":
                        status, allowed = self._attempt_auth(
                            transport, "password", username, password=password
                        )
                        attempts["password"] = 1
                        if transport.is_authenticated():
                            return
                    elif initial_allowed and not (
                        initial_allowed & {"password", "keyboard-interactive"}
                    ):
                        # 服务器明确只允许密钥（或其它非密码方式）：不要无谓再试密码
                        raise _auth_hint(initial_allowed, "认证失败：密钥不被服务器接受")
                    elif not password:
                        raise AuthError("认证失败：密钥不被服务器接受")
                    else:
                        status, allowed = self._attempt_auth(
                            transport, "password", username, password=password
                        )
                        attempts["password"] = 1
                        if transport.is_authenticated():
                            return
            else:
                status, allowed = self._attempt_auth(
                    transport, "password", username, password=password
                )
                attempts["password"] = 1
                if status == "rejected":
                    allowed.discard("password")
                if transport.is_authenticated():
                    return
                if not allowed and auth_type != "password_keyboard":
                    raise AuthError("认证失败：用户名或密码错误")
                if not allowed and auth_type == "password_keyboard":
                    raise AuthError("认证失败（密码或验证码错误）")
            allowed = self._continue_auth(
                transport, username, password, key, allowed, attempts
            )
            if not transport.is_authenticated():
                raise _auth_hint(
                    allowed, "认证未完成：服务器没有接受当前凭据，请检查认证方式与密码"
                )
            logger.info(
                "SSH 认证完成（auth_type=%s，方法=%s）",
                auth_type,
                "、".join(sorted(attempts)),
            )
        except SshError:
            raise
        except paramiko.AuthenticationException as exc:
            raise _auth_error(exc)
        except Exception as exc:  # noqa: BLE001 - 网络/协议异常统一转成可读原因
            raise AuthError(f"认证失败：{exc}")

    def _attempt_auth(
        self,
        transport: paramiko.Transport,
        method: str,
        username: str,
        *,
        key: paramiko.PKey | None = None,
        password: str = "",
    ) -> tuple[str, set[str]]:
        """
        执行一次认证方法尝试。

        :returns: `(状态, 服务器允许的下一轮方式)`；状态为
                  `ok`（通过/部分通过）、`rejected`（该方式不被允许）、`failed`（凭据错误）
        """
        try:
            if method == "publickey":
                remaining = transport.auth_publickey(username, key)
            elif method == "keyboard-interactive":
                remaining = transport.auth_interactive(
                    username, self._make_interactive_handler(username, password)
                )
            else:
                remaining = transport.auth_password(username, password)
        except PartialAuthentication as exc:  # 兼容抛出 partial 的旧实现
            return "ok", set(exc.allowed_types or ())
        except paramiko.BadAuthenticationType as exc:
            return "rejected", set(exc.allowed_types or ())
        except paramiko.AuthenticationException:
            return "failed", set()
        return "ok", set(remaining or ())

    def _continue_auth(
        self,
        transport: paramiko.Transport,
        username: str,
        password: str,
        key: paramiko.PKey | None,
        allowed: set[str],
        attempts: dict[str, int],
    ) -> set[str]:
        """按服务器允许的认证方式继续推进，直到认证完成、无路可走或超出轮次"""
        for _ in range(MAX_AUTH_ROUNDS):
            if transport.is_authenticated() or not allowed:
                break
            method = self._pick_next_method(allowed, password, key, attempts)
            if method is None:
                break
            attempts[method] = attempts.get(method, 0) + 1
            if method == "publickey":
                status, allowed = self._attempt_auth(
                    transport, method, username, key=key
                )
            else:
                status, allowed = self._attempt_auth(
                    transport, method, username, password=password
                )
            if status == "rejected":
                # 刚被拒的方式不再原地重试（由 allowed 列表决定下一步）
                allowed.discard(method)
        return allowed

    @staticmethod
    def _pick_next_method(
        allowed: set[str],
        password: str,
        key: paramiko.PKey | None,
        attempts: dict[str, int],
    ) -> str | None:
        """按优先级挑下一个认证方式（密码 → 键盘交互 → 密钥），并限制重试次数"""
        if password and "password" in allowed:
            if attempts.get("password", 0) < MAX_METHOD_ATTEMPTS["password"]:
                return "password"
        if "keyboard-interactive" in allowed:
            if (
                attempts.get("keyboard-interactive", 0)
                < MAX_METHOD_ATTEMPTS["keyboard-interactive"]
            ):
                return "keyboard-interactive"
        if key is not None and "publickey" in allowed:
            if attempts.get("publickey", 0) < MAX_METHOD_ATTEMPTS["publickey"]:
                return "publickey"
        return None

    def _make_interactive_handler(self, username: str, password: str):
        """
        构造键盘交互回调。

        服务器在「密钥通过后要求继续认证」时通常会问 `Password:` /
        `Further authentication required`，这正是 WinSCP 第二次弹窗的内容；
        有账号密码时直接代答，验证码类提示才弹 UI。
        """

        def handler(
            title: str, instructions: str, prompts: list[tuple[str, bool]]
        ) -> list[str]:
            answers: list[str] = []
            for prompt, _echo in prompts:
                answers.append(
                    self._answer_interactive_prompt(
                        prompt, username, password, title, instructions
                    )
                )
            return answers

        return handler

    def _answer_interactive_prompt(
        self,
        prompt: str,
        username: str,
        password: str,
        title: str = "",
        instructions: str = "",
    ) -> str:
        """按提示文本决定答案：用户名 / 账号密码 / 动态验证码（弹 UI）"""
        text = str(prompt or "").strip()
        lowered = text.lower()
        # 验证码类提示必须先判断：中文「动态密码」不能被当成账号密码代答
        code_keywords = (
            "verification", "verify", "code", "otp", "token", "challenge",
            "动态", "验证码", "一次性", "口令卡",
        )
        if any(marker in lowered for marker in code_keywords):
            return self._ask_interactive(text or instructions or title)
        if any(marker in lowered for marker in ("username", "user name", "login as")):
            return username
        if password and any(
            marker in lowered for marker in ("password", "passwd", "密码", "passphrase")
        ):
            return password
        if password and len(text) == 0:
            return password
        return self._ask_interactive(text or instructions or title)

    def _ask_interactive(self, prompt: str) -> str:
        """把服务器提示交给渲染层输入（超时/未回答返回空串，认证会继续失败并可重试）"""
        if self.otp_ask is None:
            return ""
        message = str(prompt or "").strip() or "服务器要求继续认证"
        try:
            return str(self.otp_ask(message) or "")
        except Exception as exc:  # noqa: BLE001 - UI 回调异常不拖垮认证线程
            logger.debug("键盘交互输入失败：%s", exc)
            return ""

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

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

#: 弹框索取账号密码的最多次数（首次 + 一次重试；避免无限弹框）
MAX_PASSWORD_PROMPTS = 2

#: 单次认证方法最多尝试次数（密钥/密码可能因服务器顺序被要求两遍）
#: publickey=3：首轮 + ssh-rsa 回退 + 「服务器部分认证后仍要 publickey」的补一轮
#: （部分旧网关第一次是算法不匹配、第二次才计数，Maverick 一类 Java SSH 服务端即是）
MAX_METHOD_ATTEMPTS = {"password": 2, "publickey": 3, "keyboard-interactive": 1}

#: `server-sig-algs` 暂存哨兵（区分「扩展不存在」与「扩展值为空」）
_LEGACY_EXT_UNSET = object()

#: 认证方式的中文名（错误提示用，不把枚举值直接抛给用户）
AUTH_TYPE_LABELS = {
    "password": "密码",
    "publickey": "密钥",
    "password_keyboard": "密码 + OTP",
    "password_publickey": "密码 + 密钥",
}


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


def _trace_text(trace: list[str] | None) -> str:
    """
    把认证序列转成可读文本（只含方法名，不含任何凭据）。

    :param trace: 按发送顺序记录的认证方式
    :returns: 形如 `publickey(ssh-rsa)→password`；空序列返回空串
    """
    items = [str(item) for item in (trace or []) if item]
    return "→".join(items)


def _auth_hint(
    allowed: set[str],
    fallback: str,
    *,
    auth_type: str = "",
    key_type: str = "",
    trace: list[str] | None = None,
    key_accepted: bool = False,
    interactive_empty: bool = False,
    other_factor_tried: bool = False,
) -> AuthError:
    """
    把服务器允许的认证方式转成可执行的中文提示。

    :param allowed: 服务器允许的认证方式集合（空集表示服务器未给出方法列表）
    :param fallback: 没有方法列表时的兜底文案
    :param auth_type: 当前配置的认证方式（用于区分「认证方式选错」与「私钥被拒」）
    :param key_type: 当前私钥的 SSH 类型（如 `ssh-rsa` / `ssh-ed25519`）
    :param trace: 实际发出的认证序列（用于让用户看到「发过什么、还差什么」）
    :param key_accepted: 本轮公钥是否已被服务器接受 —— 已接受时**绝不**再说「密钥被拒」，
                        否则用户会去反复换私钥（v1.10.18 修复：用户实测被这条文案误导）
    :param interactive_empty: 键盘交互弹框是否**没有收到输入**（超时 / 未点确定 / 取消）
    """
    if not allowed:
        return AuthError(fallback)
    allowed_text = "、".join(sorted(allowed))
    sent_text = _trace_text(trace)
    sent_part = f"本次已发送：{sent_text}；" if sent_text else ""
    if "password" in allowed:
        # 服务器同时列了 keyboard-interactive 且已试过：按协议继续推进即可，
        # 不应再让用户去改认证方式。
        interactive_sent = "keyboard-interactive" in (trace or [])
        if interactive_sent and interactive_empty:
            return AuthError(
                f"没有收到你输入的账号密码（服务器允许：{allowed_text}）。"
                f"{sent_part}"
                "服务器会弹框要求继续认证（SSH 密码）"
                "但本次弹框没有拿到输入（超时约 60 秒、或没点「确定」就关掉了）。"
                "请重新连接，在弹出的输入框里填**服务器账号登录密码**后点「确定」"
            )
        if interactive_sent:
            return AuthError(
                f"账号密码没有被服务器接受（服务器允许：{allowed_text}）。"
                f"{sent_part}"
                "本次由键盘交互（keyboard-interactive）收集口令，服务器仍未接受"
                "本次已弹出输入框并提交，但服务器仍拒绝 —— 请确认输入的是"
                "**服务器账号登录密码**（不是私钥口令），然后重新连接再输一次"
            )
        if auth_type in ("password", "password_publickey"):
            return AuthError(
                f"账号密码没有被服务器接受（服务器允许：{allowed_text}）。"
                f"{sent_part}"
                "最常见的原因是登录密码不对，"
                "请重新填写「密码」后重试；若这个网关的第二个因子是键盘交互验证码，"
                "把认证方式改为「密码 + OTP」"
            )
        return AuthError(
            f"服务器还要求账号密码认证（服务器允许：{allowed_text}）。{sent_part}"
            "请把**服务器登录密码**填到「密码」，认证方式选择「密码 + 密钥」"
        )
    if "keyboard-interactive" in allowed:
        return AuthError(
            f"服务器还要求继续认证（服务器允许：{allowed_text}）。{sent_part}"
            "请使用「密码 + 密钥」或「密码 + OTP」，连上后按提示补下一次认证；"
            "若提示里要的就是账号登录密码，把它填到「密码」即可自动代答"
        )
    if "publickey" in allowed:
        if key_accepted and other_factor_tried:
            return AuthError(
                "服务器已接受这把密钥，但**账号密码**没有被接受"
                f"（{sent_part}服务器当前的列表：{'、'.join(sorted(allowed))}）。"
                "私钥本身没有问题；请确认你填写的「密码」/弹框里输入的是"
                "**服务器账号登录密码**，然后再试一次"
            )
        if key_accepted and allowed == {"publickey"}:
            return AuthError(
                "服务器已接受这把密钥，但仍要求继续用密钥认证"
                f"（{sent_part}服务器当前只列出 publickey）。"
                "这通常表示服务器还需要**另一把**私钥（即需要两把不同私钥）；"
                "当前每条连接只支持配置一把私钥，请与服务器管理员确认"
                "该账号是否绑定了第二把密钥，或改用跳板"
            )
        if key_accepted:
            # 密钥这一步**已经通过**（后面还发过密码 / 键盘交互）：服务器现在又列出
            # publickey 是它自己的事，用户的凭据问题不在私钥上。绝不能再劝他换私钥。
            if interactive_empty:
                return AuthError(
                    f"没有收到你输入的账号密码（服务器现在要求：{allowed_text}）。"
                    f"{sent_part}"
                    "私钥本身没有问题；这台网关会弹框要求继续认证，"
                    "但本次弹框没有拿到输入（超时约 60 秒、或没点「确定」就关掉了）。"
                    "请重新连接，在弹出的输入框里填**服务器账号登录密码**后点「确定」"
                )
            return AuthError(
                f"服务器已接受密钥，但后续认证没有通过（服务器现在要求：{allowed_text}）。"
                f"{sent_part}"
                "私钥本身没有问题；请确认你在弹框 / 密码栏里填的是**服务器账号登录密码**。"
                "若这台网关确实要求第二把不同私钥，当前每条连接只支持一把，"
                "需在服务器侧确认或改用跳板"
            )
        if auth_type and auth_type not in ("publickey", "password_publickey"):
            current = AUTH_TYPE_LABELS.get(auth_type, auth_type)
            return AuthError(
                f"该服务器只接受密钥认证（服务器允许：{allowed_text}；"
                f"当前认证方式是「{current}」）。请把认证方式改为「密钥」或"
                "「密码 + 密钥」，并在表单里填入私钥"
            )
        type_hint = ""
        if key_type and key_type != "ssh-rsa":
            type_hint = (
                f"当前私钥类型是 {key_type}，旧服务器可能只接受 RSA 密钥；"
            )
        return AuthError(
            f"服务器未接受当前密钥（私钥；服务器允许：{allowed_text}）。"
            f"{sent_part}{type_hint}"
            "请确认该账号的 authorized_keys 已加入这把公钥、私钥口令正确；"
            "旧服务器可能只支持 ssh-rsa（本版已自动兼容）"
        )
    return AuthError(
        f"服务器不接受当前认证方式（服务器允许：{allowed_text}）。{sent_part}"
    )


def _auth_error(
    exc: paramiko.AuthenticationException,
    *,
    auth_type: str = "",
    key_type: str = "",
    trace: list[str] | None = None,
) -> AuthError:
    """把 paramiko 认证异常转成可读中文（带服务器允许的认证方式）"""
    # paramiko 5.x：BadAuthenticationType 与 PartialAuthentication 是**平级**的两种异常
    # （partial 不继承 BadAuthenticationType），两者都带 `allowed_types`
    allowed_types = {str(item) for item in (getattr(exc, "allowed_types", None) or ())}
    if allowed_types:
        return _auth_hint(
            allowed_types,
            str(exc),
            auth_type=auth_type,
            key_type=key_type,
            trace=trace,
        )
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
            "这是 PuTTY 私钥（.ppk）。请改用原始 PEM 文件"
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
        #: 键盘交互弹框是否**没有拿到输入**（超时 / 未点确定 / 取消）。
        #: 用于把「你输入的密码不对」和「弹框根本没收到输入」区分开 ——
        #: 二者在服务器眼里都是认证失败，但对用户的下一步完全不同。
        self._interactive_empty = False

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
            except (HostKeyError, AuthError) as exc:
                self.close()
                # 多跳链路必须让用户知道是哪一跳失败：认证/指纹错误带上「跳板 N / 目标 + 主机」
                message = f"{label} {row['host']}:{row['port']}：{exc}"
                if isinstance(exc, HostKeyError):
                    raise HostKeyError(message) from exc
                raise AuthError(message) from exc
            except paramiko.SSHException as exc:
                self.close()
                raise JumpError(
                    self._explain_negotiation_error(label, row, exc)
                ) from exc
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

    def _explain_negotiation_error(
        self, label: str, row: dict, exc: paramiko.SSHException
    ) -> str:
        """把 SSH 算法协商失败翻译成可执行中文（不泄露凭据）"""
        text = str(exc)
        host = row.get("host")
        if "no acceptable host key" in text:
            return (
                f"{label} {host} 连接失败：服务器只提供了当前客户端不支持的 SSH "
                "主机密钥算法（常见于只启用 ssh-dss 等已淘汰算法的旧服务器）。"
                "本版已恢复 ssh-rsa/RSA-SHA1 兼容；如仍失败，请联系服务器管理员"
                "为服务器启用 RSA/Ed25519 主机密钥"
            )
        if "no acceptable kex algorithm" in text:
            return (
                f"{label} {host} 连接失败：服务器只提供了已淘汰的 SHA-1 密钥交换算法。"
                "本版已启用 diffie-hellman-group14-sha1 兼容；如仍失败，请联系管理员"
                "启用 group14-sha256 或 ECDH"
            )
        if "can't match requested host key type" in text:
            return (
                f"{label} {host} 连接失败：服务器主机密钥类型与已记录的指纹不匹配。"
                "请清理该连接的主机指纹后重连，并核对新指纹"
            )
        return f"{label} {host} 连接失败：{exc}"

    def _verify_host_key(self, transport: paramiko.Transport, row: dict) -> None:
        """TOFU：首连请用户确认；已记录则严格比对"""
        server_key = transport.get_remote_server_key()
        actual = fetch_fingerprint(server_key)
        logger.info(
            "SSH 协商完成：远端=%s 主机密钥=%s KEX=%s",
            getattr(transport, "remote_version", "未知"),
            server_key.get_name(),
            getattr(getattr(transport, "kex_engine", None), "name", "未知"),
        )
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
        完成 SSH 用户认证（**严格按 RFC 4252 的 authlist 推进**）。

        标准流程与 OpenSSH / 主流客户端一致：

        1. 先发一次 `none` 取服务器的**初始方法列表**（`SSH_MSG_USERAUTH_FAILURE`
           里的 name-list 是下一步该用什么方法的唯一权威）；
        2. 每一轮取「服务器列出的方法」∩「本地能提供的能力」，挑一个发送；
        3. 服务器回 `partial success` 时继续下一轮，直到 `is_authenticated()`。

        **绝不发送服务器没有列出的方法**。本地缺凭据时（例如只配了私钥、
        服务器却要求账号密码）应当**弹框向用户索取**，再用服务器列出的那个方法发送 ——
        这是所有标准客户端的做法，而不是改认证方式或改用别的方法试探。

        :param transport: 已完成密钥交换的传输层
        :param row: 连接配置（含 auth_type / password / private_key / passphrase）
        :raises AuthError: 认证无法完成（文案会说明缺哪一步、服务器当前列了什么）
        """
        username = str(row.get("username") or "")
        auth_type = str(row.get("auth_type") or "password")
        stored_password = str(row.get("password") or "")
        key: paramiko.PKey | None = None
        if auth_type in ("publickey", "password_publickey"):
            key = load_private_key(
                str(row.get("private_key") or ""), str(row.get("passphrase") or "")
            )

        #: 实际发出的认证方法序列（诊断行用，**只含方法名**，不含任何凭据）
        trace: list[str] = []
        #: 本轮公钥是否已被服务器接受（决定报错时能不能说「私钥被拒」）
        key_accepted = False
        #: 公钥已发送次数（受 MAX_METHOD_ATTEMPTS 限制：既支持旧网关「同一把钥
        #: 连发两次签名」，又不会无限重发）
        key_attempts = 0
        #: 上一轮被服务器拒绝（BadAuthenticationType）的方法：只跳过下一轮
        skip_this_round: set[str] = set()
        #: 弹框索取过的一次性密码（只在本连接内复用，不落库）
        prompted_password = ""
        #: 已经向用户要过几次密码（首次 + 一次重试，避免无限弹框）
        password_prompts = 0
        #: 已尝试次数（防止服务器反复列同一方法导致死循环）
        attempts: dict[str, int] = {}
        #: 存储的密码是否还能用（错一次就不再原地重打，改由弹框重填）
        stored_password_usable = bool(stored_password)
        #: 是否试过「除密钥以外」的因子（密码 / 键盘交互）
        other_factor_tried = False
        #: 是否**真的发出过**非空密码（用于区分「密码被拒」与「没收到输入」）
        password_sent = False
        # 起始诊断：先用协商事实兜底，公钥下发后会被更详细的摘要覆盖
        diag = self._peer_facts(transport)

        try:
            allowed, already_authenticated = self._probe_methods(transport, username)
            if already_authenticated:
                return
            if not allowed:
                # 取不到列表（老实现 / 测试替身）：按本地凭据猜首个方法，后续以服务器返回为准
                allowed = self._guess_initial_methods(key, stored_password, auth_type)

            for _ in range(MAX_AUTH_ROUNDS):
                if transport.is_authenticated():
                    break
                usable = self._usable_methods(
                    allowed,
                    key=key,
                    attempts=attempts,
                    key_accepted=key_accepted,
                    other_factor_tried=other_factor_tried,
                    has_password=bool(
                        (stored_password and stored_password_usable) or prompted_password
                    ),
                    can_prompt=password_prompts < MAX_PASSWORD_PROMPTS,
                )
                # 刚被拒的发放只跳过这一轮（服务器之后若重新列出，仍可再试）
                usable -= skip_this_round
                skip_this_round = set()
                if not usable:
                    break
                method = self._choose_method(usable, key_attempts=key_attempts)

                # 服务器要 password 但本地没有：弹框索取（拿到的值按 password 方法发送）
                needs_password_prompt = method == "password" and not prompted_password and not (
                    stored_password and stored_password_usable
                )
                if needs_password_prompt:
                    prompted_password = self._ask_password()
                    password_prompts += 1
                    if not prompted_password and "keyboard-interactive" in usable:
                        # 服务器同时列出了 keyboard-interactive：改走它（同样会按 prompt 索取）
                        method = "keyboard-interactive"
                    elif not prompted_password:
                        hint = (
                            "账号密码没有被服务器接受"
                            if password_sent
                            else "没有收到你输入的账号密码"
                        )
                        raise self._fail_auth(
                            AuthError(
                                f"{hint}（服务器允许：{'、'.join(sorted(allowed))}）。"
                                "服务器要求继续认证（SSH 密码）"
                                + (
                                    "，本次提交的账号密码被拒绝，请确认填的是"
                                    "**服务器账号登录密码**（不是私钥口令）"
                                    if password_sent
                                    else "，但弹框没有拿到输入（超时约 60 秒、"
                                    "或没点「确定」就关掉了）。请重新连接，"
                                    "在弹出的输入框里填**服务器账号登录密码**"
                                ),
                            ),
                            self._finish_diag(diag, trace, allowed),
                        )

                secret = self._auth_secret(
                    method,
                    stored_password=stored_password,
                    stored_password_usable=stored_password_usable,
                    prompted_password=prompted_password,
                )
                if method == "password" and secret:
                    password_sent = True
                status, next_allowed = self._attempt_auth(
                    transport,
                    method,
                    username,
                    key=key if method == "publickey" else None,
                    # password 方法只用「仍然可信」的那份（存储的错一次就不重打）；
                    # keyboard-interactive 仍可以用存储的密码去应答它的 prompt
                    password=secret,
                )
                attempts[method] = attempts.get(method, 0) + 1
                trace.append(method)
                if method != "publickey":
                    other_factor_tried = True
                if method == "publickey":
                    key_attempts += 1
                    key_accepted = status == "ok"
                    if not transport.is_authenticated():
                        diag = self._auth_diag(transport, key, status, next_allowed)
                        # 旧服务器只认 ssh-rsa（RSA-SHA1）时再补一次签名；
                        # 服务器已给出下一步方法（说明密钥这步已过）时绝不回退。
                        if self._can_retry_legacy_rsa(
                            transport, key, status, next_allowed
                        ):
                            logger.info(
                                "公钥认证未通过（%s），尝试旧式 ssh-rsa 签名（远端版本：%s）",
                                status,
                                getattr(transport, "remote_version", "未知"),
                            )
                            status, next_allowed = self._attempt_legacy_rsa(
                                transport, username, key
                            )
                            attempts["publickey"] = attempts.get("publickey", 0) + 1
                            trace.append("publickey(ssh-rsa)")
                            diag += f"；ssh-rsa 回退={status}"
                            key_attempts += 1
                            key_accepted = status == "ok"
                    if status == "rejected":
                        next_allowed.discard("publickey")

                if transport.is_authenticated():
                    break

                # 密码被拒：存储的那份不再重打（改由弹框重填），弹框的也清掉允许再问一次
                if method == "password" and status in ("failed", "rejected"):
                    if stored_password and not prompted_password:
                        stored_password_usable = False
                    prompted_password = ""
                if status == "rejected":
                    # 该方式不被允许：以服务器给出的列表为准（即使为空也不沿用旧列表）
                    next_allowed.discard(method)
                    allowed = next_allowed
                    skip_this_round.add(method)
                elif next_allowed:
                    # 成功 / 部分成功：服务器给出的下一步列表就是权威
                    allowed = next_allowed
                # 普通失败（凭据错）时 paramiko 不回列表，沿用上一轮已知集合

            if not transport.is_authenticated():
                raise self._fail_auth(
                    _auth_hint(
                        allowed,
                        "认证未完成：服务器没有接受当前凭据，请检查认证方式与密码",
                        auth_type=auth_type,
                        key_type=key.get_name() if key is not None else "",
                        trace=trace,
                        key_accepted=key_accepted,
                        interactive_empty=self._interactive_empty,
                        other_factor_tried=other_factor_tried,
                    ),
                    self._finish_diag(diag, trace, allowed),
                )
            logger.info(
                "SSH 认证完成（auth_type=%s，方法=%s，序列=%s，密钥类型=%s，远端=%s，主机密钥=%s）",
                auth_type,
                "、".join(sorted(attempts)),
                "→".join(trace),
                key.get_name() if key is not None else "-",
                getattr(transport, "remote_version", "未知"),
                self._remote_host_key_type(transport),
            )
        except SshError:
            raise
        except paramiko.AuthenticationException as exc:
            raise self._fail_auth(
                _auth_error(
                    exc,
                    auth_type=auth_type,
                    key_type=key.get_name() if key is not None else "",
                    trace=trace,
                ),
                self._finish_diag(diag, trace, set()),
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 网络/协议异常统一转成可读原因
            raise self._fail_auth(
                AuthError(f"认证失败：{exc}"),
                self._finish_diag(diag, trace, set()),
            ) from exc
        finally:
            # 旧式 ssh-rsa 模式只在本轮认证内有效，成功/失败都要恢复
            self._exit_legacy_rsa(transport)

    @staticmethod
    def _auth_secret(
        method: str,
        *,
        stored_password: str,
        stored_password_usable: bool,
        prompted_password: str,
    ) -> str:
        """
        给出本次尝试要用的密码 / 交互应答口令。

        - `password` 方法：存储的密码**错一次就不再重打**（改由弹框重填），
          弹框拿到的一次性密码优先；
        - `keyboard-interactive`：仍然用存储的密码去应答它的 prompt
          （服务器可能只接受同一个口令的另一条认证路径）；
        - `publickey`：不带密码。
        """
        if method == "publickey":
            return ""
        if method == "keyboard-interactive":
            return prompted_password or stored_password
        stored = stored_password if stored_password_usable else ""
        return prompted_password or stored

    def _probe_methods(
        self, transport: paramiko.Transport, username: str
    ) -> tuple[set[str], bool]:
        """
        用一次 `none` 请求取服务器的**初始认证方法列表**（RFC 4252 §5.2）。

        这是标准做法（OpenSSH 同样先发 `none`）：只有服务器给出的 name-list
        才决定下一步该用哪种方法，客户端不应猜测。

        :returns: `(服务器列出的方法, 是否已经认证通过)`
                  老实现 / 测试替身没有 `auth_none` 时返回 `(set(), False)`
        """
        probe = getattr(transport, "auth_none", None)
        if probe is None:
            return set(), False
        try:
            probe(username)
        except paramiko.BadAuthenticationType as exc:
            return set(exc.allowed_types or ()), False
        except paramiko.AuthenticationException:
            return set(), False
        except Exception as exc:  # noqa: BLE001 - 探测失败不应中断认证
            logger.debug("取初始认证方法列表失败：%s", exc)
            return set(), False
        return set(), bool(transport.is_authenticated())

    @staticmethod
    def _guess_initial_methods(
        key: paramiko.PKey | None, password: str, auth_type: str
    ) -> set[str]:
        """取不到服务器列表时的首个方法（只用于兼容老实现；之后以服务器返回为准）"""
        guess: set[str] = set()
        if key is not None:
            guess.add("publickey")
        if password or auth_type in ("password", "password_keyboard", "password_publickey"):
            guess.add("password")
        return guess

    @staticmethod
    def _usable_methods(
        allowed: set[str],
        *,
        key: paramiko.PKey | None,
        attempts: dict[str, int] | None = None,
        key_accepted: bool = False,
        other_factor_tried: bool = False,
        has_password: bool = False,
        can_prompt: bool = True,
    ) -> set[str]:
        """
        取「服务器列出」∩「本地能做」的方法集合。

        - `publickey`：有私钥、未用完尝试上限；**已被接受的钥不再重发**，
          除非服务器当前只列 publickey 且还没试过其它因子（旧网关「补一次签名」）
        - `password`：有已存/已弹框得到的密码，或仍允许弹框索取
        - `keyboard-interactive`：服务器列出即可（回调能应答 / 弹框）
        """
        tried = attempts or {}
        usable: set[str] = set()
        if (
            "publickey" in allowed
            and key is not None
            and tried.get("publickey", 0) < MAX_METHOD_ATTEMPTS["publickey"]
            and (
                not key_accepted
                or (allowed == {"publickey"} and not other_factor_tried)
            )
        ):
            usable.add("publickey")
        if (
            "password" in allowed
            and (has_password or can_prompt)
            and tried.get("password", 0) < MAX_METHOD_ATTEMPTS["password"]
        ):
            usable.add("password")
        if (
            "keyboard-interactive" in allowed
            and tried.get("keyboard-interactive", 0)
            < MAX_METHOD_ATTEMPTS["keyboard-interactive"]
        ):
            usable.add("keyboard-interactive")
        return usable

    @staticmethod
    def _choose_method(usable: set[str], *, key_attempts: int = 0) -> str:
        """
        在服务器已列出的方法里挑一个发送。

        - 还没发过公钥时，优先公钥（最强的凭据）；
        - 公钥已经试过（失败或已通过）时，先补齐其它因子，不在原地重复同一凭据；
        - 都没有时，才回头再发公钥（旧网关可能要求同一把钥补一次签名）。
        """
        if key_attempts == 0 and "publickey" in usable:
            return "publickey"
        for method in ("password", "keyboard-interactive"):
            if method in usable:
                return method
        if "publickey" in usable:
            return "publickey"
        raise AssertionError("usable 为空时不应调用 _choose_method")

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
                handler = getattr(transport, "auth_interactive", None)
                if handler is None:
                    raise paramiko.SSHException(
                        "当前 SSH 实现不支持键盘交互认证"
                    )
                remaining = handler(
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

    def _fail_auth(self, exc: AuthError, diag: str) -> AuthError:
        """
        给认证失败补上可复制的诊断行（不含任何凭据），并写一条 INFO 日志。

        :param exc: 面向用户的认证错误
        :param diag: 协商事实摘要（可为空）
        :returns: 追加了诊断行的新错误对象（调用方直接 raise）
        """
        if not diag:
            return exc
        logger.info("SSH 认证失败诊断：%s", diag)
        return AuthError(f"{exc}\n诊断：{diag}")

    @classmethod
    def _finish_diag(
        cls,
        diag: str,
        trace: list[str] | None,
        allowed: set[str] | None = None,
    ) -> str:
        """
        拼出最终诊断行：协商事实 + 私钥 + 首轮结果 + 实际认证序列 + 服务器当前要求。

        :param diag: 首轮公钥认证后生成的协商摘要（可能为空）
        :param trace: 实际发出的认证序列
        :param allowed: 服务器在最后一轮给出的下一步方式
        :returns: 可直接展示给用户的一行诊断（不含任何凭据）
        """
        parts = [diag] if diag else []
        sent = _trace_text(trace)
        if sent:
            parts.append(f"认证序列={sent}")
        if allowed:
            parts.append(f"服务器仍要求：{'、'.join(sorted(allowed))}")
        return "；".join(part for part in parts if part)

    @classmethod
    def _auth_diag(
        cls,
        transport: paramiko.Transport,
        key: paramiko.PKey,
        status: str,
        allowed: set[str],
    ) -> str:
        """首轮公钥认证后的诊断摘要（远端版本/主机密钥/KEX/签名算法/私钥/结果）"""
        parts = [cls._peer_facts(transport)]
        bits = getattr(key, "get_bits", None)
        key_text = key.get_name()
        if callable(bits):
            key_text += f"({bits()}位)"
        parts.append(f"私钥={key_text}")
        result = f"首轮结果={status}"
        if allowed:
            result += f"（服务器允许：{'、'.join(sorted(allowed))}）"
        parts.append(result)
        return "；".join(parts)

    @staticmethod
    def _peer_facts(transport: paramiko.Transport) -> str:
        """
        收集 SSH 协商事实（可安全外传，不含凭据）。

        测试替身/协商未完成时字段缺失，一律退化成 `-`，绝不抛异常。
        """
        remote = str(getattr(transport, "remote_version", "") or "未知")
        try:
            host_key = transport.get_remote_server_key().get_name()
        except Exception:  # noqa: BLE001 - 测试替身或握手未完成
            host_key = "-"
        kex = str(getattr(getattr(transport, "kex_engine", None), "name", "") or "-")
        extensions = getattr(transport, "server_extensions", {}) or {}
        sig = extensions.get("server-sig-algs", b"") if isinstance(extensions, dict) else b""
        if isinstance(sig, bytes):
            sig = sig.decode("ascii", "ignore")
        agreed = str(getattr(transport, "_agreed_pubkey_algorithm", "") or "-")
        return (
            f"远端={remote}；主机密钥={host_key}；KEX={kex}；"
            f"服务器签名算法={str(sig) or '未提供'}；本次签名算法={agreed}"
        )

    @staticmethod
    def _remote_host_key_type(transport: paramiko.Transport) -> str:
        """取主机密钥类型（测试替身没有该方法时返回 `-`，只用于日志）"""
        getter = getattr(transport, "get_remote_server_key", None)
        if not callable(getter):
            return "-"
        try:
            return getter().get_name()
        except Exception:  # noqa: BLE001 - 日志字段失败不影响认证
            return "-"

    @staticmethod
    def _can_retry_legacy_rsa(
        transport: paramiko.Transport,
        key: paramiko.PKey,
        status: str,
        allowed: set[str] | None = None,
    ) -> bool:
        """
        判断是否值得用旧式 `ssh-rsa`（RSA-SHA1）再补一次公钥认证。

        只在「真实 paramiko transport + RSA 私钥 + 本轮未完成认证」时启用：

        - 现代服务器第一次就会成功，不会走到这里；
        - `server-sig-algs` **不作为门槛**：部分旧网关把扩展写成 `rsa-sha2-*`
          （甚至写全），实际却只接受 RSA-SHA1 签名。失败后多试一次没有副作用，
          真正的凭据错误仍会被拒绝，只是多一次认证往返；
        - **服务器要的是别的方式（`allowed` 里出现非 publickey 项）时绝不回退**：
          那说明这把密钥已经被服务器接受，只差下一个因子（典型是「允许：password」）。
          此时再发一次 publickey，会被网关当成「还差一把密钥」，把「请输入账号密码」
          误报成「请提供第二把私钥」——v1.10.15 就是在这里多发了一次（v1.10.16 修正）。
        - 已经进入旧式模式后不再重复进入（避免每轮多打一次公钥请求）；
        - `rejected`（服务器不允许该方式）、transport 已断开、非 RSA 私钥不重试。

        :param status: 本轮公钥认证状态（`ok` / `failed` / `rejected`）
        :param allowed: 服务器在本轮给出的下一步认证方式（空集表示没给）
        """
        if status == "rejected":
            return False
        if allowed and not allowed <= {"publickey"}:
            # 服务器还要密码 / 键盘交互等：密钥这一步已过，绝不能再发公钥
            return False
        if getattr(transport, "_jflove_legacy_backup", None) is not None:
            # 已经是 ssh-rsa 模式，再「进入」只会多打一次公钥请求
            return False
        if not isinstance(key, paramiko.RSAKey):
            return False
        if not isinstance(transport, paramiko.Transport):
            return False
        if transport.is_authenticated() or not transport.is_active():
            return False
        return True

    def _attempt_legacy_rsa(
        self, transport: paramiko.Transport, username: str, key: paramiko.PKey
    ) -> tuple[str, set[str]]:
        """
        进入「只发 ssh-rsa（RSA-SHA1）签名」模式，并用它做一次公钥认证。

        进入后**一直保持到本次认证流程结束**（`_exit_legacy_rsa` 恢复），不是只发一次：
        一部分旧网关（Maverick 一类 Java SSH 服务端）把第一次签名算法不匹配算作一次失败，
        第二次才真正验签；如果第二次成功后还要求补一轮 publickey，
        第补这一轮也必须继续用 ssh-rsa，否则又会被同样的算法检查打回。

        同时摘掉 `server-sig-algs`：paramiko 的 `_finalize_pubkey_algorithm`
        会先按该扩展求交集，扩展里没有 `ssh-rsa` 时它会抛
        「Unable to agree on a pubkey algorithm」—— 即使我们已经指定了 ssh-rsa。
        摘掉后 paramiko 退回「用本端首选算法」分支，正好拿到 `ssh-rsa`。
        """
        self._enter_legacy_rsa(transport)
        return self._attempt_auth(transport, "publickey", username, key=key)

    @staticmethod
    def _enter_legacy_rsa(transport: paramiko.Transport) -> None:
        """把连接切到 ssh-rsa 签名模式（幂等；原始算法表/扩展暂存，认证结束恢复）"""
        if getattr(transport, "_jflove_legacy_backup", None) is not None:
            return
        extensions = getattr(transport, "server_extensions", None)
        hidden = _LEGACY_EXT_UNSET
        if isinstance(extensions, dict) and "server-sig-algs" in extensions:
            hidden = extensions.pop("server-sig-algs")
        transport._jflove_legacy_backup = (transport._preferred_pubkeys, hidden)
        transport._preferred_pubkeys = ("ssh-rsa",)

    @staticmethod
    def _exit_legacy_rsa(transport: paramiko.Transport) -> None:
        """恢复进入旧式模式前的算法表与 `server-sig-algs` 扩展"""
        backup = getattr(transport, "_jflove_legacy_backup", None)
        if backup is None:
            return
        previous, hidden = backup
        transport._preferred_pubkeys = previous
        extensions = getattr(transport, "server_extensions", None)
        if hidden is not _LEGACY_EXT_UNSET and isinstance(extensions, dict):
            extensions["server-sig-algs"] = hidden
        transport._jflove_legacy_backup = None

    def _make_interactive_handler(self, username: str, password: str):
        """
        构造键盘交互回调。

        服务器在「密钥通过后要求继续认证」时通常会给 `Password:` 之类的 prompt；
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

    def _ask_password(self, prompt: str = "") -> str:
        """
        弹框向用户索取**服务器账号登录密码**（服务器列出 `password` 而本地没有时）。

        标准客户端（OpenSSH 等）在服务器要求 `password` 而客户端未配密码时，
        都会本地弹框索取，然后用 `password` 方法把用户输入发给服务器。
        这里复用会话的提示回调，拿到空串时记 `_interactive_empty`（文案据此区分
        「没收到输入」与「输入被拒绝」）。

        :param prompt: 自定义提示语；空则用默认文案
        :returns: 用户输入；未配置回调 / 超时 / 取消时返回空串
        """
        message = prompt or "服务器要求账号密码认证（SSH password），请输入登录密码"
        answer = self._ask_interactive(message)
        return answer

    def _ask_interactive(self, prompt: str) -> str:
        """
        把服务器提示交给渲染层输入（超时/未回答返回空串，认证会继续失败并可重试）。

        空输入会记到 `_interactive_empty`：认证失败时据此告诉用户「弹框没收到输入」，
        而不是笼统地说「密码不对」或「密钥被拒」。
        """
        if self.otp_ask is None:
            self._interactive_empty = True
            return ""
        message = str(prompt or "").strip() or "服务器要求继续认证"
        try:
            answer = str(self.otp_ask(message) or "")
        except Exception as exc:  # noqa: BLE001 - UI 回调异常不拖垮认证线程
            logger.debug("键盘交互输入失败：%s", exc)
            answer = ""
        if not answer:
            self._interactive_empty = True
        return answer

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

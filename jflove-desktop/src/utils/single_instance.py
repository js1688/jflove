"""
跨进程单实例守卫（`QLocalServer` / `QLocalSocket`）

对应设计文档 `文档记录/设计文档/v1.6.0.md`：

  * §6.6.1 选型与协议（行分隔 UTF-8 JSON、服务名、陈旧 socket 兜底、连接失败降级）
  * §6.6.3 `main.py` 的最小改动落点（判定时机 = `build_app()` 之后、`WebShell()` 之前）
  * §6.5   窗口退出与单实例的交互顺序（第二实例**不建窗口、不建托盘**，直接 `return 0`）
  * §7.1   事件表 #1 `editor.openPaths`（本模块只负责把路径交到**接入点**，
           见 `SingleInstanceGuard.set_paths_sink`，渲染层建窗格由后续的 `bridge/editor.py` 承担）

## 一处**带实测证据**的设计修订（不是实现走样）

设计 §6.6.1 原定"用 `listen()` 的返回值做唯一仲裁"。本机实测（PySide6 6.11.0 / Qt 6.11.0 /
Windows 11）证明该仲裁**在 Windows 上不成立**：

    A（首进程）        : listen(NAME) → True   isListening=True  errorString=""
                         serverError=UnknownSocketError
    B（A 存活期间同名）: listen(NAME) → True   isListening=True  errorString=""
                         serverError=UnknownSocketError   ← 本该 False（AddressInUse）
    且成功与失败时 errorString() / serverError() **同值**，均不可作判据

若照字面实现，Windows 上"第二个实例"会自认首实例 ⇒ 两个窗口 + 两个托盘图标，
正是本任务要消灭的东西。

**证据来源（可复现，仓库内脚本 + 原始输出）**：
  * 脚本 `poc/editor-probes/r2-local-server-arbitration.py`
  * 原始输出 `poc/editor-probes/out/r2-arbitration.json` / `out/r2-arbitration.log`
  * 复现命令（工作目录 `jflove-desktop`）：
    `.\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r2-local-server-arbitration.py`

> 编排者说明（2026-10-14）：本模块初稿的这段注释曾引用 `out/r2-exp2-b.log` 等文件，
> 而当时这些文件**并不存在**（属"引用了未产出的证据"）。已由编排者复跑并改为指向上述
> **真实存在**的产物；结论（仲裁不成立）经独立复现**成立**。
> 纪律依据：`.agents/skills/lessons/LESSONS.md` —— 汇总数字/证据必须能被仓库内脚本复现。

故把**仲裁者**换成"连接探测优先"，其余设计口径
（`QLocalServer`/`QLocalSocket`、行分隔 JSON、陈旧 socket 兜底、连接失败降级为本地启动）
**一条未改**：

    ① 连接探测：能连上 ⇒ 已有首实例 ⇒ 本进程是第二实例。
       ⚠ 这一步**绝不** `removeServer`：Linux 上它会摘掉正在运行实例的 socket 文件
    ② 竞态窗口：短暂停顿后再探一次（覆盖"首实例正在启动、尚未 listen"）
    ③ `listen()`；失败 ⇒ `removeServer` + **重试一次**（陈旧 socket 兜底，§6.6.1）
    ④ Windows 补一道**自探测**：连自己刚监听的名字；若连接没落到**自己的** server
       （被更早的同名实例接走）⇒ 本进程实为第二实例：关掉自己的监听去转发

## 协议（行分隔 JSON，每条消息一行，UTF-8）

    → 第二实例：{"v":1,"action":"open","paths":[...],"cwd":"…","pid":1234}\\n
    ← 首实例　：{"v":1,"action":"ack","ok":true,"accepted":2,"pid":5678}\\n

`ack` 行是对设计 §6.6.1 的**加法**（原设计只有单向"写完即关"）：实测 `waitForBytesWritten()`
在数据其实已送达时仍可能返回 `False`（`out/r2-exp2-b.log` 的 `B_PROBE_WRITTEN ok=False`
而 A 收到了该行），单向语义无法区分"已送达"与"静默丢弃"；而 §6.6.1 / F7.4 要求
**绝不静默丢弃**用户的打开请求，故以 `ack` 作为"首实例确实收到"的判据，
未收到 `ack` ⇒ 视为转交失败 ⇒ 降级为本地启动。服务端处理顺序
（`newConnection` → 读一行 → 校验 → 交付）与设计一致。

## 本模块的常量为什么不放 `config/settings.py`

v1.6.0 的 `settings.py` 由他人负责（需求 §4「只改点名文件」），故单实例相关常量
一律放本模块模块级；`main.py` 只从 `build_server_name()` 取服务名。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Sequence

from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 协议常量（设计 §6.6.1） ────────────────────────────────────────────────

#: 协议版本（行分隔 JSON 的 `v` 字段；不兼容变更时必须递增，接收端只认本值）
PROTOCOL_VERSION = 1
#: 消息动作：第二实例请求打开路径
ACTION_OPEN = "open"
#: 消息动作：首实例的确认
ACTION_ACK = "ack"
#: 消息动作：Windows 双监听防御用的自探测（不是业务消息）
ACTION_SELF_PROBE = "selfprobe"
#: 服务名前缀：`jflove-{APP_ORG}-{APP_NAME}`（设计 §6.6.1「服务名」）
SERVER_NAME_PREFIX = "jflove"
#: 单次转发的路径数量上限（§6.6.1「路径数量上限」；超出截断并记日志）
MAX_FORWARD_PATHS = 64
#: 单条路径的字符上限（防超大命令行把协议撑爆）
MAX_PATH_CHARS = 4096
#: 单行消息的字节上限（防异常/恶意对端撑爆内存）
MAX_LINE_BYTES = 1 << 20
#: 服务端等待一行消息的超时（§6.6.1「服务端处理」的 `waitForReadyRead(1000)`）
READ_TIMEOUT_MS = 1000
#: 作为客户端连接的重试次数与间隔（§6.6.1「客户端超时与兜底」3 次 × 200 ms）
CONNECT_RETRIES = 3
CONNECT_RETRY_INTERVAL_MS = 200
#: 单次连接的等待上限（毫秒）
CONNECT_TIMEOUT_MS = 400
#: 等待首实例 `ack` 的超时（毫秒）
ACK_TIMEOUT_MS = 1500
#: 首实例启动竞态：listen 之前先停顿再探一次的时长（毫秒）
STARTUP_SETTLE_MS = 250
#: Windows 自探测的等待窗口（毫秒）
SELF_PROBE_WAIT_MS = 400
#: 同名监听允许的最大待处理连接数
MAX_PENDING_CONNECTIONS = 16

# ── 转交失败原因（供日志与测试断言，不记录完整路径） ────────────────────────

SEND_CONNECT_FAILED = "connect_failed"
SEND_WRITE_FAILED = "write_failed"
SEND_NO_ACK = "no_ack"
SEND_BAD_ACK = "bad_ack"

# ── 路径来源（与设计 §7.1 事件 #1 的 `source` 取值一致） ─────────────────────

SOURCE_CLI = "cli"
SOURCE_SINGLE_INSTANCE = "single_instance"

#: 日志里最多列出几个文件名（`AGENTS.md §9.4.14`：日志不记用户内容明文）
LOG_NAME_LIMIT = 5


def build_server_name(app_org: str, app_name: str) -> str:
    """
    拼出单实例服务名（设计 §6.6.1：`jflove-{APP_ORG}-{APP_NAME}`）。

    :param app_org: `settings.APP_ORG`
    :param app_name: `settings.APP_NAME`
    :returns: 同机同用户唯一的服务名
    """
    return f"{SERVER_NAME_PREFIX}-{app_org}-{app_name}"


def _basename_any(path: str) -> str:
    """
    只取文件名（**同时**兼容 Windows 反斜杠与 POSIX 斜杠）。

    为什么不用 `os.path.basename`：日志安全（`AGENTS.md §9.4.14`）要求无论当前平台是什么，
    都不能把目录明文写进日志；`os.path.basename` 在非本平台的路径上会整串返回。

    :param path: 任意路径字符串
    :returns: 最后一段（空字符串时返回 `<空>`）
    """
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name or "<空>"


def log_safe_names(paths: Sequence[str], limit: int = LOG_NAME_LIMIT) -> str:
    """
    生成**只含文件名**（不含目录）的日志片段。

    `AGENTS.md §9.4.14` / 设计 §7.4：路径在日志里只留文件名（或截断 / 哈希）。

    :param paths: 路径序列
    :param limit: 最多列出几个名字，其余只记数量
    :returns: 形如 `a.txt, b.txt（等共 7 个）` 的文本
    """
    items = [str(p) for p in paths]
    names = [_basename_any(p) for p in items[:limit]]
    if not names:
        return "（无）"
    text = ", ".join(names)
    if len(items) > len(names):
        return f"{text}（等共 {len(items)} 个）"
    return text


def normalize_paths(argv: Sequence[str]) -> list[str]:
    """
    命令行参数 → 待打开路径列表（设计 §6.6.3）。

    * **不接受任何命令行开关**：`-` / `--` 开头的参数按"选项"忽略并记一条警告
      （避免"看起来支持但没实现"的选项）
    * 过滤空串 / 纯空白；`os.path.abspath` 规范化；去重（保留首次出现顺序）
    * **不校验存在性**（存在性由 `editor.detectAndRead` 判定并给出可观测提示，AC-48）
    * 超过 `MAX_FORWARD_PATHS` 截断（截断事实记日志，路径只记文件名）

    :param argv: 原始命令行参数（通常是 `sys.argv[1:]`）
    :returns: 规范化后的绝对路径列表
    """
    paths: list[str] = []
    seen: set[str] = set()
    option_count = 0
    empty_count = 0
    for raw in argv:
        text = str(raw).strip()
        if not text:
            empty_count += 1
            continue
        if text.startswith("-"):
            option_count += 1
            continue
        full = os.path.abspath(text)
        if full in seen:
            continue
        seen.add(full)
        paths.append(full)
    if option_count:
        logger.warning("单实例：忽略 %d 个命令行选项（本版本不接受任何开关）", option_count)
    if empty_count:
        logger.warning("单实例：忽略 %d 个空命令行参数", empty_count)
    if len(paths) > MAX_FORWARD_PATHS:
        logger.warning(
            "单实例：命令行路径 %d 条超过上限 %d，已截断（保留前 %d 条）",
            len(paths), MAX_FORWARD_PATHS, MAX_FORWARD_PATHS,
        )
        paths = paths[:MAX_FORWARD_PATHS]
    return paths


def encode_message(
    action: str,
    paths: Sequence[str],
    cwd: str | None = None,
    pid: int | None = None,
) -> bytes:
    """
    编码一条行分隔 JSON 消息（含结尾 `\\n`）。

    `ensure_ascii=False` 让中文路径按 UTF-8 原样写（协议约定 UTF-8 JSON）；
    `json.dumps` 会把路径里的换行 / 引号 / 反斜杠转义，天然不会破坏"每行一条"的帧格式。

    :param action: `ACTION_OPEN` / `ACTION_ACK` / `ACTION_SELF_PROBE`
    :param paths: 路径列表（超出上限截断）
    :param cwd: 第二实例的工作目录（仅作诊断信息，接收端不落日志）
    :param pid: 发送方进程号
    :returns: UTF-8 字节串（单行）
    """
    payload: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "action": str(action),
        "paths": [str(p) for p in list(paths)[:MAX_FORWARD_PATHS]],
    }
    if cwd is not None:
        payload["cwd"] = str(cwd)
    if pid is not None:
        payload["pid"] = int(pid)
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _parse_json_line(raw: bytes | str | bytearray) -> dict[str, Any] | None:
    """
    把一行字节 / 文本解析成 JSON 对象（失败返回 `None`，不抛异常）。

    :param raw: 一行内容（可含结尾换行）
    :returns: 解析出的 dict；非对象 / 超长 / 非法 JSON 一律 `None`
    """
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_LINE_BYTES:
            return None
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        text = str(raw)
        if len(text) > MAX_LINE_BYTES:
            return None
    line = text.strip()
    if not line:
        return None
    try:
        parsed = json.loads(line)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def decode_message(raw: bytes | str | bytearray) -> dict[str, Any] | None:
    """
    解析并**严格校验**一条业务消息（设计 §6.6.1「服务端处理」）。

    校验规则（任一不满足 ⇒ `None`，调用方只记长度不记内容）：
      * 单行 JSON **对象**，UTF-8，≤ `MAX_LINE_BYTES`
      * `v` 为整数、`action` 为非空字符串
      * `paths` 为字符串数组，条数 ≤ `MAX_FORWARD_PATHS`
      * 单条路径非空且 ≤ `MAX_PATH_CHARS`（单条非法 ⇒ 整条消息判非法，不静默丢单条）
      * 版本是否等于 `PROTOCOL_VERSION` **不在这里判**（否则无法回 `unsupported_version`
        的 `ack`），由 `_handle_connection()` 判

    :param raw: 一行消息
    :returns: `{"v":…,"action":…,"paths":[…],…}`；非法时 `None`
    """
    message = _parse_json_line(raw)
    if message is None:
        return None
    version = message.get("v")
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    action = message.get("action")
    if not isinstance(action, str) or not action:
        return None
    raw_paths = message.get("paths", [])
    if not isinstance(raw_paths, list) or len(raw_paths) > MAX_FORWARD_PATHS:
        return None
    paths: list[str] = []
    for item in raw_paths:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if not text or len(text) > MAX_PATH_CHARS:
            return None
        paths.append(text)
    message["paths"] = paths
    return message


def decode_ack(raw: bytes | str | bytearray) -> dict[str, Any] | None:
    """
    解析首实例的确认行。

    :param raw: 一行消息
    :returns: `{"v":1,"action":"ack","ok":bool,"accepted":int,…}`；非法时 `None`
    """
    message = _parse_json_line(raw)
    if message is None:
        return None
    if message.get("v") != PROTOCOL_VERSION or message.get("action") != ACTION_ACK:
        return None
    ok = message.get("ok")
    if not isinstance(ok, bool):
        return None
    accepted = message.get("accepted", 0)
    if not isinstance(accepted, int) or isinstance(accepted, bool):
        accepted = 0
    message["accepted"] = accepted
    return message


class SingleInstanceGuard(QObject):
    """
    单实例守卫：判定"本进程是否首实例"，并负责外部路径的**转交**与**入队**。

    :param server_name: 单实例名（用 `build_server_name()`；测试会换成随机名以免撞上真机实例）
    :param server_factory: 测试接缝 —— 返回 `QLocalServer` 的工厂（默认真实类）
    :param socket_factory: 测试接缝 —— 返回 `QLocalSocket` 的工厂（默认真实类）
    :param remove_server: 测试接缝 —— 取代 `QLocalServer.removeServer`（默认真实静态方法）
    :param connect_timeout_ms: 单次连接等待上限
    :param ack_timeout_ms: 等待首实例 `ack` 的超时
    """

    def __init__(
        self,
        server_name: str,
        *,
        server_factory: Callable[[], QLocalServer] | None = None,
        socket_factory: Callable[[], QLocalSocket] | None = None,
        remove_server: Callable[[str], None] | None = None,
        connect_timeout_ms: int = CONNECT_TIMEOUT_MS,
        ack_timeout_ms: int = ACK_TIMEOUT_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._server_name = str(server_name)
        self._server_factory = server_factory or QLocalServer
        self._socket_factory = socket_factory or QLocalSocket
        self._remove_server = remove_server or QLocalServer.removeServer
        self._connect_timeout_ms = int(connect_timeout_ms)
        self._ack_timeout_ms = int(ack_timeout_ms)
        #: 监听中的 server（首实例才有；非首实例保持 None）
        self._server: QLocalServer | None = None
        #: 是否已成为首实例
        self._primary = False
        #: 路径接入点（`set_paths_sink()` 注入；未注入时路径进 `_pending`）
        self._sink: Callable[[list[str], str], None] | None = None
        #: 待交付队列 `[(paths, source), …]`
        self._pending: list[tuple[list[str], str]] = []
        #: 自探测（Windows 双监听防御）状态
        self._self_probe_seen = False

    # ── 只读属性 ──────────────────────────────────────────

    @property
    def server_name(self) -> str:
        """单实例服务名"""
        return self._server_name

    @property
    def is_primary(self) -> bool:
        """本进程是否已判定为首实例"""
        return self._primary

    def pending_count(self) -> int:
        """待交付队列里的条目数（首实例在 `load_ui()` 之前收到的路径会排在这里）"""
        return len(self._pending)

    # ── 判定（设计 §6.6.1 / §6.6.3） ────────────────────────

    def try_become_primary(self) -> bool:
        """
        尝试成为首实例（**必须在 `build_app()` 之后、`WebShell()` 之前**调用）。

        :returns: `True` = 本进程是首实例（已开始监听，有新连接会交付路径）；
                  `False` = 已有首实例（调用方应 `forward()` 转交后退出，
                  或转交失败时降级为本地启动）
        """
        if self._server is not None:
            return self._primary  # 幂等：同一个 guard 重复调用不再重判

        # ① 连接探测：已有首实例 ⇒ 本进程是第二实例（此步绝不 removeServer）
        if self._probe_live_server():
            logger.info("单实例：检测到已有实例在运行，本进程按第二实例处理")
            return False

        # ② 启动竞态：首实例可能正在启动（还没 listen），稍等再探一次
        self._sleep_ms(STARTUP_SETTLE_MS)
        if self._probe_live_server():
            logger.info("单实例：短暂等待后检测到已有实例，本进程按第二实例处理")
            return False

        # ③ 监听；失败 ⇒ removeServer + 重试一次（陈旧 socket 兜底）
        server = self._server_factory()
        server.setMaxPendingConnections(MAX_PENDING_CONNECTIONS)
        server.newConnection.connect(lambda srv=server: self._on_new_connection(srv))
        if not server.listen(self._server_name):
            logger.warning(
                "单实例：监听 %s 失败（%s），清理陈旧 socket 后重试一次",
                self._server_name, server.errorString(),
            )
            self._remove_server(self._server_name)
            if not server.listen(self._server_name):
                logger.error(
                    "单实例：清理后仍无法监听 %s（%s），本进程不作为单实例监听者",
                    self._server_name, server.errorString(),
                )
                self._close_server(server)
                return False

        # ④ 平台兜底：listen() 在 Windows 上不仲裁（见模块文档的实测证据），
        #    用自探测确认"连接确实落到自己"，否则关掉监听去当第二实例。
        if not self._confirm_sole_listener(server):
            logger.info(
                "单实例：listen() 返回成功但自探测未落到本进程（并发启动竞态），按第二实例处理"
            )
            self._close_server(server)
            return False

        self._server = server
        self._primary = True
        logger.info(
            "单实例：本进程是首实例（server=%s，pid=%d）", self._server_name, os.getpid()
        )
        return True

    def forward(self, paths: Sequence[str] | None = None, cwd: str | None = None) -> bool:
        """
        以第二实例身份把路径转交给首实例（设计 §6.6.1「客户端超时与兜底」）。

        连接失败会重试 `CONNECT_RETRIES` 次 × `CONNECT_RETRY_INTERVAL_MS`；
        已连上但**未获确认**（无 `ack` / `ack` 为拒绝）时**不重发**，避免同一批路径被打开两次。

        :param paths: 待转交的绝对路径（可为空：表示"再来一次"，首实例只恢复窗口）
        :param cwd: 本进程工作目录（诊断信息）
        :returns: `True` = 首实例已确认收到（调用方应立刻 `return 0`）；
                  `False` = 转交失败（调用方应**降级为本地启动**，绝不静默丢弃）
        """
        target = [str(p) for p in (paths or [])]
        if len(target) > MAX_FORWARD_PATHS:
            logger.warning(
                "单实例：待转交路径 %d 条超过上限 %d，已截断", len(target), MAX_FORWARD_PATHS
            )
            target = target[:MAX_FORWARD_PATHS]
        payload = encode_message(ACTION_OPEN, target, cwd=cwd, pid=os.getpid())
        reason = SEND_CONNECT_FAILED
        for attempt in range(1, CONNECT_RETRIES + 1):
            reason = self._forward_once(payload)
            if reason is None:
                logger.info(
                    "单实例：已把 %d 个路径转交给首实例（第 %d 次尝试，文件名=%s）",
                    len(target), attempt, log_safe_names(target),
                )
                return True
            if reason != SEND_CONNECT_FAILED:
                break
            self._sleep_ms(CONNECT_RETRY_INTERVAL_MS)
        logger.warning(
            "单实例：转交失败（原因=%s），降级为本地启动；待打开文件名=%s",
            reason, log_safe_names(target),
        )
        return False

    def close(self) -> None:
        """关闭监听（幂等；`main()` 退出前调用，避免留下陈旧 socket）"""
        if self._server is not None:
            self._close_server(self._server)
            self._server = None

    # ── 路径交付（§6.6 接入点） ─────────────────────────────

    def set_paths_sink(self, sink: Callable[[list[str], str], None] | None) -> None:
        """
        注入"收到路径后交给谁"的回调，并**立刻冲刷**队列（§6.6 / §6.6.3 接入点）。

        设计 §6.6.3 要求首个实例把命令行路径暂存，等 `_shell.load_ui()` 成功后再交付；
        同理，在 `load_ui()` 之前被转交进来的路径也会排在队列里 —— 都由本方法一次冲出。
        「二选一」：接入点只应有**一个**（`main.py::_handle_external_paths`），
        不要同时再接别处，否则同一批路径会被交付两次。

        :param sink: `sink(paths, source) -> None`；传 `None` 只解绑不冲刷
        """
        self._sink = sink
        if sink is None:
            return
        pending, self._pending = self._pending, []
        for paths, source in pending:
            self._deliver(paths, source)

    def notify_external_paths(
        self, paths: Sequence[str], source: str = SOURCE_CLI
    ) -> None:
        """
        把外部路径交给接入点（已注入 ⇒ 立即交付；未注入 ⇒ 入队等 `set_paths_sink()`）。

        空列表是**有意义**的消息（第二实例不带参数再次启动 ⇒ 首实例只恢复窗口可见），
        所以不做"空则跳过"的早退。

        :param paths: 路径列表（通常已由 `normalize_paths()` 规范化）
        :param source: `SOURCE_CLI` / `SOURCE_SINGLE_INSTANCE`（设计 §7.1 事件 #1）
        """
        items = [str(p) for p in paths if str(p)]
        if self._sink is None:
            self._pending.append((items, str(source)))
            logger.debug("单实例：接入点尚未就绪，%d 个路径入队", len(items))
            return
        self._deliver(items, str(source))

    # ── 内部：仲裁 ────────────────────────────────────────

    def _probe_live_server(self) -> bool:
        """连接探测：能连上同名 server ⇒ 已有实例在监听"""
        sock = self._socket_factory()
        try:
            sock.connectToServer(self._server_name)
            return bool(sock.waitForConnected(self._connect_timeout_ms))
        finally:
            self._safe_abort(sock)

    def _confirm_sole_listener(self, server: QLocalServer) -> bool:
        """
        Windows 双监听防御：连自己刚监听的名字，看连接是否落到**自己的** server。

        :param server: 刚 `listen()` 成功的 server
        :returns: `True` = 自探测消息由本进程收到（本进程确实是唯一监听者）；
                  `False` = 没收到（被更早的同名实例接走 ⇒ 本进程是第二实例）
        """
        sock = self._socket_factory()
        try:
            sock.connectToServer(self._server_name)
            if not sock.waitForConnected(self._connect_timeout_ms):
                # 连不上自己：没有任何实例接连接 ⇒ 本进程就是唯一监听者
                return True
            self._self_probe_seen = False
            sock.write(encode_message(ACTION_SELF_PROBE, [], pid=os.getpid()))
            sock.flush()
        except Exception as exc:  # 自探测失败不阻塞启动：按"自己就是监听者"处理
            logger.debug("单实例：自探测不可用（%s: %s），按唯一监听者继续",
                         type(exc).__name__, exc)
            self._safe_abort(sock)
            return True
        deadline = time.monotonic() + SELF_PROBE_WAIT_MS / 1000
        while not self._self_probe_seen and time.monotonic() < deadline:
            self._pump_events()
            time.sleep(0.01)
        self._safe_abort(sock)
        return self._self_probe_seen

    def _on_new_connection(self, server: QLocalServer) -> None:
        """处理待决连接（信号槽：读一行 → 校验 → 回 `ack` → 交付路径）"""
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            try:
                self._handle_connection(conn)
            except Exception as exc:  # 单条连接异常不影响监听
                logger.error("单实例：处理转交连接失败（%s: %s）", type(exc).__name__, exc)
            finally:
                try:
                    conn.disconnectFromServer()
                except Exception:  # 对端可能已断开
                    pass

    def _handle_connection(self, conn: QLocalSocket) -> None:
        """
        处理一条转交连接（设计 §6.6.1「服务端处理」）。

        :param conn: 已接受的连接
        """
        raw = self._read_first_line(conn)
        if raw is None:
            logger.debug("单实例：连接未带完整消息行，已忽略")
            return
        message = decode_message(raw)
        if message is None:
            # 只记长度，不记内容（内容里可能有用户路径明文）
            logger.warning("单实例：收到不合法的消息（%d 字节），已忽略", len(raw))
            self._write_ack(conn, ok=False, reason="invalid_message")
            return
        if message["v"] != PROTOCOL_VERSION:
            logger.warning(
                "单实例：消息协议版本 %s 与本进程（v%d）不一致，已拒绝",
                message["v"], PROTOCOL_VERSION,
            )
            self._write_ack(conn, ok=False, reason="unsupported_version")
            return
        if message["action"] == ACTION_SELF_PROBE:
            if message.get("pid") == os.getpid():
                self._self_probe_seen = True
            return
        paths = [str(p) for p in message["paths"]]
        # 先确认再交付：让第二实例能确定"首实例确实收到"，不静默丢弃
        self._write_ack(conn, ok=True, accepted=len(paths))
        logger.info("单实例：收到另一实例转交的 %d 个路径（文件名=%s）",
                    len(paths), log_safe_names(paths))
        self.notify_external_paths(paths, source=SOURCE_SINGLE_INSTANCE)

    def _read_first_line(self, conn: QLocalSocket) -> bytes | None:
        """
        读一行（`\\n` 结尾）消息。

        :param conn: 连接
        :returns: 不含换行的一行内容；超时/超长/空 ⇒ `None`
        """
        buffer = b""
        deadline = time.monotonic() + READ_TIMEOUT_MS / 1000
        while True:
            if b"\n" in buffer:
                return buffer.split(b"\n", 1)[0]
            if len(buffer) > MAX_LINE_BYTES:
                logger.warning("单实例：消息超过 %d 字节上限，已忽略", MAX_LINE_BYTES)
                return None
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                break
            if not conn.waitForReadyRead(remaining):
                break
            chunk = bytes(conn.readAll())
            if not chunk:
                break
            buffer += chunk
        if b"\n" in buffer:
            return buffer.split(b"\n", 1)[0]
        # 对端没写换行就关了：容忍（协议要求有换行，宽松接收不吃亏）
        return buffer or None

    def _write_ack(self, conn: QLocalSocket, ok: bool, reason: str = "",
                   accepted: int = 0) -> None:
        """
        回一行 `ack`（失败只记 debug：确认丢了对端会走降级，不影响本进程）。

        :param conn: 连接
        :param ok: 是否接受
        :param reason: 拒绝原因（`ok=False` 时）
        :param accepted: 已接受的路径条数
        """
        payload: dict[str, Any] = {
            "v": PROTOCOL_VERSION,
            "action": ACTION_ACK,
            "ok": bool(ok),
            "accepted": int(accepted),
            "pid": os.getpid(),
        }
        if reason:
            payload["reason"] = str(reason)
        try:
            conn.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            conn.flush()
        except Exception as exc:  # 对端提前断开
            logger.debug("单实例：回 ack 失败（%s: %s）", type(exc).__name__, exc)

    # ── 内部：客户端 ──────────────────────────────────────

    def _forward_once(self, payload: bytes) -> str | None:
        """
        单次转交。

        :param payload: 已编码的一行消息
        :returns: `None` = 成功（已获 `ack`）；否则为 `SEND_*` 失败原因
        """
        sock = self._socket_factory()
        try:
            sock.connectToServer(self._server_name)
            if not sock.waitForConnected(self._connect_timeout_ms):
                return SEND_CONNECT_FAILED
            written = sock.write(payload)
            sock.flush()
            if written is None or int(written) < len(payload):
                logger.warning("单实例：写入转交消息不完整（%s/%d 字节）",
                               written, len(payload))
                return SEND_WRITE_FAILED
            return self._await_ack(sock)
        finally:
            self._safe_abort(sock)

    def _await_ack(self, sock: QLocalSocket) -> str | None:
        """
        等待并解析首实例的 `ack`。

        :param sock: 已送出消息的连接
        :returns: `None` = 确认通过；否则为 `SEND_*` 失败原因
        """
        buffer = b""
        deadline = time.monotonic() + self._ack_timeout_ms / 1000
        while b"\n" not in buffer and time.monotonic() < deadline:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                break
            if not sock.waitForReadyRead(remaining):
                if sock.bytesToWrite() == 0 and not self._is_connected(sock):
                    break  # 对端读完就关了（未回 ack）
                continue
            chunk = bytes(sock.readAll())
            if chunk:
                buffer += chunk
        if b"\n" not in buffer:
            return SEND_NO_ACK
        ack = decode_ack(buffer.split(b"\n", 1)[0])
        if ack is None:
            return SEND_BAD_ACK
        if not ack["ok"]:
            return f"rejected:{ack.get('reason', '')}"
        return None

    # ── 内部：小工具 ──────────────────────────────────────

    @staticmethod
    def _is_connected(sock: QLocalSocket) -> bool:
        """连接是否仍处于已连接状态（兼容测试替身）"""
        state = getattr(sock, "state", None)
        if state is None:
            return False
        try:
            return state() == QLocalSocket.SocketState.ConnectedState
        except Exception:  # 替身没有 state() 时按"已断开"处理
            return False

    @staticmethod
    def _safe_abort(sock: QLocalSocket) -> None:
        """安全断开（对端可能已消失）"""
        try:
            sock.abort()
        except Exception:  # 已断开 / 替身不支持
            pass

    @staticmethod
    def _close_server(server: QLocalServer) -> None:
        """安全关闭 server（幂等）"""
        try:
            server.close()
        except Exception:  # 已关闭
            pass

    @staticmethod
    def _pump_events() -> None:
        """
        跑一轮事件循环（自探测期间让 `newConnection` 能被投递）。

        判定发生在 `app.exec()` 之前，此时没有主事件循环，故显式抽一轮；
        窗口预算由 `SELF_PROBE_WAIT_MS` 限定（毫秒级，见模块文档）。
        """
        app = QCoreApplication.instance()
        if app is not None:
            app.processEvents()

    @staticmethod
    def _sleep_ms(milliseconds: int) -> None:
        """毫秒级休眠（启动路径上的短等待，不引入额外依赖）"""
        if milliseconds > 0:
            time.sleep(milliseconds / 1000)

    def _deliver(self, paths: list[str], source: str) -> None:
        """
        调接入点交付（接入点异常不影响单实例机制本身）。

        :param paths: 路径列表
        :param source: 来源（`cli` / `single_instance`）
        """
        if self._sink is None:
            self._pending.append((list(paths), str(source)))
            return
        try:
            self._sink(list(paths), str(source))
        except Exception as exc:
            logger.error("单实例：路径接入点抛异常（%s: %s）", type(exc).__name__, exc)

"""
v1.6.0 单实例守卫用例（`src/utils/single_instance.py`）

对应设计：`文档记录/设计文档/v1.6.0.md` §6.6（选型与协议）、§6.5（与退出的交互）、
§7.1 事件 #1（`editor.openPaths` 的 `source` 取值）。

覆盖点：
  * 纯函数口径：`normalize_paths`（不接受开关 / 去重 / 上限截断）、`log_safe_names`
    （日志只留文件名，`AGENTS.md §9.4.14`）、`build_server_name`
  * 协议编解码：`encode_message` / `decode_message` / `decode_ack` 的**严格校验**
    （非法输入一律 `None`，不抛），中文路径与含换行路径的帧安全
  * 守卫行为：判定（已存在实例 ⇒ 非首实例）、陈旧 socket 兜底重试一次、
    待交付队列（接入点未就绪时入队、注入时按序冲出、接入点抛异常不影响机制）、
    转交成功/失败（失败必须降级而不是静默丢弃）

Qt 依赖：`SingleInstanceGuard` 是 `QObject`，故本文件复用 `tests/conftest.py` 的 session 级
`qapp`（**QApplication**）fixture —— **不要**在本文件里自建 `QCoreApplication`：
同一进程只允许一个应用实例，先建 `QCoreApplication` 会让后续 `QApplication([])` 直接
终止进程（实测 `pytest exit=-1073740791` = 0xC0000409）。**不启动事件循环**。
真实双进程仲裁由 `poc/editor-probes/r2-local-server-arbitration.py` 的实测证据承担。
"""

from __future__ import annotations

import os

import pytest

from src.utils import single_instance as si


@pytest.fixture(autouse=True)
def _qt_app(qapp):
    """复用 conftest 的 QApplication 单例（本文件不需要 GUI，只需要实例存在）"""
    return qapp


# ─────────────────────── 纯函数 ─────────────────────── #


class Test路径规范化:
    """`normalize_paths`：设计 §6.6.3 —— 不接受任何命令行开关"""

    def test_去重且保留首次出现顺序(self):
        paths = si.normalize_paths(["b.txt", "a.txt", "b.txt"])
        assert [os.path.basename(p) for p in paths] == ["b.txt", "a.txt"]

    def test_忽略开关并过滤空白(self):
        paths = si.normalize_paths(["--help", "  ", "a.txt", "-x"])
        assert [os.path.basename(p) for p in paths] == ["a.txt"]

    def test_规范化为绝对路径(self):
        (path,) = si.normalize_paths(["a.txt"])
        assert os.path.isabs(path)

    def test_超过上限截断(self):
        raw = [f"f{i}.txt" for i in range(si.MAX_FORWARD_PATHS + 5)]
        assert len(si.normalize_paths(raw)) == si.MAX_FORWARD_PATHS

    def test_不校验存在性(self):
        """存在性由 detectAndRead 判定（AC-48），这里不得提前过滤"""
        paths = si.normalize_paths(["绝对不存在的文件-xyz.txt"])
        assert len(paths) == 1


class Test日志安全:
    """`log_safe_names`：`AGENTS.md §9.4.14` —— 日志不记完整路径"""

    def test_只留文件名(self):
        text = si.log_safe_names([r"D:\secret\dir\a.txt", "/home/u/b.md"])
        assert text == "a.txt, b.md"
        assert "secret" not in text and "/home/u" not in text

    def test_反斜杠路径在非本平台也取最后一段(self):
        assert si.log_safe_names([r"C:\x\y\z.log"]) == "z.log"

    def test_超限只记数量(self):
        names = [f"f{i}.txt" for i in range(si.LOG_NAME_LIMIT + 2)]
        text = si.log_safe_names(names)
        assert f"等共 {len(names)} 个" in text

    def test_空输入(self):
        assert si.log_safe_names([]) == "（无）"


class Test服务名:
    def test_按组织与应用拼接(self):
        assert si.build_server_name("Org", "App") == "jflove-Org-App"


# ─────────────────────── 协议 ─────────────────────── #


class Test协议编解码:
    """行分隔 JSON：每条消息一行，UTF-8"""

    def test_中文路径往返(self):
        raw = si.encode_message(si.ACTION_OPEN, [r"D:\资料\中文 文件.txt"], pid=1)
        assert raw.endswith(b"\n") and raw.count(b"\n") == 1
        msg = si.decode_message(raw)
        assert msg is not None
        assert msg["paths"] == [r"D:\资料\中文 文件.txt"]
        assert msg["action"] == si.ACTION_OPEN and msg["v"] == si.PROTOCOL_VERSION

    def test_含换行的路径不破坏帧(self):
        weird = "a\nb.txt"
        raw = si.encode_message(si.ACTION_OPEN, [weird])
        assert raw.count(b"\n") == 1, "换行必须被 JSON 转义，不能撑出第二行"
        assert si.decode_message(raw)["paths"] == [weird]

    def test_路径数量超限时截断(self):
        raw = si.encode_message(si.ACTION_OPEN, [f"f{i}" for i in range(200)])
        assert len(si.decode_message(raw)["paths"]) == si.MAX_FORWARD_PATHS

    @pytest.mark.parametrize("bad", [
        b"", b"\n", b"not json\n", b"[1,2,3]\n", b'"str"\n',
        b'{"action":"open"}\n',                                    # 缺 v
        b'{"v":"1","action":"open"}\n',                            # v 非整数
        b'{"v":1,"action":""}\n',                                  # action 空
        b'{"v":1,"action":"open","paths":"x"}\n',                  # paths 非数组
        b'{"v":1,"action":"open","paths":[1]}\n',                  # 元素非字符串
        b'{"v":1,"action":"open","paths":[""]}\n',                 # 空路径
        b'{"v":1,"action":"open","paths":["' + b"x" * (si.MAX_PATH_CHARS + 1) + b'"]}\n',
    ])
    def test_非法消息一律拒绝且不抛(self, bad):
        assert si.decode_message(bad) is None

    def test_超长行直接拒绝(self):
        huge = b'{"v":1,"action":"open","paths":["' + b"x" * (si.MAX_LINE_BYTES + 10) + b'"]}'
        assert si.decode_message(huge) is None

    def test_ack_解析(self):
        ok = si.decode_ack(b'{"v":1,"action":"ack","ok":true,"accepted":2}\n')
        assert ok is not None and ok["ok"] is True and ok["accepted"] == 2
        bad = si.decode_ack(b'{"v":1,"action":"ack","ok":"yes"}\n')
        assert bad is None
        wrong_action = si.decode_ack(b'{"v":1,"action":"open","ok":true}\n')
        assert wrong_action is None

    def test_ack_的accepted非法时回落为0(self):
        ack = si.decode_ack(b'{"v":1,"action":"ack","ok":true,"accepted":"x"}\n')
        assert ack is not None and ack["accepted"] == 0


# ─────────────────────── 守卫行为 ─────────────────────── #


class _FakeSocket:
    """可编排的 `QLocalSocket` 替身（判定 / 转交路径都用它）"""

    def __init__(self, *, connect_ok: bool = False, ack: bytes | None = None) -> None:
        self.connect_ok = connect_ok
        self.ack = ack
        self.written: list[bytes] = []

    def connectToServer(self, _name: str) -> None:  # noqa: N802 - Qt 命名
        pass

    def waitForConnected(self, _ms: int) -> bool:  # noqa: N802
        return self.connect_ok

    def write(self, payload: bytes) -> int:  # noqa: N802
        self.written.append(payload)
        return len(payload)

    def flush(self) -> None:  # noqa: N802
        pass

    def waitForBytesWritten(self, _ms: int) -> bool:  # noqa: N802
        return True

    def waitForReadyRead(self, _ms: int) -> bool:  # noqa: N802
        return self.ack is not None

    def readAll(self) -> bytes:  # noqa: N802
        data, self.ack = self.ack or b"", None
        return data

    def bytesToWrite(self) -> int:  # noqa: N802
        return 0

    def state(self):  # noqa: N802
        from PySide6.QtNetwork import QLocalSocket
        return QLocalSocket.SocketState.UnconnectedState

    def abort(self) -> None:  # noqa: N802
        pass

    def disconnectFromServer(self) -> None:  # noqa: N802
        pass


class _FakeServer:
    """可编排的 `QLocalServer` 替身（listen 成功/失败序列）"""

    class _Signal:
        def connect(self, _slot) -> None:
            pass

    def __init__(self, listen_results: list[bool]) -> None:
        self._results = list(listen_results)
        self.newConnection = _FakeServer._Signal()
        self.closed = False

    def setMaxPendingConnections(self, _n: int) -> None:  # noqa: N802
        pass

    def listen(self, _name: str) -> bool:  # noqa: N802
        return self._results.pop(0) if self._results else False

    def errorString(self) -> str:  # noqa: N802
        return "AddressInUseError"

    def close(self) -> None:  # noqa: N802
        self.closed = True


class Test待交付队列:
    """接入点未就绪时不能丢路径（设计 §6.6 接入点 / §7.5 同型要求）"""

    def test_未注入接入点时入队(self):
        guard = si.SingleInstanceGuard("test-guard-queue")
        guard.notify_external_paths(["a.txt"], si.SOURCE_CLI)
        guard.notify_external_paths([], si.SOURCE_SINGLE_INSTANCE)
        assert guard.pending_count() == 2, "空列表也是有意义的消息（只恢复窗口）"

    def test_注入接入点时按序冲出并保留来源(self):
        guard = si.SingleInstanceGuard("test-guard-flush")
        guard.notify_external_paths(["a.txt"], si.SOURCE_CLI)
        guard.notify_external_paths(["b.txt"], si.SOURCE_SINGLE_INSTANCE)
        seen: list[tuple[list[str], str]] = []
        guard.set_paths_sink(lambda paths, source: seen.append((paths, source)))
        assert guard.pending_count() == 0
        assert seen == [(["a.txt"], si.SOURCE_CLI), (["b.txt"], si.SOURCE_SINGLE_INSTANCE)]

    def test_接入点抛异常不影响机制(self):
        guard = si.SingleInstanceGuard("test-guard-sink-error")
        guard.set_paths_sink(lambda _paths, _source: (_ for _ in ()).throw(RuntimeError("boom")))
        guard.notify_external_paths(["a.txt"])  # 不得向上抛
        assert guard.pending_count() == 0


class Test首实例判定:
    """`try_become_primary`：已有实例 ⇒ 非首实例；监听失败 ⇒ 非首实例"""

    def test_已有实例在运行则判定为非首实例(self, monkeypatch):
        monkeypatch.setattr(si, "STARTUP_SETTLE_MS", 0)
        guard = si.SingleInstanceGuard(
            "test-guard-existing",
            socket_factory=lambda: _FakeSocket(connect_ok=True),
        )
        assert guard.try_become_primary() is False
        assert guard.is_primary is False

    def test_监听两次都失败则降级为非首实例(self, monkeypatch):
        monkeypatch.setattr(si, "STARTUP_SETTLE_MS", 0)
        guard = si.SingleInstanceGuard(
            "test-guard-listen-fail",
            server_factory=lambda: _FakeServer([False, False]),
            socket_factory=lambda: _FakeSocket(connect_ok=False),
            remove_server=lambda _name: None,
        )
        assert guard.try_become_primary() is False

    def test_陈旧socket兜底_第二次监听成功即首实例(self, monkeypatch):
        """设计 §6.6.1：listen 失败 ⇒ removeServer + 重试一次"""
        monkeypatch.setattr(si, "STARTUP_SETTLE_MS", 0)
        removed: list[str] = []
        guard = si.SingleInstanceGuard(
            "test-guard-stale-socket",
            server_factory=lambda: _FakeServer([False, True]),
            socket_factory=lambda: _FakeSocket(connect_ok=False),
            remove_server=removed.append,
        )
        assert guard.try_become_primary() is True
        assert guard.is_primary is True
        assert removed == ["test-guard-stale-socket"]

    def test_重复调用幂等(self, monkeypatch):
        monkeypatch.setattr(si, "STARTUP_SETTLE_MS", 0)
        guard = si.SingleInstanceGuard(
            "test-guard-idempotent",
            server_factory=lambda: _FakeServer([True]),
            socket_factory=lambda: _FakeSocket(connect_ok=False),
            remove_server=lambda _name: None,
        )
        assert guard.try_become_primary() is True
        assert guard.try_become_primary() is True
        guard.close()


class Test第二实例转交:
    """`forward`：连不上 ⇒ 返回 False（调用方降级本地启动，绝不静默丢弃）"""

    def test_连接失败返回False(self, monkeypatch):
        monkeypatch.setattr(si, "CONNECT_RETRY_INTERVAL_MS", 0)
        guard = si.SingleInstanceGuard(
            "test-guard-forward-fail",
            socket_factory=lambda: _FakeSocket(connect_ok=False),
        )
        assert guard.forward(["a.txt"]) is False

    def test_收到ack则转交成功(self, monkeypatch):
        monkeypatch.setattr(si, "CONNECT_RETRY_INTERVAL_MS", 0)
        ack = b'{"v":1,"action":"ack","ok":true,"accepted":1}\n'
        guard = si.SingleInstanceGuard(
            "test-guard-forward-ok",
            socket_factory=lambda: _FakeSocket(connect_ok=True, ack=ack),
        )
        assert guard.forward([r"D:\中文 路径\a.txt"]) is True

    def test_首实例拒绝则转交失败(self, monkeypatch):
        monkeypatch.setattr(si, "CONNECT_RETRY_INTERVAL_MS", 0)
        ack = b'{"v":1,"action":"ack","ok":false,"reason":"invalid_message"}\n'
        guard = si.SingleInstanceGuard(
            "test-guard-forward-rejected",
            socket_factory=lambda: _FakeSocket(connect_ok=True, ack=ack),
        )
        assert guard.forward(["a.txt"]) is False

    def test_无ack则转交失败(self, monkeypatch):
        monkeypatch.setattr(si, "CONNECT_RETRY_INTERVAL_MS", 0)
        monkeypatch.setattr(si, "ACK_TIMEOUT_MS", 1)
        guard = si.SingleInstanceGuard(
            "test-guard-forward-noack",
            socket_factory=lambda: _FakeSocket(connect_ok=True, ack=b'{"v":1,"action":"open"}\n'),
        )
        assert guard.forward(["a.txt"]) is False

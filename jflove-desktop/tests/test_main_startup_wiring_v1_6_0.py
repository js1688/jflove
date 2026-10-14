"""
v1.6.0 桌面端启动接线：**待打开路径交付顺序（S-4）+ 降级分支不丢路径（M-6）**

依据：

- 设计 `文档记录/设计文档/v1.6.0.md` §6.6.3（表）/§7.5③：第二实例转交的路径要
  "**先** `restore_from_tray()` 再打开"——否则主实例在托盘/最小化状态下被
  「用 JFLove 打开」唤起时窗格建了、窗口还藏着（AC-46②）。
- Phase 5 审查报告 `文档记录/代码审查报告/v1.6.0.md`：
  **S-4**（`main.py` 的 sink 未恢复窗口可见）、**M-6**（非首实例转交失败的降级
  分支丢弃 `cli_paths`，与 AC-48 及同文件"绝不静默丢弃"的注释矛盾）。

测试手法：`bootstrap_single_instance` / `build_paths_sink` 是**纯逻辑**（只依赖
`guard` / `shell` 的同名方法），用假对象即可覆盖全部分支，**不需要** Qt 窗口。
"""
from __future__ import annotations

from src.main import bootstrap_single_instance, build_paths_sink
from src.utils.single_instance import SOURCE_CLI

_PATHS = ["D:\\资料\\a.txt", "D:\\资料\\b.md"]


class _FakeGuard:
    """`SingleInstanceGuard` 的替身（记录调用序列）"""

    def __init__(self, *, primary: bool, forward_ok: bool = False) -> None:
        self._primary = primary
        self._forward_ok = forward_ok
        self.calls: list[tuple] = []
        self.sink = None

    def try_become_primary(self) -> bool:
        self.calls.append(("try_become_primary",))
        return self._primary

    def forward(self, paths, cwd=None) -> bool:
        self.calls.append(("forward", tuple(paths), cwd))
        return self._forward_ok

    def notify_external_paths(self, paths, source=None) -> None:
        self.calls.append(("notify_external_paths", tuple(paths), source))

    def set_paths_sink(self, sink) -> None:
        self.sink = sink

    @property
    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


class _FakeShell:
    """`WebShell` 的替身（记录 `restore_from_tray` / `notify_external_paths` 的先后）"""

    def __init__(self, *, restore_raises: bool = False) -> None:
        self.events: list[tuple] = []
        self._restore_raises = restore_raises

    def restore_from_tray(self) -> None:
        self.events.append(("restore_from_tray",))
        if self._restore_raises:
            raise RuntimeError("窗口已销毁")

    def notify_external_paths(self, paths, source=None) -> None:
        self.events.append(("notify_external_paths", tuple(paths), source))


# ── bootstrap_single_instance（AC-46① / AC-48 / M-6）────────────────


class TestBootstrapSingleInstance:
    """启动期的"是不是首实例"分支"""

    def test_primary_with_paths_queues_them(self) -> None:
        guard = _FakeGuard(primary=True)
        assert bootstrap_single_instance(guard, list(_PATHS)) is True
        assert guard.names == ["try_become_primary", "notify_external_paths"]
        assert guard.calls[1] == ("notify_external_paths", tuple(_PATHS), SOURCE_CLI)

    def test_primary_without_paths_does_not_queue(self) -> None:
        guard = _FakeGuard(primary=True)
        assert bootstrap_single_instance(guard, []) is True
        assert guard.names == ["try_become_primary"]

    def test_second_instance_forwarded_exits_without_queueing(self) -> None:
        """转交成功：本进程退出，**不**再进队列（否则会双开）"""
        guard = _FakeGuard(primary=False, forward_ok=True)
        assert bootstrap_single_instance(guard, list(_PATHS)) is False
        assert guard.names == ["try_become_primary", "forward"]
        name, forwarded, cwd = guard.calls[1]
        assert name == "forward"
        assert forwarded == tuple(_PATHS)
        assert isinstance(cwd, str) and cwd  # 转交要带上工作目录（相对路径靠它解析）

    def test_second_instance_degrade_still_delivers_paths(self) -> None:
        """
        核心回归（M-6）：转交失败降级为本地启动时，命令行路径**照样**进队列。
        原实现只有 `elif cli_paths:` 一支会交付 ⇒ 用户双击的文件一个都不打开。
        """
        guard = _FakeGuard(primary=False, forward_ok=False)
        assert bootstrap_single_instance(guard, list(_PATHS)) is True
        assert guard.names == [
            "try_become_primary",
            "forward",
            "notify_external_paths",
        ]
        assert guard.calls[2] == ("notify_external_paths", tuple(_PATHS), SOURCE_CLI)

    def test_second_instance_degrade_without_paths(self) -> None:
        guard = _FakeGuard(primary=False, forward_ok=False)
        assert bootstrap_single_instance(guard, []) is True
        assert guard.names == ["try_become_primary", "forward"]


# ── build_paths_sink（S-4）─────────────────────────────────────────


class TestBuildPathsSink:
    """交付顺序：**先恢复窗口可见，再交付路径**（设计 §6.6.3）"""

    def test_restore_happens_before_delivery(self) -> None:
        shell = _FakeShell()
        sink = build_paths_sink(shell)
        sink(list(_PATHS), SOURCE_CLI)
        assert shell.events == [
            ("restore_from_tray",),
            ("notify_external_paths", tuple(_PATHS), SOURCE_CLI),
        ]

    def test_paths_and_source_passed_through(self) -> None:
        shell = _FakeShell()
        sink = build_paths_sink(shell)
        sink(["D:\\唯一.txt"], "single_instance")
        assert shell.events[-1] == (
            "notify_external_paths",
            ("D:\\唯一.txt",),
            "single_instance",
        )

    def test_delivery_survives_restore_failure(self) -> None:
        """恢复可见性失败**不**阻断"打开文件"本身（否则用户连窗格都拿不到）"""
        shell = _FakeShell(restore_raises=True)
        sink = build_paths_sink(shell)
        sink(list(_PATHS), SOURCE_CLI)
        assert shell.events == [
            ("restore_from_tray",),
            ("notify_external_paths", tuple(_PATHS), SOURCE_CLI),
        ]

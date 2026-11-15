"""
v1.10.7：Windows 本机终端「打包 + pywinpty spawn 契约」回归测试。

背景（真机事故，v1.10.6 发布包）：
    `C:\\Program Files\\JFLove\\_internal\\winpty\\` 里只有 `conpty.dll` /
    `winpty.dll` / `_winpty.*.pyd`，缺 pywinpty 官方 wheel 自带的
    `OpenConsole.exe`（1.09MB）与 `winpty-agent.exe`（2.63MB）。
    ConPTY 因此起不来：`PTY.spawn()` **返回 False（不抛异常）**，旧实现忽略返回值，
    日志还打印"已启动" ⇒ 前端 tab 绿点、终端画面全黑、日志无任何异常。
    KVM 真机上把这两个 exe 复制进已安装目录后终端立刻正常（banner + echo 回显），
    删除后又全黑 —— 因果已闭合。

本文件把两条不变式钉死（纯 Linux 也能跑，靠注入假包目录 / 假 winpty 模块）：
    1. **打包**：Windows 构建必须把两个助手 exe 作为 `--add-data ...;winpty` 收进包，
       缺文件直接 fail；打包后还要在产物里复查，缺了同样 fail。
    2. **调用契约**：`_open_windows` 必须按 pywinpty 契约调用
       `spawn(appname, cmdline=..., cwd=绝对目录, env=字符串或 None)`，
       且 **spawn 返回 False 时必须抛 RuntimeError**（不许假"已打开"）。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_DESKTOP_ROOT = Path(__file__).resolve().parent.parent


def _load_build_module():
    """把模块 build.py 作为模块加载（它不是包，无法直接 import）。"""
    name = "jflove_desktop_build_v1107"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _DESKTOP_ROOT / "build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _fake_winpty_pkg(
    tmp_path: Path,
    files: tuple[str, ...] = ("OpenConsole.exe", "winpty-agent.exe"),
) -> Path:
    """造一个假的 pywinpty 包目录（只放运行时助手文件）。"""
    pkg = tmp_path / "winpty_pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name in files:
        (pkg / name).write_bytes(b"MZ fake helper")
    return pkg


def _fake_windows_platform():
    """"当前平台是 Windows" 的替身（只在被测模块内生效）。"""
    return types.SimpleNamespace(system=lambda: "Windows")


# ── 一、打包：--add-data 收 winpty 助手 ───────────────────────────


def test_winpty_data_args_includes_both_helpers(tmp_path):
    """两个助手 exe 都要以 `--add-data <src>;winpty` 形式进包"""
    build = _load_build_module()
    pkg = _fake_winpty_pkg(tmp_path)

    args = build.winpty_data_args(";", pkg_dir=pkg)

    assert len(args) == 4
    assert args[0] == "--add-data" and args[2] == "--add-data"
    assert args[1] == f"{pkg / 'OpenConsole.exe'};winpty"
    assert args[3] == f"{pkg / 'winpty-agent.exe'};winpty"


def test_winpty_data_args_fails_when_helper_missing(tmp_path):
    """缺 OpenConsole.exe 时必须 fail（退出码 1），不允许静默出包"""
    build = _load_build_module()
    pkg = _fake_winpty_pkg(tmp_path, files=("winpty-agent.exe",))

    with pytest.raises(SystemExit) as exc:
        build.winpty_data_args(";", pkg_dir=pkg)

    assert exc.value.code == 1


def test_assert_winpty_bundled_accepts_complete_dist(tmp_path, monkeypatch):
    """产物里两个助手都在 → 门禁通过"""
    build = _load_build_module()
    dist = tmp_path / "dist"
    internal = dist / "JFLove" / "_internal" / "winpty"
    internal.mkdir(parents=True)
    for name in build.WINPTY_DATA_FILES:
        (internal / name).write_bytes(b"MZ")

    monkeypatch.setattr(build, "platform", _fake_windows_platform())
    monkeypatch.setattr(build, "DIST_DIR", dist)
    monkeypatch.setattr(build, "APP_NAME", "JFLove")

    build.assert_winpty_bundled("onedir")  # 不抛异常即通过


def test_assert_winpty_bundled_rejects_missing_helper(tmp_path, monkeypatch):
    """产物里漏了助手 → 门禁必须 fail（v1.10.6 就是在这一步没拦住）"""
    build = _load_build_module()
    dist = tmp_path / "dist"
    internal = dist / "JFLove" / "_internal" / "winpty"
    internal.mkdir(parents=True)
    (internal / "conpty.dll").write_bytes(b"MZ")  # 只有 dll，没有 exe

    monkeypatch.setattr(build, "platform", _fake_windows_platform())
    monkeypatch.setattr(build, "DIST_DIR", dist)
    monkeypatch.setattr(build, "APP_NAME", "JFLove")

    with pytest.raises(SystemExit) as exc:
        build.assert_winpty_bundled("onedir")

    assert exc.value.code == 1


def test_run_pyinstaller_passes_winpty_add_data(tmp_path, monkeypatch):
    """Windows 分支必须把 --add-data 传给 PyInstaller，且**必须调用产物门禁**"""
    build = _load_build_module()
    pkg = _fake_winpty_pkg(tmp_path)
    fake_winpty = types.ModuleType("winpty")
    fake_winpty.__file__ = str(pkg / "__init__.py")
    fake_winpty.PTY = object  # 只需存在，PyInstaller 分支不实例化
    monkeypatch.setitem(sys.modules, "winpty", fake_winpty)
    monkeypatch.setattr(build, "platform", _fake_windows_platform())
    monkeypatch.setattr(build, "BUILD_DIR", tmp_path / "build")
    monkeypatch.setattr(build, "DIST_DIR", tmp_path / "build" / "dist")
    monkeypatch.setattr(build, "UI_DIST_DIR", tmp_path / "ui-dist")
    monkeypatch.setattr(build, "IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(build, "SRC_MAIN", tmp_path / "main.py")
    monkeypatch.setattr(build, "APP_NAME", "JFLove")
    monkeypatch.setattr(build, "prepare_output_name", lambda mode: None)

    captured: dict[str, list[str]] = {}
    gate_calls: list[str] = []

    class _Proc:
        returncode = 0

    def _fake_run(cmd, cwd=None):  # noqa: ANN001, ANN202 - 测试替身
        captured["cmd"] = list(cmd)
        return _Proc()

    monkeypatch.setattr(build.subprocess, "run", _fake_run)
    monkeypatch.setattr(build, "assert_winpty_bundled", gate_calls.append)
    build.run_pyinstaller(clean=False, mode="onedir")

    cmd = " ".join(captured["cmd"])
    assert "OpenConsole.exe" in cmd
    assert "winpty-agent.exe" in cmd
    assert "winpty" in cmd
    # 门禁不能被"顺手删掉"：删了实现这行会红
    assert gate_calls == ["onedir"]


def test_assert_winpty_bundled_follows_renamed_output(tmp_path, monkeypatch):
    """产物目录被改名为 JFLove-new 时，门禁必须查改名后的目录（不能查旧目录）"""
    build = _load_build_module()
    dist = tmp_path / "dist"
    # 旧目录齐全（干扰项）：不该被当成通过依据
    old = dist / "JFLove" / "_internal" / "winpty"
    old.mkdir(parents=True)
    for name in build.WINPTY_DATA_FILES:
        (old / name).write_bytes(b"MZ")

    monkeypatch.setattr(build, "platform", _fake_windows_platform())
    monkeypatch.setattr(build, "DIST_DIR", dist)
    monkeypatch.setattr(build, "APP_NAME", "JFLove-new")

    with pytest.raises(SystemExit) as exc:
        build.assert_winpty_bundled("onedir")
    assert exc.value.code == 1

    # 真产物补齐后放行
    new = dist / "JFLove-new" / "_internal" / "winpty"
    new.mkdir(parents=True)
    for name in build.WINPTY_DATA_FILES:
        (new / name).write_bytes(b"MZ")
    build.assert_winpty_bundled("onedir")


# ── 二、调用契约：spawn 参数 + 返回值 ────────────────────────────


class _FakePTY:
    """记录 spawn 入参的假 PTY（形状对齐 pywinpty 3.x `_winpty.PTY`）"""

    last: dict[str, object] = {}
    spawn_result = True

    def __init__(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows

    def spawn(self, appname, cmdline=None, cwd=None, env=None):  # noqa: ANN001
        type(self).last = {
            "appname": appname, "cmdline": cmdline, "cwd": cwd, "env": env,
        }
        return type(self).spawn_result

    def isalive(self) -> bool:  # noqa: D102 - 契约对齐
        return True

    def read(self, blocking: bool = False) -> str:  # noqa: D102 - 契约对齐
        return ""

    def write(self, data: str) -> int:  # noqa: D102 - 契约对齐
        return len(data)


def _install_fake_winpty(monkeypatch, spawn_result: bool = True) -> type[_FakePTY]:
    fake = types.ModuleType("winpty")
    _FakePTY.spawn_result = spawn_result
    _FakePTY.last = {}
    fake.PTY = _FakePTY
    monkeypatch.setitem(sys.modules, "winpty", fake)
    return _FakePTY


def _make_session(profile: dict | None):
    from src.services.local_terminal_service import LocalSession

    return LocalSession(profile, cols=100, rows=30)


def test_open_windows_uses_pywinpty_contract(tmp_path, monkeypatch):
    """appname 传可执行文件、cmdline 传参数、cwd 传绝对目录、env 传 \\0 串"""
    fake = _install_fake_winpty(monkeypatch)
    cwd = tmp_path / "work"
    cwd.mkdir()
    session = _make_session({
        "shell_path": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        "args": "-NoLogo",
        "cwd": str(cwd),
        "env_json": '{"JF_DEMO": "1"}',
    })

    session._open_windows()

    sent = fake.last
    assert sent["appname"] == "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    # 前导空格是 pywinpty 的 cmdline 约定（拼在 appname 后面），断言形态而非死字符串
    assert str(sent["cmdline"]).strip() == "-NoLogo"
    assert sent["cwd"] == str(cwd)
    # env 必须是 NUL 分隔的 K=V 字符串，且带上 profile 里的自定义变量
    assert isinstance(sent["env"], str)
    assert sent["env"].endswith("\0")
    assert "JF_DEMO=1\0" in sent["env"]


def test_open_windows_env_is_none_without_custom_env(monkeypatch, tmp_path):
    """没有自定义环境变量时 env 传 None（继承进程环境，不塞整份 environ）"""
    fake = _install_fake_winpty(monkeypatch)
    session = _make_session({"shell_path": "powershell.exe", "args": "", "cwd": ""})

    session._open_windows()

    assert fake.last["cmdline"] is None
    assert fake.last["env"] is None
    # 空 cwd 必须回退到存在的绝对目录（CreateProcess 不接受空串）
    assert isinstance(fake.last["cwd"], str)
    assert Path(str(fake.last["cwd"])).is_absolute()


def test_open_windows_raises_when_spawn_returns_false(monkeypatch):
    """spawn 返回 False = ConPTY 没起来 → 必须抛错（禁止再报"已打开"）"""
    _install_fake_winpty(monkeypatch, spawn_result=False)
    session = _make_session({"shell_path": "powershell.exe"})

    with pytest.raises(RuntimeError) as exc:
        session._open_windows()

    message = str(exc.value)
    assert "本机终端启动失败" in message
    assert "OpenConsole.exe" in message


def test_open_windows_wraps_winpty_error(monkeypatch):
    """pywinpty 抛的 Rust 英文错误必须包成中文 RuntimeError（并保留原因链）"""

    class _BoomPTY:
        def __init__(self, cols: int, rows: int) -> None:
            raise RuntimeError("ConPTY init failed: 0x80070002")

    fake = types.ModuleType("winpty")
    fake.PTY = _BoomPTY
    monkeypatch.setitem(sys.modules, "winpty", fake)
    session = _make_session({"shell_path": "powershell.exe"})

    with pytest.raises(RuntimeError) as exc:
        session._open_windows()

    message = str(exc.value)
    assert "本机终端启动失败" in message
    assert "OpenConsole.exe" in message  # 用户能直接看到排查方向
    assert "0x80070002" in message        # 原始原因不丢
    assert isinstance(exc.value.__cause__, RuntimeError)


# ── 三、失败可见性：打开结果补查（审查 S1）────────────────────────


def test_open_state_pending_then_ready_then_failed():
    """未上报 → pending；成功后 ready；失败带原因（渲染层建标签页后补查）"""
    from src.services import local_terminal_service as svc

    assert svc.get_open_state("s-unknown")["status"] == "pending"

    svc.remember_open_state("s1", "ready")
    assert svc.get_open_state("s1") == {"status": "ready", "message": ""}

    svc.remember_open_state("s2", "failed", "本机终端启动失败：powershell.exe")
    state = svc.get_open_state("s2")
    assert state["status"] == "failed"
    assert "本机终端启动失败" in state["message"]


def test_open_state_is_bounded():
    """状态表必须有上限（长期运行不能无限增长）"""
    from src.services import local_terminal_service as svc

    for i in range(svc._OPEN_STATE_LIMIT + 10):
        svc.remember_open_state(f"bounded-{i}", "ready")

    assert len(svc._OPEN_STATE) <= svc._OPEN_STATE_LIMIT
    # 最早的一批已被淘汰，返回 pending 而不是旧结果
    assert svc.get_open_state("bounded-0")["status"] == "pending"


def test_bridge_exposes_session_status_handler():
    """桥必须暴露 local_term.session_status（渲染层补查入口）"""
    from src.bridge import remote as remote_bridge

    assert "session_status" in remote_bridge.HANDLERS["local_term"]
    handler = remote_bridge.HANDLERS["local_term"]["session_status"]
    assert handler({"session_id": "no-such-session"})["status"] == "pending"

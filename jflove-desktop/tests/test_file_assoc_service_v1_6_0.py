"""
v1.6.0 文件关联服务（`src/services/file_assoc_service.py`）单元测试

设计依据：`文档记录/设计文档/v1.6.0.md`
  - §5.1 / §6.7.1：清单是**派生量**（`ui/public/file-assoc.manifest.json`），Python 侧只读它；
  - §6.7.4 + §7.1 #14 #15 #16：`file_assoc.get` / `set` / `openSystemDefaults` 的字段与错误语义；
  - §4.3 / D22：默认全不勾选；勾选状态落盘 `USER_DATA_DIR/storage/file_assoc.json`；
  - D18 / AC-43 ②：**不修改、不抢占任何扩展名的默认程序**。

覆盖点：
  1. `get()`：supported 来自 manifest（顺序保持）、selected 默认空、字段齐全、note 写明语义边界
  2. `set()`：归一去重 → 校验 → 原子写；`get()` 能读回（跨重启保留 = AC-44 的判据）
  3. `set()` 含不支持的扩展名 ⇒ **抛错且不写盘**（§7.1 #15"不静默过滤"）
  4. 原子写：同目录 `mkstemp` 随机名 + `os.replace`（**不是**固定 `.tmp` 名）；
     写失败时旧内容不变、不留残余临时文件
  5. manifest 缺失 ⇒ `get()` 不抛、`supported: []` + note；配置损坏 ⇒ 按空处理不抛
  6. 升级后清单缩短 ⇒ 陈旧勾选在 get 里被忽略并提示（读侧收口）
  7. 勾选**不改系统默认程序**：源码里没有注册表写 API / 不 shell out；Windows 上把
     `winreg` 的写 API 全部投毒后 `set()` / `get()` 仍正常工作
  8. `open_system_defaults()` 返回 `{ok, hint}`；非 Windows 平台 `ok=False` 并给出指引

安全口径：全部用例把 `settings.FILE_ASSOC_FILE` 与 manifest 路径指向 `tmp_path`，
**绝不写真实 USER_DATA_DIR**（AGENTS §9.4 / 设计 §7.4）。
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import settings
from src.services import editor_service, file_assoc_service

#: 夹具 manifest：字段形状照 §5.1（与真实生成物同构，只是条目少）
FIXTURE_MANIFEST = {
    "version": 1,
    "generatedFrom": "config/scratch-types.ts",
    "types": [
        {"ext": "json", "label": "JSON", "language": "json", "mime": ["application/json"]},
        {"ext": "md", "label": "Markdown", "language": "markdown", "mime": ["text/markdown"]},
        {"ext": "log", "label": "日志 (log)", "language": "text", "mime": ["text/plain"]},
        {"ext": "patch", "label": "Patch", "language": "diff", "mime": ["text/x-diff"]},
    ],
}
FIXTURE_EXTS = ["json", "md", "log", "patch"]

#: jflove-desktop 根目录（tests/ 的上一级）
_DESKTOP_ROOT = Path(__file__).resolve().parent.parent
#: 模块真身：autouse fixture 会把 `_manifest_path` 换成夹具路径（隔离用），
#: 「开发态 / 冻结态路径规则」用例需要真身，故在导入时先抓一份。
_REAL_MANIFEST_PATH = file_assoc_service._manifest_path


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """把勾选配置与 manifest 都指向 tmp_path（用例结束自动还原 settings）。"""
    storage = tmp_path / "storage"
    storage.mkdir()
    config = storage / "file_assoc.json"
    monkeypatch.setattr(settings, "FILE_ASSOC_FILE", str(config))

    manifest = tmp_path / "ui-public" / "file-assoc.manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps(FIXTURE_MANIFEST, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    monkeypatch.setattr(file_assoc_service, "_manifest_path", lambda: manifest)
    return SimpleNamespace(tmp=tmp_path, storage=storage, config=config, manifest=manifest)


def _write_config(path: Path, payload) -> None:
    """直接写配置文件（构造"上次运行留下的状态"）。"""
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ── 1. get() 的字段与来源 ────────────────────────────────────────────

def test_get_reports_supported_from_manifest(isolated):
    """supported 逐项来自 manifest 且**保持顺序**（唯一真相在 TS，不在这里手打）。"""
    info = file_assoc_service.get()
    assert info["supported"] == FIXTURE_EXTS
    assert info["selected"] == []          # 默认全不勾选（D22）
    assert info["platform"] in {"win32", "linux", "other"}
    assert isinstance(info["osRegistered"], bool)
    assert "不会修改系统默认程序" in info["note"]     # §6.7.4 的语义边界必须写清
    assert not isolated.config.exists()    # 只读不落盘


def test_supported_is_never_hardcoded_in_source():
    """扩展名清单不得手打在本文件里（§5.1 反例禁止）。

    用 AST 取**字符串常量**（排除 docstring）做等值比对，避免"注释里提到 json"这类误伤。
    """
    source = Path(file_assoc_service.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found = ast.get_docstring(node, clean=False)
            if found:
                docstrings.add(found)
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value not in docstrings
    }
    for ext in FIXTURE_EXTS:
        assert ext not in literals, f"Python 侧出现手打扩展名 {ext!r}（应全部来自 manifest）"


# ── 2. set() → get() 往返（AC-44 的持久化判据） ──────────────────────

def test_set_then_get_roundtrip(isolated):
    """勾选归一去重后落盘，重新读取仍在（跨重启保留）。"""
    result = file_assoc_service.set(["  .JSON ", "log", "json"])
    assert result == {"ok": True, "selected": ["json", "log"]}
    on_disk = json.loads(isolated.config.read_text(encoding="utf-8"))
    assert on_disk == {"version": 1, "selected": ["json", "log"]}
    assert file_assoc_service.get()["selected"] == ["json", "log"]


def test_set_empty_resets_to_none_selected(isolated):
    """置空数组 = 全不勾选（重置语义），仍然落盘 version=1。"""
    file_assoc_service.set(["md"])
    assert file_assoc_service.set([]) == {"ok": True, "selected": []}
    assert file_assoc_service.get()["selected"] == []


def test_normalize_ext_rules():
    """扩展名归一：去空白、去前导点、转小写；非字符串 → 空。"""
    assert file_assoc_service.normalize_ext("  .JSON ") == "json"
    assert file_assoc_service.normalize_ext("md") == "md"
    assert file_assoc_service.normalize_ext(None) == ""
    assert file_assoc_service.normalize_ext(123) == ""


# ── 3. set() 的错误语义：抛错、不静默过滤、不写盘 ────────────────────

def test_set_rejects_unsupported_ext_without_filtering(isolated):
    """含不支持的扩展名 ⇒ 抛错，且**一个都不写**（§7.1 #15）。"""
    with pytest.raises(ValueError) as excinfo:
        file_assoc_service.set(["json", "zzz", "exe"])
    message = str(excinfo.value)
    assert "zzz" in message and "exe" in message
    assert "json" in message or "未静默过滤" in message   # 合法项也不能被偷偷写入
    assert not isolated.config.exists(), "校验失败时不得写盘"


def test_set_rejects_non_list_and_non_string():
    """入参类型非法 ⇒ 抛错（桥层参数校验的最后一道）。"""
    with pytest.raises(ValueError):
        file_assoc_service.set("json")
    with pytest.raises(ValueError):
        file_assoc_service.set(["json", 42])


def test_set_refuses_when_manifest_missing(isolated, monkeypatch):
    """manifest 缺失时无法校验清单 ⇒ set 抛错（**不**写未经验证的勾选）。"""
    monkeypatch.setattr(file_assoc_service, "_manifest_path",
                        lambda: isolated.tmp / "nope" / "file-assoc.manifest.json")
    with pytest.raises(ValueError, match="无法保存"):
        file_assoc_service.set(["json"])
    assert not isolated.config.exists()


# ── 4. 原子写（§6.2.1） ──────────────────────────────────────────────

def test_atomic_write_uses_random_temp_in_same_dir(isolated, monkeypatch):
    """写盘走同目录 `mkstemp` 随机名 + `os.replace`，**不是**固定 `<name>.tmp`。"""
    seen: dict[str, str] = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["src"], seen["dst"] = str(src), str(dst)
        real_replace(src, dst)

    monkeypatch.setattr(editor_service, "_os_replace", spy)
    file_assoc_service.set(["log"])

    assert seen["dst"] == str(isolated.config)
    assert os.path.dirname(seen["src"]) == os.path.dirname(str(isolated.config))
    temp_name = os.path.basename(seen["src"])
    assert temp_name != f"{isolated.config.name}.tmp"      # 固定名是被禁的旧写法
    assert "jflove-" in temp_name and temp_name.endswith(".tmp")
    # 目录里只剩正式文件，没有临时残留
    assert [p.name for p in isolated.storage.iterdir()] == [isolated.config.name]


def test_write_failure_keeps_old_content_and_cleans_temp(isolated, monkeypatch):
    """写失败：旧内容逐字节不变、不留临时文件（AC-41 ④ 的同类口径）。"""
    file_assoc_service.set(["log"])
    before = isolated.config.read_bytes()

    def boom(*_args, **_kwargs):
        raise OSError("模拟 fsync 失败")

    monkeypatch.setattr(editor_service.os, "fsync", boom)
    with pytest.raises(OSError):
        file_assoc_service.set(["json"])

    assert isolated.config.read_bytes() == before
    assert [p.name for p in isolated.storage.iterdir()] == [isolated.config.name]


def test_corrupt_config_is_not_fatal(isolated):
    """配置损坏 ⇒ 按「全不勾选」处理并给出 note（不抛、不影响系统状态）。"""
    isolated.config.write_bytes(b"{ this is not json")
    info = file_assoc_service.get()
    assert info["selected"] == []
    assert "损坏" in info["note"]


def test_unknown_config_version_is_not_fatal(isolated):
    """版本不可识别 ⇒ 按空处理并提示（不做半懂不懂的兼容）。"""
    _write_config(isolated.config, {"version": 99, "selected": ["json"]})
    info = file_assoc_service.get()
    assert info["selected"] == []
    assert "版本" in info["note"]


def test_stale_selected_entries_are_ignored_on_read(isolated):
    """升级后清单缩短：陈旧勾选在 get 里被忽略并提示（写入侧仍然抛错）。"""
    _write_config(isolated.config, {"version": 1, "selected": ["json", "zzz"]})
    info = file_assoc_service.get()
    assert info["selected"] == ["json"]
    assert "已不在当前支持清单" in info["note"]


# ── 5. manifest 缺失不抛（§7.1 #14 的错误语义） ──────────────────────

def test_get_with_missing_manifest_does_not_raise(isolated, monkeypatch):
    missing = isolated.tmp / "gone" / "file-assoc.manifest.json"
    monkeypatch.setattr(file_assoc_service, "_manifest_path", lambda: missing)
    info = file_assoc_service.get()
    assert info["supported"] == []
    assert "清单" in info["note"]
    assert "不会修改系统默认程序" in info["note"]


def test_get_with_broken_manifest_does_not_raise(isolated):
    isolated.manifest.write_text("{ not json", encoding="utf-8")
    info = file_assoc_service.get()
    assert info["supported"] == []
    assert "无法解析" in info["note"]


def test_manifest_path_dev_and_frozen_rules(isolated, monkeypatch):
    """开发态读 `ui/public/`；冻结态读 `resolve_web_root()/`（§6.7.1，复用既有工具）。"""
    monkeypatch.setattr(file_assoc_service, "_manifest_path", _REAL_MANIFEST_PATH)
    monkeypatch.delattr(sys, "frozen", raising=False)
    dev_path = file_assoc_service._manifest_path()
    assert dev_path == _DESKTOP_ROOT / "ui" / "public" / file_assoc_service.MANIFEST_NAME

    fake_web_root = isolated.tmp / "webui"
    fake_web_root.mkdir()
    try:
        import src.ui.web_shell as web_shell
    except ImportError as exc:  # pragma: no cover - 无 QtWebEngine 的环境
        pytest.skip(f"无法 import src.ui.web_shell（{exc}）")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(web_shell, "resolve_web_root", lambda: str(fake_web_root))
    assert file_assoc_service._manifest_path() == fake_web_root / file_assoc_service.MANIFEST_NAME


# ── 6. 勾选不改系统默认程序（D18 / AC-43 ②） ─────────────────────────

def test_module_never_writes_system_state():
    """结构护栏：源码里没有任何写注册表 / shell out / 改默认程序的代码路径。"""
    source = Path(file_assoc_service.__file__).read_text(encoding="utf-8")
    for banned in ("SetValue", "CreateKey", "DeleteKey", "DeleteValue",
                   "subprocess", "os.system", "Popen"):
        assert banned not in source, f"文件关联服务不得出现 {banned}（D18 / AC-43 ②）"
    # 只读判定必须显式请求 KEY_READ（不会意外拿到可写句柄）
    assert "winreg.KEY_READ" in source


def test_set_and_get_still_work_with_registry_writers_poisoned(isolated, monkeypatch):
    """Windows 上把 winreg 的写 API 全部投毒：set()/get() 仍必须正常工作。"""
    if sys.platform != "win32":
        pytest.skip("仅 Windows 覆盖注册表写 API")
    winreg = pytest.importorskip("winreg")

    def boom(*_args, **_kwargs):
        raise AssertionError("file_assoc.set 不得写注册表（D18 / AC-43 ②）")

    for name in ("CreateKey", "CreateKeyEx", "SetValue", "SetValueEx",
                 "DeleteKey", "DeleteKeyEx", "DeleteValue"):
        if hasattr(winreg, name):
            monkeypatch.setattr(winreg, name, boom)

    assert file_assoc_service.set(["log"])["ok"] is True
    assert file_assoc_service.get()["selected"] == ["log"]


def test_set_only_touches_its_own_config_file(isolated):
    """`set()` 的落盘范围仅限 FILE_ASSOC_FILE（不碰系统默认程序、不写别的文件）。"""
    file_assoc_service.set(["log", "md"])
    assert [p.name for p in isolated.storage.iterdir()] == [isolated.config.name]


# ── 7. open_system_defaults（§7.1 #16） ──────────────────────────────

def test_open_system_defaults_shape():
    """返回 {ok, hint}；平台不支持时 ok=False 且必须有可执行指引。"""
    result = file_assoc_service.open_system_defaults()
    assert set(result) == {"ok", "hint"}
    assert isinstance(result["ok"], bool)
    assert isinstance(result["hint"], str) and result["hint"]
    if sys.platform != "win32":
        assert result["ok"] is False
        assert "默认应用" in result["hint"]


def test_open_system_defaults_does_not_change_anything(isolated):
    """只打开设置页：不写配置文件、不改勾选状态。"""
    file_assoc_service.open_system_defaults()
    assert not isolated.config.exists()

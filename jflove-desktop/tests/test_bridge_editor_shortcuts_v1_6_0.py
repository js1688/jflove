"""
v1.6.0 快捷键偏好桥方法用例（`editor.shortcutsLoad` / `editor.shortcutsSave`）

**为什么有这条用例**：设计 §4.2.4 规定了快捷键偏好的存储位置与结构
（`USER_DATA_DIR/storage/shortcuts.json`）与"渲染层禁止直接访问文件系统 / 禁止 localStorage"
（R-1 实测默认 profile 为 off-the-record），但 **§7.1 的桥方法表没有列出读写方法** ——
属设计缺口。实现按命名空间一致性补齐 `editor.shortcutsLoad` / `editor.shortcutsSave`，
本文件把它的契约钉住（覆盖缺口登记见 `BACKLOG.md` BL-17）。

覆盖点：
  * 文件缺失 / JSON 损坏 / 结构不合法 ⇒ 返回 `{version: 0, bindings: {}}`，**不抛**（不阻塞启动）
  * 往返：`save` → `load` 保值（含 `blockSelectModifier`）
  * 原子写：目录里不留临时文件；父目录缺失时不静默半写
  * 校验：`version` 非整数 / `bindings` 非对象 / 键值非字符串 / 超长 / 超量 ⇒ **抛 ValueError**
  * 登记：两个方法在 `HANDLERS` 里，且**不在** `MAIN_THREAD_METHODS`（磁盘 I/O 走工作线程）
"""

from __future__ import annotations

import json

import pytest

from src.bridge import editor as editor_bridge
from src.config import settings


@pytest.fixture(autouse=True)
def _isolated_settings_file(tmp_path, monkeypatch):
    """把快捷键偏好文件指向临时目录，**绝不写真实 USER_DATA_DIR**"""
    monkeypatch.setattr(settings, "SHORTCUTS_FILE", str(tmp_path / "shortcuts.json"))
    return tmp_path


class Test读取容错:
    """`load` 的任何异常都不得阻塞启动（§4.2.4 的 load 语义）"""

    def test_文件不存在时回默认(self, _isolated_settings_file):
        assert editor_bridge._h_editor_shortcuts_load({}) == {"version": 0, "bindings": {}}

    def test_json损坏时回默认且不抛(self, _isolated_settings_file):
        (_isolated_settings_file / "shortcuts.json").write_text("{ 这不是 JSON", encoding="utf-8")
        assert editor_bridge._h_editor_shortcuts_load({}) == {"version": 0, "bindings": {}}

    @pytest.mark.parametrize("payload", [
        "[]",                                    # 顶层不是对象
        '{"version": "1", "bindings": {}}',      # version 非整数
        '{"version": 1, "bindings": []}',        # bindings 非对象
        '{"version": true, "bindings": {}}',     # version 是 bool（bool 是 int 子类，必须排除）
    ])
    def test_结构不合法时回默认(self, _isolated_settings_file, payload):
        (_isolated_settings_file / "shortcuts.json").write_text(payload, encoding="utf-8")
        assert editor_bridge._h_editor_shortcuts_load({}) == {"version": 0, "bindings": {}}

    def test_过滤非法条目但保留合法项(self, _isolated_settings_file):
        long_key = "x" * (editor_bridge.MAX_SHORTCUT_KEY_CHARS + 1)
        long_id = "y" * (editor_bridge.MAX_SHORTCUT_ID_CHARS + 1)
        raw = {
            "version": 1,
            "bindings": {
                "file.save": "Ctrl+S",       # 合法
                "bad.number": 1,             # 值非字符串 → 丢
                long_id: "Ctrl+Q",           # id 超长 → 丢
                "bad.long": long_key,        # 键位超长 → 丢
            },
            "blockSelectModifier": "Ctrl",
        }
        (_isolated_settings_file / "shortcuts.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        loaded = editor_bridge._h_editor_shortcuts_load({})
        assert loaded["version"] == 1
        assert loaded["bindings"] == {"file.save": "Ctrl+S"}
        assert loaded["blockSelectModifier"] == "Ctrl"

    def test_非法修饰键字段被忽略(self, _isolated_settings_file):
        (_isolated_settings_file / "shortcuts.json").write_text(
            '{"version": 1, "bindings": {}, "blockSelectModifier": 7}', encoding="utf-8")
        assert "blockSelectModifier" not in editor_bridge._h_editor_shortcuts_load({})


class Test写入与往返:
    def test_往返保值(self, _isolated_settings_file):
        payload = {
            "version": 1,
            "bindings": {"file.save": "Ctrl+Shift+S", "edit.find": "Ctrl+F"},
            "blockSelectModifier": "Alt",
        }
        assert editor_bridge._h_editor_shortcuts_save(payload) == {"ok": True, "saved": 2}
        loaded = editor_bridge._h_editor_shortcuts_load({})
        assert loaded == payload

    def test_原子写不留临时文件(self, _isolated_settings_file):
        editor_bridge._h_editor_shortcuts_save(
            {"version": 1, "bindings": {"file.open": "Ctrl+O"}}
        )
        leftovers = [p.name for p in _isolated_settings_file.iterdir()]
        assert leftovers == ["shortcuts.json"], f"目录里出现了临时文件：{leftovers}"

    def test_中文与符号键位往返(self, _isolated_settings_file):
        payload = {"version": 1, "bindings": {"view.toolbox": "Ctrl+Alt+→"}}
        editor_bridge._h_editor_shortcuts_save(payload)
        loaded = editor_bridge._h_editor_shortcuts_load({})
        assert loaded["bindings"]["view.toolbox"] == "Ctrl+Alt+→"

    def test_省略修饰键字段时不写入该键(self, _isolated_settings_file):
        editor_bridge._h_editor_shortcuts_save({"version": 1, "bindings": {}})
        raw = json.loads((_isolated_settings_file / "shortcuts.json").read_text(encoding="utf-8"))
        assert "blockSelectModifier" not in raw


class Test写入校验:
    """结构非法一律抛错：渲染层会保留内存状态并提示，不静默丢弃用户改动"""

    @pytest.mark.parametrize("payload", [
        {"bindings": {}},                                  # 缺 version
        {"version": "1", "bindings": {}},                  # version 非整数
        {"version": True, "bindings": {}},                 # version 是 bool
        {"version": 1, "bindings": []},                    # bindings 非对象
        {"version": 1, "bindings": {"file.save": 1}},      # 值非字符串
        {"version": 1, "bindings": {"": "Ctrl+S"}},        # 空 id
        {"version": 1, "bindings": {"x" * 65: "Ctrl+S"}},  # id 超长
        {"version": 1, "bindings": {"file.save": "x" * 65}},  # 键位超长
    ])
    def test_非法结构抛ValueError(self, _isolated_settings_file, payload):
        with pytest.raises(ValueError):
            editor_bridge._h_editor_shortcuts_save(payload)

    def test_超量绑定抛ValueError(self, _isolated_settings_file):
        too_many = {f"k{i}": "Ctrl+A" for i in range(editor_bridge.MAX_SHORTCUT_BINDINGS + 1)}
        with pytest.raises(ValueError):
            editor_bridge._h_editor_shortcuts_save({"version": 1, "bindings": too_many})

    def test_校验失败时不落盘(self, _isolated_settings_file):
        with pytest.raises(ValueError):
            editor_bridge._h_editor_shortcuts_save({"version": "bad", "bindings": {}})
        assert list(_isolated_settings_file.iterdir()) == []


class Test登记:
    def test_两个方法已登记(self):
        assert "shortcutsLoad" in editor_bridge.HANDLERS["editor"]
        assert "shortcutsSave" in editor_bridge.HANDLERS["editor"]

    def test_两个方法不在主线程集合(self):
        """磁盘 I/O 不进 GUI 线程（§7.1 的主线程口径）"""
        assert ("editor", "shortcutsLoad") not in editor_bridge.MAIN_THREAD_METHODS
        assert ("editor", "shortcutsSave") not in editor_bridge.MAIN_THREAD_METHODS

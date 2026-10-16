"""
v1.6.0 Phase 6 · 无全局热键（AC-8 ① 全仓静态判据）

判据（设计 §4.2.2 / AC-8）：
  * **全仓代码 0 命中**全局热键注册 API —— `RegisterHotKey` / `globalShortcut` /
    `QHotkey` / `nativeShortcut` / `keybinder` / `pynput` / `keyboard.`；
  * 快捷键只走 **DOM 捕获阶段 `keydown`** + `document.hasFocus() && !document.hidden` 自查
    （本机「失焦后按 Ctrl+S / Ctrl+N / F6 无行为」的实现前提）。

扫描只看代码（注释与文档不算判据）：`文档记录/` 与 `plans/` 整目录排除、`.md` 不在后缀表内，
第三方离线包 `ui/public/vendor/**` 也排除（压缩产物，不是本仓代码）。规则见 `tests/code_scan.py`。

**给 P6-5 的约束（r11 真机自动化）**：要注入按键请用 `SendKeys` / `user32.SendInput`，
**不得**引入 `pynput` / `keyboard` 这类会注册全局钩子的库 —— 否则本用例必须失败。

真机半边（失焦后按键无行为）由 `poc/editor-probes/r11-real-gui-drive.py` 实跑判定。
"""
from __future__ import annotations

from tests.code_scan import (
    DESKTOP_ROOT,
    DEFAULT_SKIP_DIRS,
    READ_ERRORS,
    iter_repo_code_files,
    scan,
)

#: 全局热键注册 / 全局键盘钩子 API（AC-8 与审查报告 A4 用同一组名字）
HOTKEY_TOKENS: tuple[str, ...] = (
    "RegisterHotKey",
    "globalShortcut",
    "QHotkey",
    "nativeShortcut",
    "keybinder",
    "pynput",
    "keyboard.",
)

#: 扫描面必须覆盖到的哨兵文件（防止跳目录规则把整块代码排除掉）
#: v1.6.1：浮窗层 `ScratchPaneLayer.tsx` 已删除（设计 v1.6.1 §4.2 / DS-22），
#: 应用级快捷键监听搬到模块级运行时 `utils/scratch/shortcut-runtime.ts` ⇒ 哨兵换点。
REQUIRED_IN_SCOPE: tuple[str, ...] = (
    "jflove-desktop/src/bridge/editor.py",
    "jflove-desktop/src/ui/web_shell.py",
    "jflove-desktop/ui/src/utils/scratch/shortcut-runtime.ts",
    "jflove-server/src/main.py",
    "jflove-app/lib/main.dart",
    "jflove-web/src/App.tsx",
)

SHORTCUT_RUNTIME = (
    DESKTOP_ROOT / "ui" / "src" / "utils" / "scratch" / "shortcut-runtime.ts"
)
SHORTCUTS_DIALOG = (
    DESKTOP_ROOT / "ui" / "src" / "components" / "scratch" / "ShortcutsSettingsDialog.tsx"
)

#: 判据自身的豁免名单（**只允许这一份**）：它把关键字写成了字符串表，
#: 扫自己必然命中；产品代码不在其中，见 `test_自身豁免名单不得扩大`。
SELF_EXCLUDED: frozenset[str] = frozenset({"test_v160_phase6_hotkeys.py"})


class TestNoGlobalHotkeyRegistration:
    """AC-8 ①：全仓代码里没有任何全局热键注册入口"""

    def test_全仓代码零命中全局热键_API(self):
        files = [
            path for path in iter_repo_code_files() if path.name not in SELF_EXCLUDED
        ]
        hits = scan(files, HOTKEY_TOKENS)
        assert hits == [], "检出全局热键注册 API：\n" + "\n".join(hits)

    def test_自身豁免名单不得扩大(self):
        """本用例把关键字写在表里，扫自己必然命中 —— 因此**只**豁免判据自己这份文件；
        名单一旦变长，等于给产品代码开后门，所以在这里钉死。"""
        assert SELF_EXCLUDED == {"test_v160_phase6_hotkeys.py"}

    def test_扫描面覆盖四端且没有静默漏读(self):
        """守卫用例：范围太窄（比如跳目录跳过头）会让上面那条「全绿」变得没有意义"""
        files = list(iter_repo_code_files())
        relative = {
            str(path.relative_to(DESKTOP_ROOT.parent)).replace("\\", "/") for path in files
        }
        missing = [item for item in REQUIRED_IN_SCOPE if item not in relative]
        assert missing == [], f"扫描面漏掉了哨兵文件：{missing}"
        assert len(files) >= 300, f"扫描面过窄（{len(files)} 个文件）"
        assert READ_ERRORS == [], f"有文件没读进来（判据会失真）：{READ_ERRORS}"

    def test_跳过的目录只有依赖产物与文档(self):
        """`DEFAULT_SKIP_DIRS` 不得把任何**代码目录**排除掉（否则等于自废判据）"""
        assert "文档记录" in DEFAULT_SKIP_DIRS and "plans" in DEFAULT_SKIP_DIRS
        assert "src" not in DEFAULT_SKIP_DIRS
        assert "ui" not in DEFAULT_SKIP_DIRS
        assert "lib" not in DEFAULT_SKIP_DIRS


class TestShortcutListenerStaysDomScoped:
    """AC-8 的另一半：快捷键只挂 DOM 监听，且挂之前先自查焦点与可见性"""

    def test_应用级快捷键走_window_捕获阶段(self):
        """v1.6.1：监听搬到了模块级运行时 `utils/scratch/shortcut-runtime.ts`
        （`ScratchPaneLayer.tsx` 已删除；常驻理由见该文件头与设计 §4.7 / 13.2 U-8）。"""
        text = SHORTCUT_RUNTIME.read_text(encoding="utf-8")
        assert "window.addEventListener('keydown', listener, true)" in text, (
            "应用级快捷键必须挂在 window 的**捕获阶段**（第三参数 true）"
        )
        assert "window.removeEventListener('keydown', listener, true)" in text, (
            "卸载时必须以同样的捕获参数摘掉监听，否则会残留"
        )
        assert "!document.hasFocus() || document.hidden" in text, (
            "失焦/不可见时必须直接 return（AC-8 真机判据的实现前提）"
        )

    def test_快捷键配置对话框同样自查焦点(self):
        text = SHORTCUTS_DIALOG.read_text(encoding="utf-8")
        assert "window.addEventListener('keydown', onKeyDown, true)" in text
        assert "window.removeEventListener('keydown', onKeyDown, true)" in text
        assert "!document.hasFocus() || document.hidden" in text

    def test_渲染层不导入任何全局热键模块(self):
        """渲染层的 import 里不得出现热键/钩子包（与上面的 token 扫描互为印证）"""
        source_root = DESKTOP_ROOT / "ui" / "src"
        offenders: list[str] = []
        for path in sorted(source_root.rglob("*.ts*")):
            if "__tests__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("import ") and any(
                    token in stripped for token in HOTKEY_TOKENS
                ):
                    offenders.append(f"{path.relative_to(DESKTOP_ROOT)}: {stripped}")
        assert offenders == [], f"渲染层导入了热键相关模块：{offenders}"

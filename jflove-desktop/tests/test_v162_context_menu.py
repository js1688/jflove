"""v1.6.2 外壳右击菜单单测（需求 F13 / AC-85 / AC-86 / AC-87；设计 §3.4 DS-52）

## 判据（全部跑在**纯函数** `context_menu_spec()` 上，不起 GUI）

| 用例组 | 判据 |
|--------|------|
| 中文文案 | 菜单里每一条**可见文案都含中日韩字符**；0 命中 Chromium 英文项与浏览器导航项 |
| 弹与不弹 | 可编辑区 ⇒ 有菜单；输入框（只有 `paste` 可用）⇒ 有菜单；普通页面（只有 `copy` / `selectAll`）⇒ `None` |
| 可用性 | 逐项跟随 `editFlags`（无 `CanPaste` ⇒ 粘贴禁用；无 `CanUndo` ⇒ 撤销禁用） |
| 结构 | 首尾不出现多余分隔符；不出现「删除」（Qt 未暴露该 WebAction，见实现注释） |
| 能力面（AC-87） | 本模块**零** `runJavaScript` / 零剪贴板读取点；`JavascriptAccessClipboard` 仍未开 |

⚠ 本文件**不**验证"真机上右击真的弹出这个菜单"（那需要 GUI，属 Phase 6 的 `r22` 真机探针）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESKTOP))

from src.ui.web_shell import (  # noqa: E402  （须在 sys.path 调整之后导入）
    _CONTEXT_MENU_ENTRIES,
    _EDIT_LIKE_ACTIONS,
    _WEB_ACTION_BY_NAME,
    context_menu_spec,
)

#: 编辑器正文（CM6 是 contenteditable）能用的完整动作集
FULL_FLAGS = frozenset({'undo', 'redo', 'cut', 'copy', 'paste', 'selectAll'})

#: 普通页面选中一段文字时的动作集（Chromium 只给 copy / selectAll）
PLAIN_PAGE_FLAGS = frozenset({'copy', 'selectAll'})

#: 常被 Chromium 英文菜单带出来、本版**必须**一个都不出现的项（含导航类与英文编辑类）
FORBIDDEN_LABELS = (
    'Back',
    'Forward',
    'Reload',
    'Save page',
    'View page source',
    'Inspect',
    'Copy image',
    'Copy link',
    'Download',
    'Cut',
    'Copy',
    'Paste',
    'Undo',
    'Redo',
    'Select all',
    'Delete',
)

CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')


def labels_of(spec) -> list[str]:
    """取菜单里的可见文案（分隔符不算文案）"""
    return [label for label, action, _enabled in spec if action]


def test_editor_context_has_menu_and_all_labels_are_chinese() -> None:
    spec = context_menu_spec(True, FULL_FLAGS)
    assert spec is not None, '可编辑区必须给菜单（AC-86①）'
    labels = labels_of(spec)
    assert labels, '菜单不能是空的'
    for label in labels:
        assert CJK.search(label), f'菜单项「{label}」不是中文文案（AC-85）'


def test_no_english_or_browser_items_anywhere() -> None:
    spec = context_menu_spec(True, FULL_FLAGS)
    assert spec is not None
    joined = '｜'.join(labels_of(spec))
    for forbidden in FORBIDDEN_LABELS:
        assert forbidden not in joined, f'菜单里出现了不该有的项：{forbidden}'


def test_plain_page_gets_no_menu() -> None:
    """页面背景 / 只读预览 / 表格 / 树形 / 标签条：**不得**弹菜单（AC-86②）"""
    assert context_menu_spec(False, PLAIN_PAGE_FLAGS) is None
    assert context_menu_spec(False, frozenset()) is None
    assert context_menu_spec(False, frozenset({'copy'})) is None


def test_input_contexts_are_content_editable_and_get_menu() -> None:
    """
    AC-86③ / D35② 的唯一例外：**可编辑上下文（含 `<input>` / `<textarea>`）都给中文菜单**。

    ⚠ **真机取证（`r22` 的 `C-INPUT`）已经关闭设计里的 R-19**：右击登录页 `<input>` 时
    Chromium 上报的就是 `isContentEditable() == True` ⇒ 判据只需这一个，**不需要**基于
    `editFlags` 的兜底；而那个兜底是**有害**的 —— 真机实测「焦点还在编辑器里时右击标签条」，
    Chromium 仍报 `CanPaste=True`，兜底会把菜单**误弹到非可编辑区**（`C-PLAIN` 抓到）。
    因此这里断言：可编辑 ⇒ 有菜单；**只有 `editFlags` 可用但 `isContentEditable=False` ⇒ 无菜单**。
    """
    spec = context_menu_spec(True, frozenset({'paste', 'selectAll'}))
    assert spec is not None, '可编辑上下文必须给菜单'
    assert '粘贴' in labels_of(spec)

    # 反例（真机 C-PLAIN 的形态）：非可编辑 + 焦点残留导致 paste 可用 ⇒ 仍**不得**弹菜单
    assert context_menu_spec(False, frozenset({'paste', 'selectAll'})) is None
    assert context_menu_spec(False, _EDIT_LIKE_ACTIONS) is None


def test_enabled_state_follows_edit_flags() -> None:
    spec = context_menu_spec(True, frozenset({'undo', 'selectAll'}))
    assert spec is not None
    state = {action: enabled for _label, action, enabled in spec if action}
    assert state['undo'] is True
    assert state['redo'] is False
    assert state['paste'] is False
    assert state['copy'] is False
    assert state['selectAll'] is True


def test_separators_are_trimmed_and_ordered() -> None:
    spec = context_menu_spec(True, FULL_FLAGS)
    assert spec is not None
    assert spec[0][1] != '', '首项不得是分隔符'
    assert spec[-1][1] != '', '末项不得是分隔符'
    # 顺序 = 表序（撤销 → 重做 → ─ → 剪切 → 复制 → 粘贴 → ─ → 全选）
    assert [action for _label, action, _enabled in spec] == [
        'undo',
        'redo',
        '',
        'cut',
        'copy',
        'paste',
        '',
        'selectAll',
    ]


def test_delete_is_absent_on_purpose() -> None:
    """
    「删除」项**刻意不做**：Qt 6 的 `WebAction` 不暴露 Delete

    ⇒ 唯一实现路径是 `runJavaScript('document.execCommand(...)')`，那会新增
    "外壳 → 渲染层执行 JS"的能力面（本模块此前零 `runJavaScript`），与 §9.6 相悖。
    """
    spec = context_menu_spec(True, FULL_FLAGS)
    assert spec is not None
    actions = [action for _label, action, _enabled in spec]
    assert 'delete' not in actions
    assert 'delete' not in _WEB_ACTION_BY_NAME
    assert all(entry is None or entry[1] != 'delete' for entry in _CONTEXT_MENU_ENTRIES)


def test_every_menu_action_has_a_qt_web_action() -> None:
    """菜单里除分隔符外的每一项都必须有 Qt `WebAction` 可转发（否则点了没反应）"""
    for _label, action in [entry for entry in _CONTEXT_MENU_ENTRIES if entry is not None]:
        assert action in _WEB_ACTION_BY_NAME, f'{action} 没有对应的 Qt WebAction'


def test_capability_surface_not_expanded() -> None:
    """
    AC-87：本版**没有**为右击菜单新增能力面。

    - 外壳不执行任何 JS（AST 层面：零 `runJavaScript` / `execCommand` **调用**）；
    - 仍未开渲染层剪贴板权限（AST 层面：零 `JavascriptCanAccessClipboard` 属性访问）；
    - 动作一律经 `QWebEnginePage.triggerAction()` 交给 Chromium 自己执行。

    ⚠ 用 **AST** 而不是文本 grep：本模块的中文注释里**故意**写着"不采用
    `runJavaScript('document.execCommand(...)')`"这样的反面说明，文本 grep 会把解释性文字
    误判成能力面（这正是 `LESSONS.md` L30「判据要咬住事实，不要咬住字面」的同类问题）。
    """
    import ast

    source = (DESKTOP / 'src' / 'ui' / 'web_shell.py').read_text(encoding='utf-8')
    tree = ast.parse(source)

    called: set[str] = set()
    attribute_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        if isinstance(node, ast.Attribute):
            attribute_names.add(node.attr)

    assert 'runJavaScript' not in called, '外壳不得为右击菜单引入 JS 执行通道'
    assert 'execCommand' not in called
    assert 'triggerAction' in called, '动作必须转发给 Chromium 自己执行'
    assert 'JavascriptCanAccessClipboard' not in attribute_names, '渲染层剪贴板权限不得被打开'


@pytest.mark.parametrize('action', sorted(FULL_FLAGS))
def test_single_action_contexts_still_enable_that_action(action: str) -> None:
    """只给一个动作时，该项必须可用、其余禁用（可用性不允许被写死）"""
    spec = context_menu_spec(True, frozenset({action}))
    assert spec is not None
    state = {name: enabled for _label, name, enabled in spec if name}
    assert state[action] is True
    assert sum(1 for value in state.values() if value) == 1

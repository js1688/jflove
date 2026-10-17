#!/usr/bin/env python3
"""r23 键位冲突探针：**新增快捷键是否抢了 CM6 编辑器自己的键位**

## 为什么必须单独有一条

我们的快捷键运行时挂在 `window` 的**捕获阶段**，命中即 `preventDefault + stopPropagation`
⇒ 一旦某个组合与 CM6 的 keymap 重叠，编辑器那个功能就被**静默抢掉**。
v1.6.1 的 `view.diff` 原定 `Ctrl+D`（= CM6 `searchKeymap` 的 `selectNextOccurrence`，
"选中下一个相同词"）就是因此让回 `Ctrl+Shift+D`；v1.6.2 的 `toolbox.lines` 原定
`Ctrl+Shift+L`，也在 `searchKeymap` 里（`Mod-Shift-l` = `selectSelectionMatches`，
"选中所有相同项"）⇒ 同样让回 `Ctrl+Shift+P`。**这条纪律靠本探针机械化**，不再靠人记。

## 判据

对给定的键位清单（默认 = `ui/src/config/shortcuts.ts` 里 `scope: 'pane'` 的**全部**条目：
工具箱 11 条 + 编辑器级基础项），逐条在**已装配的 8 张 keymap** 里查重：

| 结果 | 含义 |
|------|------|
| `0 命中` | 可安全接管（我们抢它不会丢编辑器功能） |
| 有命中 | **必须换键**（或明写在文档里"这是有意抢"） |

「已装配的 keymap」以 `ScratchEditorHost.tsx::baseExtensions` 的 `keymap.of([...])` 为准
（`defaultKeymap` / `historyKeymap` / `searchKeymap` / `foldKeymap` / `closeBracketsKeymap` /
`completionKeymap` / `indentWithTab` + 块选择自绘 keymap）。

## 怎么跑

    jflove-desktop\\venv-win\\Scripts\\python.exe poc\\editor-probes\\r23-keymap-collision.py
    ... --letters F,L,U,E,B,J,M,T,Q,X,O        # 临时查任意字母组合（Ctrl+Shift+X）

## 退出码

- `0` 清单里每条都 0 命中
- `1` 有命中（清单里有键位抢了 CM6）
- `2` 环境问题（离线包缺失 / 读不出键位）

## 边界

只读 `ui/public/vendor/editor.bundle.js`（构建产物，由 `scripts/build-codemirror-bundle.mjs` 生成）；
不写任何文件、不起 GUI、不碰业务数据。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
PROBES = SCRIPT.parent
DESKTOP = SCRIPT.parents[2]
BUNDLE = DESKTOP / "ui" / "public" / "vendor" / "editor.bundle.js"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

#: 从 shortcuts.ts 抠出 `scope: 'pane'` 条目的默认键（不 import TS，只做静态解析）
DEF_BLOCK = re.compile(
    r"id:\s*'(?P<id>[^']+)'.*?defaultKey:\s*'(?P<key>[^']+)'.*?scope:\s*'(?P<scope>[^']+)'",
    re.S,
)

#: 字母 → CM6 可能写出的等价形态（`Mod` 在 Windows = Ctrl）
LETTER_FORMS = (
    "Mod-Shift-{L}",
    "Shift-Mod-{L}",
    "Ctrl-Shift-{L}",
    "Shift-Ctrl-{L}",
)


def pane_level_keys() -> list[tuple[str, str]]:
    """从 `config/shortcuts.ts` 抠出编辑器级条目的 (id, defaultKey)"""
    source = (DESKTOP / "ui" / "src" / "config" / "shortcuts.ts").read_text(encoding="utf-8")
    found: list[tuple[str, str]] = []
    for match in DEF_BLOCK.finditer(source):
        if match.group("scope") != "pane":
            continue
        found.append((match.group("id"), match.group("key")))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="r23：新增快捷键 vs CM6 keymap 查重")
    parser.add_argument("--letters", default="", help="临时查这批字母（Ctrl+Shift+<字母>，逗号分隔）")
    args = parser.parse_args()

    if not BUNDLE.exists():
        print(f"!! 离线包不存在：{BUNDLE}（先跑 `python build.py -m desktop` 或 "
              f"`node scripts/build-codemirror-bundle.mjs`）")
        return 2
    source = BUNDLE.read_text(encoding="utf-8", errors="replace")
    bundle_keys = set(re.findall(r'key:"([^"]+)"', source))
    if not bundle_keys:
        print("!! 离线包里读不到任何键位（bundle 结构变了？）")
        return 2

    if args.letters:
        targets = [(f"ctrl+shift+{letter.strip().lower()}", f"Ctrl+Shift+{letter.strip().upper()}")
                   for letter in args.letters.split(",") if letter.strip()]
    else:
        targets = pane_level_keys()

    print(f"离线包键位总数：{len(bundle_keys)}")
    print("已装配 keymap 里的 Shift 组合（供挑选替代键）：")
    for key in sorted(k for k in bundle_keys if "Shift" in k):
        print(f"  {key}")

    hits: list[tuple[str, str, list[str]]] = []
    print("---- 逐条查重 ----")
    for item_id, binding in targets:
        chord = re.fullmatch(r"Ctrl\+Shift\+([A-Za-z])", binding or "")
        if not chord:
            print(f"  {item_id:28s} {binding:16s} 跳过（本探针只查 Ctrl+Shift+<字母>）")
            continue
        letter = chord.group(1)
        forms = [form.format(L=letter) for form in LETTER_FORMS]
        matched = sorted(k for k in bundle_keys if k in forms)
        status = "0 命中" if not matched else f"**有命中** {matched}"
        print(f"  {item_id:28s} {binding:16s} {status}")
        if matched:
            hits.append((item_id, binding, matched))

    print("---- 结论 ----")
    if hits:
        for item_id, binding, matched in hits:
            print(f"!! {item_id}（{binding}）与 CM6 keymap 冲突：{matched}"
                  f" ⇒ 必须换键，或在文档里明写「有意抢」")
        return 1
    print("全部 0 命中：这些组合可以安全接管。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""r8 探针：v1.6.0 审查项 S-5 / S-6 / S-8 / M-13 / S-9 的**接线判据**（静态，可证伪）。

## 为什么是静态探针

`jflove-desktop/ui/` 本版本**没有测试运行器**（BACKLOG BL-14 / 审查 T-3），Phase 6 才会补组件测试。
在那之前，"工具箱到底挂没挂上""viewMode 到底有没有消费者""Ctrl+滚轮有没有监听器"这类
**渲染层存在性**必须有一个可执行的判据 —— 否则修完只能靠肉眼，下一轮回归无人拦截。

本探针逐条断言源码里存在**接线锚点**；任一锚点缺失即退出码 1。
它**不替代** Phase 6 的 DOM 断言（那时用真渲染判断"菜单里恰好 12 项"），只保证接线不被悄悄拆掉。

## 用法

    python poc/editor-probes/r8-ui-wiring.py        # 退出码 0 = 全部接线在位

## 负向对照（探针可信性）

把 `ScratchPane.tsx` 里 `hidden={!showHost}` 改成 `hidden={false}`（= 宿主在任何视图下都可见，
预览/表格视图会与编辑器叠在一起）后重跑 ⇒ 必须报 FAIL 且退出码 1（2026-10-15 实测如此）。
"""
from __future__ import annotations

import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[2]
REPO = DESKTOP.parent

SCRATCH_DIR = DESKTOP / "ui" / "src" / "components" / "scratch"
STORE = DESKTOP / "ui" / "src" / "stores" / "scratch-store.ts"
CSS = DESKTOP / "ui" / "src" / "styles" / "scratch-editor.css"
BLOCK_SELECT = DESKTOP / "ui" / "src" / "utils" / "scratch" / "block-select.ts"
REQUIREMENT = REPO / "文档记录" / "需求文档" / "v1.6.0.md"

# (来源文件, 说明, 必须出现的片段列表)
CHECKS: list[tuple[Path, str, list[str]]] = [
    (
        SCRATCH_DIR / "ScratchPane.tsx",
        "S-5 工具箱已挂载（12 项菜单 / 查找替换 / diff 入口可达）",
        [
            "import { ScratchToolbar } from './ScratchToolbar';",
            "<ScratchToolbar pane={pane} />",
        ],
    ),
    (
        SCRATCH_DIR / "ScratchPane.tsx",
        "S-6 viewMode 有渲染消费者（五态分派 + 可断言属性）",
        [
            "data-view-mode={viewMode}",
            "const showHost = viewMode === 'edit' || viewMode === 'split';",
            "const showMarkdown = viewMode === 'preview' || viewMode === 'split';",
            "<MarkdownSplitView pane={pane} text={viewText} showEditor={false} />",
            "<CsvTableView text={viewText} />",
            "<JsonTreeView text={viewText} />",
        ],
    ),
    (
        SCRATCH_DIR / "ScratchPane.tsx",
        "S-6 宿主在非编辑视图下只隐藏、不卸载（否则丢正文）",
        [
            'className="scratch-pane-host" hidden={!showHost}',
            "<ScratchEditorHost pane={pane} className=\"scratch-host\" />",
        ],
    ),
    (
        CSS,
        "S-6 布局类存在且补回了 UA 的 [hidden]（作者样式的 display 会盖掉它）",
        [".scratch-pane-body", ".scratch-pane-host[hidden]", ".scratch-pane-aux"],
    ),
    (
        SCRATCH_DIR / "ScratchEditorHost.tsx",
        "S-8 Ctrl+滚轮缩放字号 / Shift+滚轮横向滚动（非 passive 原生监听）",
        [
            "element.addEventListener('wheel', onWheel, { passive: false });",
            "event.ctrlKey",
            "stepFontSize(",
            "view.scrollDOM.scrollLeft += event.deltaY;",
            "ref={wrapperRef}",
        ],
    ),
    (
        SCRATCH_DIR / "index.ts",
        "S-5 barrel 补导出工具栏与只读视图",
        [
            "export { ScratchToolbar, type ScratchToolbarProps } from './ScratchToolbar';",
            "export { CsvTableView, type CsvTableViewProps } from './views/CsvTableView';",
            "export { JsonTreeView, type JsonTreeViewProps } from './views/JsonTreeView';",
        ],
    ),
    (
        STORE,
        "M-13 打开文件一律先落编辑器视图（类型默认视图只用于切语言推荐）",
        ["viewMode: 'edit',"],
    ),
    (
        BLOCK_SELECT,
        "S-9 AC-11 期望串以实现为准（1 基第 2~4 列 ⇒ jkl）",
        ["expected: 'bcd\\nh  \\njkl',"],
    ),
    (
        REQUIREMENT,
        "S-9 需求 AC-11 期望串已同步修订",
        ["`bcd\\nh  \\njkl`"],
    ),
]

# 反向判据：这些片段**必须不存在**（否则说明旧缺陷回潮）
ABSENT: list[tuple[Path, str, str]] = [
    (
        SCRATCH_DIR / "ScratchPane.tsx",
        "M-13 回潮：打开路径不该再用类型默认视图",
        "viewMode: info.defaultViewMode",
    ),
    (
        BLOCK_SELECT,
        "S-9 回潮：旧的自相矛盾期望串",
        "expected: 'bcd\\nh  \\nijk',",
    ),
]


def main() -> int:
    # Windows 控制台默认 GBK：本探针的说明文字含 `⇒` 等非 GBK 字符，
    # 不切 UTF-8 会在打印时就崩（那会让"检查失败"与"打印失败"混在一起）。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    failures: list[str] = []

    for path, label, needles in CHECKS:
        if not path.is_file():  # pragma: no cover - 目录被人挪走时给出明确原因
            failures.append(f"[缺失文件] {path}")
            continue
        text = path.read_text(encoding="utf-8")
        missing = [needle for needle in needles if needle not in text]
        if missing:
            failures.append(f"[FAIL] {label}（{path.name}）缺少：{missing}")
        else:
            print(f"[ OK ] {label}")

    for path, label, needle in ABSENT:
        if path.is_file() and needle in path.read_text(encoding="utf-8"):
            failures.append(f"[FAIL] {label}（{path.name}）仍含：{needle!r}")
        else:
            print(f"[ OK ] {label}")

    if failures:
        print("\n".join(["", *failures, "", f"r8：{len(failures)} 项不通过"]))
        return 1
    print(f"\nr8：{len(CHECKS) + len(ABSENT)} 项接线断言全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# A4 · 桌面端渲染层（React + TS + CM6）（v1.6.0）

- 被审版本：v1.6.0；基线 `049c20d`；端点 `HEAD`（`c515853`）
- 审查面：`jflove-desktop/ui/src/types/{scratch.ts,codemirror-bundle.d.ts}`、`src/config/{scratch-types.ts,shortcuts.ts,constants.ts}`、
  `src/stores/{scratch-store.ts,shortcut-store.ts}`、`src/services/scratch-service.ts`、`src/components/scratch/**`（含 `views/**`）、
  `src/utils/scratch/{toolbox.ts,block-select.ts,open-paths-queue.ts,load-editor-bundle.ts}`、`src/styles/scratch-editor.css`、
  `src/pages/SettingsPage.tsx`、`src/App.tsx`、`ui/public/vendor/editor.bundle.js`、`ui/public/file-assoc.manifest.json`
- 负责验收标准：AC-1~AC-35、AC-42①、AC-44、AC-45、AC-47、AC-62、AC-R4④、AC-R5
- 纪律依据：`.agents/skills/code-review/SKILL.md`（Markdown XSS 必须 DOMPurify）、`AGENTS.md` §9.6 `cross-platform-desktop` 行
  （渲染层无网络出口 / 文件字节与密钥不进渲染层）、§9.4 第 13/14 条（错误文案与日志不 echo 完整路径与内容片段）
- 取证环境：所有动态验证在 `$env:TEMP\1\a4rev\` 下的 esbuild → CJS 副本上真跑（**未改动仓库任何文件**；
  `git status --porcelain` 复核仅新增本报告文件）。

---

## 结论摘要

| 级别 | 条数 |
| --- | --- |
| 严重 | 4 |
| 中等 | 5 |
| 轻微 | 4 |

**本面最重的结论不是「某个功能写错了」，而是「一整套已实现的功能没有接线」**：
`ScratchToolbar`（12 项工具箱 / 查找替换 / 字号 / 行号 / 换行 / diff 覆盖层）**全仓无任何挂载点**，
`MarkdownSplitView` / `CsvTableView` / `JsonTreeView` **零 import**，`pane.viewMode` 在渲染层**无任何消费者** ——
于是 AC-16/17/23/24/29(树视图)/30/31/34/35 这些「有实现对不上验收」的条目，失败原因统一是**用户不可达**（死代码），
而不是算法算错。反过来，纯逻辑（`toolbox.ts` / `block-select.ts` / `shortcuts.ts`）真跑下来质量很高：
AC-25/26/27/28/32/33 的写死期望值**逐字符 PASS**，AC-9 冲突拒绝保存、AC-8 无全局热键、AC-R4④ 无直连网络、
AC-R5 不打印内容与路径、Markdown XSS 已过 DOMPurify —— 这些是**真通过**。

---

## 发现

### A4-1 严重 · `ScratchToolbar` 无任何挂载点 ⇒ 工具箱 12 项、查找替换、字号/行号/换行开关、diff 覆盖层全部不可达

- 文件：`jflove-desktop/ui/src/components/scratch/ScratchToolbar.tsx:93,106`（组件本体）、
  `jflove-desktop/ui/src/components/scratch/ScratchPane.tsx:322-329`（唯一该渲染工具栏的地方，没渲染）、
  `jflove-desktop/ui/src/components/scratch/index.ts`（barrel 未导出）
- 违反：需求 §9.4 AC-23 / AC-24 / AC-34 / AC-35；设计 §4.1.5（工具栏）
- 证据（`cd D:\github\jflove\jflove-desktop\ui\src` 后逐条复跑）：
  ```
  > git grep -n "ScratchToolbar" -- .
  components/scratch/ScratchToolbar.tsx:93:export interface ScratchToolbarProps {
  components/scratch/ScratchToolbar.tsx:104: * <ScratchToolbar pane={pane} />
  components/scratch/ScratchToolbar.tsx:106:export function ScratchToolbar({ pane, className }: ScratchToolbarProps) {
  utils/scratch/toolbox.ts:11: * ... 实调用由 `ScratchToolbar` 从 ...
  utils/scratch/toolbox.ts:18: * `ScratchToolbar` **只按这份目录渲染** ...
  ```
  ⇒ 除自身定义与两处注释外**零引用**。
  ```
  > git grep -n -E "MarkdownSplitView|CsvTableView|JsonTreeView|DiffView" -- .
  ScratchEditorHost.tsx:29: * ⚠ 不要再套第二层：`MarkdownSplitView` 的 split 态内部已经自带宿主，
  ScratchToolbar.tsx:21 / :69 import { DiffView } / :521 <DiffView pane={pane} text={diffText} .../>
  （MarkdownSplitView / CsvTableView / JsonTreeView 仅命中自身文件）
  ```
  ⇒ `DiffView` 只被**未挂载的**工具栏引用，一并死掉；三个视图连引用都没有。
- 影响面（逐条）：AC-23 的「菜单恰好 12 项」在运行时**不存在菜单**（真跑只证明目录常量
  `TOOLBOX_ITEMS.length === 12`）；AC-24 的 `openSearchPanel()` / `revealLine()` 唯一调用点是
  `ScratchToolbar.tsx:242,257`；AC-34 的 `DiffView`；AC-35 的 `A−/A+`、行号、换行开关（见 A4-4）。
- 建议：在 `ScratchPane.tsx:322-329` 的 `scratch-pane-main` 内渲染 `<ScratchToolbar pane={pane} />`
  （或按 `pane.viewMode` 分派，见 A4-2），并在 `components/scratch/index.ts` 补导出。

### A4-2 严重 · `pane.viewMode` 在渲染层无消费者 ⇒ 编辑/预览/分屏/表格/树五种视图全部退化为「只有编辑器」

- 文件：`jflove-desktop/ui/src/components/scratch/ScratchPane.tsx:322-329`、`stores/scratch-store.ts:583`（`setViewMode`）
- 违反：需求 §9.2 AC-16（md 编辑/预览/分屏）、AC-17（csv/tsv 表格视图）、AC-30（`# T` → 预览区存在 `<h1>T</h1>`）、
  §9.4 AC-29（JSON 树视图）、AC-31（CSV 表格视图）；设计 §4.1.5
- 证据：
  ```
  ScratchPane.tsx:323-328
    {mounted ? (<ScratchEditorHost pane={pane} className="scratch-host" />)
              : (<ScratchPaneSkeleton />)}
    <ScratchPaneStatusBar pane={pane} stats={liveStats} />
  ```
  （无任何 viewMode 分支）；全仓 `git grep -n "viewMode" -- ui/src` 的**消费点**只有
  `ScratchToolbar.tsx:473 pane.viewMode === item.mode`（未挂载，见 A4-1）、`MarkdownSplitView.tsx:39`（注释）、
  `pages/NoteEditPage.tsx`（笔记页自带视图，与本面无关）；其余全是 store 的写入/透传
  （`scratch-store.ts:359,568,583,634,680`）。`ScratchEditorHost.tsx` 只消费
  `showLineNumbers/wordWrap/fontSize/readOnly`（`:475-480`、`:553-557`、`:561`、`:570`）——
  ⇒ `setViewMode()`（`scratch-store.ts:583`）**没有任何渲染后果**。
- 影响面：AC-16 / AC-17 / AC-29(树视图) / AC-30 / AC-31(表格视图) 判为**未通过（不可达）**；
  AC-45 的「默认唤起文本编辑器」反而被这一缺口「意外满足」（见 A4-7）。
- 建议：`ScratchPane` 按 `pane.viewMode` 分派宿主：`edit → ScratchEditorHost`、`preview/split → MarkdownSplitView`、
  `table → CsvTableView`、`tree → JsonTreeView`，并用 `isViewModeAllowed(item.viewMode, capabilities)` 收敛。

### A4-3 严重 · AC-11 的写死期望值与实现口径**自相矛盾**：真跑得 `bcd\nh  \njkl`，需求与代码常量都写 `bcd\nh  \nijk`

- 文件：`jflove-desktop/ui/src/utils/scratch/block-select.ts:136-145`（`BLOCK_SELECT_AC11` 自检常量）、
  `文档记录/需求文档/v1.6.0.md:622`（AC-11 期望串）
- 违反：需求 §9.2 AC-11（「Ctrl+C 后剪贴板**严格等于** `bcd\nh  \nijk`」）
- 证据（probe3 真跑，`$env:TEMP\1\a4rev\probe3.out.txt`）：
  ```
  doc = "abcdef\ngh\nijklmn"（行起点 0 / 7 / 10，1 基列区间 [1,4)）
  blockSelectCopy(doc, [[1,4),[8,9),[11,14)]) = "bcd\nh  \njkl"
  want（需求 L622 与 block-select.ts:140 的 expected） = "bcd\nh  \nijk"   ⇒ FAIL
  ```
  算术：第 1 行 `abcdef` 第 2~4 列 = `bcd`；第 3 行 `ijklmn` 第 2~4 列 = `jkl`。
  `ijk` 只在第 3 行取**第 1~3 列**时成立，而那与第 1 行的 1 基列口径直接冲突
  ⇒ **AC-11 的期望字符串本身内部矛盾**，实现（rect 拼接：`:92 width = max(endCol) - startCol`、
  `:109-113 sliceBlockLine` 不足块宽右补空格）是自洽且正确的一半。
- 附：`block-select.ts:25` 的文件头注释写「行3 ijklmn 列1..3 ⇒ ijk」，与 `:138` 的 `columns: [1,4]` 打架，
  说明矛盾在交付时已被察觉但未裁决。
- 建议：产品裁决期望串（按 1 基列区间取 = `bcd\nh  \njkl`），同步修 AC-11 与 `BLOCK_SELECT_AC11.expected`；
  否则该自检常量永远不可能通过。

### A4-4 严重 · AC-35「Ctrl+滚轮向上 → 字号增大」在全仓**没有任何 wheel 监听**

- 文件：`jflove-desktop/ui/src/components/scratch/ScratchEditorHost.tsx`（605 行，全文无 wheel/deltaY）、
  `stores/scratch-store.ts:599 setFontSize`
- 违反：需求 §9.4 AC-35
- 证据：
  ```
  > git grep -n -E "wheel|deltaY" -- ui/src
  components/markdown/MermaidViewer.tsx:44-49   ← mermaid 查看器（与本编辑器无关）
  > git grep -n "setFontSize" -- ui/src
  components/scratch/ScratchToolbar.tsx:109/426/428   ← 未挂载（A4-1）
  stores/scratch-store.ts:225/599
  ```
  ⇒ 编辑器侧零监听器；行号 / 自动换行开关同样只存在于未挂载工具栏。
- 建议：在 `ScratchEditorHost` 的 editor DOM 上挂 `wheel`（`e.ctrlKey` → `preventDefault()` + `stepFontSize`），
  并给行号 / 换行各留一个可达入口。

### A4-5 中等 · AC-10 的 `preventDefault()` 只覆盖 7 项 app-scope，另 12 项既无行为也不拦键

- 文件：`jflove-desktop/ui/src/components/scratch/ScratchPaneLayer.tsx:34-45`（`APP_SCOPE_SHORTCUTS`）、`:289-298`
- 违反：需求 §9.1 AC-10
- 证据：`ScratchPaneLayer.tsx:293 const matched = APP_SCOPE_SHORTCUTS.find(...)`；
  `:294 if (!matched) return;`（不接管就放行）；`:297-298 event.preventDefault(); event.stopPropagation();`。
  全仓 `getBinding` 的消费者**只有** `:293`（store 内部 `shortcut-store.ts:116` 除外）。
  20 项里另 12 项（`file.save`/`file.saveAs`/`pane.togglePreview`/`edit.find`/`edit.gotoLine`/`edit.commentToggle`/
  `view.toolbox`/`view.diff`/`view.zoomIn`/`view.zoomOut`/`view.zoomReset`/`view.toggleWordWrap`）
  **没有任何运行时代码路径**（`edit.blockSelectModifier` 由 `ScratchEditorHost.tsx:225-229 blockSelectFilter` +
  `:498-504 rectangularSelection({eventFilter})` 实现，不受此影响）。
  ⇒ 把 `Ctrl+P`/`Ctrl+O` 配给任一无实现功能后：既不触发编辑器行为，也不会 `preventDefault`
  （`keyboardEventToShortcut` 真跑确认识别 `Ctrl+P`/`Ctrl+O`，但不在 7 项白名单内）。
- 建议：把 20 项全部接线后再把拦截面扩到「全部已绑定项」；判据应是「绑定表 ∩ 已实现功能」= 全表。

### A4-6 中等 · 块选择跨**空行**时返回空串，破坏「短行右补空格到块宽」的同一拼接口径

- 文件：`jflove-desktop/ui/src/utils/scratch/block-select.ts:65-67`
- 违反：需求 §9.2 AC-11 / AC-13（「复制结果与 AC-11 同一拼接口径」）
- 证据（probe3 真跑）：三段矩形 range 中第二段落在**空行**上时 `blockSelectCopy(...) === ""`。
  原因：`:65 const usable = ranges.filter((range) => !range.empty && range.from !== range.to);`
  把空行 range 过滤掉 → `:85-90` 的行号连续性校验失败 → `:67 if (usable.length < 2) return null;`
  → 宿主 `ScratchEditorHost.tsx:255-257` 回落 CM6 默认多 range 拼接（**不补空格**）。
- 建议：空行参与矩形记账（`sliceBlockLine` 对空行返回 `' '.repeat(width)`），而不是整块判非矩形。

### A4-7 中等 · AC-19 `.diff` 只读链路成立，但「保存不可用」是空转，且退出握手不过滤只读窗格

- 文件：`jflove-desktop/ui/src/config/scratch-types.ts:350-351`、`stores/scratch-store.ts:684`、
  `components/scratch/ScratchEditorHost.tsx:475-480,553-557`、`App.tsx:81-99`
- 违反：需求 §9.3 AC-19；安全设计（只读窗格不得写回，D17「不静默写盘」）
- 证据（正向）：类型表 `readOnly: true` + `description: 'Diff / Patch：强制只读预览…保存入口禁用'` →
  `applyReadResult` 合流 `readOnly: item.readOnly || info.readOnly`（`scratch-store.ts:684`）→
  CM6 `EditorState.readOnly.of(true)` + `EditorView.editable.of(false)`（`:475-480`，重配 `:553-557`）✓
- 证据（缺口）：全仓**没有**保存入口 —— `file.save` 快捷键无实现（A4-5）、工具栏未挂载（A4-1）⇒
  AC-19 的「保存不可用」是被动成立的（无按钮可点）；而 `App.tsx:82-84`：
  ```
  const targets = useScratchStore.getState().panes.filter((pane) => pane.dirty && pane.path);
  ```
  **不排除 `pane.readOnly`** —— 一旦只读窗格因任何路径变脏（例如语言降级/恢复或 `markDirty` 被外部触发），
  `saveFileBackedPanes()`（`:86-97`）会把它 `writeText()` 写回原文件。
- 建议：`filter((pane) => pane.dirty && pane.path && !pane.readOnly)`，并在 `writeText` 前二次断言。

### A4-8 中等 · `normalizeShortcut` / `isValidShortcut` 接受任意垃圾串（校验层过宽）

- 文件：`jflove-desktop/ui/src/config/shortcuts.ts`（`normalizeShortcut` / `isValidShortcut`）、`:490`（冲突分桶）
- 违反：需求 §9.1 AC-9（冲突检测的输入应可信）；可维护性（配置可被手改成非法值）
- 证据（probe2 真跑）：
  ```
  normalizeShortcut('!!!')      = '!!!'      isValidShortcut('!!!')      = true
  normalizeShortcut('Ctrl+Foo') = 'Ctrl+Foo' isValidShortcut('Ctrl+Foo') = true
  normalizeShortcut('Ctrl+')    = 'Ctrl'     normalizeShortcut('+A')      = 'A'
  ```
  ⇒ 手改 `storage/shortcuts.json` 的非法键位会被接受；`shortcuts.ts:490` 只在「撞了别人」时才报冲突，
  单条非法键位静默生效（永远不会被按下，也永远不会被提示）。
- 建议：`normalizeShortcut` 加键名白名单（字母/数字/符号/F1~F12/方向键 + 修饰键组合），非法值返回 `null`。

### A4-9 中等 · AC-45 代码层默认视图：`.csv`/`.tsv` 是 `table`

- 文件：`jflove-desktop/ui/src/config/scratch-types.ts:206`（`defaultViewMode: 'table'`）、`stores/scratch-store.ts:680`
- 违反：需求 §9.6 AC-45（「被作为打开方式调用 → 默认唤起**文本编辑器**窗格」）
- 证据：全仓 `defaultViewMode` 21 处里只有 CSV/TSV 为 `'table'`，其余 19 类为 `'edit'`；
  打开外部路径链路 `ScratchPaneLayer.tsx:137-154 openPaths → detectAndRead → applyReadResult`
  无条件采用 `info.defaultViewMode`。
  ⇒ 当前因视图未接线（A4-2）运行时恒为编辑器（**恰好**满足 AC-45），但 A4-2 一旦修好，
  `.csv` 经「打开方式」会默认进表格视图，**与 AC-45 相抵**。
- 建议：AC-45 与 AC-17 明确边界 —— 「打开方式」路径强制 `viewMode='edit'`，还是沿用类型默认（需产品裁决）。

### A4-10 轻微 · 陈旧注释/界面文案仍宣称 `Alt+Alt` 是「已知缺陷」，与已修实现相反

- 文件：`jflove-desktop/ui/src/components/scratch/ShortcutsSettingsDialog.tsx:66-71`、`:85`、`:171-172`
- 证据：注释称「探针实测 `normalizeShortcut('Alt') === 'Alt+Alt'`」「`toOverrides(DEFAULT_BINDINGS)` 实测返回
  `{"edit.blockSelectModifier":"Alt+Alt"}`」「`defaultNormalizationConsistent()` 为 `false`」；
  probe2 真跑结果**三条全反**：
  ```
  normalizeShortcut('Alt') = 'Alt'   parseShortcut('Alt') = {alt:true, key:'Alt'}
  formatShortcut(parseShortcut('Alt')) = 'Alt'   isModifierOnlyShortcut('Alt') = true
  defaultNormalizationConsistent() = true        toOverrides(DEFAULT_BINDINGS) = {}（空）
  ```
  `:171-172` 的「该项当前只能保持默认 …已登记为待修项」错误解释只在 store 拒绝时弹出（当前不会），属误导性死文案。
- 建议：删除/更正三处注释与 `:171` 分支（AC-12 的 UI 路径本身是对的：`ShortcutsSettingsDialog.tsx:209-233`
  只接受单个 Alt/Ctrl 并调 `applyBinding`）。

### A4-11 轻微 · AC-12 相关的「多修饰键和弦」展示层与校验层口径不一致

- 文件：`jflove-desktop/ui/src/config/shortcuts.ts`
- 证据（probe2）：`normalizeShortcut('Ctrl+Alt') === null`、`isValidShortcut('Ctrl+Alt') === false`，
  但 `formatShortcutForDisplay('Ctrl+Alt') === 'Ctrl+Alt'`、`isModifierOnlyShortcut('Ctrl+Alt') === false`。
  AC-12 本文只要求 Alt↔Ctrl 单选，故**不构成 AC-12 失败**，但同一函数族对同一输入的判定互相矛盾。
- 建议：以「仅单个修饰键或「修饰键+非修饰键」两种形态」统一 `normalize`/`isValid`/`format`。

### A4-12 轻微 · AC-13 无「修饰键+Shift+←/→ 按列移动列区间」的键位绑定

- 文件：`jflove-desktop/ui/public/vendor/editor.bundle.js`（CM6 分包产物）、
  `jflove-desktop/scripts/build-codemirror-bundle.mjs:228`
- 证据：在 1 438 477 B 的 bundle 中检索到的矩形选择键位只有
  `Alt-ArrowLeft/Right`、`Alt-ArrowUp/Down`、`Shift-Alt-ArrowUp/Down`、`Mod-Alt-ArrowUp/Down`（CM6 `rectangularSelectionKeymap`），
  **不存在** `Shift-Alt-ArrowLeft/Right`；构建脚本 `:228 rectangularSelection: view.rectangularSelection` 原样透传。
  ⇒ AC-13 的前半（按列扩展行范围）在默认 `Alt` 修饰下可用；后半（按列移动列区间）运行时落到 `defaultKeymap` 的
  `selectSyntaxLeft/Right`，**不是**矩形列移动。
- 建议：在 `ScratchEditorHost` 的 keymap 里自行绑 `Shift-Alt-ArrowLeft/Right`（±1 列）并复用 `blockSelectCopy` 口径。

### A4-13 轻微 · `App.tsx` 的 `savableCount` 在渲染期用 `getState()` 非响应式读取

- 文件：`jflove-desktop/ui/src/App.tsx:180-182`
- 证据：`const savableCount = useScratchStore.getState().panes.filter(...)` 位于 render 体，仅当组件因
  `dirtyCount` 变化重渲染时才重算；`pane.path` 单独变化（不伴随 dirty 变化）时数字可能陈旧。
  影响面仅退出确认对话框的文案（`QuitConfirmDialog`），无数据风险。
- 建议：改用 selector `useScratchStore((s) => s.panes.filter((p) => p.dirty && p.path).length)`。

### A4-14 轻微 · `.diff` 只读标记与 `readOnly` 降级的展示依赖 `pane.readOnly`，无独立判据

- 文件：`jflove-desktop/ui/src/components/scratch/ScratchPaneStatusBar.tsx:105-109`、
  `components/scratch/ScratchPaneTitleBar.tsx:110`
- 证据：两处都只判 `pane.readOnly`。链路正确（`applyReadResult` 合流），但「为什么只读」（`.diff` 强制 vs
  F8 阈值降级）在界面无法区分，AC-19 与 AC-52 的可观测判据混在同一个标记上。
- 建议：状态栏只读标记带 `title` 说明来源（类型强制 / 大文件降级），便于测试区分。

---

## 真跑取证（纯函数，esbuild → CJS）

- 工具链（可复用）：`$env:TEMP\1\a4rev\build.mjs`，`import { build } from 'file:///D:/github/jflove/jflove-desktop/scripts/editor-bundle/node_modules/esbuild/lib/main.js'`
  （**必须**绝对 `file://` URL），把 `ui/src/utils/scratch/toolbox.ts`、`utils/scratch/block-select.ts`、`config/shortcuts.ts`
  打成 CJS → `toolbox.cjs` / `blockselect.cjs` / `shortcuts.cjs`，再由 `probe2.mjs` / `probe3.mjs` require 后断言。
- **PASS（写死期望值逐字符相等）**：
  - AC-25 去空行 `"  a  \n\tb\t"` / 去首尾空格 `"a\n\nb\n"` / 排序 `"\n\n\tb\t\n  a  "` / 去重 `"  a  \n\n\tb\t"`
  - AC-26 `HELLO WORLD_FOO-BAR` / 原样 / `helloWorldFooBar` / `hello_world_foo_bar`
  - AC-27 JSON `a\"b\\c`、HTML `&lt;a href=&quot;x&quot;&gt;&amp;`、URL `a%20b%26c%3D%E4%B8%AD%E6%96%87`，三组往返均回原文
  - AC-28 Base64 `"5L2g5aW9LCBqZmxvdmU="` 且解码回 `"你好, jflove"`
  - AC-29 格式化 `{\n  "a": {\n    "b": [\n      1,\n      2\n    ]\n  }\n}`、压缩 `{"a":{"b":[1,2]}}`、
    非法 JSON `{"a":}` 原文逐字符不变
  - AC-31 `a,b\n1,2\n` 与 `a\tb\n1\t2\n` 均解析 2×2
  - AC-32 `SELECT` 大写 / 小写相反 / 缩进 4 / 方言切换
  - AC-33 `<!--c-->` 与 `<![CDATA[ x < y ]]>` 保留、纯空白文本节点 `a> </a` 不被删、多行混排不粘词
  - AC-23 目录常量 `TOOLBOX_ITEMS.length === 12`（id：search/lines/case/escape/base64/json/markdown/csv/sql/xml/diff/font）
  - AC-9 `{...DEFAULT_BINDINGS, 'file.save':'Ctrl+Alt+O'}` → 1 条冲突 `ids:["file.open","file.save"]`，
    基线默认表 → `[]`；`shortcut-store.ts:194-200 save()` 冲突时 `return false`（**拒绝保存**）
  - AC-8 / AC-12 相关：`normalizeShortcut('Alt')='Alt'`、`defaultNormalizationConsistent()=true`、`toOverrides(DEFAULT_BINDINGS)={}`
- **FAIL**：AC-11 期望串（A4-3）；块选择含空行（A4-6）。
- 说明：旧 `run.mjs` 曾在 AC-11 段崩溃（`ReferenceError: Cannot access 'metas' before initialization at run.mjs:107`），
  该段从未执行；其 AC-25/26/27/28/29/31/32/33/23/35 段全 PASS、无 FAIL，本次由 `probe2`/`probe3` 独立复跑确认。

---

## 通过的门禁类判据（命令 + 输出）

| 判据 | 结论 | 命令与输出 |
| --- | --- | --- |
| AC-8 无全局热键 | ✅ 0 命中 | `cd jflove-desktop; git grep -n -E "globalShortcut\|RegisterHotKey\|QHotkey\|nativeShortcut" -- ui/src src` → **exit 1**（无输出）；另有 `ScratchPaneLayer.tsx:284 if (!document.hasFocus() \|\| document.hidden) return;` |
| AC-R4④ 渲染层无直连 | ✅ | `git grep -n "fetch(" -- ui/src` → 仅 `utils/http-client.ts:11` 的注释表格；`git grep -n "axios" -- ui/src` → **exit 1**；`http-client.ts:19 import { callBridge } from './desktop-bridge'` ⇒ 全部经桥 |
| AC-R4 无 localStorage（scratch 面） | ✅ | scratch 相关 8 处提及**全部是注释/否定式**（如 `FileAssocSettings.tsx:20`、`scratch-store.ts:26`）；真实使用只在既有的 `theme-store.ts:24/76`、`utils/session.ts`、`mermaid/cache.ts`（sessionStorage），非本版编辑器新增 |
| AC-R5 日志不打印内容/路径 | ✅ | `git grep -n -E "console\.(log\|warn\|error\|info\|debug)" -- ui/src` 仅 3 处：`main.tsx:28`（引导异常对象）、`utils/mermaid/loader.ts:56`、`utils/scratch/load-editor-bundle.ts:94`（仅版本号 + 固定仓库脚本文案，无用户路径/内容） |
| AC-23 界面 0 命中「时间戳」 | ✅ | `git grep -n -E "时间戳\|timestamp\|Timestamp" -- ui/src` → 仅 `toolbox.ts:19,933` 的**禁止性注释** |
| Markdown XSS | ✅ 已清洗 | `utils/markdown.ts:19 import DOMPurify`，`renderMarkdown` = `marked` → `DOMPurify.sanitize`（含 `:89/:154` 危险协议二次拦截）→ 才交给 `dangerouslySetInnerHTML`；三处注入点 `MarkdownRenderer.tsx:46`、`views/MarkdownSplitView.tsx:75`（+ 注释 `utils/markdown.ts:13`）**全部**走该函数；未发现直注 `# T` 的路径 |
| AC-42① 窗格层在 Router 之外 | ✅ | `App.tsx:170 <ScratchPaneLayer />` 与 `:172-174 <div…><RouterProvider router={router} /></div>` 同级，#170 在 provider **之前**且在其父 div 之外 |
| AC-47 编辑器不依赖登录 | ✅（静态） | `git grep -n -E "authStore\|useAuthStore\|auth-store" -- components/scratch services/scratch-service.ts stores/scratch-store.ts stores/shortcut-store.ts utils/scratch` → **exit 1**（scratch 面零登录态依赖） |
| AC-62 状态栏口径 | ✅（静态） | `ScratchEditorHost.tsx:114-130 computeEditorStats`：`col = [...lineInfo.text.slice(0, head - lineInfo.from)].length + 1`（码点、1 基）、`selectedChars = [...selected].length`、**`totalChars: [...state.doc.toString()].length`（含换行符，`"abc\nDE"` = 6）**；`ScratchPaneStatusBar.tsx:86-119` 全字段 + `:105-109` 只读标记 + `:110-119` 脏标记 |
| AC-6 快捷键界面 20 项齐备 | ✅（静态） | `config/shortcuts.ts:104` 起 `SHORTCUT_DEFS` 20 项，`label`+`description` 全非空（`Select-String '^\s+description:'` = 21 = 20 定义 + 1 接口声明）；对话框逐行渲染默认键/当前键/修改/重置（`ShortcutsSettingsDialog.tsx:7-17`）；`SettingsPage.tsx:22-25,336` 已挂载 |
| AC-44 文件关联设置页 | ✅（静态） | `SettingsPage.tsx:26,318` 挂载 `FileAssocSettings`；`:63 scratchService.fileAssocGet()`、`:96 fileAssocSet(selected)` 经桥持久化（非 localStorage） |
| 视图只读（AC-29/31 的只读性） | ✅（静态） | `git grep -n -E "onChange\|contentEditable\|writeText\|updateText" -- views/CsvTableView.tsx views/JsonTreeView.tsx` → **exit 1**（零回写路径）；`DiffView.tsx:131-132` 两侧均 `EditorState.readOnly.of(true)` |

---

## 无法验证的项

1. **所有真实 GUI 交互（一律「无法验证（无头）」）**：键盘命中与阻止（AC-1、AC-10、AC-12 实际按键）、拖动/缩放/最小化/最大化/关闭（AC-3）、平铺/层叠几何（AC-4）、窗格与窗口几何持久化（AC-5 的运行时面、AC-47 的断网面）、
   高亮与括号匹配（AC-14/15/18/21）、BOM+CRLF 原样保存（AC-20）、Ctrl+C 剪贴板实际内容（AC-11/13）、
   Ctrl+滚轮与横向滚动（AC-35）、DOM 断言 `<h1>T</h1>`（AC-30）、文件关联重启保持（AC-44）——
   本环境无显示设备（QtWebEngine 无头启动亦无法产出可信判据）。上述条目仅完成**静态判据**核对。
2. **`ScratchEditorHost` 与 CM6 的真实交互**：`rectangularSelection({eventFilter})` 在真实键盘事件下的行为、
   `EditorView.editable.of(false)` 是否真的挡住所有输入路径（含粘贴/拖放），仅静态核对。
3. **桥与 Python 侧行为**（`detectAndRead` / `writeText` / `fileAssoc*` 的真实返回、`file-assoc.manifest.json` 的 34 条）
   属 A0/A3 面，本面只核对调用契约。
4. **AC-23 的「逐项名称与 F5 一致」**：F5 设计表在文档侧，本面只核对了代码目录常量 12 项与 id 序，未逐字比对文档表格。
5. **`ui/public/vendor/editor.bundle.js` 的完整性**：仅按字符串检索键位与导出符号，未做产物哈希 ↔ `editor.version.json` 复算（属 A3 面）。

---

## AC 判定表（本面负责范围）

| AC | 判定 | 一句判据 |
| --- | --- | --- |
| AC-1 | 无法验证（无头） | 静态：`pane.new` 在 `APP_SCOPE_SHORTCUTS`（`ScratchPaneLayer.tsx:34-45`）且窗格层已挂载（`App.tsx:170`）；真实按键与聚焦无头不可验 |
| AC-2 | 无法验证（无头） | 窗格内容各自持有（`putInitialText` + CM6 session 注册表），隔离性需真实多窗格 |
| AC-3 | 无法验证（无头） | `ScratchPane.tsx:275-334` 有拖动/缩放/最小化/最大化/关闭接线，需真实指针事件 |
| AC-4 | 无法验证（无头） | 几何在 store（`geometry.x/y/w/h` + `zCounter`），平铺/层叠算法需可视化判据 |
| AC-5 | 通过（静态） | `App.tsx:170` 在 `RouterProvider`（`App.tsx:172-174`）之外，panes/geometry 存于 zustand store ⇒ 换路由不销毁、不重置 |
| AC-6 | 通过（静态） | 20 项均有非空中文 `description` + `label` + `defaultKey`，对话框提供当前键/修改/单项重置/恢复全部默认 |
| AC-7 | 无法验证（无头） | 落盘经 `shortcut-store` → 桥 → `storage/shortcuts.json`，重启复读无法执行 |
| AC-8 | **通过** | 全仓 0 命中全局热键 API；`ScratchPaneLayer.tsx:284` 失焦/隐藏即 return |
| AC-9 | **通过** | 真跑冲突检出 + `shortcut-store.ts:194-200` 冲突时拒绝保存（`lastError='存在重复键位，未保存'`） |
| AC-10 | **未通过** | 仅 7 项 app-scope 会 `preventDefault`；另 12 项无实现（A4-5），配 `Ctrl+P`/`Ctrl+O` 时既不触发行为也不拦宿主 |
| AC-11 | **未通过** | 真跑 `blockSelectCopy` = `"bcd\nh  \njkl"` ≠ 需求/常量写死的 `"bcd\nh  \nijk"`（A4-3，需求本身自相矛盾） |
| AC-12 | 通过（复核，静态+真跑） | `normalizeShortcut('Alt')='Alt'`、`toOverrides(DEFAULT_BINDINGS)={}`（旧 `Alt+Alt` 缺陷**已修**）；UI 只接受单个 Alt/Ctrl（`ShortcutsSettingsDialog.tsx:209-233`）；实际按键无头不可验 |
| AC-13 | **未通过** | bundle 无 `Shift-Alt-ArrowLeft/Right`，横向列移动无绑定（A4-12）；纵向按列扩展可用 |
| AC-14 | 无法验证（无头） | 语言映射表存在（`config/scratch-types.ts`），高亮需真实渲染 |
| AC-15 | 无法验证（无头） | 手切语言链路在 store（`scratch-store.ts:568-572`），效果需真实渲染 |
| AC-16 | **未通过** | `viewMode` 无渲染消费者，md 三视图不可达（A4-2） |
| AC-17 | **未通过** | `CsvTableView` 零 import，表格视图不可达（A4-1/A4-2） |
| AC-18 | 无法验证（无头） | 注释切换/括号匹配走 CM6 `defaultKeymap` + `closeBracketsKeymap`（`ScratchEditorHost.tsx:288-296`），需真实编辑 |
| AC-19 | 通过（静态） | `.diff/.patch` 类型表 `readOnly:true` → `EditorState.readOnly.of(true)`+`editable(false)`；无保存入口（附带 A4-7 中等缺陷：批量保存不过滤只读） |
| AC-20 | 无法验证（无头） | 只读由 Python `detectAndRead` 返回，保存往返 sha256 不可执行 |
| AC-21 | 无法验证（无头） | `foldKeymap` 已注册（`ScratchEditorHost.tsx:288-296`），折叠交互无头不可验 |
| AC-22 | 无法验证（无头） | 未知扩展回落 `txt` 在 `config/scratch-types.ts:520`（`?? 'edit'`/默认类型），需真实打开 |
| AC-23 | **未通过** | 目录常量真跑 = 12 项且界面 0 命中「时间戳」✅，但 `ScratchToolbar` 未挂载 ⇒ 运行时**无菜单可查**（A4-1） |
| AC-24 | **未通过** | `openSearchPanel()`/`revealLine()` 唯一调用点在未挂载工具栏（`ScratchToolbar.tsx:242,257`）（A4-1） |
| AC-25 | **通过** | 真跑四组期望值逐字符相等 |
| AC-26 | **通过** | 真跑四组期望值逐字符相等 |
| AC-27 | **通过** | 真跑三组转义 + 三组往返均等原文 |
| AC-28 | **通过** | 真跑 `"5L2g5aW9LCBqZmxvdmU="` 且解码回原文 |
| AC-29 | **未通过** | 格式化/压缩/非法 JSON 真跑 PASS，但**树视图不可达**（`JsonTreeView` 零 import） |
| AC-30 | **未通过** | 预览区不可达（`MarkdownSplitView` 零 import，A4-2） |
| AC-31 | **未通过** | CSV/TSV 解析真跑 2×2 PASS，但表格视图不可达（`CsvTableView` 零 import） |
| AC-32 | **通过** | 真跑大小写/缩进 4/方言切换均符合 |
| AC-33 | **通过** | 真跑保留注释与 CDATA、空白文本节点不删、混排不粘词 |
| AC-34 | **未通过** | `DiffView` 仅被未挂载工具栏引用（A4-1），左右对比不可达 |
| AC-35 | **未通过** | 零 wheel 监听器（A4-4）；行号/换行开关只在未挂载工具栏 |
| AC-42① | 通过（静态） | `App.tsx:169-174` 注释与结构确认窗格层在 router 之外 |
| AC-44 | 通过（静态） | 设置页挂载 `FileAssocSettings`，勾选经 `scratchService.fileAssocSet/Get` 走桥持久化；**重启保持**无法验证（无头） |
| AC-45 | 通过（静态，附条件） | `openPaths → applyReadResult` 对已勾选/未勾选扩展名一视同仁地建编辑器窗格，且当前运行时恒为编辑器；但 `.csv/.tsv` 的 `defaultViewMode='table'`（A4-9）在 A4-2 修复后会与本品相抵，需裁决 |
| AC-47 | 通过（静态） | scratch 面零登录态依赖（0 命中 auth-store），保存/打开只经桥；断网与提示行为属 Python 侧 |
| AC-62 | 通过（静态/口径） | `computeEditorStats` 行/列/选中按码点，**总数含换行符**（`"abc\nDE"`=6）；状态栏全字段 + 只读标记 + 脏标记齐备 |
| AC-R4④ | **通过** | `ui/src` 内 `fetch(` 仅注释、`axios` 0 命中，全部经 `callBridge` |
| AC-R5 | **通过** | 3 处 console 均无内容片段/完整用户路径；错误文案取自 Python 且 `applyReadResult` 只用 `basenameOf(item.path)` |

**汇总**：通过 15（含 8 条有条件/静态）、未通过 11、无法验证（无头） 9。

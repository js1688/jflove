/**
 * 编辑器组件统一出口（设计 v1.6.1 §4.2 组件树 / §3.1 文件清单）
 *
 * 约定：与 `components/ui/index.ts` 同风格 —— 只做**再导出**，不实现逻辑。
 *
 * v1.6.1 的口径变化（浮动窗格 → 整页工作区 + 顶部标签页）：
 * - 删：`ScratchPaneLayer`（浮层根，已被工作区取代）、`ScratchPane`（窗格外壳 → `EditorPanel`）、
 *   `ScratchPaneTitleBar`（拖动 / 缩放 / 最小化 / 最大化入口，几何已退役）；
 * - 改：`ScratchPaneStatusBar` → `ScratchEditorStatusBar`（props 由 `pane` 改为 `tabId`）；
 * - 增：`ScratchWorkspace`（整页工作区容器）、`ScratchWorkspaceEmpty`（空态）、
 *   `ScratchTabBar` / `ScratchTabOverflowMenu`（顶部标签条与溢出下拉）。
 *
 * ⚠ 本 barrel **不导出** `ScratchEditorHost`：它的会话注册表 API 由 `EditorPanel.tsx` /
 * `ScratchToolbar.tsx` 直接从 `./ScratchEditorHost` import；这里再导出会形成第二条依赖边。
 * S-5 / S-6 起补导出 `ScratchToolbar` 与四个只读视图（`views/*`）：它们是面板正文区的公开
 * 组件，`EditorPanel` 之外（组件测试 / Phase 6）需要按名字取用。
 */

export { ScratchWorkspace } from './ScratchWorkspace';
export { ScratchWorkspaceEmpty } from './ScratchWorkspaceEmpty';
export { ScratchTabBar } from './ScratchTabBar';
export { ScratchTabOverflowMenu } from './ScratchTabOverflowMenu';
export { EditorPanel, type EditorPanelProps } from './EditorPanel';
export {
  ScratchEditorStatusBar,
  type TabEditorStats,
  type ScratchEditorStatusBarProps,
} from './ScratchEditorStatusBar';
export { ScratchToolbar, type ScratchToolbarProps } from './ScratchToolbar';
export { CsvTableView, type CsvTableViewProps } from './views/CsvTableView';
export { DiffView, type DiffViewProps } from './views/DiffView';
export { JsonTreeView, type JsonTreeViewProps } from './views/JsonTreeView';
export { MarkdownSplitView, type MarkdownSplitViewProps } from './views/MarkdownSplitView';

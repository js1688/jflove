/**
 * 浮动窗格层统一出口（设计 v1.6.0 §4.1.1 / §3.1 #17）
 *
 * 约定：与 `components/ui/index.ts` 同风格 —— 只做**再导出**，不实现逻辑。
 *
 * ⚠ 本 barrel **不导出** `ScratchEditorHost`：它的会话注册表 API 由 `ScratchPane.tsx` /
 * `ScratchToolbar.tsx` 直接从 `./ScratchEditorHost` import；这里再导出会形成第二条依赖边。
 *
 * S-5 / S-6 起补导出 `ScratchToolbar` 与四个只读视图（`views/*`）：它们是窗格正文区的公开
 * 构件，`ScratchPane` 之外（组件测试 / Phase 6）需要按名字取用。
 */

export { ScratchPaneLayer } from './ScratchPaneLayer';
export { ScratchPane, type ScratchPaneProps } from './ScratchPane';
export {
  ScratchPaneTitleBar,
  type ScratchPaneDragHandlers,
  type ScratchPaneTitleBarProps,
} from './ScratchPaneTitleBar';
export {
  ScratchPaneStatusBar,
  type PaneEditorStats,
  type ScratchPaneStatusBarProps,
} from './ScratchPaneStatusBar';
export { ScratchToolbar, type ScratchToolbarProps } from './ScratchToolbar';
export { CsvTableView, type CsvTableViewProps } from './views/CsvTableView';
export { DiffView, type DiffViewProps } from './views/DiffView';
export { JsonTreeView, type JsonTreeViewProps } from './views/JsonTreeView';
export { MarkdownSplitView, type MarkdownSplitViewProps } from './views/MarkdownSplitView';

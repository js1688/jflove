/**
 * 浮动窗格层统一出口（设计 v1.6.0 §4.1.1 / §3.1 #17）
 *
 * 约定：与 `components/ui/index.ts` 同风格 —— 只做**再导出**，不实现逻辑。
 *
 * ⚠ 本 barrel **不导出** `ScratchEditorHost`：它由另一位同事交付，正在同目录并行施工，
 * 这里再导出会形成第二条依赖边（其内部 API 仍可能变动）。需要它时直接从
 * `./ScratchEditorHost` import（`ScratchPane.tsx` 就是这么做的）。
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

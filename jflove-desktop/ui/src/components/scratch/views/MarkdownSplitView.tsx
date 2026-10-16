/**
 * Markdown 编辑 / 预览 / 分屏（**只读预览**）—— 设计 v1.6.1 §15.1（AC-73）/ §4.1.1 / §4.1.6 第 7 项
 *
 * ## 口径（v1.6.1 本轮改动：预览侧改走 `MarkdownRenderer`）
 *
 * - 渲染栈 = `components/markdown/MarkdownRenderer`（`splitMermaidBlocks` + `MermaidBlock`），
 *   普通片段仍由 `utils/markdown.ts` 的 `renderMarkdown`（`marked` + `DOMPurify`）产出 ——
 *   与**笔记端同一份**渲染实现（设计 §15.1.4：不得再在字符串里塞 mermaid 占位符，
 *   那会造成"两套 mermaid 生命周期"）。**不 import `pages/NoteEditPage.tsx`** ——
 *   它在跨端门禁清单里（`scripts/check_web_desktop_parity.py::PARITY_MANIFEST`），
 *   任何改动都会破门禁（§4.1.1 注）。
 * - `showEditor=true` 时左栏是**真正的 CM6 实例**（分屏态），由本组件内部渲染
 *   `ScratchEditorHost`；同事若已在外层挂了宿主，可通过 `editor` prop 传入，避免双实例。
 *   ⚠ 该分支当前只服务**独立分屏用法**：编辑器的 `split` 态由 `EditorPanel` 组合
 *   （宿主槽 + `scratch-pane-aux`），调用本组件时**一律** `showEditor={false}`
 *   （`EditorPanel.tsx` 唯一调用点）。
 * - 预览是**只读渲染**：没有回写通道，预览里的任何点击都不会改文档。
 * - ` ```mermaid ` 代码块渲染为**矢量图**（`MermaidBlock`：懒加载 / 主题切换重渲 /
 *   语法错误只降级本图块并给中文提示、不白屏）；其它语言代码块仍按代码高亮。
 * - **不传** `onJumpToDiagramSource`：编辑器的 `text` 是只读快照，做到"跳到第 N 个
 *   mermaid 围栏"需要新的定位能力（属功能增量，不在 AC-73 判据内）⇒ 不传时
 *   `MermaidBlock` 不渲染「跳到源码」按钮，**零死按钮**（设计 §15.1.2）。
 *
 * ## 安全
 *
 * 预览内容**必须**经过 `renderMarkdown` 的 DOMPurify 清洗后才注入（由 `MarkdownRenderer`
 * 转交，见 `utils/markdown.ts` 的 `renderMarkdown`）；本文件已无任何
 * `dangerouslySetInnerHTML` 路径，也不 `console.*` 文档内容。
 */

import type { ReactNode } from 'react';
import type { ScratchTab } from '../../../types/scratch';
import { MarkdownRenderer } from '../../markdown/MarkdownRenderer';
import { ScratchEditorHost } from '../ScratchEditorHost';

export interface MarkdownSplitViewProps {
  /** 标签页状态（左栏编辑实例需要它） */
  pane: ScratchTab;
  /** Markdown 正文（宿主里的实时内容，由调用方从会话表取或透传） */
  text: string;
  /** `true` = 左编辑 + 右预览（分屏）；`false` = 仅预览 */
  showEditor?: boolean;
  /**
   * 左栏内容覆盖项。
   *
   * 不传 ⇒ 本组件内部渲染 `<ScratchEditorHost pane={pane} />`；
   * 传 ⇒ 用调用方给的节点（例如同事的 `ScratchPane` 已经按 viewMode 挂好了宿主）。
   */
  editor?: ReactNode;
  className?: string;
}

/**
 * Markdown 分屏 / 预览。
 *
 * @example
 * // 分屏态（左编辑右预览）
 * <MarkdownSplitView pane={pane} text={session?.getText() ?? ''} />
 * // 纯预览态
 * <MarkdownSplitView pane={pane} text={text} showEditor={false} />
 */
export function MarkdownSplitView({
  pane,
  text,
  showEditor = true,
  editor,
  className,
}: MarkdownSplitViewProps) {
  return (
    <div className={`flex h-full min-h-0 w-full ${className ?? ''}`}>
      {showEditor ? (
        <div className="min-w-0 flex-1 overflow-hidden border-r border-black/10 dark:border-white/10">
          {editor ?? <ScratchEditorHost pane={pane} />}
        </div>
      ) : null}
      <div className="min-w-0 flex-1 overflow-auto">
        {/* 外层类名与改造前逐字一致（`markdown-body md-body p-4 text-sm`）：
            `markdown-body` / `md-body` 由 `MarkdownRenderer` 自己拼在最前 ⇒ 样式零变化 */}
        <MarkdownRenderer content={text} className="p-4 text-sm" />
      </div>
    </div>
  );
}

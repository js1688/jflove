/**
 * Markdown 编辑 / 预览 / 分屏（**只读预览**）—— 设计 v1.6.0 §4.1.1 / §4.1.6 第 7 项
 *
 * ## 口径
 *
 * - 渲染栈 = `marked` → `DOMPurify.sanitize` → `dangerouslySetInnerHTML`；
 *   直接复用 `utils/markdown.ts` 的 `renderMarkdown`（与 `NoteEditPage` **同栈**），
 *   但**不 import `pages/NoteEditPage.tsx`** —— 它在跨端门禁清单里
 *   （`scripts/check_web_desktop_parity.py::PARITY_MANIFEST`），任何改动都会破门禁（§4.1.1 注）。
 * - `showEditor=true` 时左栏是**真正的 CM6 实例**（分屏态），由本组件内部渲染
 *   `ScratchEditorHost`；同事若已在外层挂了宿主，可通过 `editor` prop 传入，避免双实例。
 * - 预览是**只读渲染**：没有回写通道，预览里的任何点击都不会改文档。
 * - 不渲染 mermaid：本版本 Scratch 的 Markdown 预览只要求 marked + highlight.js + dompurify
 *   （§4.1.6 第 7 项），不引入 mermaid 离线包这条额外依赖链。
 *
 * ## 安全
 *
 * 预览内容**必须**经过 `renderMarkdown` 的 DOMPurify 清洗后才注入；本文件不存在
 * 其它 `dangerouslySetInnerHTML` 路径，也不 `console.*` 文档内容。
 */

import { useMemo } from 'react';
import type { ReactNode } from 'react';
import type { ScratchPane } from '../../../types/scratch';
import { renderMarkdown } from '../../../utils/markdown';
import { ScratchEditorHost } from '../ScratchEditorHost';

export interface MarkdownSplitViewProps {
  /** 窗格状态（左栏编辑实例需要它） */
  pane: ScratchPane;
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
  // 已由 renderMarkdown 经 DOMPurify 清洗（见文件头"安全"）
  const html = useMemo(() => renderMarkdown(text), [text]);

  return (
    <div className={`flex h-full min-h-0 w-full ${className ?? ''}`}>
      {showEditor ? (
        <div className="min-w-0 flex-1 overflow-hidden border-r border-black/10 dark:border-white/10">
          {editor ?? <ScratchEditorHost pane={pane} />}
        </div>
      ) : null}
      <div className="min-w-0 flex-1 overflow-auto">
        <div
          className="markdown-body md-body p-4 text-sm"
          /* 内容来自 utils/markdown.ts 的 renderMarkdown ⇒ 已过 DOMPurify */
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </div>
  );
}

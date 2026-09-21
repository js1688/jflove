/**
 * Markdown 渲染容器（Web）
 *
 * 页面只需传 `content`，本组件负责：
 *   1. 把内容切成「普通 HTML 片段」与「mermaid 图表片段」；
 *   2. 普通片段经 DOMPurify 清洗后注入（`utils/markdown.ts` 负责）；
 *   3. 图表片段交给 `MermaidBlock`（懒加载/缓存/缩放/错误降级）；
 *   4. 用 `useMemo` 缓存解析结果 —— v1.4.2 是每次按键都重跑解析（设计文档 §3.1.4）。
 *
 * 保留 `.markdown-body` 作为最外层类名之一：`NoteEditPage` 的大纲跳转依赖
 * `document.querySelector('.markdown-body')` 查标题（向后兼容）。
 */
import { useMemo } from 'react';
import { splitMermaidBlocks } from '../../utils/markdown';
import { MermaidBlock } from './MermaidBlock';

export interface MarkdownRendererProps {
  /** Markdown 原文 */
  content: string;
  /** 额外类名（例如控制最大宽度、内边距） */
  className?: string;
  /** 点击图表「跳到源码」时回调（编辑器场景传入） */
  onJumpToDiagramSource?: (index: number) => void;
}

/**
 * 渲染 Markdown。
 *
 * 注意：普通片段用 `dangerouslySetInnerHTML`，其内容**已被本模块与 DOMPurify 清洗**，
 * 不存在未净化路径（详见 `utils/markdown.ts` 的 `renderMarkdown`）。
 */
export function MarkdownRenderer({
  content,
  className,
  onJumpToDiagramSource,
}: MarkdownRendererProps) {
  const segments = useMemo(() => splitMermaidBlocks(content), [content]);

  return (
    <div className={['markdown-body', 'md-body', className].filter(Boolean).join(' ')}>
      {segments.map((seg, i) =>
        seg.kind === 'html' ? (
          <div
            key={`html-${i}`}
            /* 内容已由 utils/markdown.ts 的 renderMarkdown 经 DOMPurify 清洗 */
            dangerouslySetInnerHTML={{ __html: seg.html }}
          />
        ) : (
          <MermaidBlock
            key={`mmd-${seg.index}`}
            code={seg.code}
            index={seg.index}
            onJumpToSource={onJumpToDiagramSource}
          />
        ),
      )}
    </div>
  );
}

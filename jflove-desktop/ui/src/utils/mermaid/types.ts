/**
 * Mermaid 渲染相关类型
 */

export type ThemeName = 'light' | 'dark';

export type MermaidFailureReason =
  /** 语法/语义错误 —— 可定位到行号 */
  | 'parse'
  /** 渲染引擎不可用（资源缺失/加载失败） */
  | 'engine'
  /** 渲染超时 */
  | 'timeout'
  /** 引擎内部异常 */
  | 'runtime';

export interface MermaidError {
  reason: MermaidFailureReason;
  /** 1-based 行号；0 表示无法定位 */
  line: number;
  /** 面向用户的简短原因（不包含笔记正文） */
  message: string;
}

export type MermaidResult =
  | {
      ok: true;
      svg: string;
      /** 图表原始尺寸（用户单位），用于占位与缩放计算 */
      width: number;
      height: number;
      /** 渲染耗时（ms），展示在图块底部 */
      elapsed: number;
      /** 图表类型（flowchart / sequence / gantt …），用于图块头部标签 */
      kind: string;
    }
  | { ok: false; error: MermaidError };

/** 从 mermaid 源码首行推断图表类型（仅用于展示标签，不参与渲染） */
export function detectDiagramKind(code: string): string {
  const first = code
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l && !l.startsWith('%%'));
  if (!first) return 'Mermaid';
  const head = first.toLowerCase();

  if (head.startsWith('sequencediagram')) return 'Sequence';
  if (head.startsWith('gantt')) return 'Gantt';
  if (head.startsWith('classdiagram')) return 'Class';
  if (head.startsWith('statediagram')) return 'State';
  if (head.startsWith('erdiagram')) return 'ER';
  if (head.startsWith('pie')) return 'Pie';
  if (head.startsWith('journey')) return 'Journey';
  if (head.startsWith('gitgraph')) return 'Git';
  if (head.startsWith('mindmap')) return 'Mindmap';
  if (head.startsWith('timeline')) return 'Timeline';
  if (head.startsWith('quadrantchart')) return 'Quadrant';
  if (head.startsWith('xychart')) return 'XY Chart';
  if (head.startsWith('block')) return 'Block';
  if (
    head.startsWith('graph') ||
    head.startsWith('flowchart')
  ) {
    return 'Flowchart';
  }
  return 'Mermaid';
}

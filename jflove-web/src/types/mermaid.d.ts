/**
 * Mermaid 离线渲染包的全局类型声明
 *
 * 本包**不是**通过 npm 依赖引入的，而是由 `scripts/build_mermaid_bundle.mjs`
 * 在构建期打成一个自包含 IIFE，作为静态资源挂到 `window.mermaid`。
 * 这样做是为了让三端（Web / 桌面端 / 移动端）使用**同一份**产物，
 * 保证同一份笔记在不同端渲染出一致的图（需求 AC-9、设计文档 §4.2）。
 */

/** mermaid 渲染成功的结果 */
export interface MermaidRenderResult {
  svg: string;
  bindFunctions?: (element: Element) => void;
}

/** mermaid 抛出的解析错误（带位置信息，用于展示行号） */
export interface MermaidParseError extends Error {
  hash?: {
    loc?: { first_line?: number; last_line?: number };
    text?: string;
  };
}

export interface MermaidConfig {
  startOnLoad?: boolean;
  theme?: string;
  securityLevel?: 'strict' | 'loose' | 'antiscript' | 'sandbox';
  themeVariables?: Record<string, unknown>;
  fontFamily?: string;
  flowchart?: Record<string, unknown>;
  sequence?: Record<string, unknown>;
  gantt?: Record<string, unknown>;
  er?: Record<string, unknown>;
  pie?: Record<string, unknown>;
  state?: Record<string, unknown>;
  class?: Record<string, unknown>;
}

export interface MermaidApi {
  initialize(config: MermaidConfig): void;
  parse(text: string): Promise<unknown>;
  render(id: string, text: string): Promise<MermaidRenderResult>;
  version?: string;
}

declare global {
  interface Window {
    /** 由 `public/vendor/mermaid.bundle.js` 挂载 */
    mermaid?: MermaidApi;
  }
}

export {};

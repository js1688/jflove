/**
 * Markdown 解析与渲染（Web 端唯一入口）
 *
 * v1.4.2 的问题：
 *   - `NoteEditPage` 与 `FilePreviewPage` 各有一份**复制粘贴**的私有渲染函数；
 *   - 无 memo、无缓存，每次按键都重跑 `marked.parse + DOMPurify.sanitize`；
 *   - `highlight.js` 已在依赖里却零引用 → 代码块没有语法高亮；
 *   - mermaid 块被当作普通代码块。
 *
 * v1.5.0（设计文档 §3.1.4）：
 *   - 统一到本模块，页面只调 `splitMermaidBlocks` + `renderMarkdown`；
 *   - mermaid 块**不参与 HTML 化**，而是被切成独立片段交给 React 组件渲染
 *     （mermaid 需要真实 DOM 节点与生命周期，`dangerouslySetInnerHTML` 承载不了）；
 *   - 接入 highlight.js 做代码高亮。
 *
 * 安全：普通片段一律经 DOMPurify 清洗后才交给 `dangerouslySetInnerHTML`
 * （对齐 AGENTS.md §9：预览渲染不得成为注入面）。
 */
import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/common';
import { Marked, type Tokens } from 'marked';

/** 一个 Markdown 片段：普通 HTML 片段，或一个待渲染的 mermaid 图表 */
export type MdSegment =
  | { kind: 'html'; html: string }
  | { kind: 'mermaid'; code: string; /** 该图表在文档中的序号，用于定位与日志脱敏 */ index: number };

/* ==================== mermaid 块切分 ==================== */

/**
 * 把 Markdown 按 ```mermaid 围栏切成片段序列。
 *
 * 说明：只在**顶层**围栏上切分（不支持嵌套围栏，Markdown 本身也没有嵌套围栏语义）。
 * 语言标记大小写不敏感，并允许前后有空格（` ```Mermaid `）。
 */
export function splitMermaidBlocks(content: string): MdSegment[] {
  const segments: MdSegment[] = [];
  const lines = content.split('\n');
  let buf: string[] = [];
  let mermaidIndex = 0;

  const flushHtml = () => {
    if (buf.length > 0) {
      const raw = buf.join('\n');
      if (raw.trim()) segments.push({ kind: 'html', html: renderMarkdown(raw) });
      buf = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fenceMatch = /^\s*```+\s*([^\s`]*)\s*$/.exec(line);

    if (fenceMatch && fenceMatch[1].toLowerCase() === 'mermaid') {
      // 找到围栏开始，收集到配对的结束围栏
      flushHtml();
      const codeLines: string[] = [];
      let closed = false;
      for (let j = i + 1; j < lines.length; j++) {
        if (/^\s*```+\s*$/.test(lines[j])) {
          closed = true;
          i = j;
          break;
        }
        codeLines.push(lines[j]);
      }
      if (closed) {
        segments.push({ kind: 'mermaid', code: codeLines.join('\n').trim(), index: mermaidIndex++ });
      } else {
        // 围栏未闭合：当作普通文本处理，避免吞掉后面所有内容
        buf.push(line, ...codeLines);
      }
      continue;
    }

    buf.push(line);
  }
  flushHtml();
  return segments;
}

/** 文档中是否含 mermaid 图表（用于决定是否加载渲染引擎） */
export function hasMermaidBlock(content: string): boolean {
  return /^\s*```+\s*mermaid\s*$/im.test(content);
}

/* ==================== marked 配置 ==================== */

/** 危险的 URL 协议（与 DOMPurify 的默认策略配合，做第二道拦截） */
const DANGEROUS_URL = /^\s*(?:javascript|vbscript|file|data):/i;

function safeUrl(href: string | null | undefined): string | null {
  if (!href) return null;
  const trimmed = href.trim();
  if (DANGEROUS_URL.test(trimmed)) return null;
  return trimmed;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const marked = new Marked({
  gfm: true,
  breaks: false,
});

marked.use({
  renderer: {
    /** 代码块：接入 highlight.js；Mermaid 块在切分阶段已被摘出，这里只处理普通代码 */
    code({ text, lang }: Tokens.Code): string {
      const language = (lang || '').trim().split(/\s+/)[0].toLowerCase();
      let body: string;
      let label = language || 'text';

      if (language && hljs.getLanguage(language)) {
        try {
          body = hljs.highlight(text, { language, ignoreIllegals: true }).value;
        } catch {
          body = escapeHtml(text);
        }
      } else if (!language) {
        // 无语言标记：尝试自动识别（失败则纯转义）
        try {
          const auto = hljs.highlightAuto(text);
          if (auto.language && auto.relevance > 4) {
            body = auto.value;
            label = auto.language;
          } else {
            body = escapeHtml(text);
          }
        } catch {
          body = escapeHtml(text);
        }
      } else {
        body = escapeHtml(text);
      }

      return (
        `<div class="code-block">` +
        `<div class="code-head">` +
        `<span class="code-dots"><i></i><i></i><i></i></span>` +
        `<span class="code-lang">${escapeHtml(label)}</span>` +
        `</div>` +
        `<pre><code class="hljs language-${escapeHtml(label)}">${body}</code></pre>` +
        `</div>`
      );
    },

    /** 链接：处理 DOMPurify 会剥离的协议（mailto / tel）并对危险协议降级为纯文本 */
    link({ href, title, tokens }: Tokens.Link): string {
      const url = safeUrl(href);
      const inner = this.parser.parseInline(tokens);
      if (!url) return inner; // 危险协议 → 只保留文字
      const isExternal = /^https?:\/\//i.test(url);
      const attrs = [
        `href="${escapeHtml(url)}"`,
        title ? `title="${escapeHtml(title)}"` : '',
        isExternal ? 'target="_blank"' : '',
        isExternal ? 'rel="noopener noreferrer nofollow"' : '',
      ]
        .filter(Boolean)
        .join(' ');
      return `<a ${attrs}>${inner}</a>`;
    },

    /** 图片：限制协议并加懒加载 */
    image({ href, title, text }: Tokens.Image): string {
      const url = safeUrl(href);
      if (!url) return escapeHtml(text || '');
      return (
        `<img src="${escapeHtml(url)}" alt="${escapeHtml(text || '')}"` +
        (title ? ` title="${escapeHtml(title)}"` : '') +
        ` loading="lazy" />`
      );
    },
  },
});

/**
 * DOMPurify 白名单外仍需保留的属性/标签。
 *
 * 注意 `class` 必须保留：`marked` 的代码块与 highlight.js 的着色都靠 class
 * （原实现也依赖它，只是之前完全没接入高亮所以没暴露问题）。
 */
const PURIFY_CONFIG = {
  ADD_ATTR: ['target', 'rel', 'loading', 'align', 'class'],
  // 允许 checkbox（GFM 任务清单）
  ADD_TAGS: ['input'],
  FORBID_TAGS: ['style', 'form', 'iframe', 'object', 'embed', 'script'],
  FORBID_ATTR: ['onerror', 'onload', 'onclick', 'style'],
};

/**
 * 把一段 Markdown 渲染为**已清洗**的 HTML 字符串。
 *
 * 调用方需保证：该字符串要么来自本函数，要么同样经过 DOMPurify。
 */
export function renderMarkdown(content: string): string {
  try {
    const raw = marked.parse(content, { async: false }) as string;
    return DOMPurify.sanitize(raw, PURIFY_CONFIG) as unknown as string;
  } catch {
    // 解析失败时按纯文本展示，绝不抛给上层（避免整页崩）
    return `<p>${escapeHtml(content)}</p>`;
  }
}

/** 供单测直接验证「危险内容被清洗」用 */
export const __testing = { marked, safeUrl, escapeHtml, PURIFY_CONFIG };

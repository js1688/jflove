/**
 * AC-73：编辑器预览把 ` ```mermaid ` 渲染为**矢量图**（与笔记端同一渲染栈）
 *
 * ## 判据出处
 *
 * - 需求 `文档记录/需求文档/v1.6.1.md` **AC-73**（①矢量图 ②与笔记端一致 ③主题重渲染
 *   ④语法错误降级为可读中文提示且**不白屏** ⑤非 mermaid 代码块仍走代码高亮 `[负向对照]`）；
 * - 设计 `文档记录/设计文档/v1.6.1.md` **§15.1**（换用 `components/markdown/MarkdownRenderer`；
 *   表格里的"新增用例"明确要求：jsdom 下 `loadMermaid()` 走 `document.createElement('script')`
 *   不会真的执行 ⇒ **必须替身化 `utils/mermaid/renderer`** 并断言 `renderMermaid` 被调用
 *   + `MermaidBlock` 被挂载）。
 *
 * ## 为什么"失败得起来"（改回 `dangerouslySetInnerHTML` 必须红）
 *
 * 改造前 `MarkdownSplitView` 用 `utils/markdown.ts` 的 `renderMarkdown`：
 * ` ```mermaid ` 会被 `marked` 当**普通代码块**渲染成 `<pre><code class="language-mermaid">`，
 * 图表源码原样出现在页面上。因此本文件的核心判据是：
 *   ① `renderMermaid` **被调用**（替身记录调用参数）；
 *   ② 页面上出现 `MermaidBlock` 的容器 `.mmd`；
 *   ③ **不存在** `<pre><code class="language-mermaid">` 这种"把源码当普通代码块"的形态
 *      —— 只把渲染器换掉、mermaid 分支没接上时，这条必红。
 *
 * ## 与既有断言的关系（口径对齐，不改旧断言）
 *
 * `__tests__/scratch-views-markdown-json.test.tsx` 的 AC-30 用例（`.markdown-body h1`、
 * `.markdown-body img`、分屏态 `aux?.querySelector('.markdown-body h1')`）**保持原样**并已实跑通过：
 * `MarkdownRenderer` 的最外层类名仍是 `markdown-body md-body …`（`MarkdownRenderer.tsx:40`），
 * 普通片段仍是 `renderMarkdown` 的输出，只是多包了一层 `<div>`（后代选择器照样命中）。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup } from '@testing-library/react';
import { createElement } from 'react';

// ---- 依赖替身（vi.mock 会被 hoist，工厂内动态 import 避免与 helper 形成加载环） ----

/** mermaid 渲染器替身：记录每次调用，返回可控结果（判据 ① 的唯一来源） */
const renderCalls: { code: string; theme: string }[] = [];
let renderResult: {
  ok: boolean;
  svg?: string;
  width?: number;
  height?: number;
  elapsed?: number;
  error?: { reason: string; line: number; message: string };
} = { ok: true, svg: '<svg><text>A</text><text>B</text></svg>', width: 200, height: 100, elapsed: 3 };

vi.mock('../../../utils/mermaid/renderer', () => ({
  renderMermaid: vi.fn(async (code: string, theme: string) => {
    renderCalls.push({ code, theme });
    return renderResult;
  }),
}));

// `sanitizeSvg` 走真实实现会依赖 DOMPurify 的 svg 配置；这里替身化以隔离"清洗"这条支线
vi.mock('../../../utils/mermaid/sanitize', () => ({
  sanitizeSvg: (svg: string) => svg,
}));

vi.mock('../../ScratchEditorHost', async (importOriginal) => {
  const real = await importOriginal<Record<string, unknown>>();
  const helper = await import('./scratch-editor-host-mock');
  return helper.scratchEditorHostModuleMock(real);
});

vi.mock('../../../utils/desktop-bridge', () => ({
  callBridge: vi.fn(async () => ({ ok: true })),
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
  callShell: vi.fn(async () => ({ ok: true })),
}));

vi.mock('../../../services/scratch-service', () => ({
  scratchService: {
    saveDraft: vi.fn(async () => ({ ok: true })),
    readText: vi.fn(async () => ({ ok: true, text: '' })),
    readTextOnly: vi.fn(async () => ({ ok: true, text: '' })),
    pickReadOnlyFile: vi.fn(async () => null),
    writeText: vi.fn(async () => ({ ok: true })),
    reportDirty: vi.fn(),
  },
  isEditorBridgeReady: () => true,
  toDraftPaneMeta: (tab: unknown) => tab,
}));

import { MarkdownSplitView } from '../views/MarkdownSplitView';
import { useScratchStore } from '../../../stores/scratch-store';
import type { ScratchTab } from '../../../types/scratch';
import { notifyThemeChanged } from '../../markdown/use-theme-name';
import {
  getScratchEditorSession,
  registerMockSession,
  renderTabInStore,
  resetMockEditorSessions,
  resetScratchStore,
  tabTextOf,
  type TabRenderHandle,
} from './scratch-editor-host-mock';

/** Markdown 标签的能力来自语言 `markdown`（`config/scratch-types.ts` 的能力组表） */
const MARKDOWN_INIT: Partial<ScratchTab> = {
  language: 'markdown',
  detected: 'markdown',
  path: null,
};

/** 渲染 `MarkdownSplitView` 的纯预览态（与 `EditorPanel` 的调用口径一致：`showEditor={false}`） */
function renderPreview(text: string): TabRenderHandle {
  let knownTabId = '';
  const handle = renderTabInStore(
    () => {
      if (!getScratchEditorSession(knownTabId)) registerMockSession(knownTabId, text);
      return createElement(
        'div',
        { className: 'scratch-pane-aux' },
        createElement(MarkdownSplitView, {
          pane: useScratchStore.getState().tabs.find((tab) => tab.id === knownTabId) as ScratchTab,
          text: tabTextOf(knownTabId),
          showEditor: false,
        }),
      );
    },
    { ...MARKDOWN_INIT },
  );
  knownTabId = handle.tabId;
  handle.sync();
  return handle;
}

/** 把异步渲染链路（`renderMermaid` 的 promise → setState）冲刷干净 */
async function flushMermaid(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  renderCalls.length = 0;
  renderResult = {
    ok: true,
    svg: '<svg><text>A</text><text>B</text></svg>',
    width: 200,
    height: 100,
    elapsed: 3,
  };
  resetMockEditorSessions();
  resetScratchStore();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('AC-73① mermaid 围栏块 ⇒ 矢量图（`MermaidBlock` 被挂载 + `renderMermaid` 被调用）', () => {
  const MERMAID_DOC = '# T\n\n```mermaid\ngraph TD; A-->B;\n```\n';

  it('渲染 ` ```mermaid ` ⇒ `renderMermaid` 收到源码与主题，且页面上出现 `.mmd` 容器与 `svg`', async () => {
    const handle = renderPreview(MERMAID_DOC);
    await flushMermaid();

    // ① 真渲染器被调用（替身记录），源码与围栏内容一致
    expect(renderCalls, '`renderMermaid` 未被调用 ⇒ mermaid 分支没接上').toHaveLength(1);
    expect(renderCalls[0].code).toBe('graph TD; A-->B;');
    expect(renderCalls[0].theme).toBe('light');

    // ② `MermaidBlock` 被挂载（`.mmd` 是它的根 figure）
    expect(handle.container.querySelector('.markdown-body .mmd'), '未挂载 MermaidBlock').not.toBeNull();
    // ③ 渲染成功后画布里有 svg，节点文本 A / B 可见
    const svg = handle.container.querySelector('.mmd-canvas svg');
    expect(svg, '未出现矢量图').not.toBeNull();
    expect(svg?.textContent).toContain('A');
    expect(svg?.textContent).toContain('B');
  });

  it('[负向对照] 源码**不得**被当作普通代码块渲染（不存在 `.language-mermaid` 的 `<pre><code>`）', async () => {
    const handle = renderPreview(MERMAID_DOC);
    await flushMermaid();

    // 改造前的形态：`marked` 把围栏块渲染成 <pre><code class="language-mermaid">
    const asCodeBlock = handle.container.querySelector('pre > code.language-mermaid');
    expect(asCodeBlock, 'mermaid 源码被当普通代码块渲染了（未换渲染栈）').toBeNull();
    // 也不得整段出现在纯 `<pre>` 里（错误卡才允许 <pre>，且必须带 `.mmd-error`）
    expect(handle.container.querySelector('.markdown-body > div > pre:not(.mmd-error pre)')).toBeNull();
  });

  it('普通 Markdown 内容仍照常渲染（同一个文档里 `<h1>` 与图表共存，不白屏）', async () => {
    const handle = renderPreview(MERMAID_DOC);
    await flushMermaid();

    const heading = handle.container.querySelector('.markdown-body h1');
    expect(heading?.textContent, 'AC-73④ 要求其余内容仍可见').toBe('T');
  });
});

describe('AC-73③ 主题切换 ⇒ 重新渲染（仍为矢量图）', () => {
  it('亮 ↔ 暗切换后 `renderMermaid` 再次被调用，且主题参数跟着变', async () => {
    const handle = renderPreview('# T\n\n```mermaid\ngraph TD; A-->B;\n```\n');
    await flushMermaid();
    expect(renderCalls).toHaveLength(1);
    expect(renderCalls[0].theme).toBe('light');

    // 切到暗色：`useThemeName` 以 `<html>` 的 `.dark` 类为准，然后广播主题变化
    document.documentElement.classList.add('dark');
    await act(async () => {
      notifyThemeChanged();
    });
    await flushMermaid();

    expect(renderCalls.length, '主题切换后未重新渲染').toBeGreaterThanOrEqual(2);
    expect(renderCalls[renderCalls.length - 1].theme).toBe('dark');
    expect(handle.container.querySelector('.mmd-canvas svg'), '切换后不再是矢量图').not.toBeNull();

    document.documentElement.classList.remove('dark');
  });
});

describe('AC-73④ 语法错误 ⇒ 可读中文提示 + 整页不白屏', () => {
  it('`renderMermaid` 报 parse 错误 ⇒ 该块显示「语法错误」中文提示与行号，其余段落仍在', async () => {
    renderResult = {
      ok: false,
      error: { reason: 'parse', line: 2, message: 'Parse error' },
    };
    const handle = renderPreview('# T\n\n```mermaid\ngraph TD; A--\n```\n');
    await flushMermaid();

    const errorCard = handle.container.querySelector('.mmd-error');
    expect(errorCard, '错误块未降级为可读提示').not.toBeNull();
    expect(errorCard?.textContent).toContain('语法错误');
    expect(errorCard?.textContent).toContain('第 2 行');
    // 不白屏：同一文档的 `<h1>` 仍在
    expect(handle.container.querySelector('.markdown-body h1')?.textContent).toBe('T');
    // 不得出现"把源码当普通代码块"的残留
    expect(handle.container.querySelector('pre > code.language-mermaid')).toBeNull();
  });

  it('[负向对照] 非 mermaid 代码块（` ```python `）仍走代码高亮，不进入 mermaid 渲染路径', async () => {
    const handle = renderPreview('# T\n\n```python\nprint(1)\n```\n');
    await flushMermaid();

    expect(renderCalls, '普通代码块被误送进 mermaid 渲染器').toHaveLength(0);
    const code = handle.container.querySelector('pre > code');
    expect(code, '普通代码块未按代码块渲染').not.toBeNull();
    expect(code?.className).toContain('language-python');
    expect(code?.textContent).toContain('print(1)');
  });
});

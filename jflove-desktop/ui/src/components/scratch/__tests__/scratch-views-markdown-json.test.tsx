/**
 * Markdown 预览（AC-30）与 JSON 树视图（AC-29）用例。
 *
 * 判据出处：`文档记录/需求文档/v1.6.0.md`
 *   - AC-30（第 651 行）：「Markdown 预览：输入 `# T` → 预览区存在 `<h1>T</h1>`（DOM 可查）」
 *   - AC-29（第 650 行）：「JSON `{"a":{"b":[1,2]}}` 切树形视图 → 可见 `a` → `b` →
 *     数组(2 个元素)`；非法 JSON `{"a":}` → 只提示错误、原文逐字符不变 [负向对照]」
 *   - AC-16（第 632 行）：分屏时编辑器与预览同时可见
 * 另据 Phase 6 任务书：「不要把真实 CodeMirror 拖进 jsdom」——`ScratchEditorHost` 由
 * `vi.mock` 替身化，只断言 DOM / 结构。
 *
 * 口径说明（证据：`components/scratch/ScratchPane.tsx`）
 *   - 预览 / 分屏走 `MarkdownSplitView`（`showEditor={false}`），根类名
 *     `markdown-body md-body p-4 text-sm`，内容经 `utils/markdown.ts:203 renderMarkdown`
 *     （marked + DOMPurify）。
 *   - 树视图走 `JsonTreeView`（递归 `details`），正文来自
 *     `getScratchEditorSession(pane.id).getText()`——store 不缓存正文，故测试必须
 *     先把正文写进替身会话再 `sync()`。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import { createElement } from 'react';

// ---- 依赖替身（vi.mock 会被 hoist，工厂内动态 import 避免与 helper 形成加载环） ----
vi.mock('../ScratchEditorHost', async (importOriginal) => {
  const real = await importOriginal<Record<string, unknown>>();
  const helper = await import('./scratch-editor-host-mock');
  return helper.scratchEditorHostModuleMock(real);
});

vi.mock('../../../utils/desktop-bridge', () => ({
  // 判据：jsdom 无 QWebChannel，真实桥调用必然 reject
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
  toDraftPaneMeta: (pane: unknown) => pane,
}));

import { ScratchPane } from '../ScratchPane';
import { useScratchStore } from '../../../stores/scratch-store';
import type { ScratchPane as ScratchPaneModel } from '../../../types/scratch';
import type { ScratchViewMode } from '../../../types/scratch';
import {
  paneTextOf,
  renderPaneInStore,
  resetMockEditorSessions,
  resetScratchStore,
  setPaneText,
} from './scratch-editor-host-mock';

// ---- 夹具 ------------------------------------------------------------------------

/**
 * 摆好正文 + 视图模式，并把最新 store 快照同步进 DOM。
 *
 * 顺序很关键：先 `setPaneText`（`ScratchPane` 从会话读正文）→ 再 `setViewMode`
 * → 最后 `sync()` 重渲染。
 */
function renderView(mode: ScratchViewMode, init: Partial<ScratchPaneModel>, text: string) {
  const handle = renderPaneInStore(
    (pane) => createElement(ScratchPane, { pane, active: true, onRequestClose: () => undefined }),
    init,
  );
  setPaneText(handle.paneId, text);
  useScratchStore.getState().setViewMode(handle.paneId, mode);
  handle.sync();
  return handle;
}

/** Markdown 窗格的能力来自语言 `markdown`（证据：config/scratch-types.ts TYPE_DETAIL_GROUPS） */
const MARKDOWN_INIT: Partial<ScratchPaneModel> = {
  language: 'markdown',
  detected: 'markdown',
  path: null,
};

/** JSON 窗格的能力来自语言 `json` */
const JSON_INIT: Partial<ScratchPaneModel> = {
  language: 'json',
  detected: 'json',
  path: null,
};

beforeEach(() => {
  resetMockEditorSessions();
  resetScratchStore();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---- AC-30 Markdown 预览 ---------------------------------------------------------

describe('AC-30 Markdown 预览', () => {
  it('输入 `# T` → 预览区存在 <h1>T</h1>', () => {
    // 判据：AC-30 原文「Markdown 预览：输入 `# T` → 预览区存在 `<h1>T</h1>`（DOM 可查）」
    const { container } = renderView('preview', MARKDOWN_INIT, '# T\n');
    const heading = container.querySelector('.markdown-body h1');
    expect(heading).not.toBeNull();
    expect(heading?.textContent).toBe('T');
  });

  it('预览内容是 DOMPurify 清洗后的结果：`onerror` 被摘掉（负向对照）', () => {
    // 判据：`utils/markdown.ts:190-196` PURIFY_CONFIG.FORBID_ATTR 含 'onerror'。
    // marked 会照抄 HTML 片段，只有清洗环节会摘掉事件属性——所以这条断言证明清洗链路是活的。
    const { container } = renderView(
      'preview',
      MARKDOWN_INIT,
      '<img src="x" onerror="window.__scratchPwned=1">\n',
    );
    const body = container.querySelector('.markdown-body');
    expect(body).not.toBeNull();
    expect(body?.innerHTML).not.toContain('onerror');
    const img = container.querySelector('.markdown-body img');
    if (img) expect(img.hasAttribute('onerror')).toBe(false);
  });

  it('AC-16 分屏：编辑器宿主与预览区同时可见，且宿主只有一个实例', () => {
    // 判据：AC-16 原文「打开 .md → 可在编辑/预览/分屏三种视图间切换；分屏时编辑器与预览同时可见」
    const { container } = renderView('split', MARKDOWN_INIT, '# T\n');
    const host = container.querySelector('.scratch-pane-host');
    expect(host).not.toBeNull();
    // 分屏下宿主必须可见（hidden 属性由 showHost 控制）
    expect(host?.hasAttribute('hidden')).toBe(false);
    expect(container.querySelectorAll('[data-testid="scratch-editor-host-mock"]')).toHaveLength(1);
    expect(container.querySelector('.markdown-body h1')?.textContent).toBe('T');
  });
});

// ---- AC-29 JSON 树视图 -----------------------------------------------------------

describe('AC-29 JSON 树视图', () => {
  it('`{"a":{"b":[1,2]}}` → 树视图可见 a、b 与数组节点', () => {
    // 判据：AC-29 原文「切树形视图 → 可见 a → b → 数组(2 个元素)」
    const { container } = renderView('tree', JSON_INIT, '{"a":{"b":[1,2]}}');
    const tree = container.querySelector('.scratch-pane-aux');
    expect(tree).not.toBeNull();
    const text = tree?.textContent ?? '';
    expect(text).toContain('"a"');
    expect(text).toContain('"b"');
    // 数组节点必须报出元素个数（2）
    expect(text).toMatch(/2\s*(项|个元素)/);
    // 叶子值就在树里
    expect(text).toContain('1');
    expect(text).toContain('2');
    // 递归折叠结构
    expect(container.querySelectorAll('.scratch-pane-aux details').length).toBeGreaterThan(0);
  });

  it('树视图只读：没有输入控件与内联事件属性', () => {
    // 判据：设计文档 v1.6.0.md 第 338 行「JsonTreeView.tsx = JSON 树（只读，可折叠）」
    const { container } = renderView('tree', JSON_INIT, '{"a":{"b":[1,2]}}');
    const aux = container.querySelector('.scratch-pane-aux');
    expect(aux).not.toBeNull();
    expect(aux?.querySelectorAll('input, textarea, select, [contenteditable="true"]')).toHaveLength(0);
    const all = Array.from(aux?.querySelectorAll('*') ?? []);
    for (const node of all) {
      for (const attr of Array.from(node.attributes)) {
        expect(attr.name.startsWith('on')).toBe(false);
      }
    }
  });

  it('非法 JSON `{"a":}` → 只提示错误、原文不变（负向对照）', () => {
    // 判据：AC-29 负向对照「非法 JSON `{"a":}` → 只提示错误、原文逐字符不变」
    const bad = '{"a":}';
    const handle = renderView('tree', JSON_INIT, bad);
    const text = handle.container.querySelector('.scratch-pane-aux')?.textContent ?? '';
    expect(text).toContain('JSON 解析失败');
    expect(text).toContain('原文未改动');
    // 原文没被改写（会话正文逐字符相等）
    expect(paneTextOf(handle.paneId)).toBe(bad);
    // 也没有"修复"入口（无按钮/输入框可写回）
    expect(
      handle.container.querySelectorAll(
        '.scratch-pane-aux button, .scratch-pane-aux input, .scratch-pane-aux textarea',
      ),
    ).toHaveLength(0);
  });

  it.fails('AC-29（树视图数组节点文案）：需求括注要求出现「数组(2 个元素)」', () => {
    // 条目：AC-29 原文括注「数组(2 个元素)」。
    // 实现现状：`src/components/scratch/views/JsonTreeView.tsx:87`
    //   `const summaryText = kind === 'array' ? `[…] ${entries.length} 项` : `{…} ${entries.length} 项`;`
    //   ——数组节点渲染为「[…] 2 项」，从不出现「数组」二字。
    // 设计文档（`文档记录/设计文档/v1.6.0.md` 第 1231 行）只规定「递归 details + 只读」，
    // 未规定节点文案，故此处按需求原文断言并标记为已知实现缺口（it.fails 必须失败才算通过）。
    const { container } = renderView('tree', JSON_INIT, '{"a":{"b":[1,2]}}');
    const text = container.querySelector('.scratch-pane-aux')?.textContent ?? '';
    expect(text).toContain('数组(2 个元素)');
  });
});

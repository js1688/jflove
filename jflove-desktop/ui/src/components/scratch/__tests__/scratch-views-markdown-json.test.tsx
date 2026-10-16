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
 * v1.6.1 迁移说明（设计文档 `文档记录/设计文档/v1.6.1.md` §4.2 / §4.3）：
 *   - 浮动窗格 `ScratchPane` → 整页标签面板 `EditorPanel`（props = `{ tabId, active }`）；
 *     `ScratchPane` / `ScratchPaneModel` 已随本次改造删除，模型类型改为 `ScratchTab`。
 *   - v1.6.0 的 `.scratch-pane-main[data-view-mode]` 元素已不存在：视图现在由 `EditorPanel`
 *     分派到 `MarkdownSplitView` / `CsvTableView` / `JsonTreeView`（`EditorPanel.tsx:150-205`）。
 *     因此本文件改为**直接渲染视图组件**并断言其 DOM（视图组件自身的契约），
 *     另用 `EditorPanel` 壳做一次「标签 id → 面板外壳」的集成断言。
 *   - 正文仍由 `getScratchEditorSession(tabId).getText()` 提供（store 不缓存正文），
 *     故夹具必须**在渲染前**注册替身会话（`EditorPanel.tsx:138` 在渲染期现取正文）。
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
  toDraftPaneMeta: (tab: unknown) => tab,
}));

import { EditorPanel } from '../EditorPanel';
import { MarkdownSplitView } from '../views/MarkdownSplitView';
import { JsonTreeView } from '../views/JsonTreeView';
import { useScratchStore } from '../../../stores/scratch-store';
import type { ScratchTab } from '../../../types/scratch';
import {
  getScratchEditorSession,
  registerMockSession,
  renderTabInStore,
  resetMockEditorSessions,
  resetScratchStore,
  tabTextOf,
  type TabRenderHandle,
} from './scratch-editor-host-mock';

// ---- 夹具 ------------------------------------------------------------------------

/** 视图容器（与生产口径一致：`EditorPanel` 把视图包在 `.scratch-pane-aux` 里） */
const AUX_CLASS = 'scratch-pane-aux';

/**
 * 建标签 → **先**把正文写进替身会话 → 再渲染视图组件。
 *
 * 顺序很关键：`EditorPanel` 在渲染期就调 `getScratchEditorSession(tabId)?.getText()`
 * （`EditorPanel.tsx:138`），正文必须先于 render 就位；替身宿主挂载时会保留已有会话的
 * 正文（`scratch-editor-host-mock.tsx:213-217`），所以不会把夹具摆好的文本冲掉。
 */
function renderTab(
  init: Partial<ScratchTab>,
  text: string,
  build: (tabId: string) => ReturnType<typeof createElement>,
): TabRenderHandle {
  let knownTabId = '';
  const handle = renderTabInStore(
    () => {
      if (!getScratchEditorSession(knownTabId)) registerMockSession(knownTabId, text);
      return createElement(
        'div',
        { className: AUX_CLASS },
        build(knownTabId),
      );
    },
    { language: 'text', detected: 'text', ...init },
  );
  knownTabId = handle.tabId;
  handle.sync();
  return handle;
}

/**
 * 摆好正文并切到给定视图，渲染对应视图组件。
 *
 * 视图模式由 `build` 直接决定（组件即判据），同时把 `viewMode` 写进 store 保持
 * 状态模型一致（`EditorPanel` 的懒挂载 / `nudgeEditorLayout` 会读它）。
 */
function renderView(
  init: Partial<ScratchTab>,
  text: string,
  build: (tabId: string, tab: ScratchTab) => ReturnType<typeof createElement>,
): TabRenderHandle {
  const handle = renderTab(init, text, (tabId) =>
    build(tabId, useScratchStore.getState().tabs.find((tab) => tab.id === tabId) as ScratchTab),
  );
  return handle;
}

/** 视图容器查询（避免把 `EditorPanel` 壳的 DOM 混进断言） */
function auxOf(handle: TabRenderHandle): HTMLElement {
  const aux = handle.container.querySelector<HTMLElement>(`.${AUX_CLASS}`);
  if (!aux) throw new Error('视图容器缺失');
  return aux;
}

/** Markdown 标签的能力来自语言 `markdown`（证据：config/scratch-types.ts TYPE_DETAIL_GROUPS） */
const MARKDOWN_INIT: Partial<ScratchTab> = {
  language: 'markdown',
  detected: 'markdown',
  path: null,
};

/** JSON 标签的能力来自语言 `json` */
const JSON_INIT: Partial<ScratchTab> = {
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
    const handle = renderView(MARKDOWN_INIT, '# T\n', (tabId, tab) =>
      createElement(MarkdownSplitView, { pane: tab, text: tabTextOf(tabId), showEditor: false }),
    );
    const heading = handle.container.querySelector('.markdown-body h1');
    expect(heading).not.toBeNull();
    expect(heading?.textContent).toBe('T');
  });

  it('预览内容是 DOMPurify 清洗后的结果：`onerror` 被摘掉（负向对照）', () => {
    // 判据：`utils/markdown.ts:190-196` PURIFY_CONFIG.FORBID_ATTR 含 'onerror'。
    // marked 会照抄 HTML 片段，只有清洗环节会摘掉事件属性——所以这条断言证明清洗链路是活的。
    const handle = renderView(
      MARKDOWN_INIT,
      '<img src="x" onerror="window.__scratchPwned=1">\n',
      (tabId, tab) =>
        createElement(MarkdownSplitView, { pane: tab, text: tabTextOf(tabId), showEditor: false }),
    );
    const body = handle.container.querySelector('.markdown-body');
    expect(body).not.toBeNull();
    expect(body?.innerHTML).not.toContain('onerror');
    const img = handle.container.querySelector('.markdown-body img');
    if (img) expect(img.hasAttribute('onerror')).toBe(false);
  });

  it('AC-16 分屏：编辑器与预览同时可见（`EditorPanel` 在 split 视图下派发预览侧）', () => {
    // 判据：AC-16 原文「打开 .md → 可在编辑/预览/分屏三种视图间切换；分屏时编辑器与预览同时可见」。
    // v1.6.1 口径：编辑器侧 = 面板内的 `.scratch-pane-host`（替身宿主），预览侧 = `.scratch-pane-aux`。
    let knownTabId = '';
    const handle = renderTabInStore(
      () => {
        if (!getScratchEditorSession(knownTabId)) registerMockSession(knownTabId, '# T\n');
        return createElement(EditorPanel, { tabId: knownTabId, active: true });
      },
      { ...MARKDOWN_INIT, viewMode: 'split' },
    );
    knownTabId = handle.tabId;
    handle.sync();

    const panel = handle.container.querySelector<HTMLElement>(
      `[data-editor-panel="${handle.tabId}"]`,
    );
    expect(panel).not.toBeNull();
    // 面板外壳在激活态
    expect(panel?.getAttribute('data-editor-panel-active')).toBe('true');
    // 编辑器宿主可见（不是 hidden）
    const host = panel?.querySelector('.scratch-pane-host');
    expect(host).not.toBeNull();
    expect(host?.hasAttribute('hidden')).toBe(false);
    // 预览侧同时在场，且 <h1> 已渲染
    const aux = panel?.querySelector<HTMLElement>(`.${AUX_CLASS}`);
    expect(aux).not.toBeNull();
    expect(aux?.querySelector('.markdown-body h1')?.textContent).toBe('T');
  });
});

// ---- AC-29 JSON 树视图 -----------------------------------------------------------

describe('AC-29 JSON 树视图', () => {
  it('`{"a":{"b":[1,2]}}` → 树视图可见 a、b 与数组节点', () => {
    // 判据：AC-29 原文「切树形视图 → 可见 a → b → 数组(2 个元素)」
    const handle = renderView(JSON_INIT, '{"a":{"b":[1,2]}}', (tabId) =>
      createElement(JsonTreeView, { text: tabTextOf(tabId) }),
    );
    const tree = auxOf(handle);
    const text = tree.textContent ?? '';
    expect(text).toContain('"a"');
    expect(text).toContain('"b"');
    // 数组节点必须报出元素个数（2）
    expect(text).toMatch(/2\s*(项|个元素)/);
    // 叶子值就在树里
    expect(text).toContain('1');
    expect(text).toContain('2');
    // 递归折叠结构
    expect(tree.querySelectorAll('details').length).toBeGreaterThan(0);
  });

  it('树视图只读：没有输入控件与内联事件属性', () => {
    // 判据：设计文档 v1.6.0.md 第 338 行「JsonTreeView.tsx = JSON 树（只读，可折叠）」
    const handle = renderView(JSON_INIT, '{"a":{"b":[1,2]}}', (tabId) =>
      createElement(JsonTreeView, { text: tabTextOf(tabId) }),
    );
    const aux = auxOf(handle);
    expect(aux.querySelectorAll('input, textarea, select, [contenteditable="true"]')).toHaveLength(0);
    for (const node of Array.from(aux.querySelectorAll('*'))) {
      for (const attr of Array.from(node.attributes)) {
        expect(attr.name.startsWith('on')).toBe(false);
      }
    }
  });

  it('非法 JSON `{"a":}` → 只提示错误、原文不变（负向对照）', () => {
    // 判据：AC-29 负向对照「非法 JSON `{"a":}` → 只提示错误、原文逐字符不变」
    const bad = '{"a":}';
    const handle = renderView(JSON_INIT, bad, (tabId) =>
      createElement(JsonTreeView, { text: tabTextOf(tabId) }),
    );
    const text = auxOf(handle).textContent ?? '';
    expect(text).toContain('JSON 解析失败');
    expect(text).toContain('原文未改动');
    // 原文没被改写（会话正文逐字符相等）
    expect(tabTextOf(handle.tabId)).toBe(bad);
    // 也没有"修复"入口（无按钮/输入框可写回）
    expect(auxOf(handle).querySelectorAll('button, input, textarea')).toHaveLength(0);
  });

  it.fails('AC-29（树视图数组节点文案）：需求括注要求出现「数组(2 个元素)」', () => {
    // 条目：AC-29 原文括注「数组(2 个元素)」。
    // 实现现状：`src/components/scratch/views/JsonTreeView.tsx:87`
    //   `const summaryText = kind === 'array' ? `[…] ${entries.length} 项` : `{…} ${entries.length} 项`;`
    //   ——数组节点渲染为「[…] 2 项」，从不出现「数组」二字。
    // 设计文档（`文档记录/设计文档/v1.6.0.md` 第 1231 行）只规定「递归 details + 只读」，
    // 未规定节点文案，故此处按需求原文断言并标记为已知实现缺口（it.fails 必须失败才算通过）。
    // 注：该 `it.fails` 为 v1.6.0 遗留（非本次改造引入），本次不删除、不修改判据，如实保留缺口。
    const handle = renderView(JSON_INIT, '{"a":{"b":[1,2]}}', (tabId) =>
      createElement(JsonTreeView, { text: tabTextOf(tabId) }),
    );
    expect(auxOf(handle).textContent ?? '').toContain('数组(2 个元素)');
  });
});

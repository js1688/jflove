/**
 * EditorPanel / ScratchToolbar 回归守门用例（S-5 / S-6 / S-8 + AC-23 + M-13）。
 *
 * 判据出处：`文档记录/需求文档/v1.6.0.md`
 *   - AC-23（第 644 行）：「工具箱菜单列出 12 项，与 F5 编号一一对应；时间戳转换类功能 0 命中」
 *   - AC-16（第 632 行）：打开 `.md` → 可在**编辑 / 预览 / 分屏**三种视图间切换；分屏时编辑器与预览同时可见
 *   - AC-17（第 633 行）：打开 `.csv` 与 `.tsv` → 均可切到表格视图
 *   - AC-29（第 650 行）：JSON 树视图；AC-30（第 651 行）：Markdown 预览
 *   - M-13：打开文件后默认 `viewMode === 'edit'`
 * 另据 Phase 6 任务书：S-5/S-6/S-8 是 Phase 5 才修好的回归点，必须有自动化守门。
 *
 * ## v1.6.1 迁移说明（本文件的改动理由）
 *
 * 浮动窗格退役（设计 v1.6.1 §14.1）：`ScratchPane.tsx` 已由 `EditorPanel.tsx` 接替，
 * 组件只收 `{ tabId, active }` 并从 store 自取标签状态。随之失效的旧断言与处置：
 *
 * | 旧断言 | 处置 |
 * | --- | --- |
 * | `container.querySelector('.scratch-pane-main')`（S-5 的"工具栏真的在 DOM 里"锚点） | **改写**：v1.6.1 DOM 契约锚点是 `[data-editor-panel="<tabId>"]`（`EditorPanel.tsx:143-154`），工具栏是其子节点 |
 * | `.scratch-pane-main[data-view-mode]` | **改写**：视图模式的观测点改为 `.scratch-pane-body[data-pane-body]`（`EditorPanel.tsx:163`）—— `.scratch-pane-main` 这个类已不再由任何组件产出，其死样式也已在 Phase 6 收尾轮从 `styles/scratch-editor.css` 删除 |
 * | `createPane` / `panes` / `paneFromStore` | **改写**：改 `createTab` / `tabs`（标签页语义） |
 * | `applyReadResult`（打开文件落地） | **改写**：`openOrFocus(item: ReadItem)`（`stores/scratch-store.ts:570-601`）是 v1.6.1 打开文件的唯一入口，M-13 判据随之迁移 |
 * | `ScratchPane` + `pane`/`onRequestClose` props | **改写**：`EditorPanel` + `{ tabId, active }` |
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import { createElement } from 'react';

// ---- 依赖替身（vi.mock 会被 hoist，工厂内动态 import 避免与 helper 形成加载环） ----
vi.mock('../ScratchEditorHost', async (importOriginal) => {
  const real = await importOriginal<Record<string, unknown>>();
  const helper = await import('./scratch-editor-host-mock');
  // 判据：任务书「用 vi.mock 替身化 ScratchEditorHost … 只断言 DOM/结构/调用行为」
  return helper.scratchEditorHostModuleMock(real);
});

vi.mock('../../../utils/desktop-bridge', () => ({
  // 判据：jsdom 无 QWebChannel，真实桥调用必然 reject（桥层实现已注明）
  callBridge: vi.fn(async () => ({ ok: true })),
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
  callShell: vi.fn(async () => ({ ok: true })),
}));

vi.mock('../../../services/scratch-service', () => {
  const scratchService = {
    saveDraft: vi.fn(async () => ({ ok: true })),
    readText: vi.fn(async () => ({ ok: true, text: '' })),
    readTextOnly: vi.fn(async () => ({ ok: true, text: '' })),
    pickReadOnlyFile: vi.fn(async () => null),
    writeText: vi.fn(async () => ({ ok: true })),
    reportDirty: vi.fn(),
  };
  return {
    scratchService,
    isEditorBridgeReady: () => true,
    toDraftPaneMeta: (tab: unknown) => tab,
  };
});

import { EditorPanel } from '../EditorPanel';
import { ScratchToolbar } from '../ScratchToolbar';
import { useScratchStore } from '../../../stores/scratch-store';
import type { ReadItemOk, ScratchTab } from '../../../types/scratch';
import { TOOLBOX_ITEMS } from '../../../utils/scratch/toolbox';
import {
  registerMockSession,
  resetMockEditorSessions,
  resetScratchStore,
} from './scratch-editor-host-mock';

// ---- 夹具 ------------------------------------------------------------------------

/**
 * 摆一个标签页。
 *
 * 口径（证据：`src/stores/scratch-store.ts:148-152 capabilitiesForTab`）：`language === detected`
 * 时能力按 `path` 扩展名解析 —— 所以表格 / 树形能力必须给 `detected`（真实打开文件时它等于
 * 语言 id，由 `openOrFocus` 写入）。
 *
 * `createTab` 默认 `mounted: true`（`stores/scratch-store.ts:374`），因此面板直接渲染出正文区
 * 而不是骨架屏；这一点是下面所有 DOM 断言的前提。
 */
function makeTab(overrides: Partial<ScratchTab> = {}): ScratchTab {
  const id = useScratchStore.getState().createTab({
    title: '标签A',
    path: overrides.path ?? null,
    language: (overrides.language as string) ?? 'text',
    detected: (overrides.detected as string) ?? (overrides.language as string) ?? 'text',
    ...overrides,
  });
  return tabFromStore(id);
}

/** 取当前 store 里的某个标签（组件渲染后拿到的是快照，断言以 store 为准） */
function tabFromStore(tabId: string): ScratchTab {
  const found = useScratchStore.getState().tabs.find((item) => item.id === tabId);
  if (!found) throw new Error(`store 中找不到标签 ${tabId}`);
  return found;
}

/**
 * 用最新 store 状态渲染编辑面板。
 *
 * **先预置会话再渲染**：`EditorPanel` 在**渲染期**就现取正文
 * （`EditorPanel.tsx:138` `getScratchEditorSession(tabId)?.getText()`），
 * 所以正文必须在挂载前就位，否则视图拿到空串。jsdom 里会话只能由测试主动注册；
 * 工具栏 SQL 项也依赖会话的方言表（`ScratchToolbar.tsx:383`）。
 * 替身宿主挂载时会**保留已有会话的正文**（`scratch-editor-host-mock.tsx:213-217`）。
 */
function renderPanel(tabId: string, active = true, text = '') {
  registerMockSession(tabId, text);
  return render(createElement(EditorPanel, { tabId, active }));
}

/** 从渲染结果里取本面板根节点（DOM 契约锚点） */
function panelOf(container: HTMLElement, tabId: string): HTMLElement | null {
  return container.querySelector<HTMLElement>(`[data-editor-panel="${tabId}"]`);
}

beforeEach(() => {
  resetMockEditorSessions();
  resetScratchStore();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---- 用例 ------------------------------------------------------------------------

describe('S-5 / AC-23 工具栏真的在 DOM 里，且工具箱覆盖全部 12 项', () => {
  it('工具箱菜单项覆盖 TOOLBOX_ITEMS 的全部 12 项，且无时间戳类功能', () => {
    const tab = makeTab({ language: 'text', detected: 'text' });
    const { container, getByRole } = renderPanel(tab.id);

    // 判据（S-5）：工具栏「真的在 DOM 里」——不能只 import 却没渲染。
    // v1.6.1 锚点：`[data-editor-panel="<tabId>"]`（旧 `.scratch-pane-main` 已无产出方）。
    const panel = panelOf(container, tab.id);
    expect(panel).not.toBeNull();
    expect(panel?.querySelector('div.relative.flex.flex-wrap')).not.toBeNull(); // 工具栏根节点

    const toggle = getByRole('button', { name: /工具箱/ });
    expect(toggle.textContent).toContain('12');

    // AC-23：菜单项与 F5 的 12 项编号一一对应
    expect(TOOLBOX_ITEMS).toHaveLength(12);
    fireEvent.click(toggle);

    // 只数**工具箱面板**里的 li（面板根下唯一一处 ul）
    const items = Array.from(panel?.querySelectorAll('ul > li') ?? []);
    expect(items).toHaveLength(12);
    TOOLBOX_ITEMS.forEach((item, index) => {
      const text = items[index]?.textContent ?? '';
      expect(text).toContain(String(item.index));
      expect(text).toContain(item.label);
    });

    // AC-23 后半句：时间戳转换类功能 0 命中
    const menuText = items.map((li) => li.textContent ?? '').join(' ');
    expect(menuText).not.toMatch(/时间戳|timestamp|unix/i);
  });

  it('直接渲染 ScratchToolbar（不经面板外壳）同样给出 12 项', () => {
    const tab = makeTab({ language: 'text', detected: 'text' });
    // v1.6.1：工具栏 props 由 `pane` 变为 `tabId`（`ScratchToolbar.tsx:99-103`）
    const { container, getByRole } = render(createElement(ScratchToolbar, { tabId: tab.id }));
    fireEvent.click(getByRole('button', { name: /工具箱/ }));
    const items = Array.from(container.querySelectorAll('ul > li'));
    expect(items).toHaveLength(12);
    expect(
      TOOLBOX_ITEMS.every((item) =>
        items.some((li) => (li.textContent ?? '').includes(item.label)),
      ),
    ).toBe(true);
  });
});

describe('S-6 / AC-16 / AC-17 / AC-29 / AC-30 五种视图模式的数据属性与真实渲染', () => {
  it.each([
    ['edit', 'text', null, ''],
    ['split', 'markdown', 'a.md', '# T\n'],
    ['preview', 'markdown', 'a.md', '# T\n'],
    ['table', 'text', 'a.csv', 'a,b\n1,2\n'],
    ['tree', 'json', 'a.json', '{"a":{"b":[1,2]}}'],
  ] as const)('viewMode=%s → data-pane-body 正确且对应视图真的渲染', (mode, language, path, text) => {
    const tab = makeTab({
      language,
      detected: language,
      path,
      viewMode: mode,
    });
    // 正文来自宿主会话（EditorPanel.tsx:138 `getScratchEditorSession(tabId)?.getText()`）
    const { container } = renderPanel(tab.id, true, text);

    // AC-16/17/29/30/31 的公共判据：`.scratch-pane-body[data-pane-body]` 反映当前模式
    // （v1.6.1：旧锚点 `.scratch-pane-main[data-view-mode]` 已无产出方）
    expect(panelOf(container, tab.id)).not.toBeNull();
    const body = container.querySelector('.scratch-pane-body');
    expect(body?.getAttribute('data-pane-body')).toBe(mode);

    if (mode === 'edit' || mode === 'split') {
      expect(container.querySelector('[data-testid="scratch-editor-host-mock"]')).not.toBeNull();
      // 这两种模式下宿主槽位可见（`EditorPanel.tsx:165` hidden={!showHost}）
      expect(container.querySelector('.scratch-pane-host')?.hasAttribute('hidden')).toBe(false);
    }
    if (mode === 'split' || mode === 'preview') {
      expect(container.querySelector('.markdown-body')).not.toBeNull();
    }
    if (mode === 'table') {
      // AC-17：CSV/TSV → 表格视图真的渲染
      expect(container.querySelector('table')).not.toBeNull();
    }
    if (mode === 'tree') {
      // AC-29：JSON → 树视图（递归 details）真的渲染
      expect(container.querySelector('details')).not.toBeNull();
    }
    if (mode === 'edit' || mode === 'table' || mode === 'tree') {
      // 非 markdown 视图不出现预览区
      expect(container.querySelector('.markdown-body')).toBeNull();
    }
  });

  it('preview 下宿主 hidden 但节点仍在（不卸载，切回状态不丢）', () => {
    const tab = makeTab({
      language: 'markdown',
      detected: 'markdown',
      path: 'a.md',
      viewMode: 'preview',
    });
    const { container } = renderPanel(tab.id);
    const host = container.querySelector('.scratch-pane-host');
    expect(host).not.toBeNull();
    // 判据（任务书 S-6）：宿主 hidden 但不卸载
    expect(host?.hasAttribute('hidden')).toBe(true);
    expect(container.querySelector('[data-testid="scratch-editor-host-mock"]')).not.toBeNull();
  });

  it('切到 table 再切回 edit 时复用同一宿主节点（React 未卸载）', () => {
    const tab = makeTab({ language: 'text', detected: 'text' });
    const view = renderPanel(tab.id);
    const before = view.container.querySelector('.scratch-pane-host');
    expect(before).not.toBeNull();
    expect(before?.hasAttribute('hidden')).toBe(false);

    for (const mode of ['table', 'edit'] as const) {
      useScratchStore.getState().setViewMode(tab.id, mode);
      // 同一 render 实例上同步重渲染 ⇒ 同一 DOM 树，才能断言"节点被复用而非重建"
      view.rerender(createElement(EditorPanel, { tabId: tab.id, active: true }));
      expect(view.container.querySelector('.scratch-pane-body')?.getAttribute('data-pane-body')).toBe(
        mode,
      );
      expect(view.container.querySelector('.scratch-pane-host')).toBe(before);
    }
    expect(before?.hasAttribute('hidden')).toBe(false);
  });

  it('markdown 的能力门：编辑 / 预览 / 分屏可点，表格 / 树形禁用（AC-16 只要求三种视图）', () => {
    const tab = makeTab({ language: 'markdown', detected: 'markdown', path: 'a.md' });
    const { getByRole } = renderPanel(tab.id);
    for (const label of ['编辑', '预览', '分屏']) {
      expect((getByRole('button', { name: label }) as HTMLButtonElement).disabled).toBe(false);
    }
    expect((getByRole('button', { name: '表格' }) as HTMLButtonElement).disabled).toBe(true);
    expect((getByRole('button', { name: '树形' }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('S-8 非 passive 的 wheel 监听（Ctrl+滚轮改字号的先决条件）', () => {
  it('宿主注册 wheel 时 passive === false', () => {
    const addSpy = vi.spyOn(HTMLElement.prototype, 'addEventListener');
    const tab = makeTab({ language: 'text', detected: 'text' });
    const { container } = renderPanel(tab.id);

    // 判据（S-8 原文）：存在**非 passive** 的 wheel 监听 —— 否则 Ctrl+滚轮
    // 无法 preventDefault()，页面会跟着缩放。
    const wheelCalls = addSpy.mock.calls.filter((call) => call[0] === 'wheel');
    expect(wheelCalls.length).toBeGreaterThan(0);
    const options = wheelCalls[wheelCalls.length - 1]?.[2];
    if (typeof options === 'object' && options !== null) {
      expect((options as { passive?: boolean }).passive).toBe(false);
    } else {
      // 传布尔/不传时，只有 false/undefined 才等价于 non-passive
      expect(options === false || options === undefined).toBe(true);
    }
    expect(container.querySelector('[data-testid="scratch-editor-host-mock"]')).not.toBeNull();
  });
});

describe('M-13 打开文件后默认 viewMode === edit（v1.6.1：改由 openOrFocus 判定）', () => {
  /** 造一条成功的读文件结果（字段逐字对齐 `types/scratch.ts:100-111`） */
  function readOk(overrides: Partial<ReadItemOk> = {}): ReadItemOk {
    return {
      ok: true,
      path: 'D:/tmp/a.md',
      name: 'a.md',
      text: '# T\n',
      size: 4,
      language: 'markdown',
      encoding: 'utf-8',
      lineEnding: 'lf',
      readOnly: false,
      warnings: [],
      ...overrides,
    };
  }

  it('openOrFocus（打开方式调用 / 读文件落地）产出的标签 viewMode 为 edit', () => {
    // `applyReadResult` 已在 v1.6.1 从 store 删除（设计 §4.3 迁移表），打开文件的唯一入口是
    // `openOrFocus`（`stores/scratch-store.ts:570-601`），M-13 判据随之迁移。
    const id = useScratchStore.getState().openOrFocus(readOk());
    expect(id).toBeTruthy();
    // 判据（M-13 / AC-45）：被作为打开方式调用后默认唤起文本编辑器视图
    expect(tabFromStore(id as string).viewMode).toBe('edit');
  });

  it('openOrFocus 对同路径二次打开只聚焦、不新建（AC-65）', () => {
    // 判据（AC-65，`stores/scratch-store.ts:576-581`）：`path` 非空且相等的已有标签 ⇒ 只 `setActiveTab`。
    // 这是"浮动窗格 → 标签页"语义下对旧"同路径再开一个窗格"行为的**等价替代判据**。
    const first = useScratchStore.getState().openOrFocus(readOk({ path: 'D:/tmp/same.md', name: 'same.md' }));
    const second = useScratchStore.getState().openOrFocus(readOk({ path: 'D:/tmp/same.md', name: 'same.md' }));
    expect(second).toBe(first);
    expect(useScratchStore.getState().tabs).toHaveLength(1);
    expect(useScratchStore.getState().activeTabId).toBe(first);
  });

  it('createTab 的默认 viewMode 也是 edit', () => {
    const tab = makeTab({ language: 'text', detected: 'text' });
    expect(tab.viewMode).toBe('edit');
  });
});

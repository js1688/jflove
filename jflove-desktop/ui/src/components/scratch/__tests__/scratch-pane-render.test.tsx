/**
 * ScratchPane / ScratchToolbar 回归守门用例（S-5 / S-6 / S-8 + AC-23 + M-13）。
 *
 * 判据出处：`文档记录/需求文档/v1.6.0.md`
 *   - AC-23（第 644 行）：「工具箱菜单列出 12 项，与 F5 编号一一对应；时间戳转换类功能 0 命中」
 *   - AC-16（第 632 行）：打开 `.md` → 可在**编辑 / 预览 / 分屏**三种视图间切换；分屏时编辑器与预览同时可见
 *   - AC-17（第 633 行）：打开 `.csv` 与 `.tsv` → 均可切到表格视图
 *   - AC-29（第 650 行）：JSON 树视图；AC-30（第 651 行）：Markdown 预览
 *   - M-13：打开文件后默认 `viewMode === 'edit'`
 * 另据 Phase 6 任务书：S-5/S-6/S-8 是 Phase 5 才修好的回归点，必须有自动化守门。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import { createElement } from 'react';

// ---- 依赖替身（vi.mock 会被 hoist，工厂内动态 import 避免与 helper 形成加载环） ----
vi.mock('../ScratchEditorHost', async () => {
  const helper = await import('./scratch-editor-host-mock');
  return {
    // 判据：任务书「用 vi.mock 替身化 ScratchEditorHost … 只断言 DOM/结构/调用行为」
    ScratchEditorHost: helper.MockScratchEditorHost,
    getScratchEditorSession: helper.getScratchEditorSession,
    listScratchEditorPaneIds: helper.listScratchEditorPaneIds,
    subscribeScratchEditorStats: helper.subscribeScratchEditorStats,
    useScratchEditorStats: helper.useScratchEditorStats,
    computeEditorStats: () => ({ line: 1, col: 1, selectedChars: 0, totalChars: 0 }),
  };
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
    toDraftPaneMeta: (pane: unknown) => pane,
  };
});

import { ScratchPane } from '../ScratchPane';
import { ScratchToolbar } from '../ScratchToolbar';
import { useScratchStore } from '../../../stores/scratch-store';
import type { ScratchPane as ScratchPaneModel } from '../../../types/scratch';
import { TOOLBOX_ITEMS } from '../../../utils/scratch/toolbox';
import { resetMockEditorSessions, registerMockSession } from './scratch-editor-host-mock';

// ---- 夹具 ------------------------------------------------------------------------

/**
 * 摆一个窗格。
 *
 * 口径（证据：`src/stores/scratch-store.ts:166 capabilitiesForPane`）：`language === detected`
 * 时能力按 `path` 扩展名解析 —— 所以表格 / 树形能力必须给 `detected`（真实打开文件时它等于
 * 语言 id，由 `applyReadResult` 写入）。
 */
function makePane(overrides: Partial<ScratchPaneModel> = {}): ScratchPaneModel {
  const id = useScratchStore.getState().createPane({
    title: '窗格A',
    path: overrides.path ?? null,
    language: (overrides.language as string) ?? 'text',
    detected: (overrides.detected as string) ?? (overrides.language as string) ?? 'text',
  });
  if (Object.keys(overrides).length > 0) {
    useScratchStore.setState((state) => ({
      panes: state.panes.map((pane) => (pane.id === id ? { ...pane, ...overrides } : pane)),
    }));
  }
  return paneFromStore(id);
}

/** 取当前 store 里的某个窗格（组件渲染后拿到的是快照，断言以 store 为准） */
function paneFromStore(paneId: string): ScratchPaneModel {
  const found = useScratchStore.getState().panes.find((pane) => pane.id === paneId);
  if (!found) throw new Error(`store 中找不到窗格 ${paneId}`);
  return found;
}

/**
 * 用最新 store 状态渲染窗格。
 *
 * 先预置会话：`ScratchPane` 只把窗格当作宿主来挂载，会先触发 `mounted` 副作用再读
 * `getScratchEditorSession(id).getText()`（ScratchPane.tsx:131）。jsdom 里会话必须
 * 由测试**主动**注册；工具栏 SQL 项也依赖会话的方言表（ScratchToolbar.tsx:368）。
 */
function renderPane(paneId: string, active = true, text = '') {
  registerMockSession(paneId, text);
  return render(
    createElement(ScratchPane, {
      pane: paneFromStore(paneId),
      active,
      onRequestClose: () => undefined,
    }),
  );
}

beforeEach(() => {
  resetMockEditorSessions();
  useScratchStore.setState({
    panes: [],
    activePaneId: null,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---- 用例 ------------------------------------------------------------------------

describe('S-5 / AC-23 工具栏真的在 DOM 里，且工具箱覆盖全部 12 项', () => {
  it('工具箱菜单项覆盖 TOOLBOX_ITEMS 的全部 12 项，且无时间戳类功能', () => {
    const pane = makePane({ language: 'text', detected: 'text' });
    const { container, getByRole } = renderPane(pane.id);

    // 判据（S-5）：工具栏「真的在 DOM 里」——不能只 import 却没渲染
    expect(container.querySelector('.scratch-pane-main')).not.toBeNull();
    const toggle = getByRole('button', { name: /工具箱/ });
    expect(toggle.textContent).toContain('12');

    // AC-23：菜单项与 F5 的 12 项编号一一对应
    expect(TOOLBOX_ITEMS).toHaveLength(12);
    fireEvent.click(toggle);

    const items = Array.from(container.querySelectorAll('li'));
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

  it('直接渲染 ScratchToolbar（不经窗格外壳）同样给出 12 项', () => {
    const pane = makePane({ language: 'text', detected: 'text' });
    const { container, getByRole } = render(
      createElement(ScratchToolbar, { pane: paneFromStore(pane.id) }),
    );
    fireEvent.click(getByRole('button', { name: /工具箱/ }));
    const items = Array.from(container.querySelectorAll('li'));
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
  ] as const)('viewMode=%s → data-view-mode 正确且对应视图真的渲染', (mode, language, path, text) => {
    const pane = makePane({
      language,
      detected: language,
      path,
      viewMode: mode,
    });
    // 正文来自宿主会话（ScratchPane.tsx:131 `viewText = getScratchEditorSession(id).getText()`）
    const { container } = renderPane(pane.id, true, text);

    // AC-16/17/29/30/31 的公共判据：`.scratch-pane-main[data-view-mode]` 反映当前模式
    const main = container.querySelector('.scratch-pane-main');
    expect(main?.getAttribute('data-view-mode')).toBe(mode);
    expect(container.querySelector('.scratch-pane-body')?.getAttribute('data-pane-body')).toBe(mode);

    if (mode === 'edit' || mode === 'split') {
      expect(container.querySelector('[data-testid="scratch-editor-host-mock"]')).not.toBeNull();
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
    const pane = makePane({
      language: 'markdown',
      detected: 'markdown',
      path: 'a.md',
      viewMode: 'preview',
    });
    const { container } = renderPane(pane.id);
    const host = container.querySelector('.scratch-pane-host');
    expect(host).not.toBeNull();
    // 判据（任务书 S-6）：宿主 hidden 但不卸载
    expect(host?.hasAttribute('hidden')).toBe(true);
    expect(container.querySelector('[data-testid="scratch-editor-host-mock"]')).not.toBeNull();
  });

  it('切到 table 再切回 edit 时复用同一宿主节点（React 未卸载）', () => {
    const pane = makePane({ language: 'text', detected: 'text' });
    const { container, rerender } = renderPane(pane.id);
    const before = container.querySelector('.scratch-pane-host');
    expect(before).not.toBeNull();
    expect(before?.hasAttribute('hidden')).toBe(false);

    const rerenderNow = () =>
      rerender(
        createElement(ScratchPane, {
          pane: paneFromStore(pane.id),
          active: true,
          onRequestClose: () => undefined,
        }),
      );

    for (const mode of ['table', 'edit'] as const) {
      useScratchStore.getState().setViewMode(pane.id, mode);
      rerenderNow();
      expect(container.querySelector('.scratch-pane-main')?.getAttribute('data-view-mode')).toBe(mode);
      expect(container.querySelector('.scratch-pane-host')).toBe(before);
    }
    expect(before?.hasAttribute('hidden')).toBe(false);
  });

  it('markdown 的能力门：编辑 / 预览 / 分屏可点，表格 / 树形禁用（AC-16 只要求三种视图）', () => {
    const pane = makePane({ language: 'markdown', detected: 'markdown', path: 'a.md' });
    const { getByRole } = renderPane(pane.id);
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
    const pane = makePane({ language: 'text', detected: 'text' });
    const { container } = renderPane(pane.id);

    // 判据（S-8 原文）：window 上存在**非 passive** 的 wheel 监听 —— 否则 Ctrl+滚轮
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

describe('M-13 打开文件后默认 viewMode === edit', () => {
  it('applyReadResult（打开方式调用 / 读文件落地）产出的窗格 viewMode 为 edit', () => {
    const id = useScratchStore.getState().applyReadResult({
      ok: true,
      path: 'D:/tmp/a.md',
      text: '# T\n',
      language: 'markdown',
      encoding: 'utf-8',
      lineEnding: 'lf',
      readOnly: false,
      largeFile: false,
    } as never);
    expect(id).toBeTruthy();
    // 判据（M-13 / AC-45）：被作为打开方式调用后默认唤起文本编辑器视图
    expect(paneFromStore(id as string).viewMode).toBe('edit');
  });

  it('createPane 的默认 viewMode 也是 edit', () => {
    const pane = makePane({ language: 'text', detected: 'text' });
    expect(pane.viewMode).toBe('edit');
  });
});

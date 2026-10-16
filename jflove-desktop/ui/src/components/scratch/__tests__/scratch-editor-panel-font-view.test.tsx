/**
 * 字号缩放 / 行号 / 自动换行的用例（AC-35）。
 *
 * 判据出处：`文档记录/需求文档/v1.6.0.md` 第 656 行 AC-35：
 *   「Ctrl+滚轮向上 → 字号（store 值 / 界面可读值）增大、向下减小并受 `[8,40]` 夹取；
 *     关闭行号 → 宿主收到关闭信号（断言 store 值 + 传给宿主的 props/属性）；
 *     关闭自动换行 → 长行不折行（断言传给宿主的 lineWrapping/等价配置为关）[负向对照]」
 *
 * 真实实现口径（证据：`components/scratch/ScratchEditorHost.tsx`）
 *   - 轮子监听：`element.addEventListener('wheel', onWheel, { passive: false })`（第 619 行），
 *     Ctrl 时 `stepFontSize(fontSize, deltaY < 0 ? +WHEEL_FONT_STEP : -WHEEL_FONT_STEP)`
 *     （第 600-608 行，`WHEEL_FONT_STEP = 2` 在第 54 行）；
 *   - 行号：`compartments.lineNumbers.reconfigure(pane.showLineNumbers ? api.extensions.lineNumbers() : [])`
 *     （第 571 行，装载期见第 492 行）——关闭 ⇒ 收到空扩展数组；
 *   - 自动换行：`compartments.wrap.reconfigure(pane.wordWrap ? api.EditorView.lineWrapping : [])`
 *     （第 575 行，装载期见第 498 行）——关闭 ⇒ 收到空扩展数组。
 *
 * 替身把自己收到的行号 / 换行开关以 `data-show-line-numbers` / `data-word-wrap` 属性
 * 暴露出来（等价于上面两处 reconfiguration 的可观察信号），并用 CSS
 * `white-space: pre` + `overflow-x: auto` 表达"长行不折行、出现横向滚动"。
 *
 * v1.6.1 迁移：浮动窗格 `ScratchPane` 已被 `EditorPanel`（整页工作区 + 标签页）取代，
 * 本文件随之改按标签页口径渲染（`renderTabInStore` + `EditorPanel`），
 * 并新增「非激活标签 hidden 但保持挂载」的等价判据。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createElement } from 'react';

// ---- 依赖替身（vi.mock 会被 hoist，工厂内动态 import 避免与 helper 形成加载环） ----
vi.mock('../ScratchEditorHost', async (importOriginal) => {
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
  toDraftPaneMeta: (pane: unknown) => pane,
}));

import { EditorPanel } from '../EditorPanel';
import { useScratchStore } from '../../../stores/scratch-store';
import type { ScratchTab } from '../../../types/scratch';
import { FONT_SIZE_MAX, FONT_SIZE_MIN, stepFontSize } from '../../../utils/scratch/toolbox';
import {
  getScratchEditorSession,
  registerMockSession,
  renderTabInStore,
  resetMockEditorSessions,
  resetScratchStore,
  type TabRenderHandle,
} from './scratch-editor-host-mock';

// ---- 夹具 ------------------------------------------------------------------------

/**
 * 渲染一个真实挂载的编辑面板（`viewMode: 'edit'` → 宿主可见）。
 *
 * v1.6.1：面板只吃 `{ tabId, active }`（`EditorPanel.tsx:62-72`），标签状态一律从 store 取。
 * 会话必须在**渲染前**就位：`EditorPanel` 在渲染期现取正文（`EditorPanel.tsx:138`）。
 */
function renderEditorTab(init: Partial<ScratchTab> = {}, text = 'a\nb\n'): TabRenderHandle {
  let knownTabId = '';
  const handle = renderTabInStore(
    () => {
      // 会话必须在**渲染前**就位：`EditorPanel` 在渲染期现取正文（`EditorPanel.tsx:138`）。
      // 替身宿主挂载时会保留已有会话的正文（`scratch-editor-host-mock.tsx:213-217`）。
      if (!getScratchEditorSession(knownTabId)) registerMockSession(knownTabId, text);
      return createElement(EditorPanel, { tabId: knownTabId, active: true });
    },
    { language: 'text', detected: 'text', ...init },
  );
  knownTabId = handle.tabId;
  handle.sync();
  return handle;
}

/** 取替身宿主的根节点（wheel 监听装在它上面） */
function hostOf(container: HTMLElement): HTMLElement {
  const host = container.querySelector<HTMLElement>('[data-testid="scratch-editor-host-mock"]');
  if (!host) throw new Error('没有渲染出编辑器宿主');
  return host;
}

/** 当前 store 里的字号（标签页口径：`ScratchState.tabs`） */
function fontSizeOf(tabId: string): number {
  const tab = useScratchStore.getState().tabs.find((item) => item.id === tabId);
  if (!tab) throw new Error(`store 中找不到标签 ${tabId}`);
  return tab.fontSize;
}

/**
 * 打开工具箱并返回「字号缩放 / 行号 / 自动换行」那一项（第 12 项）。
 *
 * 工具箱按钮是**开关**（`ScratchToolbar.tsx:504-510` `setMenuOpen((open) => !open)`），
 * 所以必须先看 `aria-expanded` 再决定要不要点。
 */
function openFontGroup(container: HTMLElement): HTMLElement {
  const toggle = screen.getByRole('button', { name: /工具箱/ });
  if (toggle.getAttribute('aria-expanded') !== 'true') {
    fireEvent.click(toggle);
  }
  const items = Array.from(container.querySelectorAll<HTMLElement>('[data-editor-panel] ul > li'));
  const fontItem = items.find((item) => (item.textContent ?? '').includes('A−'));
  if (!fontItem) throw new Error('工具箱里找不到「字号缩放 / 行号 / 自动换行」分组');
  return fontItem;
}

/** 在给定分组里按文案找按钮 */
function groupButton(group: HTMLElement, label: RegExp): HTMLButtonElement {
  const found = Array.from(group.querySelectorAll('button')).find((button) =>
    label.test(button.textContent ?? ''),
  );
  if (!found) throw new Error(`分组里找不到按钮：${String(label)}`);
  return found;
}

beforeEach(() => {
  resetMockEditorSessions();
  resetScratchStore();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---- AC-35 Ctrl + 滚轮改字号 ------------------------------------------------------

describe('AC-35 Ctrl + 滚轮缩放字号', () => {
  it('向上（deltaY < 0）增大字号，且界面可读值与 store 同步', () => {
    // 判据：AC-35 原文「Ctrl+滚轮向上 → 字号（store 值 / 界面可读值）增大」
    const handle = renderEditorTab({}, '');
    const before = fontSizeOf(handle.tabId);
    const host = hostOf(handle.container);

    act(() => {
      fireEvent.wheel(host, { deltaY: -120, ctrlKey: true });
    });
    handle.sync();

    expect(fontSizeOf(handle.tabId)).toBe(before + 2);
    // 界面可读值：工具箱分组里的「Npx」
    const group = openFontGroup(handle.container);
    expect(group.textContent).toContain(`${before + 2}px`);
    // 界面可读值（宿主侧）：font-size 样式
    expect(hostOf(handle.container).querySelector<HTMLElement>('[data-cm-container]')?.style.fontSize).toBe(
      `${before + 2}px`,
    );
  });

  it('向下（deltaY > 0）减小字号', () => {
    // 判据：AC-35 原文「向下减小」
    const handle = renderEditorTab({}, '');
    const before = fontSizeOf(handle.tabId);
    const host = hostOf(handle.container);

    act(() => {
      fireEvent.wheel(host, { deltaY: 120, ctrlKey: true });
    });

    expect(fontSizeOf(handle.tabId)).toBe(before - 2);
  });

  it('不按 Ctrl 不缩放（负向对照）', () => {
    // 判据：`ScratchEditorHost.tsx:601-604` 只有 `event.ctrlKey` 才走缩放分支
    const handle = renderEditorTab({}, '');
    const before = fontSizeOf(handle.tabId);
    const host = hostOf(handle.container);

    act(() => {
      fireEvent.wheel(host, { deltaY: -120 });
    });

    expect(fontSizeOf(handle.tabId)).toBe(before);
  });

  it('字号被 [8,40] 夹取：连续放大不超过 40，连续缩小不低于 8（负向对照）', () => {
    // 判据：AC-35 原文「受 [8,40] 夹取」；实现见 `utils/scratch/toolbox.ts:871-885`
    //       `FONT_SIZE_MIN` / `FONT_SIZE_MAX` / `clampFontSize` / `stepFontSize`
    //       与 `stores/scratch-store.ts:500-505` 的 setFontSize
    expect(FONT_SIZE_MIN).toBe(8);
    expect(FONT_SIZE_MAX).toBe(40);
    // 判决器自检：越界输入被夹住
    expect(stepFontSize(FONT_SIZE_MAX, 2)).toBe(FONT_SIZE_MAX);
    expect(stepFontSize(FONT_SIZE_MIN, -2)).toBe(FONT_SIZE_MIN);

    const upper = renderEditorTab({}, '');
    const upperHost = hostOf(upper.container);
    useScratchStore.getState().setFontSize(upper.tabId, FONT_SIZE_MAX);
    act(() => {
      fireEvent.wheel(upperHost, { deltaY: -120, ctrlKey: true });
    });
    expect(fontSizeOf(upper.tabId)).toBe(FONT_SIZE_MAX);

    const lower = renderEditorTab({}, '');
    const lowerHost = hostOf(lower.container);
    useScratchStore.getState().setFontSize(lower.tabId, FONT_SIZE_MIN);
    act(() => {
      fireEvent.wheel(lowerHost, { deltaY: 120, ctrlKey: true });
    });
    expect(fontSizeOf(lower.tabId)).toBe(FONT_SIZE_MIN);
  });

  it('工具箱的 A+ / A− 与滚轮同一条判决器路径', () => {
    // 判据：`ScratchToolbar.tsx:438-447` 的 A−/A+ 调 stepFontSize，与滚轮共用同一夹取口径
    const handle = renderEditorTab({}, '');
    const before = fontSizeOf(handle.tabId);
    const group = openFontGroup(handle.container);

    fireEvent.click(groupButton(group, /^A\+$/));
    expect(fontSizeOf(handle.tabId)).toBe(before + 2);
    handle.sync();

    const group2 = openFontGroup(handle.container);
    fireEvent.click(groupButton(group2, /^A−$/));
    expect(fontSizeOf(handle.tabId)).toBe(before);
  });
});

// ---- AC-35 行号开关 --------------------------------------------------------------

describe('AC-35 行号开关', () => {
  it('关闭行号 → store 关、宿主收到关闭信号', () => {
    // 判据：AC-35 原文「关闭行号 → 宿主收到关闭信号（断言 store 值 + 传给宿主的 props/属性）」
    // 真实实现：`ScratchEditorHost.tsx:571`
    //   `compartments.lineNumbers.reconfigure(pane.showLineNumbers ? api.extensions.lineNumbers() : [])`
    const handle = renderEditorTab();
    // 默认开着行号（`stores/scratch-store.ts:370` createTab 默认 showLineNumbers: true）
    expect(
      useScratchStore.getState().tabs.find((p) => p.id === handle.tabId)?.showLineNumbers,
    ).toBe(true);
    expect(hostOf(handle.container).getAttribute('data-show-line-numbers')).toBe('on');

    const group = openFontGroup(handle.container);
    fireEvent.click(groupButton(group, /行号/));
    handle.sync();

    // ① store 值
    expect(
      useScratchStore.getState().tabs.find((p) => p.id === handle.tabId)?.showLineNumbers,
    ).toBe(false);
    // ② 传给宿主的属性（等价于收到的行号扩展为 []）
    expect(hostOf(handle.container).getAttribute('data-show-line-numbers')).toBe('off');
    // ③ 界面可读值：按钮文案回到未勾选态
    const group2 = openFontGroup(handle.container);
    expect(groupButton(group2, /行号/).textContent).toBe('行号');
  });
});

// ---- AC-35 自动换行开关 ----------------------------------------------------------

describe('AC-35 自动换行开关', () => {
  it('关闭自动换行 → 长行不折行、出现横向滚动（负向对照）', () => {
    // 判据：AC-35 原文「关闭自动换行 → 长行不折行（断言传给宿主的 lineWrapping/等价配置为关）」
    // 真实实现：`ScratchEditorHost.tsx:575`
    //   `compartments.wrap.reconfigure(pane.wordWrap ? api.EditorView.lineWrapping : [])`
    const longLine = `${'x'.repeat(400)}\n`;
    const handle = renderEditorTab({ wordWrap: true }, longLine);
    // 开换行时：宿主报告 on
    expect(hostOf(handle.container).getAttribute('data-word-wrap')).toBe('on');

    const group = openFontGroup(handle.container);
    fireEvent.click(groupButton(group, /自动换行/));
    handle.sync();

    // ① store 值
    expect(useScratchStore.getState().tabs.find((p) => p.id === handle.tabId)?.wordWrap).toBe(false);
    // ② 传给宿主的属性（等价于 lineWrapping 扩展为 []）
    const host = hostOf(handle.container);
    expect(host.getAttribute('data-word-wrap')).toBe('off');
    // ③ 宿主容器：不折行 + 横向滚动（长行不折行的等价可观察信号）
    const inner = host.querySelector<HTMLElement>('[data-cm-container]');
    expect(inner).not.toBeNull();
    expect(inner?.style.whiteSpace).toBe('pre');
    expect(inner?.style.overflowX).toBe('auto');
    // ④ 界面可读值：按钮文案回到未勾选态
    const group2 = openFontGroup(handle.container);
    expect(groupButton(group2, /自动换行/).textContent).toBe('自动换行');
  });
});

// ---- v1.6.1 标签页等价判据：非激活面板 hidden 但保持挂载 ---------------------------

describe('v1.6.1 标签页语义：非激活面板 hidden 但保持挂载（AC-66）', () => {
  it('切到另一个标签后，前一标签的面板仍在 DOM 里、带 hidden 且去掉 active 标记', () => {
    // 判据（替代已退役的窗格几何 / 最小化最大化 / z 序判据）：设计 v1.6.1 §4.2 DOM 契约
    //   —— 根元素恒带 `data-editor-panel="<tabId>"`，**仅激活时**带 `data-editor-panel-active="true"`
    //      （`EditorPanel.tsx:143-154`），非激活面板 `hidden` + 内联 `display:none` 但**不卸载**：
    //      保住 CM6 实例、撤销历史、光标与滚动位置。
    // 工作区口径（`ScratchWorkspace.tsx`）：**同一个容器里并列挂 N 个面板**，只有一个 active。
    const first = renderTabInStore(() => createElement('div'));
    const second = renderTabInStore(() => createElement('div'));
    registerMockSession(first.tabId);

    const view = render(
      createElement(
        'div',
        null,
        createElement(EditorPanel, { tabId: first.tabId, active: true }),
        createElement(EditorPanel, { tabId: second.tabId, active: false }),
      ),
    );

    const firstPanel = view.container.querySelector<HTMLElement>(
      `[data-editor-panel="${first.tabId}"]`,
    );
    const secondPanel = view.container.querySelector<HTMLElement>(
      `[data-editor-panel="${second.tabId}"]`,
    );
    expect(view.container.querySelectorAll('[data-editor-panel]')).toHaveLength(2);
    expect(firstPanel?.getAttribute('data-editor-panel-active')).toBe('true');
    expect(firstPanel?.hasAttribute('hidden')).toBe(false);
    expect(secondPanel?.hasAttribute('hidden')).toBe(true);
    expect((secondPanel as HTMLElement).style.display).toBe('none');

    // 切到第二个标签：第一个转为非激活
    useScratchStore.getState().setActiveTab(second.tabId);
    view.rerender(
      createElement(
        'div',
        null,
        createElement(EditorPanel, { tabId: first.tabId, active: false }),
        createElement(EditorPanel, { tabId: second.tabId, active: true }),
      ),
    );

    // ① 仍是两个面板（**没有卸载**）
    const panels = view.container.querySelectorAll('[data-editor-panel]');
    expect(panels).toHaveLength(2);
    // ② 前一标签：去掉 active 标记、加上 hidden + 内联 display:none
    expect(firstPanel?.getAttribute('data-editor-panel-active')).toBeNull();
    expect(firstPanel?.hasAttribute('hidden')).toBe(true);
    expect((firstPanel as HTMLElement).style.display).toBe('none');
    // ③ 未卸载的直接证据：CM6 宿主槽位与其替身仍挂在原节点上
    expect(firstPanel?.querySelector('.scratch-pane-host')).not.toBeNull();
    expect(
      firstPanel?.querySelector('[data-testid="scratch-editor-host-mock"]'),
    ).not.toBeNull();
  });
});

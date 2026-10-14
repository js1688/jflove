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
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
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

import { ScratchPane } from '../ScratchPane';
import { useScratchStore } from '../../../stores/scratch-store';
import type { ScratchPane as ScratchPaneModel } from '../../../types/scratch';
import { FONT_SIZE_MAX, FONT_SIZE_MIN, stepFontSize } from '../../../utils/scratch/toolbox';
import {
  renderPaneInStore,
  resetMockEditorSessions,
  resetScratchStore,
  setPaneText,
} from './scratch-editor-host-mock';

// ---- 夹具 ------------------------------------------------------------------------

/** 渲染一个真实挂载的编辑窗格（edit 视图 → 宿主可见） */
function renderEditorPane(init: Partial<ScratchPaneModel> = {}, text = 'a\nb\n') {
  const handle = renderPaneInStore(
    (pane) => createElement(ScratchPane, { pane, active: true, onRequestClose: () => undefined }),
    { language: 'text', detected: 'text', ...init },
  );
  setPaneText(handle.paneId, text);
  handle.sync();
  return handle;
}

/** 取替身宿主的根节点（wheel 监听装在它上面） */
function hostOf(container: HTMLElement): HTMLElement {
  const host = container.querySelector<HTMLElement>('[data-testid="scratch-editor-host-mock"]');
  if (!host) throw new Error('没有渲染出编辑器宿主');
  return host;
}

/** 当前 store 里的字号 */
function fontSizeOf(paneId: string): number {
  const pane = useScratchStore.getState().panes.find((item) => item.id === paneId);
  if (!pane) throw new Error(`store 中找不到窗格 ${paneId}`);
  return pane.fontSize;
}

/**
 * 打开工具箱并返回「字号缩放 / 行号 / 自动换行」那一项（第 12 项）。
 *
 * 工具箱按钮是**开关**（`ScratchToolbar.tsx:485-492` `setMenuOpen((open) => !open)`），
 * 所以必须先看 `aria-expanded` 再决定要不要点。
 */
function openFontGroup(container: HTMLElement): HTMLElement {
  const toggle = screen.getByRole('button', { name: /工具箱/ });
  if (toggle.getAttribute('aria-expanded') !== 'true') {
    fireEvent.click(toggle);
  }
  const items = Array.from(container.querySelectorAll<HTMLElement>('.scratch-pane-main ul > li'));
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
    const handle = renderEditorPane({}, '');
    const before = fontSizeOf(handle.paneId);
    const host = hostOf(handle.container);

    act(() => {
      fireEvent.wheel(host, { deltaY: -120, ctrlKey: true });
    });
    handle.sync();

    expect(fontSizeOf(handle.paneId)).toBe(before + 2);
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
    const handle = renderEditorPane({}, '');
    const before = fontSizeOf(handle.paneId);
    const host = hostOf(handle.container);

    act(() => {
      fireEvent.wheel(host, { deltaY: 120, ctrlKey: true });
    });

    expect(fontSizeOf(handle.paneId)).toBe(before - 2);
  });

  it('不按 Ctrl 不缩放（负向对照）', () => {
    // 判据：`ScratchEditorHost.tsx:601-604` 只有 `event.ctrlKey` 才走缩放分支
    const handle = renderEditorPane({}, '');
    const before = fontSizeOf(handle.paneId);
    const host = hostOf(handle.container);

    act(() => {
      fireEvent.wheel(host, { deltaY: -120 });
    });

    expect(fontSizeOf(handle.paneId)).toBe(before);
  });

  it('字号被 [8,40] 夹取：连续放大不超过 40，连续缩小不低于 8（负向对照）', () => {
    // 判据：AC-35 原文「受 [8,40] 夹取」；实现见 `utils/scratch/toolbox.ts:871-885`
    //       `FONT_SIZE_MIN` / `FONT_SIZE_MAX` / `clampFontSize` / `stepFontSize`
    //       与 `stores/scratch-store.ts:599-604` 的 setFontSize
    expect(FONT_SIZE_MIN).toBe(8);
    expect(FONT_SIZE_MAX).toBe(40);
    // 判决器自检：越界输入被夹住
    expect(stepFontSize(FONT_SIZE_MAX, 2)).toBe(FONT_SIZE_MAX);
    expect(stepFontSize(FONT_SIZE_MIN, -2)).toBe(FONT_SIZE_MIN);

    const upper = renderEditorPane({}, '');
    const upperHost = hostOf(upper.container);
    useScratchStore.getState().setFontSize(upper.paneId, FONT_SIZE_MAX);
    act(() => {
      fireEvent.wheel(upperHost, { deltaY: -120, ctrlKey: true });
    });
    expect(fontSizeOf(upper.paneId)).toBe(FONT_SIZE_MAX);

    const lower = renderEditorPane({}, '');
    const lowerHost = hostOf(lower.container);
    useScratchStore.getState().setFontSize(lower.paneId, FONT_SIZE_MIN);
    act(() => {
      fireEvent.wheel(lowerHost, { deltaY: 120, ctrlKey: true });
    });
    expect(fontSizeOf(lower.paneId)).toBe(FONT_SIZE_MIN);
  });

  it('工具箱的 A+ / A− 与滚轮同一条判决器路径', () => {
    // 判据：`ScratchToolbar.tsx:423-432` 的 A−/A+ 调 stepFontSize，与滚轮共用同一夹取口径
    const handle = renderEditorPane({}, '');
    const before = fontSizeOf(handle.paneId);
    const group = openFontGroup(handle.container);

    fireEvent.click(groupButton(group, /^A\+$/));
    expect(fontSizeOf(handle.paneId)).toBe(before + 2);
    handle.sync();

    const group2 = openFontGroup(handle.container);
    fireEvent.click(groupButton(group2, /^A−$/));
    expect(fontSizeOf(handle.paneId)).toBe(before);
  });
});

// ---- AC-35 行号开关 --------------------------------------------------------------

describe('AC-35 行号开关', () => {
  it('关闭行号 → store 关、宿主收到关闭信号', () => {
    // 判据：AC-35 原文「关闭行号 → 宿主收到关闭信号（断言 store 值 + 传给宿主的 props/属性）」
    // 真实实现：`ScratchEditorHost.tsx:571`
    //   `compartments.lineNumbers.reconfigure(pane.showLineNumbers ? api.extensions.lineNumbers() : [])`
    const handle = renderEditorPane();
    // 默认开着行号（`stores/scratch-store.ts` createPane 默认 showLineNumbers: true）
    expect(
      useScratchStore.getState().panes.find((p) => p.id === handle.paneId)?.showLineNumbers,
    ).toBe(true);
    expect(hostOf(handle.container).getAttribute('data-show-line-numbers')).toBe('on');

    const group = openFontGroup(handle.container);
    fireEvent.click(groupButton(group, /行号/));
    handle.sync();

    // ① store 值
    expect(
      useScratchStore.getState().panes.find((p) => p.id === handle.paneId)?.showLineNumbers,
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
    const handle = renderEditorPane({ wordWrap: true }, longLine);
    // 开换行时：宿主报告 on
    expect(hostOf(handle.container).getAttribute('data-word-wrap')).toBe('on');

    const group = openFontGroup(handle.container);
    fireEvent.click(groupButton(group, /自动换行/));
    handle.sync();

    // ① store 值
    expect(useScratchStore.getState().panes.find((p) => p.id === handle.paneId)?.wordWrap).toBe(false);
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

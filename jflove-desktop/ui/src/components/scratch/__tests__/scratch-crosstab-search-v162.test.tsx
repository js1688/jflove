/**
 * v1.6.2 F15 用例：跨标签搜索 / 替换**面板**（AC-92 / AC-93 / AC-94 / AC-95 / AC-96）
 *
 * 判据形状：
 * - 面板只吃"已挂载标签页的正文"（`getScratchEditorSession`）⇒ 未挂载的标签页必须被**明示**、
 *   且结果集里不得出现它的任何命中（AC-94）；
 * - 「全部替换」走后**就地二次确认**（可读计数的观测点）；
 * - 桥替身的每个方法都写成"被调用即失败"的探针 ⇒ AC-96（零新增能力面）变成硬判据。
 */

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../ScratchEditorHost', async (importOriginal) => {
  const real = await importOriginal<Record<string, unknown>>();
  const helper = await import('./scratch-editor-host-mock');
  return helper.scratchEditorHostModuleMock(real);
});

/** 桥替身：**任何**调用都直接失败 —— 跨标签搜索不允许碰桥（AC-96） */
const bridgeTrap = (): never => {
  throw new Error('跨标签搜索不得调用桥（AC-96：零新增能力面）');
};

vi.mock('../../../utils/desktop-bridge', () => ({
  callBridge: vi.fn(async () => bridgeTrap()),
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
  callShell: vi.fn(async () => bridgeTrap()),
}));

vi.mock('../../../services/scratch-service', () => ({
  scratchService: {
    saveDraft: vi.fn(async () => bridgeTrap()),
    readText: vi.fn(async () => bridgeTrap()),
    readTextOnly: vi.fn(async () => bridgeTrap()),
    pickReadOnlyFile: vi.fn(async () => bridgeTrap()),
    writeText: vi.fn(async () => bridgeTrap()),
    reportDirty: vi.fn(() => bridgeTrap()),
  },
  isEditorBridgeReady: () => true,
  toDraftPaneMeta: (tab: unknown) => tab,
}));

import { CrossTabSearchPanel } from '../CrossTabSearchPanel';
import { useScratchStore } from '../../../stores/scratch-store';
import {
  mockSessionOf,
  registerMockSession,
  resetMockEditorSessions,
  resetScratchStore,
  tabTextOf,
} from './scratch-editor-host-mock';

function addTab(title: string, path: string, text: string | null): string {
  const tabId = useScratchStore.getState().createTab({ title, path });
  if (text !== null) registerMockSession(tabId, text);
  return tabId;
}

function renderPanel(tabId: string) {
  return render(createElement(CrossTabSearchPanel, { tabId, onClose: () => undefined }));
}

/** 输入查询并点「查找」 */
function searchFor(container: HTMLElement, query: string): void {
  fireEvent.change(screen.getByLabelText('查找内容'), { target: { value: query } });
  fireEvent.click(screen.getByRole('button', { name: '查找' }));
  void container;
}

/** 结果列表里的命中项（每行一个按钮） */
function hitButtons(): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('li button'));
}

beforeEach(() => {
  resetMockEditorSessions();
  resetScratchStore();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('AC-92 范围可选且默认当前标签页', () => {
  it('「搜索范围」默认 = 当前标签页；切到全部后按标签页分组列出命中（标题 + 行号 + 行文本）', () => {
    const a = addTab('a.txt', 'D:/tmp/a.txt', 'foo\nbar\n');
    addTab('b.txt', 'D:/tmp/b.txt', 'foo baz\n');
    const { container } = renderPanel(a);

    const scope = screen.getByLabelText('搜索范围') as HTMLSelectElement;
    expect(scope.value, '默认范围必须是当前标签页（用户裁决 4）').toBe('tab');

    searchFor(container, 'foo');
    // 默认范围：只搜当前标签页 ⇒ 只应出现 a.txt 的命中
    expect(container.textContent ?? '').toContain('a.txt（1 处）');
    expect(container.textContent ?? '').not.toContain('b.txt（');

    fireEvent.change(scope, { target: { value: 'all' } });
    searchFor(container, 'foo');
    const text = container.textContent ?? '';
    expect(text).toContain('a.txt（1 处）');
    expect(text).toContain('b.txt（1 处）');
    expect(text).toContain('共 2 处命中，涉及 2 个标签页');
    // 行号与行文本都可见
    expect(text).toContain('foo');
    expect(text).toMatch(/1:/);
  });
});

describe('AC-93 点结果 = 切标签页 + 定位选中', () => {
  it('点击命中后：激活标签页换成命中页，且在该页会话上 revealRange 到命中区间', () => {
    const a = addTab('a.txt', 'D:/tmp/a.txt', 'nothing\n');
    const b = addTab('b.txt', 'D:/tmp/b.txt', 'head\nfoo tail\n');
    const { container } = renderPanel(a);

    fireEvent.change(screen.getByLabelText('搜索范围'), { target: { value: 'all' } });
    searchFor(container, 'foo');

    const hits = hitButtons();
    expect(hits.length, '应恰好有 1 条命中').toBe(1);
    act(() => {
      fireEvent.click(hits[0]);
    });

    expect(useScratchStore.getState().activeTabId, '应切到命中所属标签页').toBe(b);
    const revealed = mockSessionOf(b).revealedRange;
    expect(revealed, '必须调用 revealRange 选中命中').not.toBeNull();
    expect(revealed?.from).toBe(5);
    expect(revealed?.to).toBe(8);
  });
});

describe('AC-94 未挂载标签页必须明示、且不参与搜索', () => {
  it('存在未挂载标签页时给出中文说明，且它的内容不出现在结果里', () => {
    const a = addTab('a.txt', 'D:/tmp/a.txt', 'foo\n');
    const c = addTab('c.txt', 'D:/tmp/c.txt', null); // 未挂载（无会话）
    const { container } = renderPanel(a);

    fireEvent.change(screen.getByLabelText('搜索范围'), { target: { value: 'all' } });
    searchFor(container, 'foo');

    expect(container.textContent ?? '').toContain('有 1 个标签页尚未挂载，未参与搜索');
    expect(container.textContent ?? '').not.toContain('c.txt（');
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toContain(c);
  });
});

describe('AC-95 全部替换（就地二次确认 + 计数同源）', () => {
  it('确认条显示将替换的处数与涉及页数；确认后逐页替换、提示实际处数、再搜为 0 命中', () => {
    const a = addTab('a.txt', 'D:/tmp/a.txt', 'foo\nfoo\n');
    const b = addTab('b.txt', 'D:/tmp/b.txt', 'foo\n');
    const untouched = addTab('d.txt', 'D:/tmp/d.txt', 'foo but not in scope\n');
    const { container } = renderPanel(a);

    // 范围限定当前标签页 ⇒ 只改 a
    searchFor(container, 'foo');
    fireEvent.change(screen.getByLabelText('替换为'), { target: { value: 'bar' } });
    fireEvent.click(screen.getByRole('button', { name: '全部替换' }));

    expect(container.textContent ?? '', '必须先给可读的中文确认').toContain('将替换 2 处、涉及 1 个标签页');

    fireEvent.click(screen.getByRole('button', { name: '确认全部替换' }));
    expect(tabTextOf(a)).toBe('bar\nbar\n');
    expect(tabTextOf(b), '范围外的标签页不得被改').toBe('foo\n');
    expect(tabTextOf(untouched), '范围外的标签页不得被改').toBe('foo but not in scope\n');
    expect(container.textContent ?? '').toContain('已替换 2 处');

    // 替换后再搜 = 0 命中（AC-95②）
    searchFor(container, 'foo');
    expect(container.textContent ?? '').toContain('未找到匹配内容');
  });

  it('范围 = 全部时，跨标签一次改完；未挂载的标签页一字不动', () => {
    const a = addTab('a.txt', 'D:/tmp/a.txt', 'foo\n');
    const b = addTab('b.txt', 'D:/tmp/b.txt', 'foo\nfoo\n');
    const { container } = renderPanel(a);

    fireEvent.change(screen.getByLabelText('搜索范围'), { target: { value: 'all' } });
    searchFor(container, 'foo');
    fireEvent.change(screen.getByLabelText('替换为'), { target: { value: 'bar' } });
    fireEvent.click(screen.getByRole('button', { name: '全部替换' }));
    expect(container.textContent ?? '').toContain('将替换 3 处、涉及 2 个标签页');
    fireEvent.click(screen.getByRole('button', { name: '确认全部替换' }));

    expect(tabTextOf(a)).toBe('bar\n');
    expect(tabTextOf(b)).toBe('bar\nbar\n');
    expect(container.textContent ?? '').toContain('已替换 3 处');
  });

  it('「替换当前」只改当前选中的那一条命中（不影响同页其它命中）', () => {
    const a = addTab('a.txt', 'D:/tmp/a.txt', 'foo\nfoo\n');
    const { container } = renderPanel(a);

    searchFor(container, 'foo');
    fireEvent.change(screen.getByLabelText('替换为'), { target: { value: 'bar' } });
    fireEvent.click(screen.getByRole('button', { name: '替换当前' }));

    expect(tabTextOf(a)).toBe('bar\nfoo\n');
    expect(container.textContent ?? '').toContain('已替换 1 处');
  });

  it('搜索之后正文被改动 ⇒ 「替换当前」**不猜位置**：给中文提示并重搜，正文不被改错', () => {
    const a = addTab('a.txt', 'D:/tmp/a.txt', 'foo\nfoo\n');
    const { container } = renderPanel(a);

    searchFor(container, 'foo');
    fireEvent.change(screen.getByLabelText('替换为'), { target: { value: 'bar' } });
    // 期间正文被改动（模拟"用别的途径改了这份文档"，偏移快照随之失效）
    const snapshot = tabTextOf(a);
    mockSessionOf(a).setText(`XX${snapshot}`);
    const mutated = tabTextOf(a);

    fireEvent.click(screen.getByRole('button', { name: '替换当前' }));

    expect(container.textContent ?? '', '必须给中文提示（不静默）').toContain('命中内容已变化');
    expect(tabTextOf(a), '不得把替换文本写到错误区间').toBe(mutated);
  });

  it('空匹配模式被挡下并给中文提示（不允许"在每个字符之间插入内容"）', () => {
    const a = addTab('a.txt', 'D:/tmp/a.txt', 'abc\n');
    const { container } = renderPanel(a);

    fireEvent.click(screen.getByLabelText('正则'));
    searchFor(container, 'a?');
    expect(container.textContent ?? '').toMatch(/不能匹配空字符串/);
    expect(tabTextOf(a), '被挡下时不得改动正文').toBe('abc\n');
  });
});

describe('AC-96 零新增能力面', () => {
  it('跨标签搜索 / 替换全过程零桥调用（桥替身"一调即抛"，用例仍全绿）', async () => {
    const { scratchService } = await import('../../../services/scratch-service');
    const a = addTab('a.txt', 'D:/tmp/a.txt', 'foo\n');
    addTab('b.txt', 'D:/tmp/b.txt', 'foo\n');
    const { container } = renderPanel(a);

    fireEvent.change(screen.getByLabelText('搜索范围'), { target: { value: 'all' } });
    searchFor(container, 'foo');
    fireEvent.change(screen.getByLabelText('替换为'), { target: { value: 'bar' } });
    fireEvent.click(screen.getByRole('button', { name: '全部替换' }));
    fireEvent.click(screen.getByRole('button', { name: '确认全部替换' }));

    for (const [name, fn] of Object.entries(scratchService)) {
      if (typeof fn !== 'function') continue;
      expect((fn as ReturnType<typeof vi.fn>).mock?.calls.length ?? 0, `${name} 被调用了`).toBe(0);
    }
  });
});

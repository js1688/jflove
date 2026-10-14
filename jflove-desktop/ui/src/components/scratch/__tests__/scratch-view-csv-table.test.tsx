/**
 * CSV / TSV 表格视图用例（AC-31 / AC-17）。
 *
 * 判据出处：`文档记录/需求文档/v1.6.0.md`
 *   - AC-31（第 652 行）：「CSV `a,b\n1,2\n` → 表格视图 2 行 2 列，单元格(1,1)=`a`、
 *     (2,2)=`2`；TSV `a\tb\n1\t2\n` 同为 2 行 2 列；表格只读（对单元格发起编辑操作后，
 *     渲染文本与之对应的窗格文本均不变）[负向对照]」
 *   - AC-17（第 633 行）：「打开 `.csv` 与 `.tsv` → 均可切到表格视图，且分隔符被正确识别
 *     （两种文件都得到 2 列）」
 *
 * 行列口径（证据：`components/scratch/views/CsvTableView.tsx:51-55`）
 *   `const header = rows[0] ?? []; const bodyRows = rows.slice(1);`
 *   ——表头占一行。故 `a,b\n1,2\n` 解析出 2 行 × 2 列（解析层口径），DOM 上是
 *   thead 一行 `[a, b]` + tbody **一行** `[1, 2]`；需求里的「单元格(1,1)=a / (2,2)=2」
 *   按解析层行列理解。另每行首列是序号列 `#`（CsvTableView.tsx:92-94 / 108-110），
 *   断言前必须切掉。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent } from '@testing-library/react';
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
import {
  mockSessionOf,
  paneTextOf,
  renderPaneInStore,
  resetMockEditorSessions,
  resetScratchStore,
  setPaneText,
} from './scratch-editor-host-mock';

// ---- 夹具 ------------------------------------------------------------------------

const CSV = 'a,b\n1,2\n';
const TSV = 'a\tb\n1\t2\n';

/**
 * 摆好正文并切到表格视图。
 *
 * 能力门口径（证据：`stores/scratch-store.ts:166 capabilitiesForPane`）：`language === detected`
 * 时能力按 `path` 扩展名解析——表格能力来自 `.csv` / `.tsv`，所以必须给真实扩展名路径，
 * 否则 `setViewMode('table')` 会被能力检查静默忽略。
 */
function renderTable(path: string, text: string) {
  const handle = renderPaneInStore(
    (pane) => createElement(ScratchPane, { pane, active: true, onRequestClose: () => undefined }),
    { path, language: 'text', detected: 'text' },
  );
  setPaneText(handle.paneId, text);
  useScratchStore.getState().setViewMode(handle.paneId, 'table');
  handle.sync();
  return handle;
}

/** 表头单元格（切掉行首的 `#` 序号列） */
function headerCells(container: HTMLElement): string[] {
  const row = container.querySelector('.scratch-pane-aux thead tr');
  if (!row) throw new Error('表格视图没有渲染 thead');
  return Array.from(row.querySelectorAll('th'))
    .slice(1)
    .map((cell) => cell.textContent ?? '');
}

/** 数据行单元格（切掉行首的 `#` 序号列） */
function dataCells(row: Element): string[] {
  return Array.from(row.querySelectorAll('td'))
    .slice(1)
    .map((cell) => cell.textContent ?? '');
}

/** tbody 行集合 */
function tbodyRows(container: HTMLElement): Element[] {
  return Array.from(container.querySelectorAll('.scratch-pane-aux tbody > tr'));
}

/** 视图自身的状态条文本（含「N 行 × M 列」） */
function statusText(container: HTMLElement): string {
  const status = container.querySelector('.scratch-pane-aux span.opacity-50');
  return status?.textContent ?? '';
}

/** 在给定容器内按按钮文案找按钮（避免依赖全局 screen 与多实例冲突） */
function buttonByText(container: HTMLElement, name: string): HTMLElement {
  const found = Array.from(container.querySelectorAll('button')).find(
    (button) => (button.textContent ?? '').trim() === name,
  );
  if (!found) throw new Error(`容器内找不到按钮：${name}`);
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

// ---- 用例 ------------------------------------------------------------------------

describe('AC-31 / AC-17 CSV 表格视图', () => {
  it('CSV `a,b\\n1,2\\n` → 2 行 2 列，单元格(1,1)=a、(2,2)=2', () => {
    // 判据：AC-31 原文「CSV `a,b\n1,2\n` → 表格视图 2 行 2 列，单元格(1,1)=`a`、(2,2)=`2`」
    const { container } = renderTable('D:\\tmp\\a.csv', CSV);
    expect(statusText(container)).toContain('2 行');
    expect(statusText(container)).toContain('2 列');
    expect(headerCells(container)).toEqual(['a', 'b']);
    const rows = tbodyRows(container);
    expect(rows).toHaveLength(1);
    expect(dataCells(rows[0])).toEqual(['1', '2']);
    // 需求按解析层行列给坐标：(1,1) 是表头第一个单元格，(2,2) 是第一行数据第二个单元格
    expect(headerCells(container)[0]).toBe('a');
    expect(dataCells(rows[0])[1]).toBe('2');
  });

  it('TSV `a\\tb\\n1\\t2\\n` → 同样 2 行 2 列，且分隔符识别为制表符（AC-17）', () => {
    // 判据：AC-31 原文「TSV `a\tb\n1\t2\n` 同为 2 行 2 列」+ AC-17「分隔符被正确识别」
    const { container } = renderTable('D:\\tmp\\a.tsv', TSV);
    expect(statusText(container)).toContain('2 行');
    expect(statusText(container)).toContain('2 列');
    expect(headerCells(container)).toEqual(['a', 'b']);
    expect(dataCells(tbodyRows(container)[0])).toEqual(['1', '2']);
    // 分隔符来源文案：自动识别 → 「首行投票」，且识别结果是制表符
    const aux = container.querySelector('.scratch-pane-aux');
    expect(aux?.textContent).toContain('制表符');
    expect(aux?.textContent).toContain('首行投票');
  });

  it('手动切换分隔符只影响本视图渲染，不改原文', () => {
    // 判据：`CsvTableView.tsx:44` 注释「列对齐只发生在渲染副本上，原文一个字符都不动」
    const handle = renderTable('D:\\tmp\\a.tsv', TSV);
    const before = paneTextOf(handle.paneId);
    // 用逗号解释 TSV → 整行就是一列
    fireEvent.click(buttonByText(handle.container, '逗号'));
    expect(headerCells(handle.container)).toEqual(['a\tb']);
    expect(handle.container.querySelector('.scratch-pane-aux')?.textContent).toContain('手动');
    expect(paneTextOf(handle.paneId)).toBe(before);
  });

  it('引号包裹的字段按 RFC4180 解析，不按逗号硬切（负向对照）', () => {
    // 判据：`utils/scratch/toolbox.ts:372-438 parseDelimited` 处理引号字段
    const { container } = renderTable('D:\\tmp\\a.csv', '"a,b",c\n1,2\n');
    expect(headerCells(container)).toEqual(['a,b', 'c']);
    expect(dataCells(tbodyRows(container)[0])).toEqual(['1', '2']);
  });
});

describe('AC-31 表格只读（负向对照）', () => {
  it('表格视图没有可写控件，编辑操作既不改渲染文本也不改窗格文本', () => {
    // 判据：AC-31 负向对照「表格只读（对单元格发起编辑操作后，渲染文本与之对应的窗格文本均不变）」
    const handle = renderTable('D:\\tmp\\a.csv', CSV);
    const aux = handle.container.querySelector('.scratch-pane-aux');
    // 记录"发起编辑操作前"的写回次数，用于差值断言
    const writesBefore = [...mockSessionOf(handle.paneId).writes];
    expect(aux).not.toBeNull();
    // 1) 没有任何输入控件 / 可编辑区域
    expect(aux?.querySelectorAll('input, textarea, select, [contenteditable="true"]')).toHaveLength(0);
    // 2) aux 子树里没有内联事件属性（只读视图不应挂写回钩子）
    for (const node of Array.from(aux?.querySelectorAll('*') ?? [])) {
      for (const attr of Array.from(node.attributes)) {
        expect(attr.name.startsWith('on')).toBe(false);
      }
    }
    // 3) 对单元格发起"编辑"操作 → 渲染文本与会话正文都不变
    //
    // 注意（踩过的坑）：**不要**用 `fireEvent.input(cell, { target: { textContent } })`
    // 或直接改 `cell.textContent` 来"模拟编辑"——那是测试自己改 DOM，改完再断言"没变"
    // 只能证明自己没改回去，与实现无关（React 协调器在 props 未变时也不会重写被测试
    // 直接篡改的文本节点）。这里只发**只读交互事件**，再用"渲染文本 + 会话正文 + 写回
    // 次数"三个信号判断只读性。
    const cell = handle.container.querySelector('.scratch-pane-aux tbody td:last-child');
    expect(cell).not.toBeNull();
    if (cell) {
      fireEvent.focus(cell);
      fireEvent.keyDown(cell, { key: 'Enter' });
      fireEvent.keyDown(cell, { key: 'F2' });
      fireEvent.blur(cell);
    }
    handle.sync();
    // 渲染文本仍来自原文
    const rendered = dataCells(tbodyRows(handle.container)[0]);
    expect(rendered).toEqual(['1', '2']);
    // 窗格文本（会话正文）逐字符不变，且没有**新增**写回
    // （夹具摆正文时用过一次 setText，故以快照差值为准）
    const writesAfter = [...mockSessionOf(handle.paneId).writes];
    expect(paneTextOf(handle.paneId)).toBe(CSV);
    expect(writesAfter).toEqual(writesBefore);
  });
});

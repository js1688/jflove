/**
 * 两段文本对比用例（AC-34）。
 *
 * 判据出处：`文档记录/需求文档/v1.6.0.md` 第 655 行 AC-34：
 *   「左 `a\nb\n` vs 右 `a\nc\n` → 呈现 **1 处差异**（1 行删除 + 1 行新增，或 1 行修改），
 *     相同行 `a` **不**被标记为差异；第二段来源三种接口（剪贴板 / 另一窗格 / 本地文件）
 *     各调一次，断言三条路径都能把右侧文本喂进去 [负向对照]」
 *
 * 替身口径
 *   - `../ScratchEditorHost` 整模块替身（见 `scratch-editor-host-mock.tsx`）：不拖真实 CM6；
 *   - `utils/scratch/load-editor-bundle` 替身：`DiffView` 通过它拿 `MergeView`，替身把配置
 *     记进 `MERGE_CONFIGS` 并渲染出可断言的 DOM（`.cm-merge-view` / `.cm-line`）；
 *   - `scratchService` 替身：本地文件路径只走白名单服务，不碰 fs。
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

// 判据：任务书「不要把真实 CodeMirror 拖进 jsdom」——内核装载入口也替身化
vi.mock('../../../utils/scratch/load-editor-bundle', async () => {
  const helper = await import('./scratch-editor-host-mock');
  return {
    loadEditorBundle: async () => helper.FAKE_EDITOR_API,
    isEditorBundleLoaded: () => true,
  };
});

import { DiffView } from '../views/DiffView';
import { scratchService } from '../../../services/scratch-service';
import {
  MERGE_CONFIGS,
  clearMergeConfigs,
  lastMergeConfig,
  lineDiff,
  renderMockEditorHostPane,
  resetMockEditorSessions,
  resetScratchStore,
  setPaneText,
} from './scratch-editor-host-mock';

// ---- 夹具 ------------------------------------------------------------------------

/** 左段：当前窗格正文（AC-34 原文） */
const LEFT = 'a\nb\n';
/** 右段：第二段正文（AC-34 原文） */
const RIGHT = 'a\nc\n';

/** 等「异步取第二段 + MergeView 创建」落地（创建发生在 `await loadEditorBundle()` 之后） */
async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  resetMockEditorSessions();
  resetScratchStore();
  clearMergeConfigs();
  vi.mocked(scratchService.pickReadOnlyFile).mockResolvedValue(null);
  vi.mocked(scratchService.readTextOnly).mockResolvedValue({
    path: 'D:\\tmp\\right.txt',
    ok: true,
    name: 'right.txt',
    text: RIGHT,
    size: RIGHT.length,
    language: 'text',
    encoding: 'utf-8',
    lineEnding: 'lf',
    readOnly: true,
    warnings: [],
  } as never);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---- 判据自检：差异判决器 --------------------------------------------------------

describe('AC-34 行级差异判决器（自检）', () => {
  it('`a\\nb\\n` vs `a\\nc\\n` → 恰好 1 处差异，且相同行 a 不在变化集合里', () => {
    // 判据：AC-34 原文「呈现 1 处差异（1 行删除 + 1 行新增，或 1 行修改），相同行 a 不被标记」
    const diff = lineDiff(LEFT, RIGHT);
    expect(diff.hunks).toBe(1);
    expect(diff.unchanged).toContain('a');
    expect(diff.changedA).toEqual(['b']);
    expect(diff.changedB).toEqual(['c']);
  });
});

// ---- AC-34 三条来源 --------------------------------------------------------------

describe('AC-34 第二段来源', () => {
  it('另一窗格：选一个已挂载窗格 → 右侧文本进 MergeView，相同行 a 不被标记（负向对照）', async () => {
    // 判据：AC-34 原文「三种接口各调一次，断言三条路径都能把右侧文本喂进去」+「相同行 a 不被标记」
    const left = renderMockEditorHostPane({ title: '左窗格', language: 'text', detected: 'text' });
    const right = renderMockEditorHostPane({ title: '右窗格', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);
    setPaneText(right.paneId, RIGHT);

    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));
    // 「另一窗格」按钮只切来源；真正的取文本发生在 select 的 onChange
    fireEvent.click(screen.getByRole('button', { name: '另一窗格' }));
    const select = await screen.findByRole('combobox');
    // 候选应排除自己、且必须有活会话
    const optionValues = Array.from(select.querySelectorAll('option')).map((item) =>
      item.getAttribute('value'),
    );
    expect(optionValues).toContain(right.paneId);
    expect(optionValues).not.toContain(left.paneId);

    fireEvent.change(select, { target: { value: right.paneId } });
    await flush();
    await flush();

    const config = lastMergeConfig();
    expect(config).not.toBeNull();
    // 第一段 = 当前窗格正文；第二段 = 另一窗格实时正文
    expect(config?.a.doc).toBe(LEFT);
    expect(config?.b.doc).toBe(RIGHT);
    // 两侧只读（设计文档 §6.1.3；`DiffView.tsx:131-132`）
    expect(config?.a.extensions).toHaveLength(1);
    expect(config?.b.extensions).toHaveLength(1);
    // 高亮开关与折叠未变区（DiffView.tsx:134-136）
    expect(config?.highlightChanges).toBe(true);
    expect(config?.collapseUnchanged).toEqual({ margin: 3, minSize: 4 });

    // DOM 侧：相同行 a 必须没有差异标记，变化行 b / c 必须有
    const lines = Array.from(document.querySelectorAll('.cm-merge-view .cm-line')).map((node) => ({
      text: node.textContent ?? '',
      changed: node.classList.contains('cm-diff-changed'),
    }));
    expect(lines.some((item) => item.text === 'a' && item.changed)).toBe(false);
    expect(lines.some((item) => item.text === 'b' && item.changed)).toBe(true);
    expect(lines.some((item) => item.text === 'c' && item.changed)).toBe(true);
    // 恰好一处差异（同一判决器口径）
    expect(lineDiff(LEFT, RIGHT).hunks).toBe(1);
    // 恰好一个合并视图实例
    expect(document.querySelectorAll('.cm-merge-view')).toHaveLength(1);
  });

  it('剪贴板：读到的文本进 MergeView，并显示已载入第二段', async () => {
    // 判据：AC-34 第一条来源「剪贴板」（DiffView.tsx:76-88；用户主动点击才读取）
    const readText = vi.fn(async () => RIGHT);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { readText },
    });
    const left = renderMockEditorHostPane({ title: '左窗格', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);

    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));
    fireEvent.click(screen.getByRole('button', { name: '剪贴板' }));
    await flush();
    await flush();

    expect(readText).toHaveBeenCalledTimes(1);
    expect(lastMergeConfig()?.b.doc).toBe(RIGHT);
    expect(screen.getByText(/已载入第二段/)).not.toBeNull();
  });

  it('剪贴板为空 → 只给提示，不创建对比视图（负向对照）', async () => {
    // 判据：DiffView.tsx:82-85 空剪贴板只 setMessage('剪贴板为空')
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { readText: vi.fn(async () => '') },
    });
    const left = renderMockEditorHostPane({ title: '左窗格', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);

    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));
    fireEvent.click(screen.getByRole('button', { name: '剪贴板' }));
    await flush();

    expect(screen.getByText('剪贴板为空')).not.toBeNull();
    expect(MERGE_CONFIGS).toHaveLength(0);
  });

  it('本地文件：走原生对话框 + 只读读取，右侧文本进 MergeView', async () => {
    // 判据：AC-34 第三条来源「本地文件」；安全约束：渲染层没有文件系统出口，
    //       只经 scratchService 白名单桥（AGENTS.md §9.6）
    vi.mocked(scratchService.pickReadOnlyFile).mockResolvedValue('D:\\tmp\\right.txt');
    const left = renderMockEditorHostPane({ title: '左窗格', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);

    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));
    fireEvent.click(screen.getByRole('button', { name: '本地文件' }));
    await flush();
    await flush();

    // 先选文件，再只读读文本
    expect(vi.mocked(scratchService.pickReadOnlyFile)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(scratchService.readTextOnly)).toHaveBeenCalledWith('D:\\tmp\\right.txt');
    expect(lastMergeConfig()?.b.doc).toBe(RIGHT);
  });

  it('取消选择文件 → 只给提示，不创建对比视图（负向对照）', async () => {
    // 判据：DiffView.tsx:100-104 取消路径只 setMessage('已取消选择文件')
    vi.mocked(scratchService.pickReadOnlyFile).mockResolvedValue(null);
    const left = renderMockEditorHostPane({ title: '左窗格', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);

    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));
    fireEvent.click(screen.getByRole('button', { name: '本地文件' }));
    await flush();

    expect(screen.getByText('已取消选择文件')).not.toBeNull();
    expect(MERGE_CONFIGS).toHaveLength(0);
    expect(screen.getByText(/选一种来源载入第二段文本后开始对比/)).not.toBeNull();
  });
});

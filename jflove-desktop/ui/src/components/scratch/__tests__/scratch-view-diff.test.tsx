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
  it('另一标签页：选一个已挂载标签页 → 右侧文本进 MergeView，相同行 a 不被标记（负向对照）', async () => {
    // 判据：AC-34 原文「三种接口各调一次，断言三条路径都能把右侧文本喂进去」+「相同行 a 不被标记」
    // v1.6.1 文案迁移：来源按钮由「另一窗格」改为「另一标签页」（证据：`views/DiffView.tsx:44`）；
    // 候选口径 = `store.tabs` 里排除自己、且**已有活会话**的标签页（`DiffView.tsx:64-68`）
    const left = renderMockEditorHostPane({ title: '左标签页', language: 'text', detected: 'text' });
    const right = renderMockEditorHostPane({ title: '右标签页', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);
    setPaneText(right.paneId, RIGHT);

    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));
    // 「另一标签页」按钮只切来源；真正的取文本发生在 select 的 onChange
    fireEvent.click(screen.getByRole('button', { name: '另一标签页' }));
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

// ---- 需求约束 C-1（剪贴板来源不可用时的降级）-------------------------------------
//
// 判据出处：需求 `文档记录/需求文档/v1.6.1.md` §0.5.4 C-1 ①②③ + 设计 §15.8.3 的逐行改法。
// 本组用例是**修复前必红**的守门用例：修复前 `DiffView.tsx` 把 `readText()` 的异常交给外层
// catch，`error.message`（英文 `NotAllowedError` 原文）会直接上屏。
//
// 「第二段与对比结果逐字符不变」这条判据**必须**在"已有第二段"的前提下断言，
// 否则会退化成"null === null"的假绿。

/** 读取第二段时被系统拒绝（Chromium 在无权限时的典型异常形态） */
function notAllowedError(): Error {
  const error = new Error(
    "Failed to execute 'readText' on 'Clipboard': Document is not focused.",
  );
  error.name = 'NotAllowedError';
  return error;
}

/** 让剪贴板读取被拒（每次点击都 reject） */
function mockClipboardRejection(): void {
  const readText = vi.fn(async () => {
    throw notAllowedError();
  });
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { readText },
  });
}

/** 让剪贴板读取成功 */
function mockClipboardText(text: string): void {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { readText: vi.fn(async () => text) },
  });
}

/** C-1 的中文提示（与 `DiffView.tsx` 的 `CLIPBOARD_UNAVAILABLE_HINT` 逐字一致） */
const C1_HINT =
  '当前环境不允许读取系统剪贴板，请改用「另一标签页」或「本地文件」作为第二段来源。';

describe('C-1 剪贴板来源不可用时的降级（设计 §15.8.3）', () => {
  it('readText() 被拒（NotAllowedError）⇒ 中文提示上屏、原文不上屏、第二段与对比结果逐字符不变', async () => {
    // ① 先正常载入一段第二段（`RIGHT`）并把对比视图建起来
    mockClipboardText(RIGHT);
    const left = renderMockEditorHostPane({ title: '左窗格', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);
    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));
    fireEvent.click(screen.getByRole('button', { name: '剪贴板' }));
    await flush();
    await flush();

    expect(lastMergeConfig()?.b.doc).toBe(RIGHT);
    expect(screen.getByText(`已载入第二段（${RIGHT.length} 字符）`)).not.toBeNull();
    // 记下"失败前"的对比视图：① 配置记录条数 ② 最后一个配置对象**引用**
    // （`reconfigure` 会 push 新对象 ⇒ 引用变化即"对比结果被重建"，这条比逐字符更强）
    const configsBefore = MERGE_CONFIGS.length;
    const lastConfigBefore = lastMergeConfig();

    // ② 再次点击（用户重试）⇒ 这次系统拒绝
    mockClipboardRejection();
    fireEvent.click(screen.getByRole('button', { name: '剪贴板' }));
    await flush();
    await flush();

    // C-1①：中文提示必须出现，且**不含**任何 `NotAllowedError` 原文
    const hint = screen.getByText(C1_HINT);
    expect(hint.textContent).not.toMatch(/NotAllowed|Clipboard|readText|Document is not focused/);
    // C-1②：第二段与对比结果**逐字符不变** —— 没有 `setSecond('')`、没有重建
    expect(MERGE_CONFIGS).toHaveLength(configsBefore); // 第二段未变 ⇒ `[second, text]` effect 不重跑
    expect(lastMergeConfig()).toBe(lastConfigBefore); // 连配置对象都是同一个 ⇒ 对比结果原封不动
    expect(lastMergeConfig()?.b.doc).toBe(RIGHT);
    expect(lastMergeConfig()?.a.doc).toBe(LEFT);
    expect(screen.getByText(`已载入第二段（${RIGHT.length} 字符）`)).not.toBeNull();
    // 负向判别力：修复前这里会是英文原文
    expect(document.body.textContent).not.toContain('NotAllowedError');
    expect(document.body.textContent).not.toContain('Document is not focused');
  });

  it('readText() 被拒且**尚无**第二段 ⇒ 提示上屏，但不创建对比视图（第二段保持为空）', async () => {
    mockClipboardRejection();
    const left = renderMockEditorHostPane({ title: '左窗格', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);
    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));

    fireEvent.click(screen.getByRole('button', { name: '剪贴板' }));
    await flush();
    await flush();

    expect(screen.getByText(C1_HINT)).not.toBeNull();
    expect(MERGE_CONFIGS).toHaveLength(0);
    expect(screen.queryByText(/已载入第二段/)).toBeNull();
    expect(document.body.textContent).not.toContain('NotAllowedError');
  });

  it('正向对照：`navigator.clipboard` 缺失（readText 不是函数）⇒ 同一份中文提示，语义不变', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    const left = renderMockEditorHostPane({ title: '左窗格', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);
    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));

    fireEvent.click(screen.getByRole('button', { name: '剪贴板' }));
    await flush();

    expect(screen.getByText(C1_HINT)).not.toBeNull();
    expect(MERGE_CONFIGS).toHaveLength(0);
  });

  it('正向对照：readText() 正常 resolve ⇒ 第二段正常填入并建起对比视图', async () => {
    mockClipboardText(RIGHT);
    const left = renderMockEditorHostPane({ title: '左窗格', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);
    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));

    fireEvent.click(screen.getByRole('button', { name: '剪贴板' }));
    await flush();
    await flush();

    expect(lastMergeConfig()?.b.doc).toBe(RIGHT);
    expect(screen.getByText(`已载入第二段（${RIGHT.length} 字符）`)).not.toBeNull();
    expect(screen.queryByText(C1_HINT)).toBeNull();
  });

  it('外层 catch 已收窄：`readTextOnly` 抛错 ⇒ 通用中文文案，不把 error.message 上屏', async () => {
    // 设计 §15.8.3 的"catch 处置"：`error.message` 可能是桥 / 内核实现细节，不上屏
    vi.mocked(scratchService.pickReadOnlyFile).mockResolvedValue('D:\\tmp\\right.txt');
    vi.mocked(scratchService.readTextOnly).mockRejectedValue(
      new Error('bridge internal failure: NotImplemented'),
    );
    const left = renderMockEditorHostPane({ title: '左窗格', language: 'text', detected: 'text' });
    setPaneText(left.paneId, LEFT);
    render(createElement(DiffView, { pane: left.pane(), text: LEFT }));

    fireEvent.click(screen.getByRole('button', { name: '本地文件' }));
    await flush();
    await flush();

    expect(screen.getByText('读取第二段文本失败，请重试或改用其它来源')).not.toBeNull();
    expect(document.body.textContent).not.toContain('bridge internal failure');
    expect(MERGE_CONFIGS).toHaveLength(0);
  });
});

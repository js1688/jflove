/**
 * ScratchEditorHost 替身（测试专用，非应用源码）。
 *
 * 判据出处：`文档记录/需求文档/v1.6.0.md` AC-16/17/23/29/30/31/34/35 与 Phase 6 任务书
 * 「不要把真实 CodeMirror 拖进 jsdom：用 `vi.mock` 替身化 `ScratchEditorHost`，只断言
 * DOM/结构/调用行为；真实 CM6 由别的面负责」。
 *
 * 替身必须保真的**对外契约**（证据：`components/scratch/ScratchEditorHost.tsx`）
 *   1. 根节点：`div.relative.h-full.w-full.overflow-hidden`，内层 `div.h-full.w-full.overflow-hidden`；
 *   2. `getScratchEditorSession(paneId)` 返回会话 | null，会话挂在模块级 Map；
 *   3. `subscribeScratchEditorStats(paneId, listener)` 注册时**立即**回调一次当前统计；
 *   4. wheel 监听：`element.addEventListener('wheel', onWheel, { passive: false })`——
 *      **passive 必须为 false**，否则 Ctrl+滚轮无法 `preventDefault()`（S-8 回归守门的直接依据）；
 *   5. `Ctrl+滚轮` → `stepFontSize(current.fontSize, deltaY < 0 ? +2 : -2)` 并写回 store；
 *   6. 行号 / 自动换行：视 `pane.showLineNumbers` / `pane.wordWrap` 决定是否装载
 *      `api.extensions.lineNumbers()` / `api.EditorView.lineWrapping`。
 *      替身用等价可观察信号表达：`data-show-line-numbers` / `data-word-wrap` 属性 + CSS
 *      `white-space: pre`（关换行 → 长行不折行）与 `overflow-x: auto`（出现横向滚动）。
 *
 * 注意：本文件**不是** `.test.tsx`，故不会被 vitest 的 include 收集为用例。
 */
import { useEffect, useRef, useState } from 'react';
import type { ComponentType, ReactElement } from 'react';
import { cleanup, render } from '@testing-library/react';
import { vi } from 'vitest';
import { useScratchStore } from '../../../stores/scratch-store';
import type { ScratchPane as ScratchPaneModel } from '../../../types/scratch';
import { stepFontSize } from '../../../utils/scratch/toolbox';

/** Ctrl+滚轮的单步字号增量（与 ScratchEditorHost.tsx 的 WHEEL_FONT_STEP 保持一致） */
export const MOCK_WHEEL_FONT_STEP = 2;

/** 替身对外暴露的统计形状（对齐 ScratchEditorStats） */
export interface MockEditorStats {
  line: number;
  col: number;
  selectedChars: number;
  totalChars: number;
}

/** 替身对外暴露的会话形状（子集对齐 ScratchEditorSession，够测试断言即可） */
export interface MockScratchEditorSession {
  paneId: string;
  /** 当前正文（真实实现由 CM6 view.state.doc.toString() 提供） */
  getText: () => string;
  getStats: () => MockEditorStats;
  getCaret: () => number;
  getScrollTop: () => number;
  setText: (next: string) => void;
  focus: () => void;
  openSearchPanel: () => void;
  revealLine: (line: number) => void;
  /**
   * 真实实现里是 JFLoveCmApi（jsdom 中不加载内核，故只留测试需要的探针）。
   * `formatters.sqlDialects` 必须存在：ScratchToolbar.tsx:368 在工具箱 SQL 项里读它
   * （`getScratchEditorSession(pane.id)?.api.formatters.sqlDialects`），缺失会直接抛错。
   */
  api: { name: string; formatters: { sqlDialects: readonly string[] } };
  /** 记录 setText 写入过的正文，供「只读不写回」类负向对照使用 */
  writes: string[];
}

type StatsListener = (stats: MockEditorStats) => void;

/** 模块级会话表：等价于真实实现里的 `SESSIONS` */
const SESSIONS = new Map<string, MockScratchEditorSession>();

/** 模块级统计订阅表：等价于真实实现里的监听器集合 */
const STATS_LISTENERS = new Map<string, Set<StatsListener>>();

/** 广播统计（会话内容变化后调用） */
export function emitStats(paneId: string): void {
  const session = SESSIONS.get(paneId);
  const group = STATS_LISTENERS.get(paneId);
  if (!session || !group) return;
  const stats = session.getStats();
  group.forEach((listener) => listener(stats));
}

/** 读会话（与真实 `getScratchEditorSession` 同名同语义） */
export function getScratchEditorSession(paneId: string): MockScratchEditorSession | null {
  return SESSIONS.get(paneId) ?? null;
}

/** 列出已注册会话的窗格 id（对齐真实 `listScratchEditorPaneIds`） */
export function listScratchEditorPaneIds(): string[] {
  return [...SESSIONS.keys()];
}

/**
 * 注册一个替身会话（测试**主动**摆状态用；真实实现由 host 挂载 CM6 时注册）。
 * 返回注销函数。
 */
export function registerMockSession(paneId: string, initialText = ''): () => void {
  let text = initialText;
  const session: MockScratchEditorSession = {
    paneId,
    api: { name: 'mock-cm-api', formatters: { sqlDialects: ['sql', 'postgres', 'mysql'] } },
    writes: [],
    getText: () => text,
    getStats: () => ({
      line: text.length === 0 ? 1 : text.split('\n').length,
      col: 1,
      selectedChars: 0,
      totalChars: text.length,
    }),
    getCaret: () => 0,
    getScrollTop: () => 0,
    setText: (next: string) => {
      text = next;
      session.writes.push(next);
      emitStats(paneId);
    },
    focus: () => undefined,
    openSearchPanel: () => undefined,
    revealLine: () => undefined,
  };
  SESSIONS.set(paneId, session);
  emitStats(paneId);
  return () => {
    if (SESSIONS.get(paneId) === session) SESSIONS.delete(paneId);
  };
}

/** 订阅某窗格的统计（对齐真实实现：注册时立即回调一次） */
export function subscribeScratchEditorStats(paneId: string, listener: StatsListener): () => void {
  let group = STATS_LISTENERS.get(paneId);
  if (!group) {
    group = new Set<StatsListener>();
    STATS_LISTENERS.set(paneId, group);
  }
  group.add(listener);
  const session = SESSIONS.get(paneId);
  if (session) listener(session.getStats());
  return () => {
    group?.delete(listener);
  };
}

/** 清空所有会话与订阅（每个用例前调用，避免用例互相污染） */
export function resetMockEditorSessions(): void {
  SESSIONS.clear();
  STATS_LISTENERS.clear();
}

/**
 * 状态栏实时量的 Hook 替身（对齐真实 `useScratchEditorStats`）。
 * 口径与真实实现一致：初值取当前会话统计；订阅后由广播驱动（见 ScratchPane.tsx 第 125 行）。
 */
export function useScratchEditorStats(paneId: string): MockEditorStats | null {
  const [stats, setStats] = useState<MockEditorStats | null>(
    () => SESSIONS.get(paneId)?.getStats() ?? null,
  );
  useEffect(() => {
    const session = SESSIONS.get(paneId);
    setStats(session ? session.getStats() : null);
    return subscribeScratchEditorStats(paneId, (next) => setStats(next));
  }, [paneId]);
  return stats;
}

/** 替身组件 props（与 ScratchEditorHostProps 对齐，仅保留测试用得上的部分） */
export interface MockScratchEditorHostProps {
  pane: Pick<ScratchPaneModel, 'id' | 'fontSize' | 'showLineNumbers' | 'wordWrap'>;
  className?: string;
}

/**
 * 读取 window 上 `wheel` 监听注册时的 options（S-8 判据）。
 * 从**最后一次**注册开始往前找，返回 `passive` 值；从未注册过返回 null。
 */
export function findWindowWheelPassive(
  addEventListenerSpy: { mock: { calls: unknown[][] } },
): boolean | null {
  const calls = addEventListenerSpy.mock.calls;
  for (let i = calls.length - 1; i >= 0; i -= 1) {
    const entry = calls[i] as [string, unknown, unknown];
    if (entry[0] !== 'wheel') continue;
    const options = entry[2];
    if (typeof options === 'boolean') return !options;
    if (options && typeof options === 'object' && 'passive' in options) {
      return Boolean((options as { passive?: boolean }).passive);
    }
    return false; // 未传 options ⇒ 浏览器默认 non-passive
  }
  return null;
}

/**
 * ScratchEditorHost 的替身实现。
 *
 * 行为：注册会话 → 在宿主根节点上装 **非 passive** 的 wheel 监听（Ctrl 改字号）→
 * 按 props 暴露行号 / 换行的可观察状态。
 */
export function MockScratchEditorHost({ pane, className }: MockScratchEditorHostProps) {
  const setFontSize = useScratchStore((state) => state.setFontSize);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const paneRef = useRef(pane);
  paneRef.current = pane;

  // 挂载时注册会话（真实实现是在 CM6 装载成功后注册）。
  // 若测试已预置会话，则**保留其正文**：真实路径下正文来自草稿还原 / 读文件，宿主不会清空它。
  useEffect(() => {
    const existing = SESSIONS.get(pane.id);
    const dispose = registerMockSession(pane.id, existing ? existing.getText() : '');
    return dispose;
  }, [pane.id]);

  // wheel：等价于 ScratchEditorHost.tsx 的 wheel effect
  useEffect(() => {
    const element = hostRef.current;
    if (!element) return undefined;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      const current = useScratchStore
        .getState()
        .panes.find((item) => item.id === paneRef.current.id);
      if (!current) return;
      const next = stepFontSize(
        current.fontSize,
        event.deltaY < 0 ? MOCK_WHEEL_FONT_STEP : -MOCK_WHEEL_FONT_STEP,
      );
      if (next !== current.fontSize) setFontSize(current.id, next);
      event.preventDefault();
    };
    // 判据：S-8 —— 必须是非 passive，否则 preventDefault 无效
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      element.removeEventListener('wheel', onWheel);
    };
  }, [pane.id, setFontSize]);

  return (
    <div
      ref={hostRef}
      className={['relative h-full w-full overflow-hidden', className].filter(Boolean).join(' ')}
      data-testid="scratch-editor-host-mock"
      data-pane-id={pane.id}
      data-show-line-numbers={pane.showLineNumbers ? 'on' : 'off'}
      data-word-wrap={pane.wordWrap ? 'on' : 'off'}
    >
      <div
        className="h-full w-full overflow-hidden"
        data-cm-container={pane.id}
        style={{
          whiteSpace: pane.wordWrap ? 'pre-wrap' : 'pre',
          overflowX: 'auto',
          fontSize: `${pane.fontSize}px`,
        }}
      />
    </div>
  );
}

/** 供类型层面复用的替身组件类型 */
export type MockHostComponent = ComponentType<MockScratchEditorHostProps>;

// ── 夹具区：把「摆状态 + 渲染 + 同步」的重复动作收敛到一处 ─────────────────────────

/** 清空窗格 store（每个用例前调用；状态字段对齐 ScratchState） */
export function resetScratchStore(): void {
  useScratchStore.setState({ panes: [], activePaneId: null });
}

/** 从 store 读窗格快照 */
export function paneFromStore(paneId: string): ScratchPaneModel {
  const found = useScratchStore.getState().panes.find((pane) => pane.id === paneId);
  if (!found) throw new Error(`store 中找不到窗格 ${paneId}`);
  return found;
}

/** 改替身会话的正文（真实实现里由 CM6 的 doc 持有） */
export function setPaneText(paneId: string, text: string): void {
  const session = SESSIONS.get(paneId);
  if (!session) throw new Error(`没有已注册的会话：${paneId}`);
  session.setText(text);
}

/** 读替身会话当前正文 */
export function paneTextOf(paneId: string): string {
  return SESSIONS.get(paneId)?.getText() ?? '';
}

/** 读替身会话（不存在即报错，便于定位夹具问题） */
export function mockSessionOf(paneId: string): MockScratchEditorSession {
  const session = SESSIONS.get(paneId);
  if (!session) throw new Error(`没有已注册的会话：${paneId}`);
  return session;
}

/** wheel 监听注册记录（S-8 判据） */
export interface ListenerCall {
  type: string;
  target: unknown;
  options: unknown;
}

/** 模块级监听注册表，供 `spyWindowListeners` 收集 */
export const LISTENER_CALLS: ListenerCall[] = [];

/** 清空监听注册表 */
export function clearListenerCalls(): void {
  LISTENER_CALLS.length = 0;
}

/**
 * 侦察 window 上的监听注册。
 *
 * 判据：S-8 —— `ScratchEditorHost.tsx` 的 wheel effect 必须用
 * `addEventListener('wheel', handler, { passive: false })` 注册（jsdom 的
 * addEventListener 默认不会把 options 留存下来，故此处用 spy 捕获第三参数）。
 */
export function spyWindowListeners() {
  const spy = vi.spyOn(window, 'addEventListener');
  spy.mockImplementation(((type: string, _listener: unknown, options?: unknown) => {
    LISTENER_CALLS.push({ type, target: window, options });
  }) as typeof window.addEventListener);
  return spy;
}

/** `renderPaneInStore` 的返回句柄 */
export interface PaneRenderHandle {
  paneId: string;
  container: HTMLElement;
  /** 读最新 store 快照 */
  pane: () => ScratchPaneModel;
  /** 用最新 store 状态重渲染（`ScratchPane` 的 `pane` 是 props，不会自己跟随 store） */
  sync: () => void;
}

/**
 * 在 store 里建窗格并渲染给定组件。
 *
 * `ScratchPane` 的 `pane` 来自 props，所以改 store 之后必须 `sync()` 重渲染，
 * 断言以 DOM 为准（这正是"视图模式真的被渲染"这类判据需要的口径）。
 */
export function renderPaneInStore(
  factory: (pane: ScratchPaneModel) => ReactElement,
  init?: Partial<ScratchPaneModel>,
): PaneRenderHandle {
  const createInit = { ...(init ?? {}) } as Parameters<
    ReturnType<typeof useScratchStore.getState>['createPane']
  >[0];
  const paneId = useScratchStore.getState().createPane(createInit);
  const view = render(factory(paneFromStore(paneId)));
  return {
    paneId,
    container: view.container,
    pane: () => paneFromStore(paneId),
    sync: () => view.rerender(factory(paneFromStore(paneId))),
  };
}

/**
 * 只渲染替身宿主，让某个窗格拿到「活会话」（含内容存取器）。
 *
 * 用途：AC-34「另一窗格」来源与 `scratch-store` 的内容存取器都要求会话真实存在，
 * 但又不需要整窗格外壳（避免把工具栏/视图区一起拖进来）。
 */
export function renderMockEditorHostPane(init?: Partial<ScratchPaneModel>): {
  paneId: string;
  container: HTMLElement;
  pane: () => ScratchPaneModel;
} {
  const createInit = { ...(init ?? {}) } as Parameters<
    ReturnType<typeof useScratchStore.getState>['createPane']
  >[0];
  const paneId = useScratchStore.getState().createPane(createInit);
  const view = render(
    <MockScratchEditorHost
      pane={{
        id: paneId,
        fontSize: paneFromStore(paneId).fontSize,
        showLineNumbers: paneFromStore(paneId).showLineNumbers,
        wordWrap: paneFromStore(paneId).wordWrap,
      }}
    />,
  );
  return { paneId, container: view.container, pane: () => paneFromStore(paneId) };
}

/**
 * 把整模块替身交给 `vi.mock('../ScratchEditorHost')` 的工厂使用。
 *
 * 入参是 `importOriginal()` 的结果：整模块替换（而非只替 `ScratchEditorHost`）可以避免
 * 真实实现顺带把 CM6 装载路径拖进 jsdom。
 */
export function scratchEditorHostModuleMock(real: Record<string, unknown>): Record<string, unknown> {
  void real;
  return {
    ScratchEditorHost: MockScratchEditorHost,
    getScratchEditorSession,
    listScratchEditorPaneIds,
    subscribeScratchEditorStats,
    useScratchEditorStats,
    computeEditorStats: () => ({ line: 1, col: 1, selectedChars: 0, totalChars: 0 }),
    getLoadedEditorApi: () => null,
  };
}

/** 在窗格正文里输入（真实路径会写回会话；替身只改会话正文并发统计） */
export function typeIntoPane(paneId: string, next: string): void {
  setPaneText(paneId, next);
}

export { cleanup };

// ── 编辑器内核替身（`loadEditorBundle` 的返回值） ─────────────────────────────────────
//
// 判据：任务书「不要把真实 CodeMirror 拖进 jsdom」＋ AC-34「呈现 1 处差异，相同行 a
// 不被标记为差异」。真实 `MergeView` 由 `@codemirror/merge` 提供（`public/vendor/editor.bundle.js`），
// jsdom 里不加载；替身负责两件事：① 记下传进来的合并配置（判据：两侧只读 + 两段正文）；
// ② 把行级差异渲染成可断言的 DOM（`.cm-merge-view` / `.cm-merge-a` / `.cm-merge-b`）。

/** 行级差异判决结果 */
export interface LineDiffResult {
  /** 两段都有的行（按出现顺序，含重复） */
  unchanged: string[];
  /** 只在左段出现的行 */
  changedA: string[];
  /** 只在右段出现的行 */
  changedB: string[];
  /** 差异块数（一段是删除、一段是新增时算同一处） */
  hunks: number;
}

/** 按 `\n` 切行（保留尾随空元素，与真实按行切分的口径一致） */
function toLines(text: string): string[] {
  return text.split('\n');
}

/**
 * 行级 LCS 差异判决器（纯函数，等价于 MergeView 的行级 diff 判决）。
 *
 * 用途：既可单独自检 AC-34 的「1 处差异」，也供内核替身给 DOM 打差异标记。
 */
export function lineDiff(a: string, b: string): LineDiffResult {
  const left = toLines(a);
  const right = toLines(b);
  const n = left.length;
  const m = right.length;
  // lcs[i][j] = left[i..] 与 right[j..] 的最长公共子序列长度
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lcs[i][j] =
        left[i] === right[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const unchanged: string[] = [];
  const changedA: string[] = [];
  const changedB: string[] = [];
  let hunks = 0;
  let inHunk = false;
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (left[i] === right[j]) {
      unchanged.push(left[i]);
      inHunk = false;
      i += 1;
      j += 1;
      continue;
    }
    if (!inHunk) {
      hunks += 1;
      inHunk = true;
    }
    if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      changedA.push(left[i]);
      i += 1;
    } else {
      changedB.push(right[j]);
      j += 1;
    }
  }
  while (i < n) {
    if (!inHunk) {
      hunks += 1;
      inHunk = true;
    }
    changedA.push(left[i]);
    i += 1;
  }
  while (j < m) {
    if (!inHunk) {
      hunks += 1;
      inHunk = true;
    }
    changedB.push(right[j]);
    j += 1;
  }
  return { unchanged, changedA, changedB, hunks };
}

/** 合并视图配置（`DiffView.tsx:130-137` 的形状，只声明测试要用的字段） */
export interface MergeViewConfig {
  a: { doc: string; extensions?: unknown[] };
  b: { doc: string; extensions?: unknown[] };
  parent?: unknown;
  highlightChanges?: boolean;
  gutter?: boolean;
  collapseUnchanged?: { margin: number; minSize: number };
}

/** 内核替身创建/重配过的配置记录（`lastMergeConfig()` 取最后一条） */
export const MERGE_CONFIGS: MergeViewConfig[] = [];

/** 清空配置记录 */
export function clearMergeConfigs(): void {
  MERGE_CONFIGS.length = 0;
}

/** 取最后一次传给 MergeView 的配置（没有则 null） */
export function lastMergeConfig(): MergeViewConfig | null {
  return MERGE_CONFIGS.length > 0 ? MERGE_CONFIGS[MERGE_CONFIGS.length - 1] : null;
}

/** 把一侧的文本行渲染为带差异标记的行节点（React 版，供纯 DOM 场景复用） */
export function renderMergeLines(side: 'a' | 'b', text: string, changed: string[]): ReactElement[] {
  return toLines(text).map((line, index) => {
    const isChanged = line !== '' && changed.includes(line);
    return (
      <span
        key={`${side}-${index}`}
        className={isChanged ? 'cm-line cm-diff-changed' : 'cm-line'}
      >
        {line}
      </span>
    );
  });
}

/** 内核替身：一个极简的 MergeView（记录配置 + 渲染两侧只读正文） */
class FakeMergeView {
  private config: MergeViewConfig;

  private readonly host: HTMLElement;

  constructor(config: MergeViewConfig) {
    this.config = config;
    MERGE_CONFIGS.push(config);
    this.host = config.parent as HTMLElement;
    this.render();
  }

  /** 与真实 MergeView.reconfigure 同名同语义（第二段变化时复用实例） */
  reconfigure(config: MergeViewConfig): void {
    this.config = config;
    MERGE_CONFIGS.push(config);
    this.render();
  }

  /** 与真实 MergeView.destroy 同名（卸载清理，避免泄漏） */
  destroy(): void {
    if (this.host) this.host.textContent = '';
  }

  private render(): void {
    if (typeof document === 'undefined' || !this.host) return;
    const diff = lineDiff(this.config.a.doc, this.config.b.doc);
    const root = document.createElement('div');
    root.className = 'cm-merge-view';
    const left = document.createElement('div');
    left.className = 'cm-merge-a';
    const right = document.createElement('div');
    right.className = 'cm-merge-b';
    for (const line of toLines(this.config.a.doc)) {
      const node = document.createElement('span');
      node.className = line !== '' && diff.changedA.includes(line) ? 'cm-line cm-diff-changed' : 'cm-line';
      node.textContent = line;
      left.appendChild(node);
    }
    for (const line of toLines(this.config.b.doc)) {
      const node = document.createElement('span');
      node.className = line !== '' && diff.changedB.includes(line) ? 'cm-line cm-diff-changed' : 'cm-line';
      node.textContent = line;
      right.appendChild(node);
    }
    root.appendChild(left);
    root.appendChild(right);
    this.host.textContent = '';
    this.host.appendChild(root);
  }
}

void renderMergeLines;

/** 内核替身的形状（`DiffView` 只用到这三处） */
export interface FakeEditorApi {
  version: string;
  MergeView: new (config: MergeViewConfig) => {
    reconfigure: (next: MergeViewConfig) => void;
    destroy: () => void;
  };
  EditorView: { lineWrapping: unknown[] };
  EditorState: { readOnly: { of: (value: boolean) => unknown } };
}

/**
 * 编辑器内核替身（`utils/scratch/load-editor-bundle.ts` 的 `loadEditorBundle()` 返回值）。
 *
 * 只实现 `DiffView` 用到的三处：`MergeView`、`EditorState.readOnly.of`、`EditorView.lineWrapping`。
 */
export const FAKE_EDITOR_API: FakeEditorApi = {
  version: '6.43.14',
  MergeView: FakeMergeView,
  EditorView: Object.assign(function EditorView() {}, { lineWrapping: [] as unknown[] }),
  EditorState: {
    readOnly: { of: (_value: boolean) => ({ readOnly: true }) },
  },
};

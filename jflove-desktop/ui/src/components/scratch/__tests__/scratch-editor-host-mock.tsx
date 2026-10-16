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
 * ## v1.6.1 补能力：可选「真实内核」模式（AC-66② 的直接判据）
 *
 * 默认仍是纯替身（不建任何真实 `EditorView`，正文/光标只在会话表里）—— **既有用例的行为一字不变**。
 * 只有显式调用 `enableMockRealEditorViews()`（且用例已用 `src/test/cm-bundle.ts` 的
 * `loadCmBundle()` 注入离线包）后，替身宿主才会：
 *   ① 在宿主容器（`data-cm-container`）里建**真实** `EditorView`（离线包真实现，不是替身）；
 *   ② 把它登记进 `VIEWS`，会话的 `view` getter / `getText` / `getCaret` / `getScrollTop`
 *      因此读到**真实 CM6 状态**（`state.doc` / `state.selection.main.head` / `scrollDOM.scrollTop`）；
 *   ③ 用**真实** `registerScratchContentAccessor` 注册正文读取器（与 `ScratchEditorHost.tsx:638-642`
 *      同一条通路：草稿与自动保存读正文/光标/滚动就靠它），并在宿主**卸载**时注销 + `view.destroy()`。
 *
 * 生命周期口径严格对齐真实宿主（`ScratchEditorHost.tsx:526-545` 的清理 vs `:548-654` 的创建）：
 * **建实例**由「可见」驱动且effect **没有清理函数**，**拆实例**只发生在卸载 ⇒ 可见性翻转（切标签）
 * 不拆实例。这正是 AC-66②「光标与滚动位置与离开前相同」的机制，也是本模式的判别力来源：
 * 一旦非激活面板改为**卸载**，视图与读取器都会重建，值与引用两头都会变。
 *
 * `getCaret()` 的返回类型由 `number` 更正为 `TabCaret`（与真实会话 `ScratchEditorSession.getCaret()`
 * 同口径）—— 原实现恒返 `0`，与真实形状不符；全仓库只有"定义处"两处（真实会话与替身），**无任何调用点**，
 * 故该更正不影响既有用例。
 *
 * 注意：本文件**不是** `.test.tsx`，故不会被 vitest 的 include 收集为用例。
 */
import { useEffect, useRef, useState } from 'react';
import type { ComponentType, ReactElement } from 'react';
import { cleanup, render } from '@testing-library/react';
import { vi } from 'vitest';
import { registerScratchContentAccessor, useScratchStore } from '../../../stores/scratch-store';
import type { ScratchContentAccessor } from '../../../stores/scratch-store';
import type { EditorView } from '../../../types/codemirror-bundle';
import type { ScratchTab, TabCaret } from '../../../types/scratch';
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
  /**
   * **真实** CM6 视图（仅「真实内核」模式下存在，否则 `null`）。
   *
   * 与真实 `ScratchEditorSession.view` 同语义：AC-66② 的「同一个 `EditorView` 实例」判据、
   * 以及 `state.selection.main.head` / `scrollDOM.scrollTop` 两个真实值都从这里取。
   */
  readonly view: EditorView | null;
  /** 当前正文（真实实现由 CM6 view.state.doc.toString() 提供） */
  getText: () => string;
  getStats: () => MockEditorStats;
  /** 1 基光标（与真实 `ScratchEditorSession.getCaret()` 同口径：`{line, col}`） */
  getCaret: () => TabCaret;
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

/** 真实 CM6 视图表（**仅「真实内核」模式**下有条目）；生命周期跟随宿主挂载 / 卸载 */
const VIEWS = new Map<string, EditorView>();

/** 替身**实际交给**真实注册表的内容读取器（引用相等判据；见 `mockContentAccessorOf`） */
const ACCESSORS = new Map<string, ScratchContentAccessor>();

/** 「真实内核」模式开关（默认关闭 ⇒ 既有用例行为不变；`resetMockEditorSessions()` 会关掉） */
let realEditorViewsEnabled = false;

/**
 * 打开替身宿主的**真实内核**模式（AC-66② 用）。
 *
 * 前置：用例必须先用 `src/test/cm-bundle.ts` 的 `loadCmBundle()` 注入离线包，否则宿主建不出视图，
 * 相关断言会以「没有真实 CM6 视图」的明确错误失败，而不是含糊地报 `null`。
 */
export function enableMockRealEditorViews(value = true): void {
  realEditorViewsEnabled = value;
}

/**
 * 取替身**实际注册进真实注册表**的正文读取器（未注册返回 `null`）。
 *
 * 用途：AC-66② 的「读回调本身没有被重建」判据 —— 返回值就是传给
 * `registerScratchContentAccessor(tabId, accessor)` 的那**同一个对象**，
 * 因此 `toBe` 相等即"读取器未被重建"；面板一旦卸载，读取器会被注销（条目删除），
 * 重新挂载会注册**新**对象 ⇒ 该断言必然变红。
 */
export function mockContentAccessorOf(paneId: string): ScratchContentAccessor | null {
  return ACCESSORS.get(paneId) ?? null;
}

/** 从**真实** CM6 视图读 1 基光标（与 `ScratchEditorHost.caretOfView` 同口径：列按码点） */
function caretOfRealView(view: EditorView): TabCaret {
  const head = view.state.selection.main.head;
  const line = view.state.doc.lineAt(head);
  const col = [...line.text.slice(0, Math.max(0, head - line.from))].length + 1;
  return { line: line.number, col };
}

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
 *
 * 真实内核模式（`enableMockRealEditorViews()`）下，会话的 `view` getter 会指向宿主建的真实
 * `EditorView`，正文 / 光标 / 滚动一律以它为准；未启用时与旧版替身行为一致 —— 唯一差异是
 * `getCaret()` 返回真实形状 `{line: 1, col: 1}`（旧版恒返数值 `0`，见文件头）。
 */
export function registerMockSession(paneId: string, initialText = ''): () => void {
  let text = initialText;
  /** 当前正文：真实内核模式下以视图的 doc 为准（与真实会话的 `view.state.doc.toString()` 同源） */
  const currentText = (): string => VIEWS.get(paneId)?.state.doc.toString() ?? text;
  const session: MockScratchEditorSession = {
    paneId,
    api: { name: 'mock-cm-api', formatters: { sqlDialects: ['sql', 'postgres', 'mysql'] } },
    writes: [],
    get view(): EditorView | null {
      return VIEWS.get(paneId) ?? null;
    },
    getText: () => currentText(),
    getStats: () => {
      const body = currentText();
      return {
        line: body.length === 0 ? 1 : body.split('\n').length,
        col: 1,
        selectedChars: 0,
        totalChars: body.length,
      };
    },
    getCaret: () => {
      const view = VIEWS.get(paneId);
      return view ? caretOfRealView(view) : { line: 1, col: 1 };
    },
    getScrollTop: () => VIEWS.get(paneId)?.scrollDOM.scrollTop ?? 0,
    setText: (next: string) => {
      const view = VIEWS.get(paneId);
      if (view) {
        // 与真实 `setText` 同一口径：整篇替换走 dispatch（视图是唯一真相）
        view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: next } });
      } else {
        text = next;
      }
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

/**
 * 清空所有会话 / 订阅 / 真实视图 / 读取器记录（每个用例前调用，避免用例互相污染）。
 *
 * 同时把「真实内核」模式**关回默认值**：该模式是每用例显式打开的能力开关，
 * 不能让上一个用例的开关影响下一个（同一个文件内多个用例共用模块级状态）。
 */
export function resetMockEditorSessions(): void {
  SESSIONS.clear();
  STATS_LISTENERS.clear();
  VIEWS.clear();
  ACCESSORS.clear();
  realEditorViewsEnabled = false;
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

/**
 * 替身组件 props（与 `ScratchEditorHostProps` 对齐，仅保留测试用得上的部分）。
 *
 * v1.6.1：字段名仍是 `pane`（`MarkdownSplitView` 直挂用法未改），值类型由 `ScratchPane`
 * 换成 `ScratchTab`；新增 `visible`（非激活面板带 `hidden` 时宿主被告知不可见）。
 */
export interface MockScratchEditorHostProps {
  pane: Pick<ScratchTab, 'id' | 'fontSize' | 'showLineNumbers' | 'wordWrap'>;
  visible?: boolean;
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
 * 按 props 暴露行号 / 换行的可观察状态；
 * 「真实内核」模式打开时，另在容器里建**真实** `EditorView` 并注册真实正文读取器（见文件头）。
 *
 * 生命周期口径与真实宿主严格同构：**建实例**由「可见」驱动、该 effect **无清理函数**；
 * **拆实例 / 注销读取器**只发生在宿主卸载 ⇒ 可见性翻转（切标签）不拆实例（AC-66②）。
 */
export function MockScratchEditorHost({
  pane,
  visible = true,
  className,
}: MockScratchEditorHostProps) {
  const setFontSize = useScratchStore((state) => state.setFontSize);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const releaseAccessorRef = useRef<(() => void) | null>(null);
  const paneRef = useRef(pane);
  paneRef.current = pane;

  // 挂载时注册会话（真实实现是在 CM6 装载成功后注册）。
  // 若测试已预置会话，则**保留其正文**：真实路径下正文来自草稿还原 / 读文件，宿主不会清空它。
  // 清理序对齐真实宿主 `ScratchEditorHost.tsx:531-544`：注销读取器 → 注销会话 → destroy 真实视图。
  useEffect(() => {
    const paneId = pane.id;
    const existing = SESSIONS.get(paneId);
    const dispose = registerMockSession(paneId, existing ? existing.getText() : '');
    return () => {
      if (releaseAccessorRef.current) {
        releaseAccessorRef.current();
        releaseAccessorRef.current = null;
      }
      ACCESSORS.delete(paneId);
      VIEWS.delete(paneId);
      const view = viewRef.current;
      viewRef.current = null;
      dispose();
      if (view) view.destroy();
    };
  }, [pane.id]);

  // 真实内核模式：**可见**时把会话挂到真实 `EditorView` 上（AC-66② 的判据来源）。
  // ⚠ 本 effect **刻意没有清理函数** —— 可见性翻折不拆实例，与真实宿主一致。
  useEffect(() => {
    if (!realEditorViewsEnabled || !visible || viewRef.current) return;
    const api = typeof window === 'undefined' ? null : window.__JFLOVE_CM__;
    const container = containerRef.current;
    if (!api || typeof api.EditorView !== 'function' || !container) return;
    const session = SESSIONS.get(pane.id);
    const view = new api.EditorView({
      state: api.EditorState.create({ doc: session ? session.getText() : '' }),
      parent: container,
    });
    viewRef.current = view;
    VIEWS.set(pane.id, view);
    // 与真实宿主同一条通路（草稿 / 自动保存读正文、光标、滚动都经它）
    const accessor: ScratchContentAccessor = () => ({
      text: view.state.doc.toString(),
      caret: caretOfRealView(view),
      scrollTop: view.scrollDOM.scrollTop,
    });
    releaseAccessorRef.current = registerScratchContentAccessor(pane.id, accessor);
    ACCESSORS.set(pane.id, accessor);
  }, [pane.id, visible]);

  // wheel：等价于 ScratchEditorHost.tsx 的 wheel effect
  useEffect(() => {
    const element = hostRef.current;
    if (!element) return undefined;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      const current = useScratchStore
        .getState()
        .tabs.find((item) => item.id === paneRef.current.id);
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
      data-host-pane-id={pane.id}
      data-host-visible={visible ? 'on' : 'off'}
      data-show-line-numbers={pane.showLineNumbers ? 'on' : 'off'}
      data-word-wrap={pane.wordWrap ? 'on' : 'off'}
    >
      <div
        ref={containerRef}
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

/** 清空标签 store（每个用例前调用；状态字段对齐 v1.6.1 的 ScratchState） */
export function resetScratchStore(): void {
  useScratchStore.setState({ tabs: [], activeTabId: null });
}

/** 从 store 读标签快照 */
export function tabFromStore(tabId: string): ScratchTab {
  const found = useScratchStore.getState().tabs.find((tab) => tab.id === tabId);
  if (!found) throw new Error(`store 中找不到标签 ${tabId}`);
  return found;
}

/** 改替身会话的正文（真实实现里由 CM6 的 doc 持有） */
export function setTabText(tabId: string, text: string): void {
  const session = SESSIONS.get(tabId);
  if (!session) throw new Error(`没有已注册的会话：${tabId}`);
  session.setText(text);
}

/**
 * 旧名别名（v1.6.0 术语）。
 *
 * 保留原因：`scratch-view-diff.test.tsx` 尚未迁移（**不在本次改动白名单内**），它按旧名导入；
 * 语义与 `setTabText` 完全相同。
 */
export const setPaneText = setTabText;

/** 读替身会话当前正文 */
export function tabTextOf(tabId: string): string {
  return SESSIONS.get(tabId)?.getText() ?? '';
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

/** `renderTabInStore` 的返回句柄 */
export interface TabRenderHandle {
  tabId: string;
  container: HTMLElement;
  /** 读最新 store 快照 */
  tab: () => ScratchTab;
  /** 用最新 store 状态重渲染（对显式传 props 的组件是必要的；订阅 store 的组件可省） */
  sync: () => void;
}

/**
 * 在 store 里建标签并渲染给定组件。
 *
 * v1.6.1：`EditorPanel` 只吃 `{ tabId, active }` 并从 store 自取标签状态，因此
 * `sync()` 主要是给「组件仍靠 props 拿状态」的场景兜底；断言一律以 DOM 为准。
 */
export function renderTabInStore(
  factory: (tab: ScratchTab) => ReactElement,
  init?: Partial<ScratchTab>,
): TabRenderHandle {
  const createInit = { ...(init ?? {}) } as Parameters<
    ReturnType<typeof useScratchStore.getState>['createTab']
  >[0];
  const tabId = useScratchStore.getState().createTab(createInit);
  const view = render(factory(tabFromStore(tabId)));
  return {
    tabId,
    container: view.container,
    tab: () => tabFromStore(tabId),
    sync: () => view.rerender(factory(tabFromStore(tabId))),
  };
}

/**
 * 只渲染替身宿主，让某个标签拿到「活会话」（含内容存取器）。
 *
 * 用途：AC-34「另一标签页」来源与 `scratch-store` 的内容存取器都要求会话真实存在，
 * 但又不需要整块编辑面板外壳（避免把工具栏/视图区一起拖进来）。
 *
 * 名字与返回字段保留 v1.6.0 的 `pane` 口径：消费方 `scratch-view-diff.test.tsx` 尚未迁移
 * （不在本次改动白名单内）。
 */
export function renderMockEditorHostPane(init?: Partial<ScratchTab>): {
  paneId: string;
  container: HTMLElement;
  pane: () => ScratchTab;
} {
  const createInit = { ...(init ?? {}) } as Parameters<
    ReturnType<typeof useScratchStore.getState>['createTab']
  >[0];
  const tabId = useScratchStore.getState().createTab(createInit);
  const view = render(
    <MockScratchEditorHost
      pane={{
        id: tabId,
        fontSize: tabFromStore(tabId).fontSize,
        showLineNumbers: tabFromStore(tabId).showLineNumbers,
        wordWrap: tabFromStore(tabId).wordWrap,
      }}
    />,
  );
  return { paneId: tabId, container: view.container, pane: () => tabFromStore(tabId) };
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

/** 在标签正文里输入（真实路径会写回会话；替身只改会话正文并发统计） */
export function typeIntoPane(paneId: string, next: string): void {
  setTabText(paneId, next);
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

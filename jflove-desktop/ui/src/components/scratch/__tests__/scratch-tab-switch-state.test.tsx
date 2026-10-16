/**
 * 标签切换**状态保持**守门用例 —— Phase 6（v1.6.1）
 *
 * 判据出处：
 *   - **AC-66 ①②③**（需求 `文档记录/需求文档/v1.6.1.md:471`）：开 3 个标签页 A / B / C；把 B 切到
 *     「预览」视图、把 A 滚动到中部并把光标放到某行 → 切到 C 再切回 A / B → ① 各标签页内容逐一
 *     不变；② A 的光标位置与滚动位置与离开前**相同**；③ B 仍处于「预览」视图；
 *   - **设计 §4.8 懒挂载与内存**（`文档记录/设计文档/v1.6.1.md:604-610`）：非激活标签用 `hidden`
 *     属性隐藏、**不卸载** CM6 实例 ⇒「内容 / 光标 / 滚动位置保持不变」零成本成立；
 *     从隐藏恢复可见时调用 `nudgeEditorLayout()`（`requestAnimationFrame` 派发一次合成 `resize`）
 *     让 CM6 重新测量，否则出现「空白 / 行号错位」；
 *   - **DOM 契约**（`:441-449`）：`data-editor-panel="<tabId>"` 恒有、`data-editor-panel-active="true"`
 *     **仅激活时**出现（AC-66 的观测点）；
 *   - **R-13**（`:883`）：保持挂载会让 N 个激活过的标签各持一个 CM6 实例 ⇒ 本文件守住"**不得**为省内存
 *     而卸载非激活面板"这条本版口径（退路 LRU 未启用）。
 *
 * ## 本文件如何把"不卸载"变成可判定断言
 *
 * jsdom 里没有真实 CM6 内核时，正文与光标活在**宿主替身**的会话表里（`scratch-editor-host-mock.tsx`）。
 * 替身与真实实现同语义：**挂载时注册会话、卸载时注销**。因此：
 *   ① 「非激活面板不卸载」可由 **DOM 节点同一性**（切走切回后 `===`）直接判定；
 *   ② 「会话未重建」可由 `getScratchEditorSession(tabId)` 的**对象同一性**判定 —— 面板一旦被卸载，
 *      替身的清理函数会把会话从表里删掉 ⇒ 该断言必然失败（这正是回归的判别力来源）；
 *   ③ 「切回后重新测量」由 window 上的合成 `resize` 事件计数判定（`requestAnimationFrame` 入队后
 *      由用例显式冲刷，避免真实帧时序带来的抖动）。
 *
 * ## AC-66② 的直接断言层（v1.6.1 Phase 6 收尾）
 *
 * 上面 ② 只证明"同一会话对象 ⇒ 状态不可能丢"，属**间接论证**。本文件末尾补了 `AC-66②` 一组用例，
 * 把「光标位置与滚动位置与离开前相同」变成**读真实取值**的断言：
 *   - `src/test/cm-bundle.ts::loadCmBundle()` 注入**真离线包**，替身宿主（打开
 *     `enableMockRealEditorViews()` 后）在面板容器里建**真实 `EditorView`**（jsdom 实测可用）；
 *   - 于是可以直接断言「离开前 / 切回后」的 `state.selection.main.head` 与 `scrollDOM.scrollTop`
 *     逐值相等，并且断言**是同一个 `EditorView` 实例**、面板里是**同一个 `.cm-editor` 节点**；
 *   - 还能走**真实正文读取器通路**（`registerScratchContentAccessor` → `store.flushPane` →
 *     `scratchService.draftSave` 的 `pane.caret` / `pane.scroll_top`）复核，并断言
 *     **读回调本身没有被重建**（引用相等）。
 * 判别力：一旦非激活面板改为**卸载**（而不是 `hidden`），视图、读取器、重建后的取值三者同时变化，
 * 该组用例必红（红/绿两段证据见 Phase 6 收尾回执）。
 *
 * ## 分层声明与安全
 *
 * `ScratchEditorHost` 与 `utils/desktop-bridge` 均为替身（默认不装载真实编辑器内核、不发真实网络请求；
 * 仅 `AC-66②` 一组显式注入**离线包**内核，仍不发任何网络请求）；
 * 夹具中**没有** token / session_key（AGENTS.md §9）。
 */
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../config/routes', () => ({
  router: { navigate: vi.fn() },
}));

vi.mock('../../../utils/desktop-bridge', () => ({
  callBridge: vi.fn(async () => ({ ok: true })),
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
  callShell: vi.fn(async () => ({ ok: true })),
}));

vi.mock('../ScratchEditorHost', async (importOriginal) => {
  const real = await importOriginal<Record<string, unknown>>();
  const helper = await import('./scratch-editor-host-mock');
  return helper.scratchEditorHostModuleMock(real);
});

import { loadCmBundle } from '../../../test/cm-bundle';
import type { EditorView } from '../../../types/codemirror-bundle';
import type { DraftPaneMeta, TabCaret } from '../../../types/scratch';
import { ScratchWorkspace } from '../ScratchWorkspace';
import { scratchService } from '../../../services/scratch-service';
import { useAuthStore } from '../../../stores/auth-store';
import { useScratchStore } from '../../../stores/scratch-store';
import { setAuthSnapshot } from '../../../utils/session';
import {
  enableMockRealEditorViews,
  getScratchEditorSession,
  mockContentAccessorOf,
  registerMockSession,
  resetMockEditorSessions,
  tabTextOf,
} from './scratch-editor-host-mock';

/** 取面板外壳（DOM 契约锚点） */
function panelEl(container: HTMLElement, tabId: string): HTMLElement | null {
  return container.querySelector<HTMLElement>(`[data-editor-panel="${tabId}"]`);
}

// ── AC-66② 直接断言层用到的夹具（真离线包内核 + 真实正文读取器通路） ──────────────────────

/** AC-66② 用例里"离开前"设定的滚动位置（非 0，且不是新建视图会得到的默认值） */
const SCROLL_BEFORE = 240;

/**
 * 取某个标签的**真实** CM6 视图。
 *
 * 没有就抛错（而不是返回 null 让断言静默退化）：用例忘记 `loadCmBundle()` /
 * `enableMockRealEditorViews()` 时，应当以明确的错误失败。
 */
function realViewOf(tabId: string): EditorView {
  const view = getScratchEditorSession(tabId)?.view ?? null;
  if (!view) {
    throw new Error(
      `标签 ${tabId} 没有真实 CM6 视图：用例需先 loadCmBundle() + enableMockRealEditorViews()`,
    );
  }
  return view;
}

/** `editor.draftSave` 的实参记录（读取器通路的观测点，由 `spyDraftSave()` 填充） */
const draftSaveCalls: Array<{ meta: DraftPaneMeta; text: string }> = [];

/** 捕获 `editor.draftSave` 的实参（不真正写盘） */
function spyDraftSave(): void {
  draftSaveCalls.length = 0;
  vi.spyOn(scratchService, 'draftSave').mockImplementation(async (meta, text) => {
    draftSaveCalls.push({ meta, text });
    return { ok: true, at: 0 };
  });
}

/**
 * 经**真实**的正文读取器通路读一次快照（AC-66② 的"次选"判据，与优先级 1 一起用）。
 *
 * 通路：`markDirty` → 防抖排队 → `flushPendingSaves()` → `scratch-store.ts:301` 的
 * `contentAccessors.get(tabId)`（= 替身宿主经**真实** `registerScratchContentAccessor` 注册的回调，
 * 与自动保存 / 草稿写盘**完全同一条**）→ `accessor()` → `scratchService.draftSave(meta, text)`，
 * 其中 `meta.caret` / `meta.scroll_top` 正是读取器给出的实时光标与滚动位置。
 */
async function readByContentAccessor(
  tabId: string,
): Promise<{ caret: TabCaret; scrollTop: number; text: string }> {
  draftSaveCalls.length = 0;
  act(() => {
    useScratchStore.getState().markDirty(tabId, true);
  });
  await act(async () => {
    await useScratchStore.getState().flushPendingSaves();
  });
  const last = draftSaveCalls.at(-1);
  if (!last) {
    throw new Error(`读取器通路没有触发 draftSave：标签 ${tabId} 没有注册正文读取器`);
  }
  return { caret: last.meta.caret, scrollTop: last.meta.scroll_top, text: last.text };
}

// ── 合成 resize 的确定化观测（避免依赖真实帧时序） ────────────────────────────────────────
//
// `EditorPanel.tsx:57-60` 的 `nudgeEditorLayout()` 把"派发 resize"排进 `requestAnimationFrame`。
// 这里把 rAF 换成**可显式冲刷的队列**：回调只在用例调用 `flushRaf()` 时执行，
// 因此"重新激活 ⇒ 重新测量"这条判据是**确定性的**，不会因 CI 负载抖动。
const rafQueue: FrameRequestCallback[] = [];
let realRequestAnimationFrame: typeof window.requestAnimationFrame;

/** 冲刷 rAF 队列（`nudgeEditorLayout` 的合成 resize 在其中派发） */
function flushRaf(): void {
  const pending = rafQueue.splice(0);
  act(() => {
    pending.forEach((callback) => callback(0));
  });
}

let resizeEvents = 0;
function onResize(): void {
  resizeEvents += 1;
}

/**
 * 建 3 个标签 A / B / C（返回 id 列表，顺序即标签条顺序）。
 *
 * 口径（证据：`config/scratch-types.ts:508-515` + `stores/scratch-store.ts:148-152`）：视图模式受
 * **能力表**校验，而 `language: 'text'` 不在 `EXTENSIONS` 表内 ⇒ 能力并集回落到
 * `['monospace','wordWrap','largeFileGuard']`，`preview` / `split` 都会被 `setViewMode` 拒绝。
 * AC-66 需要「B 切到预览视图」，因此三个标签都按 **markdown**（`language === detected` ⇒ 按
 * `path` 扩展名解析能力）摆，与该判据的原文场景一致。
 */
function createABC(): { a: string; b: string; c: string; ids: string[] } {
  const ids: string[] = [];
  act(() => {
    const make = (title: string, path: string) =>
      useScratchStore.getState().createTab({
        title,
        path,
        language: 'markdown',
        detected: 'markdown',
      });
    ids.push(make('A', 'a.md'));
    ids.push(make('B', 'b.md'));
    ids.push(make('C', 'c.md'));
  });
  return { a: ids[0], b: ids[1], c: ids[2], ids };
}

beforeEach(() => {
  setAuthSnapshot({ expiresAt: null });
  useAuthStore.setState({ isLoggedIn: true });
  resetMockEditorSessions();
  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  vi.spyOn(scratchService, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
  vi.spyOn(scratchService, 'draftDiscard').mockResolvedValue({ ok: true });

  rafQueue.length = 0;
  realRequestAnimationFrame = window.requestAnimationFrame;
  window.requestAnimationFrame = ((callback: FrameRequestCallback) => {
    rafQueue.push(callback);
    return rafQueue.length;
  }) as typeof window.requestAnimationFrame;
  resizeEvents = 0;
  window.addEventListener('resize', onResize);
});

afterEach(() => {
  window.removeEventListener('resize', onResize);
  window.requestAnimationFrame = realRequestAnimationFrame;
  rafQueue.length = 0;
  cleanup();
  useAuthStore.setState({ isLoggedIn: false });
  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  vi.restoreAllMocks();
});

// ── 用例 ──────────────────────────────────────────────────────────────────────────────

describe('AC-66① 非激活面板 hidden 但不卸载', () => {
  it('AC-66①：3 个标签各有一个面板常驻 DOM；非激活项带 hidden + 内联 display:none，且切走切回后节点同一', () => {
    const { a, b, c } = createABC();
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });
    const view = render(createElement(ScratchWorkspace));

    // ① 三个面板**同时**在 DOM 里（渲染集合 = mounted || 激活项；三个都由 createTab 置 mounted:true）
    expect(view.container.querySelectorAll('[data-editor-panel]')).toHaveLength(3);
    const nodeA = panelEl(view.container, a);
    const nodeB = panelEl(view.container, b);
    const nodeC = panelEl(view.container, c);
    expect(nodeA).not.toBeNull();
    expect(nodeB).not.toBeNull();
    expect(nodeC).not.toBeNull();

    // ② 激活态 DOM 契约：只有激活项带 data-editor-panel-active，且只有它不 hidden
    expect(nodeA?.hasAttribute('hidden')).toBe(false);
    expect(nodeA?.getAttribute('data-editor-panel-active')).toBe('true');
    expect(nodeA?.style.display).toBe('');
    for (const node of [nodeB, nodeC]) {
      expect(node?.hasAttribute('hidden')).toBe(true);
      // 内联 display:none 压住 UA 的 [hidden] 规则与工具类 flex，避免"隐藏失效"
      expect((node as HTMLElement).style.display).toBe('none');
      expect(node?.getAttribute('data-editor-panel-active')).toBeNull();
    }

    // ③ 切到 C 再切回 A / B：节点**同一性**不变 ⇒ React 未卸载重建（卸载会 destroy 掉 CM6 实例）
    act(() => {
      useScratchStore.getState().setActiveTab(c);
    });
    expect(panelEl(view.container, a)).toBe(nodeA);
    expect(panelEl(view.container, b)).toBe(nodeB);
    expect(panelEl(view.container, c)).toBe(nodeC);
    expect(panelEl(view.container, c)?.hasAttribute('hidden')).toBe(false);
    expect(panelEl(view.container, a)?.hasAttribute('hidden')).toBe(true);

    act(() => {
      useScratchStore.getState().setActiveTab(b);
      useScratchStore.getState().setActiveTab(a);
    });
    expect(panelEl(view.container, a)).toBe(nodeA);
    expect(panelEl(view.container, b)).toBe(nodeB);
    expect(panelEl(view.container, c)).toBe(nodeC);
    // 切回后 A 重新可见
    expect(panelEl(view.container, a)?.hasAttribute('hidden')).toBe(false);
    expect(panelEl(view.container, a)?.style.display).toBe('');
  });

  it('AC-66①：切走再切回 ⇒ 同一会话对象（`getScratchEditorSession` 未被重建）、正文逐字不变', () => {
    // 中文正文：编码问题只在非 ASCII 内容上暴露（testing 技能纪律第 4 条）
    const tabText = '标签 B 的正文：中文 + 符号 -> 完成';
    const { a, b } = createABC();
    // 预置会话（真实路径下正文来自草稿还原 / 读文件；替身宿主挂载时会**保留**既有正文）
    registerMockSession(b, tabText);
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });
    render(createElement(ScratchWorkspace));

    const sessionBefore = getScratchEditorSession(b);
    expect(sessionBefore).not.toBeNull();
    expect(tabTextOf(b)).toBe(tabText);

    // 切走（B 变非激活）
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });
    // 若面板被卸载 ⇒ 替身宿主的清理函数注销会话 ⇒ 这里会是 null（判别力来源）
    expect(getScratchEditorSession(b)).toBe(sessionBefore);

    // 切回
    act(() => {
      useScratchStore.getState().setActiveTab(b);
    });
    expect(getScratchEditorSession(b)).toBe(sessionBefore);
    expect(tabTextOf(b)).toBe(tabText);
  });

  it('AC-66③：非激活标签的视图模式与面板状态保持（B 停在「预览」，切走切回不变）', () => {
    const { a, b } = createABC();
    // B 切到预览视图（能力表按语言解析；text 语言允许 edit / preview / split）
    act(() => {
      useScratchStore.getState().setViewMode(b, 'preview');
      useScratchStore.getState().setActiveTab(a);
    });
    const view = render(createElement(ScratchWorkspace));

    const bodyOf = (tabId: string) =>
      panelEl(view.container, tabId)?.querySelector('.scratch-pane-body')?.getAttribute('data-pane-body');

    expect(bodyOf(b)).toBe('preview');
    expect(useScratchStore.getState().tabs.find((tab) => tab.id === b)?.viewMode).toBe('preview');

    act(() => {
      useScratchStore.getState().setActiveTab(b);
    });
    expect(bodyOf(b)).toBe('preview');

    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });
    // 切走期间 B 的面板仍在 DOM 且仍是预览态（未被重置回 edit）
    expect(bodyOf(b)).toBe('preview');
    expect(useScratchStore.getState().tabs.find((tab) => tab.id === b)?.viewMode).toBe('preview');
  });
});

describe('§4.8 从隐藏恢复可见 ⇒ 合成 resize 让 CM6 重新测量', () => {
  it('§4.8：首次挂载激活项、以及**每次重新激活**都必须派发合成 resize（漏掉重新激活那次必红）', () => {
    const { a, b, c } = createABC();
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });
    render(createElement(ScratchWorkspace));
    flushRaf();

    // 首次挂载（激活项 A）：`EditorPanel.tsx:129-132` 的 effect 在 active 为真时就会叫醒 CM6
    expect(resizeEvents).toBeGreaterThan(0);
    const afterMount = resizeEvents;

    // 激活 C
    act(() => {
      useScratchStore.getState().setActiveTab(c);
    });
    flushRaf();
    expect(resizeEvents).toBeGreaterThan(afterMount);
    const afterC = resizeEvents;

    // 重新激活 A（从 hidden 恢复可见）：必须**再**叫醒一次，否则出现「空白 / 行号错位」
    // （把该 effect 改成"仅挂载时执行" ⇒ 这里不会增加 ⇒ 本断言红）
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });
    flushRaf();
    expect(resizeEvents).toBeGreaterThan(afterC);
    const afterBackToA = resizeEvents;

    // 反向半边：切到 B（此前从未激活过）同样要叫醒
    act(() => {
      useScratchStore.getState().setActiveTab(b);
    });
    flushRaf();
    expect(resizeEvents).toBeGreaterThan(afterBackToA);

    // rAF 队列确实被"派发 resize"的回调消费过（而不是 resize 来自别处）
    expect(rafQueue.length).toBe(0);
  });

  it('§4.8：同一面板内视图模式变化（尺寸翻转）也要重新测量，且工具栏「编辑」按钮走同一条路径', () => {
    const { a } = createABC();
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });
    render(createElement(ScratchWorkspace));
    flushRaf();
    const baseline = resizeEvents;

    // split（宿主与预览并排）→ preview（宿主隐藏）⇒ 尺寸翻转
    act(() => {
      useScratchStore.getState().setViewMode(a, 'split');
    });
    flushRaf();
    expect(resizeEvents).toBeGreaterThan(baseline);
    const afterSplit = resizeEvents;

    act(() => {
      useScratchStore.getState().setViewMode(a, 'preview');
    });
    flushRaf();
    expect(resizeEvents).toBeGreaterThan(afterSplit);
    const afterPreview = resizeEvents;

    // 工具条上的视图切换按钮也走同一条路径（真实用户入口）
    const editButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent === '编辑',
    );
    expect(editButton).not.toBeUndefined();
    act(() => {
      fireEvent.click(editButton as HTMLButtonElement);
    });
    flushRaf();
    expect(resizeEvents).toBeGreaterThan(afterPreview);
    expect(useScratchStore.getState().tabs.find((tab) => tab.id === a)?.viewMode).toBe('edit');
  });
});

describe('AC-66② A 的光标位置与滚动位置与离开前相同（真实 CM6 取值，直接断言）', () => {
  it('AC-66②：切到 C 再切回 A 后仍是**同一个**真实 EditorView（面板里也是同一个 .cm-editor 节点），state.selection.main.head 与 scrollDOM.scrollTop 逐值不变', () => {
    // 真离线包（`src/test/cm-bundle.ts` 的既有通路）+ 打开替身的"真实内核"能力
    loadCmBundle();
    enableMockRealEditorViews();

    const docText = '第一行\n第二行\n第三行\n第四行\n第五行';
    const { a, b, c } = createABC();
    // 预置正文（真实路径下正文来自草稿还原 / 读文件）
    registerMockSession(a, docText);
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });
    const view = render(createElement(ScratchWorkspace));

    // 夹具自证：A 的面板里确实有**真 CM6** 渲染出来的 `.cm-editor`，而不只是替身空壳
    const viewOfA = realViewOf(a);
    const cmEditorBefore = panelEl(view.container, a)?.querySelector('.cm-editor') ?? null;
    expect(cmEditorBefore).not.toBeNull();
    expect(viewOfA.dom).toBe(cmEditorBefore);

    // 「离开前」的用户动作：把光标放到第 3 行行首、把内容滚动到中部（真实 CM6 状态）
    const headBefore = viewOfA.state.doc.line(3).from;
    act(() => {
      viewOfA.dispatch({ selection: { anchor: headBefore } });
      viewOfA.scrollDOM.scrollTop = SCROLL_BEFORE;
    });
    expect(viewOfA.state.selection.main.head).toBe(headBefore);
    expect(viewOfA.scrollDOM.scrollTop).toBe(SCROLL_BEFORE);
    // 判别力自证：这两个值都不是"视图被重建后会拿到的值" —— head 不为 0，而滚动位置 240
    // 也不等于 store 里缓存的 `scrollTop`（0：重建只能从 store 的旧值恢复 ⇒ 会退回 0）
    expect(headBefore).toBeGreaterThan(0);
    expect(useScratchStore.getState().tabs.find((tab) => tab.id === a)?.scrollTop).toBe(0);

    // AC-66 原文的切换序列：切到 C → 切到 B → 切回 A
    act(() => {
      useScratchStore.getState().setActiveTab(c);
    });
    // 切走期间（A 非激活、面板 hidden）实例仍在会话表里
    expect(getScratchEditorSession(a)?.view).toBe(viewOfA);
    act(() => {
      useScratchStore.getState().setActiveTab(b);
    });
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });
    // 重新激活会派发合成 resize（`nudgeEditorLayout`）⇒ 真 CM6 重新测量；测量不该动光标与滚动
    flushRaf();

    const viewAfter = realViewOf(a);
    // ① 是**同一个** `EditorView` 实例（而不是"值恰好相等"）
    expect(viewAfter).toBe(viewOfA);
    // ② 光标与滚动位置逐值相等
    expect(viewAfter.state.selection.main.head).toBe(headBefore);
    expect(viewAfter.scrollDOM.scrollTop).toBe(SCROLL_BEFORE);
    // ③ 面板 DOM 里也还是同一个 CM6 根节点（视图没被重建的 DOM 侧旁证）
    expect(panelEl(view.container, a)?.querySelector('.cm-editor')).toBe(cmEditorBefore);
  });

  it('AC-66②：切走再切回后，正文读取器（自动保存 / 草稿写盘读正文的同一条通路）读到的 caret 与 scrollTop 逐值不变，且读取器本身未被重建', async () => {
    loadCmBundle();
    enableMockRealEditorViews();
    spyDraftSave();

    const docText = '正文第一行\n第二行\n第三行\n第四行';
    const { a, b, c } = createABC();
    registerMockSession(a, docText);
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });
    render(createElement(ScratchWorkspace));

    const viewOfA = realViewOf(a);
    const headBefore = viewOfA.state.doc.line(3).from;
    act(() => {
      viewOfA.dispatch({ selection: { anchor: headBefore } });
      viewOfA.scrollDOM.scrollTop = SCROLL_BEFORE;
    });

    const accessorBefore = mockContentAccessorOf(a);
    expect(accessorBefore).not.toBeNull();

    // ① 离开前经真实读取器通路读一次：光标在第 3 行第 1 列、滚动位置是刚设的值、正文是活正文
    const before = await readByContentAccessor(a);
    expect(before.caret).toEqual({ line: 3, col: 1 });
    expect(before.scrollTop).toBe(SCROLL_BEFORE);
    expect(before.text).toBe(docText);

    // 反证"读的是活视图而不是 store 的陈旧缓存"：
    // store 里缓存的 caret 仍是 createTab 的默认值（只有草稿还原路径才会更新它）
    expect(useScratchStore.getState().tabs.find((tab) => tab.id === a)?.caret).toEqual({
      line: 1,
      col: 1,
    });

    // AC-66 原文的切换序列：切到 C → 切到 B → 切回 A
    act(() => {
      useScratchStore.getState().setActiveTab(c);
    });
    act(() => {
      useScratchStore.getState().setActiveTab(b);
    });
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });

    // ② 读取器**未被重建**：仍是注册进真实注册表的同一个回调对象（面板卸载重挂会换成新对象 ⇒ 必红）
    expect(mockContentAccessorOf(a)).toBe(accessorBefore);

    // ③ 切回后经同一条通路再读一次：逐值不变
    const after = await readByContentAccessor(a);
    expect(after.caret).toEqual(before.caret);
    expect(after.scrollTop).toBe(before.scrollTop);
    expect(after.text).toBe(before.text);
  });
});

/**
 * **AC-77 正文保活**守门用例（本批 C1 的主判据）—— 需求 AC-77 / 设计 v1.6.1 §15.5
 *
 * ## 判据出处
 *
 * - **AC-77①**（需求 `文档记录/需求文档/v1.6.1.md` §9.2）：打开文件 `f` → 在其中**再输入** `EDIT`
 *   （未保存）→ **切走 → 切回** → 该标签正文 `doc.toString()` **逐字符等于**切走前的值；
 *   **不得**出现空正文，**不得**回退成磁盘旧内容；
 * - **AC-77⑥**：正文来源可以是保活快照或草稿兜底，但**不得**用磁盘内容覆盖活会话中更新的未保存改动；
 * - **AC-5（就地改写）②**：判据 = `doc.toString()` 往返前后逐一相等；
 * - **设计 §15.5.2 ~ §15.5.6**：快照时机（callback ref + effect cleanup 双保险）、
 *   快照内容（正文 + caret + scrollTop，**undo 明确不保**）、读取优先级
 *   （快照 > 草稿 > 空）、hydrate 门新判据
 *   `shouldRead = tabs.length === 0 || pickInvalidTabs(tabs).length > 0`。
 *
 * ## 为什么这个文件用**真** `ScratchEditorHost` + **真**离线包（而不像同目录别的用例那样替身化宿主）
 *
 * F5 的根因链是「路由卸载 ⇒ 工作区卸载 ⇒ 宿主卸载 ⇒ `view.destroy()` ⇒ 正文失去唯一持有者」。
 * 正文是否真的活下来，只有**真的 CM6 实例**能回答：替身宿主的正文只活在自己的 Map 里，
 * 它不会走 `view.destroy()`、也不经过 `snapshotInitialText` ⇒ 用替身断言等于断言替身自己。
 * 因此本文件：
 *   ① `src/test/cm-bundle.ts::loadCmBundle()` 注入**真离线包**（既有通路，不起 `<script>`）；
 *   ② 不 `vi.mock('../ScratchEditorHost')` ⇒ `ScratchWorkspace → EditorPanel → ScratchEditorHost`
 *      与生产**完全同一条**装配链；
 *   ③ 用 `getScratchEditorSession(tabId).view` 取真 `EditorView`，用 `dispatch` 真改文档。
 *
 * ## 为什么整文件用「模块重载 + 动态 import」
 *
 * `ScratchWorkspace.tsx:60` 的 `hydratedThisSession` 是**模块级**状态（DS-34 故意如此），
 * `initialTexts` / `SESSIONS` 同样是模块级旁路表。AC-77⑥ 的用例需要「本会话尚未还原过」
 * 这一前提（= `hydratedThisSession` 为假，才会走读盘兜底）⇒ 必须每个用例拿到**全新一代**模块，
 * 否则用例只能靠声明顺序互相迁就（与 `scratch-workspace-gate.test.tsx` 同一手法）。
 *
 * ## 安全（AGENTS.md §9）
 *
 * 夹具里**没有** token / session_key / 真实服务端地址；`draftList` / `draftDiscard` / `draftSave`
 * 全部是替身，**不发任何真实网络请求**；断言只比正文，不打印正文到日志。
 */
import { act, render } from '@testing-library/react';
import { createElement } from 'react';
import type { ComponentType } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JFLoveCmApi } from '../../../types/codemirror-bundle';
import type { DraftPaneMeta } from '../../../types/scratch';
import { installJsdomRangeMeasureShim } from './scratch-real-editor-dom-shim';

// ── 依赖替身（vi.mock 会被 hoist；工厂内不引用外部变量，避免 TDZ） ──────────────────────

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

/** 一代应用模块（同代内 store / 宿主 / 工作区 / 服务互相引用一致） */
interface RealEditorApp {
  store: typeof import('../../../stores/scratch-store');
  host: typeof import('../ScratchEditorHost');
  Workspace: ComponentType;
  service: typeof import('../../../services/scratch-service').scratchService;
  authStore: typeof import('../../../stores/auth-store').useAuthStore;
  session: typeof import('../../../utils/session');
  /** 真离线包 API（已注入 `window.__JFLOVE_CM__`） */
  api: JFLoveCmApi;
}

/**
 * 取一代全新的应用模块 + 注入真离线包。
 *
 * 顺序：先 store / service / auth（被 spy 的对象），再 `loadCmBundle()`（挂 `window.__JFLOVE_CM__`），
 * 最后组件（宿主首建实例时 `loadEditorBundle()` 会立即拿到已挂载的 API，不进网络、不加 `<script>`）。
 */
async function loadRealEditorApp(): Promise<RealEditorApp> {
  vi.resetModules();
  const store = await import('../../../stores/scratch-store');
  const auth = await import('../../../stores/auth-store');
  const session = await import('../../../utils/session');
  const serviceModule = await import('../../../services/scratch-service');
  const cm = await import('../../../test/cm-bundle');
  const api = cm.loadCmBundle();
  const host = await import('../ScratchEditorHost');
  const workspace = await import('../ScratchWorkspace');
  return {
    store,
    host,
    Workspace: workspace.ScratchWorkspace as ComponentType,
    service: serviceModule.scratchService,
    authStore: auth.useAuthStore,
    session,
    api,
  };
}

/** 已登录且会话未过期（AC-63 准入判据为真；设计 §4.1 `canUseEditor`） */
function login(app: RealEditorApp): void {
  app.session.setAuthSnapshot({ expiresAt: null });
  app.authStore.setState({ isLoggedIn: true });
}

/**
 * 冲干「渲染 → effect → 异步建 CM6 实例」链。
 *
 * 宿主建实例是 `void (async () => { const api = await loadEditorBundle(); … })()`：
 * `loadEditorBundle()` 在离线包已挂载时返回 `Promise.resolve(api)` ⇒ 冲若干轮微任务即可。
 * 轮数取 12（远大于链上 await 个数）以覆盖"compartment 重配 effect 再跑一轮"的时序。
 */
async function flushEffects(rounds = 12): Promise<void> {
  await act(async () => {
    for (let i = 0; i < rounds; i += 1) await Promise.resolve();
  });
}

/** 造一条草稿索引条目（字段逐字对齐 `types/scratch.ts` 的 `DraftPaneMeta`） */
function draftEntry(id: string, overrides: Partial<DraftPaneMeta> = {}): DraftPaneMeta {
  return {
    id,
    title: `草稿-${id}`,
    path: null,
    language: 'text',
    encoding: 'utf-8',
    lineEnd: 'lf',
    read_only: false,
    dirty: true,
    caret: { line: 1, col: 1 },
    scroll_top: 0,
    font_size: 14,
    show_line_numbers: true,
    word_wrap: false,
    ...overrides,
  };
}

/** 取某个标签的**真实** CM6 视图（没有就明确报错，别让断言静默退化） */
function realViewOf(app: RealEditorApp, tabId: string) {
  const session = app.host.getScratchEditorSession(tabId);
  if (!session) {
    throw new Error(`标签 ${tabId} 没有真实 CM6 会话：用例需先 loadCmBundle() 并冲干微任务`);
  }
  return session.view;
}

/** AC-77③ 用例里"离开前"设定的滚动位置（非 0，且不是新建视图会得到的默认值） */
const SCROLL_BEFORE = 240;

/** 在标签里"再输入"一段文本（真事务，会触发 `updateListener` ⇒ `markDirty`） */
function typeInto(view: ReturnType<typeof realViewOf>, inserted: string): string {
  act(() => {
    view.dispatch({ changes: { from: view.state.doc.length, insert: inserted } });
  });
  return view.state.doc.toString();
}

/** 打开一个"标签页 + 初始正文"，对应生产路径的 `openOrFocus`（正文经 `initialTexts` 交付一次） */
function openTab(app: RealEditorApp, base: string, init?: Partial<{ id: string; title: string }>): string {
  let tabId = '';
  act(() => {
    tabId = app.store.useScratchStore.getState().createTab({
      id: init?.id,
      title: init?.title ?? 'f.txt',
      language: 'text',
      detected: 'text',
    });
  });
  app.store.putInitialText(tabId, base);
  return tabId;
}

/** 每用例前置：真定时器（个别用例自己切 fake）+ jsdom 测量 API 补丁（见该文件头） */
beforeEach(() => {
  installJsdomRangeMeasureShim();
  vi.useRealTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('AC-77① 切走 → 切回：正文逐字符不变（真 CM6 实例）', () => {
  it('AC-77①/AC-5②：编辑未保存 → 卸载工作区（路由往返）→ 重挂 ⇒ doc.toString() 逐字符等于改后内容', async () => {
    const app = await loadRealEditorApp();
    login(app);
    vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
    vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });

    const tabId = openTab(app, '第一行\n第二行\n');
    const first = render(createElement(app.Workspace));
    await flushEffects();

    // 前置自证：真的是**真** CM6 实例，且初始正文来自 initialTexts（不是空 doc）
    const viewBefore = realViewOf(app, tabId);
    expect(typeof viewBefore.dispatch).toBe('function');
    expect(viewBefore.state.doc.toString()).toBe('第一行\n第二行\n');

    // 用户动作：再输入 EDIT（未保存、且在 1500 ms 自动暂存防抖窗口内）
    const edited = typeInto(viewBefore, 'EDIT');
    expect(edited).toBe('第一行\n第二行\nEDIT');
    expect(app.store.useScratchStore.getState().tabs.find((tab) => tab.id === tabId)?.dirty).toBe(true);

    // 切到左侧菜单（设置）→ 工作区卸载；再切回「文本编辑器」→ 工作区重挂
    first.unmount();
    const second = render(createElement(app.Workspace));
    await flushEffects();

    // 判据：正文逐字符等于切走前的值（修复前这里拿到 '' ⇒ 必红）
    expect(realViewOf(app, tabId).state.doc.toString()).toBe(edited);
    // 且确实是**新**实例（旧实例已随卸载销毁 —— 证明"正文活下来"靠的是快照，不是"实例没走"）
    expect(realViewOf(app, tabId)).not.toBe(viewBefore);
    expect(second.container.querySelector('[data-scratch-workspace="true"]')).not.toBeNull();
  });

  it('AC-77①：防抖窗口内切走（未冲刷待写的自动保存）⇒ 正文仍在，且来源是快照而不是草稿', async () => {
    const app = await loadRealEditorApp();
    login(app);
    // 冻结防抖计时器：确保"1500 ms 自动暂存"确实**没有**发生（否则判据可能被草稿兜底蒙对）
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
    vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });
    const draftSave = vi.spyOn(app.service, 'draftSave').mockResolvedValue({ ok: true, at: 0 });

    const tabId = openTab(app, 'BASE\n');
    const first = render(createElement(app.Workspace));
    await flushEffects();

    const edited = typeInto(realViewOf(app, tabId), 'EDIT');
    expect(edited).toBe('BASE\nEDIT');
    // 前置自证：防抖计时器已排队但**未**到点 ⇒ 草稿库此刻**没有**这份内容
    expect(draftSave).not.toHaveBeenCalled();

    first.unmount();
    // 快照已写回初始文本通道（"待交付正文"存在）—— 这是"内容来自快照"的直接观测点
    expect(app.store.hasInitialText(tabId)).toBe(true);
    // 草稿通路确实一次都没跑过（判据不靠草稿兜底）
    expect(draftSave).not.toHaveBeenCalled();

    const second = render(createElement(app.Workspace));
    await flushEffects();
    expect(realViewOf(app, tabId).state.doc.toString()).toBe(edited);
    void second;
  });
});

describe('AC-77③ 光标 / 滚动位置随快照一起往返', () => {
  it('AC-77③：切走前的 caret（1 基行/列）与 scrollTop 在重挂后逐值还原', async () => {
    const app = await loadRealEditorApp();
    login(app);
    vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
    vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });

    const tabId = openTab(app, 'aaa\nbbb\nccc\nddd');
    const first = render(createElement(app.Workspace));
    await flushEffects();

    const viewBefore = realViewOf(app, tabId);
    // 第 3 行第 2 列（1 基）+ 一个非 0 的滚动位置：两者都不是"新建视图会拿到的默认值"
    const targetHead = viewBefore.state.doc.line(3).from + 1;
    act(() => {
      viewBefore.dispatch({ selection: { anchor: targetHead } });
      viewBefore.scrollDOM.scrollTop = SCROLL_BEFORE;
    });
    expect(viewBefore.state.selection.main.head).toBe(targetHead);
    expect(viewBefore.scrollDOM.scrollTop).toBe(SCROLL_BEFORE);

    first.unmount();

    // 快照把 caret / scrollTop 也带回了 store（`snapshotInitialText` 的 `meta` 参数）
    const snapTab = app.store.useScratchStore.getState().tabs.find((tab) => tab.id === tabId);
    expect(snapTab?.caret).toEqual({ line: 3, col: 2 });
    expect(snapTab?.scrollTop).toBe(SCROLL_BEFORE);

    render(createElement(app.Workspace));
    await flushEffects();

    const viewAfter = realViewOf(app, tabId);
    expect(viewAfter).not.toBe(viewBefore);
    expect(viewAfter.state.selection.main.head).toBe(targetHead);
    expect(viewAfter.scrollDOM.scrollTop).toBe(SCROLL_BEFORE);
  });
});

describe('AC-77 写侧守卫（锁 1）：已关闭标签不得被快照写回而"复活"', () => {
  it('`closeTab` 之后写快照 ⇒ 被守卫拒绝：`hasInitialText` 为假、`takeInitialText` 为 null（AC-59 不被破坏）', async () => {
    const app = await loadRealEditorApp();
    let tabId = '';
    act(() => {
      tabId = app.store.useScratchStore.getState().createTab({ title: '待关闭' });
    });

    act(() => {
      app.store.useScratchStore.getState().closeTab(tabId);
    });
    expect(app.store.hasInitialText(tabId)).toBe(false);

    // 面板卸载时的快照调用（真实路径：`snapshotInitialText`）不得让死标签复活
    app.store.snapshotInitialText(tabId, '不应复活', { caret: { line: 1, col: 1 }, scrollTop: 0 });

    expect(app.store.hasInitialText(tabId)).toBe(false);
    expect(app.store.takeInitialText(tabId)).toBeNull();
    expect(app.store.useScratchStore.getState().tabs.map((tab) => tab.id)).not.toContain(tabId);
  });
});

describe('AC-77⑥ 不得用磁盘旧内容覆盖活会话中更新的未保存改动', () => {
  it('AC-77⑥：兜底读盘确实发生（draftList 第 2 次）但同 id 草稿条目被按 id 去重 ⇒ 活正文不被覆盖、只新增缺失标签', async () => {
    const app = await loadRealEditorApp();
    login(app);
    vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });

    const tabId = openTab(app, 'BASE', { id: 'tab-keep', title: '内存标题' });

    // ① 首次读盘**失败**（L-3 的既有语义：失败不置「本会话已还原」⇒ 下次挂载还会再读一次）
    const draftList = vi
      .spyOn(app.service, 'draftList')
      .mockRejectedValueOnce(new Error('草稿索引读取失败（夹具）'));

    const first = render(createElement(app.Workspace));
    await flushEffects();
    const edited = typeInto(realViewOf(app, tabId), 'EDIT');
    expect(edited).toBe('BASEEDIT');

    first.unmount();

    // ② 磁盘上有"同 id 的**旧**内容" + 一条内存里没有的条目
    draftList.mockResolvedValue({
      cleanExit: true,
      entries: [
        draftEntry('tab-keep', { title: '磁盘标题', dirty: true }),
        draftEntry('disk-extra', { title: '磁盘新增', dirty: false }),
      ],
    });

    render(createElement(app.Workspace));
    await flushEffects();

    // 前置自证：兜底读盘**真的**发生了（⇒ 下面的"没被覆盖"不是"压根没读"的假绿）
    expect(draftList).toHaveBeenCalledTimes(2);
    // 只新增内存里没有的 id（`hydrateFromDrafts` 的按 id 去重）
    const ids = app.store.useScratchStore.getState().tabs.map((tab) => tab.id);
    expect(ids).toContain('disk-extra');
    expect(ids.filter((id) => id === 'tab-keep')).toHaveLength(1);
    // 内存标签的元数据未被磁盘条目改写（同 id 的磁盘条目**不得**参与覆盖）
    const kept = app.store.useScratchStore.getState().tabs.find((tab) => tab.id === tabId);
    expect(kept?.title).toBe('内存标题');
    // ★ 判据：活会话里更新的未保存正文（快照）活下来了，**没有**被磁盘旧内容顶掉
    expect(realViewOf(app, tabId).state.doc.toString()).toBe(edited);
  });

  it('AC-77⑥（store 层）：`hydrateFromDrafts` 对已在内存的 id 只跳过、不改写（顺序与标题都不变）', async () => {
    const app = await loadRealEditorApp();
    let keepId = '';
    act(() => {
      keepId = app.store.useScratchStore.getState().createTab({ id: 'mem-1', title: '内存里的标题' });
    });
    act(() => {
      app.store.useScratchStore.getState().hydrateFromDrafts([
        draftEntry('mem-1', { title: '磁盘上的标题' }),
        draftEntry('mem-2', { title: '磁盘新增', order: 1 }),
      ]);
    });
    const tabs = app.store.useScratchStore.getState().tabs;
    expect(tabs.map((tab) => tab.id)).toEqual([keepId, 'mem-2']);
    expect(tabs[0]?.title).toBe('内存里的标题');
  });
});

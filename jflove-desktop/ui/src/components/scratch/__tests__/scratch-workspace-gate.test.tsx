/**
 * 工作区「登录门 + 草稿还原 + 关闭后空态」守门用例 —— Phase 6（v1.6.1）
 *
 * 判据出处（逐条对应）：
 *   - **AC-63 ③④**（需求 `文档记录/需求文档/v1.6.1.md:468`）：未登录时「工作区 / 标签条 DOM
 *     **不渲染**」+「本机草稿库中若有上次未保存内容，**不被还原**（仅确认未产生标签页）」；
 *   - **AC-55**（`:489`，改写版）：**登录成功后**自动还原 3 个标签页，内容 A/B/C，顺序与激活项
 *     与强杀前一致；
 *   - **AC-69 ①②③**（`:474`）：关闭最后一个干净标签页 → 标签条清空、工作区显示**空态**且
 *     含「新建」「打开」入口，**仍停留在文本编辑器**；
 *   - **AC-72 ①②③**（`:477`）：登出 / 会话失效 → 入口消失、工作区不再渲染、**不出现阻塞式
 *     "是否保存"**、**本机草稿文件仍存在（未被清理）**；
 *   - **AC-72 ④ + 设计 §4.5**（`文档记录/设计文档/v1.6.1.md:534-541`）：重新登录后内存 `tabs`
 *     非空 ⇒ **跳过 hydrate**（绝不用磁盘旧内容覆盖内存里更新的未保存改动）；「本会话是否已
 *     还原」是**模块级**标记（DS-34，`:946`）⇒ 清空标签 + 登出再登入**不得二次还原**；
 *   - **S-1 守门**（Phase 5 审查报告 `文档记录/代码审查报告/v1.6.1.md:70-93` + 附录 A.3.1
 *     `:518-546`）：`hydrateFromDrafts` 刻意把还原出的标签置 `mounted: false`（§4.8 懒挂载），
 *     而全仓唯一的 `setMounted(id, true)` 调用点在 `EditorPanel` 内 ⇒ 渲染集合必须是
 *     `tab.mounted || tab.id === activeTabId`（`ScratchWorkspace.tsx:203`）。附录 A.2 第 1 条
 *     `:506` 明确：该回修**只用仓库外 jsdom 探针验过，入库用例仍缺** —— 本文件就是那条入库用例。
 *
 * ## 为什么整文件用「模块重载 + 动态 import」
 *
 * `ScratchWorkspace.tsx:59` 的 `let hydratedThisSession = false` 是**模块级**状态（DS-34 故意
 * 如此），一旦置真就**永久生效**。而本文件的判据横跨"未还原 / 恰好还原一次 / 不得二次还原"
 * 三种期望 ⇒ 必须让每个用例拿到**全新的模块实例**，否则用例只能靠声明顺序互相迁就。
 * `vi.resetModules()` 只重置被 inline 的源码模块（React / RTL 在 node_modules 里被 externalize，
 * 实例保持唯一）；因此下面的 `loadApp()` 每次都从**同一代**里取组件、store、service 与宿主替身，
 * 避免"跨代混用单例"造成的假绿。
 *
 * ## 分层声明
 *
 * 只断言**渲染层可观察行为**（DOM 契约属性 / store 状态 / 桥调用次数）。真实文件系统、原生
 * 对话框与 CM6 内核由 Python 侧探针与别的面覆盖；`ScratchEditorHost`（CM6 宿主）在本文件被
 * 替身化，遵守项目既定纪律「不要把真实 CodeMirror 拖进 jsdom」。
 *
 * ## 安全（AGENTS.md §9）
 *
 * 夹具里**没有** token / session_key / 真实服务端地址；`draftList` / `draftDiscard` 全部是
 * 替身，**不发任何真实网络请求**（`utils/desktop-bridge` 被替身化，jsdom 里本来也没有 QWebChannel）。
 */
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { createElement } from 'react';
import type { ComponentType } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DraftPaneMeta } from '../../../types/scratch';

// ── 依赖替身（vi.mock 会被 hoist，工厂内动态 import 避免与 helper 形成加载环） ──────────────

/** 路由替身：工作区与「空态」的打开入口都会碰 `router`，这里只留一个可断言的 `navigate` */
const routerMock = vi.hoisted(() => ({ navigate: vi.fn() }));

vi.mock('../../../config/routes', () => ({ router: routerMock }));

vi.mock('../../../utils/desktop-bridge', () => ({
  // 判据：jsdom 无 QWebChannel，真实桥调用必然 reject（桥层实现已注明）
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

// ── 逐用例的模块代际装载 ────────────────────────────────────────────────────────────────

interface LoadedApp {
  /** 被测组件（`ScratchWorkspace` 新的一代实例） */
  Workspace: ComponentType;
  /** 同一代的 scratch store 模块 */
  store: typeof import('../../../stores/scratch-store');
  /** 同一代的 auth store（登录态夹具用） */
  authStore: typeof import('../../../stores/auth-store').useAuthStore;
  /** 同一代的会话快照模块（过期夹具用） */
  session: typeof import('../../../utils/session');
  /** 同一代的桥服务集合（全部被 spy 成替身） */
  service: typeof import('../../../services/scratch-service').scratchService;
}

/**
 * 取一代全新的应用模块。
 *
 * 顺序刻意固定：宿主替身（`./scratch-editor-host-mock`）必须在**导入被测组件之前**就位，
 * 这样 `vi.mock('../ScratchEditorHost')` 工厂与本文件拿到的是**同一个** helper 实例。
 */
async function loadApp(): Promise<LoadedApp> {
  vi.resetModules();
  const store = await import('../../../stores/scratch-store');
  const auth = await import('../../../stores/auth-store');
  const session = await import('../../../utils/session');
  const serviceModule = await import('../../../services/scratch-service');
  // 替身先加载（保证工厂与本文件同一实例），再加载被测组件
  const helper = await import('./scratch-editor-host-mock');
  helper.resetMockEditorSessions();
  const workspace = await import('../ScratchWorkspace');
  return {
    Workspace: workspace.ScratchWorkspace as ComponentType,
    store,
    authStore: auth.useAuthStore,
    session,
    service: serviceModule.scratchService,
  };
}

/** 已登录且会话未过期（= AC-63 准入判据为真：设计 §4.1 `canUseEditor`） */
function login(app: LoadedApp): void {
  app.session.setAuthSnapshot({ expiresAt: null });
  app.authStore.setState({ isLoggedIn: true });
}

/** 冲干"渲染 → effect → 异步 hydrate 链"（hydrate 是一个 `void (async () => …)()` IIFE） */
async function flushEffects(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

/** 造一条草稿索引条目（字段逐字对齐 `types/scratch.ts:175-195` 的 `DraftPaneMeta`） */
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

/**
 * `IntersectionObserver` 替身：jsdom 不实现它（实测 `jsdom/lib/jsdom/living/interfaces.js` 无该接口），
 * 装配它可以让懒挂载的条件②走**真实观察器分支**而不是"环境缺失 ⇒ 视为可见"的退化分支，
 * 使"mounted 自举闭环"的断言与真实 QtWebEngine 行为同源。
 */
class VisibleIntersectionObserver {
  private readonly callback: IntersectionObserverCallback;

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
  }

  observe(target: Element): void {
    // 面板真实进入视口：立即上报一次相交
    this.callback(
      [{ isIntersecting: true, target } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }

  unobserve(): void {
    /* 替身无内部状态 */
  }

  disconnect(): void {
    /* 替身无内部状态 */
  }

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

beforeEach(() => {
  routerMock.navigate.mockClear();
  vi.stubGlobal('IntersectionObserver', VisibleIntersectionObserver);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ── 用例 ──────────────────────────────────────────────────────────────────────────────

describe('AC-63 ③④ 未登录零渲染、零还原（草稿不被读出）', () => {
  it('AC-63③④：未登录（无有效会话）⇒ 工作区/标签条/面板零渲染，且 draftList 0 次', async () => {
    const app = await loadApp();
    // 前置：未登录（gate 假）
    expect(app.authStore.getState().isLoggedIn).toBe(false);
    // 即便草稿库里有内容也不允许读：替身给出 3 条，一旦被读就会被下面的断言抓住
    const draftList = vi
      .spyOn(app.service, 'draftList')
      .mockResolvedValue({ cleanExit: true, entries: [draftEntry('h1'), draftEntry('h2'), draftEntry('h3')] });

    const view = render(createElement(app.Workspace));
    await flushEffects();

    // ① 工作区根（DOM 契约 `data-scratch-workspace`，设计 §4.2 表）不出现
    expect(view.container.querySelector('[data-scratch-workspace="true"]')).toBeNull();
    expect(view.container.querySelector('[data-scratch-tab]')).toBeNull();
    expect(view.container.querySelector('[data-editor-panel]')).toBeNull();
    expect(view.container.querySelector('[data-scratch-workspace-empty="true"]')).toBeNull();
    // ② 草稿库一次都没被读（AC-63④ 的"不被还原"在渲染层的可判定形态）
    expect(draftList).not.toHaveBeenCalled();
    expect(app.store.useScratchStore.getState().tabs).toHaveLength(0);
  });

  it('AC-72①：会话过期（isLoggedIn 仍为 true）⇒ 同样零渲染、零 draftList', async () => {
    const app = await loadApp();
    // 造"会话中途失效"：登录态没被清掉，但失效时间已过（`isSessionExpired()` 口径）
    app.session.setAuthSnapshot({ expiresAt: Math.floor(Date.now() / 1000) - 60 });
    app.authStore.setState({ isLoggedIn: true });
    const draftList = vi
      .spyOn(app.service, 'draftList')
      .mockResolvedValue({ cleanExit: false, entries: [draftEntry('h1')] });

    const view = render(createElement(app.Workspace));
    await flushEffects();

    expect(view.container.querySelector('[data-scratch-workspace="true"]')).toBeNull();
    expect(draftList).not.toHaveBeenCalled();
    expect(app.store.useScratchStore.getState().tabs).toHaveLength(0);
  });
});

describe('AC-55 / §4.5 已登录后的草稿还原（条件三条）', () => {
  it('AC-55：已登录 + 内存 tabs 空 ⇒ hydrate 恰好一次（draftList 1 次），顺序与激活项按索引还原', async () => {
    const app = await loadApp();
    login(app);
    const draftList = vi.spyOn(app.service, 'draftList').mockResolvedValue({
      cleanExit: true,
      entries: [
        draftEntry('h1', { order: 0 }),
        draftEntry('h2', { order: 1, active: true }),
        draftEntry('h3', { order: 2 }),
      ],
    });

    const view = render(createElement(app.Workspace));
    await flushEffects();

    // 判据：hydrate **恰一次**（不是 0 次也不是 N 次 —— 依赖数组抖动会变成 N 次）
    expect(draftList).toHaveBeenCalledTimes(1);
    const state = app.store.useScratchStore.getState();
    expect(state.tabs.map((tab) => tab.id)).toEqual(['h1', 'h2', 'h3']);
    expect(state.activeTabId).toBe('h2');
    // 标签条完整渲染（还原路径未误伤 ScratchTabBar）
    expect(view.container.querySelectorAll('[data-scratch-tab]')).toHaveLength(3);
    expect(
      view.container.querySelector('[data-scratch-tab-active="true"]')?.getAttribute('data-scratch-tab'),
    ).toBe('h2');
  });

  it('AC-55 条件②：内存已有标签 ⇒ 不读磁盘（draftList 0 次），内存内容不被覆盖', async () => {
    const app = await loadApp();
    login(app);
    const draftList = vi
      .spyOn(app.service, 'draftList')
      .mockResolvedValue({ cleanExit: true, entries: [draftEntry('disk-1'), draftEntry('disk-2')] });
    let memoryId = '';
    act(() => {
      memoryId = app.store.useScratchStore.getState().createTab({ title: '内存里的未保存标签' });
    });

    render(createElement(app.Workspace));
    await flushEffects();

    expect(draftList).not.toHaveBeenCalled();
    const state = app.store.useScratchStore.getState();
    expect(state.tabs.map((tab) => tab.id)).toEqual([memoryId]);
    expect(state.tabs[0]?.title).toBe('内存里的未保存标签');
  });

  it('§4.5③（DS-34）：本会话只还原一次 —— 清空标签 + 登出再登入后不得二次 hydrate', async () => {
    const app = await loadApp();
    login(app);
    const draftList = vi.spyOn(app.service, 'draftList').mockResolvedValue({
      cleanExit: false,
      entries: [draftEntry('h1', { dirty: false }), draftEntry('h2', { dirty: false, active: true })],
    });
    const draftDiscard = vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });

    render(createElement(app.Workspace));
    await flushEffects();
    expect(draftList).toHaveBeenCalledTimes(1);

    // 用户把还原出来的标签全部关闭（干净关闭路径：直接落地，不弹询问框）
    act(() => {
      const store = app.store.useScratchStore.getState();
      store.tabs.forEach((tab) => store.requestCloseTab(tab.id));
    });
    expect(app.store.useScratchStore.getState().tabs).toHaveLength(0);
    // 干净关闭顺带丢弃草稿（AC-59 的"无需询问"分支），这是**预期**的清理
    expect(draftDiscard).toHaveBeenCalledTimes(2);

    // 登出 → 再登录：gate 从 false 翻回 true，工作区 effect 会再跑一次
    act(() => {
      app.authStore.setState({ isLoggedIn: false });
    });
    act(() => {
      app.authStore.setState({ isLoggedIn: true });
    });
    await flushEffects();

    // 模块级"本会话已还原"标记继续生效 ⇒ 绝不把刚放弃的脏草稿又还原回来。
    //
    // ⚠ C1 批（2026-10-16）复核结论：**本条不改**，理由写在这里免得后人误改 ——
    // 该计数编码的是 DS-34「本会话只还原一次」的语义（`hydratedThisSession` 分支），
    // **不是**被 F5 缺陷焊死的那条「内存里有 tab ⇒ 内存里就有正文」的假设
    // （后者的观测点在 `scratch-tab-reorder.test.tsx` 的 AC-68② 用例，已按设计 §15.9.3 方案 (a) 改写）。
    // hydrate 门改成 `tabs.length === 0 || pickInvalidTabs(tabs).length > 0` 后，本用例走的是
    // effect 开头的 `hydratedThisSession` 早退分支 ⇒ **新判据根本没被求值** ⇒ 计数仍是 1（实测通过）。
    expect(draftList).toHaveBeenCalledTimes(1);
    expect(app.store.useScratchStore.getState().tabs).toHaveLength(0);
  });
});

describe('S-1 守门：草稿还原后的渲染集合必须含激活项（懒挂载自举）', () => {
  it('S-1：3 条还原标签全部 mounted:false ⇒ 激活项必须有面板，未挂载且非激活的标签不得有面板', async () => {
    const app = await loadApp();
    login(app);

    // 记录每次 store 变更后的 mounted 标志：用来证明"判定渲染集合时读到的那一份"确实是全 false
    const mountedHistory: string[] = [];
    const unsubscribe = app.store.useScratchStore.subscribe((state) => {
      mountedHistory.push(state.tabs.map((tab) => `${tab.id}:${tab.mounted ? 1 : 0}`).join(','));
    });

    const draftList = vi.spyOn(app.service, 'draftList').mockResolvedValue({
      cleanExit: true,
      entries: [
        draftEntry('h1', { order: 0 }),
        draftEntry('h2', { order: 1, active: true }),
        draftEntry('h3', { order: 2 }),
      ],
    });

    const view = render(createElement(app.Workspace));
    await flushEffects();
    unsubscribe();

    expect(draftList).toHaveBeenCalledTimes(1);

    // ① hydrate 落地的第一份状态：三条全部 mounted:false（§4.8「还原阶段只登记状态，不建实例」）
    expect(mountedHistory[0]).toBe('h1:0,h2:0,h3:0');

    // ② S-1 的判别判据：`[data-editor-panel]` 至少 1 个，且其 id = **激活项**。
    //    若把 `ScratchWorkspace.tsx:203` 的渲染集合改回只按 `tab.mounted` 过滤，
    //    则首帧渲染集合为空 ⇒ 没有 EditorPanel ⇒ 唯一 setMounted 调用点永不执行
    //    ⇒ 面板数**恒为 0** ⇒ 本断言必红（这就是回修前 `s1_hydrate_oldPredicateCount = 0` 的入库形态）。
    const panelIds = Array.from(view.container.querySelectorAll('[data-editor-panel]')).map((node) =>
      node.getAttribute('data-editor-panel'),
    );
    expect(panelIds.length).toBeGreaterThanOrEqual(1);
    expect(panelIds).toEqual(['h2']);
    expect(
      view.container.querySelector('[data-editor-panel-active="true"]')?.getAttribute('data-editor-panel'),
    ).toBe('h2');

    // ③ 反向半边：未挂载且非激活的 h1 / h3 不得出现面板
    //    （两半合起来才排除"渲染集合退化成渲染全部标签"这种假绿）
    expect(view.container.querySelector('[data-editor-panel="h1"]')).toBeNull();
    expect(view.container.querySelector('[data-editor-panel="h3"]')).toBeNull();

    // ④ 自举闭环真的完成：h2 由 false 置 true（全仓唯一 setMounted 调用点在 EditorPanel 内），
    //    且 h1/h3 仍为 false ⇒ §4.8「只挂载被激活过的标签」的内存口径未被破坏
    expect(app.store.useScratchStore.getState().tabs.map((tab) => tab.mounted)).toEqual([false, true, false]);
    expect(mountedHistory).toContain('h1:0,h2:1,h3:0');
  });
});

describe('AC-69 关闭最后一个标签页后停留在工作区空态', () => {
  it('AC-69①②③：关掉最后一个干净标签 ⇒ 出现空态且**无 navigate**；点「新建标签页」再出新标签', async () => {
    const app = await loadApp();
    login(app);
    vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });

    const view = render(createElement(app.Workspace));
    await flushEffects();

    // 起点：没有标签 ⇒ 空态（工作区根仍在）
    expect(view.container.querySelector('[data-scratch-workspace="true"]')).not.toBeNull();
    expect(view.container.querySelector('[data-scratch-workspace-empty="true"]')).not.toBeNull();

    let tabId = '';
    act(() => {
      tabId = app.store.useScratchStore.getState().createTab({ title: '干净标签' });
    });
    expect(view.container.querySelector('[data-scratch-workspace-empty="true"]')).toBeNull();
    expect(view.container.querySelectorAll('[data-scratch-tab]')).toHaveLength(1);

    // 关闭它（干净标签 ⇒ 不弹询问框）
    act(() => {
      app.store.useScratchStore.getState().requestCloseTab(tabId);
    });

    // ① 标签条清空 + 工作区显示空态
    expect(app.store.useScratchStore.getState().tabs).toHaveLength(0);
    expect(view.container.querySelectorAll('[data-scratch-tab]')).toHaveLength(0);
    expect(view.container.querySelector('[data-scratch-workspace-empty="true"]')).not.toBeNull();
    // ② 仍停留在文本编辑器：工作区根还在，且**没有任何跳转动作**（AC-69② / 设计 §4.6 末行）
    expect(view.container.querySelector('[data-scratch-workspace="true"]')).not.toBeNull();
    expect(routerMock.navigate).not.toHaveBeenCalled();

    // ③ 空态的「新建标签页」入口真的可用
    const createButton = Array.from(view.container.querySelectorAll('button')).find(
      (button) => button.textContent === '新建标签页',
    );
    expect(createButton).not.toBeUndefined();
    act(() => {
      fireEvent.click(createButton as HTMLButtonElement);
    });
    expect(app.store.useScratchStore.getState().tabs).toHaveLength(1);
  });
});

describe('AC-72 登出 / 会话失效不丢内容（草稿保留、不覆盖内存）', () => {
  it('AC-72①③：登出 ⇒ 工作区不渲染、内存 tabs 保留、draftDiscard 零调用；重登不 hydrate', async () => {
    const app = await loadApp();
    login(app);
    // 就算磁盘上有草稿也不允许读（内存非空 ⇒ 条件②拦截）
    const draftList = vi
      .spyOn(app.service, 'draftList')
      .mockResolvedValue({ cleanExit: true, entries: [draftEntry('disk-1'), draftEntry('disk-2')] });
    const draftDiscard = vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });

    // 内存里已有 2 个标签，其中一个是脏的（本机草稿形态）
    let dirtyId = '';
    act(() => {
      app.store.useScratchStore.getState().createTab({ title: '甲', path: 'D:/tmp/gate-a.txt' });
      dirtyId = app.store.useScratchStore.getState().createTab({ title: '乙', dirty: true });
    });

    const view = render(createElement(app.Workspace));
    await flushEffects();
    expect(draftList).not.toHaveBeenCalled();

    // 登出（会话失效同一条路径：gate 翻转 ⇒ 工作区准入兜底返回 null）
    act(() => {
      app.authStore.setState({ isLoggedIn: false });
    });

    // ① 工作区 / 标签条 / 面板全部消失
    expect(view.container.querySelector('[data-scratch-workspace="true"]')).toBeNull();
    expect(view.container.querySelectorAll('[data-scratch-tab]')).toHaveLength(0);
    expect(view.container.querySelectorAll('[data-editor-panel]')).toHaveLength(0);
    // ③ 草稿**不得被清理**（AC-72③：本机草稿文件中该内容仍存在）
    expect(draftDiscard).not.toHaveBeenCalled();
    // 内存 tabs 保留（不渲染、不清空），脏标记与内容都不丢
    const ids = app.store.useScratchStore.getState().tabs.map((tab) => tab.id);
    expect(ids).toHaveLength(2);
    expect(app.store.useScratchStore.getState().tabs.find((tab) => tab.id === dirtyId)?.dirty).toBe(true);

    // ④ 重新登录：入口恢复，但内存非空 ⇒ 跳过 hydrate（绝不用磁盘旧内容覆盖内存改动）
    act(() => {
      app.authStore.setState({ isLoggedIn: true });
    });
    await flushEffects();
    expect(view.container.querySelector('[data-scratch-workspace="true"]')).not.toBeNull();
    expect(draftList).not.toHaveBeenCalled();
    expect(app.store.useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual(ids);
    expect(app.store.useScratchStore.getState().tabs.find((tab) => tab.id === dirtyId)?.dirty).toBe(true);
  });
});

describe('L-3：hydrate 失败不得把「本会话已还原」置真（重挂载必须再试一次）', () => {
  it('L-3：`draftList` 拒绝 ⇒ 零标签渲染 + `lastError` 被设置；重挂载重试一次（次数 +1），第二次成功则标签被还原', async () => {
    const app = await loadApp();
    login(app);

    // `lastError` 会被紧随其后的 Toast effect 清掉，故用订阅留痕证明它**确实被设置过**
    const errorHistory: (string | null)[] = [];
    const unsubscribe = app.store.useScratchStore.subscribe((state) =>
      errorHistory.push(state.lastError),
    );

    // 第一次读盘失败（只失败一次）
    const draftList = vi
      .spyOn(app.service, 'draftList')
      .mockRejectedValueOnce(new Error('草稿索引读取失败（夹具）'));

    const first = render(createElement(app.Workspace));
    await flushEffects();

    // ① 失败 ⇒ 工作区不渲染任何标签（不是"标签没内容"，而是根本没有标签条）
    expect(draftList).toHaveBeenCalledTimes(1);
    expect(first.container.querySelectorAll('[data-scratch-tab]')).toHaveLength(0);
    expect(first.container.querySelector('[data-scratch-tab]')).toBeNull();
    expect(app.store.useScratchStore.getState().tabs).toHaveLength(0);
    // ② 失败已上报（`lastError` 被设置过）
    expect(errorHistory).toContain('草稿索引读取失败（夹具）');

    // ③ 卸载后重新挂载：第二次读盘改为成功
    first.unmount();
    draftList.mockResolvedValue({
      cleanExit: true,
      entries: [draftEntry('r1', { order: 0 }), draftEntry('r2', { order: 1, active: true })],
    });
    const second = render(createElement(app.Workspace));
    await flushEffects();
    unsubscribe();

    // 判据：失败**不得**把模块级"本会话已还原"标记置真 ⇒ 重挂载必须再试一次（1 → 2）
    //
    // ⚠ C1 批（2026-10-16）复核结论：**本条不改，且它是新判据另一半的守门人** ——
    // AC-77⑥ 把 hydrate 门从 `tabs.length > 0` 改成
    // `tabs.length === 0 || pickInvalidTabs(tabs).length > 0`，其中**"内存为空 ⇒ 必须读盘"**
    // 这半边是冷启动还原（AC-55 / AC-60 / AC-72④）的唯一通路。本用例两次挂载时 `tabs` 都为空，
    // 若有人把该半边删掉（只按"有没有正文已失效的标签"判定），第二次就不会读盘 ⇒ 这里的 2 会变成 1
    // ⇒ 本断言正是那种改法的拦网。
    expect(
      draftList,
      'hydrate 失败后本会话必须还能再试一次（标记只能在成功后置真）',
    ).toHaveBeenCalledTimes(2);
    // ④ 第二次成功 ⇒ 标签真的被还原出来
    expect(app.store.useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual(['r1', 'r2']);
    expect(second.container.querySelectorAll('[data-scratch-tab]')).toHaveLength(2);
  });
});

describe('L-6：超出上限被丢弃的打开请求必须提示一次（且不暴露路径 / 文件名）', () => {
  it('L-6：丢弃 2 条 ⇒ 挂载工作区提示一次且文案含数量；再次挂载 / 重渲染不重复提示', async () => {
    const app = await loadApp();
    // 同一代际取 `ui`（`toast` 与组件内 `../ui` 是同一个对象引用）与队列模块
    const ui = await import('../../ui');
    const queue = await import('../../../utils/scratch/open-paths-queue');
    const toastInfo = vi.spyOn(ui.toast, 'info');

    // 夹具：一次性入队 66 条（上限 64）⇒ 恰好丢弃 2 条。
    // 此刻**尚无消费者**（工作区未挂载）⇒ flush 不交付，队列与丢弃计数原样保留。
    queue.resetOpenPathsQueue();
    const accepted = queue.enqueueOpenPaths(
      Array.from({ length: 66 }, (_, index) => `D:/tmp/dropped-${index}.txt`),
    );
    expect(accepted).toBe(64);
    expect(queue.droppedOpenPaths()).toBe(2);

    login(app);
    // 草稿库为空 ⇒ 还原路径不产生 Toast，`toast.info` 只可能来自"丢弃提示"
    vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });

    const view = render(createElement(app.Workspace));
    await flushEffects();

    // ① 提示恰好一次，文案含数量
    expect(toastInfo).toHaveBeenCalledTimes(1);
    const [title, description] = toastInfo.mock.calls[0] ?? [];
    expect(String(description)).toContain('2');
    expect(String(description)).toContain('未打开');
    // ② 文案不得泄露任何路径 / 文件名（AGENTS.md §9.12 / §9.14、设计 §7.4）
    const shown = `${String(title)}${String(description)}`;
    expect(shown).not.toContain('/tmp');
    expect(shown).not.toContain('dropped-');

    // ③ 再次挂载 + 重渲染都不得重复提示（同一次会话只提示一次）
    view.unmount();
    const again = render(createElement(app.Workspace));
    await flushEffects();
    again.rerender(createElement(app.Workspace));
    await flushEffects();
    expect(toastInfo, '同一次会话只提示一次，重渲染不得重复弹').toHaveBeenCalledTimes(1);
  });
});

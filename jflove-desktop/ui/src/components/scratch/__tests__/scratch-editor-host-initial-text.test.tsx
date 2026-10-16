/**
 * **M-15 守门用例**：`takeInitialText` 与 CM6 实例创建之间的"丢正文窗口"
 * —— 审查报告 `文档记录/代码审查报告/v1.6.1.md` §15.3（M-15）/ 设计 §15.5.2
 *
 * ## 缺陷形态（修复前，逐行）
 *
 * `ScratchEditorHost.tsx` 的 `pane.id` effect 里 `:612` 做 `takeInitialText(paneId)`
 * （**取走即删**，全生命周期只交付一次），而 CM6 实例要到 `:709-712`
 * （**一个 `await loadEditorBundle()` 之后**，离线包首次加载 / 解析 3.4 MB 的窗口最宽）才创建。
 * 若卸载落在这两点之间：
 *   ① `snapshotIntoStore(paneId, viewRef.current)` 因 `view === null` 直接 `return`
 *      （`:334` 的守卫）⇒ **不写回快照**；
 *   ② 正文只活在组件内的 `initialTextRef` 里 ⇒ 随卸载消失；
 *   ③ 重挂时 `takeInitialText` 取不到 ⇒ `EditorState.create({ doc: '' })` ⇒ **空 doc**。
 *
 * 这与 F5 是**同一形态**（正文失去唯一持有者），只是窗口更窄，且落在刚改过的那段代码里。
 *
 * ## 本文件的判别力来源（为什么不是"断言自己"）
 *
 * ① 用**真** `ScratchEditorHost` + **真** store（不 mock 宿主）；
 * ② 把 `loadEditorBundle()` **挂起**（人工 `resolver`）⇒ 精确制造"宿主已挂载、实例未创建"
 *    这一时刻 —— 这正是审查报告 §15.11 第 1 行"未能确认"的那条路径；
 * ③ **前置自证**：卸载前必须 `tabs` 里仍有该标签、`initialTexts` 已被取走
 *    （`hasInitialText === false`）、`getScratchEditorSession(id) === null`
 *    ⇒ 保证下面的"正文还在"不是"压根没发生取走"的假绿；
 * ④ 配套的 `scratch-editor-host-initial-text-negative.test.tsx` 把修复分支注入成失效
 *    （等价于修复前行为）⇒ 本文件的判据在那里**必须变红**。
 *
 * ## 安全（AGENTS.md §9）
 *
 * 夹具里无 token / session_key / 真实服务端地址；草稿服务与桥全是替身，**不发真实网络请求**；
 * 断言只比正文，不打印正文到日志。
 */
import { act, render } from '@testing-library/react';
import { createElement } from 'react';
import type { ComponentType } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installJsdomRangeMeasureShim } from './scratch-real-editor-dom-shim';

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

/**
 * ★ 把离线包加载**挂起**：`loadEditorBundle()` 返回一个由用例手工 resolve 的 Promise。
 *
 * `vi.hoisted` 保证 `deferred` 在 `vi.mock` 工厂（会被提升到文件顶部）之前就绪；
 * `vi.resetModules()` 只影响模块缓存，不影响本工厂 —— 每代宿主都会拿到**同一个**挂起件。
 */
const deferred = vi.hoisted(() => {
  return {
    resolve: null as null | ((value: unknown) => void),
    promise: null as unknown,
  };
});

vi.mock('../../../utils/scratch/load-editor-bundle', () => ({
  loadEditorBundle: () =>
    new Promise((resolve) => {
      deferred.resolve = resolve as (value: unknown) => void;
      return deferred.promise;
    }),
  isEditorBundleLoaded: () => false,
}));

interface HostApp {
  store: typeof import('../../../stores/scratch-store');
  host: typeof import('../ScratchEditorHost');
  Host: ComponentType<{ pane: unknown; visible?: boolean }>;
  service: typeof import('../../../services/scratch-service').scratchService;
  authStore: typeof import('../../../stores/auth-store').useAuthStore;
  session: typeof import('../../../utils/session');
  /** 真离线包 API（用于"放行挂起的加载"后让实例真的建出来） */
  api: unknown;
}

/** 取一代全新的应用模块（store / 宿主 / 服务同代，避免跨代引用不一致） */
async function loadApp(): Promise<HostApp> {
  vi.resetModules();
  const store = await import('../../../stores/scratch-store');
  const auth = await import('../../../stores/auth-store');
  const session = await import('../../../utils/session');
  const serviceModule = await import('../../../services/scratch-service');
  const cm = await import('../../../test/cm-bundle');
  const api = cm.loadCmBundle();
  const host = await import('../ScratchEditorHost');
  return {
    store,
    host,
    Host: host.ScratchEditorHost as unknown as ComponentType<{ pane: unknown; visible?: boolean }>,
    service: serviceModule.scratchService,
    authStore: auth.useAuthStore,
    session,
    api,
  };
}

/** 已登录且会话未过期（AC-63 准入判据为真） */
function login(app: HostApp): void {
  app.session.setAuthSnapshot({ expiresAt: null });
  app.authStore.setState({ isLoggedIn: true });
}

/** 冲干微任务（宿主建实例是 `void (async () => { await loadEditorBundle(); … })()`） */
async function flushEffects(rounds = 12): Promise<void> {
  await act(async () => {
    for (let i = 0; i < rounds; i += 1) await Promise.resolve();
  });
}

/** 打开一个"标签页 + 初始正文"，对应生产路径的 `openOrFocus`（正文经 `initialTexts` 交付一次） */
function openTab(app: HostApp, base: string): string {
  let tabId = '';
  act(() => {
    tabId = app.store.useScratchStore.getState().createTab({
      title: 'f.txt',
      language: 'text',
      detected: 'text',
    });
  });
  app.store.putInitialText(tabId, base);
  return tabId;
}

/** 按 store 快照渲染宿主（与 `EditorPanel` 的挂法同形：从 store 取标签、传 `visible`） */
function hostsOf(app: HostApp, tabId: string) {
  return render(
    createElement(app.Host, {
      pane: app.store.useScratchStore.getState().tabs.find((tab) => tab.id === tabId),
      visible: true,
    }),
  );
}

const BASE = '第一行\n第二行\n';

beforeEach(() => {
  installJsdomRangeMeasureShim();
  vi.useRealTimers();
  deferred.resolve = null;
  deferred.promise = null;
});

afterEach(() => {
  vi.useRealTimers();
});

describe('M-15：宿主挂载后、实例创建前卸载 ⇒ 正文不得丢', () => {
  it('M-15①：离线包加载被挂起时卸载 ⇒ 重挂后正文**仍然存在**（且确实经过"取走即删"）', async () => {
    const app = await loadApp();
    login(app);
    vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
    vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });

    const tabId = openTab(app, BASE);
    const first = hostsOf(app, tabId);
    await flushEffects();

    // ── 前置自证：此刻正处于"已取走、未建成"的窗口 ──
    expect(deferred.resolve).not.toBeNull(); // 宿主确实在等离线包（⇒ 实例还没建）
    expect(app.host.getScratchEditorSession(tabId)).toBeNull(); // 没有实例
    expect(app.store.hasInitialText(tabId)).toBe(false); // 初始正文**已被取走即删**
    expect(app.store.takeInitialText(tabId)).toBeNull(); // 再取为空（既有语义未被改坏）

    // ── 卸载（路由切走）⇒ 修复前正文在此处消失 ──
    first.unmount();

    // 修复：cleanup 对"实例从未建成"补一次写回 ⇒ 待交付正文重新存在
    //
    // ⚠ **不得**在这里用 `takeInitialText()` 去"确认写回的是原文"：
    //   该函数是**取走即删**（既有语义，本批不许改）⇒ 一旦在这里取走，重挂时就真的没有
    //   正文可交付了（本用例自测时正是这么红的）。"写回的是原文"由重挂后的正文断言证明。
    expect(app.store.hasInitialText(tabId)).toBe(true);

    // 重挂：重新做一次交接（下面用"待交付正文已被取走"自证真的走了一遍）
    const second = hostsOf(app, tabId);
    await flushEffects();
    expect(app.store.hasInitialText(tabId)).toBe(false);

    // 放行挂起的离线包 ⇒ 实例真的建出来（"真的建出了编辑器"这条也要成立）
    expect(deferred.resolve).not.toBeNull();
    await act(async () => {
      deferred.resolve?.(app.api);
      await Promise.resolve();
      await Promise.resolve();
    });
    await flushEffects();

    const session = app.host.getScratchEditorSession(tabId);
    expect(session).not.toBeNull();
    // ★ 主判据：正文逐字符等于切走前的值（修复前这里是 ''）
    expect(session?.view.state.doc.toString()).toBe(BASE);
    void second;
  });

  it('M-15②：同一窗口内的连续性 —— 卸载重挂两次后正文仍逐字符不变（不被空串覆盖）', async () => {
    const app = await loadApp();
    login(app);
    vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
    vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });

    const tabId = openTab(app, BASE);
    const first = hostsOf(app, tabId);
    await flushEffects();
    expect(app.host.getScratchEditorSession(tabId)).toBeNull(); // 未建成
    first.unmount();

    const second = hostsOf(app, tabId);
    await flushEffects();
    // 第 2 次仍然"未建成"就再卸载：正文必须还是原样（第一次写回的不能被空的 `initialTextRef` 覆盖）
    expect(app.host.getScratchEditorSession(tabId)).toBeNull();
    expect(app.store.hasInitialText(tabId)).toBe(false); // 已被第 2 次交接取走
    second.unmount();
    // ⚠ 同 M-15①：这里**不能** `takeInitialText()` 去"确认"，那是取走即删（会把正文真取走）
    expect(app.store.hasInitialText(tabId)).toBe(true); // 第 2 次写回仍然发生

    const third = hostsOf(app, tabId);
    await flushEffects();
    await act(async () => {
      deferred.resolve?.(app.api);
      await Promise.resolve();
      await Promise.resolve();
    });
    await flushEffects();

    expect(app.host.getScratchEditorSession(tabId)?.view.state.doc.toString()).toBe(BASE);
    void third;
  });

  it('M-15③（既有语义守门）：已关闭标签不得被这条新写回"复活"', async () => {
    // 修复分支仍走 `snapshotInitialText` ⇒ 它的 `tabs.find` 守卫必须继续挡住死标签（AC-59）
    const app = await loadApp();
    const tabId = openTab(app, BASE);
    act(() => {
      app.store.useScratchStore.getState().closeTab(tabId);
    });

    app.store.snapshotInitialText(tabId, BASE);

    expect(app.store.hasInitialText(tabId)).toBe(false);
    expect(app.store.takeInitialText(tabId)).toBeNull();
  });
});

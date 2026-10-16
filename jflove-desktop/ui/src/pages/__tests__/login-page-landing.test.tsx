/**
 * 「登录成功后的落点」守门用例 —— v1.6.1 Phase 6 回修的回归判据（AC-64 ② / 缺陷 R1）
 *
 * ## 它锁死什么
 *
 * AC-64 ②（`文档记录/需求文档/v1.6.1.md:469`）：「未登录时经『打开方式』唤起文件 → 完成登录 →
 * 左侧菜单**自动切到「文本编辑器」**」。设计 §2.2.1 时序图（`文档记录/设计文档/v1.6.1.md:223-226`）
 * 把这一拍写成「登录成功 → `navigate('/editor')`」。
 *
 * ## 缺陷 R1（本文件就是它的回归判据）
 *
 * 修复前有**两处导航互相打架**，都产生「登录后的落点」：
 *
 * | # | 出处 | 动作 |
 * |---|------|------|
 * | ① | `utils/scratch/editor-open-paths.ts` 的 `useAuthStore.subscribe` 回调 | 门翻转时 `navigate('/editor')` 并 `flushOpenPaths()` |
 * | ② | `pages/LoginPage.tsx` 的「已登录则跳转」effect | `navigate('/', { replace: true })` |
 *
 * zustand 的 `subscribe` 监听器在 `set()` 时**同步**先跑（①，队列随即被清空），React 提交后
 * LoginPage 的 effect 才跑（②）。React Router 的数据路由 `navigate` 是**异步**的：
 * **后发起的②覆盖了先发起的①** ⇒ 真机观测到落点是 `#/files`（左侧菜单不在「文本编辑器」）。
 *
 * 修复方式（设计取舍）：把「登录后的落点」收敛成**唯一判据**
 * （`editorRevealPendingOnLogin()`，由①在自己的回调里置位），
 * LoginPage 只消费判据、不再自己拍板。
 *
 * ## 用例与修复前/后的对应（判据力自证）
 *
 * - 用例①（**修复前必红**）：有挂起唤起 ⇒ 落点必须是 `/editor`。修复前 ① 被 ② 覆盖 ⇒ 落 `/`；
 * - 用例②（修复前也绿，防回退）：无挂起唤起 ⇒ 落点仍必须是 `/`（防「一刀切都去编辑器」）；
 * - 用例③（修复前必红）：登出（门翻回假）⇒ 判据必须复位 ⇒ 再次登录且无唤起时落回 `/`。
 *
 * 若把判据换回「LoginPage 自己读 `pendingOpenPaths()`」或「`setTimeout` 让位」，用例①必红：
 * ①（接线）跑在 `flushOpenPaths()` 之前/之中，队列在 LoginPage 渲染时**已经是 0**。
 *
 * ## 分层与安全（AGENTS.md §9）
 *
 * - 只替换桥的**最底层**（`utils/desktop-bridge` 的注册表）与「会碰文件系统/远端」的服务方法；
 *   队列、接线、store、`applySessionView`、LoginPage 全是**真实实现**；
 * - `config/routes` 被替身化成内存路由（只挂 LoginPage + 落点占位），
 *   不把整张真实路由表（及其页面桥调用）拖进 jsdom；内存路由与真实 hash 路由的导航语义同源；
 * - 夹具里**没有** token / session_key / 真实服务端地址，**不发任何真实网络请求**（§9.14）。
 */
import { act, cleanup, render } from '@testing-library/react';
import { RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// ── 桥替身：唯一被替换的是「Python 推事件的注册表」（与 v1.6.1 既有门禁用例同一手法）──
const bridgeMock = vi.hoisted(() => {
  const listeners = new Map<string, Set<(payload: unknown) => void>>();
  return {
    emit(topic: string, payload: unknown): void {
      listeners.get(topic)?.forEach((handler) => handler(payload));
    },
    listenerCount(topic: string): number {
      return listeners.get(topic)?.size ?? 0;
    },
    onBridgeEvent(topic: string, handler: (payload: unknown) => void): () => void {
      let set = listeners.get(topic);
      if (!set) {
        set = new Set();
        listeners.set(topic, set);
      }
      set.add(handler);
      return () => {
        set?.delete(handler);
      };
    },
    callBridge: async (): Promise<Record<string, never>> => ({}),
    callShell: async (): Promise<boolean> => true,
    isBridgeReady: (): boolean => true,
    getChannelObject: (): null => null,
    whenBridgeReady: async (): Promise<void> => undefined,
  };
});

vi.mock('../../utils/desktop-bridge', () => bridgeMock);

/**
 * 桥调用服务：只替身化「会碰文件系统 / 远端」的方法。
 *
 * `onEditorOpenPaths` 保持**真实实现**（它只是把 handler 挂到被替身的注册表上），
 * `detectAndRead` 返回空条目 —— 本文件的判据是**落点**，不是建标签页。
 */
vi.mock('../../services/scratch-service', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/scratch-service')>();
  return {
    ...actual,
    scratchService: {
      ...actual.scratchService,
      detectAndRead: vi.fn(),
      writeText: vi.fn(),
      draftSave: vi.fn(),
      draftDiscard: vi.fn(),
      reportDirty: vi.fn(),
    },
  };
});

/**
 * 路由替身：内存路由，只挂**被测落点**。
 *
 * 关键点：接线（`editor-open-paths.ts`）与 LoginPage（`useNavigate()`）操作的是**同一个**
 * router 对象 —— 只有这样，「两处导航谁覆盖谁」才在本用例里真实可观测（真机同理）。
 */
vi.mock('../../config/routes', async () => {
  const { createElement } = await import('react');
  const { createMemoryRouter } = await import('react-router');
  const { LoginPage } = await import('../LoginPage');
  const stub = (testId: string) => createElement('div', { 'data-testid': testId });
  return {
    router: createMemoryRouter(
      [
        { path: '/login', element: createElement(LoginPage) },
        // 首页落点的占位（真机上 `/` 会再 redirect 到 `/files`，这里只保留"落到了首页"这一事实）
        { path: '/', element: stub('home-file-list') },
        { path: '/editor', element: stub('editor-workspace') },
      ],
      { initialEntries: ['/login'] },
    ),
  };
});

import { router } from '../../config/routes';
import { authService, type SessionView } from '../../services/auth-service';
import { scratchService } from '../../services/scratch-service';
import { useAuthStore } from '../../stores/auth-store';
import { useScratchStore } from '../../stores/scratch-store';
import { setAuthSnapshot } from '../../utils/session';
// 副作用 import：`editor-open-paths.ts:138` 在模块顶层自动装机（幂等）—— 这就是「真实接线」
import '../../utils/scratch/editor-open-paths';
import { pendingOpenPaths, resetOpenPathsQueue } from '../../utils/scratch/open-paths-queue';

const detectAndRead = vi.mocked(scratchService.detectAndRead);

/** AC-64 ② 的观测点：编辑器工作区路由（`config/routes.tsx:123`） */
const EDITOR_ROUTE = '/editor';
/** 非唤起场景的落点（首页） */
const HOME_ROUTE = '/';

/** 造一份 Python 侧返回的会话快照（字段逐字对齐 `auth-service.ts:23-38`） */
function sessionViewOf(loggedIn: boolean): SessionView {
  return {
    logged_in: loggedIn,
    username: loggedIn ? 'tester' : '',
    role: loggedIn ? 'user' : '',
    user_id: loggedIn ? 7 : null,
    is_admin: false,
    server_url: 'http://127.0.0.1:8989',
    session_ready: loggedIn,
    // 0 ⇒ 渲染层 `expiresAt: null` ⇒ 会话不过期（把变量收敛到「落点」这一件事上）
    expires_at: 0,
    has_token: loggedIn,
  };
}

/**
 * 「登录成功」：走**真实的 store action**（`auth-service.login` 之外的链路全是真的），
 * 只有桥调用被替身化 —— 因此 `applySessionView` → `setAuthSnapshot` + `set()` → 接线订阅
 * 这一整条与真机同源。
 */
async function loginSucceeded(): Promise<void> {
  vi.spyOn(authService, 'login').mockResolvedValue(sessionViewOf(true));
  await act(async () => {
    await useAuthStore.getState().login('tester', 'secret', 60);
  });
}

/** 「登出」：等效于 `authService.logout` 成功后的渲染态（清登录态 + 清失效时间） */
async function logoutSucceeded(): Promise<void> {
  await act(async () => {
    setAuthSnapshot({ loggedIn: false, expiresAt: null });
    useAuthStore.setState({ isLoggedIn: false });
  });
}

/**
 * 冲干导航：数据路由的 `navigate` 是异步的，这里等到 router 的导航状态回到 `idle`
 * （内存路由无 loader，就是若干轮微任务）—— 保证断言读到的是**最终落点**而不是中间态。
 */
async function settleNavigation(): Promise<void> {
  for (let round = 0; round < 10; round += 1) {
    await act(async () => {
      for (let index = 0; index < 8; index += 1) await Promise.resolve();
    });
    if (router.state.navigation.state === 'idle') return;
  }
}

/** 最后一次**发起**的导航目标（"谁覆盖谁"的可判定形态：后发起者胜） */
function lastNavigateTarget(spy: { mock: { calls: unknown[][] } }): unknown {
  const calls = spy.mock.calls;
  return calls.length > 0 ? calls[calls.length - 1]?.[0] : undefined;
}

const renderLoginPage = () => render(<RouterProvider router={router} />);

beforeEach(async () => {
  // `restoreMocks: true` 会把模块级 `vi.fn()` 的实现清掉，这里显式补回（与既有门禁用例一致）
  detectAndRead.mockImplementation(async () => ({ items: [] }));

  resetOpenPathsQueue();
  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  setAuthSnapshot({ loggedIn: false, expiresAt: null });
  useAuthStore.setState({ isLoggedIn: false });

  if (router.state.location.pathname !== '/login') {
    await act(async () => {
      await router.navigate('/login', { replace: true });
    });
  }
});

afterEach(() => {
  cleanup();
  resetOpenPathsQueue();
  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  setAuthSnapshot({ loggedIn: false, expiresAt: null });
  useAuthStore.setState({ isLoggedIn: false });
  vi.restoreAllMocks();
});

describe('AC-64 ② 登录后的落点（唯一判据：有挂起唤起就必须揭示编辑器）', () => {
  it('①登录成功且**有**挂起的外部唤起 ⇒ 落点是 /editor（修复前本用例必红：被 LoginPage 的 navigate("/") 覆盖）', async () => {
    const path = 'D:/tmp/ac64-落点-有唤起.txt';

    // 真实链路：Python 推 `editor.openPaths`（未登录 ⇒ 队列挂起、零远端、零导航）
    bridgeMock.emit('editor.openPaths', { paths: [path], source: 'cli' });
    expect(pendingOpenPaths(), '前置：唤起请求确实挂起在队列里').toBe(1);
    expect(
      bridgeMock.listenerCount('editor.openPaths'),
      '前置：接线（消费者）必须早于登录存在',
    ).toBeGreaterThan(0);

    const view = renderLoginPage();
    expect(router.state.location.pathname, '前置：登录页在位').toBe('/login');
    const navigateSpy = vi.spyOn(router, 'navigate');

    await loginSucceeded();
    await settleNavigation();

    // ① 用户可见落点：编辑器工作区（AC-64 ②的观测点）
    expect(
      view.container.querySelector('[data-testid="editor-workspace"]'),
      'AC-64 ②：登录完成后必须直接揭示编辑器工作区',
    ).not.toBeNull();
    expect(
      view.container.querySelector('[data-testid="home-file-list"]'),
      'AC-64 ②：登录后的落点**不得**被"回首页"覆盖',
    ).toBeNull();

    // ② router 状态与「最后发起的导航」双重判据（后者与真机的覆盖语义同源）
    expect(router.state.location.pathname).toBe(EDITOR_ROUTE);
    expect(lastNavigateTarget(navigateSpy), '后发起的导航目标必须是 /editor').toBe(EDITOR_ROUTE);

    // ③ 挂起请求确实被兑现（不是"靠不兑现换来落点正确"）
    expect(pendingOpenPaths(), '兑现后队列清空').toBe(0);
  });

  it('②登录成功但**无**挂起唤起 ⇒ 落点仍是首页 /（防"一刀切都去编辑器"的回归）', async () => {
    expect(pendingOpenPaths()).toBe(0);

    const view = renderLoginPage();
    const navigateSpy = vi.spyOn(router, 'navigate');

    await loginSucceeded();
    await settleNavigation();

    expect(
      view.container.querySelector('[data-testid="home-file-list"]'),
      '非唤起场景必须仍然回首页',
    ).not.toBeNull();
    expect(view.container.querySelector('[data-testid="editor-workspace"]')).toBeNull();
    expect(router.state.location.pathname).toBe(HOME_ROUTE);
    expect(lastNavigateTarget(navigateSpy)).toBe(HOME_ROUTE);
  });

  it('③登出（门翻回假）⇒ 判据复位：再次登录且无唤起时落点回到 /', async () => {
    // 相位 1：有唤起 ⇒ 判据置位（落点 /editor）
    bridgeMock.emit('editor.openPaths', { paths: ['D:/tmp/ac64-落点-复位.txt'], source: 'cli' });
    const view = renderLoginPage();
    await loginSucceeded();
    await settleNavigation();
    expect(router.state.location.pathname, '前置：先发生一次"唤起 ⇒ 揭示编辑器"').toBe(EDITOR_ROUTE);
    expect(pendingOpenPaths()).toBe(0);

    // 相位 2：登出（门从 true 翻回 false）
    await logoutSucceeded();

    // 相位 3：回到登录页，**没有**新的唤起请求 ⇒ 再次登录必须落回首页
    await act(async () => {
      await router.navigate('/login', { replace: true });
    });
    const navigateSpy = vi.spyOn(router, 'navigate');
    await loginSucceeded();
    await settleNavigation();

    expect(
      view.container.querySelector('[data-testid="home-file-list"]'),
      '判据未复位 ⇒ 会把"上一次唤起"的错误落点带给本次普通登录',
    ).not.toBeNull();
    expect(router.state.location.pathname).toBe(HOME_ROUTE);
    expect(lastNavigateTarget(navigateSpy)).toBe(HOME_ROUTE);
  });
});

describe('真实路由图守门（R2 消环后仍留）：LoginPage 只依赖叶子 editor-landing，路由图可完整求值', () => {
  it('④真实路由图能被完整求值（不得在模块求值期抛 TDZ / 循环依赖崩溃）', async () => {
    // 本文件其余用例把 `config/routes` 替身化成内存路由，因此**真实**路由图未被求值过；
    // 这里用 `importActual` 走一遍真实图，确保它在"模块求值期"是安全的。
    // 历史：R1 的回修曾让 LoginPage 反向 import `editor-open-paths`（后者 import `config/routes`）
    // 造成环；R2（Phase 6 收尾轮）已把落点判据搬到**零 import** 的叶子 `editor-landing.ts`，
    // 环已消失 —— 本用例保留为**真实路由图**的冒烟守门（防环的静态扫描另见
    // `utils/scratch/__tests__/editor-landing.test.ts` 的用例 ②③）。
    const actualRoutes = await vi.importActual<typeof import('../../config/routes')>(
      '../../config/routes',
    );

    expect(actualRoutes.router, '真实 hash 路由必须构造成功').toBeDefined();
    expect(typeof actualRoutes.router.navigate, '落点导航能力仍在').toBe('function');
    // 真图里 `/editor` 仍挂在登录门内（AC-63 的准入不因回修而松动）
    const editorRoute = actualRoutes.router.routes
      .flatMap((route) => route.children ?? [])
      .find((child) => child.path === 'editor');
    expect(editorRoute, '真实路由图必须仍然登记 /editor').toBeDefined();
  });
});

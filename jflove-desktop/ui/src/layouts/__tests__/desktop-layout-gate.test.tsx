/**
 * 侧栏「文本编辑器」入口的**准入守门** —— Phase 6（v1.6.1）
 *
 * 判据出处：
 *   - **AC-63 ①**（需求 `文档记录/需求文档/v1.6.1.md:468`）：在**未登录**（无有效会话）状态下
 *     「左侧主导航中**不存在**「文本编辑器」项（判据：**逐项检查菜单文本，0 命中**）」；
 *   - **设计 §4.1 消费点①**（`文档记录/设计文档/v1.6.1.md:423`）：`DesktopLayout` 的导航项渲染
 *     是 AC-63 的四处消费点之一，**缺一即 AC-63 失败**；取值必须走 `useEditorGate()` 这个
 *     **单一真相**（DS-16），不得自己再写一份 `isLoggedIn &&`；
 *   - **设计 §14.3 负向对照**（`:975`）：「把导航项改成"始终渲染" ⇒ 未登录零存在用例必须**失败**」；
 *   - **Phase 5 审查 M-3**（`文档记录/代码审查报告/v1.6.1.md:148-155`）：该 AC 的**真实落点**
 *     （`layouts/DesktopLayout.tsx:72-73`）此前**不在**守门用例的扫描面内（`TS_CHAIN_GLOBS` 不含
 *     `ui/src/layouts/**`）⇒「把它改回自己写 `isLoggedIn &&` 也不会红」。本文件补上**运行时**判据。
 *
 * ## 判别力自证（两条，任一改动都会让本文件变红）
 *
 *   ① 删掉 `DesktopLayout.tsx:73` 的 `NAV_ITEMS.filter((item) => !item.requiresEditor || canUseEditor)`
 *      （改成 `NAV_ITEMS` 全量渲染）⇒ 第 1 / 第 3 条用例（未登录 / 会话过期）必红；
 *   ② 把 `useEditorGate()` 换成直接的 `useAuthStore((s) => s.isLoggedIn)` ⇒ **第 3 条**（会话过期）
 *      必红 —— 它专门锁定"准入判据必须含**未过期**这一半"，而 `isLoggedIn` 在过期场景仍为真。
 *
 * ## 分层声明与安全
 *
 * 只断言**渲染层可观察行为**（导航项文本 / 路由落点 / `data-active`）。这里刻意**不**替换
 * `config/routes.tsx`：要验的正是"点入口 ⇒ 路由落到 `/editor`"，用真实路由组件才能守住接线；
 * 但为了不把未交付页面的桥调用拖进 jsdom，测内自建 `createMemoryRouter`（只挂被测布局与占位子路由）。
 * `utils/desktop-bridge` 被替身化：**不发任何真实网络请求**；夹具中**没有** token / session_key（§9）。
 */
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../utils/desktop-bridge', () => ({
  callBridge: vi.fn(async () => ({ ok: true })),
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
  callShell: vi.fn(async () => ({ ok: true })),
}));

import { DesktopLayout } from '../DesktopLayout';
import { useAuthStore } from '../../stores/auth-store';
import { setAuthSnapshot } from '../../utils/session';

/** 「文本编辑器」入口的可见文本（AC-63① 的原文判据单位就是**菜单文本**） */
const EDITOR_LABEL = '文本编辑器';

/** 未登录 / 过期场景下必须仍然存在的其它主导航项（正对照，防"布局整体没渲染"造成假绿） */
const OTHER_LABELS = ['文件管理', '笔记管理', '云盘挂载', '传输任务', '安全状态', '设置'];

/**
 * 用真实 `DesktopLayout` + 内存路由渲染侧栏。
 *
 * 子路由全部是**占位 div**：本文件的判据是"入口可见性 + 路由落点"，不需要真实页面
 * （真实页面在 jsdom 里会发起一堆与本判据无关的桥调用）。
 */
function renderLayout(initialPath = '/files') {
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: <DesktopLayout />,
        children: [
          { index: true, element: <div data-testid="home-page" /> },
          { path: 'files', element: <div data-testid="files-page" /> },
          { path: 'editor', element: <div data-testid="editor-page" /> },
          { path: 'notes', element: <div data-testid="notes-page" /> },
          { path: 'sync', element: <div data-testid="sync-page" /> },
          { path: 'transfer', element: <div data-testid="transfer-page" /> },
          { path: 'security', element: <div data-testid="security-page" /> },
          { path: 'settings', element: <div data-testid="settings-page" /> },
        ],
      },
    ],
    { initialEntries: [initialPath] },
  );
  const view = render(<RouterProvider router={router} />);
  return { ...view, router };
}

/** 逐项取主导航项的文本（AC-63① 的判定动作） */
function navItemTexts(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('.nav-item')).map((node) => node.textContent ?? '');
}

/** 逐项取主导航项元素 */
function navItemElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>('.nav-item'));
}

/** 断言"「文本编辑器」在导航里零命中"，并给出正对照（其它入口确实在） */
function expectEditorEntryAbsent(container: HTMLElement): void {
  const texts = navItemTexts(container);
  // 原文判据：**逐项检查菜单文本，0 命中**
  expect(texts.length).toBeGreaterThan(0);
  expect(texts.filter((text) => text.includes(EDITOR_LABEL))).toHaveLength(0);
  // 更强的形态：整张侧栏（含 tooltip / aria 文案）都不出现该字样
  expect(container.textContent ?? '').not.toContain(EDITOR_LABEL);
  for (const label of OTHER_LABELS) {
    expect(texts.some((text) => text.includes(label))).toBe(true);
  }
}

beforeEach(() => {
  setAuthSnapshot({ expiresAt: null });
  useAuthStore.setState({ isLoggedIn: false, username: null, role: null, isAdmin: false });
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ isLoggedIn: false, username: null, role: null, isAdmin: false });
  setAuthSnapshot({ expiresAt: null });
  vi.restoreAllMocks();
});

// ── 用例 ──────────────────────────────────────────────────────────────────────────────

describe('AC-63① 未登录时导航项零存在', () => {
  it('AC-63①：未登录 ⇒ 主导航逐项文本中「文本编辑器」0 命中，其它入口照常（设计 §14.3 负向对照）', () => {
    const { container } = renderLayout();

    // 前置：准入为假
    expect(useAuthStore.getState().isLoggedIn).toBe(false);
    expectEditorEntryAbsent(container);
  });

  it('AC-63①：会话过期（isLoggedIn 仍为 true）⇒ 同样 0 命中（锁定"未过期"这一半判据）', () => {
    // 造"会话中途失效"：登录态没被清掉，但失效时间已过（`editor-gate.ts:59-62` 的口径）
    setAuthSnapshot({ expiresAt: Math.floor(Date.now() / 1000) - 60 });
    useAuthStore.setState({ isLoggedIn: true });
    const { container } = renderLayout();

    expect(useAuthStore.getState().isLoggedIn).toBe(true);
    // 把 `useEditorGate()` 换成裸 `isLoggedIn` ⇒ 本断言必红（这就是 DS-16 单一真相的判据）
    expectEditorEntryAbsent(container);
  });

  it('AC-63①：未登录时工具提示 / aria 属性里也不得残留该入口（防"文本隐藏但节点还在"）', () => {
    const { container } = renderLayout();
    // `title` 只在折叠态出现（DesktopLayout.tsx:83），这里两种形态都扫：任何属性值都不得含该字样
    const attributeValues = Array.from(container.querySelectorAll('*')).flatMap((node) =>
      Array.from(node.attributes).map((attribute) => attribute.value),
    );
    expect(attributeValues.some((value) => value.includes(EDITOR_LABEL))).toBe(false);
  });
});

describe('AC-63① 已登录时入口出现且点击落到 /editor', () => {
  it('AC-63①：已登录 ⇒ 入口出现；点击后路由落到 `/editor`（面板挂上、导航项 data-active=true）', async () => {
    setAuthSnapshot({ expiresAt: null });
    useAuthStore.setState({ isLoggedIn: true, username: 'tester', role: 'user', isAdmin: false });
    const { container, router } = renderLayout('/files');

    const editorItem = navItemElements(container).find((node) =>
      (node.textContent ?? '').includes(EDITOR_LABEL),
    );
    expect(editorItem).not.toBeUndefined();
    // 起点不在编辑器：占位页是文件管理，入口未选中
    expect(container.querySelector('[data-testid="files-page"]')).not.toBeNull();
    expect(editorItem?.getAttribute('data-active')).toBe('false');

    await act(async () => {
      fireEvent.click(editorItem as HTMLElement);
    });

    // 路由真的落到 `/editor`（内存路由的 pathname 是权威观测点）
    expect(router.state.location.pathname).toBe('/editor');
    expect(container.querySelector('[data-testid="editor-page"]')).not.toBeNull();
    // 侧栏选中态同步（AC-69②「左侧菜单的选中项仍是文本编辑器」的同一观测点）
    expect(editorItem?.getAttribute('data-active')).toBe('true');
    expect(editorItem?.getAttribute('aria-current')).toBe('page');
  });

  it('AC-63① / AC-72①：登录态翻转为失效后，入口从**已渲染**的侧栏上消失（不只是首帧不渲染）', () => {
    useAuthStore.setState({ isLoggedIn: true });
    const { container } = renderLayout();
    expect(navItemTexts(container).some((text) => text.includes(EDITOR_LABEL))).toBe(true);

    // 登出 / 会话失效 ⇒ 入口必须立刻消失（DS-16 的订阅式消费，而不是"全靠组件恰好没挂载"）
    act(() => {
      useAuthStore.setState({ isLoggedIn: false });
    });
    expectEditorEntryAbsent(container);
  });
});

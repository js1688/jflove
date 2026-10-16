/**
 * 标签条（`ScratchTabBar`）守门用例 —— Phase 6（v1.6.1）
 *
 * 判据出处：
 *   - **AC-67 ①②③**（需求 `文档记录/需求文档/v1.6.1.md:472`）：连续打开 **12 个**标签页 +
 *     标签条放不下 ⇒ ① 出现**可滚动或下拉列表入口**；② 通过该入口**能定位并选中第 12 个**，
 *     且编辑面板显示其内容；③ 首选中的标签页**始终可见**（不出现"激活了但看不见"）；
 *   - **AC-3 ①④**（`:485` 改写版）：关闭（脏标签先询问，见 AC-59/AC-70）、关闭后该标签从标签条
 *     消失且其它标签不受影响；
 *   - **DOM 契约**（设计 `文档记录/设计文档/v1.6.1.md:441-449`）：`data-scratch-tab="<tabId>"`、
 *     激活项额外带 `data-scratch-tab-active="true"`、面板锚点 `data-editor-panel(-active)`；
 *   - **§4.6 标签条交互**（`:542-554`）：溢出入口**常驻右侧**（下拉为可定位入口）、
 *     `scrollIntoView({ inline: 'nearest' })`、**键盘可达**（`Enter` / `Space` 激活）；
 *   - **Phase 5 回修 M-2**（`文档记录/代码审查报告/v1.6.1.md:138-146` + 附录 A.3.4 `:582-586`）：
 *     外层标签由原生 `<button>` 改为 `div[role="tab"]` + `tabIndex=0` + **手写** `Enter`/`Space`
 *     激活（div 不会由浏览器合成 click），`×` 是其内**独立** `<button>`（内容模型合法）。
 *   - **P1 纯键盘关闭路径**（用户裁决 V-1，2026-10-15 收尾轮）：标签本体 `onKeyDown` 增加
 *     `Delete` ⇒ `onRequestClose(tab.id)`（与 `×` **同一个**回调，脏标签仍落 `pendingCloseId`）。
 *     它是**组件内**键盘路径：**不进** `SHORTCUT_DEFS` / 设置页、**不可改键**；`×` 保持
 *     `tabIndex={-1}`（放开会给每标签多一个 Tab 停靠点）；**不**把 `pane.close`(`Ctrl+W`)
 *     的面板作用域放大到工作区（与宿主窗口关闭语义冲突）。
 *
 * ## jsdom 边界（如实声明）
 *
 * jsdom **没有布局引擎**：`getBoundingClientRect()` 恒为 0、`overflow-x` 不产生真实裁剪。
 * 因此"标签条放不下"这一**几何前提**在本文件里不可观测（真机项见设计 §13.3 第 15 条）。
 * 本文件断言的是它的**可判定替代**：
 *   ① 溢出入口**常驻**且位于滚动容器**之外**（DS-36：不被裁剪、不随标签滚动而消失）；
 *   ② 入口打开后能列出**全量 12 项**并与 store 顺序一致（"能定位第 12 个"的可操作形态）；
 *   ③ 点第 12 项后激活项 + 面板锚点 + `scrollIntoView` 三处证据同时成立（AC-67 ②③）。
 *
 * ## 分层声明与安全
 *
 * `ScratchEditorHost`（CM6 宿主）与 `utils/desktop-bridge` 均为替身：不装载真实编辑器内核、
 * 不发任何真实网络请求；夹具中**没有** token / session_key（AGENTS.md §9）。
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

import { ScratchTabBar } from '../ScratchTabBar';
import { ScratchWorkspace } from '../ScratchWorkspace';
import { DEFAULT_BINDINGS, SHORTCUT_DEFS } from '../../../config/shortcuts';
import { scratchService } from '../../../services/scratch-service';
import { useAuthStore } from '../../../stores/auth-store';
import { useScratchStore } from '../../../stores/scratch-store';
import { setAuthSnapshot } from '../../../utils/session';

/** 12 个标签（AC-67 的原文数量） */
const TAB_COUNT = 12;

/** 冲干渲染 → effect 的异步链 */
async function flushEffects(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

/** 在 store 里连续建 N 个标签（`createTab` 每次追加并激活），返回 id 列表（顺序 = 标签条顺序） */
function createTabs(count: number, prefix = '标签'): string[] {
  const ids: string[] = [];
  act(() => {
    for (let i = 0; i < count; i += 1) {
      ids.push(useScratchStore.getState().createTab({ title: `${prefix}${i + 1}` }));
    }
  });
  return ids;
}

/** 取某个标签的元素（DOM 契约锚点） */
function tabEl(tabId: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-scratch-tab="${tabId}"]`);
}

/**
 * 建一个「脏且带保存路径」的标签。
 *
 * 刻意**不**走 `markDirty`：那会排一个 1500 ms 的防抖暂存定时器，跨用例触发无意义的桥调用；
 * 直接在建标签时置 `dirty`（`createTab` 接受 `Partial<ScratchTab>`）语义等价且无副作用。
 */
function createDirtyTab(title: string, path: string): string {
  let id = '';
  act(() => {
    id = useScratchStore.getState().createTab({ title, path, dirty: true });
  });
  return id;
}

// ── `scrollIntoView` 探针（AC-67③）────────────────────────────────────────────────────
//
// jsdom 不实现 `scrollIntoView`（`ScratchTabBar.tsx:154` 对此有显式容错）；
// 这里装一个可断言的替身：记录"是谁被滚进可视区"，从而把 AC-67③ 变成可判定断言。
const scrollTargets: Element[] = [];
let originalScrollIntoView: typeof Element.prototype.scrollIntoView | undefined;

beforeEach(() => {
  // 准入为真（AC-63 的正面分支）：已登录 + 会话未过期
  setAuthSnapshot({ expiresAt: null });
  useAuthStore.setState({ isLoggedIn: true });
  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  vi.spyOn(scratchService, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
  vi.spyOn(scratchService, 'draftDiscard').mockResolvedValue({ ok: true });

  originalScrollIntoView = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = function scrollIntoViewStub(this: Element) {
    scrollTargets.push(this);
  };
  scrollTargets.length = 0;
});

afterEach(() => {
  if (originalScrollIntoView) Element.prototype.scrollIntoView = originalScrollIntoView;
  else Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
  scrollTargets.length = 0;
  cleanup();
  useAuthStore.setState({ isLoggedIn: false });
  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  vi.restoreAllMocks();
});

// ── 用例 ──────────────────────────────────────────────────────────────────────────────

describe('AC-67 标签溢出可及（12 标签 + 下拉入口）', () => {
  it('AC-67①②③：溢出入口常驻于滚动容器之外，可列出 12 项；点第 12 项 ⇒ 激活项/面板锚点/scrollIntoView 三证齐备', async () => {
    const view = render(createElement(ScratchWorkspace));
    await flushEffects();

    const ids = createTabs(TAB_COUNT);
    expect(ids).toHaveLength(TAB_COUNT);

    // 先把激活项挪回第 1 个，保证"点第 12 项"真的是一次**激活变化**（否则 effect 不会重跑）
    act(() => {
      useScratchStore.getState().setActiveTab(ids[0]);
    });
    scrollTargets.length = 0;

    const bar = view.container.querySelector('.scratch-tab-bar');
    const strip = view.container.querySelector('.scratch-tab-strip');
    const overflow = view.container.querySelector('[data-scratch-tab-overflow="true"]');
    expect(bar).not.toBeNull();
    expect(strip).not.toBeNull();
    expect(overflow).not.toBeNull();

    // ① 入口**常驻**且在**滚动容器之外**（DS-36：滚动容器会裁剪绝对定位后代 ⇒ 入口必须在外）
    //    这是"标签条放不下时仍能操作"的结构保证（jsdom 无布局，几何前提见文件头声明）
    expect(strip?.querySelectorAll('[data-scratch-tab]')).toHaveLength(TAB_COUNT);
    expect(strip?.contains(overflow as Node)).toBe(false);
    // 「窄容器」：显式把可视宽度压到远小于 12 个标签，表达用例意图（jsdom 不参与布局）
    if (strip) (strip as HTMLElement).style.width = '120px';

    // ② 打开下拉：全量 12 项，顺序与 store 一致
    act(() => {
      fireEvent.click(overflow as HTMLElement);
    });
    const items = Array.from(view.container.querySelectorAll('[data-scratch-overflow-item]'));
    expect(items).toHaveLength(TAB_COUNT);
    expect(items.map((node) => node.getAttribute('data-scratch-overflow-item'))).toEqual(ids);

    // ③ 点第 12 项 ⇒ 激活项 = 第 12 项
    const item12 = view.container.querySelector(`[data-scratch-overflow-item="${ids[TAB_COUNT - 1]}"]`);
    expect(item12).not.toBeNull();
    act(() => {
      fireEvent.click(item12 as HTMLElement);
    });

    expect(useScratchStore.getState().activeTabId).toBe(ids[TAB_COUNT - 1]);
    // 面板可见：`[data-editor-panel-active="true"]` 的 id 与激活项一致（AC-66/AC-67② 的观测点）
    const activePanel = view.container.querySelector('[data-editor-panel-active="true"]');
    expect(activePanel?.getAttribute('data-editor-panel')).toBe(ids[TAB_COUNT - 1]);
    // 标签条上的激活标记同步（`data-scratch-tab-active`）
    expect(
      view.container.querySelector('[data-scratch-tab-active="true"]')?.getAttribute('data-scratch-tab'),
    ).toBe(ids[TAB_COUNT - 1]);
    // ③「首选中的标签页始终可见」：激活变化触发了 `scrollIntoView`，且对象正是第 12 个标签
    //    （`ScratchTabBar.tsx:147-157`；去掉这段 effect ⇒ 本断言必红）
    expect(scrollTargets.length).toBeGreaterThan(0);
    expect(scrollTargets[scrollTargets.length - 1]?.getAttribute('data-scratch-tab')).toBe(
      ids[TAB_COUNT - 1],
    );
    // 点选后菜单自行关闭（`ScratchTabOverflowMenu` 的 onClose 契约）
    expect(view.container.querySelectorAll('[data-scratch-overflow-item]')).toHaveLength(0);
  });

  it('AC-67①：入口 aria 语义正确（aria-haspopup=menu，展开态 aria-expanded 跟随）', async () => {
    act(() => {
      createTabs(3);
    });
    const view = render(createElement(ScratchTabBar, { onRequestClose: vi.fn() }));
    const overflow = view.container.querySelector('[data-scratch-tab-overflow="true"]');
    expect(overflow?.getAttribute('aria-haspopup')).toBe('menu');
    expect(overflow?.getAttribute('aria-expanded')).toBe('false');

    act(() => {
      fireEvent.click(overflow as HTMLElement);
    });
    expect(overflow?.getAttribute('aria-expanded')).toBe('true');
    expect(view.container.querySelectorAll('[data-scratch-overflow-item]')).toHaveLength(3);
  });
});

describe('AC-3 关闭按钮：只关闭、不激活（stopPropagation 生效）', () => {
  it('AC-3①：点非激活标签的 × ⇒ 关闭请求发出、激活项**不变**（去掉 × 的 stopPropagation 必红）', () => {
    const ids = createTabs(3);
    act(() => {
      useScratchStore.getState().setActiveTab(ids[0]);
    });
    const onRequestClose = vi.fn();
    const view = render(createElement(ScratchTabBar, { onRequestClose }));

    const closeOfTab2 = view.container.querySelector(
      `[data-scratch-tab="${ids[1]}"] .scratch-tab-close`,
    );
    expect(closeOfTab2).not.toBeNull();

    // `fireEvent.click` 的 `detail === 0`（等价于辅助技术 / 程序化 click）—— 正是标签外层
    // `onClick` 的补激活分支（`ScratchTabBar.tsx:306-310`）的触发条件：
    // 若 `×` 不 `stopPropagation`，事件冒泡到标签根 ⇒ 把**刚被关闭的那个**标签激活。
    act(() => {
      fireEvent.click(closeOfTab2 as HTMLElement);
    });

    expect(onRequestClose).toHaveBeenCalledTimes(1);
    expect(onRequestClose).toHaveBeenCalledWith(ids[1]);
    expect(useScratchStore.getState().activeTabId).toBe(ids[0]);
    expect(
      view.container.querySelector('[data-scratch-tab-active="true"]')?.getAttribute('data-scratch-tab'),
    ).toBe(ids[0]);
    // 关闭按钮本身不是可聚焦的 tab 停靠点（键盘路径统一走 Ctrl+W / 未来菜单）
    expect((closeOfTab2 as HTMLButtonElement).tabIndex).toBe(-1);
  });

  it('AC-3④：工作区里关掉非激活的干净标签 ⇒ 该标签消失、其它标签与激活项不受影响', () => {
    const ids = createTabs(3);
    act(() => {
      useScratchStore.getState().setActiveTab(ids[0]);
    });
    const view = render(createElement(ScratchWorkspace));

    const closeOfTab2 = view.container.querySelector(
      `[data-scratch-tab="${ids[1]}"] .scratch-tab-close`,
    );
    act(() => {
      fireEvent.click(closeOfTab2 as HTMLElement);
    });

    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([ids[0], ids[2]]);
    expect(useScratchStore.getState().activeTabId).toBe(ids[0]);
    expect(view.container.querySelectorAll('[data-scratch-tab]')).toHaveLength(2);
  });
});

describe('AC-66 键盘可达 + DOM 契约（Phase 5 M-2 回修后的形态）', () => {
  it('M-2 回修：标签是 div[role="tab"] + tabIndex=0，且**不存在** button 嵌套 button', () => {
    const ids = createTabs(3);
    const view = render(createElement(ScratchTabBar, { onRequestClose: vi.fn() }));

    // HTML 内容模型：interactive content 不得嵌套 ⇒ 外层不能是 <button>（回修前是）
    expect(view.container.querySelectorAll('button button')).toHaveLength(0);
    for (const id of ids) {
      const element = tabEl(id);
      expect(element).not.toBeNull();
      expect(element?.tagName).toBe('DIV');
      expect(element?.getAttribute('role')).toBe('tab');
      expect(element?.getAttribute('tabindex')).toBe('0');
      // 关闭控件仍是**独立** <button>（内容模型合法）
      expect(element?.querySelector('button.scratch-tab-close')?.tagName).toBe('BUTTON');
    }
  });

  it('AC-66 / §4.6：Enter 与 Space 都能激活（div 不会由浏览器合成 click ⇒ 必须手写）', () => {
    const ids = createTabs(3);
    const view = render(createElement(ScratchTabBar, { onRequestClose: vi.fn() }));
    expect(useScratchStore.getState().activeTabId).toBe(ids[2]);

    // Enter 激活第 3 个之外的标签
    act(() => {
      fireEvent.keyDown(tabEl(ids[0]) as HTMLElement, { key: 'Enter' });
    });
    expect(useScratchStore.getState().activeTabId).toBe(ids[0]);

    // Space 再激活另一个（键值是单个空格 `' '`）
    act(() => {
      fireEvent.keyDown(tabEl(ids[1]) as HTMLElement, { key: ' ' });
    });
    expect(useScratchStore.getState().activeTabId).toBe(ids[1]);

    // `aria-selected` 随激活项变化，且只有激活项带 `data-scratch-tab-active`
    expect(tabEl(ids[0])?.getAttribute('aria-selected')).toBe('false');
    expect(tabEl(ids[1])?.getAttribute('aria-selected')).toBe('true');
    expect(view.container.querySelectorAll('[data-scratch-tab-active="true"]')).toHaveLength(1);
    expect(
      view.container.querySelector('[data-scratch-tab-active="true"]')?.getAttribute('data-scratch-tab'),
    ).toBe(ids[1]);
  });
});

describe('P1 纯键盘关闭路径：标签本体上按 Delete（V-1 裁决后的键盘可达方案）', () => {
  it('P1①：焦点在干净标签上按 Delete ⇒ 该标签被关闭，其它标签与激活项不受影响', () => {
    const ids = createTabs(3);
    act(() => {
      useScratchStore.getState().setActiveTab(ids[0]);
    });
    const view = render(createElement(ScratchWorkspace));

    act(() => {
      fireEvent.keyDown(tabEl(ids[1]) as HTMLElement, { key: 'Delete' });
    });

    // 干净标签 ⇒ `requestCloseTab` 走"立即关闭"分支（无询问，AC-59 的"无需询问"一侧）
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([ids[0], ids[2]]);
    expect(useScratchStore.getState().activeTabId).toBe(ids[0]); // 关的不是激活项 ⇒ 激活项不动
    expect(useScratchStore.getState().pendingCloseId).toBeNull();
    expect(tabEl(ids[1])).toBeNull();
    expect(view.container.querySelectorAll('[data-scratch-tab]')).toHaveLength(2);
  });

  it('P1②：焦点在脏标签上按 Delete ⇒ 不直接关闭，而是发出关闭请求（落到 pendingCloseId），标签仍在', () => {
    const ids = createTabs(1);
    const dirtyId = createDirtyTab('脏标签', 'D:/tmp/p1-dirty.txt');

    // ── 第一段：组件级证据 —— 请求以**该标签 id 原样**交给 `onRequestClose`（与 `×` 同一个回调）──
    const onRequestClose = vi.fn();
    const bar = render(createElement(ScratchTabBar, { onRequestClose }));
    act(() => {
      fireEvent.keyDown(tabEl(dirtyId) as HTMLElement, { key: 'Delete' });
    });
    expect(onRequestClose).toHaveBeenCalledTimes(1);
    expect(onRequestClose).toHaveBeenCalledWith(dirtyId);
    // 组件**不自己关**标签、**不自己造**询问框：脏标签的处置权在工作区（AC-59）
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([ids[0], dirtyId]);
    bar.unmount();

    // ── 第二段：工作区级证据 —— 真实接线是 store 的 `requestCloseTab`，脏标签只置位不关闭 ──
    const view = render(createElement(ScratchWorkspace));
    act(() => {
      fireEvent.keyDown(tabEl(dirtyId) as HTMLElement, { key: 'Delete' });
    });
    expect(useScratchStore.getState().pendingCloseId).toBe(dirtyId);
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([ids[0], dirtyId]);
    expect(tabEl(dirtyId)).not.toBeNull();
    // 询问框真的挂出来了（保存 / 放弃修改 / 取消 三分支，`ScratchWorkspace.tsx:189-232`）
    expect(view.container.textContent).toContain('有未保存的修改');
  });

  it('P1③：Delete 会 preventDefault（压掉宿主/浏览器默认动作），且这条路径不需要 Ctrl+W 先被接管', () => {
    const ids = createTabs(2);
    act(() => {
      useScratchStore.getState().setActiveTab(ids[0]);
    });
    // 刻意渲染**裸**标签条：标签本体**不在**任何 `[data-editor-panel]` 内 —— 这正是
    // `pane.close`(`Ctrl+W`) 所属"面板作用域"运行时**刻意放行给宿主**的上下文
    // （`utils/scratch/shortcut-runtime.ts:155-158`）。`Delete` 在这个上下文里照样生效
    // ⇒ 它由标签自身的 `onKeyDown` 承办，与 `Ctrl+W` 是否被接管无关。
    const view = render(
      createElement(ScratchTabBar, {
        onRequestClose: (id: string) => useScratchStore.getState().requestCloseTab(id),
      }),
    );
    expect(view.container.querySelector('[data-editor-panel]')).toBeNull();

    // `fireEvent` 的返回值 = `!defaultPrevented` ⇒ `false` 即"处理器确实调了 preventDefault"
    const notCancelled = fireEvent.keyDown(tabEl(ids[1]) as HTMLElement, { key: 'Delete' });
    expect(notCancelled).toBe(false);
    // 而且动作真的落到了 store（干净标签直接关闭）
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([ids[0]]);
  });

  it('P1④ 回归：Tab 停靠点数量不因本次改动增加（标签本体仍 0、× 仍 -1），且 Delete 未进键位表', () => {
    const ids = createTabs(3);
    const view = render(createElement(ScratchTabBar, { onRequestClose: vi.fn() }));

    // 标签本体是停靠点（`tabIndex=0`）；`×` **不是**（`tabIndex=-1`）—— 与改动前逐条一致
    for (const id of ids) {
      expect(tabEl(id)?.getAttribute('tabindex')).toBe('0');
    }
    const closeButtons = Array.from(
      view.container.querySelectorAll<HTMLElement>('.scratch-tab-close'),
    );
    expect(closeButtons).toHaveLength(ids.length);
    for (const button of closeButtons) expect(button.tabIndex).toBe(-1);

    // 停靠点总数 = 3 个标签本体 + 「新建标签页」+「全部标签页」两个常驻按钮（= 5）
    // `Delete` 只是标签本体 `onKeyDown` 的一个分支，**没有**新增任何可聚焦元素
    const stops = Array.from(
      view.container.querySelectorAll<HTMLElement>('button, [role="tab"]'),
    ).filter((element) => element.tabIndex >= 0);
    expect(stops).toHaveLength(ids.length + 2);

    // `Delete` 是**组件内**键盘路径：不进 `SHORTCUT_DEFS` ⇒ 不出现在设置页、**不可改键**
    expect(SHORTCUT_DEFS.some((def) => String(def.defaultKey).includes('Delete'))).toBe(false);
    // 也没有借机改掉 `pane.close` 的默认键（V-1：作用域与默认键都不动）
    expect(DEFAULT_BINDINGS['pane.close']).toBe('Ctrl+W');
  });
});

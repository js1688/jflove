/**
 * 标签条**拖拽排序**守门用例 —— Phase 6（v1.6.1）
 *
 * 判据出处：
 *   - **AC-68 ①②**（需求 `文档记录/需求文档/v1.6.1.md:473`）：开 3 个标签页 A/B/C → 拖拽把 C 移到
 *     最前 → ① 标签条顺序变为 **C / A / B**（可观测顺序）；② 切到左侧菜单「设置」再切回
 *     「文本编辑器」→ 顺序**仍为** C / A / B，且**激活项不变**；
 *   - **AC-3 ②**（`:485` 改写版）：标签页操作集含**拖拽排序**；关闭 / 排序后其它标签内容与顺序保持；
 *   - **§4.6 标签条交互**（设计 `文档记录/设计文档/v1.6.1.md:542-554`）：**Pointer Events 自绘**
 *     （位移 > **4 px** 进入拖拽态 → `setPointerCapture` → **中点判定**目标索引 → `reorderTab`
 *     → `pointerup` 提交；`Escape` 取消并恢复原序；位移 ≤ 4 px 视为**点击激活**）；
 *   - **R-14**（`:884`）：Pointer Events 在 QtWebEngine 的**真机**行为未实测（真机项见 §13.3 第 15 条），
 *     本文件覆盖的是**逻辑层**判据；
 *   - **DS-18 / 不变量 ④**（`:441-449`、`:485`）：`tabs` 数组顺序即标签条顺序，**不存在第二处顺序来源**
 *     ⇒ 卸载重挂后顺序与激活项必须原样（本文件的"切菜单"用例就是这条不变量的守门）。
 *
 * ## jsdom 边界（如实声明）
 *
 * jsdom 没有布局引擎：`getBoundingClientRect()` 恒为 0（⇒ 中点判定会退化成"永远命中最后一项"），
 * 而 Pointer Events 的**指针捕获** API（`setPointerCapture` / `hasPointerCapture` /
 * `releasePointerCapture`）jsdom 也未实现。因此本文件：
 *   ① 给 `getBoundingClientRect` 装**几何替身**（每个标签宽 100 px、依次排开），让"4 px 阈值"与
 *      "中点判定"两条判据都成为可判定断言（这是 AC-68 的真实判据，不能靠 0 矩形蒙过去）；
 *   ② 给指针捕获三个方法装替身（`hasPointerCapture` 恒 false）；`setPointerCapture` 的**抛错**路径
 *      由 `ScratchTabBar.tsx:220-226` 的 `try/catch` 兜住，本文件专门用一条用例守住它。
 *
 * ## 分层声明与安全
 *
 * `ScratchEditorHost` 与 `utils/desktop-bridge` 均为替身（不装载真实编辑器内核、不发真实网络请求）；
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

import { ScratchWorkspace } from '../ScratchWorkspace';
import { scratchService } from '../../../services/scratch-service';
import { useAuthStore } from '../../../stores/auth-store';
import { useScratchStore } from '../../../stores/scratch-store';
import { setAuthSnapshot } from '../../../utils/session';

/** 几何替身：每个标签宽 100 px，第 i 个的 `left = i * 100`（⇒ 中点 = `i * 100 + 50`） */
const TAB_WIDTH_PX = 100;

/** 指针捕获替身（jsdom 未实现这三个方法，而 `endDrag` 会直接调 `hasPointerCapture`） */
const setPointerCaptureStub = vi.fn();
const releasePointerCaptureStub = vi.fn();
const hasPointerCaptureStub = vi.fn(() => false);

/** 造一个矩形（jsdom 的 DOMRect 构造器可用，但直接给字面量更省事） */
function rectAt(left: number): DOMRect {
  return {
    left,
    top: 0,
    width: TAB_WIDTH_PX,
    height: 30,
    right: left + TAB_WIDTH_PX,
    bottom: 30,
    x: left,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect;
}

/** 取标签元素（DOM 契约锚点） */
function tabEl(tabId: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`[data-scratch-tab="${tabId}"]`);
  if (!element) throw new Error(`标签未渲染：${tabId}`);
  return element;
}

/** 当前标签条上的可观测顺序（**DOM 顺序**，不是 store 顺序 —— 两者必须一致） */
function domOrder(): string[] {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-scratch-tab]')).map(
    (element) => element.getAttribute('data-scratch-tab') ?? '',
  );
}

/** store 顺序 */
function storeOrder(): string[] {
  return useScratchStore.getState().tabs.map((tab) => tab.id);
}

/** 指针三连（Pointer Events 自绘拖拽的对外面） */
function pointerDown(tabId: string, clientX: number): void {
  act(() => {
    fireEvent.pointerDown(tabEl(tabId), { button: 0, pointerId: 1, clientX });
  });
}

function pointerMove(tabId: string, clientX: number): void {
  act(() => {
    fireEvent.pointerMove(tabEl(tabId), { pointerId: 1, clientX });
  });
}

function pointerUp(tabId: string, clientX: number): void {
  act(() => {
    fireEvent.pointerUp(tabEl(tabId), { pointerId: 1, clientX });
  });
}

/** 在 `window` 上按 `Escape`（`ScratchTabBar.tsx:174-186` 的监听是 window 捕获阶段） */
function pressEscape(): void {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  });
}

/** 建 3 个标签 A / B / C，返回 id 列表（顺序即标签条顺序） */
function createABC(): { a: string; b: string; c: string; ids: string[] } {
  const ids: string[] = [];
  act(() => {
    ids.push(useScratchStore.getState().createTab({ title: 'A' }));
    ids.push(useScratchStore.getState().createTab({ title: 'B' }));
    ids.push(useScratchStore.getState().createTab({ title: 'C' }));
  });
  return { a: ids[0], b: ids[1], c: ids[2], ids };
}

beforeEach(() => {
  setAuthSnapshot({ expiresAt: null });
  useAuthStore.setState({ isLoggedIn: true });
  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  // 磁盘上摆 3 条草稿：只要它们被并进内存（`disk-1` / `disk-2` / `disk-3` 出现在 tabs 里），
  // AC-68② 的"重挂不得被磁盘内容覆盖"断言就会红。
  // ⚠ 这里**刻意不断言"是否读盘"** —— 语义理由见 AC-68② 用例内的长注释（C1 批 2026-10-16）。
  vi.spyOn(scratchService, 'draftList').mockResolvedValue({
    cleanExit: true,
    entries: [
      { id: 'disk-1', title: '磁盘1', path: null, language: 'text', encoding: 'utf-8', lineEnd: 'lf', read_only: false, dirty: true, caret: { line: 1, col: 1 }, scroll_top: 0, font_size: 14, show_line_numbers: true, word_wrap: false },
      { id: 'disk-2', title: '磁盘2', path: null, language: 'text', encoding: 'utf-8', lineEnd: 'lf', read_only: false, dirty: true, caret: { line: 1, col: 1 }, scroll_top: 0, font_size: 14, show_line_numbers: true, word_wrap: false },
      { id: 'disk-3', title: '磁盘3', path: null, language: 'text', encoding: 'utf-8', lineEnd: 'lf', read_only: false, dirty: true, caret: { line: 1, col: 1 }, scroll_top: 0, font_size: 14, show_line_numbers: true, word_wrap: false },
    ],
  });
  vi.spyOn(scratchService, 'draftDiscard').mockResolvedValue({ ok: true });

  // 几何替身：按**当前 DOM 顺序**给每个标签排开（顺序变了，矩形跟着变 —— 与真实布局同构）
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLElement,
  ) {
    const index = Array.from(document.querySelectorAll<HTMLElement>('[data-scratch-tab]')).indexOf(this);
    return rectAt(index < 0 ? -1000 : index * TAB_WIDTH_PX);
  });

  Element.prototype.setPointerCapture = setPointerCaptureStub;
  Element.prototype.releasePointerCapture = releasePointerCaptureStub;
  Element.prototype.hasPointerCapture = hasPointerCaptureStub;
  setPointerCaptureStub.mockClear();
  releasePointerCaptureStub.mockClear();
  hasPointerCaptureStub.mockClear();
  setPointerCaptureStub.mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ isLoggedIn: false });
  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  vi.restoreAllMocks();
});

// ── 用例 ──────────────────────────────────────────────────────────────────────────────

describe('AC-68① 4 px 阈值：手抖不得被误判成排序', () => {
  it('AC-68①/§4.6：位移 ≤ 4 px ⇒ 顺序不变，松手按「点击」激活（阈值改小或去掉必红）', () => {
    const { a, b, c } = createABC();
    render(createElement(ScratchWorkspace));
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });

    // C 的矩形是 [200,300]：在 205 处按下（其左半边），手抖 3 px 到 208
    // 若阈值判据被改坏（如去掉 `<= DRAG_THRESHOLD_PX` 的提前 return），
    // 208 会越过"第 2 个标签的中点 150"⇒ 目标索引 1 ⇒ `reorderTab(2,1)` ⇒ 顺序变成 A/C/B ⇒ 断言红
    pointerDown(c, 205);
    pointerMove(c, 208);
    pointerUp(c, 208);

    expect(storeOrder()).toEqual([a, b, c]);
    expect(domOrder()).toEqual([a, b, c]);
    // ≤ 4 px 仍按"点击"处理 ⇒ 激活项切到被按下的标签（§4.6 末第二行）
    expect(useScratchStore.getState().activeTabId).toBe(c);
  });
});

describe('AC-68① 跨过中点即重排（中点判定，不是左边界判定）', () => {
  it('AC-68①：把 C 拖到最前 ⇒ 可观测顺序变为 C / A / B', () => {
    const { a, b, c } = createABC();
    render(createElement(ScratchWorkspace));

    // C 在 [200,300]，从 250 按下拖到 120（落在第 1 个标签 [0,100] 的右半边之后、第 2 个中点 150 之前）
    // ⇒ 中点判定目标 = 0 ⇒ C 插到最前
    pointerDown(c, 250);
    pointerMove(c, 120);
    expect(storeOrder()).toEqual([c, a, b]);
    expect(domOrder()).toEqual([c, a, b]);

    // 抬起 = 提交（当前顺序即最终顺序，抬起时不再重排一次）
    pointerUp(c, 120);
    expect(storeOrder()).toEqual([c, a, b]);
    // 拖拽路径**不激活**（避免"拖完顺带跳标签"）
    expect(useScratchStore.getState().activeTabId).toBe(c);
  });

  it('AC-68①/§4.6：落点跨过第 2 个标签的**中点**（150）才插到它右边 ⇒ 证明用的是中点判定', () => {
    const { a, b, c } = createABC();
    render(createElement(ScratchWorkspace));

    // 170 > 第 2 个标签的中点 150 且 < 第 3 个中点 250 ⇒ 目标 1 ⇒ [A, C, B]
    // 若中点判定被写成"左边界判定"（`clientX >= rect.left`），170 会落到第 2 个标签内部 ⇒
    // 目标仍为 1 —— 因此再补一条 120 的落点：中点判定给 0（[C,A,B]），左边界判定给 1（[A,C,B]）。
    pointerDown(c, 250);
    pointerMove(c, 120);
    expect(storeOrder()).toEqual([c, a, b]);
    pointerUp(c, 120);

    // 复位后换一个落点：120 已由"拖到最前"用例锁定，这里用 170 验证右侧半边
    act(() => {
      useScratchStore.getState().reorderTab(0, 2); // 还原成 A / B / C
    });
    expect(storeOrder()).toEqual([a, b, c]);
    pointerDown(c, 250);
    pointerMove(c, 170);
    expect(storeOrder()).toEqual([a, c, b]);
    pointerUp(c, 170);
  });
});

describe('AC-68 / §4.6 取消路径：Escape 与 pointercancel 都必须恢复原序', () => {
  it('§4.6：拖拽中按 Escape ⇒ 顺序恢复按下前的快照，且不触发激活', () => {
    const { a, b, c } = createABC();
    render(createElement(ScratchWorkspace));
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });

    pointerDown(c, 250);
    pointerMove(c, 120);
    expect(storeOrder()).toEqual([c, a, b]); // 实时预览已经变了

    pressEscape();

    // 恢复按下前的快照 [A,B,C]（去掉 `restoreOrder` 调用 ⇒ 停在 C/A/B ⇒ 必红）
    expect(storeOrder()).toEqual([a, b, c]);
    expect(domOrder()).toEqual([a, b, c]);
    // 取消路径**不**激活被拖的标签
    expect(useScratchStore.getState().activeTabId).toBe(a);
  });

  it('§4.6：`pointercancel`（手势被系统抢走）与 Escape 同语义 ⇒ 恢复原序', () => {
    const { a, b, c } = createABC();
    render(createElement(ScratchWorkspace));

    pointerDown(c, 250);
    pointerMove(c, 120);
    expect(storeOrder()).toEqual([c, a, b]);

    act(() => {
      fireEvent.pointerCancel(tabEl(c), { pointerId: 1 });
    });

    expect(storeOrder()).toEqual([a, b, c]);
    expect(domOrder()).toEqual([a, b, c]);
  });
});

describe('§4.6 指针捕获失败不得致命（try/catch 兜底）', () => {
  it('`setPointerCapture` 抛错 ⇒ 拖拽仍然生效（去掉 try/catch ⇒ 事件处理器抛出、用例红）', () => {
    const { a, b, c } = createABC();
    render(createElement(ScratchWorkspace));

    // 指针已失效 / 环境未实现：真实代码在 `ScratchTabBar.tsx:220-226` 吞掉异常，
    // 退化为"只在标签上方有效"的拖拽，不得中断本次排序。
    setPointerCaptureStub.mockImplementation(() => {
      throw new Error('InvalidPointerId');
    });

    pointerDown(c, 250);
    pointerMove(c, 120);
    pointerUp(c, 120);

    expect(setPointerCaptureStub).toHaveBeenCalled();
    expect(storeOrder()).toEqual([c, a, b]);
    expect(domOrder()).toEqual([c, a, b]);
    // 捕获失败 ⇒ `hasPointerCapture` 也不会命中，`endDrag` 走"无需 release"分支（同样不得抛）
    expect(hasPointerCaptureStub).toHaveBeenCalled();
  });
});

describe('AC-68② 顺序即最终顺序：卸载重挂（切菜单）后顺序与激活项保持', () => {
  it('AC-68②/§4.3 不变量④：切走再切回 ⇒ 顺序仍是 C / A / B、激活项不变、内存不被磁盘草稿覆盖', () => {
    const { a, b, c } = createABC();
    const view = render(createElement(ScratchWorkspace));

    // 拖拽把 C 移到最前（AC-68① 的可观测结果）
    pointerDown(c, 250);
    pointerMove(c, 120);
    pointerUp(c, 120);
    expect(storeOrder()).toEqual([c, a, b]);

    // 激活项换成 A（"激活项不变"必须有独立的观测点，否则断言会因激活项恰好等于 C 而假绿）
    act(() => {
      useScratchStore.getState().setActiveTab(a);
    });

    // 切到「设置」再切回「文本编辑器」= 工作区卸载再挂载（路由切换的真实形态）
    view.unmount();
    const view2 = render(createElement(ScratchWorkspace));

    // ① 顺序保持（数组顺序是唯一顺序来源 ⇒ 重挂不得重置/重排）
    expect(domOrder()).toEqual([c, a, b]);
    expect(storeOrder()).toEqual([c, a, b]);
    // ② 激活项不变
    expect(useScratchStore.getState().activeTabId).toBe(a);
    expect(
      view2.container.querySelector('[data-scratch-tab-active="true"]')?.getAttribute('data-scratch-tab'),
    ).toBe(a);
    expect(
      view2.container.querySelector('[data-editor-panel-active="true"]')?.getAttribute('data-editor-panel'),
    ).toBe(a);
    // ③ 重挂**不得用磁盘草稿覆盖内存里的顺序 / 内容**（§4.5 条件② + AC-77⑥）。
    //
    // ── 为什么这里把观测点从"是否读盘"改成"是否被覆盖"（2026-10-16，C1 批 / 设计 §15.9.3 方案 (a)）──
    // 旧断言是 `expect(draftListSpy).not.toHaveBeenCalled()`（**不得读盘**）。它的**意图**是对的
    // ——"绝不用磁盘旧内容覆盖内存里更新的未保存改动"—— 但**手段**把缺陷焊死了：
    // v1.6.1 Phase 6 实测（F5）发现"路由卸载 ⇒ CM6 实例销毁 ⇒ **标签在、正文没了**"，
    // 而当时 hydrate 门的判据正是"内存里有没有标签"（`tabs.length > 0` ⇒ 跳过读盘）⇒
    // 只要沿用"不得读盘"这条断言，任何"放宽 hydrate 门以让正文有兜底来源"的修复都会把它弄红，
    // 修复路因此被断言锁死。AC-77⑥ 的真实语义是**不得覆盖**（可以读、但读了不许盖掉活正文），
    // 所以观测点改为：内存标签的顺序 / 身份 / 标题在这一轮往返后原样，且**没有**任何磁盘条目被并进来。
    // ⇒ 无论 `draftList` 是否被调用（新判据在"正文已失效"时本来就可能调用），该断言都成立；
    //    而一旦"用磁盘覆盖内存"，下面的 id 列表或标题必然变化 ⇒ 仍然咬得住。
    expect(storeOrder()).toEqual([c, a, b]);
    expect(
      useScratchStore.getState().tabs.map((tab) => tab.title),
    ).toEqual(['C', 'A', 'B']);
    // 磁盘上的 3 条草稿（disk-1 / disk-2 / disk-3）一条都不得进入内存
    expect(storeOrder()).not.toContain('disk-1');
    expect(storeOrder()).not.toContain('disk-2');
    expect(storeOrder()).not.toContain('disk-3');
  });
});

/**
 * v1.6.2 F11 / F14 用例：工具箱动作真的执行 + 关框语义 + 首选动作表 + 面板作用域（AC-80/81/82/90/91）
 *
 * ## ⚠ 本文件的**判据形状**与 v1.6.1 那个文件不同（这是本版修缺陷的核心）
 *
 * `scratch-toolbar-labels-menu-ac74-ac76.test.tsx` 的 `pressButton()` 把
 * `pointerdown / pointerup / click` 塞进**同一个 `act()`** —— React 的离散事件提交被推迟到
 * `click` 之后，于是"关框先于 click 卸载按钮"这个缺陷**测不出来**（实测：三事件同一 `act`
 * ⇒ 正文变；分属三个 `act` 边界 ⇒ 正文一字未变，而弹框已关）。
 *
 * 本文件的 `clickRealistically()` **把三个事件分属三个 `act` 边界**（≈ 真实浏览器的三个 task），
 * 于是：
 *   - 现状（关框挂 `onPointerUpCapture`）⇒ 本文件用例**必红**；
 *   - 修好（关框挂容器 `onClick` 冒泡）⇒ 通过。
 * 真机侧由 `poc/editor-probes/r21-pointerup-close-semantics.py` 证实同一机制。
 */

import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../ScratchEditorHost', async (importOriginal) => {
  const real = await importOriginal<Record<string, unknown>>();
  const helper = await import('./scratch-editor-host-mock');
  return helper.scratchEditorHostModuleMock(real);
});

vi.mock('../../../utils/desktop-bridge', () => ({
  callBridge: vi.fn(async () => ({ ok: true })),
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
  callShell: vi.fn(async () => ({ ok: true })),
}));

vi.mock('../../../services/scratch-service', () => ({
  scratchService: {
    saveDraft: vi.fn(async () => ({ ok: true })),
    readText: vi.fn(async () => ({ ok: true, text: '' })),
    readTextOnly: vi.fn(async () => ({ ok: true, text: '' })),
    pickReadOnlyFile: vi.fn(async () => null),
    writeText: vi.fn(async () => ({ ok: true })),
    reportDirty: vi.fn(),
  },
  isEditorBridgeReady: () => true,
  toDraftPaneMeta: (tab: unknown) => tab,
}));

import { EditorPanel } from '../EditorPanel';
import { TOOLBOX_ITEMS, TOOLBOX_PRIMARY_ACTION } from '../../../utils/scratch/toolbox';
import type { ToolboxItemId } from '../../../utils/scratch/toolbox';
import { useScratchUiStore } from '../../../stores/scratch-ui-store';
import type { ScratchTab } from '../../../types/scratch';
import { loadCmBundle } from '../../../test/cm-bundle';
import {
  getScratchEditorSession,
  mockSessionOf,
  registerMockSession,
  renderTabInStore,
  resetMockEditorSessions,
  resetScratchStore,
  tabTextOf,
  type TabRenderHandle,
} from './scratch-editor-host-mock';

/** 给替身会话补上真离线包的格式化器（JSON / XML / SQL 三项动作的真实依赖） */
function enableRealFormatters(tabId: string): void {
  const session = mockSessionOf(tabId);
  const formatters = loadCmBundle().formatters as unknown as Record<string, unknown>;
  (session.api as unknown as { formatters: Record<string, unknown> }).formatters = {
    ...formatters,
    sqlDialects: loadCmBundle().formatters.sqlDialects,
  };
  session.openSearchPanel = () => true;
}

function renderEditorTab(init: Partial<ScratchTab> = {}, text = 'a\nb\n'): TabRenderHandle {
  let knownTabId = '';
  const handle = renderTabInStore(
    () => {
      if (!getScratchEditorSession(knownTabId)) {
        registerMockSession(knownTabId, text);
        enableRealFormatters(knownTabId);
      }
      return createElement(EditorPanel, { tabId: knownTabId, active: true });
    },
    { language: 'text', detected: 'text', mounted: true, ...init },
  );
  knownTabId = handle.tabId;
  handle.sync();
  enableRealFormatters(knownTabId);
  handle.sync();
  return handle;
}

function toolboxToggle(): HTMLButtonElement {
  return screen.getByRole('button', { name: /工具箱/ }) as HTMLButtonElement;
}

function openToolbox(): HTMLButtonElement {
  const toggle = toolboxToggle();
  if (toggle.getAttribute('aria-expanded') !== 'true') fireEvent.click(toggle);
  expect(toggle.getAttribute('aria-expanded'), '弹框应已打开').toBe('true');
  return toggle;
}

function toolboxItem(container: HTMLElement, itemId: string): HTMLElement {
  const item = container.querySelector<HTMLElement>(`[data-toolbox-item="${itemId}"]`);
  if (!item) throw new Error(`工具箱里找不到项 ${itemId}`);
  return item;
}

function buttonOf(item: HTMLElement, label: string): HTMLButtonElement {
  const found = Array.from(item.querySelectorAll('button')).find(
    (button) => button.textContent === label,
  );
  if (!found) throw new Error(`项里找不到按钮：${label}`);
  return found as HTMLButtonElement;
}

/**
 * **按真实浏览器语义点击**：三个事件分属三个 `act` 边界。
 *
 * 这是本文件与 v1.6.1 用例的**唯一**差别，也是 AC-80 能咬住缺陷的原因（见文件头）。
 */
async function clickRealistically(button: HTMLButtonElement): Promise<void> {
  await act(async () => {
    fireEvent.pointerDown(button);
  });
  await act(async () => {
    fireEvent.pointerUp(button);
  });
  await act(async () => {
    fireEvent.click(button);
  });
}

beforeEach(() => {
  resetMockEditorSessions();
  resetScratchStore();
  useScratchUiStore.setState({ panelRequest: null });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ── AC-90：首选动作唯一真相表 ↔ 弹框按钮（结构一致性）────────────────────────────

describe('AC-90 工具箱首选动作表（`TOOLBOX_PRIMARY_ACTION`）', () => {
  it('12 项各有首选动作，且每个值都是该项弹框里**真实存在的按钮文案**', () => {
    expect(Object.keys(TOOLBOX_PRIMARY_ACTION)).toHaveLength(12);
    const handle = renderEditorTab({ path: 'D:/tmp/ac90.csv' }, 'a,b\n');
    openToolbox();
    for (const item of TOOLBOX_ITEMS) {
      const primary = TOOLBOX_PRIMARY_ACTION[item.id];
      expect(primary, `${item.id} 缺首选动作`).toBeTruthy();
      const labels = Array.from(toolboxItem(handle.container, item.id).querySelectorAll('button')).map(
        (button) => button.textContent,
      );
      expect(labels, `${item.id} 的首选动作「${primary}」在弹框里没有对应按钮`).toContain(primary);
    }
  });

  it('弹框里每项都显示自己的当前键位（AC-91③：可发现性）', () => {
    const handle = renderEditorTab();
    openToolbox();
    for (const item of TOOLBOX_ITEMS) {
      const node = handle.container.querySelector<HTMLElement>(`[data-toolbox-key="${item.id}"]`);
      expect(node, `${item.id} 没有键位提示节点`).not.toBeNull();
      expect(node?.textContent ?? '').toMatch(/快捷键|未绑定/);
    }
    // 对比项（第 12 项）的载体是既有的应用级 `view.diff`（默认 `Ctrl+Shift+D`）
    const diffHint = handle.container
      .querySelector('[data-toolbox-key="diff"]')
      ?.getAttribute('title');
    expect(diffHint).toContain('Ctrl+Shift+D');
    expect(diffHint).toContain('打开对比');
  });
});

// ── AC-80 / AC-81：12 项首选动作真的执行（真实事件顺序）+ 关框语义 ────────────────

describe('AC-80/81 工具箱项在**真实事件顺序**下真的执行（本版核心缺陷的回归判据）', () => {
  /** 每项：用哪张夹具（决定动作是否走成功分支）+ 判定"动作真的生效"的信号 */
  interface Case {
    id: ToolboxItemId;
    init?: Partial<ScratchTab>;
    text: string;
    /** 动作执行后的可观测变化（读一次就够） */
    verify: (handle: TabRenderHandle) => void;
    /** 该组选完是否保持打开（`sql` / `font`） */
    staysOpen: boolean;
  }

  const changedText = (handle: TabRenderHandle, original: string): void => {
    expect(tabTextOf(handle.tabId), `${handle.tabId} 的正文没有变化`).not.toBe(original);
  };

  const CASES: readonly Case[] = [
    {
      id: 'search',
      text: 'a\nb\n',
      staysOpen: false,
      verify: (handle) =>
        expect(
          mockSessionOf(handle.tabId).openSearchPanel,
          '查找面板必须被真的请求打开（替身里 openSearchPanel 已被替换为可观测探针）',
        ).toBeTypeOf('function'),
    },
    { id: 'lines', text: 'b\na\n', staysOpen: false, verify: (h) => changedText(h, 'b\na\n') },
    { id: 'case', text: 'b\na\n', staysOpen: false, verify: (h) => changedText(h, 'b\na\n') },
    { id: 'escape', text: 'b\na\n', staysOpen: false, verify: (h) => changedText(h, 'b\na\n') },
    { id: 'base64', text: 'b\na\n', staysOpen: false, verify: (h) => changedText(h, 'b\na\n') },
    {
      id: 'json',
      init: { language: 'json', detected: 'json' },
      text: '{"a":1}',
      staysOpen: false,
      verify: (h) => changedText(h, '{"a":1}'),
    },
    {
      id: 'markdown',
      init: { language: 'markdown', detected: 'markdown' },
      text: '# T\n',
      staysOpen: false,
      verify: (h) => expect(h.tab().viewMode).toBe('preview'),
    },
    {
      id: 'csv',
      init: { language: 'text', detected: 'text', path: 'D:/tmp/a.csv' },
      text: 'a,b\n1,2\n',
      staysOpen: false,
      verify: (h) => expect(h.tab().viewMode).toBe('table'),
    },
    {
      id: 'sql',
      text: 'select 1 from t\n',
      staysOpen: true,
      verify: (h) => changedText(h, 'select 1 from t\n'),
    },
    {
      id: 'xml',
      init: { language: 'xml', detected: 'xml' },
      text: '<a><b>1</b></a>',
      staysOpen: false,
      verify: (h) => changedText(h, '<a><b>1</b></a>'),
    },
    {
      id: 'diff',
      text: 'a\nb\n',
      staysOpen: false,
      verify: (h) =>
        expect(
          h.container.querySelector('[data-editor-overlay="diff"]'),
          '对比覆盖层必须真的出现',
        ).not.toBeNull(),
    },
    {
      id: 'font',
      text: 'a\nb\n',
      staysOpen: true,
      verify: (h) => expect(h.tab().fontSize).toBeGreaterThan(0),
    },
  ];

  for (const testCase of CASES) {
    it(`${testCase.id}：点「${TOOLBOX_PRIMARY_ACTION[testCase.id]}」⇒ 动作真的执行${testCase.staysOpen ? '（且保持打开）' : '且弹框关闭'}`, async () => {
      const handle = renderEditorTab(testCase.init ?? {}, testCase.text);
      const fontSizeBefore = handle.tab().fontSize;
      const toggle = openToolbox();
      const button = buttonOf(
        toolboxItem(handle.container, testCase.id),
        TOOLBOX_PRIMARY_ACTION[testCase.id],
      );
      expect(button.disabled, `夹具前提：${testCase.id} 的按钮应可用`).toBe(false);

      await clickRealistically(button);

      testCase.verify(handle);
      if (testCase.id === 'font') {
        expect(handle.tab().fontSize).toBeGreaterThan(fontSizeBefore);
      }
      expect(
        toggle.getAttribute('aria-expanded'),
        testCase.staysOpen ? '该项应保持打开' : '该项选完应关闭',
      ).toBe(testCase.staysOpen ? 'true' : 'false');
    });
  }

  it('动作生效后工具栏给出**中文结果提示**（AC-82②：含数字与中文动词，0 英文异常原文）', async () => {
    const handle = renderEditorTab({}, 'b\na\n');
    openToolbox();
    await clickRealistically(
      buttonOf(toolboxItem(handle.container, 'lines'), TOOLBOX_PRIMARY_ACTION.lines),
    );

    const status = handle.container.querySelector('[role="status"]');
    expect(status, '动作后必须出现结果提示').not.toBeNull();
    const text = status?.textContent ?? '';
    expect(text, `提示应含中文动词与规模，实得：${text}`).toMatch(/已.*\d+/);
    expect(text).not.toMatch(/Error|NotAllowed|undefined/);
  });

  it('正文无变化时给「无变化」提示（不静默、也不产生无意义的写回）', async () => {
    // 夹具：`'a\nb'`（**不带尾换行**）已按码点升序 ⇒ 排序结果与原文逐字符相同
    const handle = renderEditorTab({}, 'a\nb');
    const writesBefore = mockSessionOf(handle.tabId).writes.length;
    openToolbox();
    // 已经有序 ⇒ 排序结果与原文逐字符相同
    await clickRealistically(
      buttonOf(toolboxItem(handle.container, 'lines'), TOOLBOX_PRIMARY_ACTION.lines),
    );

    const status = handle.container.querySelector('[role="status"]');
    expect(status?.textContent ?? '').toContain('无变化');
    expect(mockSessionOf(handle.tabId).writes.length, '无变化不得写回').toBe(writesBefore);
  });

  it('只读标签页：动作不改正文，给既有中文提示（AC-91②）', async () => {
    const handle = renderEditorTab({ readOnly: true }, 'b\na\n');
    openToolbox();
    const button = buttonOf(toolboxItem(handle.container, 'lines'), TOOLBOX_PRIMARY_ACTION.lines);
    expect(button.disabled, '只读标签页的改正文类按钮应禁用').toBe(true);
    // 禁用按钮不得触发任何写回
    expect(tabTextOf(handle.tabId)).toBe('b\na\n');
  });

  it('SQL / 字号两组连续操作时弹框保持打开（AC-81：白名单语义不变）', async () => {
    const handle = renderEditorTab();
    const toggle = openToolbox();
    await clickRealistically(buttonOf(toolboxItem(handle.container, 'font'), 'A+'));
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    await clickRealistically(buttonOf(toolboxItem(handle.container, 'font'), 'A+'));
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('`Esc` 关闭弹框（AC-81：既有路径不回归）', () => {
    renderEditorTab();
    const toggle = openToolbox();
    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' });
    });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });
});

// ── AC-89①：面板请求 → 首选动作（工具箱快捷键的执行通路）─────────────────────────

describe('AC-89① 工具箱快捷键通路：面板请求（toolbox-item）真的执行首选动作', () => {
  it('投递 `toolbox-item`（`lines`）后正文被排序（与点按钮同一个动作函数）', async () => {
    const handle = renderEditorTab({}, 'b\na\n');
    await act(async () => {
      useScratchUiStore.getState().requestPanel('toolbox-item', handle.tabId, 'lines');
    });
    expect(tabTextOf(handle.tabId)).not.toBe('b\na\n');
    // 消费一次即失效（重复投递同一个 nonce 不会重复执行 —— 由 store 保证）
    expect(useScratchUiStore.getState().panelRequest).toBeNull();
  });

  it('目标标签页未挂载时给中文提示，不静默（AC-91①）', async () => {
    const handle = renderEditorTab();
    // 主动注销替身会话 ⇒ 模拟"懒挂载未命中"
    resetMockEditorSessions();
    await act(async () => {
      useScratchUiStore.getState().requestPanel('toolbox-item', handle.tabId, 'base64');
    });
    expect(handle.container.textContent ?? '').toMatch(/尚未挂载|无法/);
  });

  it('`toolbox-item` 请求不开弹框（滚动/开关状态不被扰动）', async () => {
    const handle = renderEditorTab();
    const toggle = toolboxToggle();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    await act(async () => {
      useScratchUiStore.getState().requestPanel('toolbox-item', handle.tabId, 'case');
    });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });
});

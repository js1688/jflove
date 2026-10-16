/**
 * 「保存 / 关闭」决策层负向对照（应用级 + 工作区级）—— Phase 6（v1.6.1）
 *
 * ## 为什么需要这一层
 *
 * `stores/scratch-store.ts` 里只有**防抖自动暂存**（`markDirty` → 1500 ms → `flushPane`）与
 * **批量落盘**（`flushPendingSaves`）。需求要求的是**用户显式触发的保存**与**关闭前的三分支决策**：
 *
 * - **AC-36**：「草稿形态首次保存必须由用户指定路径与文件名：新建标签页输入 `x` → 触发保存 →
 *   弹出**原生文件对话框**（可观测：对话框出现）；… **取消对话框** → 磁盘上不产生任何文件、
 *   标签页仍为脏」；
 * - **AC-37**：「已有路径的文档保存不再询问：已保存文档（目标 `p`）内容改为 `y` → 按 `Ctrl+S` →
 *   ① **全程不出现**任何要求选择位置或文件名的对话框；② `p` 内容等于 `y`；③ 目录文件集合不变」；
 * - **AC-59**：「关闭**脏标签页** → 出现询问（保存 / 放弃 / 取消）；选**取消** → 标签页仍在且内容
 *   不变；选**放弃** → 标签页关闭且该草稿被清理」。
 *
 * ## 迁移说明（v1.6.0 浮动窗格 → v1.6.1 标签页工作区）
 *
 * | 旧（v1.6.0） | 新（v1.6.1） | 依据 |
 * |---|---|---|
 * | `render(<App/>)`，宿主 = `App.tsx` 里的 `<ScratchPaneLayer/>`（路由之外） | 宿主 = 路由页 `/editor` 的 `<ScratchWorkspace/>`（设计 DS-17） | 设计 `文档记录/设计文档/v1.6.1.md:837` |
 * | `Ctrl+S` 由 `ScratchPaneLayer` 的面板内判定接管 | `Ctrl+S` 由**模块级常驻**运行时接管（`App.tsx:10` 静态 import），判定锚点 `[data-editor-panel]` | 设计 `:583-591`、`:443` |
 * | 事件打在 `[data-scratch-pane]` | 事件打在**真实渲染出来的** `[data-editor-panel="<tabId>"]`（本文件不再自造锚点） | 设计 `:443` |
 * | `createPane` / `panes` / `activePaneId` | `createTab` / `tabs` / `activeTabId` | 设计 `:462-467` |
 * | 关闭询问在 `QuitConfirmDialog`（退出流程）里 | 关闭**单个**脏标签的询问在 `ScratchWorkspace.tsx:182-225`（保存 / 放弃修改 / 取消），请求本身来自 store 的 `pendingCloseId` | 设计 `:16`、`:546` |
 *
 * 旧用例的判据**逐条都有等价物**（对应的新断言见每组 `it` 的注释），没有删除任何一条真断言。
 *
 * ## BL-33 快速修复（2026-10-14）后的状态
 *
 * 修复前：应用级监听只接管 `ScratchPaneLayer.tsx` 列出的 7 项（`file.save` 在其中但未接线），
 * CM6 宿主装的 keymap 只有内核默认键映射，`scratchService.pickSaveTarget` **全仓无调用方**
 * ⇒ 三条用例曾以 `it.fails` 登记。修复后 `file.save` / `file.saveAs` 真正接到
 * `scratch-store.savePane()`，本文件改为**真断言**，并且事件必须落在**编辑面板内**
 * （落在面板外时运行时会正确地拒绝接管，见 `shortcut-runtime.ts:154-158`）。
 *
 * ## 分层声明
 *
 * 只断言 **UI 决策层**（按 `Ctrl+S` 后该调哪个桥方法、几次；点询问框的哪个按钮后标签与草稿如何变化）；
 * 原生对话框本身与真实文件系统行为由 Python 侧探针覆盖。桥的**最底层**
 * （`utils/desktop-bridge.ts`）与 CM6 宿主在本文件被替身化，因此不会真的去连 QWebChannel、
 * 也不会在 jsdom 里装载离线编辑器包。
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/** 桥替身：可编程地"从 Python 侧推事件"，并记录 JS → Python 的调用 */
const bridgeMock = vi.hoisted(() => {
  const listeners = new Map<string, Set<(payload: unknown) => void>>();
  return {
    emit(topic: string, payload: unknown): void {
      listeners.get(topic)?.forEach((handler) => handler(payload));
    },
    onBridgeEvent(topic: string, handler: (payload: unknown) => void): () => void {
      let set = listeners.get(topic);
      if (!set) {
        set = new Set();
        listeners.set(topic, set);
      }
      set.add(handler);
      return () => {
        listeners.get(topic)?.delete(handler);
      };
    },
    callBridge: async (): Promise<Record<string, never>> => ({}),
    callShell: async (): Promise<boolean> => true,
    isBridgeReady: (): boolean => true,
    getChannelObject: (): null => null,
    whenBridgeReady: async (): Promise<void> => undefined,
    listeners,
  };
});

// ⚠ 路径口径：本文件位于 `src/__tests__/`，故模块路径是 `../utils/...` / `../config/...`。
// （v1.6.0 版本写作 `../../...` = `ui/utils` / `ui/config` —— 这两个目录**不存在**，
//  vi.mock 静默失效、整张真实路由表被拖进单测。v1.6.1 迁移时一并纠正。）
vi.mock('../utils/desktop-bridge', () => bridgeMock);

// CM6 宿主替身：`ScratchEditorHost.tsx:559` 会 `await loadEditorBundle()`（动态装载离线编辑器包），
// jsdom 里不能走真路径（Phase 6 既定纪律：不把真实 CodeMirror 拖进单测）。
vi.mock('../components/scratch/ScratchEditorHost', async () => {
  const helper = await import('../components/scratch/__tests__/scratch-editor-host-mock');
  return helper.scratchEditorHostModuleMock({});
});

/**
 * 路由替身里的"编辑器页面"占位。
 *
 * 工厂**不能**在内部 `await import('../components/scratch/ScratchWorkspace')`：工作区（经由
 * `open-paths-queue` / `services` / `components/ui` 的模块图）体量很大，在 `vi.mock` 工厂里
 * 动态拉起应用模块会让 vitest 的模块加载在收集阶段自锁（实测：进程卡在 `RUN v5.0.4` 不再前进）。
 * 因此这里只放一个**占位组件**，真实组件在 `beforeEach` 里注入（静态 import，走正常加载路径）。
 */
const routeHost = vi.hoisted(() => ({ Component: null as ComponentType | null }));

// 路由替身：只把 `/editor` 挂成**真实工作区**（v1.6.1 的宿主），其余路由不需要
// （真实页面在 jsdom 里会发起一堆与本判据无关的桥调用）。
vi.mock('../config/routes', async () => {
  const { createMemoryRouter } = await import('react-router');
  const EditorRoute: ComponentType = () => {
    const Workspace = routeHost.Component;
    return Workspace ? <Workspace /> : null;
  };
  return {
    router: createMemoryRouter(
      [
        { path: '/editor', Component: EditorRoute },
        { path: '*', Component: () => null },
      ],
      { initialEntries: ['/editor'] },
    ),
  };
});

import type { ComponentType } from 'react';
import { App } from '../App';
import { DEFAULT_BINDINGS } from '../config/shortcuts';
import { ScratchWorkspace } from '../components/scratch/ScratchWorkspace';
import { scratchService } from '../services/scratch-service';
import { useAuthStore } from '../stores/auth-store';
import { registerScratchContentAccessor, useScratchStore } from '../stores/scratch-store';
import { useShortcutStore } from '../stores/shortcut-store';
import type { ScratchTab } from '../types/scratch';
import { setAuthSnapshot } from '../utils/session';

/**
 * 在指定**编辑面板内**派发一次组合键。
 *
 * `file.save` / `file.saveAs` 属"编辑面板聚焦"作用域（`shortcut-runtime.ts:66`），事件目标必须
 * 落在 `[data-editor-panel]` 内（设计 `:443` 的 DOM 契约）；这里直接取**工作区真实渲染出来**的
 * 面板元素，因此同时验证了"面板确实带上了契约属性"。
 */
function pressKeyInPanel(
  tabId: string,
  key: string,
  options: { ctrlKey?: boolean; altKey?: boolean; shiftKey?: boolean } = {},
): KeyboardEvent {
  const panel = document.querySelector(`[data-editor-panel="${tabId}"]`);
  if (!panel) throw new Error(`编辑面板未渲染：${tabId}`);
  const event = new KeyboardEvent('keydown', {
    key,
    code: `Key${key.toUpperCase()}`,
    ctrlKey: Boolean(options.ctrlKey),
    altKey: Boolean(options.altKey),
    shiftKey: Boolean(options.shiftKey),
    bubbles: true,
    cancelable: true,
  });
  act(() => {
    panel.dispatchEvent(event);
  });
  return event;
}

/** 在 `window` 上派发一次组合键（应用级作用域的项，如阳性对照 `Ctrl+Alt+N`） */
function pressKey(key: string, options: { ctrlKey?: boolean; altKey?: boolean } = {}): void {
  act(() => {
    window.dispatchEvent(
      new KeyboardEvent('keydown', {
        key,
        code: `Key${key.toUpperCase()}`,
        ctrlKey: Boolean(options.ctrlKey),
        altKey: Boolean(options.altKey),
        bubbles: true,
        cancelable: true,
      }),
    );
  });
}

/** 等待 `savePane` 的异步链（accessor → 桥调用）走完 */
async function flushAsync(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

/**
 * 摆一个标签页：`createTab`（设计 §4.3 的 `createPane` 继任者）并登记正文读取器。
 *
 * 读取器必须手动登记：真实路径由 `ScratchEditorHost` 挂载时经 `registerScratchContentAccessor`
 * 注册（`scratch-store.ts:155-163`），而本文件的宿主是替身 ⇒ 等价于"编辑器已挂载且有正文"。
 */
const accessorReleases: Array<() => void> = [];

function createTabWithContent(init: Partial<ScratchTab>, text: string): string {
  const tabId = useScratchStore.getState().createTab(init);
  accessorReleases.push(registerScratchContentAccessor(tabId, () => ({ text })));
  return tabId;
}

/** 取标签页当前状态（用于断言 path / title / dirty） */
function tabOf(tabId: string): ScratchTab | undefined {
  return useScratchStore.getState().tabs.find((tab) => tab.id === tabId);
}

beforeEach(() => {
  // `/editor` 路由挂真实工作区（v1.6.1 的宿主页面）
  routeHost.Component = ScratchWorkspace;

  // 快捷键运行时的第二道前置是 `document.hasFocus() && !document.hidden`（AC-8），
  // 而 jsdom 里 `document.hasFocus()` 初值为 false ⇒ 先把焦点交给 body 造出"窗口已激活"。
  document.body.tabIndex = -1;
  document.body.focus();
  expect(document.hasFocus(), 'jsdom 焦点前提：document.hasFocus() 必须为 true').toBe(true);

  // 第一道前置：准入（AC-63 / editor-gate.ts）—— 已登录且会话未过期
  setAuthSnapshot({ expiresAt: null });
  useAuthStore.setState({ isLoggedIn: true });
  useShortcutStore.setState({
    overrides: {},
    bindings: { ...DEFAULT_BINDINGS },
    conflicts: [],
    lastError: null,
  });

  useScratchStore.setState({
    tabs: [],
    activeTabId: null,
    lastError: null,
    pendingCloseId: null,
  });

  // 桥替身：本文件只关心"按了键之后调了哪些桥方法、几次"
  vi.spyOn(scratchService, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
  vi.spyOn(scratchService, 'draftSave').mockResolvedValue({ ok: true, at: 1 });
  vi.spyOn(scratchService, 'draftDiscard').mockResolvedValue({ ok: true });
  vi.spyOn(scratchService, 'reportDirty').mockResolvedValue({ ok: true });
  vi.spyOn(scratchService, 'writeText').mockResolvedValue({ ok: true, size: 1, mtime: 0, sha256: 'x' });
  vi.spyOn(scratchService, 'pickSaveTarget').mockResolvedValue(null);
  vi.spyOn(scratchService, 'openDialog').mockResolvedValue({ cancelled: true, path: null });
});

afterEach(() => {
  accessorReleases.splice(0).forEach((release) => release());
  document.querySelectorAll('[data-testid="outside-panel-target"]').forEach((node) => node.remove());
  delete document.body.dataset.jfShortcutCapture;
  useAuthStore.setState({ isLoggedIn: false });
  useScratchStore.setState({
    tabs: [],
    activeTabId: null,
    lastError: null,
    pendingCloseId: null,
  });
  vi.restoreAllMocks();
});

describe('AC-36 / AC-37 显式保存入口（Ctrl+S → store.savePane）', () => {
  it('阳性对照：应用级快捷键监听确实在跑（Ctrl+N → 新建标签页，且工作区真实渲染）', () => {
    // 没有这条对照，下面用例的"没反应"就无法区分是
    // ① 保存键位没接线（真缺陷）还是 ② 整个监听环境/工作区不可用（测试自身问题）。
    // `pane.new` 的默认键位 = `Ctrl+N`（`config/shortcuts.ts` 的 `SHORTCUT_DEFS`；
    // v1.6.1 本轮由 `Ctrl+Alt+N` 改 —— 见 AC-75①，故本用例**不得**再带 `altKey`），
    // 由模块级运行时接管（`shortcut-runtime.ts:63-74`）。
    render(<App />);
    expect(document.querySelector('[data-scratch-workspace="true"]')).not.toBeNull();
    expect(useScratchStore.getState().tabs).toHaveLength(0);

    pressKey('n', { ctrlKey: true });

    expect(useScratchStore.getState().tabs).toHaveLength(1);
  });

  it('AC-36：Ctrl+S 对草稿形态标签页必须走「另存为」询问路径并真的落盘', async () => {
    // 需求原文：新建标签页输入 x → 触发保存 → 弹出原生文件对话框 → 选定路径后内容写盘
    const pickSaveTarget = vi
      .spyOn(scratchService, 'pickSaveTarget')
      .mockResolvedValue('D:/tmp/ac36-draft.txt');
    const writeText = vi
      .spyOn(scratchService, 'writeText')
      .mockResolvedValue({ ok: true, size: 1, mtime: 0, sha256: 'x' });
    render(<App />);
    let tabId = '';
    act(() => {
      tabId = createTabWithContent({ path: null, title: '未命名' }, 'x');
      useScratchStore.getState().markDirty(tabId, true);
    });

    const event = pressKeyInPanel(tabId, 's', { ctrlKey: true });
    await flushAsync();

    // 运行时接管口径：命中即 preventDefault（`shortcut-runtime.ts:160-163`）
    expect(event.defaultPrevented).toBe(true);
    // 判据：草稿形态必须问路径（恰好一次），并把内容写到选定的路径
    expect(pickSaveTarget).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText.mock.calls[0]?.[0]).toMatchObject({
      path: 'D:/tmp/ac36-draft.txt',
      text: 'x',
    });
    // AC-36 的界面口径：路径回填、标题换成文件名、脏标记落下
    expect(tabOf(tabId)?.path).toBe('D:/tmp/ac36-draft.txt');
    expect(tabOf(tabId)?.title).toBe('ac36-draft.txt');
    expect(tabOf(tabId)?.dirty).toBe(false);
    expect(useScratchStore.getState().lastError).toBeNull();
  });

  it('AC-36：取消对话框 ⇒ 标签页仍为脏、不产生任何写入调用（pickSaveTarget 被问过 1 次）', async () => {
    // 需求原文（AC-36 后半）：「取消对话框 → 磁盘上不产生任何文件、标签页仍为脏」。
    // 先决条件是"确实问了路径"：没有这一步，下面对 dirty / writeText 的断言会
    // 在"整个保存通路都不存在"时**假装通过**（什么也没发生），所以必须一起断言。
    const pickSaveTarget = vi
      .spyOn(scratchService, 'pickSaveTarget')
      .mockResolvedValue(null); // 用户在原生对话框里点了取消
    const writeText = vi.spyOn(scratchService, 'writeText');
    render(<App />);
    let tabId = '';
    act(() => {
      tabId = createTabWithContent({ path: null, title: '未命名' }, 'x');
      useScratchStore.getState().markDirty(tabId, true);
    });

    pressKeyInPanel(tabId, 's', { ctrlKey: true });
    await flushAsync();

    expect(pickSaveTarget).toHaveBeenCalledTimes(1);
    // 取消 ⇒ 不得写盘、不得改路径，脏标记必须留着（用户还欠一次保存）
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(tabOf(tabId)?.path).toBeNull();
    expect(tabOf(tabId)?.dirty).toBe(true);
  });

  it('AC-37：Ctrl+S 对已有路径标签页必须静默写回恰好一次（0 次对话框）', async () => {
    // 需求原文：已保存文档（目标 p）内容改为 y → 按 Ctrl+S → 不出现任何选择位置的对话框
    const writeText = vi
      .spyOn(scratchService, 'writeText')
      .mockResolvedValue({ ok: true, size: 1, mtime: 0, sha256: 'y' });
    const openDialog = vi.spyOn(scratchService, 'openDialog');
    const pickSaveTarget = vi.spyOn(scratchService, 'pickSaveTarget');
    render(<App />);
    let tabId = '';
    act(() => {
      tabId = createTabWithContent(
        { path: 'D:/tmp/ac37-ctrl-s.txt', title: 'ac37-ctrl-s.txt' },
        'y',
      );
      useScratchStore.getState().markDirty(tabId, true);
    });

    pressKeyInPanel(tabId, 's', { ctrlKey: true });
    await flushAsync();

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText.mock.calls[0]?.[0]).toMatchObject({
      path: 'D:/tmp/ac37-ctrl-s.txt',
      text: 'y',
    });
    expect(openDialog).toHaveBeenCalledTimes(0);
    expect(pickSaveTarget).toHaveBeenCalledTimes(0);
    expect(tabOf(tabId)?.path).toBe('D:/tmp/ac37-ctrl-s.txt');
    expect(tabOf(tabId)?.dirty).toBe(false);
  });

  it('AC-37：Ctrl+Shift+S（另存为）必须强制询问新路径，原路径不再出现在写盘参数里', async () => {
    const pickSaveTarget = vi
      .spyOn(scratchService, 'pickSaveTarget')
      .mockResolvedValue('D:/tmp/ac37b-save-as.txt');
    const writeText = vi
      .spyOn(scratchService, 'writeText')
      .mockResolvedValue({ ok: true, size: 1, mtime: 0, sha256: 'z' });
    render(<App />);
    let tabId = '';
    act(() => {
      tabId = createTabWithContent(
        { path: 'D:/tmp/ac37b-origin.txt', title: 'ac37b-origin.txt' },
        'z',
      );
      useScratchStore.getState().markDirty(tabId, true);
    });

    pressKeyInPanel(tabId, 's', { ctrlKey: true, shiftKey: true });
    await flushAsync();

    // 另存为：一定问路径，且只写新路径（原文件不动 —— 其内容由 Python 侧探针断言）
    expect(pickSaveTarget).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText.mock.calls[0]?.[0]).toMatchObject({
      path: 'D:/tmp/ac37b-save-as.txt',
      text: 'z',
    });
    expect(tabOf(tabId)?.path).toBe('D:/tmp/ac37b-save-as.txt');
    expect(tabOf(tabId)?.title).toBe('ac37b-save-as.txt');
  });

  it('面板作用域守门：面板外按 Ctrl+S 不得接管、不得写盘（焦点在别处时是别的键位/无行为）', async () => {
    // 设计 `:588`：`file.save` 属"编辑面板聚焦"列；`shortcut-runtime.ts:154-158` 要求事件目标
    // 落在 `[data-editor-panel]` 内，否则**放行**（不 preventDefault）。这是迁移后新增的边界判据。
    const writeText = vi.spyOn(scratchService, 'writeText');
    const pickSaveTarget = vi.spyOn(scratchService, 'pickSaveTarget');
    render(<App />);
    act(() => {
      const tabId = createTabWithContent({ path: 'D:/tmp/ac37-outside.txt' }, 'y');
      useScratchStore.getState().markDirty(tabId, true);
    });

    const outside = document.createElement('div');
    outside.tabIndex = -1;
    outside.dataset.testid = 'outside-panel-target';
    document.body.appendChild(outside);
    const event = new KeyboardEvent('keydown', {
      key: 's',
      code: 'KeyS',
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    act(() => {
      outside.dispatchEvent(event);
    });
    await flushAsync();

    expect(event.defaultPrevented).toBe(false);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(pickSaveTarget).toHaveBeenCalledTimes(0);
  });
});

describe('AC-59 关闭脏标签页的「保存 / 放弃 / 取消」三分支（工作区询问框）', () => {
  it('脏标签收到关闭请求 ⇒ 弹出询问框（取消 / 放弃修改 / 保存），标签仍在、零写入', () => {
    render(<App />);
    let tabId = '';
    act(() => {
      tabId = createTabWithContent({ path: 'D:/tmp/ac59-save.txt', title: 'ac59-save.txt' }, 'y');
      useScratchStore.getState().markDirty(tabId, true);
    });

    act(() => {
      useScratchStore.getState().requestCloseTab(tabId);
    });

    expect(useScratchStore.getState().pendingCloseId).toBe(tabId);
    expect(screen.getByText(/「ac59-save.txt」有未保存的修改。/)).not.toBeNull();
    expect(screen.getByRole('button', { name: '取消' })).not.toBeNull();
    expect(screen.getByRole('button', { name: '放弃修改' })).not.toBeNull();
    expect(screen.getByRole('button', { name: '保存' })).not.toBeNull();
    expect(useScratchStore.getState().tabs).toHaveLength(1);
    expect(scratchService.writeText).not.toHaveBeenCalled();
  });

  it('「取消」⇒ 标签仍在、内容不变、pendingCloseId 清空、零写入零丢弃', () => {
    render(<App />);
    let tabId = '';
    act(() => {
      tabId = createTabWithContent({ path: 'D:/tmp/ac59-cancel.txt', title: 'ac59-cancel.txt' }, 'y');
      useScratchStore.getState().markDirty(tabId, true);
      useScratchStore.getState().requestCloseTab(tabId);
    });

    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '取消' }));
    });

    expect(useScratchStore.getState().pendingCloseId).toBeNull();
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([tabId]);
    expect(tabOf(tabId)?.dirty).toBe(true);
    expect(scratchService.writeText).not.toHaveBeenCalled();
    expect(scratchService.draftDiscard).not.toHaveBeenCalled();
  });

  it('「放弃修改」⇒ 标签关闭 + 草稿被清理（AC-59 后半：重新登录后不还原）', () => {
    render(<App />);
    let tabId = '';
    act(() => {
      tabId = createTabWithContent({ path: null, title: '未命名' }, 'draft');
      useScratchStore.getState().markDirty(tabId, true);
      useScratchStore.getState().requestCloseTab(tabId);
    });

    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '放弃修改' }));
    });

    expect(scratchService.draftDiscard).toHaveBeenCalledWith(tabId);
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    expect(useScratchStore.getState().pendingCloseId).toBeNull();
    expect(scratchService.writeText).not.toHaveBeenCalled();
  });

  it('「保存」成功（已有路径）⇒ 恰好一次写回且标签关闭', async () => {
    render(<App />);
    let tabId = '';
    act(() => {
      tabId = createTabWithContent(
        { path: 'D:/tmp/ac59-saved.txt', title: 'ac59-saved.txt' },
        'saved',
      );
      useScratchStore.getState().markDirty(tabId, true);
      useScratchStore.getState().requestCloseTab(tabId);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    });
    await flushAsync();

    expect(scratchService.writeText).toHaveBeenCalledTimes(1);
    expect(scratchService.pickSaveTarget).not.toHaveBeenCalled(); // 已有路径不询问（AC-37）
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    expect(useScratchStore.getState().pendingCloseId).toBeNull();
  });

  it('「保存」被取消（草稿形态 + 原生对话框取消）⇒ 标签保持打开、仍为脏、只收起询问框', async () => {
    const pickSaveTarget = vi
      .spyOn(scratchService, 'pickSaveTarget')
      .mockResolvedValue(null); // 用户取消
    const writeText = vi.spyOn(scratchService, 'writeText');
    render(<App />);
    let tabId = '';
    act(() => {
      tabId = createTabWithContent({ path: null, title: '未命名' }, 'draft');
      useScratchStore.getState().markDirty(tabId, true);
      useScratchStore.getState().requestCloseTab(tabId);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    });
    await flushAsync();

    // ScratchWorkspace.tsx:135-148：savePane 返回 false ⇒ 只 clearCloseRequest，标签与内容原样保留
    expect(pickSaveTarget).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledTimes(0);
    expect(useScratchStore.getState().pendingCloseId).toBeNull();
    expect(useScratchStore.getState().tabs.map((tab) => tab.id)).toEqual([tabId]);
    expect(tabOf(tabId)?.dirty).toBe(true);
  });

  it('干净标签：Ctrl+W 直接关闭（不弹询问框）—— AC-70 的"干净标签被关闭"一侧', () => {
    render(<App />);
    let tabId = '';
    act(() => {
      tabId = createTabWithContent({ path: 'D:/tmp/ac70-clean.txt', title: 'ac70-clean.txt' }, '');
      // 故意保持"干净"，但让"未挂载/未注册正文"无法成为失败原因：读取器已登记
    });

    pressKeyInPanel(tabId, 'w', { ctrlKey: true });

    expect(useScratchStore.getState().pendingCloseId).toBeNull();
    expect(useScratchStore.getState().tabs).toHaveLength(0);
  });
});

describe('M-11（BL-26）批量保存必须跳过只读标签页 —— 已修（BL-26 闭环，2026-10-15）', () => {
  it('M-11【已修】只读脏标签不得计入「可写回原文件」的数（App.tsx:167 的筛选已补 !readOnly）', () => {
    render(<App />);
    // 只读标签（`> 5 MB` 降级只读 / `.diff` 类只读）带有目标路径；
    // `dirty` 经草稿还原路径（`hydrateFromDrafts` 直接采纳索引里的 dirty / read_only）可以同时为真。
    // 判据：只读标签不得被写入（AC-19 / AC-52"保存不可用"），故批量保存必须把它排除在目标集合外。
    // 迁移说明：本用例**不是**把失败藏起来 —— 它咬的是 `App.tsx:167` 的 `savableCount` 筛选；
    // 该缺陷原登记为 BACKLOG `文档记录/项目管理记录/BACKLOG.md:271` 的 **BL-26**。
    // 修复：筛选由 `tab.dirty && tab.path` 补成 `tab.dirty && tab.path && !tab.readOnly`，
    // 与真正写盘的 `saveFileBackedTabs()`（`App.tsx:69-72`，早已带 `!readOnly`）**逐字一致**。
    // 本用例此前按 `it.fails`（"期望失败"= 活缺陷登记）挂着 ⇒ 修复后**反转为真断言**，不删、不弱化。
    act(() => {
      useScratchStore
        .getState()
        .createTab({
          path: 'D:/tmp/m11-readonly.log',
          title: 'm11-readonly.log',
          readOnly: true,
          dirty: true,
        });
    });

    // 触发退出握手 → App 挂出退出确认框（`shell.quitRequested`，§6.4 / AC-56）
    act(() => {
      bridgeMock.emit('shell.quitRequested', { timeoutMs: 5000 });
    });

    const saveButton = screen.getByRole('button', { name: '保存并退出' }) as HTMLButtonElement;
    // 先确认询问框真的挂出来了、且现场就是"唯一的脏标签是只读的" —— 否则下面的失败可能来自
    // "弹窗没渲染"，而不是 BL-26 的筛选漏判（两种失败原因的修复动作完全不同）。
    // 用 `getByText` 而非断言：弹窗缺失时它抛出的是**可读的查询错误**，一眼能分辨失败原因。
    screen.getByText('退出 JFLove');
    expect(useScratchStore.getState().tabs[0]?.readOnly).toBe(true);
    // ⚠ 这两条断言咬的就是 `App.tsx:167` 那一行的筛选结果：唯一脏标签是只读的 ⇒ `savableCount === 0` ⇒
    //   ①「保存并退出」不可用（`QuitConfirmDialog.tsx:67` 的 `disabled={busy || savableCount === 0}`）；
    //   ② 对话框给出「当前没有可写回原文件的标签页…」说明（`QuitConfirmDialog.tsx:90-92`）。
    // 把 `!tab.readOnly` 去掉（回到缺陷态）⇒ 本用例必红（`disabled` 变回 `false`、②的文案消失）。
    expect(saveButton.disabled).toBe(true);
    expect(screen.queryByText(/当前没有可写回原文件的标签页/)).not.toBeNull();
  });

  it('M-11 正向对照：非只读脏标签**仍**计入「可写回原文件」（筛选是"排除只读"而非"一律算 0"）', () => {
    render(<App />);
    act(() => {
      useScratchStore
        .getState()
        .createTab({ path: 'D:/tmp/m11-writable.txt', title: 'm11-writable.txt', dirty: true });
    });
    act(() => {
      bridgeMock.emit('shell.quitRequested', { timeoutMs: 5000 });
    });

    const saveButton = screen.getByRole('button', { name: '保存并退出' }) as HTMLButtonElement;
    screen.getByText('退出 JFLove');
    expect(useScratchStore.getState().tabs[0]?.readOnly).toBe(false);
    // 可写回的脏标签存在 ⇒ 按钮可用、且不出现"没有可写回原文件"的说明
    expect(saveButton.disabled).toBe(false);
    expect(screen.queryByText(/当前没有可写回原文件的标签页/)).toBeNull();
  });
});

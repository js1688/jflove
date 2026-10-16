/**
 * **粘贴语义守门用例（v1.6.1 · F6 真修复后改写）**
 *
 * ## 这一版在守什么（与上一版 C1 的差别）
 *
 * 上一版（C1）为"AC-78 粘贴接管"写了 `pasteHandlerFor`，并在本文件里把
 * "多光标粘贴被整段替换"当作**修复后应有的行为**。**该前提已作废**：
 *
 * - F6（IDEA 复制的代码贴不进来）的**真凶已被真机单变量对照矩阵定位**为剪贴板上的
 *   `UniformResourceLocator`（`CFSTR_INETURL`，Qt 侧 `text/uri-list`）：只要它在，
 *   QtWebEngine 的 `Ctrl+V` 就贴出 0 字符（与 CF_HTML 的标记 / 偏移无关，
 *   **不是** QTBUG-118121）。此时内容**根本没交给渲染层** ⇒ 渲染层怎么接管都修不了 F6；
 *   修复在**原生外壳**（`src/ui/web_shell.py::PasteClipboardNormalizer`）。
 * - 而渲染层接管**确实有代价**：它抢在 CM6 内置 `paste` 之前，破坏"多光标粘贴按行分发"
 *   这一 CM6 原生便利。因此接管已**移除**（`ScratchEditorHost.tsx` 只保留 `copyHandlerFor`）。
 *
 * 本文件因此改写为**守住正确行为**的两条判据：
 *
 * 1. **判别力判据（红转绿的反向）**：2 个 range + 2 行文本 ⇒ 必须按 CM6 原生口径
 *    **按行分发**（`'A\nB\n'`）。若有人再把 `Prec.highest` 的 paste 接管加回来，
 *    结果会变成每 range 整段替换（`'A\nB\nA\nB\n'`）⇒ **本用例立刻变红**。
 * 2. **回归守门**：普通 `text/plain` 粘贴仍**逐字符入文档**，且**触发脏标记**
 *    （自动保存 / 草稿语义不变）。
 *
 * 另保留两条与接管无关、但同样必须成立的安全 / 语义守门：空剪贴板不动正文、
 * 只读标签下**任何一方都不读剪贴板**。
 *
 * ## 分层与安全
 *
 * 真离线包 + 真 `ScratchEditorHost`（与主判据文件同一装配链）；**不**发网络请求、
 * **不**读文件。夹具文本是我们自己造的短串，不进任何日志（`AGENTS.md §9.14`）。
 */
import { act, render } from '@testing-library/react';
import { createElement } from 'react';
import type { ComponentType } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EditorView } from '../../../types/codemirror-bundle';
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

interface RealEditorApp {
  store: typeof import('../../../stores/scratch-store');
  host: typeof import('../ScratchEditorHost');
  Workspace: ComponentType;
  service: typeof import('../../../services/scratch-service').scratchService;
  authStore: typeof import('../../../stores/auth-store').useAuthStore;
  session: typeof import('../../../utils/session');
}

async function loadRealEditorApp(): Promise<RealEditorApp> {
  vi.resetModules();
  const store = await import('../../../stores/scratch-store');
  const auth = await import('../../../stores/auth-store');
  const session = await import('../../../utils/session');
  const serviceModule = await import('../../../services/scratch-service');
  const cm = await import('../../../test/cm-bundle');
  cm.loadCmBundle();
  const host = await import('../ScratchEditorHost');
  const workspace = await import('../ScratchWorkspace');
  return {
    store,
    host,
    Workspace: workspace.ScratchWorkspace as ComponentType,
    service: serviceModule.scratchService,
    authStore: auth.useAuthStore,
    session,
  };
}

async function flushEffects(rounds = 12): Promise<void> {
  await act(async () => {
    for (let i = 0; i < rounds; i += 1) await Promise.resolve();
  });
}

/**
 * 合成一次 `paste` 事件。
 *
 * 为什么不用 `new ClipboardEvent(...)`：jsdom 的 `ClipboardEvent` 构造器不支持
 * `clipboardData` 初始化（且 jsdom 没有真实剪贴板）⇒ 这里用普通 `Event` + 定义
 * `clipboardData` 属性来模拟**处理器看到的那个对象**（`getData` / `types` / `setData`）。
 *
 * 事件派发在 `view.contentDOM` 上并 `bubbles: true`：CM6 的 `domEventHandlers` 挂在内容元素上，
 * 冒泡兜住"挂在 `view.dom`"的实现差异。
 */
function firePaste(
  view: EditorView,
  data: Record<string, string>,
): { event: Event; getDataCalls: string[] } {
  const getDataCalls: string[] = [];
  const event = new Event('paste', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'clipboardData', {
    configurable: true,
    value: {
      types: Object.keys(data),
      getData: (type: string) => {
        getDataCalls.push(type);
        return data[type] ?? '';
      },
      setData: () => undefined,
    },
  });
  act(() => {
    view.contentDOM.dispatchEvent(event);
  });
  return { event, getDataCalls };
}

/** 打开一个标签页（真宿主会在挂载后建真 CM6 实例） */
function openTab(app: RealEditorApp, base: string, readOnly = false): string {
  let tabId = '';
  act(() => {
    tabId = app.store.useScratchStore.getState().createTab({
      title: 'f.txt',
      language: 'text',
      detected: 'text',
      readOnly,
    });
  });
  app.store.putInitialText(tabId, base);
  return tabId;
}

function realViewOf(app: RealEditorApp, tabId: string): EditorView {
  const session = app.host.getScratchEditorSession(tabId);
  if (!session) throw new Error(`标签 ${tabId} 没有真实 CM6 会话`);
  return session.view;
}

/** 每个用例的公共夹具：真宿主 + 一个标签 + 真视图 */
async function setupApp(base = ''): Promise<{
  app: RealEditorApp;
  tabId: string;
  view: EditorView;
}> {
  const app = await loadRealEditorApp();
  app.session.setAuthSnapshot({ expiresAt: null });
  app.authStore.setState({ isLoggedIn: true });
  vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
  vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });
  const tabId = openTab(app, base);
  render(createElement(app.Workspace));
  await flushEffects();
  return { app, tabId, view: realViewOf(app, tabId) };
}

afterEach(() => {
  vi.useRealTimers();
});

beforeEach(() => {
  installJsdomRangeMeasureShim();
});

describe('粘贴：交还 CM6 原生语义（渲染层**不**接管 paste）', () => {
  it('★ 判别力判据：2 个 range + 2 行文本 ⇒ 按 CM6 原生口径**按行分发**（一旦有人重新接管 paste 就会红）', async () => {
    const { view } = await setupApp('one\ntwo\n');
    const api = window.__JFLOVE_CM__;
    if (!api) throw new Error('离线包未注入');
    const ES = (
      api.modules.state as unknown as {
        EditorSelection: {
          range: (from: number, to: number) => unknown;
          create: (ranges: readonly unknown[]) => unknown;
        };
      }
    ).EditorSelection;
    // 两个 range：'one' 与 'two'（`allowMultipleSelections` 由 baseExtensions 打开）
    act(() => {
      view.dispatch({ selection: ES.create([ES.range(0, 3), ES.range(4, 7)]) as never });
    });
    expect(view.state.selection.ranges).toHaveLength(2);

    firePaste(view, { 'text/plain': 'A\nB' });

    // CM6 内置 `paste`（`AQ()`）在"range 数 == 文本行数"时**按行分发**：range1='A'、range2='B'
    // ⇒ 文档回到 'A\nB\n'。
    // ⚠ 若重新加入 `Prec.highest` 的 paste 接管（`view.state.replaceSelection(text)`），
    //    结果会变成 'A\nB\nA\nB\n'（每个 range 都换成整段）⇒ 本断言失败（= 有判别力）。
    expect(view.state.doc.toString()).toBe('A\nB\n');
  });

  it('回归守门：普通 text/plain 粘贴仍逐字符入文档，且**触发脏标记**', async () => {
    const { app, tabId, view } = await setupApp('X');
    // 用户动作：把光标放到正文末尾（粘贴点即插入点）
    act(() => {
      view.dispatch({ selection: { anchor: view.state.doc.length } });
    });
    expect(app.store.useScratchStore.getState().tabs.find((tab) => tab.id === tabId)?.dirty)
      .toBe(false);

    firePaste(view, { 'text/plain': 'Y' });

    expect(view.state.doc.toString()).toBe('XY');
    expect(app.store.useScratchStore.getState().tabs.find((tab) => tab.id === tabId)?.dirty)
      .toBe(true);
  });

  it('回归守门：多行 + 尾随换行的纯文本逐字符相等（不被裁剪、不被改写）', async () => {
    const { view } = await setupApp('');
    firePaste(view, { 'text/plain': 'line1\nline2\n' });
    expect(view.state.doc.toString()).toBe('line1\nline2\n');
  });

  it('回归守门：剪贴板同时有 text/html 时仍只入 text/plain —— 不得出现 HTML 标签或 CF_HTML 头字段', async () => {
    const { view } = await setupApp('');
    firePaste(view, {
      'text/html': '<b>HTML-EL</b>',
      'text/plain': 'a\nb',
    });

    const doc = view.state.doc.toString();
    expect(doc).toBe('a\nb');
    expect(doc).not.toContain('HTML-EL');
    expect(doc).not.toContain('<b>');
    expect(doc).not.toContain('Version:');
    expect(doc).not.toContain('SourceURL:');
    expect(doc).not.toContain('StartFragment');
  });

  it('回归守门：空剪贴板（无 text/plain）⇒ 正文逐字符不变，且取值被真的执行过', async () => {
    const { view } = await setupApp('原样');
    const { getDataCalls } = firePaste(view, { 'text/html': '<b>x</b>', 'text/plain': '' });

    expect(view.state.doc.toString()).toBe('原样');
    // 判别力自证：CM6 内置处理器确实读了 `text/plain`（空串分支被真的执行过）。
    // 取到的次数可能 > 1 也正常：纯文本为空时 CM6 会再取 `text/uri-list`。
    expect(getDataCalls).toContain('text/plain');
  });

  it('安全守门：只读标签（大文件降级 / .diff）⇒ 正文不变，且**任何一方都不读剪贴板**', async () => {
    const app = await loadRealEditorApp();
    app.session.setAuthSnapshot({ expiresAt: null });
    app.authStore.setState({ isLoggedIn: true });
    vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
    vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });
    const tabId = openTab(app, '只读正文', true);
    render(createElement(app.Workspace));
    await flushEffects();

    const view = realViewOf(app, tabId);
    expect(view.state.readOnly).toBe(true);
    const { getDataCalls } = firePaste(view, { 'text/plain': 'a\nb' });

    // CM6 内置 `paste` 在 `state.readOnly` 为真时**取值之前**就 return
    // ⇒ 只读标签不会因为一次粘贴而暴露剪贴板内容
    expect(view.state.doc.toString()).toBe('只读正文');
    expect(getDataCalls).toEqual([]);
  });
});

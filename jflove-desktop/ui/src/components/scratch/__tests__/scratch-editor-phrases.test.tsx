/**
 * **AC-74 装配点**守门用例（本批 C1 的 CM6 面板部分）—— 设计 v1.6.1 §15.2.2 / §15.2.3 / §15.9.1
 *
 * ## 判据出处
 *
 * - **AC-74①②③⑤**：编辑器内**所有**用户可见面板文案为中文 —— 因此"短语表存在"不等于达标，
 *   必须证明它**真的装进了两个装配点**：
 *   ① `ScratchEditorHost.tsx` 的 `baseExtensions`（编辑器主面板）；
 *   ② `views/DiffView.tsx` 的 `config.a` / `config.b`（**两个各自独立**的 `EditorState`，M15 实测）；
 * - **设计 §15.2.3**：DiffView 的 `phrases` **不是共享的** —— 只装一侧，另一侧（合并折叠提示
 *   `$ unchanged lines`）会回落成英文 ⇒ 本文件对**两侧分别**断言。
 *
 * ## 观测方式（不是"存在性断言"）
 *
 * 不检查"数组里有个对象"，而是拿**真离线包**对装配产出的扩展求值：
 * `EditorState.create({ extensions }).phrase(key)` **必须**返回中文。
 * 这样即便有人把 `phrases` 挪走 / 传错字典 / 被更高优先级的扩展盖掉，用例也会红。
 *
 * ## 安全（AGENTS.md §9）
 *
 * 真离线包 + 真宿主；桥与草稿服务全是替身，不发真实网络请求、不读真实文件。
 */
import { act, fireEvent, render } from '@testing-library/react';
import { createElement } from 'react';
import type { ComponentType } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JFLoveCmApi, MergeEditorConfig, MergeViewConfig } from '../../../types/codemirror-bundle';
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

/** 捕获 `MergeView` 实参的替身记录（**只记配置、不建视图**；配置随后用**真** `EditorState` 求值） */
const mergeConfigs: MergeViewConfig[] = [];

interface RealEditorApp {
  store: typeof import('../../../stores/scratch-store');
  host: typeof import('../ScratchEditorHost');
  Workspace: ComponentType;
  DiffView: ComponentType<{ pane: import('../../../types/scratch').ScratchTab; text: string }>;
  service: typeof import('../../../services/scratch-service').scratchService;
  authStore: typeof import('../../../stores/auth-store').useAuthStore;
  session: typeof import('../../../utils/session');
  api: JFLoveCmApi;
}

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
  const diff = await import('../views/DiffView');
  return {
    store,
    host,
    Workspace: workspace.ScratchWorkspace as ComponentType,
    DiffView: diff.DiffView as RealEditorApp['DiffView'],
    service: serviceModule.scratchService,
    authStore: auth.useAuthStore,
    session,
    api,
  };
}

async function flushEffects(rounds = 12): Promise<void> {
  await act(async () => {
    for (let i = 0; i < rounds; i += 1) await Promise.resolve();
  });
}

/** 已登录 + 草稿服务替身（两条用例共用的前置） */
function prepare(app: RealEditorApp): void {
  app.session.setAuthSnapshot({ expiresAt: null });
  app.authStore.setState({ isLoggedIn: true });
  vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
  vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });
}

/** 建一个标签页并返回它的 store 快照（`DiffView` 需要 `ScratchTab`） */
function createTab(app: RealEditorApp, title: string) {
  let tabId = '';
  act(() => {
    tabId = app.store.useScratchStore.getState().createTab({ title, language: 'text' });
  });
  const tab = app.store.useScratchStore.getState().tabs.find((item) => item.id === tabId);
  if (!tab) throw new Error('标签未建成功');
  return tab;
}

beforeEach(() => {
  installJsdomRangeMeasureShim();
});

afterEach(() => {
  mergeConfigs.length = 0;
});

describe('AC-74 装配点 a：`ScratchEditorHost` 的 baseExtensions', () => {
  it('真宿主建出的编辑器状态里：查找 / 跳转行 / 折叠 / 控制字符 / diff 折叠提示全部为中文', async () => {
    const app = await loadRealEditorApp();
    prepare(app);
    const tab = createTab(app, 'f.txt');
    render(createElement(app.Workspace));
    await flushEffects();

    const session = app.host.getScratchEditorSession(tab.id);
    expect(session).not.toBeNull();
    const state = session?.view.state;
    if (!state) throw new Error('没有真实编辑器状态');

    // 逐条对应 AC-74 的判据（命中英文原文即失败：这里用 `toBe(中文)` 直接钉死）
    expect(state.phrase('Find')).toBe('查找');
    expect(state.phrase('Replace')).toBe('替换');
    expect(state.phrase('next')).toBe('下一个');
    expect(state.phrase('previous')).toBe('上一个');
    expect(state.phrase('match case')).toBe('区分大小写');
    expect(state.phrase('regexp')).toBe('正则表达式');
    expect(state.phrase('by word')).toBe('全词匹配');
    expect(state.phrase('replace all')).toBe('全部替换');
    expect(state.phrase('Go to line')).toBe('跳转到行');
    expect(state.phrase('go')).toBe('跳转');
    // AC-74③ 折叠占位符 / 折叠槽提示
    expect(state.phrase('folded code')).toBe('已折叠的代码');
    expect(state.phrase('unfold')).toBe('展开');
    expect(state.phrase('Fold line')).toBe('折叠此行');
    // AC-74④ 控制字符提示
    expect(state.phrase('Control character')).toBe('控制字符');
    // AC-74⑤ diff 折叠提示（`$` = 行数）
    expect(state.phrase('$ unchanged lines', 7)).toBe('未更改的 7 行');
  });
});

describe('AC-74 装配点 b：`views/DiffView` 的 a / b 两个 EditorState 各一份', () => {
  it('MergeView 的 `config.a` 与 `config.b` **分别**能本地化（只装一侧 ⇒ 另一侧必红）', async () => {
    const app = await loadRealEditorApp();
    prepare(app);

    // 第二段来源 = 本地文件（原生对话框与读盘都在服务层 ⇒ 全部替身，不碰真实文件系统）
    vi.spyOn(app.service, 'pickReadOnlyFile').mockResolvedValue('D:/tmp/second.txt');
    vi.spyOn(app.service, 'readTextOnly').mockResolvedValue({
      ok: true,
      path: 'D:/tmp/second.txt',
      name: 'second.txt',
      text: 'b1\nb2',
      language: 'text',
      encoding: 'utf-8',
      lineEnding: 'lf',
      readOnly: true,
    } as never);

    // ★ 用替身替换 `MergeView`：只捕获配置（本用例要断言的是**扩展**是否带短语表，
    //   用真 `EditorState` 直接给扩展求值比在 jsdom 里建真 MergeView 更精确）
    const realMergeView = app.api.MergeView;
    mergeConfigs.length = 0;
    (app.api as { MergeView: unknown }).MergeView = class {
      constructor(config: MergeViewConfig) {
        mergeConfigs.push(config);
      }

      reconfigure(config: MergeViewConfig): void {
        mergeConfigs.push(config);
      }

      destroy(): void {
        /* 替身无资源 */
      }
    };

    try {
      const tab = createTab(app, 'a.txt');
      const view = render(createElement(app.DiffView, { pane: tab, text: 'a1\na2' }));

      const fileButton = Array.from(view.container.querySelectorAll('button')).find(
        (button) => button.textContent === '本地文件',
      );
      expect(fileButton).toBeTruthy();
      await act(async () => {
        fireEvent.click(fileButton as HTMLButtonElement);
      });
      await flushEffects();

      // 前置自证：DiffView 真的把配置交给了 MergeView（否则下面的断言会"因为没有配置"而假绿）
      expect(mergeConfigs.length).toBeGreaterThan(0);
      const config = mergeConfigs[mergeConfigs.length - 1];
      // `MergeViewConfig.a/b` 的类型是 `MergeEditorConfig | EditorState`；DiffView 传的必然是前者
      const sideA = config.a as MergeEditorConfig;
      const sideB = config.b as MergeEditorConfig;
      expect(sideA.extensions).toBeTruthy();
      expect(sideB.extensions).toBeTruthy();

      // ★ 两侧**分别**求值：`config.a` / `config.b` 是两个独立 EditorState（M15 实测）
      const stateA = app.api.EditorState.create({
        doc: sideA.doc ?? '',
        extensions: (sideA.extensions ?? []) as never,
      });
      const stateB = app.api.EditorState.create({
        doc: sideB.doc ?? '',
        extensions: (sideB.extensions ?? []) as never,
      });

      expect(stateA.phrase('Find')).toBe('查找');
      expect(stateA.phrase('$ unchanged lines', 7)).toBe('未更改的 7 行');
      expect(stateB.phrase('Find')).toBe('查找');
      expect(stateB.phrase('$ unchanged lines', 7)).toBe('未更改的 7 行');
      // 只读语义不变（本轮只加短语，不动只读）
      expect(stateA.readOnly).toBe(true);
      expect(stateB.readOnly).toBe(true);
    } finally {
      (app.api as { MergeView: unknown }).MergeView = realMergeView;
    }
  });
});

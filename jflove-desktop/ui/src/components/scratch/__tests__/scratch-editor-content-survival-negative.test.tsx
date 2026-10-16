/**
 * **AC-77 主判据的负向对照**（本批 C1）—— 设计 §15.9.4 的 AC-77 注入①
 *
 * ## 注入的是什么
 *
 * 把写侧唯一的落点 `stores/scratch-store.ts::snapshotInitialText` **换成 no-op**
 * （见文件内 `vi.mock`）。这恰好等价于**修复前**的行为：卸载时没有任何快照写回
 * `initialTexts`（它早已被 `takeInitialText` 取走即删），于是重挂只能以**空 doc** 建立
 * （`ScratchEditorHost.tsx` 的 `EditorState.create({ doc: initialTextRef.current })`，
 * 而那里此刻是空串）。
 *
 * ## 判据
 *
 * 同一个「编辑 → 卸载工作区 → 重挂」流程，在**注入后**必须**拿不到**改后的正文
 * （即主判据 `scratch-editor-content-survival.test.tsx` 的那条断言**必须变红**）。
 * 若这条用例红了 —— 说明注入生效、且主判据真的咬得住这个缺陷；
 * 若这条用例**绿了**（注入后正文仍然活着）—— 说明主判据的"红"另有来源，
 * 必须回去复核主判据的判别力，而不是宣布修好了。
 *
 * 换句话：本文件不是"锁死缺陷行为"，而是**给主判据做的判别力证明**
 * （需求 §9.1 的判定原则：标注 `[负向对照]` 的条目必须能把错误实现注入回去并确认用例失败）。
 *
 * ## 安全（AGENTS.md §9）
 *
 * 与主判据文件相同：无 token / session_key，桥与草稿服务全是替身，不发真实网络请求。
 */
import { act, render } from '@testing-library/react';
import { createElement } from 'react';
import type { ComponentType } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

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
 * ★ 负向注入：把快照写入口改成 no-op（= 修复前的行为），其余导出逐字保留。
 *
 * 之所以在**模块层**注入而不是"改源码跑一遍"：源码是交付物，不能被测试改动；
 * 而 `snapshotInitialText` 是写侧**唯一**的存储落点，替换它即精确复现"卸载时什么都不存"。
 */
vi.mock('../../../stores/scratch-store', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../stores/scratch-store')>();
  return {
    ...real,
    // 注入点：不写、不抛、不改任何状态（等价于修复前不存在这个函数）
    snapshotInitialText: () => undefined,
  };
});

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

afterEach(() => {
  vi.useRealTimers();
});

describe('AC-77 负向对照：拿掉快照写回 ⇒ 主判据必须红', () => {
  it('注入「卸载即销毁且不快照」：同一往返流程后正文**不等于**改后内容（修复前形态）', async () => {
    const app = await loadRealEditorApp();
    app.session.setAuthSnapshot({ expiresAt: null });
    app.authStore.setState({ isLoggedIn: true });
    vi.spyOn(app.service, 'draftList').mockResolvedValue({ cleanExit: true, entries: [] });
    vi.spyOn(app.service, 'draftDiscard').mockResolvedValue({ ok: true });

    let tabId = '';
    act(() => {
      tabId = app.store.useScratchStore.getState().createTab({ title: 'f.txt', language: 'text' });
    });
    app.store.putInitialText(tabId, 'BASE');

    const first = render(createElement(app.Workspace));
    await flushEffects();
    const viewBefore = app.host.getScratchEditorSession(tabId)?.view;
    expect(viewBefore).toBeTruthy();
    expect(viewBefore?.state.doc.toString()).toBe('BASE');

    act(() => {
      viewBefore?.dispatch({ changes: { from: viewBefore.state.doc.length, insert: 'EDIT' } });
    });
    const edited = 'BASEEDIT';
    expect(viewBefore?.state.doc.toString()).toBe(edited);

    first.unmount();
    // 注入自证：快照通道**确实**没有被写入（否则下面的红不是注入造成的）
    expect(app.store.hasInitialText(tabId)).toBe(false);

    render(createElement(app.Workspace));
    await flushEffects();

    const after = app.host.getScratchEditorSession(tabId)?.view.state.doc.toString();
    // ★ 主判据（`toBe(edited)`）在这里**必须**失败 —— 本断言是它的镜像
    expect(after).not.toBe(edited);
    // 修复前的确切形态：重挂以空 doc 建立
    expect(after).toBe('');
  });
});

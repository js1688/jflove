/**
 * F-3 让路机制（快捷键捕获层遇到「正在录制键位」时不得抢键）+ 窗口活动门
 *
 * ## 迁移说明（v1.6.0 → v1.6.1）
 *
 * v1.6.0 的宿主是挂在 `RouterProvider` 之外的 `<ScratchPaneLayer />`（浮动窗格层）；
 * 本文件原先 `render(<ScratchPaneLayer />)` 后往 `[data-scratch-pane]` 里打事件。
 * v1.6.1 **删除**该组件，接线原样搬到模块级 `utils/scratch/shortcut-runtime.ts`
 * （设计 §4.7「承办方划分」），因此本文件改为：
 * 只 import 运行时模块（模块顶层 `:189` 自动装机一次），往 `[data-editor-panel]` 契约锚点里打事件。
 *
 * ## 判据出处
 *
 * - 设计 v1.6.1 `文档记录/设计文档/v1.6.1.md:583-597`（§4.7 承办方划分与 `Ctrl+W`）；
 *   v1.6.0 §4.2.5 / F-3：快捷键设置对话框**录制中**时，应用级捕获层必须让路，否则用户按下某个
 *   **已生效**的键位（如 `Ctrl+Alt+N`）会顺带触发一次动作。
 * - 实现 `utils/scratch/shortcut-runtime.ts:146`：`if (document.body.dataset.jfShortcutCapture) return;`；
 *   `:144`：`if (!document.hasFocus() || document.hidden) return;`。写入方是
 *   `components/scratch/ShortcutsSettingsDialog.tsx:258`（`= '1'`）与 `:261`（cleanup 里 `delete`）。
 * - 需求 `文档记录/需求文档/v1.6.0.md` AC-9 段落：冲突检测与录制期间不得静默触发其它功能。
 *
 * ## 旧判据的等价改写（对应验收判据 4）
 *
 * 1. 旧 `APP_SCOPE_IDS` 7 项里的 `pane.tile` / `pane.cascade`：设计 §4.7:581 已作废（键位不复存在）
 *    ⇒ **确实无等价判据**；接管面直接以 v1.6.1 的 `RUNTIME_SHORTCUTS` 7 项为准
 *    （新增 `file.save` / `file.saveAs` 两项，它们同时是面板作用域项）。
 * 2. 旧用 `panes` 断言「动作未执行」⇒ 等价改写为**标签页语义**（`tabs` / `pendingCloseId`），
 *    面板作用域项另加「零写入 / 零原生对话框」断言，比旧口径更强。
 * 3. 其余用例（标记清零后恢复接管 / 空串标记不触发让路 / 键名严格匹配 / `hasFocus` / `hidden`）
 *    语义不变、原样保留；面板作用域项的「恢复接管」是新补的等价加强。
 *
 * ## 断言口径
 *
 * 行为断言（dataset / 窗口活动 → 是否接管），逐条把 body 级全局标记清干净，
 * 避免污染同进程内的其它用例（`document.body.dataset` 是跨测试共享的全局状态）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_BINDINGS,
  keyboardEventToShortcut,
  parseShortcut,
} from '../../../config/shortcuts';
import type { ShortcutId } from '../../../config/shortcuts';
import { scratchService } from '../../../services/scratch-service';
import { useAuthStore } from '../../../stores/auth-store';
import { registerScratchContentAccessor, useScratchStore } from '../../../stores/scratch-store';
import { useShortcutStore } from '../../../stores/shortcut-store';
import { setAuthSnapshot } from '../../../utils/session';

// 副作用 import：`utils/scratch/shortcut-runtime.ts` 在模块顶层自动调用 `installEditorShortcutRuntime()` 一次（幂等）
import '../../../utils/scratch/shortcut-runtime';

// 运行时经 `revealWorkspace()` 揭示工作区（`router.navigate(EDITOR_ROUTE)`；路由常量住在
// `utils/scratch/editor-landing.ts` —— 行号不再写死，避免实现文件挪行后注释失真）；
// 这里给一个内存路由，避免把整张真实路由表（含全部页面）拉进单测。
vi.mock('../../../config/routes', async () => {
  const { createMemoryRouter } = await import('react-router');
  return {
    router: createMemoryRouter([{ path: '*', Component: () => null }], { initialEntries: ['/'] }),
  };
});

/** 运行时接管面（`shortcut-runtime.ts:55-63` 的 `RUNTIME_SHORTCUTS`，7 项） */
const RUNTIME_IDS: readonly ShortcutId[] = [
  'pane.new',
  'pane.close',
  'pane.next',
  'pane.prev',
  'file.open',
  'file.save',
  'file.saveAs',
];

/** 面板作用域（`shortcut-runtime.ts:66`）—— 只有落在 `[data-editor-panel]` 内才可能接管 */
const PANEL_SCOPE_IDS: readonly ShortcutId[] = ['pane.close', 'file.save', 'file.saveAs'];

const KEY_FROM_CHORD: Readonly<Record<string, string>> = {
  Right: 'ArrowRight',
  Left: 'ArrowLeft',
  Up: 'ArrowUp',
  Down: 'ArrowDown',
  Space: ' ',
};

function keyboardInitFor(binding: string): KeyboardEventInit {
  const chord = parseShortcut(binding);
  if (!chord) throw new Error(`非法键位：${binding}`);
  const key =
    KEY_FROM_CHORD[chord.key] ?? (/^[A-Z0-9]$/.test(chord.key) ? chord.key.toLowerCase() : chord.key);
  return {
    key,
    ctrlKey: chord.ctrl,
    altKey: chord.alt,
    shiftKey: chord.shift,
    metaKey: chord.meta,
    bubbles: true,
    cancelable: true,
  };
}

/** 按下某功能当前生效的键位，观察「是否被运行时接管」 */
function pressBinding(
  id: ShortcutId,
  target: HTMLElement,
): { prevented: boolean; reachedTarget: boolean; chord: string } {
  const binding = useShortcutStore.getState().getBinding(id);
  const event = new KeyboardEvent('keydown', keyboardInitFor(binding));
  let reachedTarget = false;
  const listener = () => {
    reachedTarget = true;
  };
  target.addEventListener('keydown', listener);
  target.dispatchEvent(event);
  target.removeEventListener('keydown', listener);
  return {
    prevented: event.defaultPrevented,
    reachedTarget,
    chord: keyboardEventToShortcut(event) ?? '',
  };
}

/** 在面板内派生一个可打事件的目标（面板作用域判定锚点 = 设计 §4.7:443 的 DOM 契约） */
function panelTargetFor(tabId: string): HTMLElement {
  const host = document.createElement('div');
  host.setAttribute('data-editor-panel', tabId);
  host.tabIndex = -1;
  document.body.appendChild(host);
  return host;
}

/** `savePane` 缺正文读取器会直接拒绝（`stores/scratch-store.ts:637-642`）⇒ 保存类用例要注册 */
const accessorReleases: Array<() => void> = [];

function registerContentAccessor(tabId: string, text: string): void {
  accessorReleases.push(registerScratchContentAccessor(tabId, () => ({ text })));
}

let outsideTarget: HTMLElement;

beforeEach(() => {
  document.body.tabIndex = -1;
  document.body.focus();
  expect(document.hasFocus(), 'jsdom 焦点前提：document.hasFocus() 必须为 true').toBe(true);

  outsideTarget = document.createElement('div');
  outsideTarget.tabIndex = -1;
  document.body.appendChild(outsideTarget);

  setAuthSnapshot({ expiresAt: null });
  useAuthStore.setState({ isLoggedIn: true });

  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  useShortcutStore.setState({
    overrides: {},
    bindings: { ...DEFAULT_BINDINGS },
    conflicts: [],
    lastError: null,
  });

  // 服务替身：本文件只关心「是否让路」，但"动作未执行"需要能观测到 → 全部替身化
  vi.spyOn(scratchService, 'pickFiles').mockResolvedValue([]);
  vi.spyOn(scratchService, 'pickSaveTarget').mockResolvedValue('D:/tmp/yield-picked.txt');
  vi.spyOn(scratchService, 'writeText').mockResolvedValue({
    ok: true,
    size: 1,
    mtime: 0,
    sha256: 'x',
  });
  vi.spyOn(scratchService, 'draftSave').mockResolvedValue({ ok: true, at: 1 });
  vi.spyOn(scratchService, 'draftDiscard').mockResolvedValue({ ok: true });
  vi.spyOn(scratchService, 'reportDirty').mockResolvedValue({ ok: true });
});

afterEach(() => {
  // F-3 的让路标记必须清掉：否则后续用例（含其它文件的运行时）会全部「让路」而静默失效
  delete document.body.dataset.jfShortcutCapture;
  useAuthStore.setState({ isLoggedIn: false });
  outsideTarget.remove();
  document.querySelectorAll('[data-editor-panel]').forEach((node) => node.remove());
  accessorReleases.splice(0).forEach((release) => release());
  vi.restoreAllMocks();
});

describe('F-3 录制期间让路', () => {
  it('录制中（`jfShortcutCapture="1"`）7 项接管面键位全部让路：不拦、不阻止传播、不执行动作', async () => {
    // 面板作用域项（`pane.close` / `file.save` / `file.saveAs`）刻意打**面板内**目标：
    // 让路判定（`:146`）在面板判定（`:154-158`）之前，因此这 3 项也必须让路（最强的让路口径）
    const tabId = useScratchStore.getState().createTab({
      title: 'yield.txt',
      path: 'D:/tmp/yield.txt',
    });
    registerContentAccessor(tabId, 'F-3 正文');
    const panel = panelTargetFor(tabId);
    const tabsBefore = useScratchStore.getState().tabs.length;

    document.body.dataset.jfShortcutCapture = '1';

    for (const id of RUNTIME_IDS) {
      const target = PANEL_SCOPE_IDS.includes(id) ? panel : outsideTarget;
      const { prevented, reachedTarget, chord } = pressBinding(id, target);
      expect(prevented, `${id} 在录制期间被拦截了（${chord}）`).toBe(false);
      expect(reachedTarget, `${id} 在录制期间不应阻止事件继续传播`).toBe(true);
    }

    // 让路 = 连动作也不执行（标签页数不变、零写盘、零原生对话框）
    await Promise.resolve();
    expect(useScratchStore.getState().tabs, '`pane.new` 不得新建标签页').toHaveLength(tabsBefore);
    expect(useScratchStore.getState().pendingCloseId, '`pane.close` 不得发起关闭请求').toBeNull();
    expect(scratchService.pickFiles, '`file.open` 不得弹原生打开对话框').not.toHaveBeenCalled();
    expect(scratchService.pickSaveTarget, '`file.saveAs` 不得弹原生保存对话框').not.toHaveBeenCalled();
    expect(scratchService.writeText, '保存类项不得写盘').not.toHaveBeenCalled();
  });

  it('标记清零后同一次按键恢复接管（让路是「暂时」的，不是永久降级）', () => {
    document.body.dataset.jfShortcutCapture = '1';
    const during = pressBinding('pane.new', outsideTarget);
    expect(during.prevented, '录制期间不接管').toBe(false);
    expect(useScratchStore.getState().tabs).toHaveLength(0);

    // 对话框 cleanup（`ShortcutsSettingsDialog.tsx:261`）删除标记
    delete document.body.dataset.jfShortcutCapture;
    expect(document.body.dataset.jfShortcutCapture).toBeUndefined();

    const after = pressBinding('pane.new', outsideTarget);
    expect(after.prevented, '结束录制后应恢复接管').toBe(true);
    expect(after.reachedTarget).toBe(false);
    expect(useScratchStore.getState().tabs, '恢复接管后动作应执行').toHaveLength(1);
  });

  it('面板作用域的项在标记清零后同样恢复接管（`file.save` 面板内 ⇒ 真的写盘）', async () => {
    const tabId = useScratchStore.getState().createTab({
      title: 'yield-resume.txt',
      path: 'D:/tmp/yield-resume.txt',
    });
    registerContentAccessor(tabId, 'F-3 恢复正文');
    const panel = panelTargetFor(tabId);

    document.body.dataset.jfShortcutCapture = '1';
    expect(pressBinding('file.save', panel).prevented, '录制期间不接管').toBe(false);
    await Promise.resolve();
    expect(scratchService.writeText).not.toHaveBeenCalled();

    delete document.body.dataset.jfShortcutCapture;
    expect(pressBinding('file.save', panel).prevented, '结束录制后应恢复接管').toBe(true);
    await vi.waitFor(() => {
      expect(scratchService.writeText, '恢复接管后应真的写盘').toHaveBeenCalledTimes(1);
    });
  });

  it('只有非空标记才让路：`jfShortcutCapture=""` 视为没有标记（数据集语义守门）', () => {
    // `dataset.x = ''` 会让 `if (document.body.dataset.jfShortcutCapture)` 取到空串（假值）
    document.body.dataset.jfShortcutCapture = '';
    const { prevented } = pressBinding('pane.new', outsideTarget);
    expect(prevented, '空串标记不应让路').toBe(true);
  });

  it('让路只认 `jfShortcutCapture` 这一个键名：其它 dataset 键不影响接管', () => {
    document.body.dataset.jfShortcutCaptureOther = '1';
    const { prevented } = pressBinding('pane.new', outsideTarget);
    expect(prevented, '键名不匹配时不应让路').toBe(true);
  });
});

describe('F-3 让路的另一半：窗口未激活 / 文档不可见时不接管', () => {
  it('`document.hasFocus()` 为 false 时不接管（窗口未激活，`shortcut-runtime.ts:144`）', () => {
    const focusSpy = vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    const { prevented } = pressBinding('pane.new', outsideTarget);
    expect(prevented, '窗口未激活时不应接管').toBe(false);
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    focusSpy.mockRestore();
  });

  it('面板作用域的项在窗口未激活时同样不接管（含不写盘）', async () => {
    const tabId = useScratchStore.getState().createTab({
      title: 'yield-blurred.txt',
      path: 'D:/tmp/yield-blurred.txt',
    });
    registerContentAccessor(tabId, 'F-3 失焦正文');
    const panel = panelTargetFor(tabId);

    const focusSpy = vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    const { prevented } = pressBinding('file.save', panel);
    expect(prevented, '窗口未激活时面板作用域项也不应接管').toBe(false);
    await Promise.resolve();
    expect(scratchService.writeText).not.toHaveBeenCalled();
    focusSpy.mockRestore();
  });

  it('`document.hidden` 为 true 时不接管（文档不可见，`shortcut-runtime.ts:144`）', () => {
    const hiddenSpy = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    const { prevented } = pressBinding('pane.new', outsideTarget);
    expect(prevented, '文档不可见时不应接管').toBe(false);
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    hiddenSpy.mockRestore();
  });
});

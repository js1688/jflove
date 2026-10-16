/**
 * 应用级快捷键运行时（模块级常驻）的**接管口径**—— AC-10 / AC-63 ② / AC-70 单测侧
 *
 * ## 迁移说明（v1.6.0 → v1.6.1）
 *
 * v1.6.0 的宿主是挂在 `RouterProvider` 之外的 `<ScratchPaneLayer />`（浮动窗格层），
 * 本文件原名 `shortcut-intercept-ac10.test.tsx`、`render(<ScratchPaneLayer />)` 后往
 * `[data-scratch-pane]` 里打事件。v1.6.1 **删除**该组件（设计 §4.7「承办方划分」把接线
 * 原样搬到模块级 `utils/scratch/shortcut-runtime.ts`），因此本文件改为：
 * 只 import 运行时模块（模块顶层 `:189` 自动装机一次），往 `[data-editor-panel]` 契约锚点里打事件。
 *
 * ## 判据出处
 *
 * - 设计 v1.6.1 `文档记录/设计文档/v1.6.1.md:551`（§4.7 快捷键）、`:583-591`（承办方划分：
 *   应用级 7 项由 `window` **捕获阶段**接管，其余 11 项归 CM6 keymap + 面板内判定）、
 *   `:593-597`（`Ctrl+W` 的接管与真机判据）、`:443`（DOM 契约：`data-editor-panel="<tabId>"`）；
 * - 需求 v1.6.1 `文档记录/需求文档/v1.6.1.md:468`（AC-63 ②：未登录按快捷键**无新标签页、无工作区**）、
 *   `:475`（AC-70：`Ctrl+W` 真实生效 —— 脏标签出询问、干净标签被关闭）；
 * - 实现：`utils/scratch/shortcut-runtime.ts:55-63`（`RUNTIME_SHORTCUTS` 7 项）、`:66`
 *   （`PANEL_SCOPE_SHORTCUTS`）、`:137-165`（三道前置 → 命中 → `preventDefault + stopPropagation`）。
 *
 * ## 逐条说明「为什么旧判据作废 / 如何改写」（对应验收判据 4）
 *
 * 1. **旧 `APP_SCOPE_IDS` 9 项里的 `pane.tile` / `pane.cascade` 作废** —— 设计 `:581`
 *    「已作废：`pane.tile`（平铺 `Ctrl+Alt+T`）、`pane.cascade`（层叠 `Ctrl+Alt+C`）—— 配置界面**不得**出现」
 *    + 需求 `§3.2`（条目 20 → 18）。故旧用例里这两项**没有等价物**（键位本身已不存在），
 *    代之以「18 项、作废键位不再登记」的自检。
 * 2. **旧 `it.fails`「窗格聚焦类 11 项也应被渲染层接管（M-9）」作废** —— 设计 `:587-588` 明确
 *    「编辑面板聚焦（其余 **11** 项）」的承办方是 **CM6 keymap（`Prec.highest`）+ 面板内 DOM 判定**，
 *    不在 `window` 运行时里。旧期望与 v1.6.1 的设计**相反**，不能保留 `it.fails`（那会把
 *    "设计"当"缺陷"）。改写为**更强的边界判据**：这 11 项**必须不被** window 运行时 `preventDefault`
 *    （防"接管面被无意扩大"把 CM6 的自有键位抢掉）。
 * 3. **旧 `it.fails`「工具箱 12 项应有已登记键位」作废** —— 设计 `:591` 曾把
 *    `view.toolbox` / `view.diff` 无接管方登记为 BACKLOG 候选 **BL-51**。
 *    **2026-10-15/16 用户实测反馈第二轮已闭环 BL-51**（设计 §15.3.5 / 需求 AC-75②）：
 *    这**两项现在必须被 window 运行时接管**，由运行时往 `stores/scratch-ui-store.ts`
 *    投递一次性面板请求，面板（工具栏）负责真正打开弹框 / 覆盖层。
 *    因此旧期望「不接管」**已被 AC-75② 取代**（"配了但按了没反应即失败"），
 *    本文件把它改写为**阳性判据**（见 `describe('AC-75 ② …')`），
 *    并在 `shortcut-intercept-ac10.test.tsx` 同步改写（理由进测试报告 §0.2）。
 * 4. **`pane.next` / `pane.prev` 的 N-1 死键已在 Phase 5 回修闭环**（原为 `it.fails`，现为**通过**的 `it`）：
 *    根因是 `DEFAULT_BINDINGS` 取了 `def.defaultKey` 原文（`Ctrl+Alt+ArrowRight/Left`）而事件侧归一成
 *    `Ctrl+Alt+Right/Left` ⇒ `getBinding(id) === chord` 永不成立（v1.6.0 遗留的 N-1 / BL-35）。
 *    回修后 `config/shortcuts.ts` 改取 `def.meta.normalizedDefault`，并把这两项在原两个文件里的 4 条
 *    `it.fails` **转正**为 `it`（本文件 2 条 + `shortcut-intercept-ac10.test.tsx` 2 条）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_BINDINGS,
  SHORTCUT_IDS,
  getShortcutDef,
  keyboardEventToShortcut,
  normalizeShortcut,
  parseShortcut,
} from '../../../config/shortcuts';
import type { ShortcutId } from '../../../config/shortcuts';
import { scratchService } from '../../../services/scratch-service';
import { useAuthStore } from '../../../stores/auth-store';
import { useScratchStore, registerScratchContentAccessor } from '../../../stores/scratch-store';
import { clearPanelRequest, consumePanelRequest, useScratchUiStore } from '../../../stores/scratch-ui-store';
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

/** 运行时接管面（`shortcut-runtime.ts` 的 `RUNTIME_SHORTCUTS`，逐项对齐；本轮 7 → **9** 项） */
const RUNTIME_IDS: readonly ShortcutId[] = [
  'pane.new',
  'pane.close',
  'pane.next',
  'pane.prev',
  'file.open',
  'file.save',
  'file.saveAs',
  // ★ v1.6.1 本轮新增（AC-75② / 闭环 BL-51）：两项由运行时接管并投递面板请求
  'view.toolbox',
  'view.diff',
];

/** 面板作用域（`shortcut-runtime.ts` 的 `PANEL_SCOPE_SHORTCUTS`）—— 目标必须落在面板内 */
const PANEL_SCOPE_IDS: readonly ShortcutId[] = ['pane.close', 'file.save', 'file.saveAs'];

/** 非接管面（9 项 = 18 − 9）：承办方是 CM6 keymap + 面板内判定（设计 `:587-588`） */
const NON_RUNTIME_IDS: readonly ShortcutId[] = SHORTCUT_IDS.filter(
  (id) => !RUNTIME_IDS.includes(id),
);

/** 归一键名 → `KeyboardEvent.key`（`keyboardEventToShortcut` 只认 `event.key`） */
const KEY_FROM_CHORD: Readonly<Record<string, string>> = {
  Right: 'ArrowRight',
  Left: 'ArrowLeft',
  Up: 'ArrowUp',
  Down: 'ArrowDown',
  Space: ' ',
};

/** 由归一键位串造出 `KeyboardEventInit`（单字符键名转小写，方向键转 `Arrow*`） */
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

/** 按下某功能当前生效的键位，返回三个可观测事实 */
function pressBinding(
  id: ShortcutId,
  target: HTMLElement,
): { event: KeyboardEvent; binding: string; prevented: boolean; reachedTarget: boolean } {
  const binding = useShortcutStore.getState().getBinding(id);
  const event = new KeyboardEvent('keydown', keyboardInitFor(binding));
  let reachedTarget = false;
  const listener = () => {
    reachedTarget = true;
  };
  // 目标节点上的监听器：若运行时在捕获阶段 stopPropagation，它就不会被调用
  target.addEventListener('keydown', listener);
  target.dispatchEvent(event);
  target.removeEventListener('keydown', listener);
  return { event, binding, prevented: event.defaultPrevented, reachedTarget };
}

/** 在面板内派生一个可打事件的目标（面板作用域判定锚点 = 设计 `:443` 的 DOM 契约） */
function panelTargetFor(tabId: string): HTMLElement {
  const host = document.createElement('div');
  host.setAttribute('data-editor-panel', tabId);
  host.tabIndex = -1;
  document.body.appendChild(host);
  return host;
}

/**
 * `savePane` 在**缺正文读取器**时直接以 `lastError` 拒绝、连磁盘都不碰
 * （`stores/scratch-store.ts:637-642`）⇒ 保存类用例必须先注册真值读取器。
 */
const accessorReleases: Array<() => void> = [];

function registerContentAccessor(tabId: string, text: string): void {
  accessorReleases.push(registerScratchContentAccessor(tabId, () => ({ text })));
}

let outsideTarget: HTMLElement;

beforeEach(() => {
  // jsdom 里 `document.hasFocus()` 初始为 false（运行时会直接让路）⇒ 先把焦点给 body 造出「窗口激活」
  document.body.tabIndex = -1;
  document.body.focus();
  expect(document.hasFocus(), 'jsdom 焦点前提：document.hasFocus() 必须为 true').toBe(true);

  // 面板外目标：用于验证「面板作用域项落在面板外必须放行」（`:157-158`）
  outsideTarget = document.createElement('div');
  outsideTarget.tabIndex = -1;
  document.body.appendChild(outsideTarget);

  // 准入：默认已登录且会话未过期（`editor-gate.ts:47`）；AC-63 ② 的用例自己翻成未登录
  setAuthSnapshot({ expiresAt: null });
  useAuthStore.setState({ isLoggedIn: true });

  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  // 面板请求是模块级 store 的一次性状态：不清会让上个用例的请求泄到下个用例
  clearPanelRequest();
  useShortcutStore.setState({
    overrides: {},
    bindings: { ...DEFAULT_BINDINGS },
    conflicts: [],
    lastError: null,
  });

  // 服务替身：本文件只关心「是否接管」，不关心落盘细节
  vi.spyOn(scratchService, 'pickFiles').mockResolvedValue([]);
  vi.spyOn(scratchService, 'pickSaveTarget').mockResolvedValue(null);
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
  // F-3 的让路标记是 body 级全局状态：**必须清掉**，否则会污染同进程内的其它用例
  delete document.body.dataset.jfShortcutCapture;
  useAuthStore.setState({ isLoggedIn: false });
  clearPanelRequest();
  outsideTarget.remove();
  document.querySelectorAll('[data-editor-panel]').forEach((node) => node.remove());
  accessorReleases.splice(0).forEach((release) => release());
  vi.restoreAllMocks();
});

describe('接管面自检（条目集 20 → 18 / 运行时 9 项）', () => {
  it('18 项默认键位都能由 KeyboardEvent 还原成同一归一化形态（保证下面的「按下」真的按到了该键位）', () => {
    expect(SHORTCUT_IDS, 'v1.6.1 §4.7：条目集 20 → 18').toHaveLength(18);
    for (const id of SHORTCUT_IDS) {
      const binding = useShortcutStore.getState().getBinding(id);
      expect(binding, `${id} 应有默认键位`).not.toBe('');
      const event = new KeyboardEvent('keydown', keyboardInitFor(binding));
      expect(keyboardEventToShortcut(event), `${id} 的 ${binding} 往返不一致`).toBe(
        normalizeShortcut(binding),
      );
    }
  });

  it('作废条目 `pane.tile` / `pane.cascade` 不再登记（设计 §4.7:581 / 需求 §3.2）', () => {
    expect(SHORTCUT_IDS as readonly string[]).not.toContain('pane.tile');
    expect(SHORTCUT_IDS as readonly string[]).not.toContain('pane.cascade');
  });
});

describe('AC-63 ② [负向对照] 未登录：应用级快捷键不接管（含不阻止宿主默认动作）', () => {
  it('未登录时运行时的 9 项键位全部不接管：不 preventDefault、不 stopPropagation、不执行动作', () => {
    useAuthStore.setState({ isLoggedIn: false });
    const panel = panelTargetFor('ac63-tab');

    for (const id of RUNTIME_IDS) {
      // 面板作用域项也要放在面板内按：确保"不接管"来自登录门而不是面板判定
      const target = PANEL_SCOPE_IDS.includes(id) ? panel : outsideTarget;
      const { prevented, reachedTarget, binding } = pressBinding(id, target);
      expect(prevented, `${id}（${binding}）在未登录时不得 preventDefault（AC-63 ②）`).toBe(false);
      expect(reachedTarget, `${id} 在未登录时不得 stopPropagation`).toBe(true);
    }
    // 「无新标签页」：连动作也不执行
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    expect(useScratchStore.getState().lastError).toBeNull();
    // 未登录也不得投递任何面板请求（`view.toolbox` / `view.diff` 不得例外）
    expect(useScratchUiStore.getState().panelRequest).toBeNull();
  });
});

describe('AC-10 [负向对照] 已登录：宿主占用组合键被运行时拦下', () => {
  it('把「新建标签页」改配到宿主占用的 `Ctrl+P`：面板内按下被 preventDefault + stopPropagation，且动作执行', () => {
    // `Ctrl+P` 是宿主（打印）默认动作占用的组合，正合 AC-10 的「如打印、打开类」样本
    useShortcutStore.getState().setBinding('pane.new', 'Ctrl+P');
    expect(useShortcutStore.getState().getBinding('pane.new')).toBe('Ctrl+P');

    const { event, prevented, reachedTarget } = pressBinding('pane.new', outsideTarget);
    expect(event.defaultPrevented, '宿主默认动作必须被拦下').toBe(true);
    expect(prevented).toBe(true);
    expect(reachedTarget, '接管后应 stopPropagation，不再流向内部').toBe(false);
    // 「触发编辑器行为」：`pane.new` 的真实动作就是新建标签页
    expect(useScratchStore.getState().tabs).toHaveLength(1);
    expect(useScratchStore.getState().lastError).toBeNull();
  });

  it('window 作用域的默认键位逐项被接管（`pane.new` / `file.open`）', () => {
    for (const id of ['pane.new', 'file.open'] as const) {
      const { prevented, reachedTarget, binding } = pressBinding(id, outsideTarget);
      expect(prevented, `${id}（${binding}）未被运行时接管`).toBe(true);
      expect(reachedTarget, `${id}（${binding}）应停止继续传播`).toBe(false);
    }
    // `pane.new` 建了 1 个标签页、`file.open` 走了原生对话框（替身返回空）
    expect(useScratchStore.getState().tabs).toHaveLength(1);
    expect(scratchService.pickFiles).toHaveBeenCalledTimes(1);
  });

  it('未挂载面板时 `pane.new` 也能建标签页（应用级语义：任意路由页可用）', () => {
    expect(document.querySelector('[data-editor-panel]')).toBeNull();
    const { prevented } = pressBinding('pane.new', outsideTarget);
    expect(prevented).toBe(true);
    expect(useScratchStore.getState().tabs).toHaveLength(1);
  });

  for (const id of ['pane.next', 'pane.prev'] as const) {
    it(`${id} 在任意目标上按下时应被接管（N-1 / BL-35 已修：默认值取归一化形态）`, () => {
      const binding = useShortcutStore.getState().getBinding(id);
      // 生效值必须已是归一化形态，否则事件侧永远匹配不上（设计 §4.7:559-560 期望它是活的）
      expect(normalizeShortcut(binding), `${id} 生效值未归一化`).toBe(binding);
      const { prevented } = pressBinding(id, outsideTarget);
      expect(prevented, `${id}（${binding}）未被运行时接管`).toBe(true);
    });
  }
});

describe('AC-70 单测侧：`Ctrl+W`（`pane.close`）在编辑面板内被接管', () => {
  it('脏标签在面板内按 `Ctrl+W` ⇒ 被接管，且交给 store 发起关闭请求（`pendingCloseId` = 该标签）', () => {
    const tabId = useScratchStore.getState().createTab({ title: '脏标签页' });
    useScratchStore.getState().markDirty(tabId, true);
    const panel = panelTargetFor(tabId);

    const { prevented, reachedTarget, binding } = pressBinding('pane.close', panel);
    expect(binding, '`pane.close` 默认键 = `Ctrl+W`（设计 §4.7:558）').toBe('Ctrl+W');
    expect(prevented, '`Ctrl+W` 必须被拦下（AC-70：不触发宿主默认动作）').toBe(true);
    expect(reachedTarget).toBe(false);
    // 脏标签：**不是**直接关闭，而是登记关闭请求（工作区据此弹「保存 / 放弃 / 取消」）
    expect(useScratchStore.getState().pendingCloseId).toBe(tabId);
    expect(useScratchStore.getState().tabs).toHaveLength(1);
  });

  it('干净标签在面板内按 `Ctrl+W` ⇒ 被接管且标签真的被关闭（草稿同步丢弃）', () => {
    const tabId = useScratchStore.getState().createTab({ title: '干净标签页' });
    const panel = panelTargetFor(tabId);

    const { prevented } = pressBinding('pane.close', panel);
    expect(prevented).toBe(true);
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    expect(useScratchStore.getState().activeTabId).toBeNull();
    // `closeTab` 对干净标签自己 `void draftDiscard(id)`（`stores/scratch-store.ts:410-412`）
    expect(scratchService.draftDiscard).toHaveBeenCalledWith(tabId);
  });

  it('面板外按 `Ctrl+W` ⇒ 放行（面板作用域项的边界，`shortcut-runtime.ts:157-158`）', () => {
    useScratchStore.getState().createTab({ title: '面板外不关' });
    const { prevented, reachedTarget } = pressBinding('pane.close', outsideTarget);
    expect(prevented, '面板作用域项落在面板外必须放行').toBe(false);
    expect(reachedTarget).toBe(true);
    expect(useScratchStore.getState().tabs).toHaveLength(1);
    expect(useScratchStore.getState().pendingCloseId).toBeNull();
  });
});

describe('面板作用域：`file.save` / `file.saveAs` 面板内接管、面板外放行', () => {
  it('面板内 `Ctrl+S` ⇒ 被接管并真的走 `savePane` 写回原路径', async () => {
    const tabId = useScratchStore.getState().createTab({
      title: 'ac37.txt',
      path: 'D:/tmp/ac37.txt',
    });
    const tab = useScratchStore.getState().tabs[0];
    expect(tab.path).toBe('D:/tmp/ac37.txt');
    // 注册正文读取器：`savePane` 写的是「编辑器真值」，不是 store 里的缓存
    registerContentAccessor(tabId, 'ac37 正文');
    const panel = panelTargetFor(tabId);

    const { prevented, reachedTarget } = pressBinding('file.save', panel);
    expect(prevented).toBe(true);
    expect(reachedTarget).toBe(false);

    await vi.waitFor(() => {
      expect(scratchService.writeText).toHaveBeenCalledTimes(1);
    });
    // 「保存」= 写回**原路径**、不弹对话框；写的内容来自注册的正文读取器
    expect(scratchService.pickSaveTarget).not.toHaveBeenCalled();
    expect(scratchService.writeText).toHaveBeenCalledWith(
      expect.objectContaining({ path: 'D:/tmp/ac37.txt', text: 'ac37 正文' }),
    );
    expect(useScratchStore.getState().lastError).toBeNull();
  });

  it('面板外 `Ctrl+S` ⇒ 放行（不接管、零写入）', async () => {
    const tabId = useScratchStore.getState().createTab({
      title: 'outside.txt',
      path: 'D:/tmp/outside.txt',
    });
    void tabId;
    const { prevented, reachedTarget } = pressBinding('file.save', outsideTarget);
    expect(prevented, '面板作用域项落在面板外必须放行').toBe(false);
    expect(reachedTarget).toBe(true);
    // 给微任务一次机会：确认不是"接管了但异步没跑完"
    await Promise.resolve();
    expect(scratchService.writeText).not.toHaveBeenCalled();
    expect(useScratchStore.getState().tabs[0].path).toBe('D:/tmp/outside.txt');
  });

  it('面板内 `Ctrl+Shift+S`（另存为）⇒ 被接管且强制弹原生保存对话框', async () => {
    vi.mocked(scratchService.pickSaveTarget).mockResolvedValue('D:/tmp/save-as.txt');
    const tabId = useScratchStore.getState().createTab({
      title: 'saveas.txt',
      path: 'D:/tmp/saveas.txt',
    });
    registerContentAccessor(tabId, 'saveAs 正文');
    const panel = panelTargetFor(tabId);

    const { prevented, binding } = pressBinding('file.saveAs', panel);
    expect(binding).toBe('Ctrl+Shift+S');
    expect(prevented).toBe(true);
    await vi.waitFor(() => {
      expect(scratchService.pickSaveTarget).toHaveBeenCalledTimes(1);
    });
    await vi.waitFor(() => {
      expect(scratchService.writeText).toHaveBeenCalledTimes(1);
    });
    // 「另存为」= 强制弹原生对话框，并写到**新选**的目标
    expect(scratchService.writeText).toHaveBeenCalledWith(
      expect.objectContaining({ path: 'D:/tmp/save-as.txt', text: 'saveAs 正文' }),
    );
    expect(useScratchStore.getState().tabs[0].path).toBe('D:/tmp/save-as.txt');
  });
});

describe('边界：运行时不越权接管非本版接管面（9 项，设计 §4.7:587-588）', () => {
  for (const id of NON_RUNTIME_IDS) {
    it(`${id} 在面板内按下时**不**被 window 运行时接管（承办方 = CM6 keymap + 面板内判定）`, () => {
      const panel = panelTargetFor('non-runtime-tab');
      const { prevented, binding } = pressBinding(id, panel);
      expect(
        prevented,
        `${id}（${binding}）被运行时接管了 —— 接管面被扩大，会抢走 CM6 的自有键位`,
      ).toBe(false);
    });
  }
});

// ── v1.6.1 本轮（用户实测反馈 F3 / AC-75）─────────────────────────────────────────

/** 6 项新默认键（用户裁决 V-3 的字面值；改错任何一项都要有对应用例红）
 *  ⚠ 2026-10-16 更正面：`view.diff` 由 `Ctrl+D` 改为 **`Ctrl+Shift+D`** —— `Ctrl+D` 是 CM6
 *  `searchKeymap` 的 `selectNextOccurrence`（"选中下一个相同词"），必须留给编辑器。 */
const NEW_DEFAULTS: Readonly<Record<string, string>> = {
  'pane.new': 'Ctrl+N',
  'file.open': 'Ctrl+O',
  'pane.next': 'Ctrl+PageDown',
  'pane.prev': 'Ctrl+PageUp',
  'view.toolbox': 'Ctrl+K',
  'view.diff': 'Ctrl+Shift+D',
};

/** 其余 12 项（**一字不动**，AC-75⑥） */
const UNCHANGED_DEFAULTS: Readonly<Record<string, string>> = {
  'pane.close': 'Ctrl+W',
  'file.save': 'Ctrl+S',
  'file.saveAs': 'Ctrl+Shift+S',
  'pane.togglePreview': 'Ctrl+Shift+V',
  'edit.find': 'Ctrl+F',
  'edit.gotoLine': 'Ctrl+G',
  'edit.commentToggle': 'Ctrl+/',
  'edit.blockSelectModifier': 'Alt',
  'view.zoomIn': 'Ctrl+=',
  'view.zoomOut': 'Ctrl+-',
  'view.zoomReset': 'Ctrl+0',
  'view.toggleWordWrap': 'Alt+Z',
};

describe('AC-75①⑥ 默认键表：6 项新值逐项相等、其余 12 项逐项不变', () => {
  it('6 项新默认键的 `defaultKey` / `normalizedDefault` / `DEFAULT_BINDINGS` 三处一致', () => {
    for (const [id, expected] of Object.entries(NEW_DEFAULTS)) {
      const def = getShortcutDef(id);
      expect(def, `${id} 条目必须存在`).not.toBeNull();
      expect(def?.defaultKey, `${id} 的 defaultKey`).toBe(expected);
      expect(def?.meta.normalizedDefault, `${id} 的 normalizedDefault`).toBe(expected);
      expect(DEFAULT_BINDINGS[id], `${id} 的生效默认键`).toBe(expected);
    }
    // 覆盖集恰为 6 项（不能多也不能少）
    expect(Object.keys(NEW_DEFAULTS)).toHaveLength(6);
  });

  it('这 6 项的新值与旧值不同（防止"改了等于没改"）', () => {
    const oldValues: Record<string, string> = {
      'pane.new': 'Ctrl+Alt+N',
      'file.open': 'Ctrl+Alt+O',
      'pane.next': 'Ctrl+Alt+Right',
      'pane.prev': 'Ctrl+Alt+Left',
      'view.toolbox': 'Ctrl+Alt+K',
      'view.diff': 'Ctrl+Alt+D',
    };
    for (const [id, oldValue] of Object.entries(oldValues)) {
      expect(DEFAULT_BINDINGS[id], `${id} 仍是旧值`).not.toBe(oldValue);
      expect(normalizeShortcut(oldValue), `${id} 的旧键位不得再被任何条目占用`).not.toBe(
        DEFAULT_BINDINGS[id],
      );
    }
    // AC-75④：默认键列中 **0 项**以 `Ctrl+Alt+` 开头
    const withCtrlAlt = SHORTCUT_IDS.filter((id) =>
      (DEFAULT_BINDINGS[id] ?? '').startsWith('Ctrl+Alt+'),
    );
    expect(withCtrlAlt, `仍有默认键占用 Ctrl+Alt+*：${withCtrlAlt.join('、')}`).toEqual([]);
  });

  it('其余 12 项默认键逐项不变（AC-75⑥）', () => {
    expect(Object.keys(UNCHANGED_DEFAULTS)).toHaveLength(12);
    for (const [id, expected] of Object.entries(UNCHANGED_DEFAULTS)) {
      expect(DEFAULT_BINDINGS[id], `${id} 的默认键被误改`).toBe(expected);
      expect(getShortcutDef(id)?.defaultKey, `${id} 的 defaultKey 被误改`).toBe(expected);
    }
    // 18 = 6 + 12，条目集一字不动
    expect(Object.keys(NEW_DEFAULTS).length + Object.keys(UNCHANGED_DEFAULTS).length).toBe(18);
    expect(SHORTCUT_IDS).toHaveLength(18);
  });

  it('`Ctrl+PageDown` / `Ctrl+PageUp` 能从 `KeyboardEvent` 还原成同一归一形态（不需要补归一化）', () => {
    expect(normalizeShortcut('Ctrl+PageDown')).toBe('Ctrl+PageDown');
    expect(normalizeShortcut('Ctrl+PageUp')).toBe('Ctrl+PageUp');
    for (const [key, expected] of [
      ['PageDown', 'Ctrl+PageDown'],
      ['PageUp', 'Ctrl+PageUp'],
    ] as const) {
      expect(
        keyboardEventToShortcut({
          key,
          ctrlKey: true,
          altKey: false,
          shiftKey: false,
          metaKey: false,
        } as KeyboardEvent),
        `${key} 的事件侧归一化`,
      ).toBe(expected);
    }
    // 每项"按下→还原"往返一致（含 6 项新键）
    for (const id of SHORTCUT_IDS) {
      const binding = useShortcutStore.getState().getBinding(id);
      expect(
        keyboardEventToShortcut(new KeyboardEvent('keydown', keyboardInitFor(binding))),
        `${id} 的 ${binding} 往返不一致`,
      ).toBe(binding);
    }
  });

  it('`toOverrides` 对"从未自定义"的用户返回空表 ⇒ 新默认键自动生效（AC-75⑤）', async () => {
    const { toOverrides, withOverrides } = await import('../../../config/shortcuts');
    // 用户从未改过键：生效值就是默认值 ⇒ overrides 为空（不需要任何"重置快捷键"手工步骤）
    expect(toOverrides({ ...DEFAULT_BINDINGS })).toEqual({});
    // 反向：用户改过的项必须保住（AC-75⑦）
    const overrides = toOverrides({ ...DEFAULT_BINDINGS, 'view.toolbox': 'Ctrl+Shift+K' });
    expect(overrides).toEqual({ 'view.toolbox': 'Ctrl+Shift+K' });
    expect(withOverrides(overrides)['view.toolbox']).toBe('Ctrl+Shift+K');
    // 未被覆盖的项仍取新默认值
    expect(withOverrides(overrides)['pane.new']).toBe('Ctrl+N');
  });
});

describe('AC-75② `view.toolbox` / `view.diff` 真的被接管并投递面板请求（闭环 BL-51）', () => {
  it('`Ctrl+K` ⇒ 命中即接管，并向 store 投递 `toolbox` 请求（目标 = 当前激活标签）', () => {
    const tabId = useScratchStore.getState().createTab({ title: '目标标签' });
    const { prevented, reachedTarget, binding } = pressBinding('view.toolbox', outsideTarget);

    expect(binding, '`view.toolbox` 默认键 = `Ctrl+K`').toBe('Ctrl+K');
    expect(prevented, '`Ctrl+K` 必须被接管（AC-75②：配了没反应即失败）').toBe(true);
    expect(reachedTarget).toBe(false);
    const request = useScratchUiStore.getState().panelRequest;
    expect(request, '未投递面板请求 ⇒ 按键没有接管方').not.toBeNull();
    expect(request?.kind).toBe('toolbox');
    expect(request?.tabId).toBe(tabId);
    // 只投递请求，**不**在这里开弹框（弹框开关仍是组件的局部 state）
    expect(useScratchStore.getState().tabs).toHaveLength(1);
  });

  it('`Ctrl+Shift+D` ⇒ 接管并投递 `diff` 请求，`nonce` 单调递增（连按两次各触发一次）', () => {
    useScratchStore.getState().createTab({ title: '目标标签' });
    const { prevented, binding } = pressBinding('view.diff', outsideTarget);
    // `Ctrl+D` 已归还 CM6（`selectNextOccurrence`）⇒ `view.diff` 的默认键是 `Ctrl+Shift+D`
    expect(binding, '`view.diff` 默认键 = `Ctrl+Shift+D`').toBe('Ctrl+Shift+D');
    expect(prevented, '`Ctrl+Shift+D` 必须被接管（AC-75②：配了没反应即失败）').toBe(true);
    const first = useScratchUiStore.getState().panelRequest;
    expect(first?.kind).toBe('diff');
    expect(first?.nonce).toBeGreaterThan(0);

    pressBinding('view.diff', outsideTarget);
    const second = useScratchUiStore.getState().panelRequest;
    expect(second, '连按两次被"幂等"吞掉了第二次').not.toBeNull();
    expect(second?.nonce).toBeGreaterThan(first?.nonce ?? 0);
  });

  it('工作区为空时按 `Ctrl+K` ⇒ **建一个空标签**并把请求指向它（"永远有可观测结果"）', () => {
    expect(useScratchStore.getState().tabs).toHaveLength(0);
    const { prevented } = pressBinding('view.toolbox', outsideTarget);

    expect(prevented).toBe(true);
    const tabs = useScratchStore.getState().tabs;
    expect(tabs, '未挂载时不得静默丢弃请求').toHaveLength(1);
    expect(useScratchUiStore.getState().panelRequest?.tabId).toBe(tabs[0].id);
  });

  it('[负向对照] 未登录时按 `Ctrl+K` / `Ctrl+Shift+D` ⇒ 不接管、不投递任何请求（AC-63② 不例外）', () => {
    useAuthStore.setState({ isLoggedIn: false });
    for (const id of ['view.toolbox', 'view.diff'] as const) {
      const { prevented, reachedTarget } = pressBinding(id, outsideTarget);
      expect(prevented, `${id} 在未登录时不得接管`).toBe(false);
      expect(reachedTarget).toBe(true);
    }
    expect(useScratchUiStore.getState().panelRequest).toBeNull();
    expect(useScratchStore.getState().tabs).toHaveLength(0);
  });

  it('`consumePanelRequest` 幂等：`nonce` 不匹配返回 false 且不清位', () => {
    useScratchStore.getState().createTab({ title: '标签' });
    pressBinding('view.toolbox', outsideTarget);
    const request = useScratchUiStore.getState().panelRequest;
    expect(request).not.toBeNull();
    const nonce = request?.nonce ?? 0;

    expect(consumePanelRequest(nonce - 1), 'nonce 不匹配不得消费').toBe(false);
    expect(useScratchUiStore.getState().panelRequest).not.toBeNull();
    expect(consumePanelRequest(nonce), '首次消费应成功').toBe(true);
    expect(useScratchUiStore.getState().panelRequest).toBeNull();
    expect(consumePanelRequest(nonce), '重复消费必须返回 false（防重复渲染重复执行）').toBe(false);
  });
});

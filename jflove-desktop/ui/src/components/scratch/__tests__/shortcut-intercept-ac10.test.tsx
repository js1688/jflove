/**
 * AC-10 负向对照（桌面端快捷键被渲染层接管 / 不让宿主默认动作执行）
 *
 * ## 迁移说明（v1.6.0 → v1.6.1）
 *
 * v1.6.0 的宿主是挂在 `RouterProvider` 之外的 `<ScratchPaneLayer />`（浮动窗格层）；
 * 本文件原先 `render(<ScratchPaneLayer />)` 后往 `[data-scratch-pane]` 里打事件。
 * v1.6.1 **删除**该组件，把接线原样搬到模块级 `utils/scratch/shortcut-runtime.ts`
 * （设计 §4.7「承办方划分」），因此本文件改为：
 * 只 import 运行时模块（模块顶层 `:189` 自动装机一次），往 `[data-editor-panel]` 契约锚点里打事件。
 *
 * ## 判据出处
 *
 * - 需求原文 `文档记录/需求文档/v1.6.0.md:616`（§9.1 AC-10）：「把一个会被页面宿主默认动作占用的
 *   组合键（如打印、打开类）配给某编辑器功能 → 在窗格内按下时**触发编辑器行为**，
 *   且**不弹出**宿主默认对话框 `[负向对照]`」（v1.6.1 沿用；登录门与 `Ctrl+W` 的新判据见
 *   `文档记录/需求文档/v1.6.1.md:468`（AC-63 ②）与 `:475`（AC-70））。
 * - 设计 v1.6.1 `文档记录/设计文档/v1.6.1.md:581`（`pane.tile`/`pane.cascade` 作废）、
 *   `:583-591`（承办方划分：window 级 7 项由捕获阶段接管、面板作用域 3 项加 `closest('[data-editor-panel]')`、
 *   其余 9 项归 CM6 keymap + 面板内判定）、`:593-597`（`Ctrl+W` 接管）、`:443`（DOM 契约
 *   `data-editor-panel="<tabId>"`）；`§15.3.5 / §15.3.6`（**DS-45**：`view.toolbox` / `view.diff`
 *   并入接管面，闭环 BL-51）。
 * - 实现：`utils/scratch/shortcut-runtime.ts:63-74`（`RUNTIME_SHORTCUTS` 9 项）、`:77`
 *   （`PANEL_SCOPE_SHORTCUTS`）、`:166-192`（三道前置 → 命中 → `preventDefault + stopPropagation`）。
 *
 * ## 逐条说明「旧判据为什么作废 / 如何等价改写」（对应验收判据 4）
 *
 * 1. 旧 `APP_SCOPE_IDS` 9 项里的 `pane.tile` / `pane.cascade`：设计 `:581` 已作废（"配置界面**不得**出现"），
 *    键位本身不复存在 ⇒ **确实无等价判据**，代之以「18 项、作废键位不再登记」的自检。
 * 2. 旧 `20 项默认键位往返` ⇒ 等价改写为 **18 项**（需求 §3.2：条目集 20 → 18）。
 * 3. 旧 M-9 `it.fails`「窗格聚焦类 12 项也应被渲染层接管」：v1.6.1 把承办方划给 **CM6 keymap**
 *    （设计 `:587-588`），旧期望与设计**相反** ⇒ 改写为正向边界判据「这 11 项**不被** window 运行时
 *    拦下，事件必须**完整到达**面板内目标（CM6 才有机会处理）」。
 * 4. 旧 M-9 `it.fails`「工具箱 12 项应有已登记键位且被接管」：设计 `:591` 把 `view.toolbox` / `view.diff`
 *    无接管方登记为 BACKLOG **BL-51**，并声明"本版不扩大改动面" ⇒ 改写为**真实断言**
 *    「工具箱 12 项的键位登记现状 + 容器入口不得并入 window 接管面」（探针沿用旧文件的 `SHORTCUT_DEFS` 载体匹配）。
 * 5. 旧基线「窗口作用域 9 项逐项被接管」：接管面变为 **7 项**且分作用域 ⇒ 改写为「按各自作用域选目标
 *    逐项被拦下 + 面板作用域项在面板外**必须放行**」。`pane.next` / `pane.prev` 的 N-1 死键已在
 *    Phase 5 回修闭环（`DEFAULT_BINDINGS` 改取归一化默认值），原 `it.fails` **转正**为通过的 `it`。
 *
 * ## ⚠ 2026-10-16 就地更正：`view.toolbox` / `view.diff` 由「不被接管」翻转为「必须被接管」
 *
 * **为什么改写（不是放宽，是纠错）**：
 *
 * - **设计已判定**：`文档记录/设计文档/v1.6.1.md` §15.3.5 / §15.3.6（**DS-45**）明确把
 *   `view.toolbox`（`Ctrl+K`）与 `view.diff`（`Ctrl+Shift+D`）**纳入** `RUNTIME_SHORTCUTS`
 *   —— 工具箱弹框的开关原本是 `ScratchToolbar.tsx` 的局部 state，模块级常驻的运行时拿不到组件
 *   state ⇒ 旧状态 = **没有任何接管方**（登记为 BACKLOG **BL-51**）。
 * - **需求已判定**：`文档记录/需求文档/v1.6.1.md:548`（**AC-75②**）写死「**逐项真机可用**（每项给
 *   可观测结果）… 按 `Ctrl+K` → **工具箱弹框出现**… **6 项中任意一项"配了但按了没反应"即失败**」
 *   ⇒ 「配了没反应」**就是**判定的缺陷，旧断言恰好把它**锁成了期望行为**。
 * - 因此本文件原有的两条「**不被**接管」（旧 `:333` / `:366`）**必须**改写为正向断言；
 *   同时**保留负向对照**「**未登录时不得接管**」—— 防止修"没反应"时把准入门（AC-63 ②）
 *   一起拆掉，变成"无条件抢键"。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_BINDINGS,
  SHORTCUT_DEFS,
  SHORTCUT_IDS,
  keyboardEventToShortcut,
  normalizeShortcut,
  parseShortcut,
} from '../../../config/shortcuts';
import type { ShortcutId } from '../../../config/shortcuts';
import { scratchService } from '../../../services/scratch-service';
import { useAuthStore } from '../../../stores/auth-store';
import { registerScratchContentAccessor, useScratchStore } from '../../../stores/scratch-store';
import { useShortcutStore } from '../../../stores/shortcut-store';
import { setAuthSnapshot } from '../../../utils/session';
import { TOOLBOX_ITEMS } from '../../../utils/scratch/toolbox';

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

/** 运行时接管面（`shortcut-runtime.ts:63-74` 的 `RUNTIME_SHORTCUTS`，逐项对齐） */
const RUNTIME_IDS: readonly ShortcutId[] = [
  'pane.new',
  'pane.close',
  'pane.next',
  'pane.prev',
  'file.open',
  'file.save',
  'file.saveAs',
  // ★ 2026-10-16 更正面：这两项**必须**被接管（AC-75②/闭环 BL-51）。
  //   旧断言锁死了"它们不被接管"= 已判定的缺陷（"配了但按了没反应"），故本轮改写为正向断言。
  'view.toolbox',
  'view.diff',
];

/** 面板作用域（`shortcut-runtime.ts:66` 的 `PANEL_SCOPE_SHORTCUTS`）—— 目标必须落在面板内 */
const PANEL_SCOPE_IDS: readonly ShortcutId[] = ['pane.close', 'file.save', 'file.saveAs'];

/** 非接管面（9 项）：承办方是 CM6 keymap + 面板内判定（设计 `:587-588`） */
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

/** 工具箱项的「键位载体」探针（沿用旧文件的匹配口径：同名或 `*.同名`） */
function carriersOfToolboxItem(itemId: string) {
  return SHORTCUT_DEFS.filter((def) => def.id === itemId || def.id.endsWith(`.${itemId}`));
}

let outsideTarget: HTMLElement;

beforeEach(() => {
  // jsdom 里 `document.hasFocus()` 初始为 false（运行时会直接让路）⇒ 先把焦点给 body 造出「窗口激活」
  document.body.tabIndex = -1;
  document.body.focus();
  expect(document.hasFocus(), 'jsdom 焦点前提：document.hasFocus() 必须为 true').toBe(true);

  // 面板外目标：用于验证「面板作用域项落在面板外必须放行」（`shortcut-runtime.ts:157-158`）
  outsideTarget = document.createElement('div');
  outsideTarget.tabIndex = -1;
  document.body.appendChild(outsideTarget);

  // 准入：默认已登录且会话未过期（`editor-gate.ts:47`）；AC-63 ② 的用例在另一文件里翻成未登录
  setAuthSnapshot({ expiresAt: null });
  useAuthStore.setState({ isLoggedIn: true });

  useScratchStore.setState({ tabs: [], activeTabId: null, lastError: null, pendingCloseId: null });
  useShortcutStore.setState({
    overrides: {},
    bindings: { ...DEFAULT_BINDINGS },
    conflicts: [],
    lastError: null,
  });

  // 服务替身：本文件只关心「是否接管」，不关心落盘细节；
  // `file.saveAs` 需要一次**确定**的写入 ⇒ 默认替身给一个固定目标
  vi.spyOn(scratchService, 'pickFiles').mockResolvedValue([]);
  vi.spyOn(scratchService, 'pickSaveTarget').mockResolvedValue('D:/tmp/ac10-picked.txt');
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
  outsideTarget.remove();
  document.querySelectorAll('[data-editor-panel]').forEach((node) => node.remove());
  accessorReleases.splice(0).forEach((release) => release());
  vi.restoreAllMocks();
});

describe('AC-10 事件构造自检 / 默认键位往返（条目集 20 → 18）', () => {
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

describe('AC-10 [负向对照] 宿主占用组合键被运行时拦下', () => {
  it('window 作用域：把「新建标签页」改配到宿主占用的 `Ctrl+P` ⇒ 焦点在面板外也被 preventDefault + stopPropagation，且动作执行', () => {
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

  it('panel 作用域：把「保存」改配到 `Ctrl+P` ⇒ 面板内被拦下并真的写盘，面板外放行（不误拦宿主默认动作）', async () => {
    useShortcutStore.getState().setBinding('file.save', 'Ctrl+P');
    const tabId = useScratchStore.getState().createTab({
      title: 'ac10-save.txt',
      path: 'D:/tmp/ac10-save.txt',
    });
    registerContentAccessor(tabId, 'AC-10 保存正文');
    const panel = panelTargetFor(tabId);

    const inside = pressBinding('file.save', panel);
    expect(inside.prevented, '面板内按下 `Ctrl+P` 必须被拦下（不能让宿主弹打印）').toBe(true);
    expect(inside.reachedTarget).toBe(false);
    await vi.waitFor(() => {
      expect(scratchService.writeText, '接管后必须真的落盘（AC-10 的「触发编辑器行为」一侧）').toHaveBeenCalledTimes(1);
    });
    expect(scratchService.writeText).toHaveBeenCalledWith(
      expect.objectContaining({ path: 'D:/tmp/ac10-save.txt', text: 'AC-10 保存正文' }),
    );

    const outside = pressBinding('file.save', outsideTarget);
    expect(outside.prevented, '面板作用域项在面板外必须放行（否则会抢走宿主的打印）').toBe(false);
    expect(outside.reachedTarget).toBe(true);
    expect(scratchService.writeText, '面板外的按键不得产生任何写入').toHaveBeenCalledTimes(1);
  });
});

describe('AC-10 基线：接管面 7 项按各自作用域逐项被拦下', () => {
  // `pane.next` / `pane.prev` 的 N-1 死键（v1.6.0 遗留）**已修**：`DEFAULT_BINDINGS` 改取归一化形态
  // （`config/shortcuts.ts:259-271`），这两项默认键从此真实生效。
  // 它们除「被接管」外还要额外断言生效值已归一化，故从基线矩阵中剔除，
  // 本 describe 内单独登记为**普通 `it`**（见下方第二个 `for`，非 `it.fails`）。
  const WORKING_RUNTIME_IDS: readonly ShortcutId[] = RUNTIME_IDS.filter(
    (id) => id !== 'pane.next' && id !== 'pane.prev',
  );

  for (const id of WORKING_RUNTIME_IDS) {
    it(`${id} 在作用域内被 preventDefault + stopPropagation；面板作用域项在面板外必须放行`, async () => {
      const tabId = useScratchStore.getState().createTab({
        title: 'ac10-base.txt',
        path: 'D:/tmp/ac10-base.txt',
      });
      registerContentAccessor(tabId, 'AC-10 基线正文');
      const panelScoped = PANEL_SCOPE_IDS.includes(id);
      const inside = panelScoped ? panelTargetFor(tabId) : outsideTarget;

      const { prevented, reachedTarget, binding } = pressBinding(id, inside);
      expect(prevented, `${id}（${binding}）未被运行时接管 —— 宿主默认动作会漏出去`).toBe(true);
      expect(reachedTarget, `${id}（${binding}）应停止继续传播`).toBe(false);

      if (id === 'file.save' || id === 'file.saveAs') {
        await vi.waitFor(() => {
          expect(scratchService.writeText, `${id} 接管后应真的落盘`).toHaveBeenCalledTimes(1);
        });
      } else {
        // 其余项的异步动作是 mocking 后立即 resolve 的（`pickFiles` / `closeTab`），让微任务跑完即可
        await Promise.resolve();
      }

      if (panelScoped) {
        const outside = pressBinding(id, outsideTarget);
        expect(outside.prevented, `${id} 在面板外不得接管`).toBe(false);
        expect(outside.reachedTarget).toBe(true);
        if (id === 'file.save' || id === 'file.saveAs') {
          expect(scratchService.writeText, '面板外的按键不得产生任何写入').toHaveBeenCalledTimes(1);
        }
      }
    });
  }

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

describe('AC-10 缺口（M-9）的等价改写：非接管面 9 项的事件必须完整到达面板内目标', () => {
  // 旧期望（"这 11 项也应由渲染层 preventDefault"）与 v1.6.1 设计相反（设计 §4.7:587-588 把承办方划给
  // CM6 keymap + 面板内判定）⇒ 不能保留 `it.fails`（那会把"设计"当"缺陷"）；这里断言**更强的边界**：
  // 运行时**不得**接管，事件必须一路到达面板内目标，否则 CM6 的自有键位会被抢掉。
  //
  // ⚠ 2026-10-16 更正面：集合由 **11 项 → 9 项** —— `view.toolbox` / `view.diff` 已按设计 **DS-45**
  // （§15.3.5 / §15.3.6）与需求 **AC-75②** 并入接管面，其正向判据见下一个 describe；
  // 本组仍守住"**其余 9 项不得被吞**"这条边界（`Ctrl+D` 已在 `config/shortcuts.ts` 归还给 CM6）。
  for (const id of NON_RUNTIME_IDS) {
    it(`${id} 在面板内按下时不被 window 运行时接管，且事件到达目标（承办方 = CM6 keymap）`, () => {
      const panel = panelTargetFor('ac10-non-runtime');
      const { prevented, reachedTarget, binding } = pressBinding(id, panel);
      expect(
        prevented,
        `${id}（${binding}）被运行时接管了 —— 接管面被扩大，会抢走 CM6 的自有键位`,
      ).toBe(false);
      expect(reachedTarget, `${id} 的事件必须到达面板内目标，CM6 keymap 才有机会处理`).toBe(true);
    });
  }
});

describe('AC-10 缺口（M-9）的等价改写：工具箱 12 项的键位登记现状', () => {
  it('TOOLBOX_ITEMS 恰好 12 项（覆盖范围自检）', () => {
    expect(TOOLBOX_ITEMS).toHaveLength(12);
  });

  it('工具箱项里只有 `diff` 有键位载体（容器入口 `view.diff`），且该入口**已并入** window 接管面（设计 §15.3.6 / DS-45，闭环 BL-51）', () => {
    const withCarrier = TOOLBOX_ITEMS.filter((item) => carriersOfToolboxItem(item.id).length > 0).map(
      (item) => item.id,
    );
    // 实测：12 项里唯一被 `SHORTCUT_DEFS` 覆盖的是「两段文本对比」（`view.diff`）。
    expect(withCarrier).toEqual(['diff']);

    for (const itemId of withCarrier) {
      for (const def of carriersOfToolboxItem(itemId)) {
        // ⚠ 2026-10-16 更正（原为 `.not.toContain`）：旧断言"不得并入接管面"锁死了 BL-51 这个
        // **已判定的缺陷** —— 需求 AC-75② 明列「按 `Ctrl+K` → 工具箱弹框出现」「任意一项
        // 配了但按了没反应即失败」，设计 DS-45 已把两项交回运行时接管 ⇒ 正向断言"必须并入"。
        expect(
          RUNTIME_IDS as readonly string[],
          `工具箱载体 ${def.id} 必须并入 window 接管面（AC-75② / DS-45，闭环 BL-51）`,
        ).toContain(def.id);
      }
    }
  });
});

/**
 * `view.toolbox` / `view.diff` 的接管面正向判据（2026-10-16 就地改写）。
 *
 * **旧断言**（本文件改写前 `:333` 两条 / `:366`）：「两项**不被**运行时接管」。
 * **为什么必须改写**：那两条把 **BL-51「配了没反应」** 这个**已判定的缺陷**锁成了期望行为 ——
 * 设计 §15.3.5 / §15.3.6（**DS-45**）已把两项纳入 `RUNTIME_SHORTCUTS`，
 * 需求 §9.2 `:548`（**AC-75②**）写死「按 `Ctrl+K` → 工具箱弹框出现」「任意一项配了但按了没反应
 * 即失败」。因此改为**正向**断言"两项**必须**被接管"，**同时保留负向对照**：
 * **未登录（gate 为假）时不得接管** —— 防止修"没反应"时把 AC-63 ② 的准入门一起拆掉
 * （那会变成"无条件抢键"：未登录也 `preventDefault`，吞掉用户的按键）。
 */
describe('AC-75② / DS-45 正向：`view.toolbox` / `view.diff` 必须被 window 运行时接管（闭环 BL-51）', () => {
  for (const id of ['view.toolbox', 'view.diff'] as const) {
    it(`${id} 在面板内按下时必须被接管（preventDefault + stopPropagation，事件不再流向 CM6）`, () => {
      const panel = panelTargetFor('ac10-takeover');
      const { prevented, reachedTarget, binding } = pressBinding(id, panel);
      expect(prevented, `${id}（${binding}）未被接管 —— 即 BL-51「配了没反应」，AC-75② 失败`).toBe(
        true,
      );
      expect(reachedTarget, `${id}（${binding}）接管后应 stopPropagation`).toBe(false);
    });

    it(`${id}[负向对照] 未登录时**不得**接管：不 preventDefault、事件完整到达目标`, () => {
      // 门 = `canUseEditor()`（`shortcut-runtime.ts:167-171`，AC-63 ②"不响应且不报错"）。
      // 两项都是 `window` 作用域（无面板内判定兜底）⇒ 只有准入门能把它们挡住，
      // 因此这条负向对照是"无条件抢键"回归的唯一守门人。
      useAuthStore.setState({ isLoggedIn: false });
      const panel = panelTargetFor('ac10-takeover-gate');
      const { prevented, reachedTarget, binding } = pressBinding(id, panel);
      expect(prevented, `${id}（${binding}）在未登录时被接管了 —— 抢了未登录用户的按键`).toBe(
        false,
      );
      expect(reachedTarget, `${id}（${binding}）未登录时必须放行，事件应到达目标`).toBe(true);
    });
  }
});


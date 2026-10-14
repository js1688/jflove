/**
 * AC-10 负向对照（桌面端快捷键被渲染层接管 / 不让宿主默认动作执行）
 *
 * ## 判据出处
 *
 * - 需求原文（`文档记录/需求文档/v1.6.0.md:616`，§9.1）：
 *   「把一个会被页面宿主默认动作占用的组合键（如打印、打开类）配给某编辑器功能 → 在窗格内按下时
 *   **触发编辑器行为**，且**不弹出**宿主默认对话框 `[负向对照]`」。
 * - 设计（`设计文档/v1.6.0.md` §4.2.2 / §4.2.3）：应用级监听 = `window` **捕获阶段** `keydown`，
 *   命中即 `preventDefault() + stopPropagation()`；`document.body.dataset.jfShortcutCapture` 存在时让路；
 *   **只有窗口作用域的 7 项**由渲染层接管，其余为「窗格聚焦类」（CM6 keymap 侧）。
 * - 代码审查报告 v1.6.0 **M-9 [中等]**：「仅 7 项 app-scope 快捷键会 `preventDefault`，
 *   另 12 项既无行为也不拦宿主」⇒ 缺口用 `it.fails` 逐项登记（`it.fails` 必须失败才算通过）。
 *
 * ## 本文件的 DOM 断言口径（把 r8 的静态断言升级为真 DOM）
 *
 * 渲染真实 `ScratchPaneLayer`，用真实 `KeyboardEvent` 打到 `[data-scratch-pane]` 内的目标上，
 * 读 `event.defaultPrevented` 与「冒泡阶段监听器是否还被调用」两个可观测事实。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { ScratchPaneLayer } from '../ScratchPaneLayer';
import {
  DEFAULT_BINDINGS,
  SHORTCUT_DEFS,
  SHORTCUT_IDS,
  keyboardEventToShortcut,
  normalizeShortcut,
  parseShortcut,
} from '../../../config/shortcuts';
import type { ShortcutId } from '../../../config/shortcuts';
import { useScratchStore } from '../../../stores/scratch-store';
import { useShortcutStore } from '../../../stores/shortcut-store';
import { TOOLBOX_ITEMS } from '../../../utils/scratch/toolbox';

// 窗格渲染由另一位施工面覆盖（`scratch-pane-render.test.tsx`）；这里只关心捕获阶段的分派，
// 因此把窗格外壳替成空组件，避免 `pane.new` 用例顺带把 CM6 宿主拉起来。
vi.mock('../ScratchPane', () => ({ ScratchPane: () => null }));

/** 渲染层**接管**的 7 项（与 `ScratchPaneLayer.tsx:52-60` 的 `APP_SCOPE_SHORTCUTS` 一致） */
const APP_SCOPE_IDS: readonly ShortcutId[] = [
  'pane.new',
  'pane.close',
  'pane.next',
  'pane.prev',
  'pane.tile',
  'pane.cascade',
  'file.open',
];

/** 非应用级（窗格聚焦类）的 13 项 = 20 项默认键位 − 7 项 */
const PANE_SCOPE_IDS: readonly ShortcutId[] = SHORTCUT_IDS.filter(
  (id) => !APP_SCOPE_IDS.includes(id),
);

/** 归一键名 → `KeyboardEvent.key`（`keyboardEventToShortcut` 只认 `event.key`，见 `shortcuts.ts:446`） */
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

/** 按下某功能当前生效的键位，返回两个可观测事实 */
function pressBinding(
  id: ShortcutId,
  target: HTMLElement,
): { event: KeyboardEvent; prevented: boolean; reachedTarget: boolean } {
  const binding = useShortcutStore.getState().getBinding(id);
  const event = new KeyboardEvent('keydown', keyboardInitFor(binding));
  let reachedTarget = false;
  const listener = () => {
    reachedTarget = true;
  };
  // 目标节点上的监听器：若渲染层在捕获阶段 stopPropagation，它就不会被调用
  target.addEventListener('keydown', listener);
  target.dispatchEvent(event);
  target.removeEventListener('keydown', listener);
  return { event, prevented: event.defaultPrevented, reachedTarget };
}

let paneTarget: HTMLElement;

beforeEach(() => {
  // jsdom 里 `document.hasFocus()` 初始为 false（层会直接让路）⇒ 先把焦点给 body 造出「窗口激活」
  document.body.tabIndex = -1;
  document.body.focus();
  expect(document.hasFocus(), 'jsdom 焦点前提：document.hasFocus() 必须为 true').toBe(true);

  // 窗格内目标：`pane.close` 额外要求 `closest('[data-scratch-pane]')`
  paneTarget = document.createElement('div');
  paneTarget.setAttribute('data-scratch-pane', 'true');
  paneTarget.tabIndex = -1;
  document.body.appendChild(paneTarget);

  useScratchStore.setState({ panes: [], activePaneId: null, zCounter: 1, lastError: null });
  useShortcutStore.setState({
    overrides: {},
    bindings: { ...DEFAULT_BINDINGS },
    conflicts: [],
    lastError: null,
  });

  render(<ScratchPaneLayer />);
});

afterEach(() => {
  // F-3 的让路标记是 body 级全局状态：**必须清掉**，否则会污染其它用例（含并行文件内的层）
  delete document.body.dataset.jfShortcutCapture;
  cleanup();
  paneTarget.remove();
});

describe('AC-10 事件构造自检 / 默认键位往返', () => {
  it('20 项默认键位都能由 KeyboardEvent 还原成同一归一化形态（保证下面的「按下」真的按到了该键位）', () => {
    for (const id of SHORTCUT_IDS) {
      const binding = useShortcutStore.getState().getBinding(id);
      expect(binding, `${id} 应有默认键位`).not.toBe('');
      const event = new KeyboardEvent('keydown', keyboardInitFor(binding));
      expect(keyboardEventToShortcut(event), `${id} 的 ${binding} 往返不一致`).toBe(
        normalizeShortcut(binding),
      );
    }
  });
});

describe('AC-10 [负向对照] 宿主占用组合键被渲染层拦下', () => {
  it('把「新建窗格」改配到宿主占用的 `Ctrl+P`：窗格内按下被 preventDefault + stopPropagation，且动作执行', () => {
    // `Ctrl+P` 是宿主（打印）默认动作占用的组合，正合 AC-10 的「如打印、打开类」样本
    useShortcutStore.getState().setBinding('pane.new', 'Ctrl+P');
    expect(useShortcutStore.getState().getBinding('pane.new')).toBe('Ctrl+P');

    const { event, prevented, reachedTarget } = pressBinding('pane.new', paneTarget);
    expect(event.defaultPrevented, '宿主默认动作必须被拦下').toBe(true);
    expect(prevented).toBe(true);
    expect(reachedTarget, '接管后应 stopPropagation，不再流向窗格内部').toBe(false);
    // 「触发编辑器行为」：`pane.new` 的真实动作就是新建窗格
    expect(useScratchStore.getState().panes).toHaveLength(1);
    expect(useScratchStore.getState().lastError).toBeNull();
  });
});

describe('AC-10 基线：窗口作用域的 7 项逐项被接管', () => {
  // 新发现 N-1（本文件实测，审查报告未记）：`pane.next` / `pane.prev` 的默认值在
  // `DEFAULT_BINDINGS` 里是**原始写法** `Ctrl+Alt+ArrowRight`（`shortcuts.ts:272-275` 直接取
  // `def.defaultKey`），而事件侧 `keyboardEventToShortcut` 归一成 `Ctrl+Alt+Right`
  // ⇒ 渲染层的 `getBinding(id) === chord` 永不成立，这两个键位在默认状态下是**死的**。
  const WORKING_APP_SCOPE_IDS = APP_SCOPE_IDS.filter((id) => id !== 'pane.next' && id !== 'pane.prev');

  for (const id of WORKING_APP_SCOPE_IDS) {
    it(`${id} 在窗格内按下时被 preventDefault（不让宿主执行默认动作）`, () => {
      const binding = useShortcutStore.getState().getBinding(id);
      const { event, reachedTarget } = pressBinding(id, paneTarget);
      expect(event.defaultPrevented, `${id}（${binding}）未被接管`).toBe(true);
      expect(reachedTarget, `${id}（${binding}）应停止继续传播`).toBe(false);
    });
  }

  for (const id of ['pane.next', 'pane.prev'] as const) {
    it.fails(`N-1：${id} 在窗格内按下时应被接管（实测默认值未归一化 ⇒ 永不匹配）`, () => {
      const binding = useShortcutStore.getState().getBinding(id);
      // 生效值必须已是归一化形态，否则事件侧永远匹配不上
      expect(normalizeShortcut(binding), `${id} 生效值未归一化`).toBe(binding);
      const { event } = pressBinding(id, paneTarget);
      expect(event.defaultPrevented, `${id}（${binding}）未被接管`).toBe(true);
    });
  }
});

describe('AC-10 缺口（M-9）：窗格聚焦类的 13 项按下时期望同样被渲染层接管', () => {
  for (const id of PANE_SCOPE_IDS) {
    it.fails(
      `${id} 在窗格内按下时应被 preventDefault（实测：既不执行也不拦宿主 —— M-9）`,
      () => {
        const binding = useShortcutStore.getState().getBinding(id);
        const { event } = pressBinding(id, paneTarget);
        expect(event.defaultPrevented, `${id}（${binding}）未被渲染层接管`).toBe(true);
      },
    );
  }
});

describe('AC-10 缺口（M-9）：工具箱 12 项没有可被拦截的键位', () => {
  it('TOOLBOX_ITEMS 恰好 12 项（覆盖范围自检）', () => {
    expect(TOOLBOX_ITEMS).toHaveLength(12);
  });

  for (const item of TOOLBOX_ITEMS) {
    it.fails(
      `工具箱第 ${item.index} 项「${item.id}」应有已登记键位且按下被接管（实测：无任何键位）`,
      () => {
        // 期望口径：AC-10 要求「配给编辑器功能的组合键」在窗格内被拦。
        // 实测：`SHORTCUT_DEFS` 20 项里没有任何一项指向 12 个工具箱项
        // （唯一相关的是容器入口 `view.toolbox = Ctrl+Alt+K`，它同样不在 7 项接管名单里）。
        const itemId: string = item.id;
        const carriers = SHORTCUT_DEFS.filter(
          (def) => def.id === itemId || def.id.endsWith(`.${itemId}`),
        );
        expect(carriers, `TOOLBOX_ITEMS.${item.id} 未登记任何键位`).not.toHaveLength(0);

        const binding = useShortcutStore.getState().getBinding(carriers[0].id);
        const { event } = pressBinding(carriers[0].id, paneTarget);
        expect(event.defaultPrevented, `工具箱项 ${item.id}（${binding}）未被接管`).toBe(true);
      },
    );
  }
});

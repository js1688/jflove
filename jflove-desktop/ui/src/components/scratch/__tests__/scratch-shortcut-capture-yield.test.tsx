/**
 * F-3 让路机制（快捷键捕获层遇到「正在录制键位」时不得抢键）
 *
 * ## 判据出处
 *
 * - 设计（`设计文档/v1.6.0.md` §4.2.5 / F-3）：快捷键设置对话框**录制中**时，应用级捕获层必须让路，
 *   否则用户按下某个**已生效**的窗格键（如 `Ctrl+Alt+N`）会顺带触发一次窗格动作。
 * - 实现（`ScratchPaneLayer.tsx:285-289`）：
 *   `if (document.body.dataset.jfShortcutCapture) return;` ——写入方是
 *   `ShortcutsSettingsDialog.tsx:258`（`= '1'`）与 `:261`（cleanup 里 `delete`）。
 * - 需求原文（`需求文档/v1.6.0.md` AC-9 段落）：冲突检测与录制期间不得静默触发其它功能。
 *
 * ## 断言口径
 *
 * 本文件只做**行为断言**（dataset → 是否接管），并逐条把 body 级全局标记清干净，
 * 避免污染同进程内的其它用例（`document.body.dataset` 是跨测试共享的全局状态）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { ScratchPaneLayer } from '../ScratchPaneLayer';
import {
  DEFAULT_BINDINGS,
  keyboardEventToShortcut,
  parseShortcut,
} from '../../../config/shortcuts';
import type { ShortcutId } from '../../../config/shortcuts';
import { useScratchStore } from '../../../stores/scratch-store';
import { useShortcutStore } from '../../../stores/shortcut-store';

vi.mock('../ScratchPane', () => ({ ScratchPane: () => null }));

/** 渲染层接管的 7 项（`ScratchPaneLayer.tsx:52-60`） */
const APP_SCOPE_IDS: readonly ShortcutId[] = [
  'pane.new',
  'pane.close',
  'pane.next',
  'pane.prev',
  'pane.tile',
  'pane.cascade',
  'file.open',
];

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

/** 按下某功能当前生效的键位，观察「是否被渲染层接管」 */
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

let paneTarget: HTMLElement;

beforeEach(() => {
  document.body.tabIndex = -1;
  document.body.focus();
  expect(document.hasFocus(), 'jsdom 焦点前提：document.hasFocus() 必须为 true').toBe(true);

  paneTarget = document.createElement('div');
  paneTarget.setAttribute('data-scratch-pane', 'true');
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
  // F-3 的让路标记必须清掉：否则后续用例（含其它文件的层）会全部「让路」而静默失效
  delete document.body.dataset.jfShortcutCapture;
  cleanup();
  paneTarget.remove();
});

describe('F-3 录制期间让路', () => {
  it('录制中（`jfShortcutCapture="1"`）7 项应用级键位全部让路：不拦、不阻止传播、不执行动作', () => {
    document.body.dataset.jfShortcutCapture = '1';

    for (const id of APP_SCOPE_IDS) {
      const { prevented, reachedTarget, chord } = pressBinding(id, paneTarget);
      expect(prevented, `${id} 在录制期间被拦截了（${chord}）`).toBe(false);
      expect(reachedTarget, `${id} 在录制期间不应阻止事件继续传播`).toBe(true);
    }
    // 让路 = 连动作也不执行
    expect(useScratchStore.getState().panes).toHaveLength(0);
  });

  it('标记清零后同一次按键恢复接管（让路是「暂时」的，不是永久降级）', () => {
    document.body.dataset.jfShortcutCapture = '1';
    const during = pressBinding('pane.new', paneTarget);
    expect(during.prevented, '录制期间不接管').toBe(false);
    expect(useScratchStore.getState().panes).toHaveLength(0);

    // 对话框 cleanup（`ShortcutsSettingsDialog.tsx:261`）删除标记
    delete document.body.dataset.jfShortcutCapture;
    expect(document.body.dataset.jfShortcutCapture).toBeUndefined();

    const after = pressBinding('pane.new', paneTarget);
    expect(after.prevented, '结束录制后应恢复接管').toBe(true);
    expect(after.reachedTarget).toBe(false);
    expect(useScratchStore.getState().panes, '恢复接管后动作应执行').toHaveLength(1);
  });

  it('只有非空标记才让路：`jfShortcutCapture=""` 视为没有标记（数据集语义守门）', () => {
    // `dataset.x = ''` 会让 `if (document.body.dataset.jfShortcutCapture)` 取到空串（假值）
    document.body.dataset.jfShortcutCapture = '';
    const { prevented } = pressBinding('pane.new', paneTarget);
    expect(prevented, '空串标记不应让路').toBe(true);
  });

  it('让路只认 `jfShortcutCapture` 这一个键名：其它 dataset 键不影响接管', () => {
    document.body.dataset.jfShortcutCaptureOther = '1';
    const { prevented } = pressBinding('pane.new', paneTarget);
    expect(prevented, '键名不匹配时不应让路').toBe(true);
  });
});

describe('F-3 让路的另一半：窗口未激活 / 文档不可见时不接管', () => {
  it('`document.hasFocus()` 为 false 时不接管（窗口未激活，§4.2.2）', () => {
    const focusSpy = vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    const { prevented } = pressBinding('pane.new', paneTarget);
    expect(prevented, '窗口未激活时不应接管').toBe(false);
    expect(useScratchStore.getState().panes).toHaveLength(0);
    focusSpy.mockRestore();
  });

  it('`document.hidden` 为 true 时不接管（文档不可见，§4.2.2）', () => {
    const hiddenSpy = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    const { prevented } = pressBinding('pane.new', paneTarget);
    expect(prevented, '文档不可见时不应接管').toBe(false);
    expect(useScratchStore.getState().panes).toHaveLength(0);
    hiddenSpy.mockRestore();
  });
});

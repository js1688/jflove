/**
 * AC-12 块选择修饰键（`edit.blockSelectModifier`）—— 两侧都要断言
 *
 * 判据出处
 * - 需求 `文档记录/需求文档/v1.6.0.md:623`（AC-12 原文样本）：
 *   「把块选择修饰键改为 `Ctrl` 后：`Ctrl` 组合触发**块选择**，`Alt` **退化为普通选择**
 *    （不再是块选择）」—— 即改键前后**两侧**都要成立。
 * - 实现口径（`src/stores/shortcut-store.ts:97-105` 的 docstring）：
 *   「用户把块选择改成 `Ctrl` 后，`Alt` 自动退化为普通（流式）选择 —— 同一个 `eventFilter`
 *    只认一个修饰键，天然满足 AC-12」。
 *
 * 被测真实接口
 * - `resolveBlockSelectModifier(value)` / `selectBlockSelectModifier(state)`（已导出，纯派生）
 * - `useShortcutStore.getState().setBinding(...)`（改键的唯一入口，含修饰键白名单）
 * - 过滤器本体 `blockSelectFilter(modifier)` 位于 `src/components/scratch/ScratchEditorHost.tsx:231-235`
 *   且**未导出**，本文件按该处逐行镜像其单修饰键规则（一行三元），派生值仍取自真 store；
 *   「帧内几何是否真的变成矩形」需真机（审查报告 §8 / R-5：无头环境收不到注入键、且无布局测量）。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import {
  BLOCK_SELECT_MODIFIER_ID,
  DEFAULT_BINDINGS,
  DEFAULT_BLOCK_SELECT_MODIFIER,
  MODIFIER_KEYS,
  SHORTCUT_DEFS,
  requiresModifierOnly,
  toOverrides,
} from '../../config/shortcuts';
import {
  resolveBlockSelectModifier,
  selectBlockSelectModifier,
  useShortcutStore,
} from '../shortcut-store';

/** 回到出厂状态（同文件内用例互不干扰） */
function resetShortcutStore(): void {
  useShortcutStore.setState({
    overrides: {},
    bindings: { ...DEFAULT_BINDINGS },
    conflicts: [],
    lastError: null,
  });
}

/** 镜像 `ScratchEditorHost.tsx:231-235`：同一个 `eventFilter` 只认一个修饰键 */
function mirrorBlockSelectFilter(modifier: string): (event: MouseEvent) => boolean {
  return modifier === 'Ctrl'
    ? (event: MouseEvent): boolean => event.ctrlKey
    : (event: MouseEvent): boolean => event.altKey;
}

/** 造只按下一个修饰键的鼠标事件 */
function mouseWith(modifier: (typeof MODIFIER_KEYS)[number]): MouseEvent {
  return new MouseEvent('mousedown', {
    bubbles: true,
    cancelable: true,
    ctrlKey: modifier === 'Ctrl',
    altKey: modifier === 'Alt',
    shiftKey: modifier === 'Shift',
    metaKey: modifier === 'Meta',
  });
}

describe('AC-12 块选择修饰键两侧断言', () => {
  beforeEach(resetShortcutStore);

  it('默认值 = Alt（第 14 项，仅修饰键条目）', () => {
    expect(DEFAULT_BLOCK_SELECT_MODIFIER).toBe('Alt');
    expect(useShortcutStore.getState().getBinding(BLOCK_SELECT_MODIFIER_ID)).toBe('Alt');
    expect(selectBlockSelectModifier(useShortcutStore.getState())).toBe('Alt');
    expect(requiresModifierOnly(BLOCK_SELECT_MODIFIER_ID)).toBe(true);
    // 全表只有这一个「仅修饰键」条目（设计 §4.2.1 第 14 行）
    expect(SHORTCUT_DEFS.filter((def) => def.meta.modifierOnly).map((def) => def.id)).toEqual([
      BLOCK_SELECT_MODIFIER_ID,
    ]);
  });

  it('派生口径：只有 `Ctrl` 是另一个可选项，其余一律回 `Alt`', () => {
    expect(resolveBlockSelectModifier(undefined)).toBe('Alt');
    expect(resolveBlockSelectModifier('ctrl')).toBe('Ctrl'); // 别名/大小写走同一套归一化
    expect(resolveBlockSelectModifier('Control')).toBe('Ctrl');
    for (const value of ['Alt', 'Shift', 'Meta', '', '!!!', 'Ctrl+Alt']) {
      expect(resolveBlockSelectModifier(value), value).toBe('Alt');
    }
  });

  it('两侧断言①：默认 `Alt` 时只有 Alt 组合是块选择，Ctrl 组合是普通选择', () => {
    const modifier = selectBlockSelectModifier(useShortcutStore.getState());
    expect(modifier).toBe('Alt');
    const filter = mirrorBlockSelectFilter(modifier);

    expect(filter(mouseWith('Alt'))).toBe(true); // 块选择
    expect(filter(mouseWith('Ctrl'))).toBe(false); // 普通（流式）选择
    expect(filter(mouseWith('Shift'))).toBe(false);
    expect(filter(mouseWith('Meta'))).toBe(false);
    // 「只认一个修饰键」⇒ 四个修饰键里恰好一个被接受
    expect(MODIFIER_KEYS.filter((name) => filter(mouseWith(name)))).toEqual(['Alt']);
  });

  it('两侧断言②：改成 `Ctrl` 后 Ctrl 触发块选择，Alt 退化为普通选择', () => {
    useShortcutStore.getState().setBinding(BLOCK_SELECT_MODIFIER_ID, 'Ctrl');

    // 落内存：bindings 与 overrides 都要变（overrides 只写非默认项）
    expect(useShortcutStore.getState().getBinding(BLOCK_SELECT_MODIFIER_ID)).toBe('Ctrl');
    expect(useShortcutStore.getState().overrides[BLOCK_SELECT_MODIFIER_ID]).toBe('Ctrl');
    expect(toOverrides(useShortcutStore.getState().bindings)[BLOCK_SELECT_MODIFIER_ID]).toBe('Ctrl');

    const modifier = selectBlockSelectModifier(useShortcutStore.getState());
    expect(modifier).toBe('Ctrl');
    const filter = mirrorBlockSelectFilter(modifier);

    expect(filter(mouseWith('Ctrl'))).toBe(true); // 改键后：Ctrl = 块选择
    expect(filter(mouseWith('Alt'))).toBe(false); // 改键后：Alt = 普通选择（AC-12 的另一侧）
    expect(MODIFIER_KEYS.filter((name) => filter(mouseWith(name)))).toEqual(['Ctrl']);
  });

  it('改回 `Alt` 后两侧复位（可逆）', () => {
    useShortcutStore.getState().setBinding(BLOCK_SELECT_MODIFIER_ID, 'Ctrl');
    useShortcutStore.getState().setBinding(BLOCK_SELECT_MODIFIER_ID, 'Alt');

    expect(selectBlockSelectModifier(useShortcutStore.getState())).toBe('Alt');
    // 与默认值相同 ⇒ toOverrides 不再写这一项（不产生伪覆盖）
    expect(toOverrides(useShortcutStore.getState().bindings)[BLOCK_SELECT_MODIFIER_ID]).toBeUndefined();
    expect(useShortcutStore.getState().getBinding(BLOCK_SELECT_MODIFIER_ID)).toBe('Alt');
  });

  it('修饰键白名单：非法值被拒且不改状态（Shift / Meta 字面合法）', () => {
    useShortcutStore.getState().setBinding(BLOCK_SELECT_MODIFIER_ID, 'Ctrl');

    // 组合键（含非修饰键）不是合法修饰键值
    useShortcutStore.getState().setBinding(BLOCK_SELECT_MODIFIER_ID, 'Ctrl+P');
    expect(useShortcutStore.getState().lastError).toBe('块选择修饰键只能是 Alt / Ctrl / Shift / Meta');
    expect(useShortcutStore.getState().getBinding(BLOCK_SELECT_MODIFIER_ID)).toBe('Ctrl');

    // 无法识别的键位字符串走另一条拒绝分支
    useShortcutStore.getState().setBinding(BLOCK_SELECT_MODIFIER_ID, '');
    expect(useShortcutStore.getState().lastError).toBe('无法识别的键位：');
    expect(useShortcutStore.getState().getBinding(BLOCK_SELECT_MODIFIER_ID)).toBe('Ctrl');

    // Shift / Meta 字面合法（按实现：派生侧仍回 Alt，只有 Ctrl 会切换过滤器）
    useShortcutStore.getState().setBinding(BLOCK_SELECT_MODIFIER_ID, 'Shift');
    expect(useShortcutStore.getState().getBinding(BLOCK_SELECT_MODIFIER_ID)).toBe('Shift');
    expect(selectBlockSelectModifier(useShortcutStore.getState())).toBe('Alt');
  });
});

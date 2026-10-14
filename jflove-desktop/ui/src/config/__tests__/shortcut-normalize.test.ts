/**
 * 快捷键规范化守门用例（AC-9 判据面 / 代码审查 M-12）
 *
 * ## 判据出处
 *
 * - 需求（`需求文档/v1.6.0.md` §4.2.2 / AC-9）：键位归一（`Ctrl`/`Control` 同一、修饰键顺序固定、
 *   键名大小写无关、`→` 与 `ArrowRight` 视为同一键）、非法键位**必须被拒绝**而不是静默保存。
 * - 设计（`设计文档/v1.6.0.md` §4.2.4）：`shortcuts.json` 里的非法值一律忽略回默认，不阻塞启动。
 * - 代码审查报告 v1.6.0 **M-12 [中等]**：`normalizeShortcut('!!!')` 原样返回、`isValidShortcut('!!!') === true`
 *   ⇒「用户输入非法键位后保存成功但永远不触发（静默失效）」；建议加白名单（`A-Z0-9`、`F1-F24`、
 *   具名键、方向键）。本文件按设计口径写断言，不满足的用 `it.fails` 登记（`it.fails` 必须失败才算通过）。
 * - `formatShortcut` 的去重分支（`shortcuts.ts:381-395` 注释）：仅修饰键的 chord 若不去重会输出
 *   `Alt+Alt`，既破坏 `normalizeShortcut` 幂等，又会被 store 的修饰键白名单拒收（第 14 项永远改不了键）。
 *   该分支已修（S-9 同轮回修），本文件把它固化成回归守门。
 */

import { describe, expect, it } from 'vitest';
import {
  MODIFIER_KEYS,
  defaultNormalizationConsistent,
  formatShortcut,
  formatShortcutForDisplay,
  isModifierOnlyShortcut,
  isValidShortcut,
  normalizeShortcut,
  parseShortcut,
} from '../shortcuts';

/** 取出必存在的 chord（避免在断言里散落非空断言） */
function chordOf(raw: string) {
  const chord = parseShortcut(raw);
  if (!chord) throw new Error(`测试前提不成立：${raw} 应可解析`);
  return chord;
}

describe('formatShortcut：不得输出重复修饰键（仅修饰键写法守门）', () => {
  it('仅修饰键的 chord 归一后仍是它自己（`Alt` → `Alt`，不出现 `Alt+Alt`）', () => {
    for (const name of MODIFIER_KEYS) {
      const formatted = formatShortcut(chordOf(name));
      expect(formatted, `${name} 被格式化成重复修饰键`).toBe(name);
      expect(normalizeShortcut(name), `${name} 归一不幂等`).toBe(name);
    }
  });

  it('普通组合键里重复写同一个修饰键也只输出一次（`Ctrl+Alt+Shift+Ctrl+N` → `Ctrl+Alt+Shift+N`）', () => {
    expect(normalizeShortcut('Ctrl+Alt+Shift+Ctrl+N')).toBe('Ctrl+Alt+Shift+N');
    expect(normalizeShortcut('Alt+Alt+Z')).toBe('Alt+Z');
  });

  it('修饰键顺序固定为 `Ctrl+Alt+Shift+Meta`，键名大小写与别名归一', () => {
    expect(normalizeShortcut('shift+alt+control+arrowup')).toBe('Ctrl+Alt+Shift+Up');
    expect(normalizeShortcut('control+option+arrowup')).toBe('Ctrl+Alt+Up');
    expect(normalizeShortcut('cmd+s')).toBe('Meta+S');
    expect(normalizeShortcut('esc')).toBe('Escape');
    expect(normalizeShortcut('ctrl + s')).toBe('Ctrl+S');
  });

  it('展示用格式化把方向键换成符号（保存值不受影响）', () => {
    expect(formatShortcutForDisplay('Ctrl+Alt+ArrowRight')).toBe('Ctrl+Alt+→');
    expect(formatShortcutForDisplay('Alt+ArrowUp')).toBe('Alt+↑');
    // 保存口径仍是归一名，不是符号
    expect(normalizeShortcut('Ctrl+Alt+ArrowRight')).toBe('Ctrl+Alt+Right');
  });

  it('`defaultNormalizationConsistent()`：20 项默认键位的 `normalizedDefault` 与实现一致', () => {
    expect(defaultNormalizationConsistent()).toBe(true);
  });

  it('合法键位必须继续被判为合法（白名单修复不能误伤既有键位）', () => {
    for (const raw of ['Ctrl+S', 'Ctrl+Shift+S', 'Ctrl+/', 'Ctrl+=', 'Ctrl+-', 'Ctrl+0', 'Alt', 'F1', 'F12', 'Escape', 'ArrowRight', 'Ctrl+ArrowLeft', 'Meta+K']) {
      expect(isValidShortcut(raw), `${raw} 应合法`).toBe(true);
      expect(normalizeShortcut(raw), `${raw} 应可归一`).not.toBeNull();
    }
    // 典型非法输入（空串 / 只有加号 / 两个非修饰键）现有实现已能拒绝
    for (const raw of ['', '+', 'Ctrl+A+B']) {
      expect(normalizeShortcut(raw), `${raw} 应被拒绝`).toBeNull();
    }
  });

  it.fails('N-2（本文件新发现，轻微）：尾部孤立的 `+` 应被判为非法，不应静默当成 `Ctrl`', () => {
    // 实现：`parseShortcut` 先按 `+` 切分并**丢弃空片段**（`shortcuts.ts:352-355`），
    // 于是 `'Ctrl+'` 只剩一个修饰键 ⇒ 按「仅修饰键」规则通过，归一成 `'Ctrl'`。
    // 影响：用户输入到一半（或粘贴时多带一个 `+`）会被当合法值接受，与 M-12 同源。
    expect(normalizeShortcut('Ctrl+')).toBeNull();
    expect(isValidShortcut('Ctrl+')).toBe(false);
  });

  it('`isModifierOnlyShortcut` 只对块选择那种「仅修饰键」写法成立', () => {
    expect(isModifierOnlyShortcut('Alt')).toBe(true);
    expect(isModifierOnlyShortcut('ctrl')).toBe(true);
    expect(isModifierOnlyShortcut('Ctrl+S')).toBe(false);
    expect(isModifierOnlyShortcut('Ctrl+Alt')).toBe(false);
  });
});

describe('M-12 登记：非法键位被静默接受（按设计口径这些断言当前必然失败）', () => {
  it.fails('M-12：`normalizeShortcut(\'!!!\')` 应返回 null（非法串必须被拒绝）', () => {
    expect(normalizeShortcut('!!!')).toBeNull();
  });

  it.fails('M-12：`isValidShortcut(\'!!!\')` 应为 false', () => {
    expect(isValidShortcut('!!!')).toBe(false);
  });

  // 审查建议的白名单之外的键名（多字符未识别键）：现状同样被接受
  for (const raw of ['Foo', 'Ctrl+Foo', 'F25', '@@']) {
    it.fails(`M-12：白名单外的键名 \`${raw}\` 应被判为非法`, () => {
      expect(isValidShortcut(raw)).toBe(false);
    });
  }

  it('现状锚点（M-12 未修时的实际行为，修复后本用例需同步更新）', () => {
    // 该断言只固定「现在的实现」，不代表验收通过；验收判据是同组上面的 it.fails
    expect(normalizeShortcut('!!!')).toBe('!!!');
    expect(isValidShortcut('!!!')).toBe(true);
    // 根因：`canonicalToken` 对未识别的多字符 token 只做首字母大写并原样保留（`shortcuts.ts:340-341`）
    expect(normalizeShortcut('foo')).toBe('Foo');
  });
});

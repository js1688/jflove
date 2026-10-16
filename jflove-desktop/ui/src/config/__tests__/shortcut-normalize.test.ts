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
 *   `Alt+Alt`，既破坏 `normalizeShortcut` 幂等，又会被 store 的修饰键白名单拒收（第 12 项永远改不了键）。
 *   该分支已修（S-9 同轮回修），本文件把它固化成回归守门。
 * - 设计（`设计文档/v1.6.1.md` §4.7，配合 `需求文档/v1.6.1.md` §3.2）：条目集 **20 → 18**
 *   （作废「平铺」`pane.tile` / 「层叠」`pane.cascade`；`pane.next` / `pane.prev` 改名
 *   「下一个标签页」/「上一个标签页」；`pane.close` 改名「关闭当前标签页」且默认键
 *   `Ctrl+Alt+W` → `Ctrl+W`）。本文件末组用例把「恰为 18 项」固化为守门断言。
 */

import { describe, expect, it } from 'vitest';
import {
  DEFAULT_BINDINGS,
  MODIFIER_KEYS,
  SHORTCUT_DEFS,
  SHORTCUT_IDS,
  defaultNormalizationConsistent,
  formatShortcut,
  formatShortcutForDisplay,
  getShortcutDef,
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

  it('`defaultNormalizationConsistent()`：18 项默认键位的 `normalizedDefault` 与实现一致', () => {
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

describe('v1.6.1 §4.7 条目集守门：20 → 18（作废「平铺」/「层叠」）', () => {
  it('条目数恰为 18，且 id 全表唯一、与 SHORTCUT_IDS / DEFAULT_BINDINGS 同源', () => {
    expect(SHORTCUT_DEFS).toHaveLength(18);
    expect(SHORTCUT_IDS).toHaveLength(18);
    // 全表 id 唯一（有重复会同时静默掉一项渲染与一项冲突检测）
    expect(new Set(SHORTCUT_IDS).size).toBe(18);
    // 三个导出面必须同源同序
    expect(SHORTCUT_IDS).toEqual(SHORTCUT_DEFS.map((def) => def.id));
    expect(Object.keys(DEFAULT_BINDINGS)).toHaveLength(18);
    for (const def of SHORTCUT_DEFS) {
      // 生效默认值必须是**归一化**形态（Phase 5 审查 S-2 / BL-35：取 `def.defaultKey` 原文会让
      // `pane.next` / `pane.prev` 的 `ArrowRight/Left` 与事件侧的 `Right/Left` 对不上 ⇒ 死键）
      expect(DEFAULT_BINDINGS[def.id], `${def.id} 缺默认键`).toBe(def.meta.normalizedDefault);
      expect(normalizeShortcut(def.defaultKey), `${def.id} 归一化口径漂移`).toBe(
        DEFAULT_BINDINGS[def.id],
      );
    }
  });

  it('`pane.close` 默认键 = `Ctrl+W`（由 `Ctrl+Alt+W` 改）', () => {
    const def = getShortcutDef('pane.close');
    expect(def, 'pane.close 条目必须存在').not.toBeNull();
    expect(def?.defaultKey).toBe('Ctrl+W');
    expect(def?.meta.normalizedDefault).toBe('Ctrl+W');
    expect(DEFAULT_BINDINGS['pane.close']).toBe('Ctrl+W');
    // 归一化口径下确实落在 `Ctrl+W`
    expect(normalizeShortcut(def?.defaultKey ?? '')).toBe('Ctrl+W');
    // 旧默认键不得再被任何条目占用（否则会与 `Ctrl+W` 冲突检测撞车）
    for (const other of SHORTCUT_DEFS) {
      expect(normalizeShortcut(other.defaultKey), `${other.id} 仍占用旧值`).not.toBe(
        normalizeShortcut('Ctrl+Alt+W'),
      );
    }
  });

  it('「平铺」`pane.tile` / 「层叠」`pane.cascade` 两项不存在', () => {
    const ids: readonly string[] = SHORTCUT_IDS;
    expect(ids).not.toContain('pane.tile');
    expect(ids).not.toContain('pane.cascade');
    expect(getShortcutDef('pane.tile')).toBeNull();
    expect(getShortcutDef('pane.cascade')).toBeNull();
    expect(DEFAULT_BINDINGS['pane.tile']).toBeUndefined();
    expect(DEFAULT_BINDINGS['pane.cascade']).toBeUndefined();
    // 作废项的中文名也不得残留在任何条目的功能名 / 说明里
    for (const def of SHORTCUT_DEFS) {
      expect(`${def.label}｜${def.description}`).not.toContain('平铺');
      expect(`${def.label}｜${def.description}`).not.toContain('层叠');
    }
  });

  it('改名三项的中文名与作用域（`pane.next` / `pane.prev` / `pane.close`）', () => {
    expect(getShortcutDef('pane.next')?.label).toBe('下一个标签页');
    expect(getShortcutDef('pane.prev')?.label).toBe('上一个标签页');
    expect(getShortcutDef('pane.close')?.label).toBe('关闭当前标签页');
    // 作用域未被改名牵连：只有 `pane.close` 是「编辑面板聚焦」（`pane`）
    expect(getShortcutDef('pane.close')?.scope).toBe('pane');
    expect(getShortcutDef('pane.next')?.scope).toBe('window');
    expect(getShortcutDef('pane.prev')?.scope).toBe('window');
  });

  it('中文文案已无「窗格」旧称（v1.6.1 统一为「标签页」/「编辑面板」）', () => {
    for (const def of SHORTCUT_DEFS) {
      expect(`${def.label}｜${def.description}`, `${def.id} 仍含旧称`).not.toContain('窗格');
    }
  });

  it('18 项下的默认键位全部可归一，且默认表内部无冲突', () => {
    const seen = new Map<string, string>();
    for (const def of SHORTCUT_DEFS) {
      const normalized = normalizeShortcut(def.defaultKey);
      expect(normalized, `${def.id} 的默认键不可归一`).toBe(def.meta.normalizedDefault);
      const previous = seen.get(normalized ?? '');
      expect(previous, `${def.id} 与 ${previous} 默认键撞车`).toBeUndefined();
      seen.set(normalized ?? '', def.id);
    }
    expect(seen.size).toBe(18);
  });
});

/**
 * v1.6.1 本轮（用户实测反馈 F3 / AC-75 / 裁决 V-3）：**只换 6 项** `Ctrl+Alt+*` 默认键。
 *
 * ⚠ **本组第 6 项（`view.diff`）于 2026-10-16 就地更正**：`Ctrl+D` 由用户裁决**让回 CM6**
 * （CM6 `searchKeymap` 的 `Mod-d` 处理器就是 `selectNextOccurrence`"选中下一个相同词"，
 * 是各家编辑器通用键位；而我们的运行时在 **window 捕获阶段** `preventDefault + stopPropagation`
 * ⇒ 配成 `Ctrl+D` 会直接抢掉那个功能），`view.diff` 改用 **`Ctrl+Shift+D`**
 * （在全部 8 张 keymap 里 **0 命中**）。因此本组期望值为 `Ctrl+Shift+D`，
 * 并由「`Ctrl+D` 不得出现在任何默认键位里」这条**可失败**的断言守住这个边界。
 *
 * 为什么放在"归一化"文件里：这 6 项里有两项依赖键名别名表（`pageup` / `pagedown`），
 * 而"事件侧还原 == 归一化"正是本文件守的那条口径；`Ctrl+PageDown` / `Ctrl+PageUp`
 * 一旦别名缺失就会变成 `Ctrl+PageDown` ≠ `Ctrl+Pagedown` 之类的静默死键。
 */
describe('AC-75 6 项新默认键 + 12 项不变（归一化往返 + 版本不迁移）', () => {
  const NEW_DEFAULTS: Readonly<Record<string, string>> = {
    'pane.new': 'Ctrl+N',
    'file.open': 'Ctrl+O',
    'pane.next': 'Ctrl+PageDown',
    'pane.prev': 'Ctrl+PageUp',
    'view.toolbox': 'Ctrl+K',
    'view.diff': 'Ctrl+Shift+D',
  };

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

  it('6 项新默认键逐项等于用户裁决的字面值（`defaultKey` 与 `normalizedDefault` 同源）', () => {
    for (const [id, expected] of Object.entries(NEW_DEFAULTS)) {
      const def = getShortcutDef(id);
      expect(def?.defaultKey, `${id} 的 defaultKey`).toBe(expected);
      expect(def?.meta.normalizedDefault, `${id} 的 normalizedDefault`).toBe(expected);
      expect(DEFAULT_BINDINGS[id], `${id} 的生效默认键`).toBe(expected);
      expect(defaultNormalizationConsistent(), '6 项新默认值与归一化不一致').toBe(true);
    }
  });

  it('其余 12 项逐项不变（AC-75⑥）', () => {
    expect(Object.keys(UNCHANGED_DEFAULTS)).toHaveLength(12);
    for (const [id, expected] of Object.entries(UNCHANGED_DEFAULTS)) {
      expect(getShortcutDef(id)?.defaultKey, `${id} 被误改`).toBe(expected);
      expect(DEFAULT_BINDINGS[id], `${id} 的生效值被误改`).toBe(expected);
    }
  });

  it('`Ctrl+PageDown` / `Ctrl+PageUp` 归一化幂等，且裸 `PageDown` / `PageUp` 归一后不是同一形态', () => {
    for (const raw of ['Ctrl+PageDown', 'Ctrl+PageUp']) {
      expect(normalizeShortcut(raw), `${raw} 归一不幂等`).toBe(raw);
      // 别名表命中：小写 / 大小写混合写法都要落到同一归一名
      expect(normalizeShortcut(raw.toLowerCase())).toBe(raw);
    }
    // 裸键与带 Ctrl 的版本**不同**（裸 PageDown/PageUp 被 CM6 三张 keymap 占用）
    expect(normalizeShortcut('PageDown')).not.toBe(normalizeShortcut('Ctrl+PageDown'));
    expect(normalizeShortcut('PageUp')).not.toBe(normalizeShortcut('Ctrl+PageUp'));
    // 展示口径不引入符号（方向键才换符号）
    expect(formatShortcutForDisplay('Ctrl+PageDown')).toBe('Ctrl+PageDown');
  });

  it('默认键列中 0 项以 `Ctrl+Alt+` 开头（AC-75④）', () => {
    const offenders = SHORTCUT_DEFS.filter((def) => def.defaultKey.startsWith('Ctrl+Alt+')).map(
      (def) => `${def.id}=${def.defaultKey}`,
    );
    expect(offenders, `仍占用 Ctrl+Alt+*：${offenders.join('、')}`).toEqual([]);
  });

  it('`Ctrl+D` 不得被任何默认键位占用（必须留给 CM6 的 selectNextOccurrence）；`Ctrl+Shift+D` 归一化往返一致', () => {
    // ① `Ctrl+D` 必须完全空出：CM6 `searchKeymap` 的 `Mod-d` 处理器就是 `selectNextOccurrence`
    //    （"选中下一个相同词"，各家编辑器通用键位）。运行时在 window 捕获阶段就
    //    `preventDefault + stopPropagation` ⇒ 谁占了 `Ctrl+D`，谁的编辑器功能就没了。
    const ctrlD = normalizeShortcut('Ctrl+D');
    expect(ctrlD, '`Ctrl+D` 应可归一化').toBe('Ctrl+D');
    const offenders = SHORTCUT_DEFS.filter(
      (def) =>
        def.meta.normalizedDefault === ctrlD ||
        normalizeShortcut(def.defaultKey) === ctrlD ||
        DEFAULT_BINDINGS[def.id] === ctrlD,
    ).map((def) => `${def.id}=${def.defaultKey}`);
    expect(offenders, '`Ctrl+D` 被占用（必须留给 CM6）：' + offenders.join('、')).toEqual([]);

    // ② `view.diff` 落在 `Ctrl+Shift+D`，且 `defaultKey` / `normalizedDefault` / 生效值三者同源
    const diff = getShortcutDef('view.diff');
    expect(diff?.defaultKey).toBe('Ctrl+Shift+D');
    expect(diff?.meta.normalizedDefault).toBe('Ctrl+Shift+D');
    expect(DEFAULT_BINDINGS['view.diff']).toBe('Ctrl+Shift+D');

    // ③ 归一化幂等 + 别名/大小写写法都落到同一归一名（事件侧还原才匹配得上）
    for (const raw of ['Ctrl+Shift+D', 'ctrl+shift+d', 'Shift+Control+d']) {
      expect(normalizeShortcut(raw), `${raw} 未被归一成 Ctrl+Shift+D`).toBe('Ctrl+Shift+D');
    }
    // ④ 与 `Ctrl+D` 是**不同**的键位（这正是本轮更正的意义）
    expect(normalizeShortcut('Ctrl+Shift+D')).not.toBe(normalizeShortcut('Ctrl+D'));
    // ⑤ 全表默认键位仍互不撞车（新增 Shift 不能撞上 `Ctrl+Shift+S` / `Ctrl+Shift+V`）
    expect(defaultNormalizationConsistent(), '默认键位与归一化口径漂移').toBe(true);
  });

  it('`SHORTCUTS_VERSION` 保持 1、不做迁移（未自定义者靠 `toOverrides` 剔除自动生效）', async () => {
    const { SHORTCUTS_VERSION, toOverrides, withOverrides } = await import('../shortcuts');
    expect(SHORTCUTS_VERSION, '本轮不做配置迁移，版本号不得升').toBe(1);
    // 从未自定义 ⇒ 空覆盖表 ⇒ 6 项新默认键直接生效
    expect(toOverrides({ ...DEFAULT_BINDINGS })).toEqual({});
    expect(withOverrides({})['pane.new']).toBe('Ctrl+N');
    expect(withOverrides({})['view.toolbox']).toBe('Ctrl+K');
    // 用户自定义过的项保持用户值（AC-75⑦）
    expect(withOverrides({ 'pane.new': 'Ctrl+Shift+N' })['pane.new']).toBe('Ctrl+Shift+N');
  });
});

/**
 * 文本编辑器快捷键表（设计 v1.6.1 §4.7 / v1.6.0 §4.2）—— **默认键位 + 中文功能说明 + 冲突检测元数据**
 *
 * ## 定位
 *
 * 本文件是快捷键的**唯一手写真相**：默认键位、界面显示名、中文功能说明、作用域、
 * 冲突检测所需的归一化元数据都在这里。`stores/shortcut-store.ts` 只做「覆盖值 + 落盘 + 冲突判定」，
 * 设置页直接消费本文件逐项渲染「功能名 + 说明 + 默认键 + 当前键 + 修改 + 重置」（AC-6）。
 *
 * ## 硬口径
 *
 * 1. **18 项，逐项对齐 v1.6.1 §4.7 的默认键位表**（v1.6.0 的 20 项 − 作废「平铺」「层叠」2 项；
 *    3 项改名、1 项改默认键）—— 默认值改动**只改本文件**（R-4 的缓解措施）。
 * 2. **不注册任何全局热键**（AC-8 / F2.5）：本文件只声明数据，唯一的事件入口是
 *    `keyboardEventToShortcut(ev)`（纯函数）；应用级监听由组件挂在 `window` 的**捕获阶段**，
 *    窗口失焦即失效（§4.2.2）。
 * 3. 键位书写形式统一为 `Ctrl+Alt+Shift+Meta+Key`（修饰键顺序固定，键名归一），
 *    归一化实现即 `normalizeShortcut`；保存前对全表归一化比对，重复即拒绝保存（AC-9）。
 */

import type { BlockSelectModifier } from '../types/scratch';

/**
 * 作用域：`window` = 任意路由页都响应（v1.6.1 §4.7「应用」）；
 * `pane` = 仅编辑面板 DOM 内响应（v1.6.1 §4.7「编辑面板聚焦」）。
 */
export type ShortcutScope = 'window' | 'pane';

/** 18 个功能 id（v1.6.1 §4.7 表逐项对齐） */
export type ShortcutId =
  | 'pane.new'
  | 'pane.close'
  | 'pane.next'
  | 'pane.prev'
  | 'file.open'
  | 'file.save'
  | 'file.saveAs'
  | 'pane.togglePreview'
  | 'edit.find'
  | 'edit.gotoLine'
  | 'edit.commentToggle'
  | 'edit.blockSelectModifier'
  | 'view.toolbox'
  | 'view.diff'
  | 'view.zoomIn'
  | 'view.zoomOut'
  | 'view.zoomReset'
  | 'view.toggleWordWrap';

/** 冲突检测 / 匹配用的归一化组合 */
export interface ShortcutChord {
  ctrl: boolean;
  alt: boolean;
  shift: boolean;
  meta: boolean;
  /** 归一化后的键名（如 `N` / `Right` / `=` / `Alt`） */
  key: string;
}

/** 单个快捷键条目 */
export interface ShortcutDef {
  id: ShortcutId;
  /** 功能名（界面显示，§4.2.1 的「功能名」列） */
  label: string;
  /** 中文功能说明（**必填**，F2.4 / AC-6，设置页逐项展示） */
  description: string;
  /** 默认键（归一化书写形式；展示时用 `formatShortcutForDisplay` 转成箭头等符号） */
  defaultKey: string;
  scope: ShortcutScope;
  /** 冲突检测所需元数据 */
  meta: {
    /** 默认键归一化后的字符串（供一致性自测与冲突基线比对） */
    normalizedDefault: string;
    /** 是否为「仅修饰键」条目（块选择修饰键）：只能配 Alt / Ctrl / Shift / Meta */
    modifierOnly: boolean;
  };
}

/** 修饰键名（归一化后） */
export const MODIFIER_KEYS = ['Ctrl', 'Alt', 'Shift', 'Meta'] as const;

/** 块选择修饰键对应的功能 id（`scratch-store` / `shortcut-store` 都要引它，避免各写字符串） */
export const BLOCK_SELECT_MODIFIER_ID: ShortcutId = 'edit.blockSelectModifier';

/** 块选择修饰键的默认值（v1.6.1 §4.7 第 12 项 / v1.6.0 §6.1.2） */
export const DEFAULT_BLOCK_SELECT_MODIFIER: BlockSelectModifier = 'Alt';

/** 快捷键偏好文件（**由 Python 侧读写**，渲染层不拼路径） */
export const SHORTCUTS_FILE_NAME = 'shortcuts.json';

/** 快捷键偏好结构版本（§4.2.4；不匹配时忽略文件回默认） */
export const SHORTCUTS_VERSION = 1;

/**
 * 被 CM6 内核独占的组合（§4.2.3 ③）：用户若强行配成同一个键，**应用级优先**，
 * 冲突提示里要额外说明「该键同时被编辑器内核使用」。
 */
export const EDITOR_KEYMAP_RESERVED: readonly string[] = ['Ctrl+Enter', 'Ctrl+Space'];

/**
 * 默认键位表（18 项，逐项对齐 v1.6.1 §4.7）。
 *
 * ⚠ 顺序即设计表的行序（1 → 18），便于逐项核对。
 *
 * ## v1.6.1 本轮（用户实测反馈 F3 / AC-75 / 裁决 V-3）只改了 **6 项**默认键
 *
 * | id | 原默认 | 新默认 |
 * |----|--------|--------|
 * | `pane.new` | `Ctrl+Alt+N` | `Ctrl+N` |
 * | `file.open` | `Ctrl+Alt+O` | `Ctrl+O` |
 * | `pane.next` | `Ctrl+Alt+ArrowRight` | `Ctrl+PageDown` |
 * | `pane.prev` | `Ctrl+Alt+ArrowLeft` | `Ctrl+PageUp` |
 * | `view.toolbox` | `Ctrl+Alt+K` | `Ctrl+K` |
 * | `view.diff` | `Ctrl+Alt+D` | `Ctrl+Shift+D` |
 *
 * 理由：旧值抢系统键（`Ctrl+Alt+*` 与常见输入法 / 系统快捷方式冲突）。
 *
 * ⚠ **`view.diff` 由 `Ctrl+D` 更正为 `Ctrl+Shift+D`**（用户裁决）：CM6 `searchKeymap` 的
 * `Mod-d`（Windows = `Ctrl+D`）处理器**就是** `selectNextOccurrence` —— "选中下一个相同词"
 * 是各家编辑器的通用键位，**必须留给编辑器**；而运行时在 **window 捕获阶段**
 * `preventDefault + stopPropagation`（`utils/scratch/shortcut-runtime.ts`）⇒ 配成 `Ctrl+D`
 * 会直接**抢掉**编辑器那个功能。`Ctrl+Shift+D` 在**全部 8 张 keymap** 里 **0 命中**，故改用它。
 *
 * 其余 **12 项一字不动**（`Ctrl+W` / `Ctrl+S` / `Ctrl+Shift+S` / `Ctrl+Shift+V` / `Ctrl+F` /
 * `Ctrl+G` / `Ctrl+/` / `Alt` / `Ctrl+=` / `Ctrl+-` / `Ctrl+0` / `Alt+Z`）。
 *
 * **本轮 `SHORTCUTS_VERSION` 保持 `1`、不做配置迁移**：`toOverrides`（下方）会剔除与默认值
 * 相同的项 ⇒ 从没改过键的用户，`shortcuts.json` 里**根本没有这 6 个 id** ⇒ 新默认值自动生效
 * （AC-75⑤）；用户改过的项由 `withOverrides` 的覆盖值优先保住（AC-75⑦）。
 *
 * 键位可行性（离线包 + 源码双向复核，证据见开发记录）：
 *   - `Ctrl+K`/`Ctrl+N`/`Ctrl+O` 在**实际装配的 6 张 keymap** 里 0 命中
 *     （唯一来源 `emacsStyleKeymap` **未被装配**）；
 *   - **`Ctrl+D` 必须留给编辑器**（见上表 `view.diff` 一行）；`Ctrl+Shift+D` 在**全部 8 张
 *     keymap** 里 0 命中 ⇒ 选它；
 *   - 裸 `PageDown`/`PageUp` 被 3 张表占用 ⇒ 必须用 `Ctrl+` 前缀版本（`Ctrl+PageDown`/`PageUp` 0 命中）；
 *   - `KEY_ALIASES` 已含 `pageup`/`pagedown` ⇒ **不需要**补归一化（`defaultKey` 与
 *     `meta.normalizedDefault` 因此逐字相同）。
 */
export const SHORTCUT_DEFS: readonly ShortcutDef[] = [
  {
    id: 'pane.new',
    label: '新建标签页',
    description: '在当前编辑器工作区中打开一个新的空白标签页',
    defaultKey: 'Ctrl+N',
    scope: 'window',
    meta: { normalizedDefault: 'Ctrl+N', modifierOnly: false },
  },
  {
    id: 'pane.close',
    label: '关闭当前标签页',
    description: '关闭当前聚焦的标签页；内容未保存时会先询问',
    defaultKey: 'Ctrl+W',
    scope: 'pane',
    meta: { normalizedDefault: 'Ctrl+W', modifierOnly: false },
  },
  {
    id: 'pane.next',
    label: '下一个标签页',
    description: '按标签条顺序切换到下一个标签页，并把它置为当前激活项',
    defaultKey: 'Ctrl+PageDown',
    scope: 'window',
    meta: { normalizedDefault: 'Ctrl+PageDown', modifierOnly: false },
  },
  {
    id: 'pane.prev',
    label: '上一个标签页',
    description: '按标签条顺序切换到上一个标签页，并把它置为当前激活项',
    defaultKey: 'Ctrl+PageUp',
    scope: 'window',
    meta: { normalizedDefault: 'Ctrl+PageUp', modifierOnly: false },
  },
  {
    id: 'file.open',
    label: '打开本地文件',
    description: '弹出原生文件对话框选择本机文件，校验通过后在新标签页打开',
    defaultKey: 'Ctrl+O',
    scope: 'window',
    meta: { normalizedDefault: 'Ctrl+O', modifierOnly: false },
  },
  {
    id: 'file.save',
    label: '保存',
    description:
      '已有路径的文件直接写回；从未保存过的标签页弹出原生对话框指定位置与文件名',
    defaultKey: 'Ctrl+S',
    scope: 'pane',
    meta: { normalizedDefault: 'Ctrl+S', modifierOnly: false },
  },
  {
    id: 'file.saveAs',
    label: '另存为',
    description: '重新指定路径与文件名保存，原文件保持不变',
    defaultKey: 'Ctrl+Shift+S',
    scope: 'pane',
    meta: { normalizedDefault: 'Ctrl+Shift+S', modifierOnly: false },
  },
  {
    id: 'pane.togglePreview',
    label: '切换预览 / 分屏',
    description: '在编辑、预览、分屏三种视图之间循环切换（Markdown 类文档）',
    defaultKey: 'Ctrl+Shift+V',
    scope: 'pane',
    meta: { normalizedDefault: 'Ctrl+Shift+V', modifierOnly: false },
  },
  {
    id: 'edit.find',
    label: '查找 / 替换',
    description: '打开查找替换面板，支持正则匹配与正则替换',
    defaultKey: 'Ctrl+F',
    scope: 'pane',
    meta: { normalizedDefault: 'Ctrl+F', modifierOnly: false },
  },
  {
    id: 'edit.gotoLine',
    label: '跳转到行',
    description: '输入行号后把光标移到该行行首',
    defaultKey: 'Ctrl+G',
    scope: 'pane',
    meta: { normalizedDefault: 'Ctrl+G', modifierOnly: false },
  },
  {
    id: 'edit.commentToggle',
    label: '切换行注释',
    description: '对选中行加注释符；再执行一次恢复原文本（逐字符相同）',
    defaultKey: 'Ctrl+/',
    scope: 'pane',
    meta: { normalizedDefault: 'Ctrl+/', modifierOnly: false },
  },
  {
    id: 'edit.blockSelectModifier',
    label: '块选择修饰键',
    description: '按住它再用鼠标左键拖拽产生矩形块选择；不按则是普通流式选择',
    defaultKey: DEFAULT_BLOCK_SELECT_MODIFIER,
    scope: 'pane',
    meta: { normalizedDefault: DEFAULT_BLOCK_SELECT_MODIFIER, modifierOnly: true },
  },
  {
    id: 'view.toolbox',
    label: '打开工具箱',
    description: '打开 12 项文字处理工具箱菜单',
    defaultKey: 'Ctrl+K',
    scope: 'window',
    meta: { normalizedDefault: 'Ctrl+K', modifierOnly: false },
  },
  {
    id: 'view.diff',
    label: '两段文本对比',
    description: '打开 diff 面板，第二段可来自剪贴板、另一个标签页或本地文件',
    // `Ctrl+Shift+D`（**不是** `Ctrl+D`）：`Ctrl+D` 是 CM6 `searchKeymap` 的 `selectNextOccurrence`
    // （"选中下一个相同词"），必须留给编辑器；`Ctrl+Shift+D` 在全部 8 张 keymap 里 0 命中。
    defaultKey: 'Ctrl+Shift+D',
    scope: 'window',
    meta: { normalizedDefault: 'Ctrl+Shift+D', modifierOnly: false },
  },
  {
    id: 'view.zoomIn',
    label: '增大字号',
    description: '把当前编辑面板的字号调大一档',
    defaultKey: 'Ctrl+=',
    scope: 'pane',
    meta: { normalizedDefault: 'Ctrl+=', modifierOnly: false },
  },
  {
    id: 'view.zoomOut',
    label: '减小字号',
    description: '把当前编辑面板的字号调小一档',
    defaultKey: 'Ctrl+-',
    scope: 'pane',
    meta: { normalizedDefault: 'Ctrl+-', modifierOnly: false },
  },
  {
    id: 'view.zoomReset',
    label: '重置字号',
    description: '把当前编辑面板字号恢复到默认值',
    defaultKey: 'Ctrl+0',
    scope: 'pane',
    meta: { normalizedDefault: 'Ctrl+0', modifierOnly: false },
  },
  {
    id: 'view.toggleWordWrap',
    label: '切换自动换行',
    description: '开启或关闭当前编辑面板的自动换行',
    defaultKey: 'Alt+Z',
    scope: 'pane',
    meta: { normalizedDefault: 'Alt+Z', modifierOnly: false },
  },
];

/** 全部功能 id（按表序） */
export const SHORTCUT_IDS: readonly ShortcutId[] = SHORTCUT_DEFS.map((def) => def.id);

/**
 * 默认键位映射（`id` → 默认键），供 store 计算「与默认值不同」的覆盖项。
 *
 * ⚠ **必须取归一化形态**（`meta.normalizedDefault`），不能取 `def.defaultKey` 原文：
 * 事件侧的 `keyboardEventToShortcut()` 产出的是归一化弦（`formatShortcut`），
 * 而 `def.defaultKey` 里方向键写的是 `ArrowRight/ArrowLeft`、归一化后是 `Right/Left`
 * ⇒ store 取原文会让 `getBinding(id) === chord` **永不成立**（v1.6.0 的 N-1 死键，
 * 本版 Phase 5 审查 S-2 修掉：`pane.next` / `pane.prev` 两个默认键从此真实生效）。
 * 一致性由 `defaultNormalizationConsistent()`（`:521`）守着，两处必须同源。
 */
export const DEFAULT_BINDINGS: Readonly<Record<string, string>> = Object.freeze(
  SHORTCUT_DEFS.reduce<Record<string, string>>((acc, def) => {
    acc[def.id] = def.meta.normalizedDefault;
    return acc;
  }, {}),
);

// ── 键名归一化（顺序与键名都归一，冲突检测与匹配共用同一套规则）──

const MODIFIER_ALIASES: Readonly<Record<string, 'Ctrl' | 'Alt' | 'Shift' | 'Meta'>> = {
  ctrl: 'Ctrl',
  control: 'Ctrl',
  ctl: 'Ctrl',
  alt: 'Alt',
  option: 'Alt',
  opt: 'Alt',
  shift: 'Shift',
  meta: 'Meta',
  cmd: 'Meta',
  command: 'Meta',
  win: 'Meta',
  super: 'Meta',
};

const KEY_ALIASES: Readonly<Record<string, string>> = {
  arrowup: 'Up',
  arrowdown: 'Down',
  arrowleft: 'Left',
  arrowright: 'Right',
  up: 'Up',
  down: 'Down',
  left: 'Left',
  right: 'Right',
  '↑': 'Up',
  '↓': 'Down',
  '←': 'Left',
  '→': 'Right',
  esc: 'Escape',
  escape: 'Escape',
  return: 'Enter',
  enter: 'Enter',
  space: 'Space',
  spacebar: 'Space',
  ' ': 'Space',
  del: 'Delete',
  delete: 'Delete',
  backspace: 'Backspace',
  tab: 'Tab',
  home: 'Home',
  end: 'End',
  pageup: 'PageUp',
  pagedown: 'PageDown',
  plus: '+',
  equal: '=',
  minus: '-',
};

/** 归一化单个键名 token（修饰键返回修饰名，其余返回规范键名；无法识别返回 null） */
function canonicalToken(raw: string): string | null {
  const token = raw.trim();
  if (!token) return null;
  const lower = token.toLowerCase();
  const modifier = MODIFIER_ALIASES[lower];
  if (modifier) return modifier;
  const alias = KEY_ALIASES[lower];
  if (alias) return alias;
  if (/^f([1-9]|1[0-2])$/.test(lower)) return lower.toUpperCase();
  if (token.length === 1) return token.toUpperCase();
  // 其它多字符键名（如 `Insert`）：只做首字母大写归一，保留原义
  return token.charAt(0).toUpperCase() + token.slice(1);
}

/**
 * 解析键位字符串为归一化组合。
 *
 * 支持「仅修饰键」写法（`Alt` / `Ctrl` / `Shift` / `Meta`），用于块选择修饰键条目；
 * 非法输入（空串、只有 `+`、键名无法识别）返回 `null`。
 */
export function parseShortcut(raw: string): ShortcutChord | null {
  if (typeof raw !== 'string') return null;
  const parts = raw
    .split('+')
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
  if (parts.length === 0) return null;

  const chord: ShortcutChord = { ctrl: false, alt: false, shift: false, meta: false, key: '' };
  for (const part of parts) {
    const token = canonicalToken(part);
    if (!token) return null;
    if (token === 'Ctrl') chord.ctrl = true;
    else if (token === 'Alt') chord.alt = true;
    else if (token === 'Shift') chord.shift = true;
    else if (token === 'Meta') chord.meta = true;
    else if (chord.key === '') chord.key = token;
    else return null; // 出现两个非修饰键 → 非法
  }
  if (chord.key === '') {
    // 仅修饰键：归一化成单个修饰名，语义与普通组合键不同（块选择专用）
    const singles = MODIFIER_KEYS.filter((name) =>
      name === 'Ctrl' ? chord.ctrl : name === 'Alt' ? chord.alt : name === 'Shift' ? chord.shift : chord.meta,
    );
    if (singles.length !== 1) return null;
    chord.key = singles[0];
  }
  return chord;
}

/** 归一化输出：修饰键顺序固定为 Ctrl+Alt+Shift+Meta，键名已归一 */
export function formatShortcut(chord: ShortcutChord): string {
  // **仅修饰键**的 chord（块选择修饰键专用，形如 `{alt:true, key:'Alt'}`）：
  // 它的键名本身就是那个修饰键，若再按修饰键输出一次就会得到 `Alt+Alt` —— 那既让
  // `normalizeShortcut` 不幂等（`normalizeShortcut('Alt') !== 'Alt'`），又会被 store 的
  // 修饰键白名单拒收，导致第 12 项**永远改不了键**并把伪 override 写进 shortcuts.json。
  // 故此处对"与 key 同名的那个修饰位"去重（非修饰键的 key 永不等于修饰名，故无副作用）。
  const keyIsModifier = (MODIFIER_KEYS as readonly string[]).includes(chord.key);
  const parts: string[] = [];
  if (chord.ctrl && !(keyIsModifier && chord.key === 'Ctrl')) parts.push('Ctrl');
  if (chord.alt && !(keyIsModifier && chord.key === 'Alt')) parts.push('Alt');
  if (chord.shift && !(keyIsModifier && chord.key === 'Shift')) parts.push('Shift');
  if (chord.meta && !(keyIsModifier && chord.key === 'Meta')) parts.push('Meta');
  parts.push(chord.key);
  return parts.join('+');
}

/**
 * 归一化键位字符串（AC-9 的判据实现）。
 *
 * 规则（逐条对齐 §4.2.2 的「冲突检测」行）：`Ctrl`/`Control` 统一、修饰键顺序固定、
 * 键名大小写归一（`→` 与 `ArrowRight` 视为同一键）。非法输入返回 `null`。
 */
export function normalizeShortcut(raw: string): string | null {
  const chord = parseShortcut(raw);
  return chord ? formatShortcut(chord) : null;
}

/** 判断键位字符串是否合法（设置页输入框即时校验用） */
export function isValidShortcut(raw: string): boolean {
  return normalizeShortcut(raw) !== null;
}

/** 是否为「仅修饰键」写法（块选择修饰键专用） */
export function isModifierOnlyShortcut(raw: string): boolean {
  const chord = parseShortcut(raw);
  if (!chord) return false;
  return MODIFIER_KEYS.some((name) => name === chord.key);
}

/** 方向键的展示符号（设计表里写作 `→` / `←`） */
const KEY_SYMBOLS: Readonly<Record<string, string>> = {
  Right: '→',
  Left: '←',
  Up: '↑',
  Down: '↓',
};

/**
 * 展示用格式化：把方向键名换成符号（`Ctrl+Alt+ArrowRight` → `Ctrl+Alt+→`），
 * 与设计表里书写的形态一致；**保存**的键位字符串不受影响（仍然是归一名）。
 */
export function formatShortcutForDisplay(raw: string): string {
  const chord = parseShortcut(raw);
  if (!chord) return raw;
  const symbol = KEY_SYMBOLS[chord.key];
  if (symbol) chord.key = symbol;
  return formatShortcut(chord);
}

/**
 * 把键盘事件转成归一化键位字符串（**唯一的事件入口，纯函数**）。
 *
 * 组件在 `window` 捕获阶段拿到事件后调它，再与 `getBinding(id)` 的归一化结果比对 ——
 * 保证「匹配」与「冲突检测」用的是同一套归一化规则，不会出现"配了但匹配不上"。
 */
export function keyboardEventToShortcut(ev: KeyboardEvent): string | null {
  const key = canonicalToken(ev.key);
  if (!key) return null;
  return formatShortcut({
    ctrl: ev.ctrlKey,
    alt: ev.altKey,
    shift: ev.shiftKey,
    meta: ev.metaKey,
    key,
  });
}

/** 按 id 取条目（未知 id 返回 `null`） */
export function getShortcutDef(id: string): ShortcutDef | null {
  return SHORTCUT_DEFS.find((def) => def.id === id) ?? null;
}

/** 某个功能 id 是否要求「仅修饰键」的值 */
export function requiresModifierOnly(id: string): boolean {
  return getShortcutDef(id)?.meta.modifierOnly ?? false;
}

/** 冲突项：同一归一化键位被多个功能占用（AC-9：重复即拒绝保存，**绝不静默覆盖**） */
export interface ShortcutConflict {
  /** 归一化后的键位（展示时用 `formatShortcutForDisplay`） */
  key: string;
  /** 占用该键位的功能 id（≥ 2 个） */
  ids: string[];
  /** 该键位是否同时被编辑器内核独占（§4.2.3 ③，提示文案要说明） */
  reservedByEditor: boolean;
}

/**
 * 全表冲突检测（§4.2.2 / AC-9）。
 *
 * 入参是**生效键位**映射（`id` → 键位，含默认值与用户覆盖值）；返回空数组 = 无冲突。
 * 非法键位单独成组（`key` 为原字符串）也算冲突，避免"配了个按不出来的键"蒙混过关。
 */
export function findConflictingShortcuts(bindings: Record<string, string>): ShortcutConflict[] {
  const grouped = new Map<string, string[]>();
  Object.keys(bindings).forEach((id) => {
    if (!getShortcutDef(id)) return; // 未知 id（旧版残留）不参与冲突
    const raw = bindings[id];
    const normalized = normalizeShortcut(raw);
    const bucketKey = normalized ?? `非法键位:${raw}`;
    const bucket = grouped.get(bucketKey);
    if (bucket) bucket.push(id);
    else grouped.set(bucketKey, [id]);
  });

  const conflicts: ShortcutConflict[] = [];
  grouped.forEach((ids, key) => {
    if (ids.length > 1) {
      conflicts.push({ key, ids, reservedByEditor: EDITOR_KEYMAP_RESERVED.includes(key) });
    }
  });
  return conflicts;
}

/**
 * 把「覆盖值」整理成持久化用的 overrides 映射（§4.2.4：**只存与默认值不同的项**）。
 *
 * 值一律以归一化形式落盘；等于默认值（归一化后相同）的项被剔除。
 */
export function toOverrides(bindings: Record<string, string>): Record<string, string> {
  const overrides: Record<string, string> = {};
  SHORTCUT_DEFS.forEach((def) => {
    const raw = bindings[def.id];
    if (raw === undefined) return;
    const normalized = normalizeShortcut(raw);
    if (!normalized) return;
    const defaultNormalized = def.meta.normalizedDefault;
    if (normalized !== defaultNormalized) overrides[def.id] = normalized;
  });
  return overrides;
}

/** 默认值 + overrides → 生效键位映射（启动加载与冲突检测共用） */
export function withOverrides(overrides: Record<string, string>): Record<string, string> {
  const bindings: Record<string, string> = { ...DEFAULT_BINDINGS };
  Object.keys(overrides).forEach((id) => {
    // 只接受已知 id 与合法键位；旧版残留 / 非法值一律忽略并回默认（§4.2.4，不阻塞启动）
    if (!getShortcutDef(id)) return;
    const normalized = normalizeShortcut(overrides[id]);
    if (normalized) bindings[id] = normalized;
  });
  return bindings;
}

/** 默认键位表的一致性自测元数据：`normalizedDefault` 必须等于 `normalizeShortcut(defaultKey)` */
export function defaultNormalizationConsistent(): boolean {
  return SHORTCUT_DEFS.every((def) => normalizeShortcut(def.defaultKey) === def.meta.normalizedDefault);
}

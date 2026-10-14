/**
 * AC-13 列选择的「Shift 扩展」路径 —— 键位实况盘点 + L-20 缺口登记
 *
 * 判据出处
 * - 需求 `文档记录/需求文档/v1.6.0.md:624`（AC-13 原文样本）：
 *   「修饰键 + `Shift` + `↑`/`↓` **扩行**；`Shift` + `←`/`→` **移动列区间**，复制口径与 AC-11 相同」。
 * - 审查报告 `文档记录/代码审查报告/v1.6.0.md` L-20 [轻微]：
 *   「A4 / CM6 bundle 缺 `Shift-Alt-ArrowLeft/Right` 绑定 ⇒ 横向列移动无热键（**AC-13 未通过的直接原因**）」。
 *
 * 被测真实对象：`ui/public/vendor/editor.bundle.js`（真 CM6 6.43.14 + merge + formatters）经
 * `loadCmBundle()` 求值后的**真实键位表**（`modules.commands.defaultKeymap` 等）；
 * 键位用 `{key, shift}` 属性形式（`{key:'ArrowUp', run: cursorLineUp, shift: selectLineUp}`），
 * 故本文件把「带 `shift` 函数的绑定」展开成 `Shift-<key>` 再与 AC 里的写法对齐。
 *
 * 无头局限（审查报告 §8 / R-5）：真机上「Shift+拖拽/按键把矩形选区横向扩一列」的**几何结果**
 * 依赖布局测量，jsdom 无布局 ⇒ 本文件只断言键位表与 `eventFilter` 契约，几何结果留**真机复核**。
 *
 * ## BL-32 快速修复（2026-10-14）后的定位
 *
 * AC-13 的键盘路径**已由宿主补上**：`ScratchEditorHost.tsx` 用
 * `Prec.highest(keymap.of([...]))` 安装 `修饰键 + Shift + ↑/↓/←/→`（几何纯函数在
 * `utils/scratch/block-select.ts`，用例见 `block-select-keys-host.test.ts`）。
 * 本文件因此**只**保留"离线包自身不提供列命令"这一事实的登记（键位仍被
 * `copyLineUp/Down` 与 `selectSyntaxLeft/Right` 占用）—— 宿主靠更高优先级抢先命中，
 * 不需要、也不应该去改离线包。
 */
import { describe, expect, it } from 'vitest';
import type { KeyBinding } from '../../../types/codemirror-bundle';
import { loadCmBundle } from '../../../test/cm-bundle';

/** 展开后的生效键位（`Mod` 归一到 `Ctrl`，`shift` 函数展开为 `Shift-` 前缀） */
interface FlatBinding {
  chord: string;
  command: string | null;
  mac: string | null;
}

/** AC-13 里写作 `Shift-Alt-ArrowLeft`；探针实测该组合的绑定是 `{key:'Alt-ArrowLeft', shift}` ⇒ 命中本名 */
const SHIFT_ALT_LEFT = 'Shift-Alt-ArrowLeft';
const SHIFT_ALT_RIGHT = 'Shift-Alt-ArrowRight';
const SHIFT_ALT_UP = 'Shift-Alt-ArrowUp';
const SHIFT_ALT_DOWN = 'Shift-Alt-ArrowDown';
const ALT_LEFT = 'Alt-ArrowLeft';

/** 防御式取键位表（手写类型里 `modules` 是索引签名，成员类型可能不是数组） */
function asKeymap(value: unknown): readonly KeyBinding[] {
  return Array.isArray(value) ? (value as readonly KeyBinding[]) : [];
}

/**
 * 把 CM6 键名归一成固定写法：修饰键按 `Shift / Ctrl / Alt / Meta` 排序 + 键名。
 *
 * 目的：`{key:'Alt-ArrowLeft', shift: selectSyntaxLeft}` 展开后写作 `Shift-Alt-ArrowLeft`，
 * 与需求 AC-13 的写法一致；`Mod` 在 Windows/Linux 即 `Ctrl`（mac 侧另有 `mac` 字段）。
 */
function canonicalChord(raw: string): string {
  const tokens = raw.split('-');
  const key = tokens.pop() ?? '';
  const mods = tokens.map((token) => (token === 'Mod' ? 'Ctrl' : token));
  const order = ['Shift', 'Ctrl', 'Alt', 'Meta'];
  const sorted = order.filter((name) => mods.includes(name)).concat(mods.filter((name) => !order.includes(name)));
  return [...sorted, key].join('-');
}

/** 全部 8 张键位表（含 search / fold / autocomplete，避免漏看某个区域） */
function allKeymaps(api: ReturnType<typeof loadCmBundle>): { tag: string; bindings: readonly KeyBinding[] }[] {
  const commands = api.modules.commands as unknown as Record<string, unknown>;
  const search = api.modules.search as unknown as Record<string, unknown>;
  const language = api.modules.language as unknown as Record<string, unknown>;
  const autocomplete = api.modules.autocomplete as unknown as Record<string, unknown>;
  return [
    { tag: 'commands.defaultKeymap', bindings: asKeymap(commands.defaultKeymap) },
    { tag: 'commands.standardKeymap', bindings: asKeymap(commands.standardKeymap) },
    { tag: 'commands.historyKeymap', bindings: asKeymap(commands.historyKeymap) },
    { tag: 'commands.emacsStyleKeymap', bindings: asKeymap(commands.emacsStyleKeymap) },
    { tag: 'search.searchKeymap', bindings: asKeymap(search.searchKeymap) },
    { tag: 'language.foldKeymap', bindings: asKeymap(language.foldKeymap) },
    { tag: 'autocomplete.completionKeymap', bindings: asKeymap(autocomplete.completionKeymap) },
    { tag: 'autocomplete.closeBracketsKeymap', bindings: asKeymap(autocomplete.closeBracketsKeymap) },
  ];
}

/** 展开成生效键位（带来源标签，便于失败时定位） */
function flatten(api: ReturnType<typeof loadCmBundle>): (FlatBinding & { tag: string })[] {
  const commands = api.modules.commands as unknown as Record<string, unknown>;
  const nameOf = (fn: unknown): string | null =>
    typeof fn === 'function' ? (Object.keys(commands).find((name) => commands[name] === fn) ?? null) : null;

  const out: (FlatBinding & { tag: string })[] = [];
  for (const { tag, bindings } of allKeymaps(api)) {
    for (const binding of bindings) {
      if (!binding.key) continue;
      out.push({ tag, chord: canonicalChord(binding.key), command: nameOf(binding.run), mac: binding.mac ?? null });
      if (typeof binding.shift === 'function') {
        out.push({ tag, chord: canonicalChord(`Shift-${binding.key}`), command: nameOf(binding.shift), mac: null });
      }
    }
  }
  return out;
}

describe('AC-13 列选择扩展键位（真 CM6 键位表）', () => {
  it('扩行键位存在：`Shift-ArrowUp/Down` 绑定到 selectLineUp / selectLineDown', () => {
    const api = loadCmBundle();
    const flat = flatten(api);
    const up = flat.filter((item) => item.chord === 'Shift-ArrowUp');
    const down = flat.filter((item) => item.chord === 'Shift-ArrowDown');

    expect(up.length).toBeGreaterThan(0);
    expect(down.length).toBeGreaterThan(0);
    expect(up.map((item) => item.command)).toContain('selectLineUp');
    expect(down.map((item) => item.command)).toContain('selectLineDown');
    // 对应命令确为真函数（不是占位）
    const commands = api.modules.commands as unknown as Record<string, unknown>;
    expect(typeof commands.selectLineUp).toBe('function');
    expect(typeof commands.selectLineDown).toBe('function');
  });

  it('实况记录：`Shift-Alt-ArrowLeft/Right` 已被「语法选择」占用，且全包无列移动命令', () => {
    const api = loadCmBundle();
    const flat = flatten(api);

    // Windows/Linux 上 `Alt-ArrowLeft` 的 shift 侧是 selectSyntaxLeft（mac 侧写作 Ctrl-ArrowLeft）
    const left = flat.find((item) => item.chord === SHIFT_ALT_LEFT);
    const right = flat.find((item) => item.chord === SHIFT_ALT_RIGHT);
    expect(left?.command).toBe('selectSyntaxLeft');
    expect(right?.command).toBe('selectSyntaxRight');
    expect(flat.find((item) => item.chord === ALT_LEFT)?.mac).toBe('Ctrl-ArrowLeft');

    // 竖向的 Shift-Alt 组合也在，但语义是「整行复制」，不是列区间
    expect(flat.find((item) => item.chord === SHIFT_ALT_UP)?.command).toBe('copyLineUp');
    expect(flat.find((item) => item.chord === SHIFT_ALT_DOWN)?.command).toBe('copyLineDown');

    // 整包不存在任何「矩形 / 列移动」类命令 ⇒ 横向列移动无从绑定
    const commands = api.modules.commands as unknown as Record<string, unknown>;
    expect(Object.keys(commands).filter((name) => /rect|column/i.test(name))).toEqual([]);
  });

  it('`rectangularSelection({ eventFilter })` 是真实可用的配置项，矩形多选依赖 allowMultipleSelections', () => {
    const api = loadCmBundle();
    expect(typeof api.extensions.rectangularSelection).toBe('function');

    // 配置项被接受且能进真 EditorState（过滤器本体由 `ScratchEditorHost` 按 store 派生值提供）
    const extension = api.extensions.rectangularSelection({ eventFilter: (event: MouseEvent) => event.altKey });
    expect(extension).toBeTruthy();
    const state = api.EditorState.create({
      doc: 'abcdef\ngh\nijklmn',
      extensions: [api.EditorState.allowMultipleSelections.of(true), extension],
    });
    expect(state.doc.lines).toBe(3);
    // 矩形选择 = 多个 range；CM6 要求打开该 facet，否则多 range 会被拒（AC-11/AC-13 的共同前提）
    expect(state.facet(api.EditorState.allowMultipleSelections)).toBe(true);

    // 无头局限（审查报告 §8 / R-5）：mousedown 确实会走到 eventFilter（探针实测 1 次/事件），
    // 但 jsdom 的 `Range.getClientRects` 不存在 ⇒ CM6 的坐标测量路径会抛
    // `TypeError: ... .getClientRects is not a function`；因此「拖拽/Shift 拖拽后的列区间几何结果」
    // 只做真机复核，本文件不伪造测量替身。
  });

  it.fails('离线包自身不提供列命令（L-20 记录；宿主侧已由 BL-32 修复补上键位）', () => {
    // 需求 `:624` 要求「Shift + ←/→ 移动列区间」；离线包里该组合被 selectSyntaxLeft / Right 占用，
    // 且不存在矩形/列移动命令。**宿主侧**已用 `Prec.highest(keymap…)` 抢先命中（BL-32 快速修复），
    // 本条只登记"离线包不提供"，不构成缺陷；保留 it.fails 以固定事实、防止离线包换版后语义漂移。
    const api = loadCmBundle();
    const flat = flatten(api);
    const left = flat.find((item) => item.chord === SHIFT_ALT_LEFT);
    const right = flat.find((item) => item.chord === SHIFT_ALT_RIGHT);

    expect(left, '`Shift-Alt-ArrowLeft` 应有绑定').toBeTruthy();
    expect(right, '`Shift-Alt-ArrowRight` 应有绑定').toBeTruthy();
    expect(left?.command ?? '', '`Shift-Alt-ArrowLeft` 应指向矩形/列移动命令').toMatch(/rect|column/i);
    expect(right?.command ?? '', '`Shift-Alt-ArrowRight` 应指向矩形/列移动命令').toMatch(/rect|column/i);
  });
});

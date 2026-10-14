/**
 * AC-13 / BL-32：块选择的**键盘几何**（宿主侧纯函数，真离线包）
 *
 * ## 这条用例修的是什么
 *
 * Phase 6 真机复核（`poc/editor-probes/r11-real-gui-drive.py` 的 H3）实测：
 * 宿主装了 `rectangularSelection`（只管鼠标）却**没有**任何键盘列命令，
 * `Alt+Shift+↓` 因此落到 CM6 默认键位的 `copyLineDown` —— 实测每按一次
 * **复制 2 行**（`docLengths [100,100,100,100,100,150,200]`），
 * 且改动会在 1.5 s 内被自动保存**写回原文件**（H3b 的数据破坏证据）。
 *
 * 修复 = `block-select.ts` 新增五个纯函数 + 宿主用 `Prec.highest(keymap...)` 抢键位。
 * 本文件钉住**纯函数**的口径；**宿主接线**（`Prec.highest` 是否真抢过 `copyLineDown`、
 * `WeakMap` 续锚、`selection.create(ranges, mainIndex)`）由
 * `src/components/scratch/__tests__/scratch-block-select-keymap.test.ts` 用**真 `keymap` facet** 覆盖
 * （审查 M3 指出：本文件原先声称的 `scratch-editor-host-*` 那组用例并不存在）。
 *
 * ## 本文件覆盖的纯函数判据
 *
 * 1. `blockSelectChord` 的键名与「只认一个修饰键」的退化规则（AC-12 口径）；
 * 2. 光标种子 → 键盘序列 → **每行一个 range** → `blockSelectCopy` 得到 AC-11 的同一个串；
 * 3. 短行被行尾夹短（真机实测与 CM6 矩形选择一致）仍能补齐空格；
 * 4. 四个方向的边界夹取（文档首尾 / 行首列 0 / 行尾）；
 * 5. 非矩形选区退化为「主选区头起一列」。
 *
 * 真内核：`src/test/cm-bundle.ts` 注入 `public/vendor/editor.bundle.js`（不是替身），
 * 因为 `EditorSelection.range/create` 是 CM6 的真实语义（多 range 必须带 anchor/head）。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { loadCmBundle } from '../../../test/cm-bundle';
import type { EditorSelection, JFLoveCmApi, SelectionRange, Text } from '../../../types/codemirror-bundle';
import {
  BLOCK_SELECT_AC11,
  BLOCK_SELECT_AC13,
  BLOCK_DIRECTIONS,
  blockCursorFromSelection,
  blockCursorMatchesSelection,
  blockCursorRanges,
  blockSelectChord,
  blockSelectCopy,
  clampBlockCursor,
  extendBlockCursor,
  isBlockSelection,
} from '../block-select';
import type { BlockCursor, BlockDirection } from '../block-select';

/** 离线包里的 `EditorSelection`（`modules.state` 在手写类型里是宽松索引签名） */
interface EditorSelectionStatic {
  range(from: number, to: number): SelectionRange;
  create(ranges: readonly SelectionRange[], mainIndex?: number): EditorSelection;
}

let api: JFLoveCmApi;
let selectionApi: EditorSelectionStatic;
let doc: Text;

beforeEach(() => {
  api = loadCmBundle();
  selectionApi = (api.modules.state as unknown as { EditorSelection: EditorSelectionStatic })
    .EditorSelection;
  // AC-11 / AC-13 共用同一份文档：三行，中间行只有 2 个字符（用来验"行尾夹短 + 补空格"）
  doc = api.EditorState.create({ doc: BLOCK_SELECT_AC11.doc }).doc;
});

/** 造一个「光标」选区（anchor == head） */
function caretAt(pos: number): EditorSelection {
  return selectionApi.create([selectionApi.range(pos, pos)]);
}

/** 把 range 列表折成便于断言的二维数组 */
function spans(selection: EditorSelection): number[][] {
  return selection.ranges.map((range) => [range.from, range.to]);
}

describe('blockSelectChord：键名与修饰键退化（AC-12 / AC-13）', () => {
  it('Ctrl ⇒ Ctrl-Shift-<Arrow>，其余修饰键一律退化为 Alt', () => {
    expect(blockSelectChord('Ctrl', 'down')).toBe('Ctrl-Shift-ArrowDown');
    expect(blockSelectChord('Ctrl', 'left')).toBe('Ctrl-Shift-ArrowLeft');
    // Shift / Meta 在设置界面可选，但几何只认一个修饰键（与 blockSelectFilter 同源）
    expect(blockSelectChord('Shift', 'right')).toBe('Alt-Shift-ArrowRight');
    expect(blockSelectChord('Meta', 'up')).toBe('Alt-Shift-ArrowUp');
  });

  it('四个方向都有键名，且方向表与键名一一对应', () => {
    const chords = BLOCK_DIRECTIONS.map((direction: BlockDirection) =>
      blockSelectChord('Alt', direction),
    );
    expect(chords).toEqual([
      'Alt-Shift-ArrowUp',
      'Alt-Shift-ArrowDown',
      'Alt-Shift-ArrowLeft',
      'Alt-Shift-ArrowRight',
    ]);
  });
});

describe('blockCursorFromSelection：种子（AC-13 的起点）', () => {
  it('普通光标 ⇒ 1 列宽的块（行 = 光标行，列 = 光标列）', () => {
    const cursor = blockCursorFromSelection(doc, caretAt(1));
    expect(cursor).toEqual({ startLine: 1, endLine: 1, leftCol: 1, rightCol: 1 });
  });

  it('已是矩形选区 ⇒ 复用它的矩形（不丢上一步的列范围）', () => {
    const rect = [
      selectionApi.range(1, 4),
      selectionApi.range(8, 9),
      selectionApi.range(11, 14),
    ];
    const cursor = blockCursorFromSelection(doc, selectionApi.create(rect));
    expect(cursor).toEqual({ startLine: 1, endLine: 3, leftCol: 1, rightCol: 3 });
  });

  it('非矩形的多 range ⇒ 退化为「主选区头起一列」', () => {
    // 第 1 行 col 0 与第 3 行 col 0 同行号不连续 ⇒ 不是矩形
    const notRect = [selectionApi.range(0, 3), selectionApi.range(10, 13)];
    const cursor = blockCursorFromSelection(doc, selectionApi.create(notRect, 1));
    expect(cursor).toEqual({ startLine: 3, endLine: 3, leftCol: 3, rightCol: 3 });
  });
});

describe('extendBlockCursor：四个方向的移动与夹取', () => {
  it('↓ 扩下端、↑ 扩上端（不越文档边界）', () => {
    const seed = blockCursorFromSelection(doc, caretAt(1))!;
    expect(extendBlockCursor(doc, seed, 'down').endLine).toBe(2);
    expect(extendBlockCursor(doc, seed, 'up')).toEqual(seed); // 已在首行 ⇒ 不动
    const twoLines = extendBlockCursor(doc, extendBlockCursor(doc, seed, 'down'), 'down');
    expect(twoLines.endLine).toBe(3);
    expect(extendBlockCursor(doc, twoLines, 'down')).toEqual(twoLines); // 已在末行 ⇒ 不动
  });

  it('←→ 移动列区间的一端（左端不越 0，右端不越块内最长行的行尾）', () => {
    const seed = blockCursorFromSelection(doc, caretAt(1))!;
    expect(extendBlockCursor(doc, seed, 'right').rightCol).toBe(2);
    // 单行块 ⇒ 上限取该行（`abcdef` 长 6）⇒ 右端最多推到 col 5
    let cursor = seed;
    for (let i = 0; i < 20; i += 1) cursor = extendBlockCursor(doc, cursor, 'right');
    expect(cursor.rightCol).toBe(6 - 1);
    // 左端最多退到 col 0
    let back = cursor;
    for (let i = 0; i < 20; i += 1) back = extendBlockCursor(doc, back, 'left');
    expect(back.leftCol).toBe(0);
  });

  it('审查 M2：右端上限取「块内最长行」，末行更短时不得把左端一起下钳', () => {
    // 块 = 第 1~2 行（`abcdef` 6 字符 / `gh` 2 字符），列区间 {leftCol: 3, rightCol: 4}
    const cursor = { startLine: 1, endLine: 2, leftCol: 3, rightCol: 4 };
    const next = extendBlockCursor(doc, cursor, 'right');
    // 修复前：cap 取末行 `gh`（长 2）⇒ cap = 1 ⇒ 返回 {leftCol:1,rightCol:2}
    //        —— 一次按键让整个块"左移并收缩"
    expect(next.leftCol).toBe(3);
    expect(next.rightCol).toBe(5); // cap = max(6,2)-1 = 5
    // 已到上限后不再增长，也绝不下钳
    const saturated = extendBlockCursor(doc, { ...cursor, rightCol: 5 }, 'right');
    expect(saturated).toEqual({ ...cursor, rightCol: 5 });
  });
});

describe('clampBlockCursor：陈旧锚点不得让 doc.line 抛（审查 M1）', () => {
  it('行号越界时夹回文档范围（文档变短后仍可安全调用）', () => {
    const shortDoc = api.EditorState.create({ doc: 'abcdef\ngh' }).doc; // 2 行
    const stale = { startLine: 2, endLine: 7, leftCol: 1, rightCol: 3 }; // 记忆里是 7 行文档
    expect(clampBlockCursor(shortDoc, stale)).toEqual({
      startLine: 2,
      endLine: 2,
      leftCol: 1,
      rightCol: 3,
    });
    // 直接喂陈旧锚点也不抛（修复前 doc.line(3) 会抛 RangeError）
    expect(() => blockCursorRanges(shortDoc, stale)).not.toThrow();
    // 行数变少 ⇒ range 个数不等 ⇒ 记忆失效，自动以当前选区重新起锚
    expect(blockCursorMatchesSelection(shortDoc, stale, [selectionApi.range(7, 9)])).toBe(false);
  });

  it('startLine / endLine 倒置时归一（不产出空循环）', () => {
    expect(clampBlockCursor(doc, { startLine: 3, endLine: 1, leftCol: 0, rightCol: 1 })).toEqual({
      startLine: 3,
      endLine: 3,
      leftCol: 0,
      rightCol: 1,
    });
  });
});

describe('AC-13：键盘序列产出 AC-11 的同一个剪贴板串', () => {
  it('键盘 →×2 + ↓×2 后，选区是 3 行各一个 range，复制结果逐字符等于 `bcd\\nh  \\njkl`', () => {
    // 起点：AC-13 常量约定的 (行 1, 列 1) —— 与鼠标拖拽的矩形成立条件完全一致
    const seedPos = doc.line(BLOCK_SELECT_AC13.seedLine).from + BLOCK_SELECT_AC13.seedCol;
    let selection = caretAt(seedPos);
    // 与宿主 keymap 逐字一致的循环：命中记忆的锚点就继续扩展，否则以选区重新起锚
    let remembered: BlockCursor | null = null;

    for (const direction of BLOCK_SELECT_AC13.keys) {
      const base = blockCursorMatchesSelection(doc, remembered, selection.ranges)
        ? remembered
        : blockCursorFromSelection(doc, selection);
      expect(base).not.toBeNull();
      const cursor = extendBlockCursor(doc, base!, direction);
      const ranges = blockCursorRanges(doc, cursor).map((item) =>
        selectionApi.range(item.from, item.to),
      );
      const mainIndex = direction === 'up' ? 0 : ranges.length - 1;
      selection = selectionApi.create(ranges, mainIndex);
      remembered = cursor;
    }

    // 每行一个 range（CM6 的矩形选择语义；不是单区间）
    expect(spans(selection)).toEqual([
      [1, 4],
      [8, 9], // 中间行 `gh` 只有 2 字符 ⇒ 被行尾夹短（补空格交给 blockSelectCopy）
      [11, 14],
    ]);
    // 复制处理器认得这是块选择（否则根本不会接管 Ctrl+C）
    expect(isBlockSelection(doc, selection.ranges)).toBe(true);
    // 与 AC-11 完全同一拼接口径
    expect(blockSelectCopy(doc, selection.ranges)).toBe(BLOCK_SELECT_AC13.expected);
    expect(BLOCK_SELECT_AC13.expected).toBe(BLOCK_SELECT_AC11.expected);
  });

  it('只按下 ↓ 时得到一个单列块（选区仍是块，可继续 ←→ 调宽）', () => {
    const seed = blockCursorFromSelection(doc, caretAt(1))!;
    const oneDown = extendBlockCursor(doc, seed, 'down');
    const ranges = blockCursorRanges(doc, oneDown).map((item) =>
      selectionApi.range(item.from, item.to),
    );
    expect(spans(selectionApi.create(ranges, 0))).toEqual([
      [1, 2],
      [8, 9], // 中间行只有 1 个字符 ⇒ 行尾夹短
    ]);
    expect(blockSelectCopy(doc, selectionApi.create(ranges, 0).ranges)).toBe('b\nh');
  });
});

describe('blockCursorMatchesSelection：锚点记忆的命中与失效', () => {
  it('单行块（1 个 range）也能命中记忆 —— 否则「先加宽再扩行」会丢列区间', () => {
    // 起点 {1,1,1,1} 按一次 →

    const seed = blockCursorFromSelection(doc, caretAt(1))!;
    const widened = extendBlockCursor(doc, seed, 'right'); // {1,1,1,2}
    const afterPress = selectionApi.create([selectionApi.range(1, 3)]);
    expect(blockCursorMatchesSelection(doc, widened, afterPress.ranges)).toBe(true);
    // 反例：光标挪到别处 ⇒ 记忆失效（重新起锚）
    expect(blockCursorMatchesSelection(doc, widened, caretAt(11).ranges)).toBe(false);
    expect(blockCursorMatchesSelection(doc, null, afterPress.ranges)).toBe(false);
  });

  it('foreign 选区（鼠标点走）后按 ↓，从新光标起一列，而不是延续旧锚点', () => {
    const remembered = { startLine: 1, endLine: 1, leftCol: 1, rightCol: 3 };
    const foreign = caretAt(doc.line(3).from + 2); // 第 3 行第 3 列
    const base = blockCursorMatchesSelection(doc, remembered, foreign.ranges)
      ? remembered
      : blockCursorFromSelection(doc, foreign);
    expect(base).toEqual({ startLine: 3, endLine: 3, leftCol: 2, rightCol: 2 });
    expect(extendBlockCursor(doc, base!, 'up')).toEqual({
      startLine: 2,
      endLine: 3,
      leftCol: 2,
      rightCol: 2,
    });
  });
});

describe('blockCursorRanges：行内夹取', () => {
  it('列区间超出短行时逐行钳到行尾（空行得到空 range）', () => {
    const withEmpty = api.EditorState.create({ doc: 'abcdef\n\nijklmn' }).doc;
    const cursor = { startLine: 1, endLine: 3, leftCol: 2, rightCol: 5 };
    expect(blockCursorRanges(withEmpty, cursor)).toEqual([
      { from: 2, to: 6 },
      { from: 7, to: 7 }, // 空行 ⇒ `from === to`
      { from: 10, to: 14 },
    ]);
  });

  it('左端大于右端（非法锚点）时返回空数组，不做静默放大（审查 L8）', () => {
    // 修复前：`min/max` 归一 ⇒ `{3,6}` 被放大成 `{3,5}` 区间（对比"拒绝"更隐蔽）
    const cursor = { startLine: 1, endLine: 1, leftCol: 5, rightCol: 2 };
    expect(blockCursorRanges(doc, cursor)).toEqual([]);
  });
});

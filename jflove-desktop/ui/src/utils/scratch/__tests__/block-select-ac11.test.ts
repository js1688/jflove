/**
 * AC-11 块选择（矩形选择）复制口径 —— Phase 6 正向判据与负向对照
 *
 * 判据出处
 * - 需求 `文档记录/需求文档/v1.6.0.md:622`（AC-11 原文样本，S-9 用户裁决后）：
 *   「文档 `abcdef\ngh\nijklmn`，选 1 基第 2~4 列（0 基区间 `[1,4)`、`[8,9)`、`[11,14)`）
 *    ⇒ 剪贴板文本严格等于 `bcd\nh  \njkl`（**短行右侧补空格到块宽**）」。
 * - S-9 [严重，已闭环]：AC-11 原期望串曾写作 `ijk`（实为「取第 1~3 列」的口径），属文档笔误；
 *   用户裁决**以实现与列口径为准**，提交 `a961d09` 已把需求 `:622` 与 `src/utils/scratch/block-select.ts`
 *   的常量同步为 `bcd\nh  \njkl`。本文件同时守住「实现输出 = 常量 = 需求样本」三者一致。
 * - 设计 §6.1.2 拼接口径：逐行取列区间 → 短行右补空格到块宽 → `\n` 连接，**末尾不补换行**。
 *
 * 真实对象来源：`ui/public/vendor/editor.bundle.js`（真 CM6 6.43.14）经 `loadCmBundle()` 求值，
 * 使用真 `Text`（`EditorState.create({doc}).doc`）与真 `SelectionRange`
 * （`modules.state.EditorSelection.range / create`），**不使用替身**。
 */
import { describe, expect, it } from 'vitest';
import type { JFLoveCmApi, SelectionRange } from '../../../types/codemirror-bundle';
import { loadCmBundle } from '../../../test/cm-bundle';
import {
  BLOCK_SELECT_AC11,
  blockSelectCopy,
  isBlockSelection,
  sliceBlockLine,
} from '../block-select';

/** `EditorSelection` 静态侧（离线包手写类型里 `modules` 是索引签名，取成员需自行收窄） */
interface EditorSelectionStatic {
  create(ranges: readonly SelectionRange[]): { ranges: readonly SelectionRange[] };
  range(anchor: number, head?: number): SelectionRange;
}

/** 取真 `EditorSelection`（AC-11 要求用真 range，而不是手搓 `{from,to}` 字面量） */
function selectionOf(api: JFLoveCmApi): EditorSelectionStatic {
  return (api.modules.state as unknown as { EditorSelection: EditorSelectionStatic }).EditorSelection;
}

/** 需求 `:622` 给的 0 基绝对区间（行 1 起算，含行间 `\n` 偏移） */
const AC11_PAIRS = [
  [1, 4],
  [8, 9],
  [11, 14],
] as const;

/** 用绝对位置区间建真矩形选择 */
function rectRanges(api: JFLoveCmApi, pairs: readonly (readonly [number, number])[]): readonly SelectionRange[] {
  const selection = selectionOf(api);
  return selection.create(pairs.map(([from, to]) => selection.range(from, to))).ranges;
}

describe('AC-11 块选择复制口径（真 CM6 Text + 真 SelectionRange）', () => {
  it('主判据：`abcdef\\ngh\\nijklmn` 取 0 基 [1,4)/[8,9)/[11,14) ⇒ `bcd\\nh  \\njkl`', () => {
    const api = loadCmBundle();
    const doc = api.EditorState.create({ doc: BLOCK_SELECT_AC11.doc }).doc;

    // 需求 `:622` 给的区间先自校验：确为「行 1/2/3 的第 1 列起」（1 基第 2 列）
    for (const [from] of AC11_PAIRS) {
      expect(doc.lineAt(from).from).toBeLessThanOrEqual(from);
    }
    expect(AC11_PAIRS.map(([from]) => doc.lineAt(from).number)).toEqual([1, 2, 3]);
    expect(AC11_PAIRS.map(([from]) => from - doc.lineAt(from).from)).toEqual([1, 1, 1]);

    const ranges = rectRanges(api, AC11_PAIRS);
    expect(ranges).toHaveLength(3);
    expect(isBlockSelection(doc, ranges)).toBe(true);

    // 实现输出 = 需求样本 = 源码常量（S-9 裁决后的唯一真相）
    expect(blockSelectCopy(doc, ranges)).toBe('bcd\nh  \njkl');
    expect(blockSelectCopy(doc, ranges)).toBe(BLOCK_SELECT_AC11.expected);
    expect(BLOCK_SELECT_AC11.columns).toEqual([1, 4]);
    expect(BLOCK_SELECT_AC11.width).toBe(3);
  });

  it('短行补空格：每行都补到块宽 3，末尾不补换行', () => {
    const api = loadCmBundle();
    const doc = api.EditorState.create({ doc: BLOCK_SELECT_AC11.doc }).doc;
    const text = blockSelectCopy(doc, rectRanges(api, AC11_PAIRS));

    const lines = text.split('\n');
    expect(lines).toEqual(['bcd', 'h  ', 'jkl']);
    for (const line of lines) expect(line).toHaveLength(BLOCK_SELECT_AC11.width); // 3
    expect(text).toHaveLength(3 * 3 + 2); // 三行三列 + 两个换行，末尾无换行
    expect(text.endsWith('\n')).toBe(false);

    // 补空格只发生在短行右侧：列区间在行内的切片规则（纯函数直测）
    expect(sliceBlockLine('abcdef', 1, 3)).toBe('bcd');
    expect(sliceBlockLine('gh', 1, 3)).toBe('h  ');
    expect(sliceBlockLine('ijklmn', 1, 3)).toBe('jkl');
  });

  it('最小可用块：仅两行也构成矩形（`abcdef\\ngh` 两段）', () => {
    const api = loadCmBundle();
    const doc = api.EditorState.create({ doc: 'abcdef\ngh' }).doc;
    const ranges = rectRanges(api, [
      [1, 4],
      [8, 9],
    ]);
    expect(isBlockSelection(doc, ranges)).toBe(true);
    expect(blockSelectCopy(doc, ranges)).toBe('bcd\nh  ');
  });

  it('复制 → 粘贴后文本与剪贴板逐字符相同（含补出的尾随空格，不被 trim）', () => {
    const api = loadCmBundle();
    const doc = api.EditorState.create({ doc: BLOCK_SELECT_AC11.doc }).doc;
    const copied = blockSelectCopy(doc, rectRanges(api, AC11_PAIRS));

    // jsdom 30 的 `ClipboardEvent` 不是构造函数（审查报告 §8 / R-5 已登记），
    // 故用事件侧 `clipboardData` 的最小替身承载同一段文本。
    const store = new Map<string, string>();
    const clipboard = {
      setData: (type: string, value: string): void => void store.set(type, value),
      getData: (type: string): string => store.get(type) ?? '',
    };
    // 等价 `ScratchEditorHost` 的 `copy` 处理器：判定矩形后写入 `text/plain`
    clipboard.setData('text/plain', copied);

    const pasted = clipboard.getData('text/plain');
    const target = api.EditorState.create({ doc: '' });
    const afterPaste = target.update({ changes: { from: 0, insert: pasted } }).state.doc.toString();

    expect(afterPaste).toBe(pasted);
    expect(Array.from(afterPaste)).toEqual(Array.from(pasted)); // 逐字符
    expect(Array.from(afterPaste)).toEqual(Array.from('bcd\nh  \njkl'));
    expect(afterPaste.split('\n')).toEqual(['bcd', 'h  ', 'jkl']);
    expect(afterPaste.endsWith('jkl')).toBe(true); // 尾随空格保留、末尾不补换行
  });

  it('负向对照：不构成矩形的选择一律返回空串（调用方回落默认复制行为）', () => {
    const api = loadCmBundle();
    const cases: {
      name: string;
      doc: string;
      pairs: readonly (readonly [number, number])[];
    }[] = [
      { name: '单 range ⇒ 普通（流式）选择', doc: 'abcdef', pairs: [[1, 4]] },
      { name: '两 range 但列区间左端不一致', doc: 'abcdef\nghijklm', pairs: [[1, 4], [9, 11]] },
      { name: '跨行 range ⇒ 流式选择', doc: 'abcdef\nghijklm', pairs: [[1, 4], [3, 9]] },
      { name: '行号不连续（1 与 3，非矩形）', doc: 'abcdef\ngh\nijklmn', pairs: [[1, 4], [11, 14]] },
      { name: '含空 range（多光标）⇒ 可用 range 不足 2 段', doc: 'abcdef\ngh\nijklmn', pairs: [[1, 1], [8, 9]] },
      { name: '全部 range 都是空 range（纯多光标，无选区）', doc: 'abcdef\ngh\nijklmn', pairs: [[1, 1], [1, 1]] },
    ];

    for (const item of cases) {
      const doc = api.EditorState.create({ doc: item.doc }).doc;
      const ranges = rectRanges(api, item.pairs);
      expect(isBlockSelection(doc, ranges), item.name).toBe(false);
      expect(blockSelectCopy(doc, ranges), item.name).toBe('');
    }
  });

  it.fails('M-10（中等，未修）：块选择跨越空行时应按零宽列补足空格，实测返回空串', () => {
    // 审查报告 `文档记录/代码审查报告/v1.6.0.md` M-10：
    // 「块选择跨空行时判定为非矩形并返回空串，破坏右补空格口径」——
    // 位置 `ui/src/utils/scratch/block-select.ts:65-67`，建议「空行按『零宽列』处理（补足空格到最大列宽）」。
    // 真实鼠标路径下，空行（长度 0）产生的 range 是**空 range**，会被 `!range.empty` 过滤掉，
    // 于是行号变成 1、2、4 被判「不连续」⇒ 返回 ''，用户以为没复制成功。
    const api = loadCmBundle();
    const doc = api.EditorState.create({ doc: 'abcdef\ngh\n\nijklmn' }).doc;
    const ranges = rectRanges(api, [
      [1, 4], // 行 1：`bcd`
      [8, 9], // 行 2：`h` + 补 2 空格
      [10, 10], // 行 3：空行（真实路径下此处是空 range）
      [12, 15], // 行 4：`jkl`
    ]);
    expect(blockSelectCopy(doc, ranges)).toBe('bcd\nh  \n   \njkl');
  });
});

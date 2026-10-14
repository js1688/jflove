/**
 * 块选择（矩形选择）复制口径 —— **纯函数**（设计 v1.6.0 §6.1.2 / AC-11 ~ AC-13）
 *
 * ## 为什么必须自实现
 *
 * 矩形选择在 CM6 里由 `EditorSelection` 的**多个 range** 表示（每行一个 range）。
 * CM6 内置的 `copy` 对多 range 的处理是「各段直接拼接」，得到的是一串首尾相接的文本，
 * **不是** IDE 的矩形口径（逐行取列区间、短行右侧补空格到块宽、行间 `\n`）。
 * 因此宿主用 `EditorView.domEventHandlers({ copy })` 接管复制，而**拼接口径抽成纯函数放本文件**
 * —— 本文件不 import 任何 CM6 包、不碰 DOM、无副作用，便于 Phase 6 直接单测。
 *
 * ## 拼接口径（逐字对齐设计 §6.1.2）
 *
 * 1. 对 `[列区间] × [行区间]` 的**每一行**，取该行的列区间字符；
 * 2. **短行右侧补空格到块宽**（块宽 = 各 range 列区间的最大右端 − 左端）；
 * 3. 行间以 `\n` 连接；末尾**不补**换行。
 *
 * ## AC-11 手算样例（自检用，也是 Phase 6 的用例基线）
 *
 * 文档 `abcdef\ngh\nijklmn`（无末尾换行），选第 2~4 列：
 *
 * ```
 * 行1 `abcdef`  列1..3（0 基 [1,4)）⇒ `bcd`
 * 行2 `gh`      列1..3 越界，短行右侧补空格到块宽 3 ⇒ `h` + `  ` = `h  `
 * 行3 `ijklmn`  列1..3 ⇒ `ijk`
 * 块宽 = 4 − 1 = 3
 * 剪贴板 = `bcd` + `\n` + `h  ` + `\n` + `ijk` = "bcd\nh  \nijk"
 * ```
 *
 * ## 列的口径（诚实说明）
 *
 * 列 = 行内 **UTF-16 code unit 偏移**（与 CM6 的文档位置口径一致，因为 `from/to` 就是位置）。
 * ASCII 文档下与"字符列"完全一致；含星平面字符时与状态栏的码点口径（§4.1.4）可能相差 1 列。
 * 这一取舍的理由：range 的 `from/to` 是位置而非码点，换算成码点列会引入不可逆的歧义。
 */

import type { SelectionRange, Text } from '../../types/codemirror-bundle';

/** 归一化后的矩形块描述（行号为 **1 基**，列为 **0 基** UTF-16 偏移） */
export interface BlockRect {
  /** 矩形第一行（1 基，含） */
  startLine: number;
  /** 矩形最后一行（1 基，含） */
  endLine: number;
  /** 列区间左端（0 基，含） */
  startCol: number;
  /** 块宽（列数）；短行右侧补空格到这个宽度 */
  width: number;
}

/**
 * 判定多 range 选区是否为**矩形（块）选择**，并归一化成 `BlockRect`。
 *
 * 判据（不满足即返回 `null`，调用方回落到 CM6 默认行为）：
 * 1. 非空 range **至少 2 个**（单个 range 是普通选择，交给默认 copy 即可，口径等价）；
 * 2. 每个 range **都在同一行内**（跨行 range = 流式选择，绝不能按矩形拼）；
 * 3. 各 range 的**列区间左端相同**（矩形每行起点一致；这是"矩形"的判别特征，
 *    因为短行的右端会被 CM6 钳到行尾，右端本来就不一致）；
 * 4. 行号**互不重复且连续**（重复 = 多光标；不连续 = 多光标/多选，不是矩形）。
 */
export function normalizeBlockSelection(
  doc: Text,
  ranges: readonly SelectionRange[],
): BlockRect | null {
  const usable = ranges.filter((range) => !range.empty && range.from !== range.to);
  // ① 单 range ⇒ 普通选择
  if (usable.length < 2) return null;

  const rects: { line: number; startCol: number; endCol: number }[] = [];
  for (const range of usable) {
    const from = Math.min(range.from, range.to);
    const to = Math.max(range.from, range.to);
    const startAt = doc.lineAt(from);
    const endAt = doc.lineAt(to);
    // ② 跨行 range ⇒ 流式选择
    if (startAt.number !== endAt.number) return null;
    rects.push({ line: startAt.number, startCol: from - startAt.from, endCol: to - startAt.from });
  }

  // ③ 列区间左端必须一致
  const startCol = rects[0].startCol;
  if (!rects.every((item) => item.startCol === startCol)) return null;

  // ④ 行号不重复且连续
  const lines = rects.map((item) => item.line).sort((a, b) => a - b);
  const seen = new Set(lines);
  if (seen.size !== lines.length) return null;
  for (let i = 1; i < lines.length; i += 1) {
    if (lines[i] !== lines[i - 1] + 1) return null;
  }

  const width = Math.max(...rects.map((item) => item.endCol)) - startCol;
  if (width <= 0) return null;

  return { startLine: lines[0], endLine: lines[lines.length - 1], startCol, width };
}

/** 是否为矩形选择（宿主 copy 处理器先问它，避免误接管普通多 range 复制） */
export function isBlockSelection(doc: Text, ranges: readonly SelectionRange[]): boolean {
  return normalizeBlockSelection(doc, ranges) !== null;
}

/**
 * 取一行内 `[startCol, startCol + width)` 的字符；**不足块宽时右侧补空格**。
 *
 * 补空格是 IDE 口径的硬性要求（AC-11 的第二行 `h  `）：
 * 短行右侧的"虚拟空白"必须显式落成空格，否则粘贴后列会错位。
 */
export function sliceBlockLine(text: string, startCol: number, width: number): string {
  const slice = text.slice(startCol, startCol + width);
  if (slice.length >= width) return slice;
  return slice + ' '.repeat(width - slice.length);
}

/**
 * 矩形选择的剪贴板文本（**AC-11 的唯一判据函数**）。
 *
 * 返回 `''` 表示"不是矩形选择 / 空选择" —— 调用方（`copy` 处理器）见此应**回落**到
 * CM6 默认行为（`return false`），绝不写空串到剪贴板。
 */
export function blockSelectCopy(doc: Text, ranges: readonly SelectionRange[]): string {
  const rect = normalizeBlockSelection(doc, ranges);
  if (!rect) return '';
  const rows: string[] = [];
  for (let lineNo = rect.startLine; lineNo <= rect.endLine; lineNo += 1) {
    rows.push(sliceBlockLine(doc.line(lineNo).text, rect.startCol, rect.width));
  }
  return rows.join('\n');
}

/**
 * AC-11 的手算样例（供 Phase 6 单测与人工复核直接引用，避免各处重抄）。
 *
 * `expected` 的第二行末尾是**两个空格**：`"bcd\nh  \nijk"`。
 */
export const BLOCK_SELECT_AC11 = {
  /** 文档（无末尾换行） */
  doc: 'abcdef\ngh\nijklmn',
  /** 各行被选中的矩形列区间（0 基、左闭右开），对应设计的"第 2~4 列" */
  columns: [1, 4] as const,
  /** 期望剪贴板内容（逐字符） */
  expected: 'bcd\nh  \nijk',
  /** 期望的块宽 */
  width: 3,
} as const;

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
 * 行1 `abcdef`  1 基第 2~4 列（0 基 [1,4)）⇒ `bcd`
 * 行2 `gh`      同上区间越界，短行右侧补空格到块宽 3 ⇒ `h` + `  ` = `h  `
 * 行3 `ijklmn`  1 基第 2~4 列 ⇒ `jkl`
 * 块宽 = 4 − 1 = 3
 * 剪贴板 = `bcd` + `\n` + `h  ` + `\n` + `jkl` = "bcd\nh  \njkl"
 * ```
 *
 * > 期望串以**实现与列口径**为准（S-9 裁决）：0 基 `[1,4)` = 1 基第 2~4 列，`ijklmn` 取到
 * > `jkl`；需求文档 AC-11 原文写 `ijk` 是"取第 1~3 列"的口径，属文档笔误，已同步改正。
 *
 * ## 本文件的两块职责
 *
 * 1. **复制口径**（AC-11）：`normalizeBlockSelection` / `blockSelectCopy`（鼠标拖出的矩形也走它）；
 * 2. **键盘几何**（AC-13）：`blockCursorFromSelection` / `extendBlockCursor` / `blockCursorRanges`
 *    —— `修饰键 + Shift + ↑/↓` 按列扩行、`修饰键 + Shift + ←/→` 移动列区间的一端。
 *    宿主用 `Prec.highest(keymap.of(...))` 抢在 CM6 默认键位之前（默认里这几个组合被
 *    `copyLineUp/Down`、`selectSyntaxLeft/Right` 占用，见 `blockSelectChord` 注释）。
 *
 * ## 列的口径（诚实说明）
 *
 * 列 = 行内 **UTF-16 code unit 偏移**（与 CM6 的文档位置口径一致，因为 `from/to` 就是位置）。
 * ASCII 文档下与"字符列"完全一致；含星平面字符时与状态栏的码点口径（§4.1.4）可能相差 1 列。
 * 这一取舍的理由：range 的 `from/to` 是位置而非码点，换算成码点列会引入不可逆的歧义。
 */

import type { EditorSelection, SelectionRange, Text } from '../../types/codemirror-bundle';

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
 * `expected` 的第二行末尾是**两个空格**：`"bcd\nh  \njkl"`。
 */
export const BLOCK_SELECT_AC11 = {
  /** 文档（无末尾换行） */
  doc: 'abcdef\ngh\nijklmn',
  /** 各行被选中的矩形列区间（0 基、左闭右开），对应设计的"第 2~4 列" */
  columns: [1, 4] as const,
  /** 期望剪贴板内容（逐字符） */
  expected: 'bcd\nh  \njkl',
  /** 期望的块宽 */
  width: 3,
} as const;

/* ==================== AC-13：键盘列选择（BL-32） ==================== */

/** 列选择的四个方向（与 `修饰键 + Shift + 方向键` 一一对应） */
export type BlockDirection = 'up' | 'down' | 'left' | 'right';

/** 四个方向（遍历 / 生成键位表时用） */
export const BLOCK_DIRECTIONS: readonly BlockDirection[] = ['up', 'down', 'left', 'right'];

/** 方向 → CM6 键名里的箭头名 */
const ARROW_KEY: Record<BlockDirection, string> = {
  up: 'ArrowUp',
  down: 'ArrowDown',
  left: 'ArrowLeft',
  right: 'ArrowRight',
};

/**
 * 键盘列选择的锚点（行号 **1 基**；列 **0 基**；`rightCol` 为**含**端）。
 *
 * 与 `BlockRect` 的区别：`BlockRect` 用「左端 + 宽度」（鼠标拖出的多 range 归一化结果），
 * 这里的 `leftCol/rightCol` 是**两个可独立移动的端点**，因为 AC-13 要求「移动列区间」。
 */
export interface BlockCursor {
  /** 行区间上端（1 基，含） */
  startLine: number;
  /** 行区间下端（1 基，含） */
  endLine: number;
  /** 列区间左端（0 基，含） */
  leftCol: number;
  /** 列区间右端（0 基，**含**） */
  rightCol: number;
}

/**
 * `修饰键 + Shift + 方向键` 的 CM6 键名（AC-13 的键位口径）。
 *
 * 修饰键与 `blockSelectFilter` 同源：只认 `Ctrl`，其余一律 `Alt`（§6.1.2
 * 「同一个 `eventFilter` 只认一个修饰键」—— 改 `Ctrl` 后 `Alt` 自动退化为普通选择）。
 *
 * ⚠ 默认键位里 `Shift-Alt-ArrowUp/Down` 是 `copyLineUp` / `copyLineDown`、
 * `Shift-Alt-ArrowLeft/Right` 是 `selectSyntaxLeft` / `selectSyntaxRight`
 * （真机 r11 H3 实测：`Alt+Shift+↓` 每按一次复制 2 行并触发自动保存写盘）。
 * 因此宿主必须用 `Prec.highest(keymap.of(...))` 安装本组键位才能抢先命中。
 */
export function blockSelectChord(modifier: string, direction: BlockDirection): string {
  const mod = modifier === 'Ctrl' ? 'Ctrl' : 'Alt';
  return `${mod}-Shift-${ARROW_KEY[direction]}`;
}

/**
 * 从当前选区推出键盘锚点（AC-13 的起点）。
 *
 * - 当前选区**已是矩形**（鼠标拖出、或键盘上一轮的产物）⇒ 直接复用它的矩形；
 * - 否则以**主选区头**（光标）的行与列起一个 **1 列宽块**（IDE 的 column-select 手感：
 *   按 `修饰键+Shift+↓` 从光标处向下扩出一列）。
 *
 * 返回 `null` 仅当连一个可用的位置都拿不到（正常 EditorState 不会发生）。
 */
export function blockCursorFromSelection(
  doc: Text,
  selection: Pick<EditorSelection, 'ranges' | 'main'>,
): BlockCursor | null {
  const rect = normalizeBlockSelection(doc, selection.ranges);
  if (rect) {
    return {
      startLine: rect.startLine,
      endLine: rect.endLine,
      leftCol: rect.startCol,
      rightCol: rect.startCol + rect.width - 1,
    };
  }
  const head = selection.main?.head ?? selection.ranges[0]?.head;
  if (typeof head !== 'number' || !Number.isFinite(head)) return null;
  const pos = Math.min(Math.max(0, head), doc.length);
  const line = doc.lineAt(pos);
  const col = Math.min(pos - line.from, line.length);
  return { startLine: line.number, endLine: line.number, leftCol: col, rightCol: col };
}

/**
 * 把锚点夹回**当前文档**的合法范围（审查 M1：陈旧锚点必须夹，不能让 `doc.line` 抛）。
 *
 * 为什么需要：宿主用 `WeakMap<EditorView, BlockCursor>` 记住上一轮派发的锚点，
 * 而文档可能在本轮之前**变短**（回车后 Undo、切文件重新加载、外部替换文档）。
 * 此时旧锚点的行号可能越界，CM6 的 `doc.line(n)` 会抛
 * `RangeError: Invalid line number N in M-line document`（落在 `keymap.run` 里未捕获 ⇒ 键位静默失效）。
 *
 * 夹取后若行数变少，`blockCursorMatchesSelection` 会因 range 个数不等而**拒绝沿用**，
 * 自动改用当前选区重新起锚 —— 这正是我们要的降级行为。
 */
export function clampBlockCursor(doc: Text, cursor: BlockCursor): BlockCursor {
  const last = Math.max(1, doc.lines);
  const startLine = Math.min(Math.max(1, cursor.startLine), last);
  const endLine = Math.min(Math.max(startLine, cursor.endLine), last);
  return { ...cursor, startLine, endLine };
}

/**
 * 按方向**移动锚点的一端**（AC-13 的键盘口径）。
 *
 * 取舍（诚实说明）：实现为**单调扩展**
 * —— `↑` 上边界 −1 行、`↓` 下边界 +1 行、`←` 左列 −1、`→` 右列 +1，
 * 全部夹取在文档（行）/ 最长行行尾（右列）之内。
 * 需求原文写「按列扩展行范围、**移动列区间**」，这里取"移动区间的一端"的读法：
 * 只有这样才能用键盘选出 AC-11 的 3 列块（`→` 两次）；不做"整体平移 + 收缩"是为了
 * 不引入需要额外保存的锚点状态（宿主没有可持久化的列选择状态机）。
 */
export function extendBlockCursor(
  doc: Text,
  cursor: BlockCursor,
  direction: BlockDirection,
): BlockCursor {
  const base = clampBlockCursor(doc, cursor);
  switch (direction) {
    case 'up':
      return { ...base, startLine: Math.max(1, base.startLine - 1) };
    case 'down':
      return { ...base, endLine: Math.min(doc.lines, base.endLine + 1) };
    case 'left':
      return { ...base, leftCol: Math.max(0, base.leftCol - 1) };
    case 'right': {
      // 右端最多推到**块内最长行**的行尾那一列（`rightCol` 是"含"端）。
      // ⚠ 不能用**末行**长度当上限：末行较短时会把 `leftCol` 一起往下钳，
      // 一次按键就让整个块"左移并收缩"（审查 M2 实测 `{leftCol:3,rightCol:4}` + 末行 2 字符
      // ⇒ `{leftCol:1,rightCol:2}`）。左端只由 `←` 负责，这里绝不改 `leftCol`。
      let cap = 0;
      for (let lineNo = base.startLine; lineNo <= base.endLine; lineNo += 1) {
        cap = Math.max(cap, doc.line(lineNo).length - 1);
      }
      const widened = Math.min(Math.max(0, cap), base.rightCol + 1);
      return { ...base, rightCol: Math.max(base.rightCol, widened) };
    }
    default:
      return base;
  }
}

/**
 * 由锚点生成「**每行一个 range**」的选区（喂给 `EditorSelection.create`）。
 *
 * 逐行 `[from, to)`：`from` = 行首 + 左列，`to` = 行首 + 右列 + 1（右端含），
 * 两者都钳到行尾 —— 短行因此被**行尾夹短**（真机实测与 CM6 的矩形选择一致：
 * `abcdef\ngh\nijklmn` 取第 2~4 列时中间行 `gh` 的 range 只有 `h`，补空格由
 * `blockSelectCopy` 负责）。空行得到空 range。
 *
 * 非法锚点（`leftCol > rightCol`）**返回空数组**，不用 `min/max` 夹出一个被放大的区间
 * （审查 L8：静默放大比直接拒绝更隐蔽）。
 */
export function blockCursorRanges(doc: Text, cursor: BlockCursor): { from: number; to: number }[] {
  const base = clampBlockCursor(doc, cursor);
  if (base.leftCol > base.rightCol) return [];
  const out: { from: number; to: number }[] = [];
  for (let lineNo = base.startLine; lineNo <= base.endLine; lineNo += 1) {
    const info = doc.line(lineNo);
    const from = Math.min(info.from + base.leftCol, info.to);
    const to = Math.min(info.from + base.rightCol + 1, info.to);
    out.push({ from: Math.min(from, to), to: Math.max(from, to) });
  }
  return out;
}

/**
 * 当前选区是否就是这把锚点**上一轮派发出去的**（决定"继续扩展"还是"以选区重新起锚"）。
 *
 * 为什么必须记住锚点：单行块在 CM6 里只是**一个普通 range**，而 `normalizeBlockSelection`
 * 的矩形判据要求 **≥2 个 range**（`isBlockSelection` 用它来决定是否接管复制，不能放宽
 * —— 否则普通 Ctrl+C 也会被接管）。于是「先按 → 加宽、再按 ↓ 扩行」这条链上，
 * 第一次按键产生的单行选区**无法**反推出宽度，键盘序列会在第二步丢掉已加宽的列区间。
 *
 * 宿主的 keymap 因此用 `WeakMap<EditorView, BlockCursor>` 记住锚点：
 * 命中本函数 ⇒ 从记住的锚点继续；否则（鼠标点击、打字、粘贴改了选区）重新起锚。
 */
export function blockCursorMatchesSelection(
  doc: Text,
  cursor: BlockCursor | null,
  ranges: readonly SelectionRange[],
): cursor is BlockCursor {
  if (!cursor) return false;
  const expected = blockCursorRanges(doc, cursor);
  if (expected.length !== ranges.length) return false;
  return expected.every((item, index) => item.from === ranges[index].from && item.to === ranges[index].to);
}

/** AC-13 的手算样例：键盘序列 **→×2 + ↓×2** 应产出 AC-11 的同一个剪贴板串 */
export const BLOCK_SELECT_AC13 = {
  /** 与 AC-11 同一份文档 */
  doc: BLOCK_SELECT_AC11.doc,
  /** 起始光标所在行（1 基）与列（0 基）：`abcdef` 的第 2 个字符（`b`） */
  seedLine: 1,
  seedCol: 1,
  /** 键盘序列（先横向移动右端两次，再纵向扩行两次） */
  keys: ['right', 'right', 'down', 'down'] as readonly BlockDirection[],
  /** 期望剪贴板内容（与 AC-11 完全一致） */
  expected: BLOCK_SELECT_AC11.expected,
} as const;

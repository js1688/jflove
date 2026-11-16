/**
 * 终端「按行数反推字号 / 行高」的纯函数（v1.10.9）
 *
 * ## 背景
 *
 * dsh TUI 在 **≤21 行**的终端里会把输入框的内容行画丢（真机复现；用 tmux 当参照终端
 * 同样复现 ⇒ 属上位程序缺陷，改不了它）。产品的兜底策略是**保证终端至少有 24 行**。
 *
 * 只缩字号不够：行高 ≈ 字体自然行高 × `lineHeight`，字号 13→10 只从 23px 降到 18px
 * （+2 行）。所以候选阶梯同时压 `lineHeight`：先压行高、再逐步缩字号。
 *
 * 这里把"挑候选"抽成纯函数，DOM 测量由调用方通过 `measure` 注入，便于单测。
 */

/** 一个候选的显示参数 */
export interface FitCandidate {
  /** 字号 */
  fontSize: number;
  /** xterm 的 lineHeight 倍数（1.0 是下限，字形会偏紧） */
  lineHeight: number;
}

/** 选中的结果 */
export interface FitResult extends FitCandidate {
  /** 该候选下的行数 */
  rows: number;
  /** 是否为了行数切换到了更紧凑的候选 */
  compacted: boolean;
  /** 最紧凑的候选也不够 */
  tooSmall: boolean;
}

/** 舒适档行高（正常窗口下的观感基线） */
export const COMFORT_LINE_HEIGHT = 1.25;

/**
 * 生成候选阶梯：从"用户字号 + 舒适行高"开始，先压行高、再逐级缩字号。
 *
 * 顺序即优先级：调用方命中第一个满足行数的候选。
 *
 * @param userSize 用户设定的字号
 * @param minSize 允许的最小字号
 */
export function buildFitCandidates(userSize: number, minSize: number): FitCandidate[] {
  const size = Math.max(minSize, Math.round(userSize));
  const candidates: FitCandidate[] = [{ fontSize: size, lineHeight: COMFORT_LINE_HEIGHT }];
  // 先只压行高：观感变化最小，通常能多出 3~5 行
  candidates.push({ fontSize: size, lineHeight: 1.1 });
  candidates.push({ fontSize: size, lineHeight: 1.0 });
  // 行高压到底仍然不够，才逐级缩字号
  for (let smaller = size - 1; smaller >= minSize; smaller -= 1) {
    candidates.push({ fontSize: smaller, lineHeight: 1.0 });
  }
  return candidates;
}

/**
 * 按候选顺序找到第一个能容纳 `minRows` 行的参数。
 *
 * @param candidates 候选阶梯（顺序即优先级）
 * @param minRows 需要的最小行数
 * @param measure 给定候选下的行数（调用方负责真正应用参数并 fit）
 * @returns 命中的候选 + 行数 + compacted/tooSmall 标记
 */
export function pickFitForMinRows(
  candidates: FitCandidate[],
  minRows: number,
  measure: (candidate: FitCandidate) => number,
): FitResult {
  if (candidates.length === 0) {
    return { fontSize: 13, lineHeight: COMFORT_LINE_HEIGHT, rows: 0, compacted: false, tooSmall: true };
  }
  for (let index = 0; index < candidates.length; index += 1) {
    const candidate = candidates[index];
    const rows = measure(candidate);
    if (rows >= minRows) {
      return { ...candidate, rows, compacted: index > 0, tooSmall: false };
    }
  }
  const last = candidates[candidates.length - 1];
  return { ...last, rows: measure(last), compacted: true, tooSmall: true };
}

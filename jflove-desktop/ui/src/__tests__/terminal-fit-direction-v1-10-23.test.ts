/**
 * 自适应字号的**方向性**语义（v1.10.23，台账 T-2 的真正结论）
 *
 * ## 事故复盘（结论纠正）
 *
 * v1.10.22 台账把 `min-rows` 用例失败记成"产品缺陷：恢复窗口后字号没还原"。
 * v1.10.23 实测查明**产品行为是对的，用例的假设错了**：
 *
 * 终端区 894×552、字号 13 时：
 * - `lineHeight 1.25`（舒适）→ 行高 **25.63px** → 只能放 **20 行** < `TUI_MIN_ROWS(24)`
 * - `lineHeight 1.00`         → 行高 **20.50px** → 能放 **26 行** ≥ 24 ✅
 *
 * ⇒ 在这个窗口尺寸下**必须压缩**，否则 TUI 会画丢（dsh ≤21 行会丢输入框内容行）。
 * 要容纳 24 行舒适档，终端区需要 ≥ 615px。
 *
 * 因此本文件锁的是**方向性**，而不是"永远不出现自适应"：
 * 1. 放得下 ⇒ 必须选非紧凑档（`compacted === false`）；
 * 2. 放不下 ⇒ 必须压缩，且压缩后行数 ≥ minRows（活着比好看重要）；
 * 3. 怎么压都不够 ⇒ `tooSmall === true`（交给界面提示，不得静默坏掉）。
 */
import { describe, expect, it } from 'vitest';
import {
  COMFORT_LINE_HEIGHT,
  buildFitCandidates,
  pickFitForMinRows,
} from '../utils/terminal-fit';

/** 按实测比例模拟行数：1.0 档行高 20.5px，行数与可用高度成正比 */
const ROWS_AT_LINE_HEIGHT_1 = 26; // 894×552 的终端区实测值
const measureFor = (availableHeight = 533) => (candidate: { lineHeight: number }) => {
  const rowHeight = 20.5 * candidate.lineHeight;
  return Math.floor(availableHeight / rowHeight);
};

describe('自适应字号方向性（v1.10.23）', () => {
  it('高度够时必须用舒适档（不得无谓压缩）', () => {
    const candidates = buildFitCandidates(13, 10);
    // 要让 1.25 档容纳 24 行，需要 24 * 25.63 ≈ 615px
    const result = pickFitForMinRows(candidates, 24, measureFor(760));
    expect(result.compacted).toBe(false);
    expect(result.lineHeight).toBe(COMFORT_LINE_HEIGHT);
    expect(result.rows).toBeGreaterThanOrEqual(24);
  });

  it('894×552 的实际尺寸下舒适档只有 20 行 ⇒ 必须压缩到够 24 行', () => {
    const candidates = buildFitCandidates(13, 10);
    const result = pickFitForMinRows(candidates, 24, measureFor(533));
    expect(result.rows).toBeGreaterThanOrEqual(24);
    expect(result.compacted).toBe(true); // 压缩是**正确**行为
    expect(result.tooSmall).toBe(false);
    // 实测该尺寸下最终是 lineHeight 1.0、26 行
    expect(result.lineHeight).toBe(1);
    expect(result.rows).toBe(ROWS_AT_LINE_HEIGHT_1);
  });

  it('舒适档的行高确实随 lineHeight 线性变化（回归：曾误判为"字体未就绪"）', () => {
    const candidates = buildFitCandidates(13, 10);
    const comfort = candidates.find((c) => c.lineHeight === COMFORT_LINE_HEIGHT)!;
    // 1.25 档行高约 25.6px；20.5 * 1.25 = 25.625
    const comfortRowHeight = 20.5 * comfort.lineHeight;
    expect(comfortRowHeight).toBeCloseTo(25.63, 1);
    // 533 / 25.63 ≈ 20.8 ⇒ 20 行，低于 24
    expect(Math.floor(533 / comfortRowHeight)).toBeLessThan(24);
  });

  it('怎么压都不够时必须置 tooSmall（不得静默坏掉）', () => {
    const candidates = buildFitCandidates(13, 10);
    const result = pickFitForMinRows(candidates, 24, measureFor(200));
    expect(result.tooSmall).toBe(true);
    expect(result.compacted).toBe(true);
  });

  it('关闭自适应时只有一个候选：放不下就置 tooSmall（交给界面提示）', () => {
    const single = [{ fontSize: 13, lineHeight: COMFORT_LINE_HEIGHT }];
    const result = pickFitForMinRows(single, 24, measureFor(533));
    // 走的是"所有候选都不够"的兜底分支 ⇒ compacted 为 true、tooSmall 为 true
    // （compacted 在兜底分支恒为 true，语义是"没能用理想档"）
    expect(result.tooSmall).toBe(true);
    expect(result.rows).toBeLessThan(24); // 确实不够
    expect(result.fontSize).toBe(13); // 仍然用用户字号，不擅自缩字
  });
});

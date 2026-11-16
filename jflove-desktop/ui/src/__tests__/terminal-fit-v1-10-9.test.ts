/**
 * 「按行数反推字号 / 行高」单测（v1.10.9）
 *
 * 事故背景：dsh TUI 在 ≤21 行会丢输入框内容行（tmux 同样复现）。
 * 兜底策略 = 从"用户字号 + 舒适行高"开始压行高、再缩字号，保证 ≥24 行；
 * 窗口拉高后回到舒适候选。
 */
import { describe, expect, it } from 'vitest';
import {
  COMFORT_LINE_HEIGHT,
  buildFitCandidates,
  pickFitForMinRows,
  type FitCandidate,
} from '../utils/terminal-fit';

/**
 * 构造与实测一致的测量器：行高 ≈ 字号 × 1.42 × lineHeight（13px/1.25 → 23px）。
 * @param availablePx 终端可用高度
 */
function measureWith(availablePx: number) {
  return (candidate: FitCandidate) => {
    const cell = Math.max(8, Math.round(candidate.fontSize * 1.42 * candidate.lineHeight));
    return Math.max(1, Math.floor(availablePx / cell));
  };
}

describe('buildFitCandidates', () => {
  it('第一个候选是用户字号 + 舒适行高', () => {
    const candidates = buildFitCandidates(13, 10);
    expect(candidates[0]).toEqual({ fontSize: 13, lineHeight: COMFORT_LINE_HEIGHT });
  });

  it('先压行高、再缩字号，且字号不低于下限', () => {
    const candidates = buildFitCandidates(13, 10);
    const sizes = candidates.map((candidate) => candidate.fontSize);
    expect(sizes[0]).toBe(13);
    expect(Math.min(...sizes)).toBe(10);
    // 前三个候选字号不变，只是行高变紧
    expect(candidates.slice(0, 3).every((candidate) => candidate.fontSize === 13)).toBe(true);
    expect(candidates[2].lineHeight).toBe(1);
  });
});

describe('pickFitForMinRows', () => {
  it('空间足够：选第一个（不 compacted）', () => {
    const candidates = buildFitCandidates(13, 10);
    const result = pickFitForMinRows(candidates, 24, measureWith(24 * 23));
    expect(result.compacted).toBe(false);
    expect(result.tooSmall).toBe(false);
    expect(result.fontSize).toBe(13);
    expect(result.lineHeight).toBe(COMFORT_LINE_HEIGHT);
    expect(result.rows).toBeGreaterThanOrEqual(24);
  });

  it('略矮：压行高就能满足，字号保持不变', () => {
    const candidates = buildFitCandidates(13, 10);
    // 20 行（13px/1.25）的空间：460px；压到 1.0 后每行约 18px → 25 行
    const result = pickFitForMinRows(candidates, 24, measureWith(20 * 23));
    expect(result.compacted).toBe(true);
    expect(result.tooSmall).toBe(false);
    expect(result.fontSize).toBe(13);
    expect(result.lineHeight).toBeLessThan(COMFORT_LINE_HEIGHT);
    expect(result.rows).toBeGreaterThanOrEqual(24);
  });

  it('很矮：缩字号 + 行高 1.0 才够', () => {
    const candidates = buildFitCandidates(13, 10);
    const result = pickFitForMinRows(candidates, 24, measureWith(17 * 23));
    expect(result.tooSmall).toBe(false);
    expect(result.rows).toBeGreaterThanOrEqual(24);
    expect(result.fontSize).toBeLessThan(13);
    expect(result.lineHeight).toBe(1);
  });

  it('极端矮：最紧凑仍不够 → tooSmall 且返回最紧凑候选', () => {
    const candidates = buildFitCandidates(13, 10);
    const result = pickFitForMinRows(candidates, 24, measureWith(10 * 23));
    expect(result.tooSmall).toBe(true);
    expect(result.fontSize).toBe(10);
    expect(result.lineHeight).toBe(1);
  });
});

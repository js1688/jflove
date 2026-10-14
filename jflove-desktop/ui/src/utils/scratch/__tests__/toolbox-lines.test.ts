/**
 * 工具箱第 2 项「行操作」负向对照 —— Phase 6（v1.6.0 · AC-25）
 *
 * 判据来源：`文档记录/需求文档/v1.6.0.md` AC-25 —— 用**需求原文样本**
 * `"  a  \n\n\tb\t\n"` 逐字符比对四种操作的输出。
 *
 * 口径（设计 §4.1.6）：输入按 `split('\n')` 切分、输出 `join('\n')`，**不额外补末尾换行**；
 * 排序为**码点升序**（`\t` 0x09 < 空格 0x20）。
 *
 * `[负向对照]`：把四组期望串改成"顺手 trim 过"的值 ⇒ 本文件必须失败（poc r9 执行）。
 */
import { describe, expect, it } from 'vitest';
import { dedupeLines, joinLines, removeEmptyLines, sortLines, splitLines, trimLines } from '../toolbox';

/** 需求 AC-25 的原文样本（4 行：`"  a  "` / 空行 / `"\tb\t"` / 空行） */
const SAMPLE = '  a  \n\n\tb\t\n';

describe('AC-25 行操作（需求原文样本，逐字符判据）', () => {
  it('样本切成 4 行，且切分不吞字符', () => {
    expect(splitLines(SAMPLE)).toEqual(['  a  ', '', '\tb\t', '']);
    expect(joinLines(['  a  ', '', '\tb\t', ''])).toBe(SAMPLE);
  });

  it('去空行 ⇒ "  a  \\n\\tb\\t"', () => {
    expect(removeEmptyLines(SAMPLE)).toBe(joinLines(['  a  ', '\tb\t']));
  });

  it('去首尾空格 ⇒ "a\\n\\nb\\n"（空行保留，末尾换行来自最后一行）', () => {
    expect(trimLines(SAMPLE)).toBe(joinLines(['a', '', 'b', '']));
  });

  it('排序（码点升序）⇒ 两个空行在前，制表符行先于空格行', () => {
    expect(sortLines(SAMPLE)).toBe(joinLines(['', '', '\tb\t', '  a  ']));
  });

  it('去重 ⇒ 只去掉重复的那个空行，原始空白不归一化', () => {
    expect(dedupeLines(SAMPLE)).toBe(joinLines(['  a  ', '', '\tb\t']));
  });

  it('负向对照锚点：样本本身不做任何归一化（首行两个前导空格必须还在）', () => {
    expect(removeEmptyLines(SAMPLE).startsWith('  a  ')).toBe(true);
    expect(removeEmptyLines(SAMPLE)).not.toBe('a\n\tb\t');
  });
});

/**
 * v1.6.2 AC-82①（单测半边）用例：`firstDifferenceIndex` —— 首个差异位置
 *
 * 用途：工具箱动作写回正文后，`ScratchEditorSession.setText(next, { revealFirstChange: true })`
 * 靠它把"变化开始的地方"滚进视口 + 定位光标（治"作用文字不明显"）。
 * 真机半边（变化行确实进了视口）由 `poc/editor-probes/r22-real-gui-v162.py` 判。
 */

import { describe, expect, it } from 'vitest';
import { firstDifferenceIndex } from '../toolbox';

describe('firstDifferenceIndex：首个差异的 UTF-16 码元位置', () => {
  it('前缀相同 ⇒ 返回第一个不同字符的下标', () => {
    expect(firstDifferenceIndex('a\nb', 'a\nB')).toBe(2);
    expect(firstDifferenceIndex('abc', 'abd')).toBe(2);
  });

  it('完全相同时返回较短长度（调用方据此判定"无变化"）', () => {
    expect(firstDifferenceIndex('abc', 'abc')).toBe(3);
    expect(firstDifferenceIndex('', '')).toBe(0);
  });

  it('一方是另一方的前缀 ⇒ 返回较短长度（差异从"截断处"开始）', () => {
    expect(firstDifferenceIndex('abc', 'abcd')).toBe(3);
    expect(firstDifferenceIndex('abcd', 'abc')).toBe(3);
  });

  it('首字符就不同 ⇒ 0（变化从头开始，光标应回到文首）', () => {
    expect(firstDifferenceIndex('abc', 'zbc')).toBe(0);
    expect(firstDifferenceIndex('', 'x')).toBe(0);
  });

  it('与码元口径一致（代理对不被当成两个字符位置错开）', () => {
    const emoji = '\u{1f600}'; // 代理对，长度 2
    expect(firstDifferenceIndex(`${emoji}a`, `${emoji}b`)).toBe(2);
    expect(firstDifferenceIndex(`${emoji}a`, `a${emoji}`)).toBe(0);
  });
});

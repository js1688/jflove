/**
 * 无边框窗口边缘热区回归（v1.10.11）
 *
 * ## 判据出处
 *
 * 用户实测：「无法改变窗口大小，只能最大化；更改窗口大小变成了拖拽。」
 * 根因：`Qt.Edge` 的位值是 **Top=1 / Left=2 / Right=4 / Bottom=8**，
 * 而这里曾写成 `left=1 / top=2` ⇒ 左右与上下热区互换：
 * 拖左右边缘实际请求的是上下缩放（看起来没反应），拖上边缘又和标题栏
 * `startSystemMove()` 抢，表现为「拉大小变成拖窗口」。
 *
 * ## 为什么判据写死在测试里
 *
 * 这两个数字是 **Qt 侧契约**（`PySide6.QtCore.Qt.Edge`），不是随便定的常量；
 * 一旦有人"顺手按直觉重排"，用户侧又会失去左右/上下缩放能力。
 * 用例同时覆盖「边缘命中」和「角落组合」。
 */

import { describe, expect, it } from 'vitest';
import { EDGE, edgesAt } from '../TitleBar';

describe('窗口边缘热区（Qt.Edge 位值）', () => {
  it('位值与 Qt.Edge 一致：Top=1 / Left=2 / Right=4 / Bottom=8', () => {
    expect(EDGE.top).toBe(1);
    expect(EDGE.left).toBe(2);
    expect(EDGE.right).toBe(4);
    expect(EDGE.bottom).toBe(8);
  });

  it('左边缘命中的是 Left（历史 bug：会误报成 Top）', () => {
    const mask = edgesAt(2, 300);
    expect(mask).toBe(EDGE.left);
    expect(mask & EDGE.left).toBeTruthy();
    expect(mask & EDGE.top).toBeFalsy();
  });

  it('上边缘命中的是 Top（历史 bug：会误报成 Left）', () => {
    const mask = edgesAt(300, 2);
    expect(mask).toBe(EDGE.top);
    expect(mask & EDGE.top).toBeTruthy();
    expect(mask & EDGE.left).toBeFalsy();
  });

  it('右/下边缘与角落组合正确', () => {
    expect(edgesAt(window.innerWidth - 2, 300)).toBe(EDGE.right);
    expect(edgesAt(300, window.innerHeight - 2)).toBe(EDGE.bottom);
    expect(edgesAt(2, 2)).toBe(EDGE.top | EDGE.left);
    expect(edgesAt(window.innerWidth - 2, window.innerHeight - 2)).toBe(
      EDGE.right | EDGE.bottom,
    );
  });

  it('窗口内部不命中任何边缘', () => {
    expect(edgesAt(300, 300)).toBe(0);
  });
});

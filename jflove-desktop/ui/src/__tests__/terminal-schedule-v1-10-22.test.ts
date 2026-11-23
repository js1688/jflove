/**
 * 终端输出调度器单测（v1.10.22）
 *
 * 核心判据：**页面被判隐藏时（`requestAnimationFrame` 完全停摆），
 * 调度器仍然要把合帧回调执行掉** —— 这正是旧实现丢输出的根因。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  __resetSchedulerForTest,
  cancelFlush,
  scheduleFlush,
} from '../utils/terminal-schedule';

describe('terminal-schedule', () => {
  beforeEach(() => {
    __resetSchedulerForTest();
  });

  afterEach(() => {
    __resetSchedulerForTest();
    vi.restoreAllMocks();
  });

  it('同一轮内多次调度只合并成一次执行', async () => {
    const calls: number[] = [];
    scheduleFlush(() => calls.push(1));
    scheduleFlush(() => calls.push(2));
    scheduleFlush(() => calls.push(3));
    expect(calls).toHaveLength(0); // 尚未到下一轮事件循环

    await new Promise<void>((resolve) => setTimeout(resolve, 10));
    expect(calls).toEqual([1, 2, 3]); // 按注册顺序，一次跑完
  });

  it('执行完可以再次调度（不是一次性）', async () => {
    let n = 0;
    scheduleFlush(() => { n += 1; });
    await new Promise<void>((resolve) => setTimeout(resolve, 10));
    scheduleFlush(() => { n += 1; });
    await new Promise<void>((resolve) => setTimeout(resolve, 10));
    expect(n).toBe(2);
  });

  it('不依赖 requestAnimationFrame（页面隐藏时 rAF 停摆也必须执行）', async () => {
    // 模拟"页面被判隐藏"：把 rAF 换成永不回调
    const rafSpy = vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation(
      // 永不调用回调，等价于 Chromium 隐藏页面时的行为
      () => 1,
    );
    let ran = false;
    scheduleFlush(() => { ran = true; });
    await new Promise<void>((resolve) => setTimeout(resolve, 20));
    expect(ran).toBe(true);
    expect(rafSpy).not.toHaveBeenCalled();
  });

  it('cancelFlush 清掉尚未执行的回调', async () => {
    let ran = false;
    scheduleFlush(() => { ran = true; });
    cancelFlush();
    await new Promise<void>((resolve) => setTimeout(resolve, 20));
    expect(ran).toBe(false);
  });

  it('单个回调抛异常不影响同轮其它回调', async () => {
    const seen: string[] = [];
    scheduleFlush(() => { throw new Error('boom'); });
    scheduleFlush(() => { seen.push('after'); });
    await new Promise<void>((resolve) => setTimeout(resolve, 20));
    expect(seen).toEqual(['after']);
  });

  it('同一轮回调里再次调度会在下一轮执行（不会递归失控）', async () => {
    let depth = 0;
    let maxDepth = 0;
    const run = () => {
      depth += 1;
      maxDepth = Math.max(maxDepth, depth);
      if (depth < 3) scheduleFlush(run);
      depth -= 1;
    };
    scheduleFlush(run);
    await new Promise<void>((resolve) => setTimeout(resolve, 30));
    expect(maxDepth).toBe(1);
  });

  it('没有 MessageChannel 的环境退化为 setTimeout', async () => {
    const OriginalMessageChannel = globalThis.MessageChannel;
    // @ts-expect-error 故意移除，模拟老引擎
    delete globalThis.MessageChannel;
    try {
      __resetSchedulerForTest();
      let ran = false;
      scheduleFlush(() => { ran = true; });
      await new Promise<void>((resolve) => setTimeout(resolve, 20));
      expect(ran).toBe(true);
    } finally {
      globalThis.MessageChannel = OriginalMessageChannel;
    }
  });
});

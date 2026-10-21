/**
 * 可见性轮询守卫（v1.8.0 / AC-232 / F27.3）
 *
 * 判据：
 *   ① 页面可见时按间隔刷新（挂载即先刷新一次）；
 *   ② 切到后台后**计时器停止**，推进时间不再产生请求；
 *   ③ 回到前台立即补一次并恢复计时；
 *   ④ 卸载后不再产生请求。
 */

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useVisiblePolling } from '../use-polling';

/** 测试探针：把 hook 的调用次数暴露给断言 */
function Probe({ enabled, task }: { enabled: boolean; task: () => void }) {
  useVisiblePolling(task, 2500, enabled);
  return null;
}

/** 修改 jsdom 的文档可见性并广播事件 */
function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value: state,
  });
  act(() => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  setVisibility('visible');
});

afterEach(() => {
  vi.useRealTimers();
  setVisibility('visible');
});

describe('useVisiblePolling（AC-232）', () => {
  it('可见时按间隔轮询；切后台停止；回前台恢复；卸载清理', async () => {
    const task = vi.fn();
    const { unmount } = render(<Probe enabled task={task} />);

    // 挂载即刷新一次（不等第一个间隔）
    await act(async () => {
      await Promise.resolve();
    });
    expect(task).toHaveBeenCalledTimes(1);

    // 可见：推进一个间隔 → 第二次
    await act(async () => {
      vi.advanceTimersByTime(2500);
      await Promise.resolve();
    });
    expect(task).toHaveBeenCalledTimes(2);

    // 切后台：计时器应停止，推进多个间隔也不再请求
    setVisibility('hidden');
    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
    });
    expect(task).toHaveBeenCalledTimes(2);

    // 回前台：立即补一次
    setVisibility('visible');
    await act(async () => {
      await Promise.resolve();
    });
    expect(task).toHaveBeenCalledTimes(3);

    // 卸载：之后不再请求
    unmount();
    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
    });
    expect(task).toHaveBeenCalledTimes(3);
  });

  it('enabled=false 时不轮询', async () => {
    const task = vi.fn();
    render(<Probe enabled={false} task={task} />);
    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
    });
    expect(task).not.toHaveBeenCalled();
  });
});

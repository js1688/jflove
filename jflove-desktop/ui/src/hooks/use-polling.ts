/**
 * 页面可见时才运行的轮询 Hook（v1.8.0 离线下载）
 *
 * 判据（需求 F27.3 / AC-232，设计 §3.2）：
 *   1. 页面在后台（`document.visibilityState !== 'visible'`）时**停止计时器**，
 *      不是"照常计时、只是跳过请求"——这样切后台后请求计数立即不再增长；
 *   2. 回到前台立即补一次刷新，并恢复定时器；
 *   3. 卸载时清理定时器与 `visibilitychange` 监听，**不留下悬挂请求**；
 *   4. 同一时刻最多一个请求在途（网络慢于轮询间隔时不会堆叠）。
 *
 * 为什么抽成 Hook：任务中心与「分析中」状态需要同一条纪律，
 * 各写一份 `setInterval` 很容易漏掉可见性守卫或卸载清理。
 */

import { useEffect, useRef } from 'react';

/**
 * 按 `intervalMs` 轮询执行 `task`，但只在页面可见且 `enabled` 为真时运行。
 *
 * @param task 每次轮询执行的动作（可返回 Promise；异常会被吞掉，由调用方在内部处理）
 * @param intervalMs 轮询间隔（毫秒；<=0 表示禁用）
 * @param enabled 是否启用（数据进入终态后由调用方置 false）
 */
export function useVisiblePolling(
  task: () => void | Promise<void>,
  intervalMs: number,
  enabled: boolean,
): void {
  // 用 ref 保存最新回调，避免调用方每次渲染生成新函数导致 effect 重启
  const taskRef = useRef(task);
  useEffect(() => {
    taskRef.current = task;
  }, [task]);

  useEffect(() => {
    if (!enabled || intervalMs <= 0) return;

    let timer: number | undefined;
    let disposed = false;
    let inFlight = false;

    const stopTimer = () => {
      if (timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };

    const tick = async () => {
      // 后台标签页不刷新：Chromium 会节流 setInterval，但可见性判据必须显式成立
      if (disposed || document.visibilityState !== 'visible' || inFlight) return;
      inFlight = true;
      try {
        await taskRef.current();
      } finally {
        inFlight = false;
      }
    };

    const startTimer = () => {
      stopTimer();
      void tick();
      timer = window.setInterval(() => {
        void tick();
      }, intervalMs);
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        startTimer();
      } else {
        stopTimer();
      }
    };

    if (document.visibilityState === 'visible') startTimer();
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      disposed = true;
      stopTimer();
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [enabled, intervalMs]);
}

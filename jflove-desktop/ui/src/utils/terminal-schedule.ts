/**
 * 终端输出写入调度器（v1.10.22）
 *
 * ## 为什么不用 `requestAnimationFrame`
 *
 * 旧实现把「PTY 输出写进 xterm」排在 `requestAnimationFrame` 上，等于把
 * **输入回显的时延绑死在页面的出帧率上**。而 rAF 在下面两种情况下会被
 * Chromium 直接掐住，表现就是「打字像卡住」：
 *
 * 1. **页面被判为隐藏**（最小化到托盘 / 窗口被完全遮挡）：
 *    实测 `document.visibilityState === 'hidden'` 时 rAF **完全停摆**
 *    （1 秒内 0 帧，同一时刻 MessageChannel 在 2.5 秒里跑了 29 万次）；
 *    窗口藏起来时终端还在收输出，这些输出会一直压在待写队列里，
 *    队列涨到 8MB 上限后开始**静默丢最旧的数据**，恢复时再一次性灌进去。
 * 2. **无合成器的 X 服务器**（Xvfb / Xvnc 等）：页面里只要有 xterm 在持续
 *    `write()`，rAF 会从 60fps 掉到 **1.5fps**，按键到画面出现实测
 *    **2~4 秒**；同一页面把 xterm 从 DOM 摘掉后立刻回到 60fps。
 *
 * ## 做法
 *
 * 用 **`MessageChannel` 的宏任务**做合帧：它会在当前任务结束后立刻执行，
 * 一个事件循环内到达的多块输出合并成一次写入，但**不依赖出帧**，
 * 因此不受上面两种限流影响。没有 `MessageChannel` 的环境（老引擎 / 测试替身）
 * 退化为 `setTimeout(0)`。
 *
 * 注意：本调度器只负责「把字节交给 xterm」，xterm 自己的绘制仍然是它内部的
 * rAF —— 但那是它的事，写入不再被卡在排队阶段。
 */

/** 一次待执行的合帧回调 */
type FlushTask = () => void;

let channel: MessageChannel | null = null;
let pendingTasks: FlushTask[] = [];
let scheduled = false;

/** 调度一轮合帧（同一轮里注册的回调按注册顺序执行） */
function runPendingTasks(): void {
  scheduled = false;
  const tasks = pendingTasks;
  pendingTasks = [];
  for (const task of tasks) {
    try {
      task();
    } catch {
      // 单个回调异常不得打断其它会话的输出
    }
  }
}

/** 保证 `channel` 已建立并已接上端口（幂等） */
function ensureChannel(): MessageChannel | null {
  if (typeof MessageChannel === 'undefined') return null;
  if (channel) return channel;
  channel = new MessageChannel();
  channel.port1.onmessage = () => runPendingTasks();
  return channel;
}

/**
 * 安排一次合帧回调。
 *
 * 同一个宏任务内多次调用只会在**下一轮事件循环**执行一次，天然完成合并，
 * 且不像 rAF 那样受页面可见性 / 出帧率影响。
 *
 * @param task 待执行回调（通常是把待写缓冲刷进 xterm）
 */
export function scheduleFlush(task: FlushTask): void {
  pendingTasks.push(task);
  if (scheduled) return;
  scheduled = true;
  const ch = ensureChannel();
  if (ch) {
    ch.port2.postMessage(0);
    return;
  }
  // 兜底：没有 MessageChannel 时用 0ms 定时器（语义一致，仅多一层定时器开销）
  setTimeout(runPendingTasks, 0);
}

/**
 * 取消尚未执行的合帧（会话销毁时调用）。
 *
 * 只清队列：已经 postMessage 的那一轮会以空队列执行，是安全的空操作。
 */
export function cancelFlush(): void {
  pendingTasks = [];
}

/** 仅供单测：重置模块级状态 */
export function __resetSchedulerForTest(): void {
  pendingTasks = [];
  scheduled = false;
  if (channel) {
    channel.port1.onmessage = null;
    try {
      channel.port1.close();
      channel.port2.close();
    } catch {
      // 关闭阶段忽略
    }
  }
  channel = null;
}

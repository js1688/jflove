/**
 * 启动期 `editor.openPaths` 事件队列（设计 v1.6.0 §7.5 / §3.1 #17 / §7.2）
 *
 * ## 为什么需要它（设计 §7.5 的问题陈述）
 *
 * `main.tsx` 是「先 `await bootstrap()`（内部有桥调用，量级 10² ms）→ 再 `createRoot().render()`」。
 * 于是「进程启动」到「工作区挂载并订阅事件」之间存在一个窗口期：
 * `desktop-bridge.ts` 的 `eventPushed` 处理器只投递给**已注册**的 handler
 * （`desktop-bridge.ts:127-137`，无 handler 时直接 return）⇒ 该窗口内推送的命令行路径**会被丢弃**。
 *
 * 本模块的对策（设计 §7.5 的**双保险之①**）：
 *   1. **模块顶层**就调 `onBridgeEvent('editor.openPaths', …)` 把事件写进队列 —— `onBridgeEvent`
 *      只要求 `window` 存在，**不要求桥握手完成**（注册表是模块级的，握手后事件照常到达）；
 *      本模块由 `App.tsx` 静态 import（v1.6.1：与 `editor-open-paths.ts` /
 *      `shortcut-runtime.ts` 同一手法），而 ESM 的 import 求值发生在
 *      `main.tsx` 的 `void bootstrap()` **之前** ⇒ 注册先于 `bootstrap()` 的 await。
 *   2. UI 就绪后由 `editor-open-paths.ts` 里的消费者调 `onOpenPaths(handler)` + `flushOpenPaths()`
 *      把积压路径**按序**交给 `editor.detectAndRead` 建标签页（见 §4.1.3 懒挂载）。
 *   3. **v1.6.1 新增**：`setOpenPathsGate()` 打准入闸 —— 未登录时 `flushOpenPaths()` 首行直接
 *      返回 `[]`，队列与 `delivered` 都不动（AC-64 ③：登录成功后自动打开，路径不丢）；
 *      判门必须在投递之前，否则 `rememberDelivered` 会让重试被去重拒绝（设计 §4.4）。
 *
 * ## 队列口径（设计 §7.5 判据）
 *
 * - **按序**：FIFO 投递，绝不重排（用户感知的顺序 = 命令行顺序）；
 * - **去重**：`queued`（在队内）+ `delivered`（已投递）双集合。
 *   Python 侧的重试推送去重键是 `(path, size, mtime)`（§7.5 ③），但**渲染层拿不到 size/mtime**
 *   （`editor.openPaths` 的 payload 只有 `paths`），故这里退化为**按路径字符串去重** —— 这也正是
 *   §7.5 ③ 所写「渲染层按 `paths` 去重」的落地；
 * - **带上限**：队列最多保留 `MAX_PENDING_OPEN_PATHS` 条（超出直接丢弃并计数，**不阻塞启动**）；
 * - **不打印路径与内容**（AGENTS.md §9.14 / 设计 §7.4）：本模块**零日志**，
 *   只通过 `droppedOpenPaths()` 暴露一个**数字**供调用方提示。
 *
 * ## 安全（AGENTS.md §9.6）
 *
 * 本模块不碰文件系统、不发网络请求，只是字符串数组的搬运工；路径字符串只在渲染层内存中流转。
 */

import { scratchService } from '../../services/scratch-service';
import type { OpenPathsPayload } from '../../types/scratch';

/** 队列容量上限（超出丢弃；与 `DETECT_AND_READ_MAX_PATHS = 64` 同量级，避免一次性撑爆桥） */
export const MAX_PENDING_OPEN_PATHS = 64;

/** `delivered` 去重集合的保留上限（防止长时间运行无界增长） */
const MAX_DELIVERED_KEYS = 512;

/** 队列消费者：一次拿到**按序**的一批路径 + 来源 */
export type OpenPathsHandler = (paths: string[], source: OpenPathsPayload['source']) => void;

/** 待处理路径（FIFO；与 `queued` 集合同生命周期） */
let pending: string[] = [];

/** 在队内路径（去重） */
const queued = new Set<string>();

/** 已投递路径（去重：覆盖「Python 侧 1500 ms 重试再推一次」的长尾，§7.5 ③） */
const delivered = new Set<string>();

/** `delivered` 的插入顺序（用于裁剪到 `MAX_DELIVERED_KEYS`） */
let deliveredOrder: string[] = [];

/** 消费者集合（正常情况下只有 `editor-open-paths.ts` 一个） */
const handlers = new Set<OpenPathsHandler>();

/**
 * 投递前的**登录门**（设计 v1.6.1 §4.4 / DS-20）。
 *
 * 默认 `() => true`（保持 v1.6.0 语义，既有测试不受影响）；由 `editor-open-paths.ts`
 * 接线成 `currentEditorGate` —— 未登录时队列**挂起**，登录成功后由登录态订阅兑现。
 *
 * ⚠ **判门必须放在投递之前**（`flushOpenPaths` 首行），不能放在消费者里：
 * `flushOpenPaths` 会把交付的路径记入 `delivered`（`:124`），而 `enqueueOpenPaths` 对
 * `delivered` 命中**拒绝入队**（`:98`）⇒「先投递、消费者发现没登录再退回」会让路径**永久丢失**。
 * 先判门、后投递，队列与去重表都不动，才能"挂起到登录成功"。
 */
let gate: () => boolean = () => true;

/** 设置投递前的登录门（由 `editor-open-paths.ts` 接线；传 `() => true` 即恢复开放） */
export function setOpenPathsGate(next: () => boolean): void {
  gate = typeof next === 'function' ? next : () => true;
}

/** 因超出上限被丢弃的条数（只暴露数字，不含路径） */
let dropped = 0;

/** 只保留非空字符串（桥的另一端在 Python，按**不可信输入**处理） */
function normalize(paths: readonly string[]): string[] {
  if (!Array.isArray(paths)) return [];
  return paths.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

/** 记入「已投递」集合，并裁剪集合规模 */
function rememberDelivered(paths: readonly string[]): void {
  paths.forEach((path) => {
    if (delivered.has(path)) return;
    delivered.add(path);
    deliveredOrder.push(path);
  });
  if (deliveredOrder.length <= MAX_DELIVERED_KEYS) return;
  const overflow = deliveredOrder.length - MAX_DELIVERED_KEYS;
  const removed = deliveredOrder.slice(0, overflow);
  deliveredOrder = deliveredOrder.slice(overflow);
  removed.forEach((path) => delivered.delete(path));
}

/**
 * 入队（幂等 + 去重 + 上限保护）。
 *
 * @param paths 待打开路径（**不做路径拼接 / 不做存在性检查** —— 一切文件系统判定都在 Python 侧）
 * @param source 事件来源（`cli` = 首个实例的命令行；`single_instance` = 单实例转发）
 * @returns 实际入队条数（0 表示全部被去重 / 被上限拦下）
 */
export function enqueueOpenPaths(
  paths: readonly string[],
  source: OpenPathsPayload['source'] = 'cli',
): number {
  const accepted: string[] = [];
  normalize(paths).forEach((path) => {
    if (queued.has(path) || delivered.has(path)) return; // 去重：队内 + 已投递
    if (queued.size >= MAX_PENDING_OPEN_PATHS) {
      dropped += 1;
      return;
    }
    queued.add(path);
    pending.push(path);
    accepted.push(path);
  });
  if (accepted.length > 0) flushOpenPaths(source);
  return accepted.length;
}

/**
 * 消费队列：把积压路径**按序**交给已注册的消费者。
 *
 * - **首行判登录门**（v1.6.1 DS-20）：未登录时直接返回空数组，**队列与 `delivered` 都不动**，
 *   请求挂起到登录成功后再兑现（AC-64）；
 * - 无消费者时**保持队列不变**（等工作区挂载后再 flush，§7.5 ②）；
 * - 有消费者时一次性交付，并把这批路径移入 `delivered`（此后重复推送不再入队）。
 *
 * @returns 本次交付的路径数组（被门拦下 / 无消费者 / 空队列返回空数组）
 */
export function flushOpenPaths(source: OpenPathsPayload['source'] = 'cli'): string[] {
  if (!gate()) return [];
  if (handlers.size === 0 || pending.length === 0) return [];
  const batch = pending;
  pending = [];
  batch.forEach((path) => queued.delete(path));
  rememberDelivered(batch);
  handlers.forEach((handler) => handler(batch, source));
  return batch;
}

/**
 * 注册消费者（工作区挂载时调用）。
 *
 * 注册即尝试 flush：若「事件早于 UI」的积压还在，这里立刻补投（§7.5 ② 的判据）。
 * 注意 flush 首行有登录门 —— 未登录时注册不会把请求投出去，也不会丢（DS-20）。
 *
 * @returns 注销函数
 */
export function onOpenPaths(handler: OpenPathsHandler): () => void {
  handlers.add(handler);
  flushOpenPaths('cli');
  return () => {
    handlers.delete(handler);
  };
}

/** 当前积压条数（诊断用；**不含路径信息**） */
export function pendingOpenPaths(): number {
  return pending.length;
}

/** 因超出上限被丢弃的条数（诊断用；调用方可据此提示「部分文件未打开」） */
export function droppedOpenPaths(): number {
  return dropped;
}

/**
 * 清空队列与去重表（**仅供测试 / 会话级重置**；正常运行路径不调用）。
 *
 * 注意：同时清空 `delivered` —— 重置后 Python 侧的同一批重试会重新入队，这是预期语义
 * （「重置」= 当作全新会话）。
 */
export function resetOpenPathsQueue(): void {
  pending = [];
  queued.clear();
  delivered.clear();
  deliveredOrder = [];
  dropped = 0;
}

// ── 模块顶层副作用：**必须在 `bootstrap()` 的 await 之前**完成订阅（§7.5 ①）──
//
// `onBridgeEvent` 只要求 `window` 存在，不要求桥握手完成；`eventPushed` 的 handler 在握手时
// 才被接线，但**注册表是模块级的**，握手后事件照常到达（`desktop-bridge.ts:127`）。
scratchService.onEditorOpenPaths((payload) => {
  enqueueOpenPaths(payload.paths, payload.source);
});

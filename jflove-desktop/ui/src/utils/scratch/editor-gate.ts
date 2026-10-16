/**
 * 文本编辑器**准入判据单一真相**（设计 v1.6.1 §4.1 / DS-16 / 需求 F1-A）
 *
 * ## 一句话规则
 *
 * 文本编辑器**只在登录后可用**：`canUseEditor()` = 有会话 **且** 会话未过期。
 *
 * ## 为什么必须只有一处
 *
 * AC-63 要求「未登录时**零入口、零响应**」，而这句话必须同时成立于**四个互不相干的地方**：
 *
 * | # | 消费点 | 失效表现 |
 * |---|--------|----------|
 * | ① | `layouts/DesktopLayout.tsx` 的导航项渲染 | 侧栏露出「文本编辑器」入口 |
 * | ② | 快捷键运行时（`Ctrl+Alt+N` / `Ctrl+W` …） | 未登录也能呼出编辑器 / 弹窗 |
 * | ③ | `/editor` 路由守卫 | 手改 hash 就能进工作区 |
 * | ④ | 草稿还原的触发条件（`ScratchWorkspace`） | 未登录时把上次草稿全还原出来 |
 *
 * 四处分头写条件，历史经验是**必然漏一处**。所以本模块是唯一判据来源。
 *
 * ## 边界（C-3 零远端）
 *
 * - **不发起任何网络请求**（不逐次校验会话）：登录只作**准入**，登录后断网照常使用编辑器（AC-71）；
 * - **不新增定时器**：过期口径沿用 `stores/auth-store.ts:138` 的 `isTokenExpired()` 实现；
 * - 本模块**只**依赖 `stores/auth-store`（只读订阅）与 `utils/session`，不得 import 任何
 *   `components/**`（判据：`grep -n "components/" ui/src/utils/scratch/editor-gate.ts` = 0）。
 *
 * ## 安全（AGENTS.md §9.6）
 *
 * 渲染层不持有令牌；这里读到的只有"是否已登录"与"失效时间戳"这类**渲染态**信息。
 */

import { useAuthStore } from '../../stores/auth-store';
import { effectiveExpireAt, getTokenExpiresAt } from '../session';

/** 准入判据的输入快照（便于纯函数测试：四种会话组合） */
export interface EditorGateSnapshot {
  isLoggedIn: boolean;
  tokenExpired: boolean;
}

/**
 * 纯函数：编辑器可用 ⇔ 有会话 且 会话未过期。
 *
 * 不做网络请求、不读文件、无副作用 —— 四种组合（已登录/未登录 × 过期/未过期）可直接单测。
 */
export function canUseEditor(snapshot: EditorGateSnapshot): boolean {
  return snapshot.isLoggedIn && !snapshot.tokenExpired;
}

/**
 * 纯判据：会话是否已过期（**无副作用**）。
 *
 * 与 `stores/auth-store.ts:138` 的 `isTokenExpired()` 同口径（桌面端 `effectiveExpireAt`
 * 是恒等函数，真正生效的失效时间由 Python 侧算好），差别只在于**不触发登出清理**。
 * 之所以不用 `isTokenExpired()` 本身：它内部会 `set(LOGGED_OUT)`，而 React 的
 * `useSyncExternalStore` 快照函数必须是纯的（渲染期改状态会告警）。
 */
export function isSessionExpired(now: number = Date.now()): boolean {
  const effective = effectiveExpireAt(getTokenExpiresAt());
  return Boolean(effective) && now / 1000 >= (effective as number);
}

/**
 * 当前是否可用编辑器（**非 React 上下文**用：快捷键运行时、唤起队列的登录门）。
 *
 * ## 它为什么**可以**是非纯的（文档澄清，行为保持不变）
 *
 * 这里走 `isTokenExpired()` 而不是 `isSessionExpired()`：后者只判定、不清理；
 * 前者在判定"已过期"的同时还会 `set(LOGGED_OUT)`，并顺带 `authService.logout()` 做一次清理
 * （见 `stores/auth-store.ts:138-150`）。这次**顺带的清理在本函数里是允许的**，
 * 因为它的调用方全部落在**非渲染期**：
 *   ① 快捷键运行时 —— `utils/scratch/shortcut-runtime.ts` 的 `handleKeyDown()`（键盘事件回调）；
 *   ② 唤起队列的登录门 —— `utils/scratch/editor-open-paths.ts` 装给 `setOpenPathsGate` 的
 *      `currentEditorGate`，由 `flushOpenPaths()` 在**投递之前**调用（桥事件 / 登录态订阅回调）。
 * 两处都是"事件驱动的一次性判定"，不存在"同一次渲染里前后读到不同值"的撕裂；
 * 顺带清理还让过期会话在这一拍就退回登录态（口径与 `RequireAuth` 的守卫一致）。
 *
 * ## 渲染期**不得**用它：必须用纯函数 `isSessionExpired()`
 *
 * 渲染期（`useSyncExternalStore` 的快照函数、`useEditorGate` 这类 hook 的计算过程）
 * 只允许调用**无副作用**的 `isSessionExpired()`：React 要求快照函数是纯的，渲染期改 store 状态
 * 会告警并可能造成重复渲染 / 无限渲染。两者判据口径一致，差别只在"要不要顺手清理"。
 */
export function currentEditorGate(): boolean {
  const state = useAuthStore.getState();
  return canUseEditor({ isLoggedIn: state.isLoggedIn, tokenExpired: state.isTokenExpired() });
}

/**
 * React 订阅版：登录态变化（登录 / 登出 / 会话恢复）触发重渲染。
 *
 * 注：本 hook **不**为"到点自动失效"设定时器（C-3 不新增定时器）—— 失效后的界面切换
 * 由既有的路由守卫（每次导航都会调 `isTokenExpired()`）与登录态变化驱动。
 */
export function useEditorGate(): boolean {
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn);
  return canUseEditor({ isLoggedIn, tokenExpired: isSessionExpired() });
}

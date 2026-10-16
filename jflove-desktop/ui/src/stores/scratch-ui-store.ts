/**
 * 编辑器**界面态**store（v1.6.1 本轮新增，设计 §15.3.5 DS-45 / AC-75② / 闭环 BL-51）
 *
 * ## 为什么单独一个 store，而不是塞进 `stores/scratch-store.ts`
 *
 * 职责不同：`scratch-store` 持有**文档与会话态**（标签、激活项、脏标记、保存、关闭询问），
 * 本 store 只持有**一次性的界面请求**（"把某个面板打开"）。分开还有一个硬理由：
 * `scratch-store` 属于并行的另一批改动面（正文保活），把本机制塞进去会让两份改动互相冲突；
 * 而本文件是**零依赖叶子**（只 import `zustand`），谁都不会被它牵连。
 *
 * ## 为什么是"store 里的一次性请求"，而不是 `window` 自定义事件
 *
 * `view.toolbox` / `view.diff` 的接管方是**模块级常驻**的快捷键运行时
 * （`utils/scratch/shortcut-runtime.ts`，设计 13.2 U-8 的硬约束、AC-1 要求"任意路由页"可用），
 * 它**没有组件上下文**，拿不到 `ScratchToolbar` 的 `useState`。
 * 若改用 `window.dispatchEvent(new CustomEvent(...))`：**没有监听者时事件被静默丢弃**
 * （DOM 事件不为"将来的监听者"缓存）⇒ 未挂载时按 `Ctrl+K` 依然"没反应"，
 * 正是 BACKLOG BL-51 的形态、也正是 AC-75② 的失败条件（"任意一项配了但按了没反应即失败"）。
 * 存在 store 里的请求则**不会丢**：面板挂载后一读即得。
 *
 * ⚠ 与本版既有的 `pendingCloseId`（`scratch-store.ts`，"有个询问要弹"）是**同一模式、
 * 不同字段**：那个表达"要问"，这个表达"要开"；语义、消费方、清位时机都不同，**不要合并**。
 *
 * ## 生命周期（谁投递、谁消费）
 *
 * ```
 * 快捷键运行时（无组件上下文）          工具栏 / 面板（有组件上下文）
 *   requestPanel(kind, tabId)   ──────►  读 panelRequest
 *   （nonce 单调递增）                     consumePanelRequest(nonce) ⇒ true 才执行
 * ```
 *
 * `nonce` 保证"同一个 `kind`+`tabId` 连按两次也各触发一次"；`consumePanelRequest` 只在
 * `nonce` 相等时清位并返回 `true`，于是重复渲染 / 重复 effect 不会重复执行。
 */

import { create } from 'zustand';

/** 可被外部请求打开的面板类型（工具箱弹框 / 两段文本对比覆盖层） */
export type ScratchPanelKind = 'toolbox' | 'diff';

/** 一次面板请求（`nonce` 单调递增 ⇒ 同一 `kind`+`tabId` 连按两次也会各触发一次） */
export interface PanelRequest {
  kind: ScratchPanelKind;
  /** 目标标签 id（由请求方解析：面板内 ⇒ 该面板；否则 ⇒ 当前激活项；都没有 ⇒ 新建后取其 id） */
  tabId: string;
  /** 单调递增序号，用于"消费一次即失效"（防重渲染重复执行） */
  nonce: number;
}

interface ScratchUiState {
  /** 待处理的**一次性**面板请求；`null` = 无 */
  panelRequest: PanelRequest | null;
  /** 投递一次面板请求（非激活 / 未挂载的标签也允许 —— 请求留在 store，等面板挂载后消费） */
  requestPanel: (kind: ScratchPanelKind, tabId: string) => void;
}

let panelNonce = 0;

/**
 * 界面态 store。
 *
 * 消费（清位）**不走 action**，而用模块级函数 `consumePanelRequest(nonce)` —— 理由：
 * 消费点在 React effect 里，`getState()` 之外不需要订阅动作本身；把它做成 action 会让
 * "谁清了位"多一处订阅面，反而容易误用。**判据**：`consumePanelRequest` 对 `nonce`
 * 不匹配的调用返回 `false` 且**不清位**（那是别人的请求）。
 */
export const useScratchUiStore = create<ScratchUiState>((set) => ({
  panelRequest: null,
  requestPanel: (kind, tabId) => {
    panelNonce += 1;
    set({ panelRequest: { kind, tabId, nonce: panelNonce } });
  },
}));

/**
 * 消费一次面板请求（**幂等**）。
 *
 * @returns `true` = 这次请求归本次调用者处理（已清位，请执行打开动作）；
 *          `false` = 无请求 / `nonce` 不匹配（已被处理过，或那是另一个请求）⇒ 调用方**直接返回**。
 *
 * ⚠ 清位与"是否执行"必须在**同一次同步调用**里完成（先 `true` 再执行），不能"先执行再清位"：
 * 执行若触发重渲染，会再进一次 effect，那时 `nonce` 已不匹配 ⇒ 天然防重。
 */
export function consumePanelRequest(nonce: number): boolean {
  const current = useScratchUiStore.getState().panelRequest;
  if (!current || current.nonce !== nonce) return false;
  useScratchUiStore.setState({ panelRequest: null });
  return true;
}

/** 供测试与"取消请求"路径使用的复位（不改变 `nonce` 的单调性） */
export function clearPanelRequest(): void {
  useScratchUiStore.setState({ panelRequest: null });
}

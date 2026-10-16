/**
 * 标签正文是否**已失效**的判据 —— 设计 v1.6.1 §15.5.5 / 需求 AC-77⑥（DS-47）
 *
 * ## 一句话定义（可直接写成断言）
 *
 * > `contentInvalid(tab) := tab.mounted === true  ∧  getScratchEditorSession(tab.id) === null
 * >                       ∧  hasInitialText(tab.id) === false`
 * >
 * > 即："**该标签在本会话里被挂载过（`mounted`），但既没有活着的 CM6 会话，也没有待取的初始正文**"。
 *
 * ## 它解决什么（F5 的兜底半边）
 *
 * 路由切换 ⇒ 工作区卸载 ⇒ `ScratchEditorHost` 的 cleanup 走到 `view.destroy()`
 * （`ScratchEditorHost.tsx:543`）⇒ 正文的唯一持有者消亡。主修复是**卸载前快照**
 * （`snapshotInitialText`，§15.5.2/15.5.3）；本模块是**读侧兜底**：当某个标签"实例走了、
 * 快照也没接上"（例如宿主因内核加载失败 / 视口未相交而从未真正建实例）时，
 * 让 `ScratchWorkspace` 的 hydrate 门**允许读一次磁盘草稿**，而不是把用户丢在空白正文上。
 *
 * ## 为什么必须同时有 ② 和 ③（反例，逐条）
 *
 * - **只看 ①（`mounted`）**：路由卸载的**正常**路径（快照已写）会被误判为失效 ⇒ 多读一次磁盘
 *   （无害但多余，且会让 `draftList` 的调用计数断言失稳）；
 * - **只看 ②（无会话）**：`hydrateFromDrafts` 还原出的标签（刻意 `mounted: false`，§4.8）会被误判
 *   ⇒ 每次进工作区都判成"要读盘"，而 `hydratedThisSession` 恰恰要防止这一点（DS-34）。
 *
 * ## 为什么"从未挂载"（`mounted === false`）**不算**失效
 *
 * 这类标签的正文**本来就该从草稿库来**（懒挂载：`ScratchWorkspace` 的渲染集合是
 * `mounted || active`，`setMounted` 只在"激活 + 相交"时置真）⇒ 判为"有效"是**正确**的
 * （避免无谓读盘），它的正文在第一次激活时由 hydrate 提供。
 *
 * ## 为什么落点是独立叶子（而不是 `stores/scratch-store.ts`）
 *
 * 本判据需要读 `SESSIONS`（`getScratchEditorSession`），而 `ScratchEditorHost.tsx` 已经 import
 * `scratch-store` ⇒ 写进 store 会形成**循环 import**。因此放这里：
 *   - 它 import `getScratchEditorSession`（`ScratchEditorHost`）与 `hasInitialText`（`scratch-store`）；
 *   - **只有 `ScratchWorkspace.tsx` import 它** —— 这是"单向扇入"，不构成环；
 *   - 判据（Phase 5 可复核）：`grep -rn "editor-liveness" jflove-desktop/ui/src` **只**应命中
 *     本文件与 `ScratchWorkspace.tsx`（防后人反向 import 造成环，§3.4 第 2 项 R2 教训的延续）。
 *
 * ## 安全（AGENTS.md §9.14）
 *
 * 纯查询：不读文件、不发请求、**零日志**、不接触任何用户内容（只问"有没有"）。
 */

import { getScratchEditorSession } from '../../components/scratch/ScratchEditorHost';
import { hasInitialText } from '../../stores/scratch-store';
import type { ScratchTab } from '../../types/scratch';

/**
 * 该标签的正文是否**已失效**（AC-77 的 hydrate 门判据 / 设计 §15.5.5）。
 *
 * 三条同时成立才算失效（逐条理由见文件头）：
 *   ① `tab.mounted === true` —— 本会话里被激活/挂载过（`createTab` 默认 true；
 *      `hydrateFromDrafts` 刻意 false）；
 *   ② 画面上没有活着的 CM6 会话（`SESSIONS` 表）—— 路由卸载后 `view.destroy()` 会注销；
 *   ③ 也没有待取的初始正文快照 —— 即"实例已经走了、且快照没接上"。
 */
export function isTabContentInvalid(tab: ScratchTab): boolean {
  if (!tab.mounted) return false;
  if (getScratchEditorSession(tab.id) !== null) return false;
  return !hasInitialText(tab.id);
}

/**
 * 批量版本：一次算出"有没有正文已失效的标签"（`ScratchWorkspace` 的 hydrate 门用）。
 *
 * 纯函数 + 模块级表查询 ⇒ 不新增 zustand 字段、不触发额外重渲染。
 */
export function pickInvalidTabs(tabs: readonly ScratchTab[]): ScratchTab[] {
  return tabs.filter(isTabContentInvalid);
}

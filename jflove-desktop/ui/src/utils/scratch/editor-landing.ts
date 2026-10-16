/**
 * 「登录后的落点」**唯一真相**（设计 v1.6.1 §4.4 / AC-64 ② / 缺陷 R2 的修复）
 *
 * # 它装什么
 *
 * 1. `EDITOR_ROUTE` —— 编辑器工作区路由的**唯一字面量**；
 * 2. 本次登录的落点标记（`revealOnThisLogin`）+ 读 / 置 / 复位三个函数。
 *
 * # 为什么它必须是一个**零依赖的叶子模块**（缺陷 R2）
 *
 * 修复前，落点判据与 `EDITOR_ROUTE` 都住在 `./editor-open-paths.ts`，而那个模块要
 * `router.navigate()` ⇒ 必须 import `config/routes`；`config/routes.tsx` 又 import
 * `pages/LoginPage.tsx`；`LoginPage` 为了取判据再 import 回来 ⇒ 形成
 * `LoginPage → editor-open-paths → config/routes → LoginPage` 的**模块环**。
 *
 * 环在运行期确实没炸（两侧都不在模块顶层使用对方的导出），但它把「一条只能靠人工纪律
 * 维持的隐式契约」留在了代码里：任何人在任意一侧的**模块顶层**加一行求值，
 * 都可能踩到 TDZ / 半初始化导出。所以本版按裁决把它**结构性**断掉 ——
 * 判据搬到这个**谁都不依赖**的叶子模块：
 *
 * ```
 * LoginPage ──┐
 *             ├──▶ editor-landing.ts（叶子：零 import）
 * editor-open-paths ──▶ …（本模块不再被任何人反向依赖）
 * ```
 *
 * ⚠⚠ **本文件必须保持零 import**：一旦这里 import 了任何东西（尤其 `config/routes`、
 * `pages/LoginPage` 或 `./editor-open-paths`），`LoginPage → … → config/routes → LoginPage`
 * 的环就会重新出现。防环回归判据（静态扫描本文件源码，断言不含任何 import 语句）见
 * `./__tests__/editor-landing.test.ts` 的用例 ②。
 *
 * 依据：`文档记录/设计文档/v1.6.1.md` §4.4（唤起 → 登录门 → 打开并揭示）、
 * `文档记录/需求文档/v1.6.1.md:469`（AC-64 ②）。
 */

/**
 * 编辑器工作区路由（`config/routes.tsx` 内挂在 `RequireAuth → AppLayout` 之下）。
 *
 * **导出**给「登录后的落点」判据的消费方用（`pages/LoginPage.tsx` 的落点 effect、
 * `./editor-open-paths.ts` 的导航、`./shortcut-runtime.ts` 的工作区揭示），
 * 避免各处各写一份字面量 —— 落点只有一个（AC-64 ②），路由常量也只能有一份（缺陷 R4）。
 */
export const EDITOR_ROUTE = '/editor';

/**
 * 「本次登录是否必须把落点定在编辑器」的**唯一判据本体**（AC-64 ② / 缺陷 R1 的回修）。
 *
 * ## 为什么必须是模块级标记（而不是谁去读队列）
 *
 * 登录成功时，`./editor-open-paths.ts` 的登录订阅回调（zustand `subscribe`，**同步**执行）
 * 会先一步 `flushOpenPaths()` 把挂起队列清空；`LoginPage` 的落点 effect 要等 React 提交后
 * 才跑，那时 `pendingOpenPaths()` 必然已是 0 —— 直接读队列是「看起来能读、实则永远读到 0」。
 * 所以判据只能由**唯一知道"刚刚兑现了一批挂起请求"的那一方**写下，`LoginPage` 只消费。
 *
 * ## 时序（谁写谁读）
 *
 * ```
 * 唤起（未登录）→ 入队挂起
 *    ↓ 登录成功（门翻真 + 队列非空）
 * editor-open-paths 的订阅回调：markEditorRevealOnLogin() → navigate(EDITOR_ROUTE) → flushOpenPaths()
 *    ↓
 * LoginPage 的落点 effect：editorRevealPendingOnLogin() ? '/editor' : '/'
 * 门翻假（登出 / 会话失效）/ 接线卸载：clearEditorRevealOnLogin()（绝不让上一次唤起污染下一次登录）
 * ```
 *
 * ⚠ 置位必须发生在 `flushOpenPaths()` **之前**（顺序理由见 `./editor-open-paths.ts` 回调里的注释）。
 */
let revealOnThisLogin = false;

/**
 * 本次登录是否必须把落点定在编辑器（`pages/LoginPage.tsx` 的落点判据）。
 *
 * @returns `true` = 登录完成即揭示编辑器（AC-64 ②：外部唤起的文件还在等）；`false` = 按常规回首页
 */
export function editorRevealPendingOnLogin(): boolean {
  return revealOnThisLogin;
}

/**
 * 标记「本次登录的落点 = 编辑器」。
 *
 * 只在 `./editor-open-paths.ts` 的登录订阅回调里、**且**门为真、**且**队列非空时调用
 * —— 那三件事同时成立才等于「有一批外部唤起正等着被揭示」。
 */
export function markEditorRevealOnLogin(): void {
  revealOnThisLogin = true;
}

/**
 * 复位落点判据（**唯一的清除入口**）。
 *
 * 两个调用点，语义都是"这次登录的唤起已经不再是落点依据了"：
 * 1. 登录门翻回假（登出 / 会话失效）—— 防止上一次唤起污染下一次普通登录；
 * 2. 接线卸载（只给测试用）—— 判据与接线一起退场，避免用例之间串味。
 */
export function clearEditorRevealOnLogin(): void {
  revealOnThisLogin = false;
}

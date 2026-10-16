/**
 * **异常 → 用户可见文案**的唯一真相（叶子模块）。v1.6.1 Phase 6 收尾轮缺陷 **L-7**。
 *
 * ## 这条债原来长什么样
 *
 * `describeError` 曾在五个文件里**各写一份**（Phase 5 审查记为 L-7「×5」）：
 *
 * | # | 原定义处 | 取 `message` 的判据 | 兜底文案 |
 * |---|----------|---------------------|----------|
 * | ① | `utils/scratch/shortcut-runtime.ts:75` | `error instanceof Error && error.message` | `'操作失败'` |
 * | ② | `stores/shortcut-store.ts:83` | 同上 | `'快捷键偏好操作失败'` |
 * | ③ | `stores/scratch-store.ts:118` | 同上 | `'保存失败'` |
 * | ④ | `components/scratch/FileAssocSettings.tsx:37` | 同上 | `'文件关联操作失败'` |
 * | ⑤ | `components/scratch/ScratchWorkspace.tsx:46` | 同上 | `'操作失败'` |
 *
 * 五份的**取 `message` 判据逐字相同**，差别只有兜底文案 ⇒
 * 抽成"同一算法 + 兜底参数"，**四组不同的兜底文案一个字都不改**（见下方注释）。
 *
 * ## 为什么必须放在叶子模块（而不是某个 store / 组件里）
 *
 * 本模块有出边，就等于给 `stores/**` 与 `components/**` 之间造出**新的依赖方向**：
 * `FileAssocSettings.tsx` 若从 `stores/**` 取判据，就形成
 * `components → stores` 的新边，而 `shortcut-runtime.ts`（utils）本来就已经被
 * `stores/**` 引用 —— 环随时能重新长回来（同 `utils/scratch/editor-landing.ts`
 * 消除 R2 模块环的处置口径）。
 *
 * 因此本文件**刻意零 import**：
 * 既不 import `stores/**`、`components/**`、`config/routes`，也不 import 任何服务层，
 * 只做「`unknown` → 可读中文文案」的**纯函数**。叶子地位由静态扫描用例守卫。
 *
 * ## 安全口径（`AGENTS.md` §9.13）
 *
 * 本模块只**透传**上游 message，自己**不拼接、不补全路径**：
 * 调用方（Python 桥 / store / 服务层）已保证文案不含完整路径与文件内容片段。
 * 兜底文案一律是**与具体文件无关的通用中文短句**，天然不含路径。
 * 单测里有「文案不得含完整路径 / 文件内容片段」的守卫用例。
 */

/**
 * 「异常 → 文案」的兜底值 —— **逐字对齐抽取前五处原定义**，不得改写。
 *
 * 这些都是**直接展示给用户**的文案，任何措辞变化都是可见的行为变更，因此集中在这里
 * 标注出处，而不是在调用点各写一遍字面量（原调用点仍用自己那份常量）。
 */
export const ERROR_FALLBACK = {
  /** 原 `utils/scratch/shortcut-runtime.ts` 与 `components/scratch/ScratchWorkspace.tsx` 的兜底 */
  OPERATION: '操作失败',
  /** 原 `stores/shortcut-store.ts` 的兜底（快捷键偏好读写） */
  SHORTCUT: '快捷键偏好操作失败',
  /** 原 `stores/scratch-store.ts` 的兜底（写盘失败） */
  SAVE: '保存失败',
  /** 原 `components/scratch/FileAssocSettings.tsx` 的兜底（文件关联读写） */
  FILE_ASSOC: '文件关联操作失败',
} as const;

/**
 * 把任意抛出物转成可展示文案：**是 `Error` 且有非空 `message` 就原样用它，否则用调用方给的兜底**。
 *
 * 判据与抽取前的五份逐字一致：
 *
 * 1. `error instanceof Error` —— 跨 realm 的普通对象**不算**（原实现同样不算，未改）；
 * 2. `error.message` 必须**非空** —— 空串 `''` 照样落到兜底（原实现同样如此）；
 * 3. 兜底为**必填**形参：抽取前五处的兜底各不相同，收成可选参数就会引入一个
 *    "默认兜底"，等于悄悄改掉其中四处的用户可见文案。
 *
 * @param error 任意抛出物（`unknown`：`catch` 绑定的静态类型）
 * @param fallback 取不到 `message` 时展示的文案（由调用方按场景提供，见 `ERROR_FALLBACK`）
 */
export function describeErrorWithFallback(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

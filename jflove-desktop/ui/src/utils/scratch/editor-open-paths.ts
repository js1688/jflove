/**
 * 「外部唤起 → 登录门 → 打开并揭示」的**唯一接线点**（设计 v1.6.1 §4.4 / DS-20 / AC-64）。
 *
 * ## 它解决什么
 *
 * 用户双击关联文件 / 命令行传路径时，Python 侧会发 `editor.openPaths`
 * （`web_shell.py` → 桥事件 → `utils/scratch/open-paths-queue.ts`）。v1.6.1 起编辑器
 * **必须先登录**（AC-63），于是这条链必须变成：
 *
 * ```
 * 未登录：入队挂起（tabs 0、无 detectAndRead、无任何持久化产物）
 *    ↓ 登录成功
 * 已登录：导航到 /editor → detectAndRead（Python 侧类型校验先行）→ 打开 + 去重聚焦 + 激活
 * ```
 *
 * ## 三件必须一起做的事（`:36-60`）
 *
 * | # | 动作 | 为什么必须在这一层 |
 * |---|------|--------------------|
 * | ① | `setOpenPathsGate(currentEditorGate)` | 门放在**投递之前**（`flushOpenPaths` 首行），否则路径会被记入 `delivered` 而**永久丢失**（设计 §4.4 的粗体说明） |
 * | ② | `onOpenPaths(...)` 在**模块顶层**注册 | 消费者必须早于登录存在：未登录时队列要有人"接住"，登录后才有 flush 的落点 |
 * | ③ | `useAuthStore.subscribe(...)` 兑现挂起请求 | 登录是**唯一**能把门从 false 翻到 true 的事件（AC-64 ②） |
 *
 * ## 第四件：登录后的落点判据（v1.6.1 回修 · 缺陷 R1 / R2）
 *
 * ③ 的回调除了导航，还负责把「本次登录的落点 = 编辑器」的标记**置位** —— 那是「登录后落点」
 * 的**唯一判据**，由 `pages/LoginPage.tsx` 的「已登录则跳转」effect 消费。
 * 修复前落点有两个所有者（本回调的 `/editor` 与 LoginPage 的 `/`），后者后发起并覆盖前者，
 * 表现为「未登录唤起 → 登录成功后停在 `#/files`」（AC-64 ② 失败）。
 *
 * > **判据本体住在叶子模块 `./editor-landing.ts`**（缺陷 R2 的修复）：标记的读 / 置 / 复位
 * > （`editorRevealPendingOnLogin` / `markEditorRevealOnLogin` / `clearEditorRevealOnLogin`）
 * > 全在那里，本文件只**调用**它们。于是 `pages/LoginPage.tsx` 取判据时 import 的是
 * > **零依赖的叶子**，不再反向依赖本模块 ⇒
 * > `LoginPage → editor-open-paths → config/routes → LoginPage` 的模块环**消失了**。
 * > 本模块对 `config/routes` 的 import **保持不变**：那是既有边，本身不构成环
 * > —— 环只由 `LoginPage` 指回来造成。
 *
 * ## 前置（顺序有硬性要求）
 *
 * 必须先 `setOpenPathsGate` 再 `onOpenPaths` —— `onOpenPaths` 注册时会立刻尝试一次
 * `flushOpenPaths()`，此时门必须已经是真判据，否则会把未登录的请求投出去。
 *
 * ## 谁 import 它
 *
 * `App.tsx` 顶层静态 `import './utils/scratch/editor-open-paths'`：静态 import 在
 * **首帧之前**求值，保证「启动期事件早于 UI」的积压有消费者（§7.5 ② 的双保险之一）。
 *
 * ## 边界
 *
 * - **零远端**：只走桥方法 `editor.detectAndRead`（C-3）；
 * - **不持久化**：挂起队列是纯内存（AC-64 ⑤ / U-2：放弃登录后退出，不留任何产物）；
 * - 渲染层不读文件字节（C-5）：正文由 `detectAndRead` 交付，本文件只做"转交 + 揭示"。
 */

import { router } from '../../config/routes';
import { scratchService } from '../../services/scratch-service';
import { useAuthStore } from '../../stores/auth-store';
import { useScratchStore } from '../../stores/scratch-store';
import { DETECT_AND_READ_MAX_PATHS } from '../../types/scratch';
import { canUseEditor, currentEditorGate } from './editor-gate';
import {
  EDITOR_ROUTE,
  clearEditorRevealOnLogin,
  markEditorRevealOnLogin,
} from './editor-landing';
import { flushOpenPaths, onOpenPaths, pendingOpenPaths, setOpenPathsGate } from './open-paths-queue';

/** 未登录挂起的条数是否还有（仅供测试与调试观察，不对外暴露路径） */
export { pendingOpenPaths };

/** 按固定大小切批（桥的入参上限是 `DETECT_AND_READ_MAX_PATHS = 64`） */
function chunkPaths(paths: readonly string[], size: number): string[][] {
  const batches: string[][] = [];
  for (let offset = 0; offset < paths.length; offset += size) {
    batches.push(paths.slice(offset, offset + size));
  }
  return batches;
}

/** 过滤空串 + 同批去重（保序）—— 与 v1.6.0 的消费者口径一致 */
function normalizePaths(paths: readonly string[]): string[] {
  return Array.from(new Set(paths.filter((item) => typeof item === 'string' && item.trim().length > 0)));
}

/**
 * 打开一批路径并**揭示编辑器**：先切到 `/editor`，再逐批 `detectAndRead` 并落成标签页。
 *
 * 「切菜单」先于「读文件」：用户点了文件之后应当立刻看到编辑器界面（AC-64 ② 的观测点之一），
 * 即使文件读取还在进行。
 *
 * 单条失败**不抛**（`items[].ok:false` → `openOrFocus` 写 `lastError` → 工作区的提示订阅统一弹出）；
 * 整批失败（桥未就绪 / 桥抛错）才 `catch`，同样落 `lastError` —— 不能让用户"双击了文件却毫无反应"。
 */
export async function openPathsAndReveal(paths: readonly string[]): Promise<void> {
  const unique = normalizePaths(paths);
  if (unique.length === 0) return;
  router.navigate(EDITOR_ROUTE);
  for (const batch of chunkPaths(unique, DETECT_AND_READ_MAX_PATHS)) {
    try {
      const result = await scratchService.detectAndRead(batch);
      const store = useScratchStore.getState();
      result.items.forEach((item) => {
        store.openOrFocus(item);
      });
    } catch (error) {
      useScratchStore.getState().setError(
        error instanceof Error && error.message ? error.message : '打开文件失败',
      );
    }
  }
}

/** 接线是否已装（模块会被 `App.tsx` 与测试同时 import，必须幂等） */
let installed = false;

/**
 * 「本次登录是否必须把落点定在编辑器」的判据**为什么长这样**（AC-64 ② / 缺陷 R1 回修）
 *
 * ⚠ 判据**本体**（模块级标记 `revealOnThisLogin` 与它的读 / 置 / 复位）住在**叶子模块**
 * `./editor-landing.ts`（缺陷 R2：`LoginPage → 本模块 → config/routes → LoginPage`
 * 的模块环靠"判据搬进零依赖叶子"断掉）。下面这段"为什么"依然成立，只是执行者换成了那个模块。
 *
 * ## 为什么需要它（缺陷 R1 的根因）
 *
 * 修复前「登录后的落点」有**两个所有者**，互相打架：
 *   ① 本文件 `installEditorOpenPaths()` 的登录订阅回调 → `router.navigate(EDITOR_ROUTE)`；
 *   ② `pages/LoginPage.tsx` 的「已登录则跳转首页」effect → `navigate('/', { replace: true })`。
 * zustand 的 `subscribe` 监听器在 `set()` 时**同步**先跑（于是队列已被 `flushOpenPaths()` 清空），
 * LoginPage 的 effect 要等 React 提交后才跑；而 React Router 的数据路由 `navigate` 是**异步**的
 * ⇒ **后发起的 `/` 覆盖了先发起的 `/editor`**，用户最终停在 `#/files`（AC-64 ② 失败）。
 *
 * ## 为什么标记必须在 `flushOpenPaths()` **之前**置位
 *
 * `flushOpenPaths()` 会把本批路径记入 `delivered` 并**清空 `pending`**（`open-paths-queue.ts:146-149`）。
 * 所以「先 flush、再问 `pendingOpenPaths() > 0`」永远是假 —— 顺序反了标记就永远置不上，
 * LoginPage 会退回 `/`，缺陷原样复现。判据必须在「队列仍被占用」的那一刻落下。
 *
 * ## 为什么不能让 LoginPage 自己读 `pendingOpenPaths()`
 *
 * 同一枚硬币的另一面：LoginPage 的 effect 跑在订阅回调**之后**，那时队列必然已是 0
 * —— 「看起来能读、实则永远读到 0」的陷阱。落点判据只能由**唯一知道"刚刚兑现了一批挂起请求"
 * 的那一方**（本模块的订阅回调）写下，LoginPage 只**消费**它。
 */

/**
 * 装上「唤起 → 登录门 → 打开」的接线（幂等；模块顶层自动调用一次）。
 *
 * @returns 卸载函数（只给测试用：清掉消费者与登录态订阅，便于用例之间互不串味）
 */
export function installEditorOpenPaths(): () => void {
  if (installed) return () => undefined;
  installed = true;

  // ① 门 = 准入单一真相 —— 必须在 ② 之前，因为 onOpenPaths 注册时会立刻 flush 一次
  setOpenPathsGate(currentEditorGate);

  // ② 消费者（早于登录存在：未登录时由门拦下，队列保持挂起）
  const disposeConsumer = onOpenPaths((paths) => {
    void openPathsAndReveal(paths);
  });

  // ③ 登录成功 → 兑现挂起请求（门翻转的唯一入口；落点判据也在这里落定）
  const unsubscribeAuth = useAuthStore.subscribe((state) => {
    const gate = canUseEditor({
      isLoggedIn: state.isLoggedIn,
      tokenExpired: state.isTokenExpired(),
    });
    if (!gate) {
      // 门翻回假（登出 / 会话失效）⇒ 落点判据一并复位：绝不让"上一次唤起"污染下一次普通登录
      clearEditorRevealOnLogin();
      return;
    }
    if (pendingOpenPaths() === 0) return;
    // ⚠ 顺序是硬要求：必须在 `flushOpenPaths()` **之前**置位 —— flush 会清空队列，
    // 之后再判 `pendingOpenPaths() > 0` 就永远是假（理由见 `revealOnThisLogin` 的注释）。
    markEditorRevealOnLogin();
    // 会话自动恢复（登录页根本没挂载）时没有 LoginPage 的 effect 来消费判据，这里照旧直接揭示；
    // 登录页在场时两处导航**目标一致**（都由同一个判据决定），不再互相覆盖（缺陷 R1 的回修点）。
    router.navigate(EDITOR_ROUTE);
    flushOpenPaths();
  });

  return () => {
    installed = false;
    // 卸载 = 会话级重置（只给测试用）：判据与接线一起退场，避免用例之间串味
    clearEditorRevealOnLogin();
    disposeConsumer();
    unsubscribeAuth();
    setOpenPathsGate(() => true);
  };
}

// 模块顶层接线：App.tsx 静态 import ⇒ 求值早于首帧（§7.5 ②）
installEditorOpenPaths();

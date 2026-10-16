/**
 * **编辑器快捷键运行时**（模块级常驻，设计 v1.6.1 §4.7 / §4.7.1 / 13.2 U-8 / DS-24）。
 *
 * ## 为什么必须常驻（而不是挂在工作区组件里）
 *
 * v1.6.1 的 **AC-1** 原文是：*"**已登录**状态下，在**任意路由页**按「新建标签页」快捷键 →
 * ① 界面切到**编辑器工作区**；② 出现 1 个新的标签页"*。
 * 若监听器挂在 `/editor` 页面组件里（v1.6.0 的 `ScratchPaneLayer` 就是"挂在外层"才满足旧 AC-1），
 * 那么用户在「文件管理」页按 `Ctrl+Alt+N` 时监听器根本不存在 ⇒ **AC-1 直接失败**。
 * 所以本模块在 `App.tsx` 里被**静态 import**，`installEditorShortcutRuntime()` 在**模块顶层**调用一次，
 * 与「哪个路由在渲染」完全无关（设计 13.2 U-8 把这条写成硬约束）。
 *
 * ## 覆盖面（18 项里的 9 项，与 v1.6.0 的 `APP_SCOPE_SHORTCUTS` 逐项对齐 + 本轮新增 2 项）
 *
 * | id | 默认键 | 本模块做什么 |
 * |----|--------|-------------|
 * | `pane.new` | `Ctrl+N`（v1.6.1 本轮由 `Ctrl+Alt+N` 改） | 不在工作区 ⇒ 先 `router.navigate('/editor')`，再 `createTab()` |
 * | `pane.close` | `Ctrl+W` | 面板聚焦才生效 ⇒ `store.requestCloseTab(id)`（脏标签由工作区弹询问） |
 * | `pane.next` / `pane.prev` | `Ctrl+PageDown` / `Ctrl+PageUp`（本轮改） | 不在工作区 ⇒ 先切过去，再 `nextTab()` / `prevTab()` |
 * | `file.open` | `Ctrl+O`（本轮改） | 原生对话框选文件 ⇒ 复用 `openPathsAndReveal()`（内部已含切路由 + 去重聚焦） |
 * | `file.save` | `Ctrl+S` | 面板聚焦才生效 ⇒ `store.savePane(id)`（AC-36/37 语义） |
 * | `file.saveAs` | `Ctrl+Shift+S` | 同上，`{ forceDialog: true }` |
 * | `view.toolbox` | `Ctrl+K`（本轮改） | **本轮新增接管**（闭环 BL-51）：切工作区 → 定目标标签 → `requestPanel('toolbox', id)` |
 * | `view.diff` | `Ctrl+Shift+D`（本轮改） | **本轮新增接管**：同上，`requestPanel('diff', id)` |
 *
 * **不在本模块**：`pane.togglePreview` / `edit.*` / `view.zoom*` / `view.toggleWordWrap`
 * ⇒ 由 CM6 keymap 与面板工具栏承担（§4.7.1 的"编辑面板聚焦"列）。
 *
 * > 为什么 `view.toolbox` / `view.diff` 用 **store 里的一次性请求**而不是 `window` 自定义事件：
 * > 事件在**没有监听者**时被静默丢弃 ⇒ 未挂载时按 `Ctrl+K` 依然"没反应"，
 * > 正是 AC-75② 的失败条件（"配了但按了没反应即失败"）。请求存在 store 里则不会丢，
 * > 面板挂载后一读即得（设计 §15.3.5 DS-45 的候选方案对照）。落点见
 * > `stores/scratch-ui-store.ts`（**刻意不放** `scratch-store`：职责不同，且那是另一批的改动面）。
 *
 * ## 三道前置（顺序不可换）
 *
 * 1. **准入**：`canUseEditor()` 为假 ⇒ **立即返回，且绝不 `preventDefault`**（AC-63 ②：
 *    未登录时"不响应且不报错"，不能吞掉用户的按键）；
 * 2. **窗口活跃**：`document.hasFocus() && !document.hidden`（AC-8：失焦/最小化期间零编辑器行为）；
 * 3. **让路**：`document.body.dataset.jfShortcutCapture` 有值 ⇒ 快捷键设置对话框正在录制键位，直接放行。
 *
 * 命中后按 §4.2.3 规则 2 接管：`preventDefault() + stopPropagation()`，避免宿主默认动作（`Ctrl+W`
 * 在新版 QtWebEngine 上可能触发 WebAction）与 CM6 双触发。
 */

import { router } from '../../config/routes';
import { keyboardEventToShortcut } from '../../config/shortcuts';
import type { ShortcutId } from '../../config/shortcuts';
import { scratchService } from '../../services/scratch-service';
import { useAuthStore } from '../../stores/auth-store';
import { useScratchStore } from '../../stores/scratch-store';
import { useScratchUiStore } from '../../stores/scratch-ui-store';
import { useShortcutStore } from '../../stores/shortcut-store';
import { toast } from '../../components/ui';
// 缺陷 L-7：`describeError` 原先在 5 个文件里各写一份，现统一取叶子模块（本模块零出边）
import { ERROR_FALLBACK, describeErrorWithFallback } from '../error-text';
import { canUseEditor } from './editor-gate';
// 缺陷 R4：路由常量只有一个真相（`./editor-landing.ts`），本模块不再自带 `/editor` 私有副本
import { EDITOR_ROUTE } from './editor-landing';
import { openPathsAndReveal } from './editor-open-paths';

/** 本模块接管的 9 项（= v1.6.0 `APP_SCOPE_SHORTCUTS` 去掉两项已作废的平铺/层叠 + 本轮新增 2 项） */
const RUNTIME_SHORTCUTS: readonly ShortcutId[] = [
  'pane.new',
  'pane.close',
  'pane.next',
  'pane.prev',
  'file.open',
  'file.save',
  'file.saveAs',
  // ★ v1.6.1 本轮新增：闭环 BL-51（`Ctrl+K` 有接管方），并按 AC-75② 让 `Ctrl+Shift+D` 真实生效
  'view.toolbox',
  'view.diff',
];

/** 需要「事件目标位于某个编辑面板 DOM 内」的项（§4.7 的"编辑面板聚焦"作用域） */
const PANEL_SCOPE_SHORTCUTS: readonly ShortcutId[] = ['pane.close', 'file.save', 'file.saveAs'];

/** 事件目标所在编辑面板的标签 id（不在任何面板内 ⇒ null） */
function panelTabIdOf(target: EventTarget | null): string | null {
  if (!(target instanceof HTMLElement)) return null;
  const host = target.closest('[data-editor-panel]');
  return host instanceof HTMLElement ? (host.dataset.editorPanel ?? null) : null;
}

/** 异常 → 用户可见文案（不含完整路径；Python / store 侧文案已保证，§7.4） */
function describeOpenFileError(error: unknown): string {
  return describeErrorWithFallback(error, ERROR_FALLBACK.OPERATION);
}

/** 切到编辑器工作区（已在工作区则什么都不做，避免无谓的历史记录） */
function revealWorkspace(): void {
  if (router.state.location.pathname === EDITOR_ROUTE) return;
  void router.navigate(EDITOR_ROUTE);
}

/** `file.open`：原生对话框选文件 → 交给唯一唤起链路（内部含切路由 / 去重聚焦 / 错误提示） */
async function pickAndOpen(): Promise<void> {
  try {
    const paths = await scratchService.pickFiles();
    if (paths.length === 0) return; // 用户取消不是错误
    await openPathsAndReveal(paths);
  } catch (error) {
    toast.error('打开文件失败', describeOpenFileError(error));
  }
}

/** 执行一条已被接管的快捷键（`tabId` = 面板聚焦时的事件目标标签） */
function runShortcut(id: ShortcutId, tabId: string | null): void {
  const store = useScratchStore.getState();
  switch (id) {
    case 'pane.new':
      revealWorkspace(); // AC-1 ①：任意路由页按快捷键 ⇒ 界面切到工作区
      store.createTab();
      break;
    case 'pane.close': {
      const target = tabId ?? store.activeTabId;
      if (target) store.requestCloseTab(target); // 脏标签由工作区弹「保存 / 放弃 / 取消」
      break;
    }
    case 'pane.next':
      revealWorkspace();
      store.nextTab();
      break;
    case 'pane.prev':
      revealWorkspace();
      store.prevTab();
      break;
    case 'file.open':
      void pickAndOpen();
      break;
    case 'file.save':
    case 'file.saveAs': {
      const target = tabId ?? store.activeTabId;
      if (!target) break;
      void store.savePane(target, { forceDialog: id === 'file.saveAs' }).then((saved) => {
        if (saved) toast.success('已保存');
      });
      break;
    }
    /**
     * `view.toolbox`（`Ctrl+K`）/ `view.diff`（`Ctrl+Shift+D`）——**本轮新增接管**（AC-75② / 闭环 BL-51）。
     *
     * 三步（顺序不可换）：
     *   ① 先把用户带到工作区（与 `pane.new` 同一口径：任意路由页按键都要进编辑器）；
     *   ② 定目标标签：面板聚焦时的那个 → 否则当前激活项 → 都没有则**建一个空标签**
     *      （"配了但按了没反应"是 AC-75② 的失败条件；建空标签让按键**永远有可观测结果**，
     *       与 `pane.new` 一样都属于"用户在请求一个编辑动作"的合理推断）；
     *   ③ 投递**一次性请求**（不在这里拿正文 —— 正文只有组件上下文能取，store 不持有正文，§4.1.2）。
     */
    case 'view.toolbox':
    case 'view.diff': {
      revealWorkspace();
      const target = tabId ?? store.activeTabId ?? store.createTab();
      useScratchUiStore
        .getState()
        .requestPanel(id === 'view.toolbox' ? 'toolbox' : 'diff', target);
      break;
    }
    default:
      break;
  }
}

/** 键盘事件 → 是否已被本模块消化（返回 true 表示已接管，调用方无需再处理） */
function handleKeyDown(event: KeyboardEvent): boolean {
  // ① 准入：未登录 ⇒ 不响应、不报错、不 preventDefault（AC-63 ②）
  const auth = useAuthStore.getState();
  if (!canUseEditor({ isLoggedIn: auth.isLoggedIn, tokenExpired: auth.isTokenExpired() })) {
    return false;
  }
  // ② 窗口活跃（AC-8）
  if (!document.hasFocus() || document.hidden) return false;
  // ③ 快捷键设置对话框正在录制 ⇒ 让路
  if (document.body.dataset.jfShortcutCapture) return false;

  const chord = keyboardEventToShortcut(event);
  if (!chord) return false;
  const bindings = useShortcutStore.getState();
  const matched = RUNTIME_SHORTCUTS.find((id) => bindings.getBinding(id) === chord);
  if (!matched) return false; // 不命中就立刻返回，事件继续流向 CM6（§4.2.3 规则 1）

  const panelTabId = panelTabIdOf(event.target);
  if (PANEL_SCOPE_SHORTCUTS.includes(matched) && panelTabId === null) {
    // 面板聚焦类：事件目标不在任何编辑面板内 ⇒ 不接管（例如焦点在左侧菜单上按 Ctrl+S）
    return false;
  }

  event.preventDefault();
  event.stopPropagation();
  runShortcut(matched, panelTabId);
  return true;
}

/** 是否已装上（模块可能被 `App.tsx` 与测试同时 import，必须幂等） */
let installed = false;

/**
 * 装上应用级快捷键监听（幂等；模块顶层已自动调用一次）。
 *
 * @returns 卸载函数（只给测试用：用例之间互不串味）
 */
export function installEditorShortcutRuntime(): () => void {
  if (installed) return () => undefined;
  installed = true;
  const listener = (event: KeyboardEvent): void => {
    handleKeyDown(event);
  };
  // **捕获阶段**：先于 CM6 / 宿主的冒泡处理拿到事件（§4.2.3 规则 2）
  window.addEventListener('keydown', listener, true);
  return () => {
    installed = false;
    window.removeEventListener('keydown', listener, true);
  };
}

// 模块顶层安装：`App.tsx` 静态 import ⇒ 早于首帧（与 `editor-open-paths.ts` 同一手法）
installEditorShortcutRuntime();

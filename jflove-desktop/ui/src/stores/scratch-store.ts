/**
 * 文本编辑器标签页状态管理（设计 v1.6.1 §4.3 / §4.5 / §4.8；承接 v1.6.0 §6.2.2 / §6.3.2 / §7.2）
 *
 * ## 一句话职责
 *
 * 标签页列表（**数组顺序 = 标签条顺序**）/ 激活项 / 脏标记 / 目标路径 `path|null` /
 * 草稿还原 / **1500 ms 防抖的自动保存与草稿写入**。
 *
 * ## v1.6.1 相对 v1.6.0 的模型迁移（DS-18）
 *
 * - `panes → tabs`、`activePaneId → activeTabId`；
 * - **整体删除**：每个窗格的 `geometry`（x/y/w/h/z/min/maxed）、`zCounter`、`viewport`，
 *   以及 `movePane` / `resizePane` / `toggleMinimize` / `toggleMaximize` / `tileAll` /
 *   `cascadeAll` / `setViewport`（判据：`grep -rniE "tileAll|cascadeAll|movePane|resizePane|toggleMinimize|toggleMaximize"` = 0）；
 * - **新增**：`openOrFocus`（同路径去重聚焦，AC-65）、`reorderTab`、`nextTab`、`prevTab`、`closeActiveTab`；
 * - **桥与磁盘侧命名保持 `pane*`**（DS-19）：草稿文件名仍是 `pane-<id>.txt`，桥参数仍叫 `pane`，
 *   只有渲染层用「标签页」语义 —— 这样既有的用户草稿**一份都不会丢**。
 *
 * ## 两条不可越界的红线
 *
 * 1. **自动保存只在 `path != null` 时写用户文件**（AC-40）：草稿形态（`path === null`）**永不**写
 *    用户文件系统，内容只进草稿库（`editor.draftSave` → `USER_DATA_DIR/storage/scratch/`）。
 * 2. **正文不进 store**：`text` 由 CM6 实例持有，store 只在两种时刻碰它 —— 新建标签时的初始正文
 *    暂存（`takeInitialText`）与防抖到点后的写盘（经 `registerScratchContentAccessor` 注册的读取器）。
 *    这样 10 个还原标签也只占「1 份正文 + N 个 React 空壳」（AC-60 / §4.8 的内存判据）。
 *
 * ## 防抖常量
 *
 * `AUTO_SAVE_DEBOUNCE_MS = 1500` 与 Python 侧 `src/config/settings.py` 的
 * `AUTO_SAVE_DEBOUNCE_MS` **同值**（§6.2.2 明示"为唯一常量，前端防抖、Python 只做写入"）。
 * 读取顺序：① `import.meta.env.VITE_AUTO_SAVE_DEBOUNCE_MS`（构建期注入，便于联调与测试）；
 * ② 读不到或非法 → **1500 兜底**。渲染层没有 `vite/client` 类型声明，故用结构化断言读取。
 *
 * ## 安全（AGENTS.md §9.6 / §9.14）
 *
 * 本 store 不读文件字节、不拼路径、不写 localStorage；日志/错误文案只用 id、数字与文件名。
 */

import { create } from 'zustand';
import {
  LANGUAGES,
  getCapabilitiesForLanguage,
  getDefaultViewModeForLanguage,
  hasCapability,
  resolveTypeForPath,
} from '../config/scratch-types';
import type { ScratchCapability } from '../config/scratch-types';
import { scratchService, toDraftPaneMeta } from '../services/scratch-service';
import type { DraftPaneMeta, ReadItem, ScratchTab, ScratchViewMode, TabCaret } from '../types/scratch';
import type { LineEnding, TextEncoding } from '../types/scratch';
// 缺陷 L-7：共享「异常 → 文案」叶子模块（本 store 不再自带一份实现）
import { ERROR_FALLBACK, describeErrorWithFallback } from '../utils/error-text';

/** 字号档位范围（F5.12 的 `Ctrl` + 滚轮缩放闭环） */
const FONT_SIZE_MIN = 8;
const FONT_SIZE_MAX = 40;
const DEFAULT_FONT_SIZE = 14;

/** 防抖兜底值（与 Python 侧 `AUTO_SAVE_DEBOUNCE_MS` 同值） */
const FALLBACK_AUTO_SAVE_DEBOUNCE_MS = 1500;

/**
 * 读取防抖间隔（毫秒）。
 *
 * ① 优先取构建期注入的 `VITE_AUTO_SAVE_DEBOUNCE_MS`（便于联调时对齐 Python 侧常量）；
 * ② 缺省 / 非数字 / 非正数 → **1500 兜底**（与 `settings.py` 的 `AUTO_SAVE_DEBOUNCE_MS` 同值）。
 */
function readDebounceMs(): number {
  const meta = import.meta as ImportMeta & {
    readonly env?: Record<string, string | boolean | undefined>;
  };
  const raw = meta.env?.VITE_AUTO_SAVE_DEBOUNCE_MS;
  const parsed = typeof raw === 'string' ? Number(raw) : Number.NaN;
  if (Number.isFinite(parsed) && parsed > 0) return parsed;
  return FALLBACK_AUTO_SAVE_DEBOUNCE_MS;
}

/** 自动保存 / 草稿写入的防抖间隔（ms） */
export const AUTO_SAVE_DEBOUNCE_MS = readDebounceMs();

// ── 模块级旁路表（不进 zustand 状态：内容读取器 / 计时器 / 编辑序号等不该触发重渲染）──

/**
 * 未挂载标签的初始正文暂存区（挂载时 `takeInitialText` 取走并删除，避免双份内存）。
 *
 * **生命周期四态**（AC-77 / 设计 §15.5.3 的表，改动本表前先读它）：
 * | 阶段 | 状态 | 谁在动 |
 * |------|------|--------|
 * | ① 打开文件 | 写入 | `openOrFocus`（`:600` 附近的 `putInitialText`） |
 * | ② 宿主挂载 | **取走即删** | `ScratchEditorHost` 的 `pane.id` effect（`takeInitialText`） |
 * | ③ 宿主卸载 / 宿主 DOM 被换 | **写回**（覆盖式 + "标签仍存在"守卫） | `snapshotInitialText`（本文件） |
 * | ④ 关闭标签 | 删除 | `closeTab`（下方的 `initialTexts.delete(id)`） |
 *
 * ⚠ ④ 与 ③ 的**物理顺序**是"先 `closeTab`（删）→ 面板卸载（可能想写）"，
 * 靠 ③ 的 `tabs.find(...)` 守卫保证"已关闭的标签不会在内存里复活"。
 * **不要**把守卫改成"看 `initialTexts.has(id)`"（那会在"从未挂载过、但已写入初始正文"的标签上误判）。
 */
const initialTexts = new Map<string, string>();

/** 由标签组件注册的正文读取器（`ScratchEditorHost` 挂载时注册，卸载时注销） */
const contentAccessors = new Map<string, ScratchContentAccessor>();

/** 每个标签的编辑序号（防抖期间是否又发生过编辑，用于决定能否落下脏标记） */
const editSeqs = new Map<string, number>();

/** 待触发的防抖计时器 */
const flushTimers = new Map<string, ReturnType<typeof setTimeout>>();

/**
 * 标签正文读取器：把 **CM6 实例的实时值**交给 store（store 不缓存正文）。
 *
 * `text` 必填；`caret` / `scrollTop` 用于草稿还原（缺省则沿用 store 里缓存的旧值）。
 */
export type ScratchContentAccessor = () => {
  text: string;
  caret?: TabCaret;
  scrollTop?: number;
};

/** 生成标签 id（设计指定 `crypto.randomUUID()`；非安全上下文做一次兜底） */
function newTabId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `pane-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** 只取文件名（错误文案只用文件名，**不含目录**，§7.4） */
function basenameOf(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

/** 异常 → 展示文案（不含路径与内容）—— 兜底文案 `'保存失败'` 保持抽取前原样 */
function describeSaveError(error: unknown): string {
  return describeErrorWithFallback(error, ERROR_FALLBACK.SAVE);
}

/** 视图模式是否被能力表允许（不可用则 `setViewMode` 不动，§4.1.2） */
export function isViewModeAllowed(mode: ScratchViewMode, capabilities: ScratchCapability[]): boolean {
  switch (mode) {
    case 'edit':
      return true;
    case 'preview':
      return capabilities.includes('preview');
    case 'split':
      return capabilities.includes('split');
    case 'table':
      return capabilities.includes('table');
    case 'tree':
      return capabilities.includes('tree');
    default:
      return false;
  }
}

/**
 * 取某个标签生效的能力集。
 *
 * 口径：**手动切过语言就以语言为准**（用户显式选择优先）；否则有目标路径时按扩展名查表
 * （csv 的语言是 `text`，但它的「表格视图」能力来自扩展名，必须按 ext 查才不会误禁）；
 * 草稿形态（无路径）退回语言并集（宽松兜底）。
 */
export function capabilitiesForTab(tab: ScratchTab): ScratchCapability[] {
  if (tab.language !== tab.detected) return getCapabilitiesForLanguage(tab.language);
  if (tab.path) return resolveTypeForPath(tab.path).capabilities;
  return getCapabilitiesForLanguage(tab.language);
}

/** 注册正文读取器（返回注销函数；`ScratchEditorHost` 挂载/卸载时调用） */
export function registerScratchContentAccessor(
  tabId: string,
  accessor: ScratchContentAccessor,
): () => void {
  contentAccessors.set(tabId, accessor);
  return () => {
    if (contentAccessors.get(tabId) === accessor) contentAccessors.delete(tabId);
  };
}

/** 暂存未挂载标签的初始正文（`openOrFocus` 用；草稿还原暂无此路径，见交付说明） */
export function putInitialText(tabId: string, text: string): void {
  initialTexts.set(tabId, text);
}

/** 取走初始正文（**取走即删除**，全生命周期只交付一次） */
export function takeInitialText(tabId: string): string | null {
  const text = initialTexts.get(tabId);
  if (text === undefined) return null;
  initialTexts.delete(tabId);
  return text;
}

/** 初始正文暂存区里是否有该标签的待交付正文（**只查不取**，与 `takeInitialText` 正交） */
export function hasInitialText(tabId: string): boolean {
  return initialTexts.has(tabId);
}

/** 快照附带的光标 / 滚动（与 `ScratchContentAccessor` 的 `caret` / `scrollTop` **形状对齐**） */
export interface InitialTextSnapshot {
  /** 1 基光标（`TabCaret`：`{line, col}`，与 `types/scratch.ts` 的 `TabCaret` 逐字同形） */
  caret?: TabCaret;
  /** 像素滚动位置（草稿 `scroll_top` 的同一口径） */
  scrollTop?: number;
}

/**
 * **把一份"活正文"快照写回初始文本暂存区**（AC-77 的写侧修复；由 `ScratchEditorHost` 的
 * callback ref 与 `pane.id` effect 的 cleanup 调用）。
 *
 * ## 语义（三条，缺一不可）
 *
 * 1. **只在标签仍然存在时写入**：路由卸载、宿主 DOM 被换、视图模式切换都会走这里，
 *    而"关闭标签"（`closeTab`）已经 `initialTexts.delete(id)` —— 若此时无守卫地写入，
 *    会让**已关闭的标签在内存里"复活"**（下次 `takeInitialText` 拿到一份属于死标签的正文，
 *    且与 AC-59「放弃修改后不还原」冲突）。
 * 2. **`set`（覆盖）而不是"只在缺失时写"**：同一标签在一个会话里可能卸载多次
 *    （切菜单 → 回来 → 再切走），每次都应保留**最新**正文。
 * 3. **不写日志、不打印正文**（`AGENTS.md §9.14`）：本函数**零日志**。
 *
 * ## 明确不保的东西
 *
 * **undo / redo 历史不在快照范围内**：CM6 的 history 活在 `EditorState` 里，`view.destroy()`
 * 后无处可取；这里只交 `doc.toString()` 一份字符串。⇒ 重挂后 `Ctrl+Z` **不能**撤回卸载前的编辑。
 * 这是**刻意的取舍**（要保 undo 就得让实例跨路由常驻，与 D27/DS-17 的"整页工作区"冲突）。
 *
 * @param tabId 标签 id（`ScratchTab.id`）
 * @param text  实例当前正文（`view.state.doc.toString()`）
 * @param meta  可选的光标 / 滚动；缺省时**不改** store 里 `tab.caret` / `tab.scrollTop`
 */
export function snapshotInitialText(
  tabId: string,
  text: string,
  meta?: InitialTextSnapshot,
): void {
  const tab = useScratchStore.getState().tabs.find((item) => item.id === tabId);
  if (!tab) return; // ① 守卫：标签已关闭 / 从不存在 ⇒ 不写入（防"死标签复活"）
  initialTexts.set(tabId, text);
  if (meta?.caret || typeof meta?.scrollTop === 'number') {
    useScratchStore.setState((prev) => ({
      tabs: prev.tabs.map((item) =>
        item.id === tabId
          ? {
              ...item,
              caret: meta.caret ?? item.caret,
              scrollTop: typeof meta.scrollTop === 'number' ? meta.scrollTop : item.scrollTop,
            }
          : item,
      ),
    }));
  }
}

interface ScratchState {
  /** 全部标签页（**数组顺序 = 标签条顺序**，DS-18：不存在第二处顺序来源） */
  tabs: ScratchTab[];
  /** 当前激活标签 id；`tabs` 为空时必为 `null` */
  activeTabId: string | null;
  /** 最近一次失败原因（自动保存 / 写回失败），供 UI 明确提示（AC-41） */
  lastError: string | null;
  /**
   * **待确认的关闭请求**（v1.6.1 施工补充，设计 §4.7 / AC-59）。
   *
   * 为什么放在 store 而不是工作区的 `useState`：`Ctrl+W` 的接管方是**模块级常驻**的快捷键运行时
   * （`utils/scratch/shortcut-runtime.ts`，设计 13.2 U-8 的硬约束、AC-1 要求"任意路由页"可用），
   * 它拿不到工作区的局部 state；由它调 `requestCloseTab(id)` 置位，工作区只负责把询问框渲染出来。
   * `null` = 无待确认请求。
   */
  pendingCloseId: string | null;

  // ── §4.3 的公开 action ──
  /** 新建标签（**追加到末尾并激活**），返回新标签 id */
  createTab: (init?: Partial<ScratchTab>) => string;
  /** 关闭标签（`dirty` 的询问由调用方负责；干净关闭顺带丢弃草稿） */
  closeTab: (id: string) => void;
  /** 关闭当前激活标签（`Ctrl+W` 的目标） */
  closeActiveTab: () => void;
  /** 激活标签（同时视为"被激活"→ 允许懒挂载） */
  setActiveTab: (id: string) => void;
  /** 下一个 / 上一个标签（循环；空列表无操作） */
  nextTab: () => void;
  prevTab: () => void;
  /** 拖拽排序：把 `from` 位置的标签移动到 `to` 位置（越界自动收敛） */
  reorderTab: (from: number, to: number) => void;
  setLanguage: (id: string, language: string) => void;
  setViewMode: (id: string, mode: ScratchViewMode) => void;
  /** 脏标记（同时驱动草稿防抖与 Python 侧 `dirtyCount`） */
  markDirty: (id: string, dirty: boolean) => void;
  setFontSize: (id: string, fontSize: number) => void;
  toggleLineNumbers: (id: string) => void;
  toggleWordWrap: (id: string) => void;
  /** 启动还原：**只登记状态，`mounted` 保持 false**（§4.8 懒挂载） */
  hydrateFromDrafts: (entries: DraftPaneMeta[]) => void;
  /**
   * 把 `editor.detectAndRead` 的 `ReadItem` 变成标签页；失败条目只记提示，返回 null。
   *
   * **同路径去重（AC-65）**：`path` 非空且与既有标签相等 ⇒ **只聚焦、不新建、不覆盖内容**。
   */
  openOrFocus: (item: ReadItem) => string | null;

  // ── 施工面辅助 action（组件需要用）──
  /** 懒挂载标记（首次成为激活项时由工作区置位） */
  setMounted: (id: string, mounted: boolean) => void;
  /** 立刻落盘所有待写内容（退出握手前调用；会清空全部防抖计时器） */
  flushPendingSaves: () => Promise<void>;
  /**
   * **手动保存**（AC-36 / AC-37）—— `Ctrl+S` / `Ctrl+Shift+S` 与"关闭前保存"统一走它。
   *
   * - 标签**从未落过盘**（`path === null`）或 `forceDialog`（= 另存为）⇒ 先弹**原生**保存对话框
   *   （`scratchService.pickSaveTarget`）；用户取消 ⇒ **磁盘零变化**且脏标记保持（AC-36）；
   * - 已有路径且非另存为 ⇒ **不弹任何对话框**，直接写回同一路径（AC-37）；
   * - 只读标签（`.diff` / `.patch` / 大文件降级，AC-19）直接拒绝，不产生任何写入；
   * - 成功 ⇒ 路径 / 标题 / 脏标记一起更新；失败 ⇒ 只给提示（`lastError`），内容与脏标记不变（AC-41）。
   *
   * @returns `true` = 真的写盘成功（取消 / 只读 / 写失败都是 `false`）
   */
  savePane: (id: string, options?: { forceDialog?: boolean }) => Promise<boolean>;
  /** 清掉最近一次错误提示 */
  clearError: () => void;
  /**
   * 记录一条用户可见的失败提示（v1.6.1 施工补充）。
   *
   * 用途：`utils/scratch/editor-open-paths.ts` 在**桥层整体失败**（`detectAndRead` 抛错，
   * 例如桥未就绪）时也要有提示出口 —— 单条失败（`items[].ok:false`）已经由 `openOrFocus`
   * 写进 `lastError`，这里补的是"整批都没跑起来"的情况。
   */
  setError: (message: string) => void;
  /**
   * 请求关闭某个标签（AC-59 / AC-70 的唯一入口）。
   *
   * - 标签**不脏** ⇒ 立即关闭（顺带丢弃草稿，DRY：与 `closeTab` 同语义）；
   * - 标签**脏** ⇒ 只置 `pendingCloseId`，由工作区弹出「保存 / 放弃 / 取消」；
   * - 已在待确认中的同一个标签 ⇒ **幂等**（防重入：快捷键与关闭按钮可能同帧各触发一次）；
   * - 标签不存在 ⇒ 无操作。
   */
  requestCloseTab: (id: string) => void;
  /** 收起关闭询问框（取消 / 已处理完），**不动标签** */
  clearCloseRequest: () => void;
}

export const useScratchStore = create<ScratchState>((set, get) => {
  /** 清掉某个标签的防抖计时器 */
  const clearFlushTimer = (tabId: string): void => {
    const timer = flushTimers.get(tabId);
    if (timer !== undefined) {
      clearTimeout(timer);
      flushTimers.delete(tabId);
    }
  };

  /** 把脏标签数量报给 Python（退出握手前置，AC-56）；失败不打扰用户 */
  const syncDirtyCount = (): void => {
    const count = get().tabs.filter((tab) => tab.dirty).length;
    void scratchService.reportDirty(count).catch(() => {});
  };

  /** 防抖调度：同一标签的连续编辑只保留最后一次 */
  const scheduleFlush = (tabId: string): void => {
    clearFlushTimer(tabId);
    flushTimers.set(
      tabId,
      setTimeout(() => {
        flushTimers.delete(tabId);
        void flushPane(tabId);
      }, AUTO_SAVE_DEBOUNCE_MS),
    );
  };

  /**
   * 防抖到点后的落盘：**先草稿库，后（仅 `path != null` 时）用户文件**。
   *
   * 失败语义（AC-41 / §6.2.2）：内容、脏标记、草稿**都不变**，只给明确提示。
   */
  const flushPane = async (tabId: string): Promise<void> => {
    const tab = get().tabs.find((item) => item.id === tabId);
    if (!tab || !tab.dirty) return; // 不脏不写（AC-54：内容不变再等一个周期，草稿 mtime 不变）
    const accessor = contentAccessors.get(tabId);
    if (!accessor) return; // 未挂载标签没有正文可写

    let snapshot: ReturnType<ScratchContentAccessor>;
    try {
      snapshot = accessor();
    } catch {
      return;
    }
    const seqAtStart = editSeqs.get(tabId) ?? 0;

    try {
      // ① 草稿库：两种形态都写 —— 草稿形态的**唯一**落点（§6.3.2）
      //    顺序 / 激活项取自当前 store（DS-30：渲染层写侧新增 3 字段）
      const state = get();
      const index = state.tabs.findIndex((item) => item.id === tabId);
      await scratchService.draftSave(
        toDraftPaneMeta(tab, {
          caret: snapshot.caret,
          scrollTop: snapshot.scrollTop,
          order: index < 0 ? 0 : index,
          active: state.activeTabId === tabId,
        }),
        snapshot.text,
      );
      // ② 用户文件：仅 `path != null` 且非只读（AC-40 / AC-19）
      const wroteUserFile = tab.path !== null && !tab.readOnly;
      if (wroteUserFile && tab.path !== null) {
        await scratchService.writeText({
          path: tab.path,
          text: snapshot.text,
          encoding: tab.encoding,
          lineEnding: tab.lineEnding,
        });
      }
      // 写回成功且期间没有新编辑 ⇒ 脏标记落下；有编辑则保持，等下一轮防抖
      if (wroteUserFile && (editSeqs.get(tabId) ?? 0) === seqAtStart) {
        set((state2) => ({
          tabs: state2.tabs.map((item) => (item.id === tabId ? { ...item, dirty: false } : item)),
        }));
        syncDirtyCount();
      }
    } catch (error) {
      set({ lastError: describeSaveError(error) });
    }
  };

  return {
    tabs: [],
    activeTabId: null,
    lastError: null,
    pendingCloseId: null,

    createTab: (init) => {
      const language = init?.language ?? 'text';
      const tab: ScratchTab = {
        id: init?.id ?? newTabId(),
        title: init?.title ?? '未命名',
        path: init?.path ?? null,
        language,
        detected: init?.detected ?? language,
        viewMode: init?.viewMode ?? 'edit',
        encoding: init?.encoding ?? 'utf-8',
        lineEnding: init?.lineEnding ?? 'lf',
        readOnly: init?.readOnly ?? false,
        dirty: init?.dirty ?? false,
        caret: init?.caret ?? { line: 1, col: 1 },
        scrollTop: init?.scrollTop ?? 0,
        fontSize: init?.fontSize ?? DEFAULT_FONT_SIZE,
        showLineNumbers: init?.showLineNumbers ?? true,
        wordWrap: init?.wordWrap ?? false,
        // 新建即激活（§4.8：首次成为激活项 ⇒ 允许建实例）；
        // 还原路径走 hydrateFromDrafts，那里**保持 false**。
        mounted: init?.mounted ?? true,
      };
      set((state) => ({
        // 新标签**追加到末尾并激活**（§4.3）
        tabs: [...state.tabs, tab],
        activeTabId: tab.id,
        lastError: null,
      }));
      return tab.id;
    },

    closeTab: (id) => {
      const tab = get().tabs.find((item) => item.id === id);
      if (!tab) return;
      clearFlushTimer(id);
      contentAccessors.delete(id);
      editSeqs.delete(id);
      initialTexts.delete(id);
      set((state) => {
        const index = state.tabs.findIndex((item) => item.id === id);
        const tabs = state.tabs.filter((item) => item.id !== id);
        let activeTabId = state.activeTabId;
        if (state.activeTabId === id) {
          // 关闭后激活项 = 原位置的**右邻**，无右邻则左邻，全空则 null（§4.3）
          const neighbour = tabs[index] ?? tabs[index - 1] ?? null;
          activeTabId = neighbour ? neighbour.id : null;
        }
        return {
          tabs,
          activeTabId,
          // 关闭的正是"待确认关闭"的那个标签 ⇒ 询问框自然收起（防出现指向已消失标签的弹窗）
          pendingCloseId: state.pendingCloseId === id ? null : state.pendingCloseId,
        };
      });
      // 干净关闭 ⇒ 不再需要草稿（否则重启会把已关的标签又还原出来）。
      // 脏标签的去留由调用方决定：保存分支走 writeText，放弃分支由组件调 `editor.draftDiscard`。
      if (!tab.dirty) {
        void scratchService.draftDiscard(id).catch(() => {});
      }
      syncDirtyCount();
    },

    closeActiveTab: () => {
      const active = get().activeTabId;
      if (active) get().closeTab(active);
    },

    setActiveTab: (id) => {
      if (!get().tabs.some((item) => item.id === id)) return;
      set((state) => ({
        activeTabId: id,
        // 激活即允许建实例（§4.8），此后保持挂载
        tabs: state.tabs.map((item) => (item.id === id ? { ...item, mounted: true } : item)),
      }));
    },

    nextTab: () => {
      const { tabs, activeTabId } = get();
      if (tabs.length === 0) return;
      const index = tabs.findIndex((item) => item.id === activeTabId);
      const next = tabs[(index + 1) % tabs.length];
      get().setActiveTab(next.id);
    },

    prevTab: () => {
      const { tabs, activeTabId } = get();
      if (tabs.length === 0) return;
      const index = tabs.findIndex((item) => item.id === activeTabId);
      const prev = tabs[(index - 1 + tabs.length) % tabs.length];
      get().setActiveTab(prev.id);
    },

    reorderTab: (from, to) => {
      set((state) => {
        const length = state.tabs.length;
        if (length < 2) return {};
        if (!Number.isInteger(from) || !Number.isInteger(to)) return {};
        const source = Math.min(Math.max(from, 0), length - 1);
        const target = Math.min(Math.max(to, 0), length - 1);
        if (source === target) return {};
        const tabs = [...state.tabs];
        const [moved] = tabs.splice(source, 1);
        tabs.splice(target, 0, moved);
        // 顺序即 `tabs` 数组顺序，激活项不受排序影响（§4.3 不变量 ③）
        return { tabs };
      });
    },

    setLanguage: (id, language) => {
      // 只接受类型表内的语言 id（§5.1 的 LANGUAGES 派生量）
      if (!LANGUAGES.some((item) => item === language)) return;
      set((state) => ({
        tabs: state.tabs.map((item) => {
          if (item.id !== id) return item;
          const capabilities = getCapabilitiesForLanguage(language);
          const viewMode = isViewModeAllowed(item.viewMode, capabilities)
            ? item.viewMode
            : getDefaultViewModeForLanguage(language);
          // `detected` 保留原识别值，供「恢复自动」
          return { ...item, language, viewMode };
        }),
      }));
    },

    setViewMode: (id, mode) => {
      set((state) => ({
        tabs: state.tabs.map((item) => {
          if (item.id !== id) return item;
          // 能力表校验：不可用则不动（§4.1.2）
          if (!isViewModeAllowed(mode, capabilitiesForTab(item))) return item;
          return { ...item, viewMode: mode };
        }),
      }));
    },

    markDirty: (id, dirty) => {
      // 编辑序号只增不减：用于判断异步写回期间是否又发生了编辑
      editSeqs.set(id, (editSeqs.get(id) ?? 0) + 1);
      set((state) => ({
        tabs: state.tabs.map((item) => (item.id === id ? { ...item, dirty } : item)),
      }));
      if (dirty) scheduleFlush(id);
      else clearFlushTimer(id);
      syncDirtyCount();
    },

    setFontSize: (id, fontSize) => {
      const clamped = Math.min(FONT_SIZE_MAX, Math.max(FONT_SIZE_MIN, Math.round(fontSize)));
      set((state) => ({
        tabs: state.tabs.map((item) => (item.id === id ? { ...item, fontSize: clamped } : item)),
      }));
    },

    toggleLineNumbers: (id) => {
      set((state) => ({
        tabs: state.tabs.map((item) =>
          item.id === id ? { ...item, showLineNumbers: !item.showLineNumbers } : item,
        ),
      }));
    },

    toggleWordWrap: (id) => {
      set((state) => ({
        tabs: state.tabs.map((item) => (item.id === id ? { ...item, wordWrap: !item.wordWrap } : item)),
      }));
    },

    hydrateFromDrafts: (entries) => {
      // 归一 + 排序（DS-30：新索引带 `order`；旧索引无该字段 ⇒ 保持数组顺序）
      const normalized = entries
        .map((meta, index) => ({ meta, index }))
        // 索引可能被半截写入 / 被外部改坏：字段不合法就**跳过该条**（不阻塞启动，§6.3.2）
        .filter(({ meta }) => Boolean(meta) && typeof meta.id === 'string' && meta.id.length > 0);
      const hasOrder = normalized.some(({ meta }) => Number.isFinite(meta.order));
      const ordered = hasOrder
        ? [...normalized].sort(
            (a, b) => (Number(a.meta.order) || 0) - (Number(b.meta.order) || 0) || a.index - b.index,
          )
        : normalized;

      const restored: ScratchTab[] = ordered.map(({ meta }) => ({
        id: meta.id,
        title: typeof meta.title === 'string' && meta.title ? meta.title : '未命名',
        path: typeof meta.path === 'string' && meta.path ? meta.path : null,
        language: typeof meta.language === 'string' && meta.language ? meta.language : 'text',
        detected: typeof meta.language === 'string' && meta.language ? meta.language : 'text',
        viewMode: normalizeViewMode(meta.view_mode),
        encoding: normalizeEncoding(meta.encoding),
        lineEnding: normalizeLineEnding(meta.lineEnd),
        readOnly: Boolean(meta.read_only),
        dirty: Boolean(meta.dirty),
        caret: normalizeCaret(meta.caret),
        scrollTop: typeof meta.scroll_top === 'number' ? meta.scroll_top : 0,
        fontSize: typeof meta.font_size === 'number' ? meta.font_size : DEFAULT_FONT_SIZE,
        showLineNumbers: meta.show_line_numbers !== false,
        wordWrap: Boolean(meta.word_wrap),
        // §4.8：还原阶段**只登记状态**，不建 CM6 实例
        mounted: false,
      }));

      // 激活项：索引里 `active` 为真的第一条；都没有则第一条（AC-55 / 兼容判据）
      const activeIndex = ordered.findIndex(({ meta }) => meta.active === true);
      const preferredActiveId = restored[activeIndex >= 0 ? activeIndex : 0]?.id ?? null;

      set((state) => {
        const existing = new Set(state.tabs.map((item) => item.id));
        // 幂等：同 id 已在场则跳过（重复调用 / 事件重放都不会翻倍）
        const added = restored.filter((item) => !existing.has(item.id));
        if (added.length === 0) return {};
        return {
          tabs: [...state.tabs, ...added],
          activeTabId: state.activeTabId ?? preferredActiveId,
        };
      });
    },

    openOrFocus: (item) => {
      if (!item.ok) {
        // 二进制 / 过大 / 编码不可判定 / 不存在：只提示（文案取自 Python，**不含完整路径**）
        set({ lastError: item.message || `${basenameOf(item.path)} 无法打开` });
        return null;
      }
      // AC-65：同路径（`path` 非空且相等）⇒ 只聚焦已有标签，**不新建、不覆盖内容**
      const existing = get().tabs.find((tab) => tab.path !== null && tab.path === item.path);
      if (existing) {
        get().setActiveTab(existing.id);
        return existing.id;
      }
      const info = resolveTypeForPath(item.path);
      // M-13 / AC-45：**打开文件**（双击关联 / 打开对话框 / 拖入）一律先落编辑器视图；
      // 类型表里的 `defaultViewMode`（如 `.csv` 的 `table`）只作为**切语言 / 切类型**时的
      // 推荐视图（见 `setLanguage` → `getDefaultViewModeForLanguage`），不决定打开态。
      const id = get().createTab({
        title: item.name,
        path: item.path,
        language: item.language,
        detected: item.language,
        viewMode: 'edit',
        encoding: item.encoding,
        lineEnding: item.lineEnding,
        // 强制只读类（diff / patch）与 F8 阈值降级都在这里合流
        readOnly: item.readOnly || info.readOnly,
        dirty: false,
      });
      // 正文交给 CM6 挂载时取走（store 不缓存）
      putInitialText(id, item.text);
      return id;
    },

    setMounted: (id, mounted) => {
      set((state) => ({
        tabs: state.tabs.map((item) => (item.id === id ? { ...item, mounted } : item)),
      }));
    },

    flushPendingSaves: async () => {
      const pendingIds = Array.from(flushTimers.keys());
      pendingIds.forEach((tabId) => clearFlushTimer(tabId));
      await Promise.all(pendingIds.map((tabId) => flushPane(tabId)));
    },

    savePane: async (id, options) => {
      const tab = get().tabs.find((item) => item.id === id);
      if (!tab) return false;

      /**
       * 失败 / 取消的统一收尾：把该标签的自动保存**重新排上队**（审查 M4）。
       *
       * 写盘前会 `clearFlushTimer(id)`；若此后直接 `return false`，原本 1500 ms 后
       * 会把内容落到**草稿库**的那次 flush 也随之消失 —— 用户此时关进程，
       * 最后一次编辑既不进用户文件、也不进草稿库。仅在"仍脏且当前没有计时器"时补排。
       */
      const rescheduleFlush = (): false => {
        const stillDirty = get().tabs.find((item) => item.id === id)?.dirty ?? false;
        if (stillDirty && !flushTimers.has(id)) scheduleFlush(id);
        return false;
      };

      // 只读标签（AC-19 / AC-52）：保存入口直接拒绝，连磁盘都不碰
      if (tab.readOnly) {
        set({ lastError: '只读标签页不支持保存' });
        return rescheduleFlush();
      }
      const accessor = contentAccessors.get(id);
      if (!accessor) {
        // 未挂载 / 内核未就绪：没有真值可写（骨架态与加载失败态）
        set({ lastError: '编辑器尚未就绪，暂时无法保存' });
        return rescheduleFlush();
      }
      let snapshot: ReturnType<ScratchContentAccessor>;
      try {
        snapshot = accessor();
      } catch {
        return rescheduleFlush();
      }

      // ① 目标路径：已有路径直接写回；草稿形态或"另存为"则弹**原生**保存对话框（AC-36）
      let target = tab.path;
      if (options?.forceDialog === true || target === null) {
        try {
          target = await scratchService.pickSaveTarget(tab.title || undefined);
        } catch (error) {
          set({ lastError: describeSaveError(error) });
          return rescheduleFlush();
        }
        // 取消：不产生任何写入、目录条目数不变、脏标记保持（AC-36 的"取消"路径）
        if (!target) return rescheduleFlush();
      }

      // ② 手动保存接管本轮：取消排队中的自动保存，避免同一路径写两次（AC-37 的"恰好一次"）
      clearFlushTimer(id);
      const seqAtStart = editSeqs.get(id) ?? 0;

      // ③ 写盘。桥的壳是 `{ok: true, result}` / `{ok: false, error}`，而服务层的业务失败分两种：
      //    只读 ⇒ **返回** `{ok: false, reason: 'read_only'}`（不抛）；冲突 ⇒ **抛**
      //    `EditorError('conflict')`（被桥包成 `{ok: false, error}`）。
      //    所以 TS 的 `WriteTextResult`（只声明 `ok: true`）是乐观类型，这里按运行时判。
      //    注：`conflict` 分支目前是**防御性**的 —— `savePane` 不传 `expectedSha256`，
      //    服务层不会报冲突（覆盖冲突检测的接线见 BACKLOG「另存为/覆盖冲突」项）。
      let result: { ok?: boolean; reason?: string; error?: string } | null;
      try {
        result = (await scratchService.writeText({
          path: target,
          text: snapshot.text,
          encoding: tab.encoding,
          lineEnding: tab.lineEnding,
        })) as unknown as { ok?: boolean; reason?: string; error?: string } | null;
      } catch (error) {
        set({ lastError: describeSaveError(error) });
        return rescheduleFlush();
      }
      if (result?.ok !== true) {
        const reason = result?.reason;
        set({
          lastError:
            reason === 'read_only'
              ? '该文件被判定为只读，未写入'
              : reason === 'conflict'
                ? '文件已在别处被修改，未覆盖'
                : describeSaveError(result?.error ?? '保存失败'),
        });
        return rescheduleFlush();
      }

      // ④ 成功：路径 / 标题（文件名）/ 脏标记一起落地；期间若又有编辑则保持脏，等下一轮防抖
      const stillClean = (editSeqs.get(id) ?? 0) === seqAtStart;
      set((state) => ({
        tabs: state.tabs.map((item) =>
          item.id === id
            ? {
                ...item,
                path: target,
                title: target ? basenameOf(target) : item.title,
                dirty: stillClean ? false : item.dirty,
              }
            : item,
        ),
        lastError: null,
      }));
      syncDirtyCount();
      return true;
    },

    clearError: () => set({ lastError: null }),
    setError: (message) => set({ lastError: message }),

    requestCloseTab: (id) => {
      const { tabs, pendingCloseId, closeTab } = get();
      const tab = tabs.find((item) => item.id === id);
      if (!tab) return;
      if (!tab.dirty) {
        closeTab(id); // 干净关闭：顺带丢弃草稿（与 closeTab 同一语义，AC-59 的"无需询问"分支）
        return;
      }
      if (pendingCloseId === id) return; // 幂等：已在询问中，不重复置位
      set({ pendingCloseId: id });
    },
    clearCloseRequest: () => set({ pendingCloseId: null }),
  };
});

// ── 草稿条目字段的容错归一（索引损坏时不能让整次还原崩掉）──

function normalizeViewMode(raw: ScratchViewMode | undefined): ScratchViewMode {
  const allowed: ScratchViewMode[] = ['edit', 'preview', 'split', 'table', 'tree'];
  return allowed.find((item) => item === raw) ?? 'edit';
}

function normalizeCaret(raw: TabCaret | undefined): TabCaret {
  const line = typeof raw?.line === 'number' && raw.line > 0 ? raw.line : 1;
  const col = typeof raw?.col === 'number' && raw.col > 0 ? raw.col : 1;
  return { line, col };
}

function normalizeEncoding(raw: TextEncoding | undefined): TextEncoding {
  const allowed: TextEncoding[] = ['utf-8', 'utf-8-bom', 'gbk', 'utf-16le', 'utf-16be'];
  return allowed.find((item) => item === raw) ?? 'utf-8';
}

function normalizeLineEnding(raw: LineEnding | undefined): LineEnding {
  return raw === 'crlf' ? 'crlf' : 'lf';
}

// ── 选择器（组件用，避免各写一份派生逻辑）──

/** 选择器：脏标签数量（退出握手 / 标题栏提示用） */
export function selectDirtyCount(state: ScratchState): number {
  return state.tabs.filter((tab) => tab.dirty).length;
}

/** 选择器：当前激活标签 */
export function selectActiveTab(state: ScratchState): ScratchTab | null {
  if (!state.activeTabId) return null;
  return state.tabs.find((tab) => tab.id === state.activeTabId) ?? null;
}

/** 选择器：按 id 取标签（找不到返回 null） */
export function selectTabById(state: ScratchState, id: string): ScratchTab | null {
  return state.tabs.find((tab) => tab.id === id) ?? null;
}

/** 选择器：某标签是否具备某项能力（工具栏按能力禁用按钮用） */
export function selectTabHasCapability(
  state: ScratchState,
  id: string,
  capability: ScratchCapability,
): boolean {
  const tab = selectTabById(state, id);
  if (!tab) return false;
  return hasCapability(resolveTypeForPath(tab.path ?? ''), capability);
}

/** 类型再导出：标签页模型（`ScratchTab`，v1.6.1 由 `ScratchPane` 更名） */
export type { ScratchTab };

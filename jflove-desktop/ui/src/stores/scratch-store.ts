/**
 * 文本编辑器窗格状态管理（设计 v1.6.0 §4.1.2 / §4.1.3 / §6.2.2 / §6.3.2 / §7.2）
 *
 * ## 一句话职责
 *
 * 窗格列表 / 几何（x,y,w,h,z,min,maxed）/ 层级聚焦 / 脏标记 / 目标路径 `path|null` /
 * 草稿还原 / **1500 ms 防抖的自动保存与草稿写入**。
 *
 * ## 两条不可越界的红线
 *
 * 1. **自动保存只在 `path != null` 时写用户文件**（AC-40）：草稿形态（`path === null`）**永不**写
 *    用户文件系统，内容只进草稿库（`editor.draftSave` → `USER_DATA_DIR/storage/scratch/`）。
 * 2. **正文不进 store**：`text` 由 CM6 实例持有，store 只在两种时刻碰它 —— 新建窗格时的初始正文
 *    暂存（`takeInitialText`）与防抖到点后的写盘（经 `registerScratchContentAccessor` 注册的读取器）。
 *    这样 10 个还原窗格也只占「1 份正文 + N 个 React 空壳」（AC-60 的内存判据）。
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
import type {
  DraftPaneMeta,
  PaneCaret,
  PaneGeometry,
  ReadItem,
  ScratchPane,
  ScratchViewMode,
} from '../types/scratch';
import type { LineEnding, TextEncoding } from '../types/scratch';

/** 窗格最小尺寸（缩放钳制，§4.1.2） */
export const PANE_MIN_WIDTH = 320;
export const PANE_MIN_HEIGHT = 200;

/** 新建窗格的默认尺寸 */
const DEFAULT_PANE_WIDTH = 720;
const DEFAULT_PANE_HEIGHT = 480;

/** 新建窗格的层叠起点 */
const CASCADE_BASE_X = 60;
const CASCADE_BASE_Y = 40;

/** 层叠偏移（§4.1.2：固定偏移 24 px 递增） */
const CASCADE_OFFSET = 24;

/** 平铺间距（设计只说"互不重叠铺满可用区域"，取 4 px 保证边框可见） */
const TILE_GAP = 4;

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

/** 未挂载窗格的初始正文暂存区（挂载时 `takeInitialText` 取走并删除，避免双份内存） */
const initialTexts = new Map<string, string>();

/** 由窗格组件注册的正文读取器（`ScratchEditorHost` 挂载时注册，卸载时注销） */
const contentAccessors = new Map<string, ScratchContentAccessor>();

/** 每个窗格的编辑序号（防抖期间是否又发生过编辑，用于决定能否落下脏标记） */
const editSeqs = new Map<string, number>();

/** 待触发的防抖计时器 */
const flushTimers = new Map<string, ReturnType<typeof setTimeout>>();

/** 最小化 / 最大化之前的几何（设计模型无此字段，故放旁路表，且**不持久化**） */
const restoreGeometries = new Map<string, PaneGeometry>();

/**
 * 窗格正文读取器：把 **CM6 实例的实时值**交给 store（store 不缓存正文）。
 *
 * `text` 必填；`caret` / `scrollTop` 用于草稿还原（缺省则沿用 store 里缓存的旧值）。
 */
export type ScratchContentAccessor = () => {
  text: string;
  caret?: PaneCaret;
  scrollTop?: number;
};

/** 生成窗格 id（设计指定 `crypto.randomUUID()`；非安全上下文做一次兜底） */
function newPaneId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `pane-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** 只取文件名（错误文案只用文件名，**不含目录**，§7.4） */
function basenameOf(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

/** 异常 → 展示文案（不含路径与内容） */
function describeError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return '保存失败';
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
 * 取某个窗格生效的能力集。
 *
 * 口径：**手动切过语言就以语言为准**（用户显式选择优先）；否则有目标路径时按扩展名查表
 * （csv 的语言是 `text`，但它的「表格视图」能力来自扩展名，必须按 ext 查才不会误禁）；
 * 草稿形态（无路径）退回语言并集（宽松兜底）。
 */
export function capabilitiesForPane(pane: ScratchPane): ScratchCapability[] {
  if (pane.language !== pane.detected) return getCapabilitiesForLanguage(pane.language);
  if (pane.path) return resolveTypeForPath(pane.path).capabilities;
  return getCapabilitiesForLanguage(pane.language);
}

/** 注册正文读取器（返回注销函数；`ScratchEditorHost` 挂载/卸载时调用） */
export function registerScratchContentAccessor(
  paneId: string,
  accessor: ScratchContentAccessor,
): () => void {
  contentAccessors.set(paneId, accessor);
  return () => {
    if (contentAccessors.get(paneId) === accessor) contentAccessors.delete(paneId);
  };
}

/** 暂存未挂载窗格的初始正文（`applyReadResult` 用；草稿还原暂无此路径，见交付说明） */
export function putInitialText(paneId: string, text: string): void {
  initialTexts.set(paneId, text);
}

/** 取走初始正文（**取走即删除**，全生命周期只交付一次） */
export function takeInitialText(paneId: string): string | null {
  const text = initialTexts.get(paneId);
  if (text === undefined) return null;
  initialTexts.delete(paneId);
  return text;
}

interface ScratchState {
  /** 全部窗格（按创建顺序；层级看 `geometry.z`） */
  panes: ScratchPane[];
  /** 当前聚焦窗格 id */
  activePaneId: string | null;
  /** 层级计数（`z = ++zCounter`，保证层叠顺序可读） */
  zCounter: number;
  /** 可用区域尺寸（平铺 / 层叠 / 最大化用；由 `ScratchPaneLayer` 上报） */
  viewport: { w: number; h: number };
  /** 最近一次失败原因（自动保存 / 写回失败），供 UI 明确提示（AC-41） */
  lastError: string | null;

  // ── §7.2 的公开 action ──
  /** 新建窗格（快捷键 / 打开文件 / 命令行路径入口**统一走它**），返回新窗格 id */
  createPane: (init?: Partial<ScratchPane>) => string;
  /** 关闭窗格（`dirty` 的询问由调用方负责；干净关闭顺带丢弃草稿） */
  closePane: (id: string) => void;
  /** 聚焦并置顶（`z = ++zCounter`；同时视为"被激活"→ 允许懒挂载） */
  focusPane: (id: string) => void;
  movePane: (id: string, dx: number, dy: number) => void;
  resizePane: (id: string, w: number, h: number) => void;
  toggleMinimize: (id: string) => void;
  toggleMaximize: (id: string) => void;
  tileAll: () => void;
  cascadeAll: () => void;
  setLanguage: (id: string, language: string) => void;
  setViewMode: (id: string, mode: ScratchViewMode) => void;
  /** 脏标记（同时驱动草稿防抖与 Python 侧 `dirtyCount`） */
  markDirty: (id: string, dirty: boolean) => void;
  setFontSize: (id: string, fontSize: number) => void;
  toggleLineNumbers: (id: string) => void;
  toggleWordWrap: (id: string) => void;
  /** 启动还原：**只登记状态，`mounted` 保持 false**（§4.1.3 懒挂载） */
  hydrateFromDrafts: (entries: DraftPaneMeta[]) => void;
  /** 把 `editor.detectAndRead` 的 `ReadItem` 变成窗格；失败条目只记提示，返回 null */
  applyReadResult: (item: ReadItem) => string | null;

  // ── 施工面辅助 action（§7.2 未列，组件需要用；已在交付说明中登记）──
  /** 懒挂载标记（IntersectionObserver / 激活时由 `ScratchPane` 置位） */
  setMounted: (id: string, mounted: boolean) => void;
  /** 上报可用区域尺寸（`ScratchPaneLayer` 挂载 / resize 时调用） */
  setViewport: (w: number, h: number) => void;
  /** 立刻落盘所有待写内容（退出握手前调用；会清空全部防抖计时器） */
  flushPendingSaves: () => Promise<void>;
  /**
   * **手动保存**（AC-36 / AC-37）—— `Ctrl+S` / `Ctrl+Shift+S` 与"关闭前保存"统一走它。
   *
   * - 窗格**从未落过盘**（`path === null`）或 `forceDialog`（= 另存为）⇒ 先弹**原生**保存对话框
   *   （`scratchService.pickSaveTarget`）；用户取消 ⇒ **磁盘零变化**且脏标记保持（AC-36）；
   * - 已有路径且非另存为 ⇒ **不弹任何对话框**，直接写回同一路径（AC-37）；
   * - 只读窗格（`.diff` / `.patch` / 大文件降级，AC-19）直接拒绝，不产生任何写入；
   * - 成功 ⇒ 路径 / 标题 / 脏标记一起更新；失败 ⇒ 只给提示（`lastError`），内容与脏标记不变（AC-41）。
   *
   * @returns `true` = 真的写盘成功（取消 / 只读 / 写失败都是 `false`）
   */
  savePane: (id: string, options?: { forceDialog?: boolean }) => Promise<boolean>;
  /** 清掉最近一次错误提示 */
  clearError: () => void;
}

export const useScratchStore = create<ScratchState>((set, get) => {
  /** 清掉某个窗格的防抖计时器 */
  const clearFlushTimer = (paneId: string): void => {
    const timer = flushTimers.get(paneId);
    if (timer !== undefined) {
      clearTimeout(timer);
      flushTimers.delete(paneId);
    }
  };

  /** 把脏窗格数量报给 Python（退出握手前置，AC-56）；失败不打扰用户 */
  const syncDirtyCount = (): void => {
    const count = get().panes.filter((pane) => pane.dirty).length;
    void scratchService.reportDirty(count).catch(() => {});
  };

  /** 防抖调度：同一窗格的连续编辑只保留最后一次 */
  const scheduleFlush = (paneId: string): void => {
    clearFlushTimer(paneId);
    flushTimers.set(
      paneId,
      setTimeout(() => {
        flushTimers.delete(paneId);
        void flushPane(paneId);
      }, AUTO_SAVE_DEBOUNCE_MS),
    );
  };

  /**
   * 防抖到点后的落盘：**先草稿库，后（仅 `path != null` 时）用户文件**。
   *
   * 失败语义（AC-41 / §6.2.2）：内容、脏标记、草稿**都不变**，只给明确提示。
   */
  const flushPane = async (paneId: string): Promise<void> => {
    const pane = get().panes.find((item) => item.id === paneId);
    if (!pane || !pane.dirty) return; // 不脏不写（AC-54：内容不变再等一个周期，草稿 mtime 不变）
    const accessor = contentAccessors.get(paneId);
    if (!accessor) return; // 未挂载窗格没有正文可写

    let snapshot: ReturnType<ScratchContentAccessor>;
    try {
      snapshot = accessor();
    } catch {
      return;
    }
    const seqAtStart = editSeqs.get(paneId) ?? 0;

    try {
      // ① 草稿库：两种形态都写 —— 草稿形态的**唯一**落点（§6.3.2）
      await scratchService.draftSave(
        toDraftPaneMeta(pane, { caret: snapshot.caret, scrollTop: snapshot.scrollTop }),
        snapshot.text,
      );
      // ② 用户文件：仅 `path != null` 且非只读（AC-40 / AC-19）
      const wroteUserFile = pane.path !== null && !pane.readOnly;
      if (wroteUserFile && pane.path !== null) {
        await scratchService.writeText({
          path: pane.path,
          text: snapshot.text,
          encoding: pane.encoding,
          lineEnding: pane.lineEnding,
        });
      }
      // 写回成功且期间没有新编辑 ⇒ 脏标记落下；有编辑则保持，等下一轮防抖
      if (wroteUserFile && (editSeqs.get(paneId) ?? 0) === seqAtStart) {
        set((state) => ({
          panes: state.panes.map((item) =>
            item.id === paneId ? { ...item, dirty: false } : item,
          ),
        }));
        syncDirtyCount();
      }
    } catch (error) {
      set({ lastError: describeError(error) });
    }
  };

  /** 新建窗格时的层叠落点（在可用区域内的固定起点 + 24 px 递进） */
  const cascadeGeometryFor = (index: number): PaneGeometry => {
    const { w: viewportW, h: viewportH } = get().viewport;
    const step = (index % 8) * CASCADE_OFFSET;
    const maxX = Math.max(0, viewportW - PANE_MIN_WIDTH);
    const maxY = Math.max(0, viewportH - PANE_MIN_HEIGHT);
    return {
      x: Math.min(CASCADE_BASE_X + step, maxX),
      y: Math.min(CASCADE_BASE_Y + step, maxY),
      w: Math.min(DEFAULT_PANE_WIDTH, Math.max(PANE_MIN_WIDTH, viewportW)),
      h: Math.min(DEFAULT_PANE_HEIGHT, Math.max(PANE_MIN_HEIGHT, viewportH)),
      z: get().zCounter + 1,
      min: false,
      maxed: false,
    };
  };

  return {
    panes: [],
    activePaneId: null,
    zCounter: 0,
    viewport:
      typeof window === 'undefined'
        ? { w: 1280, h: 800 }
        : { w: window.innerWidth, h: window.innerHeight },
    lastError: null,

    createPane: (init) => {
      const current = get();
      const z = current.zCounter + 1;
      const geometry = init?.geometry ?? { ...cascadeGeometryFor(current.panes.length), z };
      const language = init?.language ?? 'text';
      const pane: ScratchPane = {
        id: init?.id ?? newPaneId(),
        title: init?.title ?? '未命名',
        path: init?.path ?? null,
        language,
        detected: init?.detected ?? language,
        viewMode: init?.viewMode ?? 'edit',
        encoding: init?.encoding ?? 'utf-8',
        lineEnding: init?.lineEnding ?? 'lf',
        readOnly: init?.readOnly ?? false,
        dirty: init?.dirty ?? false,
        geometry,
        caret: init?.caret ?? { line: 1, col: 1 },
        scrollTop: init?.scrollTop ?? 0,
        fontSize: init?.fontSize ?? DEFAULT_FONT_SIZE,
        showLineNumbers: init?.showLineNumbers ?? true,
        wordWrap: init?.wordWrap ?? false,
        // 新建即当前激活窗格（§4.1.3 ②"窗格被激活"⇒ 允许建实例）；
        // 还原路径走 hydrateFromDrafts，那里**保持 false**。
        mounted: init?.mounted ?? true,
      };
      set((state) => ({
        panes: [...state.panes, pane],
        zCounter: z,
        activePaneId: pane.id,
        lastError: null,
      }));
      return pane.id;
    },

    closePane: (id) => {
      const pane = get().panes.find((item) => item.id === id);
      if (!pane) return;
      clearFlushTimer(id);
      contentAccessors.delete(id);
      editSeqs.delete(id);
      initialTexts.delete(id);
      restoreGeometries.delete(id);
      set((state) => {
        const panes = state.panes.filter((item) => item.id !== id);
        const activePaneId =
          state.activePaneId === id
            ? (panes.reduce<ScratchPane | null>(
                (top, item) => (top === null || item.geometry.z > top.geometry.z ? item : top),
                null,
              )?.id ?? null)
            : state.activePaneId;
        return { panes, activePaneId };
      });
      // 干净关闭 ⇒ 不再需要草稿（否则重启会把已关的窗格又还原出来）。
      // 脏窗格的去留由调用方决定：保存分支走 writeText，放弃分支由组件调 `editor.draftDiscard`。
      if (!pane.dirty) {
        void scratchService.draftDiscard(id).catch(() => {});
      }
      syncDirtyCount();
    },

    focusPane: (id) => {
      const z = get().zCounter + 1;
      set((state) => ({
        zCounter: z,
        activePaneId: id,
        panes: state.panes.map((item) =>
          item.id === id ? { ...item, geometry: { ...item.geometry, z }, mounted: true } : item,
        ),
      }));
    },

    movePane: (id, dx, dy) => {
      set((state) => ({
        panes: state.panes.map((item) => {
          if (item.id !== id) return item;
          const { w } = item.geometry;
          // 保证窗格至少留一条可抓取的可见带（不限制在视口内，用户可以把窗格拖出去一部分）
          const minX = -(w - PANE_MIN_WIDTH / 2);
          const maxX = state.viewport.w - PANE_MIN_WIDTH / 2;
          const minY = 0;
          const maxY = Math.max(0, state.viewport.h - PANE_MIN_HEIGHT / 2);
          return {
            ...item,
            geometry: {
              ...item.geometry,
              x: Math.min(Math.max(item.geometry.x + dx, minX), maxX),
              y: Math.min(Math.max(item.geometry.y + dy, minY), maxY),
            },
          };
        }),
      }));
    },

    resizePane: (id, w, h) => {
      set((state) => ({
        panes: state.panes.map((item) =>
          item.id === id
            ? {
                ...item,
                geometry: {
                  ...item.geometry,
                  w: Math.max(PANE_MIN_WIDTH, Math.round(w)),
                  h: Math.max(PANE_MIN_HEIGHT, Math.round(h)),
                },
              }
            : item,
        ),
      }));
    },

    toggleMinimize: (id) => {
      const pane = get().panes.find((item) => item.id === id);
      if (!pane) return;
      if (!pane.geometry.min) restoreGeometries.set(id, { ...pane.geometry });
      set((state) => ({
        panes: state.panes.map((item) =>
          item.id === id
            ? { ...item, geometry: { ...item.geometry, min: !item.geometry.min } }
            : item,
        ),
      }));
    },

    toggleMaximize: (id) => {
      const pane = get().panes.find((item) => item.id === id);
      if (!pane) return;
      const z = get().zCounter + 1;
      if (!pane.geometry.maxed) {
        restoreGeometries.set(id, { ...pane.geometry });
        const { w, h } = get().viewport;
        set((state) => ({
          zCounter: z,
          activePaneId: id,
          panes: state.panes.map((item) =>
            item.id === id
              ? {
                  ...item,
                  geometry: { x: 0, y: 0, w, h, z, min: false, maxed: true },
                }
              : item,
          ),
        }));
        return;
      }
      const restored = restoreGeometries.get(id) ?? pane.geometry;
      set((state) => ({
        zCounter: z,
        activePaneId: id,
        panes: state.panes.map((item) =>
          item.id === id
            ? { ...item, geometry: { ...restored, z, min: false, maxed: false } }
            : item,
        ),
      }));
    },

    tileAll: () => {
      const targets = get().panes.filter((pane) => !pane.geometry.min);
      if (targets.length === 0) return;
      const cols = Math.max(1, Math.ceil(Math.sqrt(targets.length)));
      const rows = Math.ceil(targets.length / cols);
      const { w: viewportW, h: viewportH } = get().viewport;
      const cellW = Math.max(PANE_MIN_WIDTH, Math.floor(viewportW / cols) - TILE_GAP);
      const cellH = Math.max(PANE_MIN_HEIGHT, Math.floor(viewportH / rows) - TILE_GAP);
      const targetIds = new Set(targets.map((pane) => pane.id));

      set((state) => ({
        panes: state.panes.map((item) => {
          if (!targetIds.has(item.id)) return item;
          const index = targets.findIndex((pane) => pane.id === item.id);
          const col = index % cols;
          const row = Math.floor(index / cols);
          return {
            ...item,
            geometry: {
              ...item.geometry,
              x: col * (cellW + TILE_GAP),
              y: row * (cellH + TILE_GAP),
              w: cellW,
              h: cellH,
              maxed: false,
            },
          };
        }),
      }));
    },

    cascadeAll: () => {
      const targets = get().panes.filter((pane) => !pane.geometry.min);
      if (targets.length === 0) return;
      const targetIds = new Set(targets.map((pane) => pane.id));
      const { w: viewportW, h: viewportH } = get().viewport;

      set((state) => ({
        panes: state.panes.map((item) => {
          if (!targetIds.has(item.id)) return item;
          const index = targets.findIndex((pane) => pane.id === item.id);
          const step = index * CASCADE_OFFSET;
          return {
            ...item,
            geometry: {
              ...item.geometry,
              x: Math.min(CASCADE_BASE_X + step, Math.max(0, viewportW - PANE_MIN_WIDTH)),
              y: Math.min(CASCADE_BASE_Y + step, Math.max(0, viewportH - PANE_MIN_HEIGHT)),
              maxed: false,
            },
          };
        }),
      }));
    },

    setLanguage: (id, language) => {
      // 只接受类型表内的语言 id（§5.1 的 LANGUAGES 派生量）
      if (!LANGUAGES.some((item) => item === language)) return;
      set((state) => ({
        panes: state.panes.map((item) => {
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
        panes: state.panes.map((item) => {
          if (item.id !== id) return item;
          // 能力表校验：不可用则不动（§4.1.2）
          if (!isViewModeAllowed(mode, capabilitiesForPane(item))) return item;
          return { ...item, viewMode: mode };
        }),
      }));
    },

    markDirty: (id, dirty) => {
      // 编辑序号只增不减：用于判断异步写回期间是否又发生了编辑
      editSeqs.set(id, (editSeqs.get(id) ?? 0) + 1);
      set((state) => ({
        panes: state.panes.map((item) => (item.id === id ? { ...item, dirty } : item)),
      }));
      if (dirty) scheduleFlush(id);
      else clearFlushTimer(id);
      syncDirtyCount();
    },

    setFontSize: (id, fontSize) => {
      const clamped = Math.min(FONT_SIZE_MAX, Math.max(FONT_SIZE_MIN, Math.round(fontSize)));
      set((state) => ({
        panes: state.panes.map((item) => (item.id === id ? { ...item, fontSize: clamped } : item)),
      }));
    },

    toggleLineNumbers: (id) => {
      set((state) => ({
        panes: state.panes.map((item) =>
          item.id === id ? { ...item, showLineNumbers: !item.showLineNumbers } : item,
        ),
      }));
    },

    toggleWordWrap: (id) => {
      set((state) => ({
        panes: state.panes.map((item) =>
          item.id === id ? { ...item, wordWrap: !item.wordWrap } : item,
        ),
      }));
    },

    hydrateFromDrafts: (entries) => {
      const restored: ScratchPane[] = [];
      entries.forEach((meta) => {
        // 索引可能被半截写入 / 被外部改坏：字段不合法就**跳过该条**（不阻塞启动，§6.3.2）
        if (!meta || typeof meta.id !== 'string' || meta.id.length === 0) return;
        const geometry = normalizeGeometry(meta.geometry);
        restored.push({
          id: meta.id,
          title: typeof meta.title === 'string' && meta.title ? meta.title : '未命名',
          path: typeof meta.path === 'string' && meta.path ? meta.path : null,
          language: typeof meta.language === 'string' && meta.language ? meta.language : 'text',
          detected: typeof meta.language === 'string' && meta.language ? meta.language : 'text',
          viewMode: 'edit',
          encoding: normalizeEncoding(meta.encoding),
          lineEnding: normalizeLineEnding(meta.lineEnd),
          readOnly: Boolean(meta.read_only),
          dirty: Boolean(meta.dirty),
          geometry,
          caret: normalizeCaret(meta.caret),
          scrollTop: typeof meta.scroll_top === 'number' ? meta.scroll_top : 0,
          fontSize: typeof meta.font_size === 'number' ? meta.font_size : DEFAULT_FONT_SIZE,
          showLineNumbers: meta.show_line_numbers !== false,
          wordWrap: Boolean(meta.word_wrap),
          // §4.1.3：还原阶段**只登记状态**，不建 CM6 实例
          mounted: false,
        });
      });

      set((state) => {
        const existing = new Set(state.panes.map((item) => item.id));
        // 幂等：同 id 已在场则跳过（重复调用 / 事件重放都不会翻倍）
        const added = restored.filter((item) => !existing.has(item.id));
        if (added.length === 0) return {};
        const panes = [...state.panes, ...added];
        const top = panes.reduce<ScratchPane | null>(
          (acc, item) => (acc === null || item.geometry.z > acc.geometry.z ? item : acc),
          null,
        );
        return {
          panes,
          activePaneId: state.activePaneId ?? top?.id ?? null,
          zCounter: Math.max(state.zCounter, ...added.map((item) => item.geometry.z)),
        };
      });
    },

    applyReadResult: (item) => {
      if (!item.ok) {
        // 二进制 / 过大 / 编码不可判定 / 不存在：只提示（文案取自 Python，**不含完整路径**）
        set({ lastError: item.message || `${basenameOf(item.path)} 无法打开` });
        return null;
      }
      const info = resolveTypeForPath(item.path);
      // M-13 / AC-45：**打开文件**（双击关联 / 打开对话框 / 拖入）一律先落编辑器视图；
      // 类型表里的 `defaultViewMode`（如 `.csv` 的 `table`）只作为**切语言 / 切类型**时的
      // 推荐视图（见 `setLanguage` → `getDefaultViewModeForLanguage`），不决定打开态。
      const id = get().createPane({
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
        panes: state.panes.map((item) => (item.id === id ? { ...item, mounted } : item)),
      }));
    },

    setViewport: (w, h) => {
      if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return;
      set({ viewport: { w: Math.round(w), h: Math.round(h) } });
    },

    flushPendingSaves: async () => {
      const pendingIds = Array.from(flushTimers.keys());
      pendingIds.forEach((paneId) => clearFlushTimer(paneId));
      await Promise.all(pendingIds.map((paneId) => flushPane(paneId)));
    },

    savePane: async (id, options) => {
      const pane = get().panes.find((item) => item.id === id);
      if (!pane) return false;

      /**
       * 失败 / 取消的统一收尾：把该窗格的自动保存**重新排上队**（审查 M4）。
       *
       * 写盘前会 `clearFlushTimer(id)`；若此后直接 `return false`，原本 1500 ms 后
       * 会把内容落到**草稿库**的那次 flush 也随之消失 —— 用户此时关进程，
       * 最后一次编辑既不进用户文件、也不进草稿库。仅在"仍脏且当前没有计时器"时补排。
       */
      const rescheduleFlush = (): false => {
        const stillDirty = get().panes.find((item) => item.id === id)?.dirty ?? false;
        if (stillDirty && !flushTimers.has(id)) scheduleFlush(id);
        return false;
      };

      // 只读窗格（AC-19 / AC-52）：保存入口直接拒绝，连磁盘都不碰
      if (pane.readOnly) {
        set({ lastError: '只读窗格不支持保存' });
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
      let target = pane.path;
      if (options?.forceDialog === true || target === null) {
        try {
          target = await scratchService.pickSaveTarget(pane.title || undefined);
        } catch (error) {
          set({ lastError: describeError(error) });
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
          encoding: pane.encoding,
          lineEnding: pane.lineEnding,
        })) as unknown as { ok?: boolean; reason?: string; error?: string } | null;
      } catch (error) {
        set({ lastError: describeError(error) });
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
                : describeError(result?.error ?? '保存失败'),
        });
        return rescheduleFlush();
      }

      // ④ 成功：路径 / 标题（文件名）/ 脏标记一起落地；期间若又有编辑则保持脏，等下一轮防抖
      const stillClean = (editSeqs.get(id) ?? 0) === seqAtStart;
      set((state) => ({
        panes: state.panes.map((item) =>
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
  };
});

// ── 草稿条目字段的容错归一（索引损坏时不能让整次还原崩掉）──

function normalizeGeometry(raw: PaneGeometry | undefined): PaneGeometry {
  const source = raw ?? ({} as PaneGeometry);
  const num = (value: unknown, fallback: number): number =>
    typeof value === 'number' && Number.isFinite(value) ? value : fallback;
  return {
    x: num(source.x, CASCADE_BASE_X),
    y: num(source.y, CASCADE_BASE_Y),
    w: Math.max(PANE_MIN_WIDTH, num(source.w, DEFAULT_PANE_WIDTH)),
    h: Math.max(PANE_MIN_HEIGHT, num(source.h, DEFAULT_PANE_HEIGHT)),
    z: num(source.z, 1),
    min: Boolean(source.min),
    maxed: Boolean(source.maxed),
  };
}

function normalizeCaret(raw: PaneCaret | undefined): PaneCaret {
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

/** 选择器：脏窗格数量（退出握手 / 标题栏提示用） */
export function selectDirtyCount(state: ScratchState): number {
  return state.panes.filter((pane) => pane.dirty).length;
}

/** 选择器：当前聚焦窗格 */
export function selectActivePane(state: ScratchState): ScratchPane | null {
  if (!state.activePaneId) return null;
  return state.panes.find((pane) => pane.id === state.activePaneId) ?? null;
}

/** 选择器：按 id 取窗格（找不到返回 null） */
export function selectPaneById(state: ScratchState, id: string): ScratchPane | null {
  return state.panes.find((pane) => pane.id === id) ?? null;
}

/** 选择器：某窗格是否具备某项能力（工具栏按能力禁用按钮用） */
export function selectPaneHasCapability(
  state: ScratchState,
  id: string,
  capability: ScratchCapability,
): boolean {
  const pane = selectPaneById(state, id);
  if (!pane) return false;
  return hasCapability(resolveTypeForPath(pane.path ?? ''), capability);
}

/** 类型再导出：窗格模型（设计 §4.1.2 把它定义在 store 小节，这里保持同名导出） */
export type { ScratchPane };

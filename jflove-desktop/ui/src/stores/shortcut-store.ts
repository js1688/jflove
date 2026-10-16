/**
 * 快捷键状态管理（设计 v1.6.0 §4.2 / §4.2.4）
 *
 * ## 职责边界
 *
 * - 默认键位与中文说明在 `config/shortcuts.ts`（**唯一手写真相**，本文件不重复定义）；
 * - 本文件只管三件事：**覆盖值（overrides）**、**冲突检测**、**经桥落盘**；
 * - 落盘位置 `USER_DATA_DIR/storage/shortcuts.json` **由 Python 侧读写** ——
 *   渲染层既不碰文件系统，也**不用 localStorage**（R-1 实测：默认 profile 为 off-the-record，
 *   localStorage 写了就丢）。
 *
 * ## 持久化语义（逐条对齐 §4.2.4）
 *
 * | 动作 | 是否立即落盘 | 说明 |
 * | --- | --- | --- |
 * | `setBinding` / `resetBinding` | ❌ 否 | 只改内存，等用户点「保存」——避免误配被静默持久化 |
 * | `save` | ✅ 是 | **先做全表冲突检测**：有重复即拒绝保存（AC-9），失败时内存态回滚 |
 * | `resetAll`（一键恢复默认） | ✅ 是 | 设计明确「写 `{"version":1,"bindings":{}}`」，故立即落盘；失败回滚 |
 * | `load` | — | `version` 不匹配 / 键名不存在 / 键位非法 ⇒ **忽略该项回默认**，不阻塞启动 |
 *
 * ## 不注册全局热键（AC-8）
 *
 * 本 store 只产数据；应用级监听由组件在 `window` 捕获阶段实现，匹配时调用
 * `config/shortcuts.ts` 的 `keyboardEventToShortcut`（与冲突检测共用同一套归一化规则）。
 */

import { create } from 'zustand';
import {
  BLOCK_SELECT_MODIFIER_ID,
  DEFAULT_BINDINGS,
  DEFAULT_BLOCK_SELECT_MODIFIER,
  SHORTCUTS_VERSION,
  findConflictingShortcuts,
  getShortcutDef,
  normalizeShortcut,
  requiresModifierOnly,
  toOverrides,
  withOverrides,
} from '../config/shortcuts';
import type { ShortcutConflict, ShortcutId } from '../config/shortcuts';
import { scratchService } from '../services/scratch-service';
import type { BlockSelectModifier, ShortcutsFileV1 } from '../types/scratch';
// 缺陷 L-7：共享「异常 → 文案」叶子模块（本 store 不再自带一份实现）
import { ERROR_FALLBACK, describeErrorWithFallback } from '../utils/error-text';

interface ShortcutState {
  /** **只存与默认值不同的项**（overrides 语义，§4.2.4）；这是持久化的唯一真相 */
  overrides: Record<string, string>;
  /** 默认值 + overrides 的生效映射（派生缓存，避免 keydown 热路径每次重算） */
  bindings: Record<string, string>;
  /** 是否已完成一次加载尝试（失败也算完成，避免反复重试阻塞启动） */
  loaded: boolean;
  loading: boolean;
  saving: boolean;
  /** 最近一次冲突检测结果（空数组 = 无冲突） */
  conflicts: ShortcutConflict[];
  /** 最近一次失败原因（加载 / 保存 / 非法键位），供设置页明确提示 */
  lastError: string | null;

  /** 取生效键位（归一化形式） */
  getBinding: (id: ShortcutId) => string;
  /** 改键（只改内存；非法值不改状态并记 `lastError`） */
  setBinding: (id: ShortcutId, key: string) => void;
  /** 单项重置 = 删除该 id 的 overrides 项（不落盘，等「保存」） */
  resetBinding: (id: ShortcutId) => void;
  /** 一键恢复默认 = 清空 overrides **并立即落盘** */
  resetAll: () => Promise<boolean>;
  /** 全表冲突检测（返回并写入 `conflicts`） */
  detectConflicts: () => ShortcutConflict[];
  /** 从 Python 侧读取偏好（幂等；失败回默认不阻塞启动） */
  load: () => Promise<void>;
  /** 保存到 Python 侧（有冲突则拒绝保存并返回 false） */
  save: () => Promise<boolean>;
}

/** 由 overrides 推导状态（单一派生入口，避免 overrides 与 bindings 漂移） */
function deriveState(overrides: Record<string, string>): {
  overrides: Record<string, string>;
  bindings: Record<string, string>;
} {
  return { overrides, bindings: withOverrides(overrides) };
}

/** 把异常转成可展示文案（不含路径与内容，§7.4）—— 兜底文案保持抽取前原样 */
function describeShortcutError(error: unknown): string {
  return describeErrorWithFallback(error, ERROR_FALLBACK.SHORTCUT);
}

/** 由生效映射构造落盘结构（`bindings` 只写覆盖项；`blockSelectModifier` 派生自第 12 项） */
function buildShortcutsFile(bindings: Record<string, string>): ShortcutsFileV1 {
  return {
    version: SHORTCUTS_VERSION,
    bindings: toOverrides(bindings),
    blockSelectModifier: resolveBlockSelectModifier(bindings[BLOCK_SELECT_MODIFIER_ID]),
  };
}

/**
 * 块选择修饰键的派生口径（§6.1.2）：只有 `Ctrl` 是另一个可选项，其余一律回 `Alt`。
 *
 * 含义：用户把块选择改成 `Ctrl` 后，**`Alt` 自动退化为普通（流式）选择** ——
 * 同一个 `eventFilter` 只认一个修饰键，天然满足 AC-12。
 */
export function resolveBlockSelectModifier(value: string | undefined): BlockSelectModifier {
  return normalizeShortcut(value ?? '') === 'Ctrl' ? 'Ctrl' : DEFAULT_BLOCK_SELECT_MODIFIER;
}

export const useShortcutStore = create<ShortcutState>((set, get) => ({
  overrides: {},
  bindings: { ...DEFAULT_BINDINGS },
  loaded: false,
  loading: false,
  saving: false,
  conflicts: [],
  lastError: null,

  getBinding: (id) => get().bindings[id] ?? DEFAULT_BINDINGS[id] ?? '',

  setBinding: (id, key) => {
    const def = getShortcutDef(id);
    if (!def) {
      set({ lastError: `未知的快捷键功能：${id}` });
      return;
    }
    const normalized = normalizeShortcut(key);
    if (!normalized) {
      set({ lastError: `无法识别的键位：${key}` });
      return;
    }
    if (requiresModifierOnly(id) && !isAllowedModifierValue(normalized)) {
      set({ lastError: '块选择修饰键只能是 Alt / Ctrl / Shift / Meta' });
      return;
    }
    const nextBindings = { ...get().bindings, [id]: normalized };
    const overrides = toOverrides(nextBindings);
    set({ ...deriveState(overrides), lastError: null });
  },

  resetBinding: (id) => {
    const overrides = { ...get().overrides };
    delete overrides[id];
    set({ ...deriveState(overrides), lastError: null });
  },

  resetAll: async () => {
    const snapshot = get().overrides;
    // 先落内存（界面立刻回到默认），再落盘；失败则回滚（§4.2.4）
    set({ ...deriveState({}), conflicts: [], lastError: null, saving: true });
    try {
      await scratchService.shortcutsSave({
        version: SHORTCUTS_VERSION,
        bindings: {},
        blockSelectModifier: DEFAULT_BLOCK_SELECT_MODIFIER,
      });
      set({ saving: false });
      return true;
    } catch (error) {
      set({ ...deriveState(snapshot), saving: false, lastError: describeShortcutError(error) });
      return false;
    }
  },

  detectConflicts: () => {
    const conflicts = findConflictingShortcuts(get().bindings);
    set({ conflicts });
    return conflicts;
  },

  load: async () => {
    set({ loading: true });
    try {
      const file = await scratchService.shortcutsLoad();
      if (file.version !== SHORTCUTS_VERSION) {
        // 版本不匹配 ⇒ 忽略文件回默认（§4.2.4，不阻塞启动）
        set({ ...deriveState({}), loading: false, loaded: true, lastError: null });
        return;
      }
      const source: Record<string, string> = { ...file.bindings };
      // 顶层 blockSelectModifier 是块选择项的真源（§4.2.4 的结构里它单独一个字段）
      if (file.blockSelectModifier) source[BLOCK_SELECT_MODIFIER_ID] = file.blockSelectModifier;
      // withOverrides 会过滤未知 id / 非法键位 / 与默认值相同的项
      const overrides = toOverrides(withOverrides(source));
      set({ ...deriveState(overrides), loading: false, loaded: true, lastError: null });
    } catch (error) {
      // 桥未就绪 / 方法未登记 / 文件不可读：一律回默认，只留提示
      set({
        ...deriveState({}),
        loading: false,
        loaded: true,
        lastError: describeShortcutError(error),
      });
    }
  },

  save: async () => {
    const conflicts = findConflictingShortcuts(get().bindings);
    if (conflicts.length > 0) {
      // AC-9：发现重复即拒绝保存并在该行标红 + 提示，**绝不静默覆盖**
      set({ conflicts, lastError: '存在重复键位，未保存' });
      return false;
    }
    const snapshot = get().overrides;
    set({ saving: true, conflicts: [], lastError: null });
    try {
      await scratchService.shortcutsSave(buildShortcutsFile(get().bindings));
      set({ saving: false });
      return true;
    } catch (error) {
      set({ ...deriveState(snapshot), saving: false, lastError: describeShortcutError(error) });
      return false;
    }
  },
}));

/** 块选择修饰键的值是否合法（只能是修饰键本身） */
function isAllowedModifierValue(normalized: string): boolean {
  return (
    normalized === 'Alt' || normalized === 'Ctrl' || normalized === 'Shift' || normalized === 'Meta'
  );
}

/** 选择器：生效键位映射（设置页批量渲染用） */
export function selectBindings(state: ShortcutState): Record<string, string> {
  return state.bindings;
}

/** 选择器：块选择修饰键（默认 `Alt`；改 `Ctrl` 后 `Alt` 退化为普通选择，§6.1.2） */
export function selectBlockSelectModifier(state: ShortcutState): BlockSelectModifier {
  return resolveBlockSelectModifier(state.bindings[BLOCK_SELECT_MODIFIER_ID]);
}

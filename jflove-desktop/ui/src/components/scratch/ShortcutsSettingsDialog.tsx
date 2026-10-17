/**
 * 快捷键配置对话框（设计 v1.6.1 §4.7 / v1.6.0 §4.2；设置页「文本编辑器」Section 的子块 ①，§4.3）
 *
 * ## 硬口径（逐条对齐设计 / 任务要求）
 *
 * 1. **18 项逐项呈现**：行数据全部来自 `config/shortcuts.ts::SHORTCUT_DEFS`（唯一手写真相），
 *    每行显示「功能名 + 中文功能说明 + 默认键 + 当前键 + 修改 + 重置」，并支持单项重置与一键恢复默认。
 * 2. **冲突检测**：全表归一化比对由 `stores/shortcut-store.ts::save()` 统一执行（AC-9）；本组件把
 *    `store.conflicts` 实时渲染成顶部横幅 + 标红行（含「该键同时被编辑器内核使用」说明），
 *    冲突时保存被拒绝，**不落盘**、**绝不静默覆盖**。
 * 3. **改键交互**：点「修改」后在 `window` **捕获阶段**监听 `keydown`，用
 *    `keyboardEventToShortcut()`（与冲突检测共用同一套归一化规则）拿到组合键；
 *    **拒绝纯修饰键**作为普通快捷键（块选择修饰键那一项例外 —— 它只能配单个 Alt / Ctrl）。
 * 4. **不注册任何全局热键**（AC-8）：监听只在配置期间存在，且校验 `document.hasFocus() && !document.hidden`；
 *    本组件只产数据，真正的键位匹配发生在快捷键运行时（编辑器工作区挂在 `window` 上的应用级监听里）。
 * 5. **落盘时机**（§4.2.4）：改键 / 单项重置**只改内存**，用户点「保存」才写盘（不做防抖自动写）；
 *    「恢复全部默认」按设计**立即落盘**（写 `{"version":1,"bindings":{}}`），失败由 store 回滚。
 * 6. 渲染层**不打印**路径与内容（§7.4）、**不用** `localStorage`（R-1）、**不发**任何网络请求。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  MODIFIER_KEYS,
  SHORTCUT_DEFS,
  SHORTCUT_SCOPE_GROUPS,
  formatShortcutForDisplay,
  getShortcutDef,
  isModifierOnlyShortcut,
  keyboardEventToShortcut,
  normalizeShortcut,
  parseShortcut,
  shortcutsOfScope,
} from '../../config/shortcuts';
import type { ShortcutDef, ShortcutId } from '../../config/shortcuts';
import { useShortcutStore } from '../../stores/shortcut-store';
import { Button, Modal, toast } from '../ui';

/**
 * 最近一次「已落盘」的 overrides 快照（模块级）。
 *
 * 为什么放在模块级：对话框关闭 / 用户切页再回来后组件会重新挂载，若把基线只存在组件内，
 * 重挂载时会把"尚未保存的改动"误当作"已保存"。模块级变量在应用会话内跨挂载存活，
 * 且只在加载成功 / 保存成功 / 一键重置成功时更新，语义单一。
 */
let savedOverridesSnapshot: string | null = null;

/**
 * 块选择修饰键可接受的取值（§6.1.2）。
 *
 * 只有 `Ctrl` 是另一个可选项：其余取值会被 `resolveBlockSelectModifier()` 折叠回 `Alt`，
 * 与其让用户配出一个"看起来生效其实没生效"的值，不如在这里挡掉并说明。
 */
const BLOCK_SELECT_CHOICES: readonly string[] = ['Alt', 'Ctrl'];

/** 键盘事件的 `key` → 归一化修饰键名（`Control` → `Ctrl`，与 `config/shortcuts.ts` 口径一致） */
const EVENT_KEY_MODIFIERS: Readonly<Record<string, 'Ctrl' | 'Alt' | 'Shift' | 'Meta'>> = {
  Control: 'Ctrl',
  Ctrl: 'Ctrl',
  Alt: 'Alt',
  Shift: 'Shift',
  Meta: 'Meta',
  OS: 'Meta',
};

/**
 * 显示用的键位文案。
 *
 * ⚠ **已知缺陷（在 `config/shortcuts.ts` / `stores/shortcut-store.ts` 里，本任务禁止改动）**：
 * `formatShortcut()` 对「仅修饰键」条目会把修饰键**再拼一次** —— 块选择修饰键的默认值 `Alt`
 * 归一化后是 `Alt+Alt`（探针实测：`normalizeShortcut('Alt') === 'Alt+Alt'`，
 * 且 `defaultNormalizationConsistent()` 为 `false`）。
 * 展示层在这里去掉重复的那一次（取归一化后的键名本身），避免界面出现 `Alt+Alt` 这种怪值；
 * **存储与归一化口径未动**，落盘内容仍由 store 决定。
 */
export function formatBindingForDisplay(def: ShortcutDef, raw: string): string {
  if (def.meta.modifierOnly) {
    const chord = parseShortcut(raw);
    if (chord) return chord.key;
  }
  return formatShortcutForDisplay(raw);
}

/**
 * 该项是否**被用户真正改过**（用于「已自定义 N 项」与单项重置按钮的可用性）。
 *
 * 与 `overrides[id] !== undefined` 的差别：**仅修饰键**条目（块选择修饰键）只接受单个修饰键，
 * 归一化后可能与默认值是**同一语义**（同一个修饰键）—— 此时不该算"用户改过"，
 * 否则全新配置也会显示"已自定义 1 项"、并让该行的「重置」看起来可用。
 *
 * 勘误（Phase 5 审查 D-2）：原注释用 `toOverrides(DEFAULT_BINDINGS)` 返回
 * `{"edit.blockSelectModifier":"Alt+Alt"}` 当论据 —— 该值**实测为 `{}`**（`toOverrides`
 * 会剔除与默认归一值相同的项，见 `config/shortcuts.ts:504-515`；而 `DEFAULT_BINDINGS`
 * 本身取的就是归一化默认值，见同文件 `:266-271`），论据已失效；函数保留是因为上面这条
 * "同语义不算改动"的判定本身仍然需要（用户把修饰键改回默认时不应留下"已自定义"痕迹）。
 *
 * 附带结论：`toOverrides` 会剔除掉与默认同归一值的项，且 store 的落盘数据也统一经它过一遍
 * （`stores/shortcut-store.ts:181`）⇒ 当前代码里**到不了**"`overrides[id]` 存在但语义等于默认"
 * 的情形，下面 `modifierOnly` 那段已是**防御性分支**（防外部手改的落盘文件），不是活路径。
 */
export function isUserCustomizedShortcut(
  def: ShortcutDef,
  overrides: Record<string, string>,
  bindings: Record<string, string>,
): boolean {
  if (overrides[def.id] === undefined) return false;
  if (!def.meta.modifierOnly) return true;
  // 仅修饰键条目：与默认值语义相同（同一个修饰键）即不算用户改动
  const current = parseShortcut(bindings[def.id] ?? def.defaultKey)?.key;
  const fallback = parseShortcut(def.defaultKey)?.key;
  return current !== fallback;
}

export interface ShortcutsSettingsDialogProps {
  open: boolean;
  onClose: () => void;
}

export function ShortcutsSettingsDialog({ open, onClose }: ShortcutsSettingsDialogProps) {
  const loaded = useShortcutStore((s) => s.loaded);
  const loading = useShortcutStore((s) => s.loading);
  const saving = useShortcutStore((s) => s.saving);
  const bindings = useShortcutStore((s) => s.bindings);
  const overrides = useShortcutStore((s) => s.overrides);
  const conflicts = useShortcutStore((s) => s.conflicts);
  const crossScopeNotes = useShortcutStore((s) => s.crossScopeNotes);
  const lastError = useShortcutStore((s) => s.lastError);

  /** 正在等待按键的功能 id（`null` = 未处于配置态） */
  const [capturingId, setCapturingId] = useState<ShortcutId | null>(null);
  /** 配置态的即时提示（非法按键 / 被拒绝的原因），由本组件逐条给出，不静默丢弃 */
  const [captureHint, setCaptureHint] = useState<string | null>(null);
  /** 「恢复全部默认」的二次确认（就地确认，不叠第二层弹窗） */
  const [confirmResetAll, setConfirmResetAll] = useState(false);
  /** 最近一次「已落盘」的 overrides 快照（用于"有未保存修改"提示），初值取模块级缓存 */
  const [savedSnapshot, setSavedSnapshot] = useState<string | null>(savedOverridesSnapshot);

  // 建立"已保存"基线：只在还没有基线时建立，避免把未保存改动误判成已保存
  useEffect(() => {
    if (!loaded || savedSnapshot !== null) return;
    const snapshot = savedOverridesSnapshot ?? JSON.stringify(useShortcutStore.getState().overrides);
    savedOverridesSnapshot = snapshot;
    setSavedSnapshot(snapshot);
  }, [loaded, savedSnapshot]);

  // 打开对话框时若尚未加载过偏好则加载一次（幂等；失败回默认不阻塞，由 lastError 提示）
  useEffect(() => {
    if (!open) return;
    if (!useShortcutStore.getState().loaded) void useShortcutStore.getState().load();
  }, [open]);

  // 生效键位变化 ⇒ 立刻重跑全表冲突检测（AC-9 的"改完就能看见重复"）
  useEffect(() => {
    if (!open) return;
    useShortcutStore.getState().detectConflicts();
  }, [open, bindings]);

  const conflictingIds = useMemo(
    () => new Set(conflicts.flatMap((conflict) => conflict.ids)),
    [conflicts],
  );
  /** 用户真正改过的项数（口径见 `isUserCustomizedShortcut` 的说明） */
  const customizedCount = useMemo(
    () => SHORTCUT_DEFS.filter((def) => isUserCustomizedShortcut(def, overrides, bindings)).length,
    [overrides, bindings],
  );
  const dirty = savedSnapshot !== null && savedSnapshot !== JSON.stringify(overrides);

  /** 记录"已落盘"基线（保存 / 一键重置成功后调用） */
  const rememberSaved = useCallback(() => {
    const snapshot = JSON.stringify(useShortcutStore.getState().overrides);
    savedOverridesSnapshot = snapshot;
    setSavedSnapshot(snapshot);
  }, []);

  /** 写入一次改键结果：store 负责校验（非法值 / 修饰键限制）与归一化，被拒绝时不退出配置态 */
  const applyBinding = useCallback((id: ShortcutId, value: string) => {
    useShortcutStore.getState().setBinding(id, value);
    const state = useShortcutStore.getState();
    if (state.lastError) {
      // 未写入：保留配置态并把明确原因显示在该行（绝不静默丢弃用户按键）
      // 说明（Phase 5 审查 **L-4**）：原先这里对"仅修饰键"额外拼了一段「已知缺陷：会被拼成 Alt+Alt」，
      // 该缺陷在 v1.6.x 已不存在（`setBinding` 走 `isAllowedModifierValue` 白名单，Alt/Ctrl/Shift/Meta 均可改），
      // 陈旧文案反而会误导用户 ⇒ 统一只显示 store 给出的 `lastError`。
      setCaptureHint(state.lastError);
      return;
    }
    setCapturingId(null);
    setCaptureHint(null);
    setConfirmResetAll(false);
  }, []);

  /**
   * 配置态按键捕获（**只在对话框打开且处于配置态时挂载**，窗口失焦即不响应 —— AC-8）。
   *
   * 说明：本监听挂在 `window` 捕获阶段，与快捷键运行时的应用级监听同层；后者先注册，
   * 因此当用户按下的键恰好是某个已生效的应用级快捷键时，对应动作仍可能被触发一次
   * （例如按下 `Ctrl+Alt+N` 想把它改给别的功能会顺手新建标签页）。
   * 这正是"该键已被占用"的场景 —— 下面会给出冲突提示，保存会被拒绝。
   * 同时设置 `body[data-jf-shortcut-capture]`，供应用级监听自查"正在配置快捷键"并让路。
   */
  useEffect(() => {
    if (!open || !capturingId) return;
    const def = getShortcutDef(capturingId);
    const modifierOnly = def?.meta.modifierOnly ?? false;

    const onKeyDown = (event: KeyboardEvent) => {
      // AC-8 / §4.2.2：窗口失焦或文档不可见时一律不响应
      if (!document.hasFocus() || document.hidden) return;
      // 配置期间完全接管按键：既挡住宿主 / 编辑器的默认动作，也不让事件继续流向别处
      event.preventDefault();
      event.stopPropagation();
      if (event.repeat) return; // 长按不重复写入
      if (event.key === 'Escape') {
        setCapturingId(null);
        setCaptureHint(null);
        return;
      }

      if (modifierOnly) {
        // 块选择修饰键：只接受**单个**修饰键本身，且只能是 Alt / Ctrl（§6.1.2）
        const pressed = EVENT_KEY_MODIFIERS[event.key];
        if (!pressed) {
          setCaptureHint('块选择修饰键只能是单个修饰键（Alt / Ctrl），不能配字母或组合键');
          return;
        }
        const held = MODIFIER_KEYS.filter((name) =>
          name === 'Ctrl'
            ? event.ctrlKey
            : name === 'Alt'
              ? event.altKey
              : name === 'Shift'
                ? event.shiftKey
                : event.metaKey,
        );
        if (held.length !== 1) {
          setCaptureHint('请只按一个修饰键（同时按住多个不能作为块选择修饰键）');
          return;
        }
        if (!BLOCK_SELECT_CHOICES.includes(held[0])) {
          setCaptureHint('块选择修饰键仅支持 Alt / Ctrl；Shift / Win 不可用');
          return;
        }
        applyBinding(capturingId, held[0]);
        return;
      }

      const candidate = keyboardEventToShortcut(event);
      if (!candidate) {
        setCaptureHint('无法识别这个键，请换一个（字母 / 数字 / 符号 / 方向键 / F1~F12）');
        return;
      }
      const normalized = normalizeShortcut(candidate);
      if (!normalized || isModifierOnlyShortcut(normalized)) {
        // 纯修饰键（含"只按住修饰键"的组合）一律拒绝
        setCaptureHint('不能只用修饰键，请再按一个字母 / 数字 / 符号键（例如 Ctrl+K）');
        return;
      }
      const chord = parseShortcut(normalized);
      if (chord && !chord.ctrl && !chord.alt && !chord.shift && !chord.meta) {
        // 增强保护：无修饰键的裸键会在编辑面板内抢走正常输入（把 `A` 配成快捷键后打不出字母）
        setCaptureHint('快捷键至少需要一个修饰键（Ctrl / Alt / Shift / Meta），否则会抢走编辑器里的正常输入');
        return;
      }
      applyBinding(capturingId, normalized);
    };

    window.addEventListener('keydown', onKeyDown, true);
    document.body.dataset.jfShortcutCapture = '1';
    return () => {
      window.removeEventListener('keydown', onKeyDown, true);
      delete document.body.dataset.jfShortcutCapture;
    };
  }, [open, capturingId, applyBinding]);

  const beginCapture = useCallback((id: ShortcutId) => {
    setConfirmResetAll(false);
    setCaptureHint(null);
    setCapturingId((prev) => (prev === id ? null : id));
  }, []);

  const handleResetOne = useCallback((id: ShortcutId) => {
    // 单项重置 = delete overrides[id]（§4.2.4），只改内存，等用户点「保存」
    useShortcutStore.getState().resetBinding(id);
    setCaptureHint(null);
    setCapturingId(null);
  }, []);

  const handleSave = useCallback(async () => {
    const ok = await useShortcutStore.getState().save();
    if (ok) {
      rememberSaved();
      toast.success('快捷键已保存', '下次启动仍然生效');
      return;
    }
    const reason = useShortcutStore.getState().lastError;
    toast.error('未能保存快捷键', reason ?? '请检查是否存在重复键位');
  }, [rememberSaved]);

  const handleResetAll = useCallback(async () => {
    const ok = await useShortcutStore.getState().resetAll();
    setConfirmResetAll(false);
    setCapturingId(null);
    setCaptureHint(null);
    if (ok) {
      rememberSaved();
      toast.success('已恢复全部默认键位');
      return;
    }
    const reason = useShortcutStore.getState().lastError;
    toast.error('恢复默认失败', reason ?? '请稍后重试');
  }, [rememberSaved]);

  const handleClose = useCallback(() => {
    setCapturingId(null);
    setCaptureHint(null);
    setConfirmResetAll(false);
    onClose();
  }, [onClose]);

  return (
    <Modal
      open={open}
      title="快捷键配置"
      icon="edit"
      onClose={handleClose}
      width={880}
      footer={
        <>
          <span className="mr-auto text-[11.5px] text-subtle">
            {loading
              ? '正在读取快捷键偏好…'
              : dirty
                ? '有未保存的修改（改键只改内存，点「保存」才会写入）'
                : '与已保存的键位一致'}
          </span>
          {confirmResetAll ? (
            <>
              <span className="text-[11.5px] text-danger-700">
                恢复全部默认？将立即写入偏好文件
              </span>
              <Button size="sm" variant="ghost" onClick={() => setConfirmResetAll(false)}>
                取消
              </Button>
              <Button
                size="sm"
                variant="danger"
                loading={saving}
                onClick={() => void handleResetAll()}
              >
                确认恢复
              </Button>
            </>
          ) : (
            <>
              <Button
                size="sm"
                variant="ghost"
                icon="refresh"
                disabled={saving || customizedCount === 0}
                onClick={() => {
                  setCapturingId(null);
                  setCaptureHint(null);
                  setConfirmResetAll(true);
                }}
              >
                恢复全部默认
              </Button>
              <Button
                size="sm"
                variant="primary"
                icon="save"
                loading={saving}
                onClick={() => void handleSave()}
              >
                保存
              </Button>
              <Button size="sm" variant="secondary" onClick={handleClose}>
                关闭
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {/* 口径说明：用户硬性要求「一定要有配置的地方和功能说明」 */}
        <div className="rounded-lg border border-line-subtle bg-sunken px-3 py-2 text-[11.5px] leading-relaxed text-muted">
          <div>
            点某一行的「修改」后，直接按下新的组合键即可（<span className="tabular">Esc</span> 取消）。
            <span className="text-danger-700">不能只按修饰键</span>
            ；若新键与其他功能重复，会在下方标红并拒绝保存（不会静默覆盖）。
          </div>
          <div className="mt-0.5">
            快捷键只在 JFLove 窗口处于激活状态时生效，
            <span className="font-semibold">不注册任何系统级全局热键</span>；当前共{' '}
            {SHORTCUT_DEFS.length} 项，已自定义 {customizedCount} 项。
          </div>
        </div>

        {/* 冲突横幅（AC-9：明确提示 + 不落盘） */}
        {conflicts.length > 0 && (
          <div className="rounded-lg border border-danger-500/25 bg-danger-50 px-3 py-2 text-[11.5px] leading-relaxed text-danger-700">
            <div className="font-semibold">存在重复键位：保存会被拒绝，不会覆盖任何一项</div>
            <ul className="mt-1 list-disc pl-4">
              {conflicts.map((conflict) => (
                <li key={conflict.key}>
                  <span className="tabular">{formatShortcutForDisplay(conflict.key)}</span>
                  {' 同时被 '}
                  {conflict.ids.map((id) => getShortcutDef(id)?.label ?? id).join('、')}
                  {' 占用'}
                  {conflict.reservedByEditor && '（该键同时被编辑器内核使用，应用级优先）'}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* 跨作用域同键的**提示**（v1.6.2 · AC-97④：允许共存，但必须让用户知道作用域不同） */}
        {crossScopeNotes.length > 0 && (
          <div className="rounded-lg border border-line-subtle bg-sunken px-3 py-2 text-[11.5px] leading-relaxed text-muted">
            <div className="font-semibold">
              有 {crossScopeNotes.length} 组键位跨作用域重复（**不会同时触发**，可以保存）
            </div>
            <ul className="mt-1 list-disc pl-4">
              {crossScopeNotes.map((note) => (
                <li key={note.key}>
                  <span className="tabular">{formatShortcutForDisplay(note.key)}</span>
                  {' 同时被 '}
                  {note.ids
                    .map((id) => {
                      const def = getShortcutDef(id);
                      const scopeLabel = def?.scope === 'window' ? '应用级' : '文本编辑器级';
                      return `${def?.label ?? id}（${scopeLabel}）`;
                    })
                    .join('、')}
                  {' 使用：两者作用域不同，不会同时触发'}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* 落盘 / 校验失败的明确提示（含桥未就绪、偏好文件不可写） */}
        {lastError && conflicts.length === 0 && (
          <div className="rounded-lg border border-danger-500/25 bg-danger-50 px-3 py-2 text-[11.5px] text-danger-700">
            {lastError}
          </div>
        )}

        {/* v1.6.2（F16 / AC-97）：**两组**渲染 —— 应用级 / 文本编辑器级
            （用户裁决："只要分应用级和文本编辑器级"；工具箱 11 项 + 视图/编辑项归后者）。
            行数据仍逐项来自 `SHORTCUT_DEFS`（唯一手写真相），不做任何过滤；
            `data-shortcut-group` 是用例的作用域观测点（AC-97①②）。 */}
        <div className="max-h-[52vh] overflow-y-auto rounded-lg border border-line-subtle">
          {SHORTCUT_SCOPE_GROUPS.map((group) => (
            <div key={group.scope} data-shortcut-group={group.scope}>
              <div className="border-b border-line-subtle bg-sunken px-3 py-2">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-[13px] font-semibold text-fg">{group.title}</span>
                  <span className="rounded-full border border-line-subtle px-1.5 text-[10.5px] text-subtle">
                    {group.subtitle}
                  </span>
                  <span className="text-[10.5px] text-subtle">
                    {shortcutsOfScope(group.scope).length} 项
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] leading-relaxed text-subtle">
                  {group.description}
                </div>
              </div>
              {shortcutsOfScope(group.scope).map((def) => {
            const current = bindings[def.id] ?? def.defaultKey;
            const changed = isUserCustomizedShortcut(def, overrides, bindings);
            const conflicting = conflictingIds.has(def.id);
            const capturing = capturingId === def.id;
            const currentClass = conflicting
              ? 'text-danger-700'
              : changed
                ? 'text-fg-brand'
                : 'text-fg';
            return (
              <div
                key={def.id}
                className={[
                  'flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line-subtle px-3 py-2.5 last:border-b-0',
                  conflicting ? 'bg-danger-50/60' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
              >
                <div className="min-w-[240px] flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-[13px] text-fg">{def.label}</span>
                    <span className="rounded-full border border-line-subtle px-1.5 text-[10.5px] text-subtle">
                      {def.scope === 'window' ? '任意页面生效' : '编辑面板聚焦'}
                    </span>
                    {changed && <span className="text-[10.5px] text-fg-brand">已自定义</span>}
                    {conflicting && <span className="text-[10.5px] text-danger-700">键位重复</span>}
                  </div>
                  {/* 中文功能说明（F2.4 / AC-6 要求逐项展示，不允许只给功能名） */}
                  <div className="mt-0.5 text-[11.5px] leading-relaxed text-subtle">
                    {def.description}
                  </div>
                  {def.meta.modifierOnly && (
                    <div className="mt-0.5 text-[11px] text-warning-700">
                      该项只能是单个修饰键（Alt / Ctrl），不是组合键
                    </div>
                  )}
                </div>

                <div className="w-[118px] shrink-0">
                  <div className="text-[10.5px] text-subtle">默认键</div>
                  <div className="tabular text-[12.5px] text-muted">
                    {formatBindingForDisplay(def, def.defaultKey)}
                  </div>
                </div>

                <div className="w-[136px] shrink-0">
                  <div className="text-[10.5px] text-subtle">当前键</div>
                  <div className={`tabular text-[12.5px] ${currentClass}`}>
                    {capturing ? '请按键…' : formatBindingForDisplay(def, current)}
                  </div>
                </div>

                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    size="sm"
                    variant={capturing ? 'primary' : 'secondary'}
                    icon="edit"
                    onClick={() => beginCapture(def.id)}
                  >
                    {capturing ? '按键中' : '修改'}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon="refresh"
                    disabled={!changed || saving}
                    onClick={() => handleResetOne(def.id)}
                  >
                    重置
                  </Button>
                </div>

                {capturing && (
                  <div className="w-full text-[11.5px] text-muted" role="status">
                    {captureHint ?? '请按下新的组合键；按 Esc 取消'}
                  </div>
                )}
              </div>
            );
              })}
            </div>
          ))}
        </div>

        <div className="text-[11px] leading-relaxed text-subtle">
          提示：编辑器内核已占用的组合（如 Ctrl+Enter、Ctrl+Space）不在默认键位表里；若强行配成同一个键，
          应用级优先，冲突提示会额外说明。
        </div>
      </div>
    </Modal>
  );
}

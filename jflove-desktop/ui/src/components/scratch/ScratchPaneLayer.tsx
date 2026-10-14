/**
 * 浮动窗格层根（设计 v1.6.0 §4.1.1 / §4.1.3 / §4.2.2 / §7.5 / AC-47）
 *
 * ## 定位（AC-47 的判据）
 *
 * 挂在 `App.tsx` 的 `TitleBar` **同级**、`RouterProvider` **之外**：因此「任意路由页都能呼出窗格」
 * （F1.1），并且换页不会销毁窗格。层自身 `pointer-events: none`，只有窗格与工具条恢复 `auto`
 * ⇒ 不遮挡页面的正常交互。
 *
 * ## 本文件承担的六件事
 *
 * 1. **渲染窗格**：按 `geometry.z` 升序渲染（z 越大越靠前），几何 / 层级全部来自 `scratch-store`；
 * 2. **可用区域上报**：`ResizeObserver` → `setViewport`（平铺 / 层叠 / 最大化的基准）；
 * 3. **工具条**：新建 / 打开 / 平铺 / 层叠（§4.1.1）；
 * 4. **启动期事件补投**：`onOpenPaths` + `flushOpenPaths`（§7.5 ②，化解「事件早于 UI」）；
 * 5. **关闭脏窗格的询问**：保存 / 放弃 / 取消，**文案不含完整路径**（③ / §7.4）；
 * 6. **应用级快捷键**：`window` **捕获阶段** `keydown` + `document.hasFocus() && !document.hidden`
 *    （§4.2.2「窗口激活才生效」）。**只处理窗口作用域的 7 项**，`preventDefault()+stopPropagation()`
 *    后执行；不命中立刻返回，事件照常流向 CM6（§4.2.3 的协作规则）。
 *    **不注册任何系统级热键** —— 只用 DOM 事件；AC-8 的 grep 判据要求某些系统级 API 标识符 0 命中，
 *    因此本文件（含注释）刻意不出现它们。
 *
 * ## 与其它施工面的边界
 *
 * - 窗格内部（CM6 宿主 `ScratchEditorHost` / 工具栏 / 视图）由另一位同事交付，
 *   本文件只 import 外壳 `ScratchPane`；宿主已由 `ScratchPane.tsx` 接线（props `{ pane }`），
 *   状态栏实时量走宿主导出的 `useScratchEditorStats`（见 `ScratchPaneStatusBar.tsx` 注释）；
 * - 退出握手（`shell.quitRequested` → 保存 / 放弃 / 取消 → `editor.confirmQuit`）属另一施工面，
 *   本文件只处理**单窗格关闭**的脏标记询问。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { keyboardEventToShortcut } from '../../config/shortcuts';
import type { ShortcutId } from '../../config/shortcuts';
import { scratchService } from '../../services/scratch-service';
import { useScratchStore } from '../../stores/scratch-store';
import { useShortcutStore } from '../../stores/shortcut-store';
import { DETECT_AND_READ_MAX_PATHS } from '../../types/scratch';
import type { ScratchPane } from '../../types/scratch';
import { flushOpenPaths, onOpenPaths } from '../../utils/scratch/open-paths-queue';
import { Modal, toast } from '../ui';
import { ScratchPane as ScratchPaneView } from './ScratchPane';
import '../../styles/scratch-editor.css';

/**
 * 应用级监听**接管**的快捷键（其余 13 项是「窗格聚焦类」，由 CM6 keymap 侧实现，§4.2.3）。
 *
 * 逐项对齐 §4.2.1 的「作用域」列：
 * `pane.new` / `pane.next` / `pane.prev` / `pane.tile` / `pane.cascade` / `file.open` = 窗口激活；
 * `pane.close` = 窗格聚焦（故上面额外做 `closest('[data-scratch-pane]')` 校验）。
 */
const APP_SCOPE_SHORTCUTS: readonly ShortcutId[] = [
  'pane.new',
  'pane.close',
  'pane.next',
  'pane.prev',
  'pane.tile',
  'pane.cascade',
  'file.open',
];

/** 事件目标是否位于某个窗格 DOM 内（§4.2.2「窗格聚焦类」的判定口径） */
function isInsidePane(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.closest('[data-scratch-pane]') !== null;
}

/** 按层级取窗格（z 升序；末位 = 最前） */
function panesByDepth(panes: readonly ScratchPane[]): ScratchPane[] {
  return [...panes].sort((a, b) => a.geometry.z - b.geometry.z);
}

/** 异常 → 提示文案（只用 `message`；Python / store 侧文案已保证不含完整路径，§7.4） */
function describeError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return '操作失败';
}

export function ScratchPaneLayer() {
  const panes = useScratchStore((state) => state.panes);
  const activePaneId = useScratchStore((state) => state.activePaneId);
  const lastError = useScratchStore((state) => state.lastError);
  const clearError = useScratchStore((state) => state.clearError);

  const rootRef = useRef<HTMLDivElement | null>(null);

  /** 待确认的关闭请求（窗格 id）；null = 无弹窗 */
  const [closeRequestId, setCloseRequestId] = useState<string | null>(null);
  /** 关闭弹窗中的「保存」异步进行中（禁用按钮防重复提交） */
  const [closingBusy, setClosingBusy] = useState(false);
  /** 已在弹窗流程中的窗格（防重入：全局快捷键与关闭按钮可能同帧各触发一次） */
  const pendingCloseRef = useRef<Set<string>>(new Set());

  const requestPane = closeRequestId
    ? (panes.find((pane) => pane.id === closeRequestId) ?? null)
    : null;

  // ── ① 可用区域上报（平铺 / 最大化基准）──
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const report = () => {
      useScratchStore.getState().setViewport(el.clientWidth, el.clientHeight);
    };
    report();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', report);
      return () => window.removeEventListener('resize', report);
    }
    const observer = new ResizeObserver(report);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // ── 错误 / 通知：统一 Toast（文案不含完整路径与内容，§7.4）──
  useEffect(() => {
    if (!lastError) return;
    toast.error('编辑器', lastError);
    clearError();
  }, [lastError, clearError]);

  useEffect(
    () =>
      scratchService.onEditorNotice((notice) => {
        const title = notice.level === 'error' ? '编辑器错误' : '编辑器提示';
        if (notice.level === 'error') toast.error(title, notice.message);
        else toast.info(title, notice.message);
      }),
    [],
  );

  /**
   * 打开一批路径 → `detectAndRead` → 每个成功条目建一个窗格（§7.1 #2 / §4.1.2 `applyReadResult`）。
   *
   * 分批（每批 ≤ `DETECT_AND_READ_MAX_PATHS` = 64，与桥的入参上限一致）；
   * 单条失败**不抛**（`items[].ok:false`），由 store 落成 `lastError` → 上面的 Toast 订阅统一提示。
   */
  const openPaths = useCallback(async (paths: readonly string[]) => {
    const unique = Array.from(
      new Set(paths.filter((item) => typeof item === 'string' && item.trim().length > 0)),
    );
    if (unique.length === 0) return;
    for (let offset = 0; offset < unique.length; offset += DETECT_AND_READ_MAX_PATHS) {
      const batch = unique.slice(offset, offset + DETECT_AND_READ_MAX_PATHS);
      try {
        const result = await scratchService.detectAndRead(batch);
        const store = useScratchStore.getState();
        result.items.forEach((item) => {
          store.applyReadResult(item);
        });
      } catch (error) {
        toast.error('打开文件失败', describeError(error));
      }
    }
  }, []);

  // ── ④ 启动期事件补投（§7.5 ②）──
  useEffect(() => {
    const dispose = onOpenPaths((paths) => {
      void openPaths(paths);
    });
    // `onOpenPaths` 注册时已尝试 flush；这里再调一次是为了「注册发生在队列仍有积压之后」的兜底（幂等）
    flushOpenPaths();
    return dispose;
  }, [openPaths]);

  /** 原生对话框选文件 → 打开（`file.open` 快捷键 / 工具条共用） */
  const pickAndOpen = useCallback(async () => {
    try {
      const paths = await scratchService.pickFiles();
      if (paths.length === 0) return; // 取消不是错误
      await openPaths(paths);
    } catch (error) {
      toast.error('打开文件失败', describeError(error));
    }
  }, [openPaths]);

  // ── ⑤ 关闭请求：干净直接关；脏窗格先询问 ──
  const requestClose = useCallback((paneId: string) => {
    const store = useScratchStore.getState();
    const pane = store.panes.find((item) => item.id === paneId);
    if (!pane) return;
    if (!pane.dirty) {
      store.closePane(paneId);
      return;
    }
    if (pendingCloseRef.current.has(paneId)) return; // 防重入：同一窗格只保留一个待确认请求
    pendingCloseRef.current.add(paneId);
    setCloseRequestId(paneId);
  }, []);

  /** 取消：只关弹窗，不动窗格 */
  const handleCancelClose = useCallback(() => {
    if (closeRequestId) pendingCloseRef.current.delete(closeRequestId);
    setCloseRequestId(null);
  }, [closeRequestId]);

  /**
   * 保存后关闭。
   *
   * `flushPendingSaves()` 会立刻落盘所有待写内容：**总是**写草稿库；`path != null` 时**同时**
   * 写回用户文件（AC-40：草稿形态永不写用户文件系统）。因此：
   *   - 有路径的窗格：这里就是真正的「保存」；
   *   - 草稿形态的窗格：正文进入草稿库（**内容不丢**，下次启动可还原），`path` 仍为 null ⇒
   *     落成用户文件需要「另存为」流程，而它依赖 CM6 宿主提供的正文读取器
   *     （`registerScratchContentAccessor`，由 `ScratchEditorHost` 注册）—— 见交付说明的待对齐项。
   */
  const handleSaveAndClose = useCallback(async () => {
    const paneId = closeRequestId;
    if (!paneId) return;
    setClosingBusy(true);
    try {
      await useScratchStore.getState().flushPendingSaves();
    } catch (error) {
      // 写盘失败：**不关闭**窗格（内容仍在内存 + 草稿），并明确提示（AC-41）
      toast.error('保存失败', describeError(error));
      setClosingBusy(false);
      return;
    }
    useScratchStore.getState().closePane(paneId);
    pendingCloseRef.current.delete(paneId);
    setCloseRequestId(null);
    setClosingBusy(false);
  }, [closeRequestId]);

  /** 放弃修改：先显式丢弃草稿（否则「脏」标记会让 closePane 保留草稿 ⇒ 下次启动又还原出来） */
  const handleDiscardAndClose = useCallback(() => {
    const paneId = closeRequestId;
    if (!paneId) return;
    void scratchService.draftDiscard(paneId).catch(() => {
      // 丢弃失败不阻塞关闭：脏窗格关闭时 closePane 本来就不会删草稿，最坏情况是下次仍可还原
    });
    useScratchStore.getState().closePane(paneId);
    pendingCloseRef.current.delete(paneId);
    setCloseRequestId(null);
  }, [closeRequestId]);

  // ── ⑥ 应用级快捷键（窗口捕获阶段；失焦 / 后台一律不响应）──
  const runShortcut = useCallback(
    (id: ShortcutId) => {
      const store = useScratchStore.getState();
      switch (id) {
        case 'pane.new':
          store.createPane();
          break;
        case 'pane.close': {
          const target = store.activePaneId ?? panesByDepth(store.panes).pop()?.id ?? null;
          if (target) requestClose(target);
          break;
        }
        case 'pane.next':
        case 'pane.prev': {
          const ordered = panesByDepth(store.panes).filter((pane) => !pane.geometry.min);
          if (ordered.length === 0) break;
          const currentIndex = ordered.findIndex((pane) => pane.id === store.activePaneId);
          const step = id === 'pane.next' ? 1 : -1;
          const nextIndex =
            currentIndex < 0
              ? id === 'pane.next'
                ? 0
                : ordered.length - 1
              : (currentIndex + step + ordered.length) % ordered.length;
          store.focusPane(ordered[nextIndex].id);
          break;
        }
        case 'pane.tile':
          store.tileAll();
          break;
        case 'pane.cascade':
          store.cascadeAll();
          break;
        case 'file.open':
          void pickAndOpen();
          break;
        default:
          break;
      }
    },
    [pickAndOpen, requestClose],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      // AC-8 / §4.2.2：窗口失焦或文档不可见时，keydown 不产生任何编辑器行为
      if (!document.hasFocus() || document.hidden) return;
      // 快捷键配置对话框正在录制键位 ⇒ 让路。否则用户按下某个**已生效**的窗格键
      // （如 Ctrl+Alt+N）时，窗格动作会额外触发一次（本监听在捕获阶段注册更早，且
      // stopPropagation 拦不住同一 target 上的其它监听器）。
      // 录制期间由 ShortcutsSettingsDialog 设置 document.body.dataset.jfShortcutCapture。
      if (document.body.dataset.jfShortcutCapture) return;
      const chord = keyboardEventToShortcut(event);
      if (!chord) return;
      const bindings = useShortcutStore.getState();
      const matched = APP_SCOPE_SHORTCUTS.find((id) => bindings.getBinding(id) === chord);
      if (!matched) return; // 不接管就立刻返回，事件继续流向 CM6（§4.2.3 规则 1）
      if (matched === 'pane.close' && !isInsidePane(event.target)) return; // 窗格聚焦类作用域
      // 接管：阻止宿主默认动作与 CM6 双触发（§4.2.3 规则 2）
      event.preventDefault();
      event.stopPropagation();
      runShortcut(matched);
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [runShortcut]);

  const ordered = panesByDepth(panes);

  return (
    <div className="scratch-layer" ref={rootRef} data-scratch-layer="true">
      {ordered.map((pane) => (
        <ScratchPaneView
          key={pane.id}
          pane={pane}
          active={pane.id === activePaneId}
          onRequestClose={requestClose}
        />
      ))}

      {/* 工具条：有窗格时才出现（无窗格时按 `pane.new` / `file.open` 快捷键呼出，F1.1） */}
      {ordered.length > 0 && (
        <div className="scratch-toolbar" role="toolbar" aria-label="窗格工具条">
          <button
            type="button"
            className="scratch-tool-btn"
            title="新建编辑窗格"
            onClick={() => useScratchStore.getState().createPane()}
          >
            新建
          </button>
          <button
            type="button"
            className="scratch-tool-btn"
            title="打开本地文件"
            onClick={() => void pickAndOpen()}
          >
            打开
          </button>
          <span className="scratch-tool-sep" />
          <button
            type="button"
            className="scratch-tool-btn"
            title="平铺全部窗格"
            onClick={() => useScratchStore.getState().tileAll()}
          >
            平铺
          </button>
          <button
            type="button"
            className="scratch-tool-btn"
            title="层叠全部窗格"
            onClick={() => useScratchStore.getState().cascadeAll()}
          >
            层叠
          </button>
        </div>
      )}

      {/* 关闭脏窗格的询问：保存 / 放弃 / 取消（文案只有文件名，**不含目录**，§7.4） */}
      <Modal
        open={requestPane !== null}
        title="关闭窗格"
        icon="warning"
        onClose={handleCancelClose}
        width={460}
        footer={
          <>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={handleCancelClose}
              disabled={closingBusy}
            >
              取消
            </button>
            <button
              type="button"
              className="btn btn-danger"
              onClick={handleDiscardAndClose}
              disabled={closingBusy}
            >
              放弃修改
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void handleSaveAndClose()}
              disabled={closingBusy}
            >
              保存
            </button>
          </>
        }
      >
        <p className="m-0 text-[13.5px] leading-relaxed break-words text-muted">
          「{requestPane?.title ?? '未命名'}」有未保存的修改。
        </p>
        {requestPane?.path === null && (
          <p className="mt-2 mb-0 text-xs leading-relaxed text-muted">
            该窗格尚未指定保存位置：选择「保存」会先把内容写入草稿库（内容不会丢失），
            需要落成文件请使用「另存为」。
          </p>
        )}
      </Modal>
    </div>
  );
}

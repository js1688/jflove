/**
 * 文本编辑器**工作区**（路由 `/editor`）—— 设计 v1.6.1 §4.2 / §4.5 / §4.8
 *
 * ## 它替换掉了什么
 *
 * v1.6.0 的 `<ScratchPaneLayer />` 挂在 `App.tsx` 里、`RouterProvider` **之外**（浮动层，
 * 任意路由页可呼出，AC-47）。v1.6.1 把它整个换成**一个正常的整页页面**：顶部固定标签条 +
 * 下面一块内容区（同一时刻只显示激活标签的面板，其余用 `hidden` 隐藏、**不卸载**）。
 *
 * ## 本文件承担的四件事
 *
 * | # | 职责 | 依据 |
 * |---|------|------|
 * | ① | 准入兜底：未登录不渲染工作区（路由守卫已挡一层，这里是"停留期间会话失效"的第二层） | AC-63 / 设计 §4.1 |
 * | ② | 草稿还原：**仅在「已登录 且（内存 tabs 为空 **或** 存在正文已失效的标签）且 本会话尚未还原过」**时 `draftList()` → hydrate；随后 `flushOpenPaths()` 让**被唤起的文件最后夺激活** | §4.5 / §15.5.5 / AC-64 ③ / **AC-77⑥** |
 * | ③ | 脏标签关闭询问（保存 / 放弃 / 取消）：**请求本身来自 store**（`pendingCloseId`），因为 `Ctrl+W` 的接管方是模块级常驻的快捷键运行时，拿不到本组件的局部 state | §4.7 / AC-59 / AC-70 |
 * | ④ | 错误与通知统一 Toast（文案来自 Python / store，**不含完整路径**） | §7.4 |
 *
 * ## 与其它施工面的边界
 *
 * - **应用级快捷键**（含任意路由页 `Ctrl+Alt+N` 切到工作区）**不在本文件** —— 见
 *   `utils/scratch/shortcut-runtime.ts`（模块级常驻，AC-1 的硬要求）；
 * - 标签条本体（拖拽 / 溢出 / 脏点）在 `ScratchTabBar`；面板本体在 `EditorPanel`；
 * - 退出握手（`shell.quitRequested` → 保存/放弃/取消 → `editor.confirmQuit`）与
 *   `QuitConfirmDialog` 仍在 `App.tsx`（DS-17）：那是**应用级**流程，不属于编辑器页面；
 * - 外部唤起（`editor.openPaths`）的登录门接线在 `utils/scratch/editor-open-paths.ts`，
 *   本文件只负责"工作区就绪后补投一次"。
 */

import { useCallback, useEffect, useState } from 'react';
import { scratchService } from '../../services/scratch-service';
import { useScratchStore } from '../../stores/scratch-store';
import { useEditorGate } from '../../utils/scratch/editor-gate';
import { pickInvalidTabs } from '../../utils/scratch/editor-liveness';
import {
  MAX_PENDING_OPEN_PATHS,
  droppedOpenPaths,
  flushOpenPaths,
} from '../../utils/scratch/open-paths-queue';
// 缺陷 L-7：共享「异常 → 文案」叶子模块（本组件不再自带一份实现）
import { ERROR_FALLBACK, describeErrorWithFallback } from '../../utils/error-text';
import { Modal, toast } from '../ui';
import { EditorPanel } from './EditorPanel';
import { ScratchTabBar } from './ScratchTabBar';
import { ScratchWorkspaceEmpty } from './ScratchWorkspaceEmpty';
import '../../styles/scratch-editor.css';

/** 异常 → 提示文案（只用 `message`；Python / store 侧文案已保证不含完整路径，§7.4） */
function describeDraftRestoreError(error: unknown): string {
  return describeErrorWithFallback(error, ERROR_FALLBACK.OPERATION);
}

/**
 * 「本会话是否已经还原过草稿」。
 *
 * **刻意放在模块作用域而不是 `useRef`**：设计 §4.5 的表述是"本会话尚未还原过（`useRef(false)`）"，
 * 但工作区会随路由**卸载再挂载**（去设置页再回来）——`useRef` 会随组件重建而复位，
 * 于是"关掉全部标签 → 去设置页 → 回到编辑器"就会把刚放弃的脏草稿又还原回来。
 * 模块级标记的语义才是"本会话"，与 §4.5 的**意图**一致（措辞偏差已登记在设计差异里）。
 */
let hydratedThisSession = false;

/**
 * 「被丢弃的打开请求」是否已经提示过（**本会话一次**）。
 *
 * 与 `hydratedThisSession` 同理放在模块作用域：工作区会随路由卸载再挂载，用 `useRef`
 * 会在重挂载时复位 ⇒ 每次回到编辑器都重复弹同一条提示。
 */
let droppedOpenPathsNoticeShown = false;

export function ScratchWorkspace() {
  const tabs = useScratchStore((state) => state.tabs);
  const activeTabId = useScratchStore((state) => state.activeTabId);
  const lastError = useScratchStore((state) => state.lastError);
  const clearError = useScratchStore((state) => state.clearError);
  const pendingCloseId = useScratchStore((state) => state.pendingCloseId);
  const clearCloseRequest = useScratchStore((state) => state.clearCloseRequest);
  const gate = useEditorGate();

  /** 弹窗中的「保存」异步进行中（禁用按钮防重复提交） */
  const [closingBusy, setClosingBusy] = useState(false);

  const requestTab = pendingCloseId
    ? (tabs.find((tab) => tab.id === pendingCloseId) ?? null)
    : null;

  // ── ② 草稿还原 + 唤起补投（§4.5：条件三条同时满足才 hydrate）──
  useEffect(() => {
    if (!gate) return;
    if (hydratedThisSession) {
      // 会话内已还原过：登录态翻转回来时只补投挂起的唤起请求（内存标签绝不被磁盘覆盖）
      flushOpenPaths();
      return;
    }
    void (async () => {
      const store = useScratchStore.getState();
      /**
       * 条件②（**本轮改写**，AC-77⑥ / V-6 / 设计 §15.5.5）：
       *
       * 旧判据 `skipRead = tabs.length > 0` 把"内存里有标签"误当成"内存里有正文"，
       * 而 F5 的形态恰恰是「**标签在、正文没了**」（路由卸载 ⇒ `view.destroy()`）⇒
       * 该判据恒为"跳过"，把修复路焊死（这也是"重挂后连草稿兜底都没有"的第二个原因）。
       *
       * 新判据三态（**必须含"内存为空"这一条**，否则冷启动还原 AC-55 / AC-60 会失效）：
       *   ① 内存为空 ⇒ **必须读盘**（冷启动 / 清空标签后的还原路径，DS-21 的原始语义）；
       *   ② 内存非空且**存在正文已失效的标签** ⇒ 读盘兜底（该标签的实例已走、快照也没接上）；
       *   ③ 内存非空且所有标签正文都有效 ⇒ **跳过**（绝不用磁盘旧内容覆盖活会话，AC-5⑤ / AC-77⑥）。
       */
      const shouldRead = store.tabs.length === 0 || pickInvalidTabs(store.tabs).length > 0;
      try {
        if (shouldRead) {
          const { cleanExit, entries } = await scratchService.draftList();
          // ★ 只对"当前不在内存里"的 id 计数与提示：内存里那些标签的最新正文在**快照**里，
          //   草稿库里的同 id 条目**不得**参与覆盖 —— `hydrateFromDrafts` 内部按 id 去重（既有）。
          const inMemory = new Set(store.tabs.map((tab) => tab.id));
          const fresh = entries.filter((entry) => !inMemory.has(entry.id));
          if (fresh.length > 0) {
            // 传**全集**：内部自动只新增内存里没有的 id（顺序 / 幂等语义不变）
            useScratchStore.getState().hydrateFromDrafts(entries);
            // 文案沿用 v1.6.0：正常退出仍有条目 = 用户选了"保留下次恢复"；否则是异常退出后自动还原
            toast.info(
              `已还原 ${fresh.length} 个标签页`,
              cleanExit ? '上次退出时选择了保留' : '上次未正常退出，草稿已自动还原',
            );
          }
        }
        // 只有"读盘完成（含确认无需读盘）且未抛错"才算本会话还原过（L-3）
        hydratedThisSession = true;
      } catch (error) {
        // 读盘失败**不置**「本会话已还原」标记 ⇒ 下次挂载还能再试一次；
        // 旧实现在 await 之前就置真，导致一次失败后本会话永远不再重试（L-3）。
        useScratchStore.getState().setError(describeDraftRestoreError(error));
      }
      // 次序硬性要求：先 hydrate 再 flush —— 被唤起的文件要**最后**夺激活（AC-64 ③）
      flushOpenPaths();
    })();
  }, [gate]);

  // ── ②′ 打开请求被丢弃（超出队列上限）时必须让用户看见（L-6）──
  // 没有消费点时，用户"双击了文件却毫无反应"；这里按**本会话一次**提示，且**只报数字**：
  // 文案不含路径 / 文件名（AGENTS.md §9.12 / §9.14、设计 §7.4），上限值直接取队列常量防空转不同步。
  useEffect(() => {
    if (!gate) return;
    if (droppedOpenPathsNoticeShown) return;
    const dropped = droppedOpenPaths();
    if (dropped <= 0) return;
    droppedOpenPathsNoticeShown = true;
    toast.info(
      '编辑器',
      `有 ${dropped} 个文件未打开（一次最多打开 ${MAX_PENDING_OPEN_PATHS} 个）`,
    );
  }, [gate]);

  // ── ④ 错误 / 通知：统一 Toast（§7.4）──
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

  /** 取消：只关弹窗，不动标签 */
  const handleCancelClose = useCallback(() => {
    clearCloseRequest();
  }, [clearCloseRequest]);

  /**
   * 保存后关闭（AC-59 的「保存」分支）。
   *
   * 语义 = `savePane`（AC-36 / AC-37，BL-44 的裁定）：
   * - 已有路径 ⇒ **不弹任何对话框**，直接写回同一路径；
   * - 从未落过盘（`path === null`）⇒ 弹**原生**保存对话框指定位置；
   * - 用户在对话框里取消、或写盘失败 ⇒ **标签保持打开**（内容不丢），只把弹窗关掉。
   */
  const handleSaveAndClose = useCallback(async () => {
    const tabId = pendingCloseId;
    if (!tabId) return;
    setClosingBusy(true);
    try {
      const saved = await useScratchStore.getState().savePane(tabId);
      if (!saved) {
        clearCloseRequest(); // 取消 / 只读 / 失败：标签与内容原样保留（失败文案已由 lastError 提示）
        return;
      }
      useScratchStore.getState().closeTab(tabId); // 关闭时顺带收起询问框（store 内部同步）
    } finally {
      setClosingBusy(false);
    }
  }, [pendingCloseId, clearCloseRequest]);

  /** 放弃修改（AC-59）：先**显式**丢弃草稿，再关闭 —— 否则"脏"标记会让草稿留存、下次又被还原 */
  const handleDiscardAndClose = useCallback(() => {
    const tabId = pendingCloseId;
    if (!tabId) return;
    void scratchService.draftDiscard(tabId).catch(() => {
      // 丢弃失败不阻塞关闭：最坏情况是下次启动仍能还原出该标签（内容不丢的兜底方向）
    });
    useScratchStore.getState().closeTab(tabId);
  }, [pendingCloseId]);

  // ① 准入兜底：未登录不渲染工作区（路由守卫已挡一层，这里防"停留期间会话失效"）
  if (!gate) return null;

  /** 需要渲染面板外壳的标签：已挂载的 + **当前激活项**。
   *
   * ⚠ 必须包含激活项，否则**草稿还原**路径会自锁（Phase 5 审查 S-1）：
   * `hydrateFromDrafts` 刻意把还原出的标签置 `mounted: false`（§4.8 懒挂载），而全仓唯一的
   * `setMounted(id, true)` 调用点在 `EditorPanel` 里（要求该面板**先被渲染**）⇒ 若这里只按
   * `tab.mounted` 过滤，激活项没有面板、面板永远不会被挂载，工作区停在"标签条有内容、正文空白"。
   * 新建 / 打开路径不受影响（`createTab` 默认 `mounted: true`），但还原路径是用户可见的主路径（AC-55 / AC-60 / AC-72）。
   */
  const mountedTabs = tabs.filter((tab) => tab.mounted || tab.id === activeTabId);

  return (
    <div className="scratch-workspace" data-scratch-workspace="true">
      {tabs.length > 0 && <ScratchTabBar onRequestClose={requestCloseTabFromStore} />}

      <div className="scratch-workspace-body">
        {tabs.length === 0 ? (
          <ScratchWorkspaceEmpty />
        ) : (
          mountedTabs.map((tab) => (
            <EditorPanel key={tab.id} tabId={tab.id} active={tab.id === activeTabId} />
          ))
        )}
      </div>

      {/* 关闭脏标签的询问：保存 / 放弃 / 取消（文案只有标题，**不含目录**，§7.4） */}
      <Modal
        open={requestTab !== null}
        title="关闭标签页"
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
          「{requestTab?.title ?? '未命名'}」有未保存的修改。
        </p>
        {requestTab?.path === null && (
          <p className="mt-2 mb-0 text-xs leading-relaxed text-muted">
            该标签页尚未指定保存位置：选择「保存」会先询问保存到哪里。
          </p>
        )}
      </Modal>
    </div>
  );
}

/** 关闭请求的稳定引用（store action 是稳定的，这里抽出来避免内联箭头每次渲染都换引用） */
function requestCloseTabFromStore(tabId: string): void {
  useScratchStore.getState().requestCloseTab(tabId);
}

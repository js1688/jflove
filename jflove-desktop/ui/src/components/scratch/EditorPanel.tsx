/**
 * 单个**编辑面板**（标签页的面板本体）—— 设计 v1.6.1 §4.2 / §4.8
 *
 * ## 它替换掉了什么
 *
 * v1.6.0 的 `ScratchPane.tsx` 是「浮动窗格外壳」：拖动 / 8 向缩放 / 最小化 / 最大化 / 关闭按钮 /
 * 位置尺寸 → CSS 换算 / `zIndex` 层级 / `PANE_MIN_WIDTH|HEIGHT` 下限 / `onPointerDownCapture` 层级聚焦。
 * v1.6.1 这七类能力**全部退役**（设计 §14.1：位置尺寸与排列 action 已从 store 删除），
 * 于是本组件只剩下「面板本体」这一件事：
 *
 * - **懒挂载**（§4.8）：两个条件**同时**满足才允许宿主建 CM6 实例
 *   ① 本标签是激活项（非激活面板带 `hidden` ⇒ 矩形恒为 0，`IntersectionObserver` 永不命中）；
 *      从隐藏恢复可见时派发一次合成 `resize`，让 CM6 重新测量（沿用 v1.6.0 `ScratchPane.tsx:89-92` 的
 *      `nudgeEditorLayout` 做法 —— `EditorView` 的测量是 `resize` 事件驱动的，不叫醒就会出现
 *      「空白 / 行号错位」）；
 *   ② 面板进入视口（或环境没有 `IntersectionObserver`）。命中后**只进不退**：已挂载**永不复位**
 *      （复位会 `destroy()` 掉 CM6 实例 ⇒ 未落盘的编辑丢失；草稿还原路径 `takeInitialText`
 *      只交付一次，取走即删，无法重新取回正文）。
 * - **视图分派**（§4.2 组件树 + §6.1.3 四类视图）：`edit` / `split` / `preview` / `table` / `tree`。
 * - **DOM 契约**（§4.2 DOM 契约表）：根元素带 `data-editor-panel="<tabId>"`，且**仅在激活时**
 *   带 `data-editor-panel-active="true"` —— 这是 AC-66 的观测点，也是应用级快捷键的作用域锚点
 *   （`utils/scratch/shortcut-runtime.ts` 的 `panelTabIdOf` 用 `closest('[data-editor-panel]')` 判定，
 *   即 v1.6.0 窗格锚点的接替者）。
 *
 * ## 非激活面板：`hidden` 隐藏但**不卸载**（AC-66）
 *
 * 保住 CM6 实例、撤销历史、光标与滚动位置。因此：
 * - 隐藏靠根元素的原生 `hidden` 属性（根元素的 display 类**只在激活时**加 `flex`，
 *   未激活时留给 UA 的 `[hidden] { display: none }`；不要写死 `display: flex` 盖掉它）；
 * - 根元素布局 = `relative flex h-full min-h-0 w-full flex-col`（`relative` 供工具栏内部的
 *   `absolute` 工具箱面板 / diff 覆盖层定位）；
 * - `.scratch-pane-body` / `.scratch-pane-host` / `.scratch-pane-aux` 等**布局类沿用 v1.6.0 的样式表**
 *   （`styles/scratch-editor.css` 只读参考，本任务不改它）。
 *
 * ## 安全（AGENTS.md §9.6 / §9.14）
 *
 * 只处理标签元数据与视图切换；**不读文件、不拼路径、不打印内容 / 路径**，DOM 属性里只有 `tabId`。
 */

import { useEffect, useRef, useState } from 'react';
import { useScratchStore } from '../../stores/scratch-store';
import { getScratchEditorSession, ScratchEditorHost } from './ScratchEditorHost';
import { ScratchEditorStatusBar } from './ScratchEditorStatusBar';
import { ScratchToolbar } from './ScratchToolbar';
import { CsvTableView } from './views/CsvTableView';
import { JsonTreeView } from './views/JsonTreeView';
import { MarkdownSplitView } from './views/MarkdownSplitView';

/**
 * 让 CM6 重新测量。
 *
 * 面板从 `hidden` 恢复可见（切换标签、或视图模式在「真实尺寸 ↔ hidden」之间翻转）时，
 * 内容区尺寸会从 0 变回真实尺寸，而 `EditorView` 的测量是 `resize` 事件驱动的 ⇒ 派发一次
 * **合成 resize**，避免还原后出现「空白 / 行号错位」。做法逐字沿用 v1.6.0
 * `ScratchPane.tsx:89-92`（几何退役后这个提醒仍然需要，只是触发点换成了「激活」）。
 */
function nudgeEditorLayout(): void {
  if (typeof window === 'undefined') return;
  window.requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
}

export interface EditorPanelProps {
  /** 本面板对应的标签 id（DOM 契约锚点 `data-editor-panel` 的值） */
  tabId: string;
  /**
   * 是否为当前激活标签。
   *
   * 由工作区**直接传入**（`ScratchWorkspace.tsx` 用 `tab.id === activeTabId` 算），
   * 本组件**不再从 store 查一次**激活态 —— 避免同一事实有两个来源。
   */
  active: boolean;
}

/**
 * 单个编辑面板。
 *
 * 标签不存在（已被关闭 / 尚未登记）时返回 `null`：工作区会在下一帧从 `tabs` 里把它摘掉，
 * 这里先兜住「渲染期间标签被删」的竞态，不让上层拿到 `undefined.id` 崩掉。
 */
export function EditorPanel({ tabId, active }: EditorPanelProps): JSX.Element | null {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const setMounted = useScratchStore((state) => state.setMounted);
  const tab = useScratchStore((state) => state.tabs.find((item) => item.id === tabId));

  /**
   * 本面板是否与视口相交（懒挂载条件②；命中后不回退）。
   *
   * 非激活面板带 `hidden` ⇒ 矩形为 0 ⇒ 观察器不会命中。重新激活时**重置一次**：
   * 隐藏期间卸载观察是安全的（宿主本来就没建实例），重置保证「切回来真的重新观察一次」。
   */
  const [visible, setVisible] = useState(false);

  // 视图模式（标签不存在时为 undefined；仅用于下面的重测量 effect 与渲染分支）
  const viewMode = tab?.viewMode;

  // ── 条件②：面板矩形与视口相交 ──
  useEffect(() => {
    if (!active) {
      setVisible(false); // 隐藏期间矩形为 0，观察没有意义；激活时重新观察
      return;
    }
    if (visible) return;
    const element = rootRef.current;
    if (!element) return;
    // 没有 IntersectionObserver（老 WebEngine / 测试环境）：视为可见，保证功能可用（宁可早建也不留空壳）
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        setVisible(true);
        observer.disconnect();
      },
      { threshold: 0.01 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [active, visible]);

  // ── 条件①：被激活（store 侧 `setActiveTab` 已把该标签置 `mounted: true`） ──
  useEffect(() => {
    if (!active || !visible || !tab || tab.mounted) return;
    setMounted(tabId, true);
  }, [active, setMounted, tab, tabId, visible]);

  // ── 激活 + 视图切换 = 内容区尺寸翻转：派发一次合成 resize 让 CM6 重新测量 ──
  useEffect(() => {
    if (!active) return;
    nudgeEditorLayout();
  }, [active, viewMode]);

  if (!tab) return null;

  // store 不缓存正文，正文只活在 CM6 会话里 ⇒ 预览 / 表格 / 树的文本必须**现取**
  // （每次编辑都会让宿主广播统计，父组件重渲染时这里自然重新取值，视图随输入实时刷新）
  const viewText = tab.mounted ? (getScratchEditorSession(tabId)?.getText() ?? '') : '';
  const showHost = viewMode === 'edit' || viewMode === 'split';
  const showMarkdown = viewMode === 'preview' || viewMode === 'split';

  return (
    <div
      ref={rootRef}
      // DOM 契约：作用域锚点（快捷键判定用 `[data-editor-panel]`）
      data-editor-panel={tabId}
      // DOM 契约：**仅激活时**出现（AC-66 的观测点）
      {...(active ? { 'data-editor-panel-active': 'true' } : {})}
      hidden={!active}
      // `display` 用**内联样式**压住：`hidden` 属性只提供 UA 层规则，
      // 与工具类里的 `flex` 谁赢取决于样式表顺序 —— 内联样式不参与这场竞猜。
      style={active ? undefined : { display: 'none' }}
      className="editor-panel relative h-full min-h-0 w-full flex-col overflow-hidden flex"
    >
      {/* 工具栏（含保存 / 另存为 / 视图切换 / 字号 / 行号 / 自动换行 / 工具箱 12 项）。
          外层刻意用一个**普通块级 div** 承载：`ScratchToolbar` 内部有 `absolute` 的
          工具箱面板与 diff 覆盖层，定位祖先必须是本面板根元素（`relative`）。 */}
      <div className="shrink-0">
        <ScratchToolbar tabId={tabId} />
      </div>

      {tab.mounted ? (
        <div className="scratch-pane-body" data-pane-body={viewMode}>
          {/* 宿主槽位：`edit` / `split` 可见，其余模式 `hidden` 但**保持挂载**（正文只活在 CM6 里） */}
          <div className="scratch-pane-host" hidden={!showHost}>
            <ScratchEditorHost pane={tab} visible={active} className="scratch-host" />
          </div>

          {/* Markdown 预览：`split` = 与宿主并排；`preview` = 独占（宿主隐藏但保持挂载） */}
          {showMarkdown ? (
            <div className="scratch-pane-aux">
              <MarkdownSplitView pane={tab} text={viewText} showEditor={false} />
            </div>
          ) : null}

          {/* CSV / TSV 表格视图（只读，AC-17 / AC-31） */}
          {viewMode === 'table' ? (
            <div className="scratch-pane-aux">
              <CsvTableView text={viewText} />
            </div>
          ) : null}

          {/* JSON 树视图（只读，AC-29） */}
          {viewMode === 'tree' ? (
            <div className="scratch-pane-aux">
              <JsonTreeView text={viewText} />
            </div>
          ) : null}
        </div>
      ) : (
        <EditorPanelSkeleton />
      )}

      {/* 状态栏（§4.1.4 的 F10 全字段；`tabId` 由它自己查 store 与宿主广播） */}
      <ScratchEditorStatusBar tabId={tabId} />
    </div>
  );
}

/** 未挂载面板的内容区骨架（标签条与工具栏保持可用，§4.8） */
function EditorPanelSkeleton() {
  return (
    <div className="scratch-skeleton" aria-hidden="true">
      <span className="scratch-skeleton-line" />
      <span className="scratch-skeleton-line" />
      <span className="scratch-skeleton-line scratch-skeleton-line-short" />
    </div>
  );
}

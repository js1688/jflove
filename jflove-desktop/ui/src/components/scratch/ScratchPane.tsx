/**
 * 单个浮动编辑窗格的外壳（设计 v1.6.0 §4.1.1 / §4.1.2 / §4.1.3）
 *
 * ## 职责
 *
 * - **拖动**（标题栏指针事件）/ **缩放**（8 向 handle）/ **最小化** / **最大化-还原** / **关闭请求**；
 *   所有几何变化**立刻写回 `scratch-store`**（`movePane` / `resizePane` / `toggleMinimize` /
 *   `toggleMaximize`），组件自身**不缓存几何**（单一真相在 store，§4.1.2）。
 * - **懒挂载**（§4.1.3）：`IntersectionObserver` 命中视口 **或** 窗格被激活（`focusPane` 已置位）
 *   时才把 `mounted` 置 true；未挂载只渲染「标题栏 + 骨架」，内存占用 = 一个 React 空壳。
 *   已挂载**永不复位**（复位会销毁 CM6 实例 ⇒ 未落盘的编辑丢失；草稿还原路径
 *   `takeInitialText` 只交付一次，无法重新取回正文）。
 * - 关闭**不在这里执行**：`onRequestClose` 交给 `ScratchPaneLayer` 统一判断脏标记后弹询问（③）。
 *
 * ## 与同事组件的接线（已接）
 *
 * `ScratchEditorHost`（props = `{ pane: ScratchPane; className?: string }`，从 `./ScratchEditorHost` 导出）
 * 是 CM6 实例宿主，由另一位同事交付。本文件**只 import 使用**（不创建、不修改对方文件）：
 *
 * - 正文区渲染 `<ScratchEditorHost pane={pane} className="scratch-host" />`（`className` 只为让宿主
 *   flex 占满正文区，不改对方任何逻辑）；
 * - 状态栏的实时量走对方导出的 `useScratchEditorStats(pane.id)`（其注释明确「状态栏直接用这个」），
 *   再作为 `stats` 传给 `ScratchPaneStatusBar` ⇒ 状态栏保持**纯展示**、无模块级注册表。
 *
 * ## 安全（AGENTS.md §9.6 / §9.14）
 *
 * 只处理几何数字与 `pane` 元数据；不读文件、不打印路径与内容；DOM 属性里不出现路径。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react';
import { PANE_MIN_HEIGHT, PANE_MIN_WIDTH, useScratchStore } from '../../stores/scratch-store';
import type { PaneGeometry, ScratchPane } from '../../types/scratch';
import { ScratchEditorHost, useScratchEditorStats } from './ScratchEditorHost';
import { ScratchPaneStatusBar } from './ScratchPaneStatusBar';
import { ScratchPaneTitleBar } from './ScratchPaneTitleBar';
import type { ScratchPaneDragHandlers } from './ScratchPaneTitleBar';

/** 8 向缩放 handle（与 CSS 类名后缀一一对应） */
type ResizeEdge = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';

const RESIZE_EDGES: readonly ResizeEdge[] = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];

/** 拖动中的指针状态（增量语义：每次 move 只把本次位移交给 store） */
interface DragState {
  pointerId: number;
  lastX: number;
  lastY: number;
}

/** 缩放中的指针状态（**整段快照**语义：以按下时的几何为基准算目标值） */
interface ResizeState {
  pointerId: number;
  edge: ResizeEdge;
  startX: number;
  startY: number;
  origin: PaneGeometry;
}

/**
 * 几何变化后提醒 CM6 重新测量。
 *
 * 最小化 / 最大化 / 复位会把内容区尺寸从「0 / 裁剪」变回真实尺寸，而 `EditorView` 的测量是
 * 事件驱动的（宿主监听 `window` 的 `resize`）。这里派发一次**合成 resize**（只在几何真正变化时），
 * 让已挂载的编辑器重新测量，避免还原后出现「空白 / 行号错位」。
 */
function nudgeEditorLayout(): void {
  if (typeof window === 'undefined') return;
  window.requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
}

export interface ScratchPaneProps {
  /** 窗格状态（来自 `scratch-store`；几何以它为准） */
  pane: ScratchPane;
  /** 是否为当前聚焦窗格 */
  active: boolean;
  /** 关闭请求（由 `ScratchPaneLayer` 判断脏标记 → 询问 / 直接关） */
  onRequestClose: (paneId: string) => void;
}

/**
 * 单个窗格外壳。
 *
 * 注意：**不做任何「窗格被遮挡就卸载」的动作** —— 见文件头「已挂载永不复位」。
 */
export function ScratchPane({ pane, active, onRequestClose }: ScratchPaneProps) {
  const rootRef = useRef<HTMLElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const resizeRef = useRef<ResizeState | null>(null);

  const focusPane = useScratchStore((state) => state.focusPane);
  const movePane = useScratchStore((state) => state.movePane);
  const toggleMinimize = useScratchStore((state) => state.toggleMinimize);
  const toggleMaximize = useScratchStore((state) => state.toggleMaximize);
  const setMounted = useScratchStore((state) => state.setMounted);

  const { min: minimized, maxed, z } = pane.geometry;
  const mounted = pane.mounted;
  /** 本窗格是否与视口相交（懒挂载条件①；命中后不回退） */
  const [visible, setVisible] = useState(false);

  // 状态栏实时量：来自宿主的广播（`行:列` / 选中 / 总数，§4.1.4）。未挂载时返回 null ⇒ 降级显示
  const liveStats = useScratchEditorStats(pane.id);

  // ── 懒挂载（§4.1.3）：**两个条件取交集**才允许宿主建 CM6 实例 ──
  // ① 窗格矩形与视口相交
  useEffect(() => {
    if (visible || minimized) return;
    const el = rootRef.current;
    if (!el) return;
    // 没有 IntersectionObserver（老 WebEngine / 测试环境）：视为可见，保证功能可用
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
    observer.observe(el);
    return () => observer.disconnect();
  }, [minimized, visible]);

  // ② 窗格被激活。与 ① 取交集的原因：10 个还原窗格可能同时落在视口内，
  //    若按「相交即挂载」就会一次建 10 个实例 —— 与 AC-60「只有当前激活的 1 个建实例」冲突。
  useEffect(() => {
    if (mounted || minimized || !visible || !active) return;
    setMounted(pane.id, true);
  }, [active, minimized, mounted, pane.id, setMounted, visible]);

  // ── 拖动（标题栏）──
  const handleDragPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      if (event.button !== 0) return;
      if (maxed) return; // 最大化窗格不可拖动（与用户直觉一致）
      const target = event.currentTarget;
      try {
        target.setPointerCapture(event.pointerId);
      } catch {
        // 指针已释放（极少数竞态）：退化为普通拖动，不中断
      }
      dragRef.current = { pointerId: event.pointerId, lastX: event.clientX, lastY: event.clientY };
    },
    [maxed],
  );

  const handleDragPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const dx = event.clientX - drag.lastX;
      const dy = event.clientY - drag.lastY;
      if (dx === 0 && dy === 0) return;
      drag.lastX = event.clientX;
      drag.lastY = event.clientY;
      movePane(pane.id, dx, dy);
    },
    [movePane, pane.id],
  );

  const handleDragPointerUp = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // 已自动释放
    }
  }, []);

  const dragHandlers: ScratchPaneDragHandlers = {
    onPointerDown: handleDragPointerDown,
    onPointerMove: handleDragPointerMove,
    onPointerUp: handleDragPointerUp,
    onPointerCancel: handleDragPointerUp,
  };

  // ── 缩放（8 向）──
  const startResize = useCallback(
    (edge: ResizeEdge) => (event: ReactPointerEvent<HTMLElement>) => {
      if (event.button !== 0) return;
      event.stopPropagation();
      const target = event.currentTarget;
      try {
        target.setPointerCapture(event.pointerId);
      } catch {
        // 同上：退化处理
      }
      resizeRef.current = {
        pointerId: event.pointerId,
        edge,
        startX: event.clientX,
        startY: event.clientY,
        origin: { ...pane.geometry },
      };
    },
    [pane.geometry],
  );

  const handleResizePointerMove = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const state = resizeRef.current;
      if (!state || state.pointerId !== event.pointerId) return;
      const east = state.edge.includes('e');
      const west = state.edge.includes('w');
      const north = state.edge.includes('n');
      const south = state.edge.includes('s');
      const dx = event.clientX - state.startX;
      const dy = event.clientY - state.startY;

      const store = useScratchStore.getState();
      const current = store.panes.find((item) => item.id === pane.id);
      if (!current) return;

      const targetW = Math.max(
        PANE_MIN_WIDTH,
        Math.round(state.origin.w + (east ? dx : 0) + (west ? -dx : 0)),
      );
      const targetH = Math.max(
        PANE_MIN_HEIGHT,
        Math.round(state.origin.h + (south ? dy : 0) + (north ? -dy : 0)),
      );
      // 西 / 北边缘：尺寸变化要配一次位置补偿（store 的 resizePane 只改 w/h）
      const targetX = west
        ? Math.round(state.origin.x + (state.origin.w - targetW))
        : current.geometry.x;
      const targetY = north
        ? Math.round(state.origin.y + (state.origin.h - targetH))
        : current.geometry.y;

      if (targetX !== current.geometry.x || targetY !== current.geometry.y) {
        store.movePane(pane.id, targetX - current.geometry.x, targetY - current.geometry.y);
      }
      if (targetW !== current.geometry.w || targetH !== current.geometry.h) {
        store.resizePane(pane.id, targetW, targetH);
      }
    },
    [pane.id],
  );

  const handleResizePointerUp = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const state = resizeRef.current;
    if (!state || state.pointerId !== event.pointerId) return;
    resizeRef.current = null;
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // 已自动释放
    }
    nudgeEditorLayout();
  }, []);

  // ── 最小化 / 最大化：几何交给 store，随后提醒编辑器重测 ──
  const handleToggleMinimize = useCallback(
    (paneId: string) => {
      toggleMinimize(paneId);
      nudgeEditorLayout();
    },
    [toggleMinimize],
  );

  const handleToggleMaximize = useCallback(
    (paneId: string) => {
      toggleMaximize(paneId);
      nudgeEditorLayout();
    },
    [toggleMaximize],
  );

  // 点击窗格任意处即置顶并激活（层级聚焦；同时满足 §4.1.3 条件②）
  const handleActivate = useCallback(() => {
    if (active) return;
    focusPane(pane.id);
  }, [active, focusPane, pane.id]);

  const style: CSSProperties = maxed
    ? { left: 0, top: 0, width: '100%', height: '100%', zIndex: z }
    : {
        left: pane.geometry.x,
        top: pane.geometry.y,
        width: pane.geometry.w,
        height: minimized ? undefined : pane.geometry.h,
        zIndex: z,
      };

  const resizable = !minimized && !maxed;

  return (
    <section
      ref={rootRef}
      className={[
        'scratch-pane',
        active ? 'scratch-pane-active' : '',
        minimized ? 'scratch-pane-min' : '',
        maxed ? 'scratch-pane-max' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      style={style}
      // 作用域锚点：应用级快捷键靠它判定「窗格聚焦类」作用域（设计 §4.2.2）
      data-scratch-pane={pane.id}
      data-pane-active={active ? 'true' : undefined}
      aria-label={`编辑窗格：${pane.title}`}
      onPointerDownCapture={handleActivate}
    >
      <ScratchPaneTitleBar
        pane={pane}
        active={active}
        onRequestClose={onRequestClose}
        onToggleMinimize={handleToggleMinimize}
        onToggleMaximize={handleToggleMaximize}
        onDoubleClick={handleToggleMaximize}
        dragHandlers={dragHandlers}
      />

      {/* 最小化时整块隐藏（**组件保持挂载**，见文件头「已挂载永不复位」） */}
      <div className="scratch-pane-main" hidden={minimized}>
        {mounted ? (
          <ScratchEditorHost pane={pane} className="scratch-host" />
        ) : (
          <ScratchPaneSkeleton />
        )}
        <ScratchPaneStatusBar pane={pane} stats={liveStats} />
      </div>

      {resizable &&
        RESIZE_EDGES.map((edge) => (
          <div
            key={edge}
            className={`scratch-resize scratch-resize-${edge}`}
            data-resize-edge={edge}
            onPointerDown={startResize(edge)}
            onPointerMove={handleResizePointerMove}
            onPointerUp={handleResizePointerUp}
            onPointerCancel={handleResizePointerUp}
          />
        ))}
    </section>
  );
}

/** 未挂载窗格的内容区骨架（保持标题栏 + 状态栏可见，§4.1.3） */
function ScratchPaneSkeleton() {
  return (
    <div className="scratch-skeleton" aria-hidden="true">
      <span className="scratch-skeleton-line" />
      <span className="scratch-skeleton-line" />
      <span className="scratch-skeleton-line scratch-skeleton-line-short" />
    </div>
  );
}

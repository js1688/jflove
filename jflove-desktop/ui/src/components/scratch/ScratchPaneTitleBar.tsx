/**
 * 窗格标题栏（设计 v1.6.0 §4.1.1）
 *
 * 职责：**标题 / 目标文件名 / 脏标记 / 最小化 / 最大化-还原 / 关闭**，并承载拖动热区。
 *
 * ## 为什么拖动逻辑不写在这里
 *
 * 拖动要写回 `scratch-store` 的几何（`movePane` 的增量语义 + 指针捕获 + 视口钳制），
 * 这些属于**窗格外壳**（`ScratchPane`）的职责。本组件只把标题栏上的指针事件原样转发给
 * `dragHandlers`（避免两个文件各写一份几何逻辑）。
 *
 * ## 文案口径（设计 §7.4 / AGENTS.md §9.4.13）
 *
 * 标题只显示**文件名**（`pane.title`，草稿形态为「未命名」）；**永不显示完整路径**，
 * 也不把路径写进 `title` / `aria-label` / 日志。
 */

import type { PointerEvent as ReactPointerEvent } from 'react';
import type { ScratchPane } from '../../types/scratch';

/** 标题栏上的指针事件（由 `ScratchPane` 提供实现） */
export interface ScratchPaneDragHandlers {
  onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerMove: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerUp: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerCancel: (event: ReactPointerEvent<HTMLElement>) => void;
}

export interface ScratchPaneTitleBarProps {
  /** 窗格状态（标题 / 脏标记 / 最小化与最大化标记） */
  pane: ScratchPane;
  /** 是否为当前聚焦窗格（决定高亮） */
  active: boolean;
  /** 关闭请求：**脏窗格由上层先弹询问**（保存 / 放弃 / 取消），本组件不直接关 */
  onRequestClose: (paneId: string) => void;
  /** 最小化 / 还原 */
  onToggleMinimize: (paneId: string) => void;
  /** 最大化 / 还原 */
  onToggleMaximize: (paneId: string) => void;
  /** 双击标题栏 = 最大化 / 还原 */
  onDoubleClick: (paneId: string) => void;
  /** 拖动（指针事件转发） */
  dragHandlers: ScratchPaneDragHandlers;
}

const ICON_MIN = (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
    <path d="M0 5.5h10" stroke="currentColor" strokeWidth="1" fill="none" />
  </svg>
);

const ICON_MAX = (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
    <rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" />
  </svg>
);

const ICON_RESTORE = (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
    <rect x="0.5" y="2.5" width="7" height="7" fill="none" stroke="currentColor" />
    <path d="M2.5 2.5V0.5h7v7h-2" fill="none" stroke="currentColor" />
  </svg>
);

const ICON_CLOSE = (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
    <path d="M0.5 0.5l9 9M9.5 0.5l-9 9" stroke="currentColor" strokeWidth="1" fill="none" />
  </svg>
);

const BTN_CLASS = 'scratch-title-btn';

export function ScratchPaneTitleBar({
  pane,
  active,
  onRequestClose,
  onToggleMinimize,
  onToggleMaximize,
  onDoubleClick,
  dragHandlers,
}: ScratchPaneTitleBarProps) {
  const minimized = pane.geometry.min;
  const maxed = pane.geometry.maxed;

  return (
    <header
      className={active ? 'scratch-title scratch-title-active' : 'scratch-title'}
      // 拖动热区：按下即开始拖动（按钮上的按下会被按钮自己 stopPropagation）
      onPointerDown={dragHandlers.onPointerDown}
      onPointerMove={dragHandlers.onPointerMove}
      onPointerUp={dragHandlers.onPointerUp}
      onPointerCancel={dragHandlers.onPointerCancel}
      onDoubleClick={(event) => {
        // 双击按钮不应触发最大化 / 还原
        if ((event.target as HTMLElement).closest('.scratch-title-btn')) return;
        onDoubleClick(pane.id);
      }}
    >
      <span className="scratch-title-name" title={pane.title}>
        {pane.title}
      </span>

      {/* 草稿形态标记：明确告诉用户「这份内容还没落到用户文件上」（§4.1.2 的 path === null 语义） */}
      {pane.path === null && (
        <span className="scratch-title-chip" title="尚未指定保存位置，内容保存在草稿库">
          草稿
        </span>
      )}

      {pane.readOnly && (
        <span className="scratch-title-chip" title="只读窗格">
          只读
        </span>
      )}

      {/* 脏标记：与状态栏同源（pane.dirty） */}
      {pane.dirty && (
        <span className="scratch-title-dot" title="有未保存的修改" aria-label="有未保存的修改" />
      )}

      <span className="scratch-title-grow" />

      <button
        type="button"
        className={BTN_CLASS}
        title={minimized ? '还原窗格' : '最小化窗格'}
        aria-label={minimized ? '还原窗格' : '最小化窗格'}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => onToggleMinimize(pane.id)}
      >
        {ICON_MIN}
      </button>

      <button
        type="button"
        className={BTN_CLASS}
        title={maxed ? '还原窗格' : '最大化窗格'}
        aria-label={maxed ? '还原窗格' : '最大化窗格'}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => onToggleMaximize(pane.id)}
      >
        {maxed ? ICON_RESTORE : ICON_MAX}
      </button>

      <button
        type="button"
        className={`${BTN_CLASS} scratch-title-btn-close`}
        title="关闭窗格"
        aria-label="关闭窗格"
        onPointerDown={(event) => event.stopPropagation()}
        onClick={() => onRequestClose(pane.id)}
      >
        {ICON_CLOSE}
      </button>
    </header>
  );
}

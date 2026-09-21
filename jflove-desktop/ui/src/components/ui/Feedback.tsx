/**
 * 基础容器与反馈组件：Card / Badge / Progress / Skeleton / EmptyState / ErrorState
 * 规格见设计文档 §5。
 */
import type { HTMLAttributes, ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

/* ==================== Card ==================== */

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** 悬停时抬升（列表/可点击卡片用） */
  hover?: boolean;
  /** 内边距档位：none 时不加 padding（自己控制分段布局） */
  pad?: 'none' | 'md';
}

export function Card({ hover = false, pad = 'md', className, children, ...rest }: CardProps) {
  const classes = ['card', hover && 'card-hover', pad === 'md' && 'p-5', className]
    .filter(Boolean)
    .join(' ');
  return (
    <div className={classes} {...rest}>
      {children}
    </div>
  );
}

/** 卡片头部（标题行 + 右侧操作），配合 `<Card pad="none">` 使用 */
export function CardHead({
  icon,
  title,
  extra,
  className,
}: {
  icon?: IconName;
  title: ReactNode;
  extra?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`flex items-center gap-3 border-b border-line-subtle px-5 py-4 ${className ?? ''}`}
    >
      {icon && <Icon name={icon} className="text-brand-500" />}
      <span className="text-[15px] font-semibold">{title}</span>
      <div className="grow" />
      {extra}
    </div>
  );
}

/* ==================== Badge ==================== */

export type BadgeTone = 'brand' | 'success' | 'warning' | 'danger' | 'neutral';

export interface BadgeProps {
  tone?: BadgeTone;
  icon?: IconName;
  children: ReactNode;
  className?: string;
}

export function Badge({ tone = 'neutral', icon, children, className }: BadgeProps) {
  return (
    <span className={['badge', `badge-${tone}`, className].filter(Boolean).join(' ')}>
      {icon && <Icon name={icon} size="sm" strokeWidth={2.2} className="!h-3 !w-3" />}
      {children}
    </span>
  );
}

/* ==================== Progress ==================== */

export function Progress({ value, className }: { value: number; className?: string }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div
      className={['progress', className].filter(Boolean).join(' ')}
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <i style={{ width: `${pct}%` }} />
    </div>
  );
}

/* ==================== Skeleton ==================== */

export function Skeleton({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return <div className={['skeleton', className].filter(Boolean).join(' ')} style={style} />;
}

/** 列表骨架：替代转圈，列表/卡片加载时用 */
export function ListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-3 p-4" aria-busy="true" aria-label="加载中">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="h-9 w-9 shrink-0 rounded-md" />
          <div className="flex flex-1 flex-col gap-1.5">
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="h-3 w-1/5" />
          </div>
        </div>
      ))}
    </div>
  );
}

/* ==================== EmptyState ==================== */

export interface EmptyStateProps {
  icon?: IconName;
  title?: string;
  description?: string;
  /** 行动按钮区 */
  action?: ReactNode;
}

/**
 * 统一空状态（需求 AC-6）。
 * 注意：v1.4.2 的实现默认图标是 emoji `📭`，此处已改为矢量图标。
 */
export function EmptyState({
  icon = 'files',
  title = '暂无数据',
  description,
  action,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center px-6 py-16 text-center">
      <div className="empty-art mb-5">
        <Icon name={icon} size="xl" strokeWidth={1.4} className="!h-11 !w-11" />
      </div>
      <div className="text-sm font-semibold text-fg">{title}</div>
      {description && <div className="mt-1 max-w-sm text-xs text-muted">{description}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/* ==================== ErrorState ==================== */

export interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}

/**
 * 统一错误态（需求 AC-6）。
 * 替代原先各页面手写的 `加载失败: xxx` 纯文本。
 */
export function ErrorState({ message, onRetry, retryLabel = '重试' }: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center px-6 py-16 text-center" role="alert">
      <div className="mb-4 grid h-16 w-16 place-items-center rounded-3xl bg-danger-50 text-danger-500">
        <Icon name="error" size="xl" strokeWidth={1.5} className="!h-8 !w-8" />
      </div>
      <div className="text-sm font-semibold text-fg">加载失败</div>
      <div className="mt-1 max-w-md text-xs break-words text-muted">{message}</div>
      {onRetry && (
        <button type="button" className="btn btn-secondary btn-sm mt-5" onClick={onRetry}>
          <Icon name="refresh" size="sm" />
          {retryLabel}
        </button>
      )}
    </div>
  );
}

import type { ReactNode } from 'react';
import { Icon, IconButton } from './ui';

interface PageHeaderProps {
  title: string;
  /** 标题下方的次要信息（可选） */
  subtitle?: string;
  actions?: ReactNode;
  onBack?: () => void;
}

/**
 * 页面标题栏（sticky 固定顶部，滚动时不被滚走；PC / 移动视图通用）
 *
 * v1.5.0：视觉重做 —— 返回键由 `←` 字符改为矢量图标，标题字重/高度对齐设计令牌。
 * props 与 v1.4.2 保持兼容（新增 subtitle 为可选）。
 */
export function PageHeader({ title, subtitle, actions, onBack }: PageHeaderProps) {
  return (
    <div className="page-header">
      {onBack && (
        <IconButton icon="back" label="返回" size="sm" onClick={onBack} className="-ml-1" />
      )}
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-[17px] leading-tight font-semibold tracking-[-0.014em]">
          {title}
        </h1>
        {subtitle && <div className="truncate text-[11.5px] text-subtle">{subtitle}</div>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

/** 供页面复用的图标再导出，避免每页都从 ui 深路径导入 */
export { Icon };

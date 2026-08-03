import { ReactNode } from 'react';

interface PageHeaderProps {
  title: string;
  actions?: ReactNode;
  onBack?: () => void;
}

/** 页面标题栏（sticky 固定顶部，滚动时不被滚走；PC / 移动视图通用） */
export function PageHeader({ title, actions, onBack }: PageHeaderProps) {
  return (
    <div className="sticky top-0 z-20 flex items-center h-14 px-4 border-b border-gray-100 bg-white">
      {onBack && (
        <button
          onClick={onBack}
          className="mr-3 text-gray-400 hover:text-gray-600 text-lg"
          aria-label="返回"
        >
          ←
        </button>
      )}
      <h1 className="text-lg font-semibold text-gray-800 flex-1 truncate">{title}</h1>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

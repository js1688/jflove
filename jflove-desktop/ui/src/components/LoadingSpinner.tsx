import { Icon } from './ui';

/**
 * 加载指示器
 *
 * v1.5.0：配色改为令牌（原先 `border-indigo-500` 硬编码），并修掉 `border-3`
 * —— 那不是 Tailwind 的内置刻度（只有 2/4/8），原先实际没生效。
 *
 * 列表/卡片场景建议优先用 `ListSkeleton`（骨架屏），转圈只用于不确定等待。
 */
export function LoadingSpinner({ text = '加载中…' }: { text?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-subtle">
      <Icon name="loading" size="xl" className="animate-spin text-brand-500" />
      <span className="text-[13px]">{text}</span>
    </div>
  );
}

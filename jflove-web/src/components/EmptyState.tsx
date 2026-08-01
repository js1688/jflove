/** 空状态 */
export function EmptyState({
  icon = '📭',
  title = '暂无数据',
  description,
}: {
  icon?: string;
  title?: string;
  description?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-gray-400">
      <span className="text-4xl mb-3">{icon}</span>
      <span className="text-sm font-medium">{title}</span>
      {description && <span className="text-xs mt-1">{description}</span>}
    </div>
  );
}

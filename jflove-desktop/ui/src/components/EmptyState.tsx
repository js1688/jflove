import { EmptyState as UiEmptyState } from './ui';
import type { IconName } from './ui';

/**
 * 空状态（兼容层）
 *
 * v1.5.0：实现迁移到 `components/ui/Feedback.tsx` 的统一 EmptyState。
 * 这里保留旧签名以兼容既有调用点，但把 `icon` 从 **emoji 字符串** 改为语义图标名
 * （需求 AC-5：控件图标全面矢量化）。
 *
 * 迁移提示：新代码请直接用 `import { EmptyState } from '@/components/ui'`。
 */
export function EmptyState({
  icon = 'files',
  title = '暂无数据',
  description,
}: {
  icon?: IconName;
  title?: string;
  description?: string;
}) {
  return <UiEmptyState icon={icon} title={title} description={description} />;
}

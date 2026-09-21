import { ConfirmDialog as UiConfirmDialog } from './ui';

interface ConfirmDialogProps {
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * 确认对话框（兼容层）
 *
 * v1.5.0：实现迁移到 `components/ui/Overlay.tsx` 的统一 Modal + ConfirmDialog，
 * 对外 props 与 v1.4.2 完全一致，既有调用点无需改动。
 *
 * 迁移提示：新代码请直接用 `import { ConfirmDialog } from '@/components/ui'`。
 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel = '确认',
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <UiConfirmDialog
      title={title}
      message={message}
      confirmLabel={confirmLabel}
      danger={danger}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}

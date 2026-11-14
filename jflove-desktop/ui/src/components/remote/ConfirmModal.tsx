/**
 * 应用内确认弹层（v1.10.4）
 *
 * 替代 `window.confirm`：固定居中、主题一致、可键盘操作。
 */
import { Button } from '../../components/ui';

export interface ConfirmModalProps {
  title: string;
  message: string;
  confirmText?: string;
  danger?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmModal({
  title,
  message,
  confirmText = '确定',
  danger = false,
  onCancel,
  onConfirm,
}: ConfirmModalProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
      onClick={onCancel}
    >
      <div
        className="w-[400px] rounded-xl border border-line bg-surface p-5 shadow-e4"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onCancel();
          if (event.key === 'Enter') onConfirm();
        }}
        tabIndex={-1}
      >
        <div className="mb-2 text-sm font-semibold">{title}</div>
        <p className="text-[12.5px] leading-relaxed text-muted">{message}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            取消
          </Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} autoFocus>
            {confirmText}
          </Button>
        </div>
      </div>
    </div>
  );
}

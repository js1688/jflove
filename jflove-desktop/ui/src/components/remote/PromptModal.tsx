/**
 * 应用内输入弹层（v1.10.2）
 *
 * 替代 `window.prompt`：与现有 UI 一致、支持暗色主题、可键盘确认/取消。
 */
import { useState } from 'react';
import { Button } from '../../components/ui';

export interface PromptModalProps {
  title: string;
  initial?: string;
  placeholder?: string;
  /** 确认按钮文案，默认「确定」 */
  confirmText?: string;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}

export function PromptModal({
  title,
  initial = '',
  placeholder,
  confirmText = '确定',
  onCancel,
  onConfirm,
}: PromptModalProps) {
  const [value, setValue] = useState(initial);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
      onClick={onCancel}
    >
      <div
        className="w-[420px] rounded-xl border border-line bg-surface p-5 shadow-e4"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-3 text-sm font-semibold">{title}</div>
        <input
          autoFocus
          className="input"
          value={value}
          placeholder={placeholder}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onConfirm(value);
            if (event.key === 'Escape') onCancel();
          }}
        />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            取消
          </Button>
          <Button variant="primary" onClick={() => onConfirm(value)}>
            {confirmText}
          </Button>
        </div>
      </div>
    </div>
  );
}

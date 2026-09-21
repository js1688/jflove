/**
 * Toast / Modal（含确认弹窗）/ Segmented / Input
 * 规格见设计文档 §5。
 */
import {
  useEffect,
  useRef,
  type InputHTMLAttributes,
  type ReactNode,
} from 'react';
import { create } from 'zustand';
import { Icon, type IconName } from './Icon';
// 主题切换直接读 store：theme-store 本身无 UI 依赖，不存在循环 import
import { useThemeStore, type ThemeMode } from '../../stores/theme-store';

/* ==================== Toast ==================== */

export type ToastTone = 'success' | 'error' | 'info';

interface ToastItem {
  id: number;
  tone: ToastTone;
  title: string;
  description?: string;
}

interface ToastState {
  items: ToastItem[];
  push: (t: Omit<ToastItem, 'id'>) => void;
  dismiss: (id: number) => void;
}

let toastSeq = 0;

export const useToastStore = create<ToastState>((set) => ({
  items: [],
  push: (t) => {
    const id = ++toastSeq;
    set((s) => ({ items: [...s.items, { ...t, id }] }));
    // 自动消失（错误提示留久一点，便于阅读）
    const ttl = t.tone === 'error' ? 6000 : 3500;
    setTimeout(() => set((s) => ({ items: s.items.filter((i) => i.id !== id) })), ttl);
  },
  dismiss: (id) => set((s) => ({ items: s.items.filter((i) => i.id !== id) })),
}));

/** 命令式调用：`toast.success('已保存')` */
export const toast = {
  success: (title: string, description?: string) =>
    useToastStore.getState().push({ tone: 'success', title, description }),
  error: (title: string, description?: string) =>
    useToastStore.getState().push({ tone: 'error', title, description }),
  info: (title: string, description?: string) =>
    useToastStore.getState().push({ tone: 'info', title, description }),
};

const TOAST_ICON: Record<ToastTone, IconName> = {
  success: 'success',
  error: 'error',
  info: 'info',
};

/** 渲染 Toast 容器（挂在 App 根部，一次即可） */
export function ToastHost() {
  const items = useToastStore((s) => s.items);
  const dismiss = useToastStore((s) => s.dismiss);
  if (items.length === 0) return null;
  return (
    <div className="pointer-events-none fixed top-4 right-4 z-[100] flex flex-col gap-2">
      {items.map((t) => (
        <div key={t.id} className="toast pointer-events-auto" role="status">
          <span className={`toast-ico toast-ico-${t.tone}`}>
            <Icon name={TOAST_ICON[t.tone]} size="sm" strokeWidth={2.2} />
          </span>
          <div className="flex-1">
            <div className="text-[13px] font-semibold text-fg">{t.title}</div>
            {t.description && <div className="mt-0.5 text-xs text-muted">{t.description}</div>}
          </div>
          <button
            type="button"
            className="icon-btn icon-btn-sm"
            aria-label="关闭"
            onClick={() => dismiss(t.id)}
          >
            <Icon name="close" size="sm" />
          </button>
        </div>
      ))}
    </div>
  );
}

/* ==================== Modal ==================== */

export interface ModalProps {
  open: boolean;
  title: string;
  icon?: IconName;
  onClose: () => void;
  children: ReactNode;
  /** 底部操作区 */
  footer?: ReactNode;
  width?: number;
}

export function Modal({ open, title, icon, onClose, children, footer, width = 520 }: ModalProps) {
  // Esc 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="overlay"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} style={{ width }}>
        <div className="flex items-center gap-3 px-5 pt-5 pb-3">
          {icon && <Icon name={icon} className="text-brand-500" />}
          <span className="text-[15px] font-semibold">{title}</span>
          <div className="grow" />
          <button type="button" className="icon-btn icon-btn-sm" aria-label="关闭" onClick={onClose}>
            <Icon name="close" size="sm" />
          </button>
        </div>
        <div className="px-5 pb-5">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-line-subtle bg-sunken px-5 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

/* ==================== ConfirmDialog ==================== */

export interface ConfirmDialogProps {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 危险操作：确认按钮变红 + 警示图标 */
  danger?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * 确认弹窗（保持 v1.4.2 的对外签名，内部换成新视觉）。
 * 破坏性操作请传 `danger`，会使用危险色确认按钮。
 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel = '确认',
  cancelLabel = '取消',
  danger = false,
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal
      open
      title={title}
      icon={danger ? 'warning' : 'help'}
      onClose={onCancel}
      width={440}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`}
            onClick={onConfirm}
            disabled={loading}
          >
            {loading && <Icon name="loading" size="sm" className="animate-spin" />}
            {confirmLabel}
          </button>
        </>
      }
    >
      <p className="m-0 text-[13.5px] leading-relaxed break-words text-muted">{message}</p>
    </Modal>
  );
}

/* ==================== Segmented ==================== */

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  icon?: IconName;
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  className,
}: {
  value: T;
  options: SegmentedOption<T>[];
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div className={['segmented', className].filter(Boolean).join(' ')} role="tablist">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={value === o.value}
          data-on={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.icon && <Icon name={o.icon} size="sm" />}
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ==================== Input ==================== */

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** 左侧图标 */
  icon?: IconName;
}

export function Input({ icon, className, ...rest }: InputProps) {
  if (!icon) {
    return <input className={['input', className].filter(Boolean).join(' ')} {...rest} />;
  }
  return (
    <div className={['search-field', '!h-[38px]', className].filter(Boolean).join(' ')}>
      <Icon name={icon} size="sm" />
      <input {...rest} />
    </div>
  );
}

/* ==================== Switch ==================== */

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="relative h-[22px] w-[38px] shrink-0 rounded-full transition-colors duration-[180ms] disabled:opacity-45"
      style={{
        backgroundImage: checked ? 'var(--grad-brand)' : undefined,
        backgroundColor: checked ? undefined : 'var(--neutral-300)',
      }}
    >
      <span
        className="absolute top-0.5 left-0.5 h-[18px] w-[18px] rounded-full bg-white shadow-e1 transition-transform duration-[180ms]"
        style={{
          transform: checked ? 'translateX(16px)' : 'none',
          transitionTimingFunction: 'var(--ease-spring)',
        }}
      />
    </button>
  );
}

/* ==================== 主题切换按钮 ==================== */

/** 亮 → 暗 → 跟随系统 三态循环切换（顶栏用） */
export function ThemeToggle() {
  const mode = useThemeStore((s) => s.mode);
  const setMode = useThemeStore((s) => s.setMode);

  const order: ThemeMode[] = ['system', 'light', 'dark'];
  const meta: Record<ThemeMode, { icon: IconName; label: string }> = {
    system: { icon: 'themeSystem', label: '跟随系统' },
    light: { icon: 'themeLight', label: '亮色' },
    dark: { icon: 'theme', label: '暗色' },
  };
  const cur = meta[mode];

  return (
    <button
      type="button"
      className="icon-btn"
      onClick={() => setMode(order[(order.indexOf(mode) + 1) % order.length])}
      title={`主题：${cur.label}（点击切换）`}
      aria-label={`主题：${cur.label}（点击切换）`}
    >
      <Icon name={cur.icon} />
    </button>
  );
}

/** 让外部（如设置页）拿到 toast 的命令式接口 */
export function useToast() {
  const push = useToastStore((s) => s.push);
  return useRef({
    success: (t: string, d?: string) => push({ tone: 'success', title: t, description: d }),
    error: (t: string, d?: string) => push({ tone: 'error', title: t, description: d }),
    info: (t: string, d?: string) => push({ tone: 'info', title: t, description: d }),
  }).current;
}

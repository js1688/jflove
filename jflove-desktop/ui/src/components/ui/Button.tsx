/**
 * 按钮 / 图标按钮
 *
 * 视觉规格见设计文档 §5 组件契约；样式落在 `styles/components.css` 的 `.btn-*`，
 * 这里只负责语义化的 props 与无障碍属性。
 */
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Icon, type IconName, type IconSize } from './Icon';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  ghost: 'btn-ghost',
  danger: 'btn-danger',
};

const SIZE_CLASS: Record<ButtonSize, string> = {
  sm: 'btn-sm',
  md: '',
  lg: 'btn-lg',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** 左侧图标（语义名） */
  icon?: IconName;
  /** 右侧图标（语义名） */
  iconRight?: IconName;
  /** 加载中：显示旋转图标并禁用点击 */
  loading?: boolean;
  children?: ReactNode;
}

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  iconRight,
  loading = false,
  disabled,
  className,
  children,
  ...rest
}: ButtonProps) {
  const iconSize: IconSize = size === 'lg' ? 'md' : 'sm';
  const classes = ['btn', VARIANT_CLASS[variant], SIZE_CLASS[size], className]
    .filter(Boolean)
    .join(' ');

  return (
    <button type="button" className={classes} disabled={disabled || loading} {...rest}>
      {loading ? (
        <Icon name="loading" size={iconSize} className="animate-spin" />
      ) : (
        icon && <Icon name={icon} size={iconSize} />
      )}
      {children}
      {iconRight && <Icon name={iconRight} size={iconSize} />}
    </button>
  );
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** 语义图标名 */
  icon: IconName;
  /** 无障碍标签（必填：图标按钮没有可见文本） */
  label: string;
  size?: 'sm' | 'md';
  iconSize?: IconSize;
  /** 右上角小红点（有新内容提示） */
  dot?: boolean;
}

export function IconButton({
  icon,
  label,
  size = 'md',
  iconSize = 'md',
  dot = false,
  className,
  ...rest
}: IconButtonProps) {
  const classes = ['icon-btn', size === 'sm' && 'icon-btn-sm', className].filter(Boolean).join(' ');
  return (
    <button type="button" className={classes} aria-label={label} title={label} {...rest}>
      <Icon name={icon} size={iconSize} />
      {dot && (
        <span
          className="absolute top-[7px] right-[7px] h-[7px] w-[7px] rounded-full bg-accent-500 ring-2 ring-surface"
          aria-hidden="true"
        />
      )}
    </button>
  );
}

import { useEffect, useState } from 'react';
import { Icon, IconButton } from './ui';

interface ErrorBannerProps {
  message: string;
  onDismiss?: () => void;
  duration?: number;
}

/**
 * 错误提示条
 *
 * v1.5.0：⚠️/✕ 字符改为矢量图标，配色走令牌（原先 `red-50/border-red-200/...` 硬编码）。
 * props 与 v1.4.2 保持兼容。
 *
 * v1.10.26（P7）：**默认不再自动消失**（`duration` 默认 5000 → 0）。
 * 错误提示自动消失的问题在于：用户还没看清"为什么失败"提示就没了，
 * 只能重复点击再猜一次 —— 实测登录取证时，网络错误横幅 5 秒后消失，
 * 截图/复现都抓不到原因。现在改为常驻，由用户点「关闭」或下次成功操作清除；
 * 需要自动消失的场景仍可显式传 `duration`。
 */
export function ErrorBanner({ message, onDismiss, duration = 0 }: ErrorBannerProps) {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    if (duration > 0) {
      const t = setTimeout(() => {
        setVisible(false);
        onDismiss?.();
      }, duration);
      return () => clearTimeout(t);
    }
  }, [duration, onDismiss]);

  if (!visible || !message) return null;

  return (
    <div
      role="alert"
      className="fixed top-4 left-1/2 z-50 flex max-w-md -translate-x-1/2 items-center gap-2.5 rounded-lg border border-danger-500/25 bg-danger-50 px-4 py-3 text-[13px] text-danger-700 shadow-e4"
    >
      <Icon name="warning" size="sm" className="shrink-0" />
      <span className="flex-1 break-words">{message}</span>
      <IconButton
        icon="close"
        label="关闭"
        size="sm"
        onClick={() => {
          setVisible(false);
          onDismiss?.();
        }}
      />
    </div>
  );
}

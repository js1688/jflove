import { useEffect, useState } from 'react';

interface ErrorBannerProps {
  message: string;
  onDismiss?: () => void;
  duration?: number;
}

/** 错误提示条 */
export function ErrorBanner({ message, onDismiss, duration = 5000 }: ErrorBannerProps) {
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
    <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 px-4 py-3 bg-red-50 border border-red-200 text-red-700 rounded-lg shadow-lg text-sm max-w-md">
      <span>⚠️</span>
      <span className="flex-1">{message}</span>
      <button
        onClick={() => { setVisible(false); onDismiss?.(); }}
        className="text-red-400 hover:text-red-600"
      >
        ✕
      </button>
    </div>
  );
}

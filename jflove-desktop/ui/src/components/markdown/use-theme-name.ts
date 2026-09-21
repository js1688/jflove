/**
 * 读取当前主题名（供图表渲染使用）
 *
 * 为什么不用 `useThemeStore` 直接取：
 *   - 图表是叶子组件，且可能在 store 未初始化时渲染（例如测试环境）；
 *   - 这里以 **DOM 上的 `.dark` 类**为准，与实际生效的主题永远一致
 *     （首屏防闪烁脚本也是改这个类），不会出现"状态与画面不一致"的中间态。
 */
import { useEffect, useState } from 'react';
import type { ThemeName } from '../../utils/mermaid/types';

const THEME_EVENT = 'jflove:theme-changed';

/** 读取 `<html>` 上的主题类 */
export function readDomTheme(): ThemeName {
  if (typeof document === 'undefined') return 'light';
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
}

/**
 * 订阅主题变化。
 *
 * 除了 `MutationObserver`（theme-store 改 class 时触发），
 * 还监听自定义事件 `jflove:theme-changed`，便于测试与未来其它入口主动通知。
 */
export function useThemeName(): ThemeName {
  const [theme, setTheme] = useState<ThemeName>(readDomTheme);

  useEffect(() => {
    const sync = () => setTheme(readDomTheme());

    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    });
    window.addEventListener(THEME_EVENT, sync);
    sync();

    return () => {
      observer.disconnect();
      window.removeEventListener(THEME_EVENT, sync);
    };
  }, []);

  return theme;
}

/** 主动广播主题变化（theme-store 调用；也让测试能驱动） */
export function notifyThemeChanged(): void {
  window.dispatchEvent(new Event(THEME_EVENT));
}

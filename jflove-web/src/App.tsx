import { useEffect } from 'react';
import { RouterProvider } from 'react-router';
import { router } from './config/routes';
import { useThemeStore } from './stores/theme-store';
import { ToastHost } from './components/ui';

/**
 * 根组件
 *
 * v1.5.0 新增：
 *   - 订阅系统主题变化（`system` 模式下实时跟随，需求 AC-2）；
 *   - 挂载统一 Toast 容器（全站单例）。
 * 首屏防暗色闪烁由 `index.html` 的内联脚本负责（在 React 挂载前设好类名）。
 */
export function App() {
  const watchSystem = useThemeStore((s) => s.watchSystem);

  useEffect(() => {
    // 返回取消订阅函数，StrictMode 下重复挂载也安全
    return watchSystem();
  }, [watchSystem]);

  return (
    <>
      <RouterProvider router={router} />
      <ToastHost />
    </>
  );
}

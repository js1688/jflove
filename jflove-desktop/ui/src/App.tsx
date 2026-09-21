import { useEffect } from 'react';
import { RouterProvider } from 'react-router';
import { router } from './config/routes';
import { useThemeStore, type ThemeMode } from './stores/theme-store';
import { ToastHost } from './components/ui';
import { TitleBar } from './components/desktop/TitleBar';
import { callBridge, callShell, onBridgeEvent } from './utils/desktop-bridge';

/**
 * 根组件
 *
 * 与 Web 端 `jflove-web/src/App.tsx` 的差异：
 *   1. **多一层桌面端窗口标题栏**（Web 端由浏览器提供边框，桌面端去掉了 Qt 外框）；
 *   2. **接托盘的主题遥控**（N17）：Python 托盘菜单只发意图（`theme.set` 事件），
 *      由这里应用到渲染层；主题变化后再回执 `shell.reportTheme(mode)` 同步托盘勾选。
 *      主题真相只有一份 —— 始终在 Web 的 theme-store。
 *
 * 其余保持一致：订阅系统主题变化、挂载统一 Toast 容器；首屏防暗色闪烁由
 * `index.html` 的内联脚本负责（在 React 挂载前设好类名）。
 */

const THEME_MODES: ThemeMode[] = ['system', 'light', 'dark'];

export function App() {
  const watchSystem = useThemeStore((s) => s.watchSystem);
  const themeMode = useThemeStore((s) => s.mode);

  useEffect(() => {
    // 返回取消订阅函数，StrictMode 下重复挂载也安全
    return watchSystem();
  }, [watchSystem]);

  // 系统托盘「主题」菜单 → 渲染层应用
  useEffect(() => {
    return onBridgeEvent('theme.set', (payload) => {
      const mode = (payload as { mode?: string } | null)?.mode;
      if (mode && (THEME_MODES as string[]).includes(mode)) {
        useThemeStore.getState().setMode(mode as ThemeMode);
      }
    });
  }, []);

  // 主题变化 → 回执给 Python，同步托盘菜单的勾选状态
  useEffect(() => {
    void callShell('reportTheme', themeMode).catch(() => {
      // 在普通浏览器里 `npm run dev` 时没有桥，忽略
    });
  }, [themeMode]);

  // 兜底：离开预览页时确保原生媒体浮层已收起。
  //
  // 原生浮层是**独立顶层窗口**，会盖在 Web 内容之上；一旦它没被及时隐藏，
  // 用户看到的就是"页面内容被一块黑色窗口挡住"（实测反馈：预览页点返回后
  // 文件列表不见了）。预览页自身会清理，这里再兜一层：任何路由变化后只要
  // 当前不在预览页，就再发一次关闭（幂等，代价极低）。
  useEffect(() => {
    const unsubscribe = router.subscribe((state) => {
      if (!state.location.pathname.includes('/preview')) {
        void callBridge('media', 'close', {}).catch(() => {});
      }
    });
    return unsubscribe;
  }, []);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-canvas">
      <TitleBar />
      {/* 内容区：高度由 flex 分配，页面内部自己滚动 */}
      <div className="relative min-h-0 flex-1">
        <RouterProvider router={router} />
      </div>
      <ToastHost />
    </div>
  );
}

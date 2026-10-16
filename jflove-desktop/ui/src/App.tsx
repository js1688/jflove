import { useEffect, useState } from 'react';
import { RouterProvider } from 'react-router';
import { router } from './config/routes';
import { useThemeStore, type ThemeMode } from './stores/theme-store';
import { ToastHost, toast } from './components/ui';
import { TitleBar } from './components/desktop/TitleBar';
// ── 编辑器两处**模块级常驻**接线（v1.6.1）──
// ① 快捷键运行时：AC-1 要求"已登录时在**任意路由页**按新建键都能进工作区"，
//    所以监听器不能挂在 /editor 页面组件里，必须由根组件静态 import（设计 §4.7.1 / U-8）。
import './utils/scratch/shortcut-runtime';
// ② 外部唤起（双击关联文件 / 命令行传路径）→ 登录门 → 打开并切到工作区（设计 §4.4 / AC-64）。
//    静态 import = 模块顶层装上消费者，早于首帧，避免"启动期事件早于 UI"丢失（§7.5）。
import './utils/scratch/editor-open-paths';
import { QuitConfirmDialog } from './components/scratch/QuitConfirmDialog';
import { getScratchEditorSession } from './components/scratch/ScratchEditorHost';
import { useScratchStore, selectDirtyCount } from './stores/scratch-store';
import { scratchService } from './services/scratch-service';
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
  const dirtyCount = useScratchStore(selectDirtyCount);
  // 退出握手：Python 侧（托盘 / 关窗）发 `shell.quitRequested` 后由这里弹确认框（§6.4）
  const [quitRequested, setQuitRequested] = useState(false);
  const [quitBusy, setQuitBusy] = useState(false);

  useEffect(() => {
    // 返回取消订阅函数，StrictMode 下重复挂载也安全
    return watchSystem();
  }, [watchSystem]);

  // 说明：草稿**还原**已不在根组件做（v1.6.1 §4.5）：编辑器必须登录后才能用（AC-63），
  // 而在根组件还原会在未登录时就把标签登记进内存。还原时机与提示统一由
  // `components/scratch/ScratchWorkspace.tsx` 负责（条件：已登录 且 内存无标签 且 本会话未还原过）。

  // 脏标签数变化 → 告知 Python（AC-56 前置：退出握手要知道"有多少内容待保存"）
  useEffect(() => {
    void scratchService.reportDirty(dirtyCount).catch(() => {
      // 无桥时忽略（浏览器 dev）
    });
  }, [dirtyCount]);

  // 退出请求（托盘「退出」/ 关窗）→ 弹确认框
  useEffect(() => {
    return scratchService.onShellQuitRequested(() => setQuitRequested(true));
  }, []);

  /** 把"有保存路径"的脏标签写回原文件；草稿形态的标签永不静默写盘（D17 红线）。
   *
   * `readOnly` 必须排除（BL-26 / M-11）：只读标签（如 > 5 MB 降级只读）的改动不得被退出
   * 流程写回磁盘。⚠ 本筛选与 `savableCount`（`App.tsx:167`）是**同一判据的两处**，后者 2026-10-15 才补。
   */
  const saveFileBackedTabs = async (): Promise<number> => {
    const targets = useScratchStore
      .getState()
      .tabs.filter((tab) => tab.dirty && tab.path && !tab.readOnly);
    let saved = 0;
    for (const tab of targets) {
      const session = getScratchEditorSession(tab.id);
      if (!session || !tab.path) continue; // 未挂载标签没有实时正文，留给草稿库
      await scratchService.writeText({
        path: tab.path,
        text: session.getText(),
        encoding: tab.encoding,
        lineEnding: tab.lineEnding,
      });
      useScratchStore.getState().markDirty(tab.id, false);
      saved += 1;
    }
    return saved;
  };

  const handleSaveAndQuit = async (retain: boolean) => {
    setQuitBusy(true);
    try {
      await saveFileBackedTabs();
      // 草稿库补一次"落定"写入（含未挂载标签）—— 之后才允许 Python 放行退出
      await useScratchStore.getState().flushPendingSaves();
      await scratchService.confirmQuit(retain);
    } catch (error) {
      // 保存失败绝不退出：内容留在编辑器与草稿库里，把选择权还给用户
      toast.error('保存失败，已取消退出', error instanceof Error ? error.message : undefined);
      setQuitBusy(false);
      setQuitRequested(false);
    }
  };

  const handleDiscardAndQuit = async (retain: boolean) => {
    setQuitBusy(true);
    try {
      await scratchService.confirmQuit(retain);
    } catch (error) {
      toast.error('退出失败', error instanceof Error ? error.message : undefined);
      setQuitBusy(false);
      setQuitRequested(false);
    }
  };

  const handleCancelQuit = () => {
    setQuitRequested(false);
    void scratchService.cancelQuit().catch(() => {
      // 无桥时忽略
    });
  };

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
      {/* 内容区：高度由 flex 分配，页面内部自己滚动。
          编辑器工作区**就是其中一个路由页**（`/editor`，设计 DS-17）—— 不再是浮在路由之上的浮层。 */}
      <div className="relative min-h-0 flex-1">
        <RouterProvider router={router} />
      </div>
      <ToastHost />
      {/* 退出确认（§6.4）：由 `shell.quitRequested` 驱动，回执 `editor.confirmQuit(retain)` */}
      <QuitConfirmDialog
        open={quitRequested}
        dirtyCount={dirtyCount}
        savableCount={useScratchStore.getState().tabs.filter((tab) => tab.dirty && tab.path && !tab.readOnly).length}
        busy={quitBusy}
        onSaveAndQuit={(retain) => void handleSaveAndQuit(retain)}
        onDiscardAndQuit={(retain) => void handleDiscardAndQuit(retain)}
        onCancel={handleCancelQuit}
      />
    </div>
  );
}

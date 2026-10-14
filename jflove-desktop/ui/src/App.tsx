import { useEffect, useState } from 'react';
import { RouterProvider } from 'react-router';
import { router } from './config/routes';
import { useThemeStore, type ThemeMode } from './stores/theme-store';
import { ToastHost, toast } from './components/ui';
import { TitleBar } from './components/desktop/TitleBar';
// 浮动窗格层：与 TitleBar 同级、在 RouterProvider **之外** ⇒ 任意路由页都能呼出窗格，
// 且切换路由不会销毁窗格（设计 v1.6.0 §4.1.1 / AC-47）。
import { ScratchPaneLayer } from './components/scratch';
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

  // 启动还原（D7 / D9 / §6.3.2 / AC-55 / AC-60）：
  // 索引里**有条目**就还原 —— 异常退出（clean_exit=false）由崩溃检测触发，
  // 正常退出时条目仍在则说明用户在退出确认里选了"保留下次恢复"（选不保留会被清空 ⇒ 无条目）。
  // 还原阶段只登记状态、不建 CM6 实例（§4.1.3 懒挂载），所以几十份草稿也不会拖慢启动。
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const { cleanExit, entries } = await scratchService.draftList();
        if (!alive || entries.length === 0) return;
        useScratchStore.getState().hydrateFromDrafts(entries);
        toast.info(
          `已还原 ${entries.length} 个编辑窗格`,
          cleanExit ? '上次退出时选择了保留' : '上次未正常退出，草稿已自动还原',
        );
      } catch {
        // 浏览器 `npm run dev` 无桥 / 索引不可读：静默降级，不阻塞启动（AC-S2 ①）
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 脏窗格数变化 → 告知 Python（AC-56 前置：退出握手要知道"有多少内容待保存"）
  useEffect(() => {
    void scratchService.reportDirty(dirtyCount).catch(() => {
      // 无桥时忽略（浏览器 dev）
    });
  }, [dirtyCount]);

  // 退出请求（托盘「退出」/ 关窗）→ 弹确认框
  useEffect(() => {
    return scratchService.onShellQuitRequested(() => setQuitRequested(true));
  }, []);

  /** 把"有保存路径"的脏窗格写回原文件；草稿形态的窗格永不静默写盘（D17 红线） */
  const saveFileBackedPanes = async (): Promise<number> => {
    const targets = useScratchStore
      .getState()
      .panes.filter((pane) => pane.dirty && pane.path);
    let saved = 0;
    for (const pane of targets) {
      const session = getScratchEditorSession(pane.id);
      if (!session || !pane.path) continue; // 未挂载窗格没有实时正文，留给草稿库
      await scratchService.writeText({
        path: pane.path,
        text: session.getText(),
        encoding: pane.encoding,
        lineEnding: pane.lineEnding,
      });
      useScratchStore.getState().markDirty(pane.id, false);
      saved += 1;
    }
    return saved;
  };

  const handleSaveAndQuit = async (retain: boolean) => {
    setQuitBusy(true);
    try {
      await saveFileBackedPanes();
      // 草稿库补一次"落定"写入（含未挂载窗格）—— 之后才允许 Python 放行退出
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
      {/* 浮动窗格层（绝对定位，不占 flex 流；router 之外 ⇒ 换页不销毁窗格） */}
      <ScratchPaneLayer />
      {/* 内容区：高度由 flex 分配，页面内部自己滚动 */}
      <div className="relative min-h-0 flex-1">
        <RouterProvider router={router} />
      </div>
      <ToastHost />
      {/* 退出确认（§6.4）：由 `shell.quitRequested` 驱动，回执 `editor.confirmQuit(retain)` */}
      <QuitConfirmDialog
        open={quitRequested}
        dirtyCount={dirtyCount}
        savableCount={
          useScratchStore.getState().panes.filter((pane) => pane.dirty && pane.path).length
        }
        busy={quitBusy}
        onSaveAndQuit={(retain) => void handleSaveAndQuit(retain)}
        onDiscardAndQuit={(retain) => void handleDiscardAndQuit(retain)}
        onCancel={handleCancelQuit}
      />
    </div>
  );
}

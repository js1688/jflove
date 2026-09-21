/**
 * 响应式断点（桌面端）
 *
 * 与 Web 端 `jflove-web/src/hooks/use-responsive.ts` 的差异：
 * **桌面端窗口恒为 PC 布局**，不存在窄屏分支（那套是给浏览器准备的）。
 * 因此这里不再监听 `matchMedia`，直接返回恒定值。
 *
 * 保留同名的 `useIsPC` / `useBreakpoint` 导出，是为了让从 Web 端复制的页面
 * （如 `SettingsPage`）可以逐字使用，不必改动其 `isPC` 分支代码。
 */

/** 桌面端恒为 PC */
export function useIsPC(): boolean {
  return true;
}

export type Breakpoint = 'mobile' | 'pc';

/** 桌面端恒为 pc */
export function useBreakpoint(): Breakpoint {
  return 'pc';
}

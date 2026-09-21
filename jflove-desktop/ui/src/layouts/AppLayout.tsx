import { DesktopLayout } from './DesktopLayout';

/**
 * 应用布局
 *
 * 与 Web 端 `AppLayout` 的差异：**桌面端窗口恒为 PC 布局**，
 * 因此不引入 `use-responsive` 与 `MobileLayout`（那两个是为浏览器窄屏准备的）。
 * 其余结构与 Web 端一致。
 */
export function AppLayout() {
  return <DesktopLayout />;
}

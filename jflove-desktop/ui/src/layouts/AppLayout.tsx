import { DesktopLayout } from './DesktopLayout';
import { useRemoteSessionSync } from '../utils/remote-session-sync';

/**
 * 应用布局
 *
 * 与 Web 端 `AppLayout` 的差异：**桌面端窗口恒为 PC 布局**，
 * 因此不引入 `use-responsive` 与 `MobileLayout`（那两个是为浏览器窄屏准备的）。
 * 其余结构与 Web 端一致。
 *
 * `useRemoteSessionSync()` 必须挂在登录后的常驻布局：会话事件不能只在
 * 「远程连接」页面被订阅，否则用户切到其它页面后会话结束不会关标签（v1.10.10）。
 */
export function AppLayout() {
  useRemoteSessionSync();
  return <DesktopLayout />;
}

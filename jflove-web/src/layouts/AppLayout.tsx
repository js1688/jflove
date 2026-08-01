import { useIsPC } from '../hooks/use-responsive';
import { DesktopLayout } from './DesktopLayout';
import { MobileLayout } from './MobileLayout';

/** 根据视口宽度自动切换 PC/移动端布局 */
export function AppLayout() {
  const isPC = useIsPC();
  return isPC ? <DesktopLayout /> : <MobileLayout />;
}

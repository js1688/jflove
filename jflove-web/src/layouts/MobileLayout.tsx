/**
 * 移动端布局：主内容 + 底部导航
 *
 * v1.5.0 改版要点：
 *   - **底部导航 6 项收敛为 5 项**（文件/笔记/同步/传输/设置）；
 *     「修复中心」不再是导航项，其功能并入传输页（需求 §2.5 / AC-18），
 *     `/repair` 路由保留以兼容深链与 Web 端联动；
 *   - 图标矢量化（原先 6 个 emoji）+ 选中指示器；
 *   - 高度改用令牌 `--tabbar-h`，并补齐**安全区适配**
 *     （原先 `safe-area-bottom` 类名在全项目无任何定义，是空效果）；
 *   - 点按区域 ≥ 44px（AC-19）。
 */
import { Outlet, useNavigate, useLocation } from 'react-router';
import { Icon, type IconName } from '../components/ui';

interface TabEntry {
  path: string;
  label: string;
  icon: IconName;
  /** 需要前缀匹配的 tab（进入子页面时仍高亮父 tab） */
  prefix?: boolean;
}

const TABS: TabEntry[] = [
  { path: '/files', label: '文件', icon: 'files', prefix: true },
  { path: '/notes', label: '笔记', icon: 'notes', prefix: true },
  { path: '/sync', label: '同步', icon: 'sync' },
  { path: '/transfer', label: '传输', icon: 'transfer', prefix: true },
  { path: '/settings', label: '设置', icon: 'settings' },
];

export function MobileLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  const isActive = (tab: TabEntry) =>
    tab.prefix ? location.pathname.startsWith(tab.path) : location.pathname === tab.path;

  // 管理子页面 / 磁盘浏览 / 预览 / 笔记编辑（push 子页面）不显示底部导航，
  // 对齐移动端 Flutter：push 进入的子页面无 TabBar，仅靠顶部返回按钮。
  // 注意 `/transfer` 带前缀匹配：修复中心并入传输页后，`/repair` 也归到该 tab，
  // 但 `/repair` 本身是主页级页面，仍显示底部导航。
  const hideTabBar =
    location.pathname.startsWith('/admin') || location.pathname.startsWith('/files/') || location.pathname.startsWith('/notes/');

  return (
    <div className="flex h-screen flex-col bg-canvas">
      <main className="min-w-0 flex-1 overflow-auto">
        <Outlet />
      </main>

      {!hideTabBar && (
        <nav className="tabbar safe-area-bottom" aria-label="主导航">
          {TABS.map((tab) => {
            const active = isActive(tab);
            return (
              <button
                key={tab.path}
                type="button"
                onClick={() => navigate(tab.path)}
                data-active={active}
                aria-current={active ? 'page' : undefined}
                className="tabbar-item"
              >
                <Icon name={tab.icon} size="lg" strokeWidth={active ? 2.1 : 1.75} />
                <span className="truncate">{tab.label}</span>
              </button>
            );
          })}
        </nav>
      )}
    </div>
  );
}

/**
 * PC 端布局：侧边栏（可折叠为 rail）+ 主内容区
 *
 * v1.5.0 改版要点（设计文档 §3.1.5）：
 *   - 图标全部矢量化（原先全部是 emoji 字面量，需求 AC-5）；
 *   - 侧栏分区：主导航 / 管理 / 底部固定，激活项带渐变指示条；
 *   - 折叠态改为真正的 rail（68px，用 --sidebar-w-rail），折叠后仅显示图标 + tooltip；
 *   - 底部增加存储占用卡与用户卡。
 *
 * 修复的既有缺陷：原 `isActive('/admin')` 分支永远不会命中（adminItems 的 path
 * 都是 `/admin/xxx`，走的是精确匹配分支），现改为统一的前缀匹配实现。
 */
import { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { APP_NAME } from '../config/constants';
import { Icon, IconButton, type IconName } from '../components/ui';

interface NavEntry {
  path: string;
  label: string;
  icon: IconName;
  /** 精确匹配（用于 /files、/notes 这类需要前缀匹配的例外） */
  prefix?: boolean;
}

/** 主导航 */
const NAV_ITEMS: NavEntry[] = [
  { path: '/files', label: '文件管理', icon: 'files', prefix: true },
  { path: '/notes', label: '笔记管理', icon: 'notes', prefix: true },
  { path: '/sync', label: '同步管理', icon: 'sync' },
  // v1.5.0 修复：底部导航 6→5 后「修复中心」并入传输页的第二个页签，
  // 这里不能继续保留独立导航项 —— 否则同一个功能在侧栏和页签里各有一个入口，
  // 用户会以为重复。`/repair` 路由仍然保留（深链与「查看修复进度」跳转）。
  { path: '/transfer', label: '传输任务', icon: 'transfer' },
];

/** 底部固定项（对标桌面端 NavigationItemPosition.BOTTOM） */
const BOTTOM_ITEMS: NavEntry[] = [
  { path: '/security', label: '安全状态', icon: 'security' },
  { path: '/settings', label: '设置', icon: 'settings' },
];

/** 管理面板（仅 admin） */
const ADMIN_ITEMS: NavEntry[] = [
  { path: '/admin/users', label: '用户管理', icon: 'users' },
  { path: '/admin/disks', label: '磁盘管理', icon: 'disks' },
  { path: '/admin/permissions', label: '权限配置', icon: 'permissions' },
  { path: '/admin/system', label: '系统设置', icon: 'system' },
];

/** 统一激活判定：前缀匹配或精确匹配 */
function useIsActive() {
  const location = useLocation();
  return (item: NavEntry) =>
    item.prefix ? location.pathname.startsWith(item.path) : location.pathname === item.path;
}

export function DesktopLayout() {
  const navigate = useNavigate();
  const { username, role, isAdmin } = useAuthStore();
  const [collapsed, setCollapsed] = useState(false);
  const isActive = useIsActive();

  const renderItem = (item: NavEntry) => {
    const active = isActive(item);
    return (
      <button
        key={item.path}
        type="button"
        onClick={() => navigate(item.path)}
        data-active={active}
        title={collapsed ? item.label : undefined}
        aria-current={active ? 'page' : undefined}
        className={['nav-item', collapsed && 'nav-item-rail'].filter(Boolean).join(' ')}
      >
        <Icon name={item.icon} />
        {!collapsed && <span className="grow truncate text-left">{item.label}</span>}
      </button>
    );
  };

  return (
    <div className="flex h-screen bg-canvas">
      {/* ---------------- 侧边栏 ---------------- */}
      <aside
        className="relative flex shrink-0 flex-col border-r border-line-subtle bg-surface transition-[width] duration-[280ms] ease-[var(--ease-out-quint)]"
        style={{ width: collapsed ? 'var(--sidebar-w-rail)' : 'var(--sidebar-w)' }}
      >
        {/* 顶部品牌区 */}
        <div className="relative z-[1] flex h-[var(--header-h)] shrink-0 items-center gap-2.5 px-3">
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            className="flex min-w-0 items-center gap-2.5"
            title={collapsed ? '展开侧栏' : '折叠侧栏'}
            aria-label={collapsed ? '展开侧栏' : '折叠侧栏'}
          >
            <span className="logo-mark">
              <Icon name="shield" size="sm" strokeWidth={2.1} />
            </span>
            {!collapsed && (
              <span className="min-w-0">
                <span className="grad-text block text-[15px] leading-tight font-semibold tracking-[-0.015em]">
                  {APP_NAME}
                </span>
                <span className="block text-[10.5px] tracking-[0.02em] text-subtle">
                  私有云 · 端到端加密
                </span>
              </span>
            )}
          </button>
        </div>

        {/* 导航 */}
        <nav className="relative z-[1] flex-1 overflow-y-auto px-3 pt-2 pb-4">
          <div className="flex flex-col gap-0.5">{NAV_ITEMS.map(renderItem)}</div>

          {isAdmin && (
            <>
              <div className="mx-3 my-4 border-t border-line-subtle" />
              {!collapsed && (
                <div className="px-3 pb-2 text-[11px] font-semibold tracking-[0.08em] text-subtle uppercase">
                  管理
                </div>
              )}
              <div className="flex flex-col gap-0.5">{ADMIN_ITEMS.map(renderItem)}</div>
            </>
          )}
        </nav>

        {/* 底部：用户卡（v1.5.0 修复：删掉原先硬编码假数据的「存储空间」卡 —— 
            磁盘余量应展示真实数据，且归属「磁盘管理」页，不该在导航栏里写死 73.4/128 GB） */}
        <div className="relative z-[1] shrink-0 border-t border-line-subtle px-3 pt-3 pb-4">
          <div className="flex flex-col gap-0.5 pb-1">{BOTTOM_ITEMS.map(renderItem)}</div>

          {username && (
            <div
              className={[
                'mt-1 flex items-center gap-2.5 rounded-lg p-2 transition-colors hover:bg-hover',
                collapsed && 'justify-center',
              ]
                .filter(Boolean)
                .join(' ')}
              title={collapsed ? `${username}（${role === 'admin' ? '管理员' : '用户'}）` : undefined}
            >
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-500 text-[12.5px] font-semibold text-white shadow-[0_0_0_2px_var(--bg-surface),0_0_0_3.5px_var(--brand-200)]">
                {username.slice(0, 2).toUpperCase()}
              </span>
              {!collapsed && (
                <span className="min-w-0">
                  <span className="block truncate text-[12.5px] font-semibold">{username}</span>
                  <span className="block text-[11px] text-subtle">
                    {role === 'admin' ? '管理员' : '普通用户'}
                  </span>
                </span>
              )}
            </div>
          )}
        </div>
      </aside>

      {/* ---------------- 主内容区 ---------------- */}
      <main className="min-w-0 flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}

/** 供移动端布局复用的图标按钮（避免重复 import） */
export { IconButton };

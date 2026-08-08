import { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { APP_NAME } from '../config/constants';

/** PC 端布局：侧边栏 + 主内容区 */
export function DesktopLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { username, role, isAdmin } = useAuthStore();
  const [collapsed, setCollapsed] = useState(false);

  const navItems: { path: string; label: string; icon: string }[] = [
    { path: '/files', label: '文件管理', icon: '📁' },
    { path: '/notes', label: '笔记管理', icon: '📝' },
    { path: '/sync', label: '同步管理', icon: '🔄' },
    { path: '/transfer', label: '传输任务', icon: '📊' },
  ];

  // 底部固定项（对标桌面端 NavigationItemPosition.BOTTOM）：安全状态 + 设置
  const bottomItems = [
    { path: '/security', label: '安全状态', icon: '🔒' },
    { path: '/settings', label: '设置', icon: '⚙️' },
  ];

  const adminItems = [
    { path: '/admin/users', label: '用户管理', icon: '👤' },
    { path: '/admin/disks', label: '磁盘管理', icon: '💾' },
    { path: '/admin/permissions', label: '权限配置', icon: '🔑' },
    { path: '/admin/system', label: '系统设置', icon: '⚙️' },
  ];

  const isActive = (path: string) => {
    if (path === '/files') return location.pathname.startsWith('/files');
    if (path === '/notes') return location.pathname.startsWith('/notes');
    if (path === '/admin') return location.pathname.startsWith('/admin');
    return location.pathname === path;
  };

  const w = collapsed ? 'w-16' : 'w-60';

  return (
    <div className="flex h-screen bg-gray-50">
      {/* 侧边栏 */}
      <aside className={`${w} flex flex-col bg-white border-r border-gray-200 transition-all duration-200`}>
        {/* Logo */}
        <div className="flex items-center h-14 px-3 border-b border-gray-100">
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="flex items-center gap-2 w-full"
          >
            <span className="text-xl">🔐</span>
            {!collapsed && (
              <span className="font-semibold text-gray-800 truncate">{APP_NAME}</span>
            )}
          </button>
        </div>

        {/* 导航项 */}
        <nav className="flex-1 py-2 overflow-y-auto">
          {navItems.map(item => (
            <button
              key={item.path}
              onClick={() => navigate(item.path)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 text-sm transition-colors
                ${isActive(item.path)
                  ? 'bg-indigo-50 text-indigo-700 font-medium'
                  : 'text-gray-600 hover:bg-gray-50'
                }`}
            >
              <span className="text-lg w-6 text-center">{item.icon}</span>
              {!collapsed && <span className="truncate">{item.label}</span>}
            </button>
          ))}

          {/* 管理面板（仅 admin） */}
          {isAdmin && (
            <>
              <div className="mx-3 my-2 border-t border-gray-100" />
              <div className="px-3 py-1 text-xs text-gray-400 uppercase tracking-wider">
                {!collapsed && '管理'}
              </div>
              {adminItems.map(item => (
                <button
                  key={item.path}
                  onClick={() => navigate(item.path)}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 text-sm transition-colors
                    ${isActive(item.path)
                      ? 'bg-indigo-50 text-indigo-700 font-medium'
                      : 'text-gray-600 hover:bg-gray-50'
                    }`}
                >
                  <span className="text-lg w-6 text-center">{item.icon}</span>
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </button>
              ))}
            </>
          )}
        </nav>

        {/* 底部：安全状态 + 设置（固定项，对标桌面端） */}
        <div className="border-t border-gray-100">
          {bottomItems.map(item => (
            <button
              key={item.path}
              onClick={() => navigate(item.path)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 text-sm transition-colors
                ${isActive(item.path)
                  ? 'bg-indigo-50 text-indigo-700 font-medium'
                  : 'text-gray-600 hover:bg-gray-50'
                }`}
            >
              <span className="text-lg w-6 text-center">{item.icon}</span>
              {!collapsed && <span>{item.label}</span>}
            </button>
          ))}
          {!collapsed && username && (
            <div className="px-3 py-2 text-xs text-gray-400">
              {username}
              <span className="ml-1 text-indigo-500">{role === 'admin' ? '(管理员)' : ''}</span>
            </div>
          )}
        </div>
      </aside>

      {/* 主内容区 */}
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}

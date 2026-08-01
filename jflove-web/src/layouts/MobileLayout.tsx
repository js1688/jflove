import { Outlet, useNavigate, useLocation } from 'react-router';

/** 移动端布局：主内容 + 底部 TabBar */
export function MobileLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  const tabs = [
    { path: '/files', label: '文件', icon: '📁' },
    { path: '/notes', label: '笔记', icon: '📝' },
    { path: '/sync', label: '同步', icon: '🔄' },
    { path: '/transfer', label: '传输', icon: '📊' },
    { path: '/settings', label: '设置', icon: '⚙️' },
  ];

  const isActive = (path: string) => {
    if (path === '/files') return location.pathname.startsWith('/files');
    if (path === '/notes') return location.pathname.startsWith('/notes');
    return location.pathname === path || (path === '/settings' && location.pathname.startsWith('/settings'));
  };

  // 管理子页面不显示底部 TabBar
  const hideTabBar = location.pathname.startsWith('/admin') ||
    location.pathname.includes('/preview');

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* 主内容区 */}
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>

      {/* 底部 TabBar */}
      {!hideTabBar && (
        <nav className="flex items-center justify-around h-14 bg-white border-t border-gray-200 safe-area-bottom">
          {tabs.map(tab => (
            <button
              key={tab.path}
              onClick={() => navigate(tab.path)}
              className={`flex flex-col items-center justify-center gap-0.5 min-w-0 flex-1 h-full text-xs transition-colors
                ${isActive(tab.path)
                  ? 'text-indigo-600'
                  : 'text-gray-400'
                }`}
            >
              <span className="text-lg">{tab.icon}</span>
              <span className="truncate">{tab.label}</span>
            </button>
          ))}
        </nav>
      )}
    </div>
  );
}

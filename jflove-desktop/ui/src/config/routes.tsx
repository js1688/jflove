import { createHashRouter, Navigate, Outlet } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { AppLayout } from '../layouts/AppLayout';
import { AuthLayout } from '../layouts/AuthLayout';
import { DiskBrowserPage } from '../pages/DiskBrowserPage';
import { FileListPage } from '../pages/FileListPage';
import { FilePreviewPage } from '../pages/FilePreviewPage';
import { RouteErrorPage } from '../pages/RouteErrorPage';
import { LoginPage } from '../pages/LoginPage';
import { NoteEditPage } from '../pages/NoteEditPage';
import { NoteListPage } from '../pages/NoteListPage';
import { SecurityPage } from '../pages/SecurityPage';
import { SyncPage } from '../pages/SyncPage';
import { TransferTabsPage } from '../pages/TransferTabsPage';
import { SettingsPage } from '../pages/SettingsPage';
import { AdminSystemPage } from '../pages/admin/AdminSystemPage';
import { AdminUsersPage } from '../pages/admin/AdminUsersPage';
import { AdminDisksPage } from '../pages/admin/AdminDisksPage';
import { AdminPermissionsPage } from '../pages/admin/AdminPermissionsPage';
import { PlaceholderPage } from '../pages/PlaceholderPage';
import {
  ADMIN_PLACEHOLDERS,
  BOTTOM_PLACEHOLDERS,
  MAIN_PLACEHOLDERS,
  type PlaceholderEntry,
} from '../config/placeholders';

/**
 * 路由表（桌面端）
 *
 * 与 Web 端 `jflove-web/src/config/routes.tsx` 的两处必要差异：
 *
 * 1. **用 `createHashRouter` 而不是 `createBrowserRouter`**：
 *    桌面端页面由 QtWebEngine 以 `file://` 加载，没有 HTTP 服务端做 SPA fallback，
 *    History API 的路径会被当成真实文件路径而失配；hash 路由天然可用。
 *
 * 2. **只接线已交付的页面**：各业务页面按菜单逐个交付（N7 ~ N16），
 *    未交付的落到带节点编号的占位页，避免"看起来能用其实没接"。
 */

function RequireAuth({ children }: { children: React.ReactNode }) {
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn);
  const isTokenExpired = useAuthStore((s) => s.isTokenExpired);

  if (!isLoggedIn || isTokenExpired()) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function RequireAdmin({ children }: { children: React.ReactNode }) {
  const isAdmin = useAuthStore((s) => s.isAdmin);
  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}

/** 生成一个占位路由 */
function placeholderFor(entry: PlaceholderEntry, path: string) {
  return {
    path,
    element: (
      <PlaceholderPage
        title={entry.label}
        node={entry.node}
        icon={entry.icon}
        adminOnly={Boolean(entry.adminOnly)}
      />
    ),
  };
}

/** 主导航 + 底部固定项的占位路由 */
function placeholderRoutes() {
  return [...MAIN_PLACEHOLDERS, ...BOTTOM_PLACEHOLDERS]
    .filter((item) => !item.implemented)
    .map((item) => placeholderFor(item, item.path.replace(/^\//, '')));
}

/**
 * 管理面板的占位路由。
 *
 * **必须挂在 `/admin` 的 children 下**（而不是顶层），这样它们继承
 * `RequireAdmin` 守卫。此前放在顶层时，非管理员只要手改 hash 就能进管理页 ——
 * 虽然侧栏不显示入口，但路由本身没被拦住（AGENTS.md §9 要求路由级鉴权）。
 */
function adminPlaceholderRoutes() {
  return ADMIN_PLACEHOLDERS.filter((item) => !item.implemented).map((item) =>
    placeholderFor(item, item.path.replace(/^\/admin\//, '')),
  );
}

export const router = createHashRouter([
  {
    path: '/login',
    element: <AuthLayout />,
    // 兜底：任何路由级错误都显示友好页面，**不要** react-router 的开发错误页
    // （历史上点"不能在线预览"的文件会跳到这里，看到 "Unexpected Application Error!"）
    errorElement: <RouteErrorPage />,
    children: [{ index: true, element: <LoginPage /> }],
  },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    errorElement: <RouteErrorPage />,
    children: [
      // 登录后默认进入文件管理（与 Web 端/移动端首屏一致）
      { index: true, element: <Navigate to="/files" replace /> },

      // ── 已交付的真实页面 ──
      { path: 'files', element: <FileListPage /> },
      { path: 'files/:diskId', element: <DiskBrowserPage /> },
      { path: 'files/:diskId/preview', element: <FilePreviewPage /> },
      { path: 'notes', element: <NoteListPage /> },
      { path: 'sync', element: <SyncPage /> },
      // 传输任务与修复中心同一页的两个标签（/repair 保留以兼容深链）
      { path: 'transfer', element: <TransferTabsPage /> },
      { path: 'repair', element: <TransferTabsPage /> },
      { path: 'notes/:noteId', element: <NoteEditPage /> },
      { path: 'security', element: <SecurityPage /> },
      { path: 'settings', element: <SettingsPage /> },

      // ── 管理面板（仅 admin，复用外层 AppLayout 的侧栏） ──
      {
        path: 'admin',
        element: (
          <RequireAdmin>
            <Outlet />
          </RequireAdmin>
        ),
        children: [
          { path: 'users', element: <AdminUsersPage /> },
          { path: 'disks', element: <AdminDisksPage /> },
          { path: 'permissions', element: <AdminPermissionsPage /> },
          { path: 'system', element: <AdminSystemPage /> },
          ...adminPlaceholderRoutes(),
        ],
      },

      // ── 尚未交付的菜单（占位页，标注归属节点） ──
      ...placeholderRoutes(),
    ],
  },
]);

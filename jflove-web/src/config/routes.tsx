import { createBrowserRouter, Navigate, Outlet, redirect } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { AppLayout } from '../layouts/AppLayout';
import { AuthLayout } from '../layouts/AuthLayout';
import { LoginPage } from '../pages/LoginPage';
import { FileListPage } from '../pages/FileListPage';
import { DiskBrowserPage } from '../pages/DiskBrowserPage';
import { FilePreviewPage } from '../pages/FilePreviewPage';
import { NoteListPage } from '../pages/NoteListPage';
import { NoteEditPage } from '../pages/NoteEditPage';
import { SyncPage } from '../pages/SyncPage';
import { TransferTabsPage } from '../pages/TransferTabsPage';
import { SecurityPage } from '../pages/SecurityPage';
import { SettingsPage } from '../pages/SettingsPage';
import { AdminUsersPage } from '../pages/admin/AdminUsersPage';
import { AdminDisksPage } from '../pages/admin/AdminDisksPage';
import { AdminPermissionsPage } from '../pages/admin/AdminPermissionsPage';
import { AdminSystemPage } from '../pages/admin/AdminSystemPage';

/**
 * 路由守卫包装器。
 * 在路由层做鉴权检查：未登录或 token 已过期时重定向到 /login。
 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const isLoggedIn = useAuthStore(s => s.isLoggedIn);
  const isTokenExpired = useAuthStore(s => s.isTokenExpired);

  if (!isLoggedIn || isTokenExpired()) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function RequireAdmin({ children }: { children: React.ReactNode }) {
  const isAdmin = useAuthStore(s => s.isAdmin);
  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}

export const router = createBrowserRouter([
  {
    path: '/login',
    element: <AuthLayout />,
    children: [
      { index: true, element: <LoginPage /> },
    ],
  },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      // 默认进入文件管理（不做首页），对齐桌面端/移动端首屏即文档管理。
      // v1.5.0 反馈修复：改用 loader 重定向（`redirect` 在**渲染前**生效），
      // 原先是 `<Navigate>` 在 effect 里跳转，会先渲染一帧「只有侧边栏、
      // 主内容区空白」再跳走（实测 128.9ms），即用户看到的「闪动」。
      { index: true, loader: () => redirect('/files') },
      { path: 'files', element: <FileListPage /> },
      { path: 'files/:diskId', element: <DiskBrowserPage /> },
      { path: 'files/:diskId/preview', element: <FilePreviewPage /> },
      { path: 'notes', element: <NoteListPage /> },
      { path: 'notes/:noteId', element: <NoteEditPage /> },
      { path: 'sync', element: <SyncPage /> },
      // v1.5.0：传输与修复合并到同一页（底部导航 6→5 项，修复中心并入传输页）；
      // `/repair` 保留以兼容深链与「查看修复进度」跳转，落在同一页的「修复」标签。
      { path: 'transfer', element: <TransferTabsPage /> },
      { path: 'repair', element: <TransferTabsPage /> },
      { path: 'security', element: <SecurityPage /> },
      { path: 'settings', element: <SettingsPage /> },
      {
        // 管理面板子路由：仅做角色守卫，渲染外层 AppLayout 的 Outlet，
        // 不再嵌套第二层 AppLayout（否则会重复渲染侧边栏菜单，造成菜单嵌套）
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
        ],
      },
    ],
  },
]);

import { createBrowserRouter, Navigate, Outlet } from 'react-router';
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
import { TransferPage } from '../pages/TransferPage';
import { RepairCenterPage } from '../pages/RepairCenterPage';
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
      // 默认进入文件管理（不做首页），对齐桌面端/移动端首屏即文档管理
      { index: true, element: <Navigate to="/files" replace /> },
      { path: 'files', element: <FileListPage /> },
      { path: 'files/:diskId', element: <DiskBrowserPage /> },
      { path: 'files/:diskId/preview', element: <FilePreviewPage /> },
      { path: 'notes', element: <NoteListPage /> },
      { path: 'notes/:noteId', element: <NoteEditPage /> },
      { path: 'sync', element: <SyncPage /> },
      { path: 'transfer', element: <TransferPage /> },
      // v1.4.2：修复中心（全平台共享任务列表）
      { path: 'repair', element: <RepairCenterPage /> },
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

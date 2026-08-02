import { createBrowserRouter, Navigate } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { AppLayout } from '../layouts/AppLayout';
import { AuthLayout } from '../layouts/AuthLayout';
import { LoginPage } from '../pages/LoginPage';
import { HomePage } from '../pages/HomePage';
import { FileListPage } from '../pages/FileListPage';
import { DiskBrowserPage } from '../pages/DiskBrowserPage';
import { FilePreviewPage } from '../pages/FilePreviewPage';
import { NoteListPage } from '../pages/NoteListPage';
import { NoteEditPage } from '../pages/NoteEditPage';
import { SyncPage } from '../pages/SyncPage';
import { TransferPage } from '../pages/TransferPage';
import { SecurityPage } from '../pages/SecurityPage';
import { SettingsPage } from '../pages/SettingsPage';
import { AdminUsersPage } from '../pages/admin/AdminUsersPage';
import { AdminDisksPage } from '../pages/admin/AdminDisksPage';
import { AdminPermissionsPage } from '../pages/admin/AdminPermissionsPage';

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
      { index: true, element: <HomePage /> },
      { path: 'files', element: <FileListPage /> },
      { path: 'files/:diskId', element: <DiskBrowserPage /> },
      { path: 'files/:diskId/preview', element: <FilePreviewPage /> },
      { path: 'notes', element: <NoteListPage /> },
      { path: 'notes/:noteId', element: <NoteEditPage /> },
      { path: 'sync', element: <SyncPage /> },
      { path: 'transfer', element: <TransferPage /> },
      { path: 'security', element: <SecurityPage /> },
      { path: 'settings', element: <SettingsPage /> },
      {
        path: 'admin',
        element: <RequireAdmin><AppLayout /></RequireAdmin>,
        children: [
          { path: 'users', element: <AdminUsersPage /> },
          { path: 'disks', element: <AdminDisksPage /> },
          { path: 'permissions', element: <AdminPermissionsPage /> },
        ],
      },
    ],
  },
]);

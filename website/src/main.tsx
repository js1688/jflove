import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router';
import './index.css';
import { AppLayout } from './layouts/AppLayout';
import { HomePage } from './pages/HomePage';
import { FeaturesPage } from './pages/FeaturesPage';
import { DocsPage } from './pages/DocsPage';
import { ApiPage } from './pages/ApiPage';
import { ApiAnnouncementsPage } from './pages/ApiAnnouncementsPage';
import { DownloadPage } from './pages/DownloadPage';
import { GuidePage } from './pages/GuidePage';
import { SecurityPage } from './pages/SecurityPage';

/**
 * 路由表。所有页面共用 `AppLayout`（顶栏 + 页脚）。
 *
 * nginx 侧配了 SPA fallback（`try_files $uri $uri/ /index.html`），
 * 因此直接访问 `/guide/web` 这类深链也能正常打开。
 */
const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'features', element: <FeaturesPage /> },
      { path: 'docs', element: <DocsPage /> },
      { path: 'api', element: <ApiPage /> },
      { path: 'api-announcements', element: <ApiAnnouncementsPage /> },
      { path: 'download', element: <DownloadPage /> },
      // 下载与安装已合并（用户反馈）：/install 保留以兼容旧链接，直接落到 /download
      { path: 'install', element: <Navigate to="/download" replace /> },
      { path: 'guide/:platform', element: <GuidePage /> },
      { path: 'security', element: <SecurityPage /> },
    ],
  },
]);

const root = document.getElementById('root');
if (!root) throw new Error('缺少 #root 挂载点');

createRoot(root).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);

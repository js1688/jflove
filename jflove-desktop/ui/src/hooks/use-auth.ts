/**
 * 认证相关 Hook
 *
 * 只导出 `handleLogout`（唯一使用方：`pages/SettingsPage.tsx:46` 的「退出登录」）。
 * 原先还有一个 `handleLogin`（R3 死代码清理，2026-10-15）：登录流程由 `pages/LoginPage.tsx`
 * 自己的局部 `handleLogin` 直接调 `auth-store.login()` 完成，从不经过本 Hook
 * ⇒ 保留它等于留一条永不执行的跳转分支。登录语义未变，只是删掉了这条死路径。
 */

import { useCallback } from 'react';
import { useAuthStore } from '../stores/auth-store';
import { useNavigate } from 'react-router';

export function useAuth() {
  const store = useAuthStore();
  const navigate = useNavigate();

  const handleLogout = useCallback(async () => {
    await store.logout();
    navigate('/login', { replace: true });
  }, [store, navigate]);

  return {
    ...store,
    handleLogout,
  };
}

/**
 * 认证相关 Hook
 */

import { useCallback } from 'react';
import { useAuthStore } from '../stores/auth-store';
import { useNavigate } from 'react-router';

export function useAuth() {
  const store = useAuthStore();
  const navigate = useNavigate();

  const handleLogin = useCallback(async (
    username: string,
    password: string,
    localMaxSeconds?: number,
  ) => {
    await store.login(username, password, localMaxSeconds);
    navigate('/', { replace: true });
  }, [store, navigate]);

  const handleLogout = useCallback(async () => {
    await store.logout();
    navigate('/login', { replace: true });
  }, [store, navigate]);

  return {
    ...store,
    handleLogin,
    handleLogout,
  };
}

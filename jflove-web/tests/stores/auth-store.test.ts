/**
 * 认证状态管理测试
 *
 * 覆盖登录/登出/角色状态，为路由守卫（权限控制）提供支撑。
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { useAuthStore } from '../../src/stores/auth-store';
import {
  setToken, saveUserInfo, setTokenExpiresAt, clearSession,
} from '../../src/utils/session';

describe('auth-store 认证状态', () => {
  beforeEach(() => {
    localStorage.clear();
    clearSession();
    useAuthStore.setState({
      isLoggedIn: false,
      token: null,
      userId: null,
      username: null,
      role: null,
      serverUrl: 'http://localhost:8989',
      isAdmin: false,
      isEncrypted: false,
    });
  });

  afterEach(() => {
    clearSession();
  });

  it('从 localStorage 恢复登录态（免登录）', () => {
    setToken('test-token');
    saveUserInfo(1, 'alice', 'admin');
    setTokenExpiresAt(Date.now() / 1000 + 3600);

    useAuthStore.getState().initFromStorage();

    const s = useAuthStore.getState();
    expect(s.isLoggedIn).toBe(true);
    expect(s.username).toBe('alice');
    expect(s.role).toBe('admin');
    expect(s.isAdmin).toBe(true);
  });

  it('无持久化 token 时不恢复登录态', () => {
    useAuthStore.getState().initFromStorage();
    expect(useAuthStore.getState().isLoggedIn).toBe(false);
  });

  it('登出后清除登录状态', async () => {
    // 模拟登录态
    useAuthStore.setState({
      isLoggedIn: true,
      token: 't',
      userId: 1,
      username: 'alice',
      role: 'user',
      isAdmin: false,
    });
    setToken('t');
    saveUserInfo(1, 'alice', 'user');

    await useAuthStore.getState().logout();

    const s = useAuthStore.getState();
    expect(s.isLoggedIn).toBe(false);
    expect(s.username).toBeNull();
    expect(s.token).toBeNull();
  });

  it('token 过期后 isTokenExpired 返回 true 并登出', () => {
    setToken('expired-token');
    saveUserInfo(1, 'alice', 'user');
    // token 已过期
    setTokenExpiresAt(Date.now() / 1000 - 10);

    useAuthStore.getState().initFromStorage();

    const expired = useAuthStore.getState().isTokenExpired();
    expect(expired).toBe(true);
    expect(useAuthStore.getState().isLoggedIn).toBe(false);
  });

  it('token 未过期时 isTokenExpired 返回 false', () => {
    setToken('valid-token');
    saveUserInfo(1, 'alice', 'user');
    setTokenExpiresAt(Date.now() / 1000 + 3600);

    useAuthStore.getState().initFromStorage();

    expect(useAuthStore.getState().isTokenExpired()).toBe(false);
  });
});

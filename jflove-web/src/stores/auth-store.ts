/**
 * 认证状态管理
 *
 * 对标桌面端 session_manager + auth_service 的状态部分。
 */

import { create } from 'zustand';
import {
  getToken,
  getPersistedUsername, getPersistedRole, getPersistedUserId,
  getTokenExpiresAt,
  getServerUrl, setServerUrl,
  isEncrypted,
  effectiveExpireAt,
  clearSession,
} from '../utils/session';
import { authService } from '../services/auth-service';
import { serverHistoryService, normalizeServerUrl } from '../services/server-history-service';

interface AuthState {
  // 状态
  isLoggedIn: boolean;
  token: string | null;
  userId: number | null;
  username: string | null;
  role: 'admin' | 'user' | null;
  serverUrl: string;
  isAdmin: boolean;
  isEncrypted: boolean;

  // 初始化（从 localStorage 恢复）
  initFromStorage: () => void;

  // 密钥交换
  keyExchange: (serverUrl: string) => Promise<void>;

  // 登录流程
  login: (username: string, password: string, localMaxSeconds?: number) => Promise<void>;

  // 登出
  logout: () => Promise<void>;

  // 检查 token 是否过期
  isTokenExpired: () => boolean;
}

export const useAuthStore = create<AuthState>((set) => ({
  isLoggedIn: false,
  token: null,
  userId: null,
  username: null,
  role: null,
  serverUrl: getServerUrl(),
  isAdmin: false,
  isEncrypted: false,

  initFromStorage: () => {
    const token = getToken();
    const username = getPersistedUsername();
    const role = getPersistedRole();
    const userId = getPersistedUserId();

    if (token && username && role && userId) {
      set({
        isLoggedIn: true,
        token,
        username,
        role,
        userId,
        isAdmin: role === 'admin',
        serverUrl: getServerUrl(),
        isEncrypted: false, // session_key 不持久化，需要重新密钥交换
      });
    }
  },

  keyExchange: async (serverUrl: string) => {
    // 规范化地址（补全 http://、去尾部斜杠），避免相对路径请求
    const normalized = normalizeServerUrl(serverUrl);
    setServerUrl(normalized);
    await authService.keyExchange(normalized);
    serverHistoryService.record(normalized);
    set({
      serverUrl: normalized,
      isEncrypted: true,
    });
  },

  login: async (username, password, localMaxSeconds) => {
    const result = await authService.login(username, password, localMaxSeconds);

    set({
      isLoggedIn: true,
      token: result.token,
      userId: result.user_id,
      username: result.username,
      role: result.role,
      isAdmin: result.role === 'admin',
      isEncrypted: isEncrypted(),
    });
  },

  logout: async () => {
    await authService.logout();
    set({
      isLoggedIn: false,
      token: null,
      userId: null,
      username: null,
      role: null,
      isAdmin: false,
      isEncrypted: false,
    });
  },

  isTokenExpired: () => {
    const tokenExpiresAt = getTokenExpiresAt();
    const effective = effectiveExpireAt(tokenExpiresAt);
    if (effective && Date.now() / 1000 >= effective) {
      // 同步清除本地会话状态（服务端登出由调用方另行触发，避免阻塞路由守卫）
      clearSession();
      set({
        isLoggedIn: false,
        token: null,
        userId: null,
        username: null,
        role: null,
        isAdmin: false,
        isEncrypted: false,
      });
      return true;
    }
    return false;
  },
}));

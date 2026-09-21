/**
 * 认证状态管理（桌面端）
 *
 * 与 Web 端 `jflove-web/src/stores/auth-store.ts` 的差异：
 *   - **没有 `token` 字段** —— 令牌只在 Python 侧。这不是疏漏：若将来有页面
 *     误以为能读到 JWT，TypeScript 会直接编译报错，而不是静默拿到 null；
 *   - `initFromStorage()`（同步读 localStorage）改为 `bootstrap()`（异步问 Python），
 *     因为权威会话在 Python，桌面端还支持真正的"免登录恢复"；
 *   - `isEncrypted` 由 Python 报告的 `session_ready` 决定。
 */

import { create } from 'zustand';
import { authService, type SessionView } from '../services/auth-service';
import { loadServerHistory, serverHistoryService } from '../services/server-history-service';
import {
  effectiveExpireAt,
  getAuthSnapshot,
  getTokenExpiresAt,
  setAuthSnapshot,
} from '../utils/session';

type Role = 'admin' | 'user';

interface AuthState {
  // 状态
  isLoggedIn: boolean;
  userId: number | null;
  username: string | null;
  role: Role | null;
  serverUrl: string;
  isAdmin: boolean;
  isEncrypted: boolean;
  /** 启动引导（恢复会话 + 加载地址历史）是否完成 */
  bootstrapped: boolean;

  // 启动引导
  bootstrap: () => Promise<void>;

  // 密钥交换
  keyExchange: (serverUrl: string) => Promise<void>;

  // 登录 / 登出
  login: (username: string, password: string, localMaxSeconds?: number) => Promise<void>;
  logout: () => Promise<void>;

  // 检查会话是否过期
  isTokenExpired: () => boolean;
}

/** 把 Python 的会话快照映射成 store 状态（并同步缓存到 session 模块） */
function applySessionView(view: SessionView): Partial<AuthState> {
  const snap = setAuthSnapshot({
    loggedIn: Boolean(view.logged_in),
    username: view.username || '',
    role: view.role || '',
    userId: view.user_id ?? null,
    isAdmin: Boolean(view.is_admin),
    serverUrl: view.server_url || getAuthSnapshot().serverUrl,
    sessionReady: Boolean(view.session_ready),
    expiresAt: view.expires_at ? Number(view.expires_at) : null,
    sessionId: view.session_id || '',
    keyExchangeTime: Number(view.key_exchange_time || 0),
  });
  return {
    isLoggedIn: snap.loggedIn,
    userId: snap.userId,
    username: snap.username || null,
    role: snap.role ? (snap.role as Role) : null,
    serverUrl: snap.serverUrl,
    isAdmin: snap.isAdmin,
    isEncrypted: snap.sessionReady,
  };
}

const LOGGED_OUT: Partial<AuthState> = {
  isLoggedIn: false,
  userId: null,
  username: null,
  role: null,
  isAdmin: false,
  isEncrypted: false,
};

export const useAuthStore = create<AuthState>((set, get) => ({
  isLoggedIn: false,
  userId: null,
  username: null,
  role: null,
  serverUrl: '',
  isAdmin: false,
  isEncrypted: false,
  bootstrapped: false,

  bootstrap: async () => {
    // ① 地址历史（纯本地文件，不出网）
    await loadServerHistory();
    set({ serverUrl: serverHistoryService.getDefault() });

    // ② 问 Python：有没有可恢复的会话（有则它已顺手完成 ECDH）
    try {
      const view = await authService.restore();
      set({ ...applySessionView(view), bootstrapped: true });
      if (view.restore_error) {
        // 服务端不可达等：按未登录处理，登录页会展示地址输入
        set({ ...LOGGED_OUT, bootstrapped: true });
      }
    } catch {
      set({ ...LOGGED_OUT, bootstrapped: true });
    }
  },

  keyExchange: async (serverUrl: string) => {
    const view = await authService.keyExchange(serverUrl);
    set(applySessionView(view));
    // 只有连接成功才记录地址（与 Web 端一致）
    if (view.server_url) {
      serverHistoryService.record(view.server_url);
    }
  },

  login: async (username, password, localMaxSeconds) => {
    // 登录由 Python 服务层完成（它会写 session_manager + session.json），
    // 返回值本身就是会话快照，不需要再问一次 session()
    const view = await authService.login(username, password, localMaxSeconds);
    set(applySessionView(view));
  },

  logout: async () => {
    try {
      const view = await authService.logout();
      set(applySessionView(view));
    } catch {
      // 即使 Python 侧清理失败，也要把渲染态退回登录页
      set({ ...LOGGED_OUT });
    }
  },

  isTokenExpired: () => {
    const expired = (() => {
      const effective = effectiveExpireAt(getTokenExpiresAt());
      return Boolean(effective) && Date.now() / 1000 >= (effective as number);
    })();
    if (expired && get().isLoggedIn) {
      set({ ...LOGGED_OUT });
      void authService.logout().catch(() => {
        // 忽略：本地状态已经清掉了
      });
    }
    return expired;
  },
}));

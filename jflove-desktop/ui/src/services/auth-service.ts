/**
 * 认证服务（桌面端）
 *
 * 与 Web 端 `jflove-web/src/services/auth-service.ts` 的公开 API 一致，
 * 但底层不再自己生成密钥对：
 *   - **ECDH 密钥交换由 Python 执行**（`auth_service.do_key_exchange`），
 *     因为 session_key 只允许存在于 Python；
 *   - 登录 / 初始化管理员 / 管理员检测 走 `http-client` → 桥 → Python；
 *   - 登录响应里的 JWT 会被 Python **剥掉**（见 `src/bridge/bridge.py`），
 *     所以这里拿到的 `DesktopAuthResult` 不含 token。
 */

import { callBridge } from '../utils/desktop-bridge';
import {
  clearSession,
  getLocalSessionMaxSeconds,
  setAuthSnapshot,
  setServerUrl,
} from '../utils/session';
import { normalizeServerUrl } from './server-history-service';

/** Python 侧返回的会话快照（见 bridge 的 `_session_view`） */
export interface SessionView {
  logged_in: boolean;
  username: string;
  role: 'admin' | 'user' | '';
  user_id: number | null;
  is_admin: boolean;
  server_url: string;
  session_ready: boolean;
  /** 实际生效失效时间（Unix 秒），0 表示无 */
  expires_at: number;
  has_token: boolean;
  /** ECDH 会话 ID（安全状态页展示用，不是密钥） */
  session_id?: string;
  /** 密钥交换完成时间（Unix 秒） */
  key_exchange_time?: number;
}

export const authService = {
  /**
   * 执行 X25519 密钥交换（实际由 Python 完成）。
   *
   * @param serverUrl 服务端地址；桌面端必须给出完整地址（不存在"同源"）
   */
  async keyExchange(serverUrl?: string): Promise<SessionView> {
    const normalized = normalizeServerUrl(serverUrl ?? '');
    if (normalized) setServerUrl(normalized);
    return await callBridge<SessionView>('auth', 'key_exchange', {
      server_url: normalized,
    });
  },

  /** 检查服务端是否已有管理员（明文白名单接口） */
  async adminExists(): Promise<boolean> {
    return await callBridge<boolean>('auth', 'admin_exists', {});
  },

  /** 初始化管理员账号 */
  async initAdmin(username: string, password: string): Promise<void> {
    await callBridge('auth', 'init_admin', { username, password });
  },

  /**
   * 用户登录。
   *
   * **必须走桥的 `auth.login`，不能经 `api.request` 透传**：
   * 会话的唯一真相在 Python 的 `session_manager`，只有服务层
   * （`auth_service.login`）会同时写入会话与持久化 session.json。
   * 走透传会得到"HTTP 成功但桥回答未登录"的假成功。
   *
   * 返回的会话快照**不含令牌** —— 令牌只在 Python 侧。
   */
  async login(
    username: string,
    password: string,
    localMaxSeconds?: number,
  ): Promise<SessionView> {
    const maxSec = localMaxSeconds ?? getLocalSessionMaxSeconds();
    return await callBridge<SessionView>('auth', 'login', {
      username,
      password,
      local_max_seconds: maxSec,
    });
  },

  /** 登出（清空 Python 侧会话与持久化数据） */
  async logout(): Promise<SessionView> {
    const view = await callBridge<SessionView>('auth', 'logout', {});
    clearSession();
    return view;
  },

  /** 查询当前会话（不含令牌内容） */
  async session(): Promise<SessionView> {
    return await callBridge<SessionView>('auth', 'session', {});
  },

  /**
   * 刷新会话密钥（安全状态页的「刷新会话密钥」按钮）。
   *
   * 由 Python 执行：旧 session_key 立即失效并重新 ECDH。
   * 刷新会换掉 `session_id` 与密钥交换时间，因此这里把渲染层快照同步过来 ——
   * 否则安全状态页要等到下一次 `auth.session` 才会显示新的 Session ID。
   */
  async refreshSessionKey(): Promise<SessionView> {
    const view = await callBridge<SessionView>('auth', 'refresh_key_exchange', {});
    setAuthSnapshot({
      sessionReady: Boolean(view.session_ready),
      expiresAt: view.expires_at ? Number(view.expires_at) : null,
      sessionId: view.session_id || '',
      keyExchangeTime: Number(view.key_exchange_time || 0),
    });
    return view;
  },

  /**
   * 启动时恢复会话（桌面端的"免登录"能力）。
   *
   * Python 会读本地 session.json 并重建 ECDH 会话；服务端不可达时
   * **不抛异常**，而是返回 `restore_error`，让应用正常显示登录页。
   */
  async restore(): Promise<SessionView & { restore_error?: string }> {
    return await callBridge<SessionView & { restore_error?: string }>(
      'auth',
      'restore_session',
      {},
    );
  },
};

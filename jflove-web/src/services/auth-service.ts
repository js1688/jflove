/**
 * 认证服务
 *
 * 对标桌面端 src/services/auth_service.py。
 * 密钥交换 / 管理员检测 / 初始化管理员 / 登录 / 登出 / 静默续约。
 */

import { httpClient } from '../utils/http-client';
import {
  generateKeyPair, deriveSessionKey,
} from '../utils/crypto';
import {
  setSessionKey, setSessionId, setKeyExchangeTime,
  setServerUrl, setToken, saveUserInfo,
  setTokenExpiresAt, clearSession, getLocalSessionMaxSeconds,
} from '../utils/session';
import type {
  KeyExchangeResponse,
  AdminExistsResponse,
  AuthResult,
} from '../types/models';
export const authService = {
  /**
   * 执行 X25519 密钥交换。
   * 生成临时密钥对 → 发送公钥到服务端 → ECDH + HKDF 派生 session_key。
   */
  async keyExchange(serverUrl?: string): Promise<void> {
    if (serverUrl) setServerUrl(serverUrl);

    const { publicKeyB64, privateKey } = await generateKeyPair();

    const resp = await httpClient.postPlain<KeyExchangeResponse>(
      '/api/v1/auth/key-exchange',
      { client_public_key: publicKeyB64 },
    );

    const sessionKey = await deriveSessionKey(privateKey, resp.server_public_key);

    setSessionKey(sessionKey);
    setSessionId(resp.session_id);
    setKeyExchangeTime(Date.now() / 1000);
  },

  /** 检查服务端是否已有管理员 */
  async adminExists(): Promise<boolean> {
    const resp = await httpClient.getPlain<AdminExistsResponse>(
      '/api/v1/auth/admin-exists',
    );
    return resp.exists;
  },

  /** 初始化管理员账号 */
  async initAdmin(username: string, password: string): Promise<void> {
    await httpClient.post('/api/v1/auth/init-admin', {
      username,
      password,
    });
  },

  /** 用户登录 */
  async login(
    username: string,
    password: string,
    localMaxSeconds?: number,
  ): Promise<AuthResult> {
    const body: Record<string, unknown> = {
      username,
      password,
    };

    const maxSec = localMaxSeconds ?? getLocalSessionMaxSeconds();
    if (maxSec) {
      body.requested_ttl_seconds = maxSec;
    }

    const result = await httpClient.post<AuthResult>(
      '/api/v1/auth/login',
      body,
    );

    // 持久化 token 和用户信息
    setToken(result.token);
    saveUserInfo(result.user_id, result.username, result.role);

    // 从 JWT 解码 exp（简化处理：用 expires_in 估算）
    // 服务端返回 expires_in（秒），转换为绝对时间戳
    const expiresAt = (Date.now() / 1000) + (result.expires_in || 86400);
    setTokenExpiresAt(expiresAt);

    return result;
  },

  /** 登出 */
  async logout(): Promise<void> {
    try {
      await httpClient.post('/api/v1/auth/logout', {});
    } catch {
      // 登出失败不阻塞本地清除
    }
    clearSession();
  },

  /**
   * 静默续约（仅重新密钥交换，不动 JWT token）。
   * 由 http-client 在 401 时自动调用。
   */
  async resyncSession(): Promise<void> {
    const { publicKeyB64, privateKey } = await generateKeyPair();

    const resp = await httpClient.postPlain<KeyExchangeResponse>(
      '/api/v1/auth/key-exchange',
      { client_public_key: publicKeyB64 },
    );

    const sessionKey = await deriveSessionKey(privateKey, resp.server_public_key);

    setSessionKey(sessionKey);
    setSessionId(resp.session_id);
    setKeyExchangeTime(Date.now() / 1000);
  },

  /** 刷新会话密钥（用户手动触发，原 session_key 立即失效） */
  async refreshSessionKey(): Promise<void> {
    await this.resyncSession();
  },
};

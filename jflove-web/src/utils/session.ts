/**
 * 会话管理模块
 *
 * 对标桌面端 src/utils/session.py 和移动端 lib/utils/session.dart。
 *
 * session_key 仅存内存闭包变量，不进入 Zustand store 或 localStorage，
 * 页面关闭即清除。token 可持久化到 localStorage 用于免登录恢复。
 */

import { SESSION_TTL_OPTIONS, DEFAULT_SERVER_URL } from '../config/constants';

// ── 内存中的 session_key（闭包变量，不暴露到全局） ──

let _sessionKey: Uint8Array | null = null;
let _sessionId: string | null = null;
let _keyExchangeTime: number | null = null;

// ── localStorage key ──

const STORAGE_KEYS = {
  token: 'jflove_token',
  serverUrl: 'jflove_server_url',
  username: 'jflove_username',
  role: 'jflove_role',
  userId: 'jflove_user_id',
  tokenExpiresAt: 'jflove_token_expires_at',
  localSessionMaxSeconds: 'jflove_session_max_seconds',
  notesDiskId: 'jflove_notes_disk_id',
  notesPath: 'jflove_notes_path',
};

// ── Session Key 管理 ──

export function getSessionKey(): Uint8Array | null {
  return _sessionKey;
}

export function setSessionKey(key: Uint8Array | null): void {
  _sessionKey = key;
}

export function getSessionId(): string | null {
  return _sessionId;
}

export function setSessionId(id: string | null): void {
  _sessionId = id;
}

export function getKeyExchangeTime(): number | null {
  return _keyExchangeTime;
}

export function setKeyExchangeTime(time: number | null): void {
  _keyExchangeTime = time;
}

/** 是否已建立加密会话 */
export function isEncrypted(): boolean {
  return _sessionKey !== null && _sessionId !== null;
}

// ── Token 持久化（localStorage） ──

export function getToken(): string | null {
  return localStorage.getItem(STORAGE_KEYS.token);
}

export function setToken(token: string | null): void {
  if (token) {
    localStorage.setItem(STORAGE_KEYS.token, token);
  } else {
    localStorage.removeItem(STORAGE_KEYS.token);
  }
}

// ── 服务器地址 ──

export function getServerUrl(): string {
  return localStorage.getItem(STORAGE_KEYS.serverUrl) || DEFAULT_SERVER_URL;
}

export function setServerUrl(url: string): void {
  localStorage.setItem(STORAGE_KEYS.serverUrl, url);
}

// ── 用户信息持久化 ──

export function getPersistedUsername(): string | null {
  return localStorage.getItem(STORAGE_KEYS.username);
}

export function getPersistedRole(): 'admin' | 'user' | null {
  return localStorage.getItem(STORAGE_KEYS.role) as 'admin' | 'user' | null;
}

export function getPersistedUserId(): number | null {
  const v = localStorage.getItem(STORAGE_KEYS.userId);
  return v ? parseInt(v, 10) : null;
}

export function saveUserInfo(userId: number, username: string, role: string): void {
  localStorage.setItem(STORAGE_KEYS.userId, String(userId));
  localStorage.setItem(STORAGE_KEYS.username, username);
  localStorage.setItem(STORAGE_KEYS.role, role);
}

// ── Token 过期时间 ──

export function getTokenExpiresAt(): number | null {
  const v = localStorage.getItem(STORAGE_KEYS.tokenExpiresAt);
  return v ? parseFloat(v) : null;
}

export function setTokenExpiresAt(expiresAt: number | null): void {
  if (expiresAt) {
    localStorage.setItem(STORAGE_KEYS.tokenExpiresAt, String(expiresAt));
  } else {
    localStorage.removeItem(STORAGE_KEYS.tokenExpiresAt);
  }
}

// ── 登录有效期偏好 ──

export function getLocalSessionMaxSeconds(): number {
  const v = localStorage.getItem(STORAGE_KEYS.localSessionMaxSeconds);
  if (v) return parseInt(v, 10);
  // 默认取最后一个选项（30 天）
  return SESSION_TTL_OPTIONS[SESSION_TTL_OPTIONS.length - 1].value;
}

export function setLocalSessionMaxSeconds(seconds: number): void {
  localStorage.setItem(STORAGE_KEYS.localSessionMaxSeconds, String(seconds));
}

// ── 笔记目录偏好 ──

export function getNotesDiskId(): number | null {
  const v = localStorage.getItem(STORAGE_KEYS.notesDiskId);
  return v ? parseInt(v, 10) : null;
}

export function setNotesDiskId(diskId: number | null): void {
  if (diskId !== null) {
    localStorage.setItem(STORAGE_KEYS.notesDiskId, String(diskId));
  } else {
    localStorage.removeItem(STORAGE_KEYS.notesDiskId);
  }
}

export function getNotesPath(): string {
  return localStorage.getItem(STORAGE_KEYS.notesPath) || '';
}

export function setNotesPath(path: string): void {
  localStorage.setItem(STORAGE_KEYS.notesPath, path);
}

// ── 清除所有会话状态 ──

/** 登出时清除所有持久化数据 + 内存状态 */
export function clearSession(): void {
  _sessionKey = null;
  _sessionId = null;
  _keyExchangeTime = null;

  Object.values(STORAGE_KEYS).forEach((key) => {
    // 保留服务器地址历史和登录有效期偏好（用户偏好，不随登出清除）
    if (key === STORAGE_KEYS.serverUrl) return;
    if (key === STORAGE_KEYS.localSessionMaxSeconds) return;
    localStorage.removeItem(key);
  });
}

// ── 有效过期时间（取 JWT exp 与 local_max 较小者） ──

export function effectiveExpireAt(tokenExpiresAt: number | null): number | null {
  if (!tokenExpiresAt) return null;
  const keyExchangeTime = getKeyExchangeTime();
  if (!keyExchangeTime) return tokenExpiresAt;
  const localMax = getLocalSessionMaxSeconds();
  const localExpire = keyExchangeTime + localMax;
  return Math.min(tokenExpiresAt, localExpire);
}

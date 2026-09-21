/**
 * 会话渲染态缓存（桌面端）
 *
 * 与 Web 端 `jflove-web/src/utils/session.ts` 的**根本差异**：
 * **JS 侧不持有 token，也不持有 session_key** —— 权威状态在 Python 的
 * `src/utils/session.py::session_manager`，令牌只在那里，永不下发到渲染层。
 *
 * 本模块只保存两样东西：
 *   1. 一份「用于渲染的会话快照」（用户名 / 角色 / 是否已登录 / 失效时间）；
 *   2. 纯 UI 偏好（服务端地址、登录有效期上限），放 localStorage。
 */

import { SESSION_TTL_OPTIONS } from '../config/constants';

export interface AuthSnapshot {
  /** 是否已登录（以 Python 侧的会话为准） */
  loggedIn: boolean;
  username: string;
  role: 'admin' | 'user' | '';
  userId: number | null;
  isAdmin: boolean;
  serverUrl: string;
  /** 加密会话是否已建立（ECDH 是否完成） */
  sessionReady: boolean;
  /** 实际生效失效时间（Unix 秒）；由 Python 按 min(JWT exp, 本地上限) 算好 */
  expiresAt: number | null;
  /** ECDH 会话 ID（安全状态页展示用；**不是密钥**，泄漏它也解不开密文） */
  sessionId: string;
  /** 密钥交换完成时间（Unix 秒），0 表示尚未完成 */
  keyExchangeTime: number;
}

const EMPTY_SNAPSHOT: AuthSnapshot = {
  loggedIn: false,
  username: '',
  role: '',
  userId: null,
  isAdmin: false,
  serverUrl: '',
  sessionReady: false,
  expiresAt: null,
  sessionId: '',
  keyExchangeTime: 0,
};

const STORAGE_KEYS = {
  serverUrl: 'jflove_server_url',
  localSessionMaxSeconds: 'jflove_session_max_seconds',
  notesDiskId: 'jflove_notes_disk_id',
  notesPath: 'jflove_notes_path',
};

let snapshot: AuthSnapshot = { ...EMPTY_SNAPSHOT };

// ── 会话快照 ──────────────────────────────────────

/** 读取当前渲染态快照 */
export function getAuthSnapshot(): AuthSnapshot {
  return snapshot;
}

/** 合并更新快照（`serverUrl` 会同步落到 localStorage） */
export function setAuthSnapshot(patch: Partial<AuthSnapshot>): AuthSnapshot {
  snapshot = { ...snapshot, ...patch };
  if (patch.serverUrl !== undefined) {
    setServerUrl(patch.serverUrl);
  }
  return snapshot;
}

/** 是否已建立加密会话 */
export function isEncrypted(): boolean {
  return snapshot.sessionReady;
}

/**
 * 清除渲染态会话。
 *
 * 注意：**不会**动 Python 侧的会话（那里由 `auth.logout` 负责），
 * 也不清除服务端地址与登录有效期偏好（属用户偏好，不随登出重置）。
 */
export function clearSession(): void {
  snapshot = { ...EMPTY_SNAPSHOT, serverUrl: getServerUrl() };
}

/**
 * 令牌占位。
 *
 * 桌面端的 JWT 只在 Python 侧，渲染层拿不到也不需要。
 * 保留此导出是为了让从 Web 端复制的调用点能通过类型检查，
 * 并让"拿不到令牌"这件事在代码里显式可见。
 */
export function getToken(): string | null {
  return null;
}

/** 会话失效时间（Unix 秒） */
export function getTokenExpiresAt(): number | null {
  return snapshot.expiresAt;
}

/**
 * 计算实际生效的失效时间。
 *
 * 桌面端直接返回入参即可 —— Python 的 `session_manager.effective_expire_at()`
 * 已经取过 `min(JWT exp, key_exchange_time + local_session_max_seconds)`，
 * 不需要在渲染层重算一遍。
 */
export function effectiveExpireAt(expiresAt: number | null): number | null {
  return expiresAt;
}

// ── UI 偏好 ───────────────────────────────────────

/** 服务端地址（UI 偏好；连接成功后由 auth-service 写入） */
export function getServerUrl(): string {
  try {
    return localStorage.getItem(STORAGE_KEYS.serverUrl) || '';
  } catch {
    return '';
  }
}

export function setServerUrl(url: string): void {
  try {
    localStorage.setItem(STORAGE_KEYS.serverUrl, url);
  } catch {
    // localStorage 不可用时不影响本次会话
  }
}

/** 登录有效期上限（秒）。默认取最后一个选项（30 天），与 Web 端一致 */
export function getLocalSessionMaxSeconds(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.localSessionMaxSeconds);
    if (raw) return parseInt(raw, 10);
  } catch {
    // 忽略
  }
  return SESSION_TTL_OPTIONS[SESSION_TTL_OPTIONS.length - 1].value;
}

export function setLocalSessionMaxSeconds(seconds: number): void {
  try {
    localStorage.setItem(STORAGE_KEYS.localSessionMaxSeconds, String(seconds));
  } catch {
    // 忽略
  }
}

// ── 会话详情（安全状态页展示用） ───────────────────

/**
 * ECDH 会话 ID。
 *
 * 这只是"会话标识"，**不是密钥** —— 每次密钥交换都会变，且没有它也无法解密
 * （解密需要 session_key，而它只在 Python 侧）。Web 端同样把它展示在安全状态页。
 */
export function getSessionId(): string {
  return snapshot.sessionId;
}

/** 密钥交换完成时间（Unix 秒）；未完成时返回 null */
export function getKeyExchangeTime(): number | null {
  return snapshot.keyExchangeTime || null;
}

// ── 笔记目录偏好 ───────────────────────────────────
// 注意：这只是「渲染层的即时状态」，权威值在服务端 users.notes_disk_id /
// notes_path（由 `noteService.getNotesDiskConfig/setNotesDiskConfig` 同步），
// 与 Web 端保持同一套 localStorage 键名，便于将来比对。

export function getNotesDiskId(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.notesDiskId);
    return raw ? parseInt(raw, 10) : null;
  } catch {
    return null;
  }
}

export function setNotesDiskId(diskId: number | null): void {
  try {
    if (diskId !== null) {
      localStorage.setItem(STORAGE_KEYS.notesDiskId, String(diskId));
    } else {
      localStorage.removeItem(STORAGE_KEYS.notesDiskId);
    }
  } catch {
    // 忽略
  }
}

export function getNotesPath(): string {
  try {
    return localStorage.getItem(STORAGE_KEYS.notesPath) || '';
  } catch {
    return '';
  }
}

export function setNotesPath(path: string): void {
  try {
    localStorage.setItem(STORAGE_KEYS.notesPath, path);
  } catch {
    // 忽略
  }
}

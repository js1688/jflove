/**
 * 远程连接服务（渲染层唯一入口）
 *
 * 所有调用都走 `callBridge`（QWebChannel），渲染层没有任何网络出口：
 * 配置由 Python 侧与服务端交换，SSH/SFTP 由 Python 侧直连目标主机
 * （`文档记录/设计文档/v1.10.0.md` §1.1）。
 *
 * ⚠ 本模块拿不到、也不应该拿到密码 / 私钥原文：桥只返回 `has_*` 与指纹。
 */
import { callBridge } from '../utils/desktop-bridge';

export interface RemoteGroup {
  id: number;
  name: string;
  sort_order: number;
  connection_count: number;
}

export type AuthType = 'password' | 'publickey' | 'password_keyboard';

export interface RemoteConnectionSummary {
  id: number;
  ssh_group_id: number;
  name: string;
  host: string;
  port: number;
  username: string;
  auth_type: AuthType;
  jump_connection_id: number;
  has_password: boolean;
  has_private_key: boolean;
  last_connected_at: string | null;
  updated_at: string;
}

export interface RemoteConnectionDetail {
  id: number;
  ssh_group_id: number;
  name: string;
  host: string;
  port: number;
  username: string;
  auth_type: AuthType;
  jump_connection_id: number;
  terminal_type: string;
  charset: string;
  init_command: string;
  host_key_fingerprint: string;
  private_key_fingerprint: string;
  has_password: boolean;
  has_private_key: boolean;
}

/** 新建 / 更新时提交的字段（凭据留空 = 不修改） */
export interface RemoteConnectionInput {
  name: string;
  host: string;
  port: number;
  username: string;
  auth_type: AuthType;
  ssh_group_id?: number;
  password?: string;
  private_key?: string;
  passphrase?: string;
  jump_connection_id?: number;
  terminal_type?: string;
  charset?: string;
  init_command?: string;
  clear_password?: boolean;
  clear_private_key?: boolean;
}

export interface OpenSessionResult {
  session_id: string;
  port: number;
  token: string;
}

export interface SftpEntry {
  name: string;
  size: number;
  mode: number;
  is_dir: boolean;
  mtime: number;
}

export const remoteService = {
  // ── 分组 ──
  listGroups: () => callBridge<RemoteGroup[]>('remote', 'groups_list'),
  createGroup: (name: string, sortOrder = 0) =>
    callBridge<{ id: number }>('remote', 'groups_create', { name, sort_order: sortOrder }),
  updateGroup: (id: number, patch: { name?: string; sort_order?: number }) =>
    callBridge<{ ok: boolean }>('remote', 'groups_update', { id, ...patch }),
  deleteGroup: (id: number) => callBridge<{ ok: boolean }>('remote', 'groups_delete', { id }),

  // ── 连接 ──
  listConnections: (keyword = '') =>
    callBridge<RemoteConnectionSummary[]>('remote', 'connections_list', { keyword }),
  getConnection: (id: number) =>
    callBridge<{ connection: RemoteConnectionDetail }>('remote', 'connections_get', { id }),
  createConnection: (payload: RemoteConnectionInput) =>
    callBridge<{ id: number }>('remote', 'connections_create', { ...payload } as unknown as Record<string, unknown>),
  updateConnection: (id: number, payload: RemoteConnectionInput) =>
    callBridge<{ ok: boolean }>('remote', 'connections_update', { id, ...payload } as unknown as Record<string, unknown>),
  deleteConnection: (id: number) =>
    callBridge<{ ok: boolean }>('remote', 'connections_delete', { id }),
  duplicateConnection: (id: number) =>
    callBridge<{ id: number }>('remote', 'connections_duplicate', { id }),

  // ── 会话 ──
  openSession: (connectionId: number, cols: number, rows: number) =>
    callBridge<OpenSessionResult>('remote', 'session_open', {
      connection_id: connectionId,
      cols,
      rows,
    }),
  closeSession: (sessionId: string) =>
    callBridge<{ ok: boolean }>('remote', 'session_close', { session_id: sessionId }),
  confirmHostKey: (sessionId: string, accept: boolean) =>
    callBridge<{ ok: boolean }>('remote', 'hostkey_confirm', { session_id: sessionId, accept }),
  confirmOtp: (sessionId: string, code: string) =>
    callBridge<{ ok: boolean }>('remote', 'otp_confirm', { session_id: sessionId, code }),

  // ── SFTP ──
  sftpList: (sessionId: string, path: string) =>
    callBridge<{ entries: SftpEntry[] }>('remote', 'sftp_list', { session_id: sessionId, path }),
  sftpMkdir: (sessionId: string, path: string) =>
    callBridge<{ ok: boolean }>('remote', 'sftp_mkdir', { session_id: sessionId, path }),
  sftpRename: (sessionId: string, oldPath: string, newPath: string) =>
    callBridge<{ ok: boolean }>('remote', 'sftp_rename', {
      session_id: sessionId,
      old: oldPath,
      new: newPath,
    }),
  sftpDelete: (sessionId: string, path: string, isDir: boolean) =>
    callBridge<{ ok: boolean }>('remote', 'sftp_delete', {
      session_id: sessionId,
      path,
      is_dir: isDir,
    }),
  sftpDownload: (sessionId: string, remotePath: string, localPath: string) =>
    callBridge<{ transfer_id: string }>('remote', 'sftp_download', {
      session_id: sessionId,
      remote_path: remotePath,
      local_path: localPath,
    }),
  sftpCancel: (sessionId: string) =>
    callBridge<{ ok: boolean }>('remote', 'sftp_cancel', { session_id: sessionId }),
  sftpUpload: (sessionId: string, localPath: string, remotePath: string) =>
    callBridge<{ transfer_id: string }>('remote', 'sftp_upload', {
      session_id: sessionId,
      local_path: localPath,
      remote_path: remotePath,
    }),
};

// ── 本机终端（仅本地，不走服务端） ──

export interface LocalShell {
  kind: string;
  label: string;
  path: string;
  args: string;
  available: boolean;
}

export interface LocalProfile {
  id: number;
  group_id: number;
  name: string;
  shell_kind: string;
  shell_path: string;
  args: string;
  cwd: string;
  env_json: string;
  init_command: string;
}

export const localTermService = {
  detectShells: () => callBridge<{ shells: LocalShell[] }>('local_term', 'detect_shells'),
  listGroups: () => callBridge<{ groups: Array<{ id: number; name: string; sort_order: number }> }>(
    'local_term',
    'groups_list',
  ),
  createGroup: (name: string) =>
    callBridge<{ id: number }>('local_term', 'groups_create', { name }),
  updateGroup: (id: number, name: string) =>
    callBridge<{ ok: boolean }>('local_term', 'groups_update', { id, name }),
  deleteGroup: (id: number) => callBridge<{ ok: boolean }>('local_term', 'groups_delete', { id }),
  listProfiles: () => callBridge<{ profiles: LocalProfile[] }>('local_term', 'profiles_list'),
  createProfile: (payload: Record<string, unknown>) =>
    callBridge<{ id: number }>('local_term', 'profiles_create', payload),
  updateProfile: (id: number, payload: Record<string, unknown>) =>
    callBridge<{ ok: boolean }>('local_term', 'profiles_update', { id, ...payload }),
  deleteProfile: (id: number) =>
    callBridge<{ ok: boolean }>('local_term', 'profiles_delete', { id }),
  open: (profileId: number, cols: number, rows: number) =>
    callBridge<OpenSessionResult>('local_term', 'open', {
      profile_id: profileId,
      cols,
      rows,
    }),
};

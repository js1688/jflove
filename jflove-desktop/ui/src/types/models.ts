/**
 * 数据模型类型定义
 *
 * 对标后端 Pydantic models，确保字段名与后端 API 响应完全一致。
 */

// ── 用户 ──

export interface User {
  id: number;
  username: string;
  role: 'admin' | 'user';
  enabled: boolean;
  created_at: string;
}

// ── 认证 ──

export interface AuthResult {
  token: string;
  user_id: number;
  username: string;
  role: 'admin' | 'user';
  expires_in: number;
}

export interface KeyExchangeRequest {
  client_public_key: string;
}

export interface KeyExchangeResponse {
  session_id: string;
  server_public_key: string;
}

export interface AdminExistsResponse {
  exists: boolean;
}

// ── 虚拟磁盘 ──

export interface VirtualDisk {
  id: number;
  name: string;
  /** 服务端真实路径（后端字段为 real_path，管理端展示用） */
  real_path?: string;
  path: string;
  can_write: boolean;
  /** v1.4.2：删除权限（修复功能要求写+删并存） */
  can_delete: boolean;
  created_at: string;
}

export interface DiskDir {
  name: string;
  path: string;
}

/** 服务器目录浏览的单条目录（v1.8.1：GET /virtual-disks/server-dirs，仅管理员） */
export interface ServerDirEntry {
  name: string;
  path: string;
}

/** 服务器目录浏览响应（只含目录，不含文件） */
export interface ServerDirListing {
  /** 当前规范化绝对路径 */
  path: string;
  /** 上一级绝对路径；根目录 / 盘符根为 null */
  parent: string | null;
  /** 当前目录下的子目录（隐藏目录已由服务端过滤） */
  dirs: ServerDirEntry[];
  /** 本机常见起点，供选择器快捷跳转 */
  roots: ServerDirEntry[];
  /** 子目录数超过服务端上限时为 true */
  truncated: boolean;
  /** 服务端列目录上限（v1.8.2 / C1）；旧服务端不返回时由前端回落 2000 */
  limit?: number;
}

// ── 文件/目录 ──

export interface FileItem {
  name: string;
  path: string;
  size: number;
  is_dir: boolean;
  modified_at: number;
}

export interface PreviewResult {
  type: 'text' | 'image' | 'markdown' | 'unsupported';
  content_type: string;
  content?: string;
  base64?: string;
  file_size: number;
}

// ── 笔记 ──

export interface Note {
  filename: string;
  size: number;
  modified_at: number;
}

// ── 同步 ──

export interface SyncSnapshotFile {
  path: string;
  size: number;
  modified_at: number;
}

export interface SyncSnapshotResponse {
  files: SyncSnapshotFile[];
}

// ── 传输任务 ──

export type TaskKind = 'upload' | 'download';
export type TaskStatus = 'pending' | 'hashing' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface TransferTask {
  id: string;
  kind: TaskKind;
  filename: string;
  localPath: string;
  diskId: number;
  remotePath: string;
  fileSize: number;
  transferred: number;
  percent: number;
  status: TaskStatus;
  error?: string;
}

// ── 权限 ──

export interface DiskPermission {
  user_id: number;
  username: string;
  virtual_disk_id: number;
  disk_name: string;
  can_read: boolean;
  can_write: boolean;
  can_delete: boolean;
}

// ── 服务端配置（GET /api/v1/config 响应，管理员）──

export interface ServerConfig {
  /** 服务端 config 表键值对（含 media_repair_enabled 等，v1.4.0） */
  config: Record<string, string>;
}

// ── API 错误 ──

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`[${status}] ${detail}`);
    this.name = 'ApiError';
  }
}

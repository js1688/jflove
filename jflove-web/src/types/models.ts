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
  /** 单层目录数量上限（v1.8.2 / C1，由服务端 `SERVER_DIR_LIMIT` 下发）；
   * 旧服务端不返回该字段，客户端需回落 2000 */
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
// v1.7.0：浏览器端移除同步能力，`SyncSnapshotFile` / `SyncSnapshotResponse` 已删除。
// 同步只在桌面端（挂载）与移动端（相册备份）提供。

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

// ── 离线下载（v1.8.0，接口前缀 /api/v1/offline，全部加密信封、无路径参数）──

/**
 * 任务状态（与后端 `offline_download_tasks.status` 一致）
 *
 * 状态机：queued → analyzing → ready → downloading → merging → success
 * 失败/取消可从任意进行中状态进入 failed / canceled。
 */
export type OfflineTaskStatus =
  | 'queued'
  | 'analyzing'
  | 'ready'
  | 'downloading'
  | 'merging'
  | 'success'
  | 'failed'
  | 'canceled';

/** 清单项类型（视频 / 音频 / 图片 / 字幕） */
export type OfflineItemKind = 'video' | 'audio' | 'image' | 'subtitle';

/**
 * 任务行（`GET /offline/tasks` / `GET /offline/detail` 的 `task`）
 *
 * 字段与设计文档 §2.2.2 `TaskRow` 严格一致；`status_text` 是服务端给的中文状态文案，
 * 界面直接展示，避免前端各写一份状态映射。
 */
export interface OfflineTask {
  id: number;
  /** 服务端返回的创建者用户名；任务已按账号隔离，界面不再展示 */
  username?: string;
  source_domain: string;
  page_title: string;
  status: OfflineTaskStatus;
  status_text: string;
  item_total: number;
  item_done: number;
  bytes_total: number;
  bytes_done: number;
  speed_bps: number;
  target_disk_name: string;
  target_rel_path: string;
  error_code: string;
  error_message: string;
  /** v1.8.2：partial = 解析超时但返回了部分结果（客户端展示提示） */
  analysis_level?: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** 自己的任务 **且** 对目标磁盘仍有 write 权限才为 true（服务端算好，前端不自行判断） */
  can_operate: boolean;
}

/** 清单项（`GET /offline/detail` 的 `items[]`，字段同 §2.2.3 `ItemRow`） */
export interface OfflineItem {
  id: number;
  kind: OfflineItemKind | string;
  display_name: string;
  ext: string;
  width: number;
  height: number;
  duration_ms: number;
  est_size: number;
  is_estimated: boolean;
  is_selected: boolean;
  status: string;
  error_message: string;
}

/** 任务详情（含清单与「是否还能继续抓下一页」） */
export interface OfflineTaskDetail {
  task: OfflineTask;
  items: OfflineItem[];
  has_more_images: boolean;
}

/** 可写磁盘（`/offline/meta` 已按该账号 write 权限过滤，客户端不自行判断） */
export interface OfflineWritableDisk {
  disk_id: number;
  name: string;
}

/** 当前账号的默认目录配置（`valid=false` 表示磁盘删除/权限收回/目录不存在） */
export interface OfflineConfig {
  disk_id: number;
  rel_path: string;
  valid: boolean;
}

/** 不可用原因四态（`/offline/meta` 的 `unusable_reason`，另有一个空串表示可用） */
export type OfflineUnusableReason =
  | ''
  | 'disabled'
  | 'no_writable_disk'
  | 'not_configured'
  | 'config_invalid';

/** 客户端初始化元信息（`GET /offline/meta`，字段同设计文档 §2.2.7） */
export interface OfflineMeta {
  enabled: boolean;
  deep_analyze_enabled: boolean;
  deep_analyze_available: boolean;
  can_use: boolean;
  configured: boolean;
  unusable_reason: OfflineUnusableReason | string;
  config: OfflineConfig;
  writable_disks: OfflineWritableDisk[];
  max_items: number;
  max_file_size: number;
  max_images: number;
  /** 轮询间隔（毫秒）；后端当前固定 2500 */
  poll_interval_ms: number;
}

/** `POST /offline/analyze` 响应 */
export interface OfflineAnalyzeResult {
  task_id: number;
  status: string;
  source_domain: string;
  page_title: string;
}

/** `GET /offline/tasks` 响应 */
export interface OfflineTaskList {
  total: number;
  tasks: OfflineTask[];
}

/** `POST /offline/select` 响应 */
export interface OfflineSelectResult {
  task_id: number;
  status: string;
  selected: number;
  bytes_total: number;
}

/** `POST /offline/next-page` 响应 */
export interface OfflineNextPageResult {
  added: number;
  item_total: number;
  has_more: boolean;
  page_index: number;
}

/** `POST /offline/settings` 响应 */
export interface OfflineSettingsResult {
  configured: boolean;
  disk_id: number;
  rel_path: string;
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

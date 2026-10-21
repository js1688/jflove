/**
 * 离线下载服务（v1.8.0）
 *
 * 封装服务端 `/api/v1/offline/*` 的 10 个接口（设计 §2.2 / §7）：
 *   - analyze：创建分析任务（粘贴网址）
 *   - listTasks / getDetail：任务中心列表与清单详情
 *   - select / nextPage：勾选开始下载、图片追加下一页
 *   - cancel / retry / deleteRecord：任务操作
 *   - getMeta / saveSettings：账号级默认目录与可用性元信息
 *
 * ## 分层与安全（AGENTS.md §9.5 / §9.6）
 *
 * - **所有请求必须走 `utils/http-client.ts`**：桌面端渲染层无网络出口，
 *   `httpClient` 会把请求交给 Python 桥，加密与解密只在 Python 侧完成；
 * - GET 一律用 `httpClient.get(path, body)` —— Python 侧把 body 放进加密请求体，
 *   不会出现在 URL 上（§9.3 第 4 条 / 设计 §2.2.0）；
 * - 本文件**不**包含任何加密原语、token 读取或 session_key 处理，
 *   `token` 由 Python 的 `_build_payload()` 注入（渲染层永远拿不到 token）。
 */

import { httpClient } from '../utils/http-client';

/** 任务状态（与服务端 `_STATUS_TEXT` 的键一一对应） */
export type OfflineTaskStatus =
  | 'queued'
  | 'analyzing'
  | 'ready'
  | 'downloading'
  | 'merging'
  | 'success'
  | 'failed'
  | 'canceled';

/** 任务行（设计 §2.2.2 `TaskRow`，字段不可自行增删） */
export interface OfflineTask {
  id: number;
  username: string;
  /** 来源域名（服务端只下发域名，不含完整 URL） */
  source_domain: string;
  /** 页面标题（产物命名基础） */
  page_title: string;
  status: string;
  status_text: string;
  item_total: number;
  item_done: number;
  bytes_total: number;
  bytes_done: number;
  /** 当前速度（字节/秒） */
  speed_bps: number;
  target_disk_name: string;
  target_rel_path: string;
  error_code: string;
  error_message: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** 是否「自己的任务」且「对目标磁盘仍有写权限」 */
  can_operate: boolean;
}

/** 清单项（设计 §2.2.3 `ItemRow`，字段不可自行增删） */
export interface OfflineItem {
  id: number;
  /** video / audio / image / subtitle */
  kind: string;
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

/** `POST /analyze` 响应 */
export interface OfflineAnalyzeResult {
  task_id: number;
  status: string;
  source_domain: string;
  page_title: string;
}

/** `GET /detail` 响应 */
export interface OfflineDetail {
  task: OfflineTask;
  items: OfflineItem[];
  has_more_images: boolean;
}

/** `GET /tasks` 响应 */
export interface OfflineTaskList {
  total: number;
  tasks: OfflineTask[];
}

/** `POST /select` 响应 */
export interface OfflineSelectResult {
  task_id: number;
  status: string;
  selected: number;
  bytes_total: number;
}

/** `POST /next-page` 响应 */
export interface OfflineNextPageResult {
  added: number;
  item_total: number;
  has_more: boolean;
  page_index: number;
}

/** `POST /settings` 响应 */
export interface OfflineSettingsResult {
  configured: boolean;
  disk_id: number;
  rel_path: string;
}

/** 可写磁盘（服务端已按 write 权限过滤，客户端不自行判断） */
export interface OfflineWritableDisk {
  disk_id: number;
  name: string;
}

/** 账号级默认目录 */
export interface OfflineMetaConfig {
  disk_id: number;
  rel_path: string;
  /** 配置存在但磁盘被删除 / 权限被收回 / 目录不存在时为 false */
  valid: boolean;
}

/** `GET /meta` 响应（客户端初始化元信息） */
export interface OfflineMeta {
  enabled: boolean;
  deep_analyze_enabled: boolean;
  deep_analyze_available: boolean;
  can_use: boolean;
  configured: boolean;
  /** disabled / no_writable_disk / not_configured / config_invalid */
  unusable_reason: string;
  config: OfflineMetaConfig;
  writable_disks: OfflineWritableDisk[];
  max_items: number;
  max_file_size: number;
  max_images: number;
  poll_interval_ms: number;
}

/** `POST /cancel|retry|delete-record` 的统一响应 */
export interface OfflineTaskAction {
  task_id: number;
  status?: string;
  deleted?: boolean;
}

/**
 * 离线下载服务对象。
 *
 * 说明：方法名用 camelCase（前端规范），请求字段保持后端契约的 snake_case。
 */
export const offlineDownloadService = {
  /** 创建分析任务（服务端异步分析，返回的 task 需轮询 `/detail` 等 ready） */
  async analyze(url: string): Promise<OfflineAnalyzeResult> {
    return httpClient.post<OfflineAnalyzeResult>('/api/v1/offline/analyze', { url });
  },

  /** 分页获取**当前账号自己的**任务列表（服务端按 user_id 过滤） */
  async listTasks(page = 1, pageSize = 20): Promise<OfflineTaskList> {
    return httpClient.get<OfflineTaskList>('/api/v1/offline/tasks', {
      page,
      page_size: pageSize,
    });
  },

  /** 获取任务详情（含清单；他人任务按不存在返回 404） */
  async getDetail(taskId: number): Promise<OfflineDetail> {
    return httpClient.get<OfflineDetail>('/api/v1/offline/detail', { task_id: taskId });
  },

  /**
   * 提交勾选并开始下载。
   *
   * `diskId` / `targetPath` 为本次**临时**目标（不回写账号默认目录）。
   * 注意：目标相对路径为空时传 `.`，否则服务端会把空串当成「未传」并回退到
   * 账号默认目录（`select_items` 的 fallback），导致用户选了磁盘根却被写到默认目录。
   */
  async select(
    taskId: number,
    itemIds: number[],
    diskId: number,
    targetPath: string,
    packZip = false,
  ): Promise<OfflineSelectResult> {
    return httpClient.post<OfflineSelectResult>('/api/v1/offline/select', {
      task_id: taskId,
      item_ids: itemIds,
      disk_id: diskId,
      target_path: targetPath.trim() || '.',
      pack_zip: packZip,
    });
  },

  /** 图片追加下一页（服务端按 image_page_index 自增，页数上限由服务端控制） */
  async nextPage(taskId: number): Promise<OfflineNextPageResult> {
    return httpClient.post<OfflineNextPageResult>('/api/v1/offline/next-page', {
      task_id: taskId,
    });
  },

  /** 取消排队/分析/下载/合并中的任务（终态返回 409） */
  async cancel(taskId: number): Promise<OfflineTaskAction> {
    return httpClient.post<OfflineTaskAction>('/api/v1/offline/cancel', { task_id: taskId });
  },

  /** 重试失败/已取消的任务（DRM / 需登录等不可重试类型返回 409） */
  async retry(taskId: number): Promise<OfflineTaskAction> {
    return httpClient.post<OfflineTaskAction>('/api/v1/offline/retry', { task_id: taskId });
  },

  /** 删除任务记录（只软删任务行，**不删除磁盘文件**） */
  async deleteRecord(taskId: number): Promise<OfflineTaskAction> {
    return httpClient.post<OfflineTaskAction>('/api/v1/offline/delete-record', {
      task_id: taskId,
    });
  },

  /** 读取初始化元信息（登录用户均可读；只含自己的配置与可写磁盘） */
  async getMeta(): Promise<OfflineMeta> {
    return httpClient.get<OfflineMeta>('/api/v1/offline/meta');
  },

  /**
   * 保存/清除**自己的**默认目录。
   *
   * `diskId=0` 表示清除配置（服务端软删除配置行）。
   * `create=true` 允许在目标磁盘内创建不存在的目录（`POST /settings` 的契约）。
   */
  async saveSettings(
    diskId: number,
    relPath: string,
    create = false,
  ): Promise<OfflineSettingsResult> {
    return httpClient.post<OfflineSettingsResult>('/api/v1/offline/settings', {
      disk_id: diskId,
      rel_path: relPath,
      create,
    });
  },
};

/**
 * 服务端配置服务
 *
 * 对标桌面端 src/services/config_service.py。
 * 后端 `GET/PUT /api/v1/config` 均为管理员接口（加密 body + JWT）。
 */

import { httpClient } from '../utils/http-client';
import type { ServerConfig } from '../types/models';

/** 媒体修复相关配置键（与后端 settings.py 保持一致）
 * v1.4.2 hotfix：media_repair_enabled（实时修复总开关）与
 * media_repair_allow_transcode（重编码降级）均已废弃，前端不再读写 */
export const MEDIA_REPAIR_KEYS = {
  maxConcurrent: 'media_repair_max_concurrent',
} as const;

/** 离线下载全局策略配置键（与后端 settings.py 的 OFFLINE_*_KEY 保持一致）
 * 默认值由后端 `OFFLINE_CONFIG_DEFAULTS` 补齐；总开关默认开（v1.8.0 UAT 裁决）。
 * v1.8.2（F37.6）：新增 3 个超时项，范围/默认值与服务端 `validate_config_value` 同源
 * （解析 90s / 30–180，连接 15s / 5–30，读取 60s / 15–120）。 */
export const OFFLINE_DOWNLOAD_KEYS = {
  enabled: 'offline_download_enabled',
  deepAnalyze: 'offline_download_deep_analyze',
  maxConcurrent: 'offline_download_max_concurrent',
  maxFileSize: 'offline_download_max_file_size_mb',
  maxItems: 'offline_download_max_items',
  maxImages: 'offline_download_max_images',
  analyzeTimeout: 'offline_download_analyze_timeout_s',
  connectTimeout: 'offline_download_connect_timeout_s',
  readTimeout: 'offline_download_read_timeout_s',
} as const;

/** 存储与临时文件配置键（v1.9.0 / 设计 §3.5 字段映射，与后端 settings.py 同源）
 *
 * 注意 `upload_resume_ttl_hours` 是**唯一 TTL 来源**（D32）：上传会话过期、
 * 自动清理保护期、客户端「可续传至」提示都读它，不存在第二个清理阈值。 */
export const STORAGE_CLEANUP_KEYS = {
  enabled: 'upload_cleanup_enabled',
  resumeTtlHours: 'upload_resume_ttl_hours',
  cleanupIntervalMinutes: 'upload_cleanup_interval_minutes',
  cleanupClientTypes: 'upload_cleanup_client_types',
  orphanTtlHours: 'upload_orphan_ttl_hours',
  maxSessionsPerUser: 'upload_max_sessions_per_user',
  mergeConcurrency: 'upload_merge_concurrency',
  chunkConcurrency: 'upload_chunk_concurrency',
} as const;

/** 服务端未落库时展示的默认值（与后端 `UPLOAD_CONFIG_DEFAULTS` 一致，以 config key 索引） */
export const STORAGE_CLEANUP_DEFAULTS: Record<string, string> = {
  [STORAGE_CLEANUP_KEYS.enabled]: '1',
  [STORAGE_CLEANUP_KEYS.resumeTtlHours]: '24',
  [STORAGE_CLEANUP_KEYS.cleanupIntervalMinutes]: '60',
  [STORAGE_CLEANUP_KEYS.cleanupClientTypes]: '',
  [STORAGE_CLEANUP_KEYS.orphanTtlHours]: '24',
  [STORAGE_CLEANUP_KEYS.maxSessionsPerUser]: '8',
  [STORAGE_CLEANUP_KEYS.mergeConcurrency]: '1',
  [STORAGE_CLEANUP_KEYS.chunkConcurrency]: '2',
};

/** 允许自动清理的客户端类型（与后端 `UPLOAD_CLIENT_TYPES` 一致） */
export const STORAGE_CLIENT_TYPES = ['app', 'desktop', 'web', 'unknown'] as const;
export type StorageClientType = (typeof STORAGE_CLIENT_TYPES)[number];

/** 浏览器端的客户端类型中文名 */
export const STORAGE_CLIENT_TYPE_LABELS: Record<StorageClientType, string> = {
  app: 'App',
  desktop: '桌面端',
  web: 'Web 端',
  unknown: '未知客户端',
};

/**
 * 解析逗号配置 → 合法客户端类型（去重、过滤非法值，保持后端顺序）。
 *
 * 空集合语义 = 全部类型，调用方据此展示「全部」。
 */
export function parseClientTypes(
  value: string | null | undefined,
): StorageClientType[] {
  if (!value) return [];
  const selected = new Set(
    value.split(',').map((raw) => raw.trim().toLowerCase()),
  );
  return STORAGE_CLIENT_TYPES.filter((type) => selected.has(type));
}

/**
 * 客户端类型多选 → 逗号配置（按后端声明顺序排列；空数组 = 全部类型）。
 *
 * 顺序稳定，避免「勾选顺序不同 → PUT 值不同」造成无意义的配置刷新。
 */
export function joinClientTypes(types: readonly string[]): string {
  const selected = new Set(types);
  return STORAGE_CLIENT_TYPES.filter((t) => selected.has(t)).join(',');
}

/** 立即清理接口返回的统计（`POST /api/v1/config/cleanup`，设计 §4） */
export interface CleanupResult {
  /** 删除的上传会话数 */
  removed_sessions: number;
  /** 删除的临时目录数 */
  removed_dirs: number;
  /** 删除的合并残留 `.part` 数 */
  removed_parts: number;
  /** 因命名/归属无法确认而跳过的数量 */
  skipped: number;
  /** 本次执行时间（Unix 秒，服务端可选返回） */
  executed_at?: number;
}

export const configService = {
  /** 获取服务端全部配置（key-value 映射，管理员） */
  async getConfig(): Promise<ServerConfig> {
    return httpClient.get<ServerConfig>('/api/v1/config');
  },

  /** 更新单个配置项（管理员，写后立即生效、无需重启） */
  async updateConfig(key: string, value: string): Promise<{ message: string }> {
    return httpClient.put<{ message: string }>('/api/v1/config', { key, value });
  },

  /** 立即执行一次安全清理（管理员；v1.9.0 F51.6） */
  async cleanupNow(): Promise<CleanupResult> {
    return httpClient.post<CleanupResult>('/api/v1/config/cleanup');
  },
};

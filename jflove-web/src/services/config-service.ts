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
 * v1.8.2（F37.6）：新增解析 / 连接 / 读取三个超时键，范围与服务端
 * `offline_download_service.validate_config_value` 一致（90/15/60，30–180 / 5–30 / 15–120） */
export const OFFLINE_DOWNLOAD_KEYS = {
  enabled: 'offline_download_enabled',
  deepAnalyze: 'offline_download_deep_analyze',
  maxConcurrent: 'offline_download_max_concurrent',
  maxFileSize: 'offline_download_max_file_size_mb',
  maxItems: 'offline_download_max_items',
  maxImages: 'offline_download_max_images',
  /** 单次网址解析总预算（秒），默认 90，允许 30~180 */
  analyzeTimeout: 'offline_download_analyze_timeout_s',
  /** 出站 HTTP 连接超时（秒），默认 15，允许 5~30 */
  connectTimeout: 'offline_download_connect_timeout_s',
  /** 出站 HTTP 读取超时（秒），默认 60，允许 15~120 */
  readTimeout: 'offline_download_read_timeout_s',
} as const;

// ── v1.9.0：存储与临时文件（上传断点续传 / 自动清理 / 弱服务端资源隔离） ──

/**
 * 上传清理与资源隔离配置键。
 *
 * 与后端 `settings.py` 的 `UPLOAD_*_KEY` 一一对应；解析/校验/默认值由服务端
 * `upload_cleanup_service` 负责，前端只做展示与前置范围校验。
 * 「断点续传保障时长」是**唯一 TTL 来源**（会话过期、清理保护期、客户端提示同一值，D32）。
 */
export const UPLOAD_CLEANUP_KEYS = {
  /** 自动清理总开关（"1"/"0"） */
  enabled: 'upload_cleanup_enabled',
  /** 断点续传保障时长（小时，1~168） */
  resumeTtlHours: 'upload_resume_ttl_hours',
  /** 自动清理间隔（分钟，5~1440） */
  cleanupIntervalMinutes: 'upload_cleanup_interval_minutes',
  /** 允许自动清理的客户端类型（逗号集；空 = 全部） */
  clientTypes: 'upload_cleanup_client_types',
  /** 孤儿临时文件保留期（小时，1~168） */
  orphanTtlHours: 'upload_orphan_ttl_hours',
  /** 每用户未完成上传会话上限（1~64） */
  maxSessionsPerUser: 'upload_max_sessions_per_user',
  /** 全局合并并发（0~4；0 = 仅排队不执行） */
  mergeConcurrency: 'upload_merge_concurrency',
  /** 全局上传分片并发（1~8） */
  chunkConcurrency: 'upload_chunk_concurrency',
} as const;

/** 各上传清理配置的默认值（须与服务端 `upload_cleanup_service.DEFAULTS` 一致） */
export const UPLOAD_CLEANUP_DEFAULTS = {
  [UPLOAD_CLEANUP_KEYS.enabled]: '1',
  [UPLOAD_CLEANUP_KEYS.resumeTtlHours]: '24',
  [UPLOAD_CLEANUP_KEYS.cleanupIntervalMinutes]: '60',
  [UPLOAD_CLEANUP_KEYS.clientTypes]: '',
  [UPLOAD_CLEANUP_KEYS.orphanTtlHours]: '24',
  [UPLOAD_CLEANUP_KEYS.maxSessionsPerUser]: '8',
  [UPLOAD_CLEANUP_KEYS.mergeConcurrency]: '1',
  [UPLOAD_CLEANUP_KEYS.chunkConcurrency]: '2',
} as const;

/** 各数值配置的允许范围（前端前置校验；越界值服务端也会 400） */
export const UPLOAD_CLEANUP_RANGES = {
  resumeTtlHours: { min: 1, max: 168 },
  cleanupIntervalMinutes: { min: 5, max: 1440 },
  orphanTtlHours: { min: 1, max: 168 },
  maxSessionsPerUser: { min: 1, max: 64 },
  mergeConcurrency: { min: 0, max: 4 },
  chunkConcurrency: { min: 1, max: 8 },
} as const;

/** 客户端类型枚举（与后端 `_CLIENT_TYPES` 一致） */
export const CLEANUP_CLIENT_TYPES = ['app', 'desktop', 'web', 'unknown'] as const;

/** 弱服务端模式预设（F56.5：合并并发 1 / 上传并发 2 / 每用户会话上限 4；分片 512KB 为客户端常量） */
export const WEAK_SERVER_PRESET = {
  [UPLOAD_CLEANUP_KEYS.mergeConcurrency]: '1',
  [UPLOAD_CLEANUP_KEYS.chunkConcurrency]: '2',
  [UPLOAD_CLEANUP_KEYS.maxSessionsPerUser]: '4',
} as const;

/** 立即清理结果（`POST /api/v1/config/cleanup`，设计 §4） */
export interface CleanupRunResult {
  /** 清理的上传会话数 */
  removed_sessions: number;
  /** 清理的目录数 */
  removed_dirs: number;
  /** 清理的合并残留 `.part` 数 */
  removed_parts: number;
  /** 因命名/路径不符合安全规则而跳过的对象数 */
  skipped: number;
  /** 执行时间（秒级时间戳） */
  executed_at: number;
}

/** 把配置里的逗号集解析为客户端类型数组（空 = 全部） */
export function parseCleanupClientTypes(value: string | undefined): string[] {
  return (value ?? '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean);
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

  /**
   * 立即执行一次安全清理（仅管理员，规则与定时任务一致）。
   * 返回清理数量与跳过数量（F51.6 / AC-353）。
   */
  async runCleanup(): Promise<CleanupRunResult> {
    return httpClient.post<CleanupRunResult>('/api/v1/config/cleanup');
  },
};

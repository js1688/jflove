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

export const configService = {
  /** 获取服务端全部配置（key-value 映射，管理员） */
  async getConfig(): Promise<ServerConfig> {
    return httpClient.get<ServerConfig>('/api/v1/config');
  },

  /** 更新单个配置项（管理员，写后立即生效、无需重启） */
  async updateConfig(key: string, value: string): Promise<{ message: string }> {
    return httpClient.put<{ message: string }>('/api/v1/config', { key, value });
  },
};

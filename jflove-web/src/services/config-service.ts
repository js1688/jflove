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

/**
 * 服务端配置查询服务
 *
 * 对标桌面端 src/services/config_service.py。
 */

import { httpClient } from '../utils/http-client';
import type { ServerConfig } from '../types/models';

export const configService = {
  /** 获取服务端配置 */
  async getConfig(): Promise<ServerConfig> {
    return httpClient.get<ServerConfig>('/api/v1/config');
  },
};

/**
 * 同步服务（降级）
 *
 * 浏览器端不支持本地文件系统同步，仅提供 snapshot 查询功能。
 */

import { httpClient } from '../utils/http-client';
import type { SyncSnapshotResponse } from '../types/models';

export const syncService = {
  /** 获取远端目录快照（用于信息展示） */
  async getSnapshot(diskId: number, remotePath: string): Promise<SyncSnapshotResponse> {
    return httpClient.post<SyncSnapshotResponse>('/api/v1/sync/snapshot', {
      disk_id: diskId,
      remote_path: remotePath,
    });
  },
};

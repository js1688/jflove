/**
 * 虚拟磁盘管理服务（admin + 用户通用）
 *
 * 对标桌面端 src/services/disk_service.py。
 */

import { httpClient } from '../utils/http-client';
import type { VirtualDisk, DiskDir } from '../types/models';

export const diskService = {
  /** 管理员获取全部磁盘列表 */
  async listAllDisks(): Promise<VirtualDisk[]> {
    const resp = await httpClient.get<{ disks: VirtualDisk[] }>('/api/v1/disks/list');
    return resp.disks;
  },

  /** 创建磁盘（admin） */
  async createDisk(name: string, path: string): Promise<void> {
    await httpClient.post('/api/v1/disks/create', { name, path });
  },

  /** 更新磁盘（admin） */
  async updateDisk(diskId: number, name: string, path: string): Promise<void> {
    await httpClient.post('/api/v1/disks/update', {
      disk_id: diskId,
      name,
      path,
    });
  },

  /** 删除磁盘（admin） */
  async deleteDisk(diskId: number): Promise<void> {
    await httpClient.post('/api/v1/disks/delete', { disk_id: diskId });
  },

  /** 浏览磁盘内子目录（用户+admin 通用） */
  async browseDirs(diskId: number, path: string): Promise<DiskDir[]> {
    const resp = await httpClient.post<{ dirs: DiskDir[] }>(
      '/api/v1/disks/browse-dirs',
      { disk_id: diskId, path },
    );
    return resp.dirs;
  },
};

/**
 * 虚拟磁盘管理服务（admin + 用户通用）
 *
 * 对标桌面端 src/services/disk_service.py。
 * 接口路径与后端 virtual_disk_controller.py（prefix /api/v1/virtual-disks）保持一致：
 *   - GET/POST  /api/v1/virtual-disks                   列表 / 创建（body: name, real_path）
 *   - PUT       /api/v1/virtual-disks/{disk_id}         更新（body: name, real_path）
 *   - DELETE    /api/v1/virtual-disks/{disk_id}         删除
 *   - GET       /api/v1/virtual-disks/{disk_id}/browse  浏览子目录（body: path）
 * 只读接口（list/browse）走加密 GET + URL query 信封（浏览器 GET 无法携带 body）。
 */

import { httpClient } from '../utils/http-client';
import type { VirtualDisk, DiskDir } from '../types/models';

export const diskService = {
  /** 管理员获取全部磁盘列表 */
  async listAllDisks(): Promise<VirtualDisk[]> {
    const resp = await httpClient.get<{ disks: VirtualDisk[] }>(
      '/api/v1/virtual-disks',
    );
    return resp.disks;
  },

  /** 创建磁盘（admin） */
  async createDisk(name: string, realPath: string): Promise<void> {
    await httpClient.post('/api/v1/virtual-disks', {
      name,
      real_path: realPath,
    });
  },

  /** 更新磁盘（admin） */
  async updateDisk(diskId: number, name: string, realPath: string): Promise<void> {
    await httpClient.put(`/api/v1/virtual-disks/${diskId}`, {
      name,
      real_path: realPath,
    });
  },

  /** 删除磁盘（admin） */
  async deleteDisk(diskId: number): Promise<void> {
    await httpClient.delete(`/api/v1/virtual-disks/${diskId}`);
  },

  /** 浏览磁盘内子目录（用户+admin 通用，服务端为 GET 只读接口） */
  async browseDirs(diskId: number, path: string): Promise<DiskDir[]> {
    const resp = await httpClient.get<{ dirs: DiskDir[] }>(
      `/api/v1/virtual-disks/${diskId}/browse`,
      { path },
    );
    return resp.dirs;
  },
};

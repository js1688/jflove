/**
 * 权限配置服务（admin only）
 *
 * 对标桌面端 src/services/permission_service.py。
 */

import { httpClient } from '../utils/http-client';
import type { DiskPermission } from '../types/models';

export const permissionService = {
  /** 获取权限列表 */
  async listPermissions(): Promise<DiskPermission[]> {
    const resp = await httpClient.get<{ permissions: DiskPermission[] }>(
      '/api/v1/permissions/list',
    );
    return resp.permissions;
  },

  /** 设置权限 */
  async setPermission(
    userId: number,
    diskId: number,
    canRead: boolean,
    canWrite: boolean,
  ): Promise<void> {
    await httpClient.post('/api/v1/permissions/set', {
      user_id: userId,
      disk_id: diskId,
      can_read: canRead,
      can_write: canWrite,
    });
  },

  /** 删除权限 */
  async deletePermission(userId: number, diskId: number): Promise<void> {
    await httpClient.post('/api/v1/permissions/delete', {
      user_id: userId,
      disk_id: diskId,
    });
  },
};

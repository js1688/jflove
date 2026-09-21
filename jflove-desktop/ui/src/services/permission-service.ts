/**
 * 权限配置服务（admin only）
 *
 * 对标桌面端 src/services/permission_service.py。
 * 接口路径与后端 permission_controller.py 保持一致：
 *   - GET/POST  /api/v1/permissions/users/{user_id}/disks            列表 / 设置
 *   - DELETE    /api/v1/permissions/users/{user_id}/disks/{disk_id}  删除
 * 注：http-client.get 内部用 POST 发送。
 */

import { httpClient } from '../utils/http-client';
import type { DiskPermission } from '../types/models';

export const permissionService = {
  /** 获取指定用户的磁盘权限列表 */
  async listPermissions(userId: number): Promise<DiskPermission[]> {
    const resp = await httpClient.get<{ permissions: DiskPermission[] }>(
      `/api/v1/permissions/users/${userId}/disks`,
    );
    return resp.permissions;
  },

  /** 设置权限 */
  async setPermission(
    userId: number,
    diskId: number,
    canRead: boolean,
    canWrite: boolean,
    canDelete = false,
  ): Promise<void> {
    await httpClient.post(
      `/api/v1/permissions/users/${userId}/disks/${diskId}`,
      {
        can_read: canRead,
        can_write: canWrite,
        can_delete: canDelete,
      },
    );
  },

  /** 删除权限 */
  async deletePermission(userId: number, diskId: number): Promise<void> {
    await httpClient.delete(
      `/api/v1/permissions/users/${userId}/disks/${diskId}`,
    );
  },
};

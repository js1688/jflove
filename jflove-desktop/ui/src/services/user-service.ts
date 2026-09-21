/**
 * 用户管理服务（admin only）
 *
 * 对标桌面端 src/services/user_service.py。
 * 接口路径与后端 user_controller.py 保持一致：
 *   - GET/POST  /api/v1/users                   列表 / 创建
 *   - DELETE    /api/v1/users/{user_id}         删除
 *   - PUT       /api/v1/users/{user_id}/password 修改密码（body: password）
 *   - PUT       /api/v1/users/{user_id}/enabled  启用/禁用（body: enabled）
 * 注：http-client.get 内部用 POST 发送。
 */

import { httpClient } from '../utils/http-client';
import type { User } from '../types/models';

export const userService = {
  /** 获取用户列表 */
  async listUsers(): Promise<User[]> {
    const resp = await httpClient.get<{ users: User[] }>('/api/v1/users');
    return resp.users;
  },

  /** 创建用户 */
  async createUser(username: string, password: string): Promise<void> {
    await httpClient.post('/api/v1/users', {
      username,
      password,
    });
  },

  /** 修改用户密码 */
  async changePassword(userId: number, newPassword: string): Promise<void> {
    await httpClient.put(`/api/v1/users/${userId}/password`, {
      password: newPassword,
    });
  },

  /** 删除用户 */
  async deleteUser(userId: number): Promise<void> {
    await httpClient.delete(`/api/v1/users/${userId}`);
  },

  /** 启用/禁用用户 */
  async setEnabled(userId: number, enabled: boolean): Promise<void> {
    await httpClient.put(`/api/v1/users/${userId}/enabled`, {
      enabled,
    });
  },
};

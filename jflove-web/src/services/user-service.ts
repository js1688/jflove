/**
 * 用户管理服务（admin only）
 *
 * 对标桌面端 src/services/user_service.py。
 */

import { httpClient } from '../utils/http-client';
import type { User } from '../types/models';

export const userService = {
  /** 获取用户列表 */
  async listUsers(): Promise<User[]> {
    const resp = await httpClient.get<{ users: User[] }>('/api/v1/users/list');
    return resp.users;
  },

  /** 创建用户 */
  async createUser(username: string, password: string): Promise<void> {
    await httpClient.post('/api/v1/users/create', {
      username,
      password,
    });
  },

  /** 修改用户密码 */
  async changePassword(userId: number, newPassword: string): Promise<void> {
    await httpClient.post('/api/v1/users/change-password', {
      user_id: userId,
      new_password: newPassword,
    });
  },

  /** 删除用户 */
  async deleteUser(userId: number): Promise<void> {
    await httpClient.post('/api/v1/users/delete', {
      user_id: userId,
    });
  },

  /** 启用/禁用用户 */
  async setEnabled(userId: number, enabled: boolean): Promise<void> {
    await httpClient.post('/api/v1/users/set-enabled', {
      user_id: userId,
      enabled,
    });
  },
};

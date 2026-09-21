/**
 * 媒体修复服务（v1.4.2）
 *
 * 封装手动离线修复的任务管理接口（与后端 repair_controller 对应）：
 *   - create：创建修复任务（健康文件被服务端拒绝：400 无需修复）
 *   - list：分页任务列表（全平台共享，所有登录用户可见）
 *   - cancel / override / deleteArtifact / deleteRecord
 *
 * 说明：服务端对操作类接口统一校验磁盘写+删权限；
 * 只读账号可看列表但操作会 403（UI 按磁盘权限禁用按钮）。
 */

import { httpClient } from '../utils/http-client';

/** 修复任务（服务端 media_repair_tasks 行） */
export interface RepairTask {
  id: number;
  username: string;
  disk_id: number;
  filename: string;
  /** pending/running/verifying/success/failed/canceled/overridden */
  status: string;
  progress: number;
  error_message: string;
  source_size: number;
  output_name: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export const repairService = {
  /** 创建媒体修复任务（右键菜单 / 播放失败弹窗「立即修复」） */
  async create(diskId: number, path: string, filename: string): Promise<{ task_id: number; message: string }> {
    return httpClient.post('/api/v1/files/repair/create', {
      disk_id: diskId,
      path,
      filename,
    });
  },

  /** 分页获取修复任务列表（全平台共享） */
  async list(page = 1, pageSize = 50): Promise<{ total: number; tasks: RepairTask[] }> {
    return httpClient.get('/api/v1/files/repair/tasks', { page, page_size: pageSize });
  },

  /** 取消排队中/执行中的任务（执行中会终止 ffmpeg 并清理半成品） */
  async cancel(taskId: number): Promise<{ message: string }> {
    return httpClient.post('/api/v1/files/repair/cancel', { task_id: taskId });
  },

  /** 覆盖原文件（原损坏文件被直接删除、不留备份，调用前必须二次确认） */
  async override(taskId: number): Promise<{ message: string }> {
    return httpClient.post('/api/v1/files/repair/override', { task_id: taskId });
  },

  /** 删除修复成功但尚未覆盖的产物 */
  async deleteArtifact(taskId: number): Promise<{ message: string }> {
    return httpClient.post('/api/v1/files/repair/delete-artifact', { task_id: taskId });
  },

  /** 删除终态任务记录（软删除，不影响磁盘产物） */
  async deleteRecord(taskId: number): Promise<{ message: string }> {
    return httpClient.post('/api/v1/files/repair/delete-record', { task_id: taskId });
  },
};

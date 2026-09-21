/**
 * 笔记管理服务
 *
 * 对标桌面端 src/services/note_service.py。
 * 接口路径/字段与后端 note_controller.py 保持一致：
 *   - GET/POST  /api/v1/notes/list       笔记列表
 *   - GET/POST  /api/v1/notes/read       读取内容（body: filename）
 *   - POST      /api/v1/notes/write      新建或覆盖（body: filename, content）
 *   - DELETE    /api/v1/notes/           删除（body: filename）
 *   - POST      /api/v1/notes/rename     重命名（body: old_name, new_name）
 *   - GET/POST  /api/v1/notes/disk-config 获取笔记磁盘配置
 *   - PUT       /api/v1/notes/disk-config 设置笔记磁盘配置
 * 注：http-client.get 内部用 POST 发送（浏览器 GET 不能携带 body）。
 */

import { httpClient } from '../utils/http-client';
import type { Note } from '../types/models';

export const noteService = {
  /** 获取笔记列表 */
  async listNotes(): Promise<Note[]> {
    const resp = await httpClient.get<{ notes: Note[] }>('/api/v1/notes/list');
    return resp.notes;
  },

  /** 获取笔记内容 */
  async getNote(filename: string): Promise<string> {
    const resp = await httpClient.get<{ content: string }>(
      '/api/v1/notes/read',
      { filename },
    );
    return resp.content;
  },

  /** 保存笔记（覆盖已有内容） */
  async saveNote(filename: string, content: string): Promise<void> {
    await httpClient.post('/api/v1/notes/write', {
      filename,
      content,
    });
  },

  /** 创建笔记（后端通过 write 接口新建，空内容） */
  async createNote(filename: string): Promise<void> {
    await httpClient.post('/api/v1/notes/write', {
      filename,
      content: '',
    });
  },

  /** 重命名笔记 */
  async renameNote(oldFilename: string, newFilename: string): Promise<void> {
    await httpClient.post('/api/v1/notes/rename', {
      old_name: oldFilename,
      new_name: newFilename,
    });
  },

  /** 删除笔记 */
  async deleteNote(filename: string): Promise<void> {
    await httpClient.delete('/api/v1/notes/', {
      filename,
    });
  },

  /** 获取当前用户的笔记磁盘配置 */
  async getNotesDiskConfig(): Promise<{
    disk_id: number | null;
    path: string;
    disks: { id: number; name: string }[];
  }> {
    return httpClient.get('/api/v1/notes/disk-config');
  },

  /** 设置当前用户的笔记磁盘配置 */
  async setNotesDiskConfig(diskId: number | null, path = ''): Promise<void> {
    await httpClient.put('/api/v1/notes/disk-config', {
      disk_id: diskId,
      path,
    });
  },
};

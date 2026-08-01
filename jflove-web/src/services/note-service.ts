/**
 * 笔记管理服务
 *
 * 对标桌面端 src/services/note_service.py。
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
    const resp = await httpClient.post<{ content: string }>(
      '/api/v1/notes/get',
      { filename },
    );
    return resp.content;
  },

  /** 保存笔记 */
  async saveNote(filename: string, content: string): Promise<void> {
    await httpClient.post('/api/v1/notes/save', {
      filename,
      content,
    });
  },

  /** 创建笔记 */
  async createNote(filename: string): Promise<void> {
    await httpClient.post('/api/v1/notes/create', {
      filename,
    });
  },

  /** 重命名笔记 */
  async renameNote(oldFilename: string, newFilename: string): Promise<void> {
    await httpClient.post('/api/v1/notes/rename', {
      old_filename: oldFilename,
      new_filename: newFilename,
    });
  },

  /** 删除笔记 */
  async deleteNote(filename: string): Promise<void> {
    await httpClient.post('/api/v1/notes/delete', {
      filename,
    });
  },
};

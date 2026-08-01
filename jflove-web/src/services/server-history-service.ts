/**
 * 服务端地址历史服务（纯本地 localStorage 操作）
 *
 * 对标桌面端 src/services/server_history_service.py。
 */

import { MAX_SERVER_HISTORY, DEFAULT_SERVER_URL } from '../config/constants';

const STORAGE_KEY = 'jflove_server_history';

export const serverHistoryService = {
  /** 获取历史地址列表（最近使用的在前） */
  listHistory(): string[] {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      return JSON.parse(raw) as string[];
    } catch {
      return [];
    }
  },

  /** 获取默认地址（历史首项，无历史则兜底） */
  getDefault(): string {
    const history = this.listHistory();
    return history[0] || DEFAULT_SERVER_URL;
  },

  /** 记录成功连接的地址（去重 + 置顶 + 限 10 条） */
  record(url: string): void {
    const normalized = url.replace(/\/+$/, '');
    const history = this.listHistory().filter(
      (h) => h.replace(/\/+$/, '') !== normalized,
    );
    history.unshift(normalized);
    if (history.length > MAX_SERVER_HISTORY) {
      history.length = MAX_SERVER_HISTORY;
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
  },

  /** 删除某条历史记录 */
  delete(url: string): void {
    const normalized = url.replace(/\/+$/, '');
    const history = this.listHistory().filter(
      (h) => h.replace(/\/+$/, '') !== normalized,
    );
    localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
  },
};

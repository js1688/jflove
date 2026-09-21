/**
 * 服务端地址历史服务（纯本地 localStorage 操作）
 *
 * 对标桌面端 src/services/server_history_service.py。
 */

import { MAX_SERVER_HISTORY, DEFAULT_SERVER_URL } from '../config/constants';

const STORAGE_KEY = 'jflove_server_history';

/**
 * 规范化服务端地址：
 *  - 去除首尾空白与尾部斜杠
 *  - **空串原样返回**：v1.5.0 起空串表示「同源」（请求走相对路径 `/api/...`，
 *    由 nginx / dev proxy 反代到后端）。这能避免跨源 CORS 预检 —— 否则每次
 *    业务调用都会先发一条 `OPTIONS`，服务端收到的 HTTP 条数翻倍
 *  - 未带协议（如 `127.0.0.1:8989`）时自动补全 `http://`，
 *    否则会被当成相对路径发往当前站点（那是"同源"，不是用户想要的地址）
 */
export function normalizeServerUrl(input: string): string {
  let url = input.trim();
  if (!url) return url;
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(url)) {
    url = `http://${url}`;
  }
  return url.replace(/\/+$/, '');
}

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

  /** 记录成功连接的地址（去重 + 置顶 + 限 10 条）。空串（同源）不入历史 */
  record(url: string): void {
    const normalized = url.replace(/\/+$/, '');
    // 同源模式（空串）没有"地址"可记，也不需要出现在历史列表里
    if (!normalized) return;
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

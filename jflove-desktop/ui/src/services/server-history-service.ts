/**
 * 服务端地址历史服务（桌面端）
 *
 * 与 Web 端的差异：历史的**权威存储在 Python**
 * （`src/services/server_history_service.py` → `%APPDATA%\JFLove\storage\server_history.json`），
 * 而不是浏览器 localStorage —— 桌面端换设备/清缓存不应丢历史，且这类偏好
 * 本来就属于桌面端已有的本地状态。
 *
 * 因此本模块在启动时把历史**拉进内存缓存**，之后提供同步读取接口，
 * 以保持与 Web 端完全一致的调用方式（`listHistory()` / `getDefault()` 都是同步的）。
 */

import { DEFAULT_DESKTOP_SERVER_URL } from '../config/desktop';
import { callBridge } from '../utils/desktop-bridge';

let cache: string[] = [];

/**
 * 规范化服务端地址（与 Web 端同实现）：
 *  - 去除首尾空白与尾部斜杠
 *  - 未带协议（如 `127.0.0.1:8989`）时自动补全 `http://`
 *
 * 注意：Web 端把**空串**当作「同源」；桌面端不存在同源，空串会在
 * `authService.keyExchange` 里由 Python 回落到默认地址。
 */
export function normalizeServerUrl(input: string): string {
  let url = input.trim();
  if (!url) return url;
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(url)) {
    url = `http://${url}`;
  }
  return url.replace(/\/+$/, '');
}

/** 从 Python 拉取历史地址（启动时调用一次） */
export async function loadServerHistory(): Promise<string[]> {
  try {
    cache = await callBridge<string[]>('meta', 'server_history', {});
  } catch {
    cache = [];
  }
  return cache;
}

export const serverHistoryService = {
  /** 历史地址列表（最近使用的在前）—— 同步读内存缓存 */
  listHistory(): string[] {
    return cache;
  },

  /** 默认地址（历史首项，无历史则用桌面端默认值） */
  getDefault(): string {
    return cache[0] || DEFAULT_DESKTOP_SERVER_URL;
  },

  /** 记录成功连接的地址（Python 侧去重、置顶、限 10 条） */
  record(url: string): void {
    const normalized = url.replace(/\/+$/, '');
    if (!normalized) return;
    void callBridge('meta', 'server_history_record', { url: normalized })
      .then(() => loadServerHistory())
      .catch(() => {
        // 记录失败不影响本次连接
      });
  },

  /** 删除某条历史记录 */
  delete(url: string): void {
    void callBridge('meta', 'server_history_delete', { url })
      .then(() => loadServerHistory())
      .catch(() => {
        // 忽略
      });
  },
};

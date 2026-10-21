/**
 * 离线下载服务（v1.8.0 新增）
 *
 * 封装后端 `offline_download_controller`（前缀 `/api/v1/offline`）的 10 个接口，
 * 字段与设计文档 §2.2 严格一致：
 *   1. analyze        POST  /analyze         { url }
 *   2. listTasks      GET   /tasks           { page, page_size }
 *   3. detail         GET   /detail          { task_id }
 *   4. select         POST  /select          { task_id, item_ids, disk_id, target_path, pack_zip }
 *   5. nextPage       POST  /next-page       { task_id }
 *   6. cancel         POST  /cancel          { task_id }
 *   7. retry          POST  /retry           { task_id }
 *   8. deleteRecord   POST  /delete-record   { task_id }
 *   9. meta           GET   /meta            —
 *  10. saveSettings   POST  /settings        { disk_id, rel_path, create }
 *
 * 约定（安全宪法 §9）：
 *   - 全部接口走 `http-client.ts` 的加密信封，**不新增任何明文接口 / 不新增加解密代码**；
 *   - GET 用 `httpClient.get(path, body)`（浏览器 GET 无 body，信封放 URL query 的密文里）；
 *   - POST 用 `httpClient.post(path, body)`；
 *   - `token` 由 http-client 注入加密 body，本层不接触；
 *   - `user_id` **永不**由客户端传（服务端只认 token 解析出的账号）。
 */

import { httpClient } from '../utils/http-client';
import type {
  OfflineAnalyzeResult,
  OfflineMeta,
  OfflineNextPageResult,
  OfflineSelectResult,
  OfflineSettingsResult,
  OfflineTaskDetail,
  OfflineTaskList,
} from '../types/models';

/** 任务列表单页条数（后端 page_size 上限 100） */
export const OFFLINE_PAGE_SIZE = 50;

/** `POST /offline/select` 入参 */
export interface OfflineSelectParams {
  taskId: number;
  itemIds: number[];
  /** 虚拟磁盘 id；`0` 表示沿用账号默认配置（服务端回退） */
  diskId: number;
  /** 磁盘内相对路径；空串同样触发服务端回退到账号默认目录 */
  targetPath: string;
  /** 图片是否打包为 zip */
  packZip?: boolean;
}

export const offlineDownloadService = {
  /** 1. 创建分析任务：粘贴网址 → 服务端异步分析（SSRF / 总开关 / 目录前置校验在服务端） */
  async analyze(url: string): Promise<OfflineAnalyzeResult> {
    return httpClient.post<OfflineAnalyzeResult>('/api/v1/offline/analyze', { url });
  },

  /** 2. 任务列表（**按账号隔离**，只返回自己的任务） */
  async listTasks(page = 1, pageSize = OFFLINE_PAGE_SIZE): Promise<OfflineTaskList> {
    return httpClient.get<OfflineTaskList>('/api/v1/offline/tasks', {
      page,
      page_size: pageSize,
    });
  },

  /** 3. 任务详情 + 清单（他人任务服务端按 404 处理，不泄露存在性） */
  async detail(taskId: number): Promise<OfflineTaskDetail> {
    return httpClient.get<OfflineTaskDetail>('/api/v1/offline/detail', { task_id: taskId });
  },

  /** 4. 提交勾选并开始下载（磁盘写权限 / 路径 / 余量 / 体积上限由服务端校验） */
  async select(params: OfflineSelectParams): Promise<OfflineSelectResult> {
    return httpClient.post<OfflineSelectResult>('/api/v1/offline/select', {
      task_id: params.taskId,
      item_ids: params.itemIds,
      disk_id: params.diskId,
      target_path: params.targetPath,
      pack_zip: params.packZip ?? false,
    });
  },

  /** 5. 图片追加下一页（仅 `ready` 状态可调用；页数上限由服务端守） */
  async nextPage(taskId: number): Promise<OfflineNextPageResult> {
    return httpClient.post<OfflineNextPageResult>('/api/v1/offline/next-page', {
      task_id: taskId,
    });
  },

  /** 6. 取消任务（终态拒绝，服务端 409） */
  async cancel(taskId: number): Promise<{ task_id: number; status: string }> {
    return httpClient.post('/api/v1/offline/cancel', { task_id: taskId });
  },

  /** 7. 重试任务（DRM / 需登录 / 体积超限等不可重试类由服务端 409 拒绝） */
  async retry(taskId: number): Promise<{ task_id: number; status: string }> {
    return httpClient.post('/api/v1/offline/retry', { task_id: taskId });
  },

  /** 8. 删除任务记录（**不删磁盘文件**，只软删任务行） */
  async deleteRecord(taskId: number): Promise<{ task_id: number; deleted: boolean }> {
    return httpClient.post('/api/v1/offline/delete-record', { task_id: taskId });
  },

  /** 9. 初始化元信息（只含**自己**的默认目录、可写磁盘与全局策略） */
  async meta(): Promise<OfflineMeta> {
    return httpClient.get<OfflineMeta>('/api/v1/offline/meta');
  },

  /**
   * 10. 保存 / 清除「我的」默认目录
   *
   * @param diskId   虚拟磁盘 id；传 `0` 表示**清除**配置
   * @param relPath  磁盘内相对路径
   * @param create   目录不存在时是否在该磁盘内创建
   */
  async saveSettings(
    diskId: number,
    relPath: string,
    create = false,
  ): Promise<OfflineSettingsResult> {
    return httpClient.post<OfflineSettingsResult>('/api/v1/offline/settings', {
      disk_id: diskId,
      rel_path: relPath,
      create,
    });
  },
};

// 类型再导出：页面/组件只 import 本服务即可拿到全部离线下载类型
export type {
  OfflineAnalyzeResult,
  OfflineConfig,
  OfflineItem,
  OfflineItemKind,
  OfflineMeta,
  OfflineNextPageResult,
  OfflineSelectResult,
  OfflineSettingsResult,
  OfflineTask,
  OfflineTaskDetail,
  OfflineTaskList,
  OfflineTaskStatus,
  OfflineUnusableReason,
  OfflineWritableDisk,
} from '../types/models';

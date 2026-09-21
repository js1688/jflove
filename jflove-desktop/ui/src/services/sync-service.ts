/**
 * 同步服务（桌面端专有，N9）
 *
 * Web 端没有这个能力（浏览器沙箱碰不到本地文件系统），因此没有可复制的参考实现；
 * 本文件把原桌面端的 `sync_service` / `sync_engine` 经桥暴露给渲染层。
 *
 * 线程与真相归属：
 * - **规则存在本地 JSON**（`sync_service` 负责），不在服务端；
 * - **同步执行在 Python**（`sync_engine`，QThread），渲染层只发指令、看事件。
 */
import { callBridge } from '../utils/desktop-bridge';

/** 一条同步规则（字段与 `sync_service.create_config` 一致） */
export interface SyncConfig {
  id: string;
  name: string;
  local_path: string;
  disk_id: number;
  remote_path: string;
  auto_sync: boolean;
  sync_interval: number;
  enabled: boolean;
  last_synced_at: string | null;
}

/** 新建/更新规则时提交的字段 */
export interface SyncConfigInput {
  name: string;
  local_path: string;
  disk_id: number;
  remote_path: string;
  auto_sync: boolean;
  sync_interval: number;
  enabled?: boolean;
}

/** 一次同步的结果（`SyncResult` 的 JSON 形态） */
export interface SyncResult {
  config_id?: string;
  uploaded?: number;
  downloaded?: number;
  deleted_local?: number;
  deleted_remote?: number;
  skipped?: number;
  errors?: string[];
  duration_ms?: number;
  total_actions?: number;
}

export const syncService = {
  /** 列出全部同步规则 */
  async list(): Promise<SyncConfig[]> {
    const resp = await callBridge<{ configs: SyncConfig[] }>('sync', 'list', {});
    return resp.configs ?? [];
  },

  /** 新建规则，返回规则 id */
  async create(input: SyncConfigInput): Promise<string> {
    const resp = await callBridge<{ id: string }>('sync', 'create', { ...input });
    return resp.id;
  },

  /** 更新规则 */
  async update(configId: string, input: SyncConfigInput): Promise<void> {
    await callBridge('sync', 'update', { config_id: configId, ...input });
  },

  /** 删除规则 */
  async remove(configId: string): Promise<void> {
    await callBridge('sync', 'delete', { config_id: configId });
  },

  /** 取远端目录快照（预览将同步的内容） */
  async snapshot(diskId: number, remotePath: string): Promise<Record<string, unknown>[]> {
    const resp = await callBridge<{ entries: Record<string, unknown>[] }>('sync', 'snapshot', {
      disk_id: diskId,
      remote_path: remotePath,
    });
    return resp.entries ?? [];
  },

  /** 立即同步一次（引擎内部起线程，不阻塞界面） */
  async run(configId: string): Promise<boolean> {
    const resp = await callBridge<{ started: boolean }>('sync', 'run', {
      config_id: configId,
    });
    return Boolean(resp.started);
  },

  /** 规则变更后让引擎重载（自动同步定时器据此更新） */
  async reload(): Promise<void> {
    await callBridge('sync', 'reload', {});
  },
};

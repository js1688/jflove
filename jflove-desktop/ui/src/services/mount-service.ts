/**
 * 云盘挂载服务（v1.7.0）
 *
 * 渲染层唯一的挂载能力入口：经 `callBridge('mount', ...)` 调 Python 侧
 * `src/bridge/mount.py`。文件字节不进入渲染层——下载由 Python 侧
 * `transfer_manager` 完成，这里只投递任务与读状态。
 */
import { callBridge } from '../utils/desktop-bridge';

/** 挂载点状态统计 */
export interface MountStatus {
  total: number;
  cached: number;
  online: number;
  local_path: string;
}

/** 挂载点 */
export interface MountInfo {
  id: string;
  name: string;
  disk_id: number;
  remote_path: string;
  local_path: string;
  enabled: boolean;
  migrated?: boolean;
  status: MountStatus;
}

/** 挂载点内的文件（含缓存状态） */
export interface MountFile {
  rel_path: string;
  size: number;
  mtime: number;
  is_dir: boolean;
  cached: boolean;
  local_path: string;
}

/** 新建 / 编辑挂载点的输入 */
export interface MountInput {
  name: string;
  disk_id: number;
  remote_path: string;
  local_path: string;
}

export const mountService = {
  /** 列出全部挂载点（含状态；首次调用会触发旧同步配置迁移） */
  list: async (): Promise<MountInfo[]> => {
    const resp = await callBridge<{ mounts: MountInfo[] }>('mount', 'list', {});
    return resp.mounts ?? [];
  },

  /** 新建挂载点（默认关闭） */
  create: async (input: MountInput): Promise<{ id: string }> =>
    callBridge<{ id: string }>('mount', 'create', { ...input }),

  /** 更新挂载点 */
  update: async (mountId: string, input: MountInput): Promise<void> => {
    await callBridge('mount', 'update', { mount_id: mountId, ...input });
  },

  /** 删除挂载点（不动本地文件与远端） */
  remove: async (mountId: string): Promise<void> => {
    await callBridge('mount', 'delete', { mount_id: mountId });
  },

  /** 启用 / 停用 */
  setEnabled: async (mountId: string, enabled: boolean): Promise<void> => {
    await callBridge('mount', 'set_enabled', { mount_id: mountId, enabled });
  },

  /** 拉增量并更新索引 */
  refresh: (mountId: string): Promise<{ applied: number; deleted: number; latest_rev: number }> =>
    callBridge('mount', 'refresh', { mount_id: mountId }),

  /** 全量对账 */
  reconcile: (mountId: string): Promise<{ applied: number; deleted: number; latest_rev: number }> =>
    callBridge('mount', 'reconcile', { mount_id: mountId }),

  /** 列出文件与缓存状态 */
  files: async (mountId: string): Promise<MountFile[]> => {
    const resp = await callBridge<{ files: MountFile[] }>('mount', 'files', {
      mount_id: mountId,
    });
    return resp.files ?? [];
  },

  /** 下载到本地缓存目录 */
  download: (mountId: string, relPath: string): Promise<{ task_id: string; local_path: string }> =>
    callBridge('mount', 'download', { mount_id: mountId, rel_path: relPath }),

  /** 释放本地缓存 */
  release: (mountId: string, relPath: string): Promise<{ removed: boolean }> =>
    callBridge('mount', 'release', { mount_id: mountId, rel_path: relPath }),
};

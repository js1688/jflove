/**
 * 云盘挂载服务（v1.7.0）
 *
 * 渲染层唯一的挂载能力入口：经 `callBridge('mount', ...)` 调 Python 侧
 * `src/bridge/mount.py`。文件字节不进入渲染层——下载由 Python 侧
 * `transfer_manager` 完成，这里只投递任务与读状态。
 */
import { callBridge } from '../utils/desktop-bridge';

/** 正在传输 / 失败的文件（供界面显示"在传哪个文件"） */
export interface MountTransfer {
  rel_path: string;
  direction: 'upload' | 'download';
  /** 0-100 */
  progress: number;
  size: number;
  /** 失败原因（仅 state=failed 时非空） */
  error: string;
  /** 无进展秒数（0 表示正常推进）；>0 用于提示「可能卡住」 */
  stalled: number;
  /** 当前阶段已耗时秒数（仅「服务端处理中」有意义：服务端无法上报百分比） */
  elapsed: number;
}

/** 挂载点状态统计 */
export interface MountStatus {
  total: number;
  /** 已同步（本地有实体且与远端一致） */
  synced: number;
  /** 兼容旧字段：等于 synced */
  cached: number;
  /** 在线（本地无实体，读取时自动下载） */
  online: number;
  /** 待上传（本地有改动，排队中） */
  pending: number;
  /** 上传前校验（SHA256）中 */
  hashing: number;
  uploading: number;
  /** 服务端合并 + 校验中（分片已传完） */
  finalizing: number;
  downloading: number;
  /** 失败（30 秒后自动重试） */
  failed: number;
  local_path: string;
  /** 正在传输 + 失败的文件（最多 20 条） */
  active: MountTransfer[];
  active_total: number;
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

/** 文件同步状态 */
export type MountFileState =
  | 'synced'
  | 'pending'
  | 'hashing'
  | 'uploading'
  | 'finalizing'
  | 'downloading'
  | 'failed'
  | 'online';

/** 挂载点内的文件（含同步状态） */
export interface MountFile {
  rel_path: string;
  size: number;
  mtime: number;
  is_dir: boolean;
  cached: boolean;
  /** 见 MountFileState 注释 */
  state: MountFileState;
  /** 传输方向（仅 uploading / downloading / pending / failed 有值） */
  direction: 'upload' | 'download' | '';
  /** 传输中进度（0-100） */
  progress: number;
  /** 失败原因（仅 state=failed） */
  error: string;
  /** 无进展秒数（0 = 正常） */
  stalled: number;
  /** 当前阶段已耗时秒数（仅 finalizing 有意义） */
  elapsed: number;
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

  /** 立即与远端核对一次（平时由自动同步完成） */
  refresh: (mountId: string): Promise<{ applied: number; deleted: number; latest_rev: number }> =>
    callBridge('mount', 'refresh', { mount_id: mountId }),

  /** 全量重扫（自动同步在游标失效时会自行调用，界面不再单独暴露） */
  reconcile: (mountId: string): Promise<{ applied: number; deleted: number; latest_rev: number }> =>
    callBridge('mount', 'reconcile', { mount_id: mountId }),

  /** 列出文件与缓存状态 */
  files: async (mountId: string): Promise<MountFile[]> => {
    const resp = await callBridge<{ files: MountFile[] }>('mount', 'files', {
      mount_id: mountId,
    });
    return resp.files ?? [];
  },

  /** 预取到本地（读取挂载目录时会自动触发，界面不再暴露入口） */
  download: (mountId: string, relPath: string): Promise<{ task_id: string; local_path: string }> =>
    callBridge('mount', 'download', { mount_id: mountId, rel_path: relPath }),

  /** 释放本地缓存（可在系统文件管理器里直接删除，效果相同） */
  release: (mountId: string, relPath: string): Promise<{ removed: boolean }> =>
    callBridge('mount', 'release', { mount_id: mountId, rel_path: relPath }),
};

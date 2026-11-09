/**
 * 传输任务状态管理
 *
 * 对标桌面端 src/utils/transfer_manager.py。
 */

import { create } from 'zustand';
import type { TransferTask, TaskStatus } from '../types/models';

interface TransferState {
  tasks: TransferTask[];
  // 统计：total 总数 / running 进行中（含 pending/hashing/running）/ pending 等待中 / completed 已完成 / failed 失败 / cancelled 已取消
  stats: {
    total: number; running: number; pending: number;
    completed: number; failed: number; cancelled: number;
  };

  addTask: (task: TransferTask) => void;
  updateTask: (id: string, updates: Partial<TransferTask>) => void;
  cancelTask: (id: string) => void;
  removeTask: (id: string) => void;
  clearFinished: () => void;

  /**
   * 注册「用户取消」回调（v1.9.0）。
   *
   * 上传/下载任务持有 AbortController；取消时先中止在途请求，再由上传服务决定是否
   * DELETE 服务端会话（下载则保留本地 part）。回调不进 Zustand 状态（保持状态可序列化），
   * 返回值用于任务结束后注销。
   */
  registerCancelHandler: (id: string, handler: () => void) => () => void;

  /** 生成唯一任务 ID */
  generateTaskId: () => string;
}

let taskCounter = 0;

/** 任务取消回调表（模块级，不进 store 状态） */
const cancelHandlers = new Map<string, () => void>();

export const useTransferStore = create<TransferState>((set) => ({
  tasks: [],
  stats: { total: 0, running: 0, pending: 0, completed: 0, failed: 0, cancelled: 0 },

  addTask: (task) => {
    set(s => {
      const tasks = [task, ...s.tasks];
      return { tasks, stats: computeStats(tasks) };
    });
  },

  updateTask: (id, updates) => {
    set(s => {
      const tasks = s.tasks.map(t =>
        t.id === id ? { ...t, ...updates } : t,
      );
      return { tasks, stats: computeStats(tasks) };
    });
  },

  cancelTask: (id) => {
    const handler = cancelHandlers.get(id);
    if (handler) {
      cancelHandlers.delete(id);
      try {
        handler();
      } catch {
        // 取消失败不应阻塞 UI 状态更新
      }
    }
    set(s => {
      const tasks = s.tasks.map(t =>
        t.id === id ? { ...t, status: 'cancelled' as TaskStatus } : t,
      );
      return { tasks, stats: computeStats(tasks) };
    });
  },

  removeTask: (id) => {
    cancelHandlers.delete(id);
    set(s => {
      const tasks = s.tasks.filter(t => t.id !== id);
      return { tasks, stats: computeStats(tasks) };
    });
  },

  clearFinished: () => {
    set(s => {
      const tasks = s.tasks.filter(
        t => t.status === 'pending' || t.status === 'hashing' || t.status === 'running',
      );
      for (const task of s.tasks) {
        if (!tasks.some(keep => keep.id === task.id)) cancelHandlers.delete(task.id);
      }
      return { tasks, stats: computeStats(tasks) };
    });
  },

  registerCancelHandler: (id, handler) => {
    cancelHandlers.set(id, handler);
    return () => {
      if (cancelHandlers.get(id) === handler) cancelHandlers.delete(id);
    };
  },

  generateTaskId: () => {
    taskCounter += 1;
    return `task-${Date.now()}-${taskCounter}`;
  },
}));

function computeStats(tasks: TransferTask[]) {
  return {
    total: tasks.length,
    running: tasks.filter(t =>
      t.status === 'pending' || t.status === 'hashing' || t.status === 'running',
    ).length,
    pending: tasks.filter(t => t.status === 'pending').length,
    // 已完成：仅 completed 态
    completed: tasks.filter(t => t.status === 'completed').length,
    failed: tasks.filter(t => t.status === 'failed').length,
    cancelled: tasks.filter(t => t.status === 'cancelled').length,
  };
}

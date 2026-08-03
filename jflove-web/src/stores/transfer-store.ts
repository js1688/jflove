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

  /** 生成唯一任务 ID */
  generateTaskId: () => string;
}

let taskCounter = 0;

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
    set(s => {
      const tasks = s.tasks.map(t =>
        t.id === id ? { ...t, status: 'cancelled' as TaskStatus } : t,
      );
      return { tasks, stats: computeStats(tasks) };
    });
  },

  removeTask: (id) => {
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
      return { tasks, stats: computeStats(tasks) };
    });
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

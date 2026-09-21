/**
 * 传输任务状态管理（桌面端）
 *
 * ## 与 Web 端的必要差异：**任务真相在 Python**
 *
 * Web 端此 store 是任务的**唯一真相**（页面自己 addTask/updateTask，取消只是把本地状态
 * 改成 `cancelled`）。桌面端不能这样 —— 真正干活的是 Python 的 `TransferManager`
 * （`src/utils/transfer_manager.py`，QThread + 取消检查点）：
 *
 * | 能力 | Web 端（渲染层持有） | 桌面端（Python 持有） |
 * | --- | --- | --- |
 * | 进度 | JS 自己算 | 管理器信号 → 桥事件 `transfer.updated` |
 * | **取消** | 只改本地状态（**传输仍在继续**） | `transfer.cancel` → 工作线程在取消检查点退出 |
 * | 重试 | 无 | 管理器提供 |
 *
 * 因此本 store 现在是**Python 状态的投影**：
 * - `transfer.added` / `transfer.updated` → `applyTask()`（按 id 覆盖或插入）
 * - `transfer.removed` → `removeTask()`
 * - `cancelTask()` / `clearFinished()` → 调桥，**等 Python 事件回来再更新界面**
 *   （不再"乐观地"把界面改成已取消，避免出现"界面说取消了、文件还在传"的假象）
 */

import { create } from 'zustand';
import { callBridge, onBridgeEvent } from '../utils/desktop-bridge';
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
  /** 按 id 覆盖或插入（消费 Python 事件用） */
  applyTask: (task: TransferTask) => void;
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

  applyTask: (task) => {
    set(s => {
      const exists = s.tasks.some(t => t.id === task.id);
      const tasks = exists
        ? s.tasks.map(t => (t.id === task.id ? { ...t, ...task } : t))
        : [task, ...s.tasks];
      return { tasks, stats: computeStats(tasks) };
    });
  },

  /**
   * 取消任务：**交给 Python**（工作线程在下一个取消检查点退出）。
   *
   * 这里**不**先把界面改成 `cancelled`：管理器取消成功后会推 `transfer.updated`，
   * 由事件驱动界面更新 —— 避免"界面说取消了、其实还在传"的假象。
   */
  cancelTask: (id) => {
    void callBridge('transfer', 'cancel', { task_id: id }).catch(() => {
      // 取消失败（任务已结束等）不弹错，等事件自然收敛
    });
  },

  removeTask: (id) => {
    set(s => {
      const tasks = s.tasks.filter(t => t.id !== id);
      return { tasks, stats: computeStats(tasks) };
    });
  },

  /** 清空已结束任务：同样交给 Python（它才是任务持有者） */
  clearFinished: () => {
    void callBridge('transfer', 'clear', {}).catch(() => {});
  },

  generateTaskId: () => {
    taskCounter += 1;
    return `task-${Date.now()}-${taskCounter}`;
  },
}));

/** 订阅 Python 的任务事件（模块加载时一次，供所有页面共享） */
function subscribeToManager(): void {
  const { applyTask, removeTask } = useTransferStore.getState();
  onBridgeEvent('transfer.added', (p) => applyTask(p as TransferTask));
  onBridgeEvent('transfer.updated', (p) => applyTask(p as TransferTask));
  onBridgeEvent('transfer.removed', (p) => {
    const id = (p as { id?: string } | null)?.id;
    if (id) removeTask(id);
  });
  // 拉一次现状：Python 侧可能已有任务（例如本页刚挂载）
  void callBridge<{ tasks: TransferTask[] }>('transfer', 'list', {})
    .then((resp) => {
      (resp?.tasks ?? []).forEach(t => useTransferStore.getState().applyTask(t));
    })
    .catch(() => {});
}

subscribeToManager();

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

/** 类型再导出（页面里用到 TaskStatus 时不必再引 types） */
export type { TaskStatus };

/**
 * 传输任务状态管理测试
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { useTransferStore } from '../../src/stores/transfer-store';
import type { TransferTask } from '../../src/types/models';

/** 构造测试任务 */
function makeTask(overrides: Partial<TransferTask> = {}): TransferTask {
  return {
    id: 't1',
    kind: 'upload',
    filename: 'a.txt',
    localPath: 'a.txt',
    diskId: 1,
    remotePath: '',
    fileSize: 100,
    transferred: 0,
    percent: 0,
    status: 'pending',
    ...overrides,
  };
}

describe('transfer-store 传输任务状态管理', () => {
  beforeEach(() => {
    // 重置 store 状态
    useTransferStore.setState({
      tasks: [],
      stats: { total: 0, running: 0, pending: 0, completed: 0, failed: 0, cancelled: 0 },
    });
  });

  it('添加任务后统计正确（进行中 +1）', () => {
    const store = useTransferStore.getState();
    store.addTask(makeTask());

    const s = useTransferStore.getState();
    expect(s.tasks).toHaveLength(1);
    expect(s.stats.total).toBe(1);
    expect(s.stats.running).toBe(1);
    expect(s.stats.completed).toBe(0);
  });

  it('更新任务进度', () => {
    const store = useTransferStore.getState();
    store.addTask(makeTask());
    store.updateTask('t1', { transferred: 50, percent: 50, status: 'running' });

    const task = useTransferStore.getState().tasks[0];
    expect(task.transferred).toBe(50);
    expect(task.percent).toBe(50);
    expect(task.status).toBe('running');
  });

  it('取消任务 → 状态变为 cancelled，计入已取消统计', () => {
    const store = useTransferStore.getState();
    store.addTask(makeTask());
    store.cancelTask('t1');

    const s = useTransferStore.getState();
    expect(s.tasks[0].status).toBe('cancelled');
    expect(s.stats.running).toBe(0);
    expect(s.stats.completed).toBe(0);
    expect(s.stats.cancelled).toBe(1);
  });

  it('清除已完成任务 → 仅保留进行中任务', () => {
    const store = useTransferStore.getState();
    store.addTask(makeTask({ id: 't1', status: 'completed' }));
    store.addTask(makeTask({ id: 't2', status: 'running' }));
    store.addTask(makeTask({ id: 't3', status: 'failed' }));

    store.clearFinished();

    const s = useTransferStore.getState();
    expect(s.tasks).toHaveLength(1);
    expect(s.tasks[0].id).toBe('t2');
  });

  it('生成唯一任务 ID', () => {
    const store = useTransferStore.getState();
    const id1 = store.generateTaskId();
    const id2 = store.generateTaskId();
    expect(id1).not.toBe(id2);
  });
});

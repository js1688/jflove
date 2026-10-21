/**
 * 任务中心操作可用性（v1.8.0）
 *
 * 关键判据（F27.2 / AC-229）：
 *   - 分析中任务尚未绑定目标磁盘（`target_disk_name=''`），服务端 `can_operate=false`
 *     只是"还没选目录"，取消按钮**必须仍可用**；
 *   - 已绑定磁盘但写权限被收回（`can_operate=false`）⇒ 只读：显示「只读」徽章，
 *     不提供「重试」；
 *   - 失败原因用中文展示，并给出建议动作。
 */

import { MemoryRouter, useLocation } from 'react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { OfflineTask } from '../../../services/offline-download-service';

const serviceMock = vi.hoisted(() => ({
  listTasks: vi.fn(),
  cancel: vi.fn(),
  retry: vi.fn(),
  deleteRecord: vi.fn(),
}));

vi.mock('../../../services/offline-download-service', () => ({
  offlineDownloadService: serviceMock,
}));

import { TaskList } from '../TaskList';

function makeTask(overrides: Partial<OfflineTask> & { id: number }): OfflineTask {
  return {
    username: 'alice',
    source_domain: 'example.com',
    page_title: `页面-${overrides.id}`,
    status: 'analyzing',
    status_text: '分析中',
    item_total: 0,
    item_done: 0,
    bytes_total: 0,
    bytes_done: 0,
    speed_bps: 0,
    target_disk_name: '',
    target_rel_path: '',
    error_code: '',
    error_message: '',
    created_at: '2026-10-20T12:00:00+08:00',
    started_at: null,
    finished_at: null,
    can_operate: false,
    ...overrides,
  };
}

const TASKS: OfflineTask[] = [
  makeTask({ id: 1, status: 'analyzing', status_text: '分析中' }),
  makeTask({
    id: 2,
    status: 'failed',
    status_text: '失败',
    target_disk_name: '磁盘 A',
    target_rel_path: 'movies',
    error_code: 'net_timeout',
    error_message: '访问源站超时',
    can_operate: false,
  }),
  makeTask({
    id: 3,
    status: 'success',
    status_text: '已完成',
    page_title: '已完成的视频',
    target_disk_name: '磁盘 A',
    target_rel_path: 'movies',
    item_total: 1,
    item_done: 1,
    bytes_total: 1024,
    bytes_done: 1024,
    can_operate: true,
    finished_at: '2026-10-20T12:05:00+08:00',
  }),
];

/** 观察 MemoryRouter 的当前地址（验证「在文件管理中打开」的 navigate 目标） */
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="loc">{`${location.pathname}${location.search}`}</div>;
}

function renderTaskList() {
  return render(
    <MemoryRouter initialEntries={['/offline']}>
      <TaskList
        pollIntervalMs={2500}
        writableDisks={[{ disk_id: 7, name: '磁盘 A' }]}
        onSelectTask={vi.fn()}
      />
      <LocationProbe />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  serviceMock.listTasks.mockReset();
  serviceMock.cancel.mockReset();
  serviceMock.retry.mockReset();
  serviceMock.deleteRecord.mockReset();
  serviceMock.listTasks.mockResolvedValue({ total: TASKS.length, tasks: TASKS });
  serviceMock.cancel.mockResolvedValue({ task_id: 1, status: 'canceled' });
});

describe('任务中心：只读与可操作判定', () => {
  it('分析中任务（未绑定磁盘）仍可取消；已绑定但无写权限的任务显示只读且不给重试', async () => {
    renderTaskList();

    // 三个任务都渲染（产物名可见）
    expect(await screen.findByText('产物：页面-1')).not.toBeNull();
    expect(screen.getByText('产物：页面-2')).not.toBeNull();
    expect(screen.getByText('产物：已完成的视频')).not.toBeNull();

    // 分析中：取消可用
    const cancel = screen.getByRole('button', { name: '取消' });
    expect((cancel as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(cancel);
    await waitFor(() => expect(serviceMock.cancel).toHaveBeenCalledWith(1));

    // 失败且权限被收回：只读徽章 + 无重试按钮
    expect(screen.getByText('只读')).not.toBeNull();
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull();

    // 失败原因中文 + 建议动作
    expect(screen.getByText(/失败原因：访问源站超时/)).not.toBeNull();
    expect(screen.getByText(/建议：可重试/)).not.toBeNull();
  });

  it('成功任务可「在文件管理中打开」，路径映射到磁盘路由 + 目录查询参数', async () => {
    renderTaskList();
    await screen.findByText('产物：已完成的视频');
    // 失败任务与成功任务都绑定了同一目标目录，因此有两个同名按钮；点第一个即可
    const openButtons = screen.getAllByRole('button', { name: '在文件管理中打开' });
    expect(openButtons.length).toBe(2);
    fireEvent.click(openButtons[0]);
    expect(screen.getByTestId('loc').textContent).toBe('/files/7?path=movies');
  });
});

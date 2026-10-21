/**
 * 离线下载页面测试（v1.8.0）
 *
 * 覆盖「界面层必须保证」的三件事：
 *   1. `unusable_reason` 四态里最关键的两种（无可写磁盘整页禁用提示 / 未配置目录引导配置）；
 *   2. 任务卡片按状态给出正确操作（成功 → 「在文件管理中打开」）；
 *   3. 轮询纪律（F27.3 / AC-232）：任务全部终态即**停止**轮询；页面切到后台也停止，
 *      切回前台重新拉起。
 *
 * 服务层整体 mock：本页不发真实请求（加密信封由 http-client / service 测试覆盖）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

vi.mock('../../src/services/offline-download-service', () => ({
  offlineDownloadService: {
    meta: vi.fn(),
    listTasks: vi.fn(),
    detail: vi.fn(),
    analyze: vi.fn(),
    select: vi.fn(),
    nextPage: vi.fn(),
    cancel: vi.fn(),
    retry: vi.fn(),
    deleteRecord: vi.fn(),
    saveSettings: vi.fn(),
  },
}));

import { OfflineDownloadPage } from '../../src/pages/OfflineDownloadPage';
import { offlineDownloadService } from '../../src/services/offline-download-service';
import type { OfflineMeta, OfflineTask } from '../../src/types/models';

const metaMock = vi.mocked(offlineDownloadService.meta);
const listTasksMock = vi.mocked(offlineDownloadService.listTasks);

/** 可用状态的 meta */
function meta(partial: Partial<OfflineMeta> = {}): OfflineMeta {
  return {
    enabled: true,
    deep_analyze_enabled: true,
    deep_analyze_available: true,
    can_use: true,
    configured: true,
    unusable_reason: '',
    config: { disk_id: 1, rel_path: 'downloads', valid: true },
    writable_disks: [{ disk_id: 1, name: '主盘' }],
    max_items: 50,
    max_file_size: 4 * 1024 ** 3,
    max_images: 200,
    poll_interval_ms: 2500,
    ...partial,
  };
}

/** 任务行 */
function task(partial: Partial<OfflineTask> & { id: number }): OfflineTask {
  return {
    source_domain: 'example.com',
    page_title: '示例视频',
    status: 'success',
    status_text: '已完成',
    item_total: 1,
    item_done: 1,
    bytes_total: 1024,
    bytes_done: 1024,
    speed_bps: 0,
    target_disk_name: '主盘',
    target_rel_path: 'downloads',
    error_code: '',
    error_message: '',
    created_at: '2026-10-20T10:00:00+08:00',
    started_at: null,
    finished_at: null,
    can_operate: true,
    ...partial,
  };
}

/** 设置 document.visibilityState（jsdom 默认 visible） */
function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state,
  });
  act(() => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/offline']}>
      <OfflineDownloadPage />
    </MemoryRouter>,
  );
}

const realVisibility = Object.getOwnPropertyDescriptor(Document.prototype, 'visibilityState');

beforeEach(() => {
  metaMock.mockReset();
  listTasksMock.mockReset();
});

afterEach(() => {
  // 还原 jsdom 的 visibilityState（默认 visible），避免影响其他测试文件
  if (realVisibility) {
    Object.defineProperty(document, 'visibilityState', realVisibility);
  }
  vi.useRealTimers();
});

describe('离线下载页 · 不可用态', () => {
  it('没有可写磁盘：整页提示需求原文，且分析入口不可用', async () => {
    metaMock.mockResolvedValue(
      meta({
        can_use: false,
        configured: false,
        unusable_reason: 'no_writable_disk',
        writable_disks: [],
        config: { disk_id: 0, rel_path: '', valid: false },
      }),
    );
    listTasksMock.mockResolvedValue({ total: 0, tasks: [] });

    renderPage();

    expect(
      await screen.findByText('你没有可写入的磁盘，无法使用离线下载'),
    ).toBeInTheDocument();
    expect(screen.getByText(/当前账号不可使用离线下载/)).toBeInTheDocument();
  });

  it('未配置目录：提示「请先设置离线下载目录」并给出配置入口', async () => {
    metaMock.mockResolvedValue(
      meta({
        configured: false,
        unusable_reason: 'not_configured',
        config: { disk_id: 0, rel_path: '', valid: false },
      }),
    );
    listTasksMock.mockResolvedValue({ total: 0, tasks: [] });

    renderPage();

    expect(await screen.findByText('请先设置离线下载目录')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /去设置目录/ })).toBeInTheDocument();
  });
});

describe('离线下载页 · 任务中心', () => {
  it('展示任务与状态，成功任务提供「在文件管理中打开」', async () => {
    metaMock.mockResolvedValue(meta());
    listTasksMock.mockResolvedValue({ total: 1, tasks: [task({ id: 1 })] });

    renderPage();

    expect(await screen.findByText('示例视频')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /在文件管理中打开/ })).toBeInTheDocument();
  });

  it('失败任务按错误码决定是否显示「重试」', async () => {
    metaMock.mockResolvedValue(meta());
    listTasksMock.mockResolvedValue({
      total: 2,
      tasks: [
        task({ id: 1, status: 'failed', status_text: '失败', error_code: 'net_timeout', error_message: '访问源站超时' }),
        task({ id: 2, status: 'failed', status_text: '失败', error_code: 'drm_protected', error_message: '受版权保护' }),
      ],
    });

    renderPage();

    await screen.findByText(/访问源站超时/);
    // 只有 net_timeout 那条可重试
    expect(screen.getAllByRole('button', { name: /重试/ })).toHaveLength(1);
  });
});

describe('离线下载页 · 轮询纪律（AC-232）', () => {
  it('任务全部终态后停止轮询', async () => {
    metaMock.mockResolvedValue(meta({ poll_interval_ms: 100 }));
    listTasksMock.mockResolvedValue({ total: 1, tasks: [task({ id: 1 })] });

    renderPage();
    await screen.findByText('示例视频');

    // 等「轮询已暂停」提示出现（说明判定全终态后已停表）
    await screen.findByText(/全部任务已结束，轮询已暂停/);
    const settled = listTasksMock.mock.calls.length;
    expect(settled).toBeGreaterThanOrEqual(1);

    // 再等 350ms（远大于 100ms 轮询间隔），调用次数不再增长
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect(listTasksMock.mock.calls.length).toBe(settled);
  });

  it('页面切到后台停止轮询，切回前台重新拉起', async () => {
    metaMock.mockResolvedValue(meta({ poll_interval_ms: 100 }));
    listTasksMock.mockResolvedValue({
      total: 1,
      // 进行中：不会因为「全部终态」而停
      tasks: [task({ id: 1, status: 'downloading', status_text: '下载中', bytes_done: 10, bytes_total: 100 })],
    });

    renderPage();
    await screen.findByText('示例视频');

    // 前台持续轮询
    await waitFor(() => expect(listTasksMock.mock.calls.length).toBeGreaterThanOrEqual(2), {
      timeout: 1500,
    });

    // 切后台 → 停表
    setVisibility('hidden');
    await new Promise((resolve) => setTimeout(resolve, 120));
    const hiddenCount = listTasksMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect(listTasksMock.mock.calls.length).toBe(hiddenCount);

    // 切回前台 → 立即补一次并继续
    setVisibility('visible');
    await waitFor(
      () => expect(listTasksMock.mock.calls.length).toBeGreaterThan(hiddenCount),
      { timeout: 1500 },
    );
  });
});

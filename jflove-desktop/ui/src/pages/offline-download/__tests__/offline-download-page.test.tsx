/**
 * 离线下载页容器接线（v1.8.0）
 *
 * 判据：
 *   - `/offline/meta` 拉取后按 `writable_disks` 决定「不可用」态，并给出中文原因；
 *   - 页内「任务中心 / 新建任务」页签可切换，任务中心初始为空态；
 *   - 无可写磁盘时「新建任务」整块禁用，设置面板同样只读。
 *
 * 服务层被整体替身化：本用例不发出任何真实请求（渲染层无网络出口，测试同样如此）。
 */

import { MemoryRouter } from 'react-router';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { OfflineMeta } from '../../../services/offline-download-service';

const serviceMock = vi.hoisted(() => ({
  getMeta: vi.fn(),
  listTasks: vi.fn(),
  getDetail: vi.fn(),
  analyze: vi.fn(),
  select: vi.fn(),
  nextPage: vi.fn(),
  cancel: vi.fn(),
  retry: vi.fn(),
  deleteRecord: vi.fn(),
  saveSettings: vi.fn(),
}));

vi.mock('../../../services/offline-download-service', () => ({
  offlineDownloadService: serviceMock,
}));

import { OfflineDownloadPage } from '../OfflineDownloadPage';

/** 无可写磁盘的账号（AC-214 / F25.3） */
const META_NO_DISK: OfflineMeta = {
  enabled: true,
  deep_analyze_enabled: true,
  deep_analyze_available: false,
  can_use: false,
  configured: false,
  unusable_reason: 'no_writable_disk',
  config: { disk_id: 0, rel_path: '', valid: false },
  writable_disks: [],
  max_items: 50,
  max_file_size: 4 * 1024 * 1024 * 1024,
  max_images: 200,
  poll_interval_ms: 2500,
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/offline']}>
      <OfflineDownloadPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  serviceMock.getMeta.mockReset();
  serviceMock.listTasks.mockReset();
  serviceMock.getMeta.mockResolvedValue(META_NO_DISK);
  serviceMock.listTasks.mockResolvedValue({ total: 0, tasks: [] });
});

describe('离线下载页：不可用态与页签接线', () => {
  it('无可写磁盘 ⇒ 页面提示中文原因，任务中心为空态', async () => {
    renderPage();

    const reasons = await screen.findAllByText(/你没有可写入的磁盘/);
    expect(reasons.length).toBeGreaterThan(0);
    expect(await screen.findByText('暂无离线下载任务')).not.toBeNull();
    // 只调用一次 meta；任务列表由 TaskList 自己拉取
    expect(serviceMock.getMeta).toHaveBeenCalledTimes(1);
    expect(serviceMock.listTasks).toHaveBeenCalled();
  });

  it('切到「新建任务」⇒ 整块不可用；设置面板打开且只读提示', async () => {
    renderPage();
    await screen.findByText('暂无离线下载任务');

    fireEvent.click(screen.getByRole('tab', { name: '新建任务' }));
    expect(await screen.findByText('离线下载不可用')).not.toBeNull();

    // 页头横幅与空态里各有一个「查看设置」，点任意一个都能打开同一个面板
    fireEvent.click(screen.getAllByRole('button', { name: '查看设置' })[0]);
    expect(screen.getByRole('dialog', { name: '离线下载设置' })).not.toBeNull();
    expect(screen.getByText(/无法配置离线下载目录/)).not.toBeNull();
    // 只读态：保存按钮必须禁用（不提交任何请求）
    const save = screen.getAllByRole('button').find((btn) => btn.textContent === '保存');
    expect(save).toBeDefined();
    expect((save as HTMLButtonElement).disabled).toBe(true);
    expect(serviceMock.saveSettings).not.toHaveBeenCalled();
  });
});

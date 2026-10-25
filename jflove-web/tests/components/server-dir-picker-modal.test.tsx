/**
 * 服务器目录选择器（v1.8.1 / D14 / AC-257）
 *
 * 判据：
 *   - 打开时按初始路径加载，只展示目录；
 *   - roots 快捷入口、上级、进入子目录都能改变当前路径；
 *   - 「选择此目录」回填服务端规范化后的绝对路径；
 *   - 加载失败**不关闭弹窗**，可重试（错误同时 toast）。
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ServerDirListing } from '../../src/types/models';

vi.mock('../../src/services/disk-service', () => ({
  diskService: { browseServerDirs: vi.fn() },
}));

import { diskService } from '../../src/services/disk-service';
import { ServerDirPickerModal } from '../../src/components/ServerDirPickerModal';

const browseMock = vi.mocked(diskService.browseServerDirs);

const ROOTS = [
  { name: '/', path: '/' },
  { name: 'storage', path: '/storage' },
];

const STORAGE: ServerDirListing = {
  path: '/storage',
  parent: '/',
  dirs: [
    { name: 'media', path: '/storage/media' },
    { name: 'backup', path: '/storage/backup' },
  ],
  roots: ROOTS,
  truncated: false,
};

const MEDIA: ServerDirListing = {
  path: '/storage/media',
  parent: '/storage',
  dirs: [{ name: 'videos', path: '/storage/media/videos' }],
  roots: ROOTS,
  truncated: false,
};

beforeEach(() => {
  browseMock.mockReset();
});

describe('Web 服务器目录选择器（AC-257）', () => {
  it('按初始路径加载，逐级进入后回填当前目录', async () => {
    browseMock.mockResolvedValueOnce(STORAGE).mockResolvedValueOnce(MEDIA);

    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <ServerDirPickerModal initialPath="/storage" onSelect={onSelect} onClose={onClose} />,
    );

    await screen.findByText('media');
    expect(browseMock).toHaveBeenCalledWith('/storage');
    expect(screen.getByText('/storage')).toBeInTheDocument();

    fireEvent.click(screen.getByText('media'));
    await screen.findByText('videos');
    expect(browseMock).toHaveBeenLastCalledWith('/storage/media');

    fireEvent.click(screen.getByText('选择此目录'));
    expect(onSelect).toHaveBeenCalledWith('/storage/media');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('「上级」使用服务端 parent，而不是客户端拼路径', async () => {
    browseMock.mockResolvedValueOnce(MEDIA).mockResolvedValueOnce(STORAGE);

    render(
      <ServerDirPickerModal initialPath="/storage/media" onSelect={vi.fn()} onClose={vi.fn()} />,
    );
    await screen.findByText('videos');

    fireEvent.click(screen.getByText('上级'));
    await screen.findByText('media');
    expect(browseMock).toHaveBeenLastCalledWith('/storage');
  });

  it('加载失败保留弹窗并提示重试', async () => {
    browseMock
      .mockRejectedValueOnce(new Error('无权限访问该目录'))
      .mockResolvedValueOnce(STORAGE);

    render(<ServerDirPickerModal initialPath="/root" onSelect={vi.fn()} onClose={vi.fn()} />);

    expect(await screen.findByText(/加载失败：无权限访问该目录/)).toBeInTheDocument();
    expect(screen.getByText('选择服务器目录')).toBeInTheDocument();

    fireEvent.click(screen.getByText('重试'));
    await screen.findByText('media');
    expect(browseMock).toHaveBeenCalledTimes(2);
  });

  it('目录超限且服务端未下发 limit 时，文案回落 2000', async () => {
    browseMock.mockResolvedValueOnce({ ...STORAGE, truncated: true });

    render(<ServerDirPickerModal initialPath="/storage" onSelect={vi.fn()} onClose={vi.fn()} />);

    expect(await screen.findByText(/仅显示前 2000 项/)).toBeInTheDocument();
  });

  it('目录超限时文案使用响应 limit（v1.8.2 / C1 / AC-294）', async () => {
    browseMock.mockResolvedValueOnce({ ...STORAGE, truncated: true, limit: 500 });

    render(<ServerDirPickerModal initialPath="/storage" onSelect={vi.fn()} onClose={vi.fn()} />);

    expect(await screen.findByText(/仅显示前 500 项/)).toBeInTheDocument();
    expect(screen.queryByText(/仅显示前 2000 项/)).not.toBeInTheDocument();
  });
});

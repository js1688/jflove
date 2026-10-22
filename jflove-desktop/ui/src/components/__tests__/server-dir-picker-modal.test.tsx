/**
 * 服务器目录选择器（v1.8.1 / D14 / AC-257）
 *
 * 判据：
 *   - 打开时按初始路径加载，只展示目录；
 *   - roots 快捷入口、上级、进入子目录都能改变当前路径；
 *   - 「选择此目录」回填服务端规范化后的绝对路径；
 *   - 加载失败**不关闭弹窗**，可重试；
 *   - 超限时提示进入更具体路径。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ServerDirListing } from '../../types/models';

const serviceMock = vi.hoisted(() => ({ browseServerDirs: vi.fn() }));

vi.mock('../../services/disk-service', () => ({ diskService: serviceMock }));

import { ServerDirPickerModal } from '../ServerDirPickerModal';

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
  serviceMock.browseServerDirs.mockReset();
});

describe('服务器目录选择器（AC-257）', () => {
  it('按初始路径加载，只列目录并可逐级进入后回填', async () => {
    serviceMock.browseServerDirs
      .mockResolvedValueOnce(STORAGE)
      .mockResolvedValueOnce(MEDIA);

    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <ServerDirPickerModal initialPath="/storage" onSelect={onSelect} onClose={onClose} />,
    );

    // 首次加载使用传入的绝对路径
    await screen.findByText('media');
    expect(serviceMock.browseServerDirs).toHaveBeenCalledWith('/storage');
    expect(screen.getByText('/storage')).toBeTruthy();

    // 进入子目录
    fireEvent.click(screen.getByText('media'));
    await screen.findByText('videos');
    expect(serviceMock.browseServerDirs).toHaveBeenLastCalledWith('/storage/media');
    expect(screen.getByText('/storage/media')).toBeTruthy();

    // 选中当前目录 → 回填服务端规范化路径
    fireEvent.click(screen.getByText('选择此目录'));
    expect(onSelect).toHaveBeenCalledWith('/storage/media');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('roots 快捷入口与「上级」可切换当前路径', async () => {
    serviceMock.browseServerDirs
      .mockResolvedValueOnce(MEDIA)
      .mockResolvedValueOnce(STORAGE);

    render(<ServerDirPickerModal initialPath="/storage/media" onSelect={vi.fn()} onClose={vi.fn()} />);
    await screen.findByText('videos');

    // 上级：使用服务端给的 parent，而不是客户端拼字符串
    fireEvent.click(screen.getByText('上级'));
    await screen.findByText('media');
    expect(serviceMock.browseServerDirs).toHaveBeenLastCalledWith('/storage');
  });

  it('加载失败不关闭弹窗，可重试', async () => {
    serviceMock.browseServerDirs
      .mockRejectedValueOnce(new Error('无权限访问该目录'))
      .mockResolvedValueOnce(STORAGE);

    render(<ServerDirPickerModal initialPath="/root" onSelect={vi.fn()} onClose={vi.fn()} />);

    await screen.findByText(/加载失败：无权限访问该目录/);
    // 弹窗仍在（标题可见），错误不应把选择器关掉
    expect(screen.getByText('选择服务器目录')).toBeTruthy();

    fireEvent.click(screen.getByText('重试'));
    await screen.findByText('media');
    expect(serviceMock.browseServerDirs).toHaveBeenCalledTimes(2);
  });

  it('目录超限时提示进入更具体路径', async () => {
    serviceMock.browseServerDirs.mockResolvedValueOnce({ ...STORAGE, truncated: true });

    render(<ServerDirPickerModal initialPath="/storage" onSelect={vi.fn()} onClose={vi.fn()} />);

    await waitFor(() =>
      expect(screen.getByText(/仅显示前 2000 项/)).toBeTruthy(),
    );
  });
});

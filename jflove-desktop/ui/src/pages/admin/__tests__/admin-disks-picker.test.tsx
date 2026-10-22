/**
 * 磁盘管理「浏览…」集成（v1.8.1 / D14 / AC-257）
 *
 * 判据：添加磁盘时可打开服务器目录选择器 → 选中目录回填路径 → 创建请求带上该路径；
 * 手动输入仍然保留（选择器不是唯一入口）。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ServerDirListing } from '../../../types/models';

const serviceMock = vi.hoisted(() => ({
  listAllDisks: vi.fn(),
  createDisk: vi.fn(),
  updateDisk: vi.fn(),
  deleteDisk: vi.fn(),
  browseDirs: vi.fn(),
  browseServerDirs: vi.fn(),
}));

vi.mock('../../../services/disk-service', () => ({ diskService: serviceMock }));

import { AdminDisksPage } from '../AdminDisksPage';

const LISTING: ServerDirListing = {
  path: '/storage',
  parent: '/',
  dirs: [{ name: 'media', path: '/storage/media' }],
  roots: [{ name: '/', path: '/' }],
  truncated: false,
};

beforeEach(() => {
  serviceMock.listAllDisks.mockResolvedValue([]);
  serviceMock.browseServerDirs.mockResolvedValue(LISTING);
});

describe('磁盘管理 · 服务器目录选择（AC-257）', () => {
  it('点「浏览…」选中目录后回填路径，并用该路径创建磁盘', async () => {
    render(
      <MemoryRouter>
        <AdminDisksPage />
      </MemoryRouter>,
    );

    // 等列表加载完，再打开创建表单
    await screen.findByText('暂无磁盘');
    fireEvent.click(screen.getByText('添加磁盘'));
    fireEvent.change(screen.getByPlaceholderText('磁盘名称'), {
      target: { value: '媒体盘' },
    });

    // 打开服务器目录选择器（创建时允许服务端决定起点）
    fireEvent.click(screen.getByText('浏览…'));
    await screen.findByText('选择服务器目录');
    expect(serviceMock.browseServerDirs).toHaveBeenCalledWith(undefined);

    await screen.findByText('media');
    fireEvent.click(screen.getByText('选择此目录'));

    const pathInput = screen.getByPlaceholderText(/磁盘路径/) as HTMLInputElement;
    expect(pathInput.value).toBe('/storage');

    fireEvent.click(screen.getByText('确认'));
    await waitFor(() =>
      expect(serviceMock.createDisk).toHaveBeenCalledWith('媒体盘', '/storage'),
    );
  });

  it('仍可手动输入绝对路径（文本框作为兜底）', async () => {
    render(
      <MemoryRouter>
        <AdminDisksPage />
      </MemoryRouter>,
    );

    await screen.findByText('暂无磁盘');
    fireEvent.click(screen.getByText('添加磁盘'));
    fireEvent.change(screen.getByPlaceholderText('磁盘名称'), {
      target: { value: '手输盘' },
    });
    fireEvent.change(screen.getByPlaceholderText(/磁盘路径/), {
      target: { value: '/data/custom' },
    });
    fireEvent.click(screen.getByText('确认'));

    await waitFor(() =>
      expect(serviceMock.createDisk).toHaveBeenCalledWith('手输盘', '/data/custom'),
    );
    expect(serviceMock.browseServerDirs).not.toHaveBeenCalled();
  });
});

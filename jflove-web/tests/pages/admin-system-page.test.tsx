/**
 * 管理员「系统设置」页测试（v1.8.0 / UAT 问题 #1 / AC-247）
 *
 * 锁死三件事：
 *   1. 「离线下载」卡片存在，且总开关默认按后端默认值展示为开启；
 *   2. 开关调用 `updateConfig` 写入正确的键与值（关闭 = "0"）；
 *   3. 数字项保存时逐项写入 4 个配置键。
 *
 * configService 整体 mock：本页不发真实请求（加密信封由 http-client 测试覆盖）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

vi.mock('../../src/services/config-service', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../src/services/config-service')>();
  return {
    ...actual,
    configService: {
      getConfig: vi.fn(),
      updateConfig: vi.fn(),
    },
  };
});

import { AdminSystemPage } from '../../src/pages/admin/AdminSystemPage';
import {
  configService,
  OFFLINE_DOWNLOAD_KEYS,
} from '../../src/services/config-service';

const getConfigMock = vi.mocked(configService.getConfig);
const updateConfigMock = vi.mocked(configService.updateConfig);

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminSystemPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  getConfigMock.mockReset();
  updateConfigMock.mockReset();
  updateConfigMock.mockResolvedValue({ message: '配置已更新' });
});

describe('系统设置 · 离线下载全局策略', () => {
  it('默认值缺失时按后端默认展示：总开关开启、数值为 2/4096/50/200', async () => {
    getConfigMock.mockResolvedValue({ config: {} });
    renderPage();

    expect(
      await screen.findByRole('heading', { name: '离线下载' }),
    ).toBeInTheDocument();
    const sw = screen.getByRole('switch', { name: '离线下载总开关' });
    expect(sw).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByLabelText('下载并发数')).toHaveValue(2);
    expect(screen.getByLabelText('单文件体积上限（MB）')).toHaveValue(4096);
    expect(screen.getByLabelText('单次清单项数上限')).toHaveValue(50);
    expect(screen.getByLabelText('单页图片张数上限')).toHaveValue(200);
  });

  it('显式关闭的库展示为关闭', async () => {
    getConfigMock.mockResolvedValue({ config: { offline_download_enabled: '0' } });
    renderPage();

    await screen.findByRole('heading', { name: '离线下载' });
    expect(
      screen.getByRole('switch', { name: '离线下载总开关' }),
    ).toHaveAttribute('aria-checked', 'false');
  });

  it('关闭总开关写入 offline_download_enabled=0，并立即反映在界面上', async () => {
    getConfigMock.mockResolvedValue({ config: {} });
    renderPage();

    const sw = await screen.findByRole('switch', { name: '离线下载总开关' });
    fireEvent.click(sw);

    await waitFor(() => {
      expect(updateConfigMock).toHaveBeenCalledWith(
        OFFLINE_DOWNLOAD_KEYS.enabled,
        '0',
      );
    });
    expect(sw).toHaveAttribute('aria-checked', 'false');
  });

  it('保存数值配置时逐项写入 4 个键', async () => {
    getConfigMock.mockResolvedValue({ config: {} });
    renderPage();

    fireEvent.change(await screen.findByLabelText('下载并发数'), {
      target: { value: '3' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存数值配置' }));

    await waitFor(() => {
      expect(updateConfigMock).toHaveBeenCalledWith(
        OFFLINE_DOWNLOAD_KEYS.maxConcurrent,
        '3',
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        OFFLINE_DOWNLOAD_KEYS.maxFileSize,
        '4096',
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        OFFLINE_DOWNLOAD_KEYS.maxItems,
        '50',
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        OFFLINE_DOWNLOAD_KEYS.maxImages,
        '200',
      );
    });
    expect(updateConfigMock).toHaveBeenCalledTimes(4);
  });
});

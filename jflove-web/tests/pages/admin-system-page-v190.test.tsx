/**
 * 管理员「系统设置 · 存储与临时文件」页测试（v1.9.0，AC-351 / AC-352 / AC-353 / AC-364）
 *
 * 锁死：
 *   1. 卡片存在，字段按服务端默认值展示（24h / 60min / 24h / 8 / 1 / 2），客户端类型默认全选；
 *   2. 越界值前端前置拦截，不发任何 PUT；
 *   3. 保存时逐项写入 6 个数字键 + 客户端类型逗号集，并重新 GET 刷新；
 *   4. 「弱服务端模式」一键预设写 merge=1 / chunk=2 / sessions=4；
 *   5. 「立即清理」调 runCleanup 并显示删除 / 跳过数量；失败时显示原因（不静默）。
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
      runCleanup: vi.fn(),
    },
  };
});

import { AdminSystemPage } from '../../src/pages/admin/AdminSystemPage';
import {
  configService,
  UPLOAD_CLEANUP_KEYS,
  WEAK_SERVER_PRESET,
} from '../../src/services/config-service';

const getConfigMock = vi.mocked(configService.getConfig);
const updateConfigMock = vi.mocked(configService.updateConfig);
const runCleanupMock = vi.mocked(configService.runCleanup);

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminSystemPage />
    </MemoryRouter>,
  );
}

async function waitForStorageCard() {
  expect(
    await screen.findByRole('heading', { name: '存储与临时文件' }),
  ).toBeInTheDocument();
}

beforeEach(() => {
  getConfigMock.mockReset();
  updateConfigMock.mockReset();
  runCleanupMock.mockReset();
  getConfigMock.mockResolvedValue({ config: {} });
  updateConfigMock.mockResolvedValue({ message: '配置已更新' });
});

describe('系统设置 · 存储与临时文件', () => {
  it('默认值缺失时按服务端默认展示，客户端类型默认四项全选', async () => {
    renderPage();
    await waitForStorageCard();

    expect(
      screen.getByRole('switch', { name: '自动清理总开关' }),
    ).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByLabelText('断点续传保障时长（小时）')).toHaveValue(24);
    expect(screen.getByLabelText('自动清理间隔（分钟）')).toHaveValue(60);
    expect(screen.getByLabelText('孤儿临时文件保留期（小时）')).toHaveValue(24);
    expect(screen.getByLabelText('每用户未完成会话上限')).toHaveValue(8);
    expect(screen.getByLabelText('合并并发')).toHaveValue(1);
    expect(screen.getByLabelText('上传分片并发')).toHaveValue(2);
    for (const label of ['App', '桌面端', 'Web 端', '未知']) {
      expect(screen.getByLabelText(label)).toBeChecked();
    }
    // 单一来源说明（F52.3）
    expect(
      screen.getByText(/该值同时决定断点续传有效期和临时文件清理保护期/),
    ).toBeInTheDocument();
  });

  it('后端已落库的值按实际值展示', async () => {
    getConfigMock.mockResolvedValue({
      config: {
        upload_cleanup_enabled: '0',
        upload_resume_ttl_hours: '72',
        upload_cleanup_interval_minutes: '30',
        upload_cleanup_client_types: 'web',
      },
    });
    renderPage();
    await waitForStorageCard();

    expect(
      screen.getByRole('switch', { name: '自动清理总开关' }),
    ).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByLabelText('断点续传保障时长（小时）')).toHaveValue(72);
    expect(screen.getByLabelText('自动清理间隔（分钟）')).toHaveValue(30);
    expect(screen.getByLabelText('App')).not.toBeChecked();
    expect(screen.getByLabelText('Web 端')).toBeChecked();
  });

  it('越界值前端前置拦截，不发任何 PUT', async () => {
    renderPage();
    await waitForStorageCard();

    fireEvent.change(screen.getByLabelText('断点续传保障时长（小时）'), {
      target: { value: '0' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存存储配置' }));

    expect(updateConfigMock).not.toHaveBeenCalled();
    expect(screen.getByText(/断点续传保障时长（小时）需为 1~168 的整数/))
      .toBeInTheDocument();
  });

  it('取消全部客户端类型时拦截保存并提示', async () => {
    renderPage();
    await waitForStorageCard();
    for (const label of ['App', '桌面端', 'Web 端', '未知']) {
      fireEvent.click(screen.getByLabelText(label));
    }
    fireEvent.click(screen.getByRole('button', { name: '保存存储配置' }));
    expect(updateConfigMock).not.toHaveBeenCalled();
    expect(screen.getByText(/请至少勾选一种允许自动清理的客户端类型/))
      .toBeInTheDocument();
  });

  it('保存时逐项写入数字配置 + 客户端类型，并立即重新 GET 刷新', async () => {
    renderPage();
    await waitForStorageCard();

    fireEvent.change(screen.getByLabelText('断点续传保障时长（小时）'), {
      target: { value: '72' },
    });
    fireEvent.change(screen.getByLabelText('自动清理间隔（分钟）'), {
      target: { value: '30' },
    });
    fireEvent.change(screen.getByLabelText('孤儿临时文件保留期（小时）'), {
      target: { value: '48' },
    });
    fireEvent.change(screen.getByLabelText('每用户未完成会话上限'), {
      target: { value: '6' },
    });
    fireEvent.change(screen.getByLabelText('合并并发'), {
      target: { value: '2' },
    });
    fireEvent.change(screen.getByLabelText('上传分片并发'), {
      target: { value: '4' },
    });
    fireEvent.click(screen.getByLabelText('未知'));

    fireEvent.click(screen.getByRole('button', { name: '保存存储配置' }));

    await waitFor(() => {
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.resumeTtlHours,
        '72',
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.cleanupIntervalMinutes,
        '30',
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.orphanTtlHours,
        '48',
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.maxSessionsPerUser,
        '6',
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.mergeConcurrency,
        '2',
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.chunkConcurrency,
        '4',
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.clientTypes,
        'app,desktop,web',
      );
    });

    // 保存后立即刷新：getConfig 被再次调用
    await waitFor(() => expect(getConfigMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('存储与临时文件配置已保存，立即生效'))
      .toBeInTheDocument();
  });

  it('弱服务端模式一键预设：合并 1 / 上传并发 2 / 会话上限 4，并刷新', async () => {
    renderPage();
    await waitForStorageCard();

    fireEvent.click(screen.getByRole('button', { name: '启用弱服务端模式' }));
    await waitFor(() => {
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.mergeConcurrency,
        WEAK_SERVER_PRESET[UPLOAD_CLEANUP_KEYS.mergeConcurrency],
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.chunkConcurrency,
        WEAK_SERVER_PRESET[UPLOAD_CLEANUP_KEYS.chunkConcurrency],
      );
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.maxSessionsPerUser,
        WEAK_SERVER_PRESET[UPLOAD_CLEANUP_KEYS.maxSessionsPerUser],
      );
    });
    await waitFor(() => expect(getConfigMock).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/已启用「弱服务端模式」/)).toBeInTheDocument();
  });

  it('立即清理：调用 runCleanup 并显示删除会话 / 目录 / 残留 / 跳过数量', async () => {
    runCleanupMock.mockResolvedValue({
      removed_sessions: 2,
      removed_dirs: 1,
      removed_parts: 3,
      skipped: 1,
      executed_at: 1760000000,
    });
    renderPage();
    await waitForStorageCard();

    fireEvent.click(screen.getByRole('button', { name: '立即清理' }));
    await waitFor(() => expect(runCleanupMock).toHaveBeenCalledTimes(1));
    // 统计文案由多个 JSX 表达式拼接，按 textContent 断言（避免被拆成多个文本节点）
    const summary = await screen.findByText(/最近清理/);
    expect(summary.textContent).toContain('删除会话 2 个');
    expect(summary.textContent).toContain('目录 1 个');
    expect(summary.textContent).toContain('合并残留 3 个');
    expect(summary.textContent).toContain('跳过 1 个');
  });

  it('立即清理失败时显示错误原因（不静默）', async () => {
    runCleanupMock.mockRejectedValue(new Error('服务器内部错误'));
    renderPage();
    await waitForStorageCard();

    fireEvent.click(screen.getByRole('button', { name: '立即清理' }));
    await waitFor(() => expect(runCleanupMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('服务器内部错误')).toBeInTheDocument();
  });

  it('关闭自动清理总开关写入 upload_cleanup_enabled=0', async () => {
    renderPage();
    await waitForStorageCard();
    fireEvent.click(screen.getByRole('switch', { name: '自动清理总开关' }));
    await waitFor(() => {
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.enabled,
        '0',
      );
    });
  });
});

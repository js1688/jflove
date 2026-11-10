/**
 * 管理员「系统设置 · 存储与临时文件」页测试（v1.9.0 引入，v1.9.1 精简）
 *
 * 锁死：
 *   1. 卡片只剩 3 项：自动清理临时文件 / 临时文件期限 / 清理检查间隔，外加「立即清理」；
 *   2. 已删除项不得回归：客户端类型多选、孤儿保留期、会话上限、合并/分片并发、弱服务端模式；
 *   3. 越界值前端前置拦截，不发任何 PUT；保存只写 2 个数字项并重新 GET 刷新；
 *   4. 「立即清理」调 runCleanup 并显示删除 / 跳过数量；失败时显示原因（不静默）；
 *   5. 关闭自动清理临时文件写入 enabled=0（但手动清理仍然可用）。
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

describe('系统设置 · 存储与临时文件（v1.9.1 精简）', () => {
  it('默认值按服务端默认展示，且只有 3 项', async () => {
    renderPage();
    await waitForStorageCard();

    expect(
      screen.getByRole('switch', { name: '自动清理临时文件' }),
    ).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByLabelText('临时文件期限（小时）')).toHaveValue(24);
    expect(screen.getByLabelText('清理检查间隔（分钟）')).toHaveValue(60);

    // 单一期限来源说明
    expect(
      screen.getByText(/临时文件期限同时决定断点续传有效期与清理保护期/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/关闭自动清理后，「立即清理」仍可手动执行一次/),
    ).toBeInTheDocument();

    // 已删除的参数不得回归
    expect(screen.queryByText(/允许自动清理的客户端类型/)).toBeNull();
    expect(screen.queryByText(/孤儿临时文件保留期/)).toBeNull();
    expect(screen.queryByText(/每用户未完成会话上限/)).toBeNull();
    expect(screen.queryByText(/^合并并发$/)).toBeNull();
    expect(screen.queryByText(/上传分片并发/)).toBeNull();
    expect(screen.queryByText(/弱服务端模式/)).toBeNull();
    expect(screen.queryByText(/未知/)).toBeNull();
  });

  it('后端已落库的值按实际值展示（遗留键不渲染）', async () => {
    getConfigMock.mockResolvedValue({
      config: {
        upload_cleanup_enabled: '0',
        upload_resume_ttl_hours: '72',
        upload_cleanup_interval_minutes: '30',
        upload_cleanup_client_types: 'web',
        upload_orphan_ttl_hours: '12',
        upload_max_sessions_per_user: '6',
      },
    });
    renderPage();
    await waitForStorageCard();

    expect(
      screen.getByRole('switch', { name: '自动清理临时文件' }),
    ).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByLabelText('临时文件期限（小时）')).toHaveValue(72);
    expect(screen.getByLabelText('清理检查间隔（分钟）')).toHaveValue(30);
    expect(screen.queryByText(/允许自动清理的客户端类型/)).toBeNull();
  });

  it('越界值前端前置拦截，不发任何 PUT', async () => {
    renderPage();
    await waitForStorageCard();

    fireEvent.change(screen.getByLabelText('临时文件期限（小时）'), {
      target: { value: '0' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存存储配置' }));

    expect(updateConfigMock).not.toHaveBeenCalled();
    expect(screen.getByText(/临时文件期限（小时）需为 1~168 的整数/))
      .toBeInTheDocument();
  });

  it('保存只写 2 个数字项，并立即重新 GET 刷新', async () => {
    renderPage();
    await waitForStorageCard();

    fireEvent.change(screen.getByLabelText('临时文件期限（小时）'), {
      target: { value: '72' },
    });
    fireEvent.change(screen.getByLabelText('清理检查间隔（分钟）'), {
      target: { value: '30' },
    });
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
    });
    expect(updateConfigMock).toHaveBeenCalledTimes(2);
    for (const call of updateConfigMock.mock.calls) {
      expect(call[0]).not.toBe('upload_cleanup_client_types');
      expect(call[0]).not.toBe('upload_orphan_ttl_hours');
      expect(call[0]).not.toBe('upload_max_sessions_per_user');
      expect(call[0]).not.toBe('upload_merge_concurrency');
      expect(call[0]).not.toBe('upload_chunk_concurrency');
    }

    // 保存后立即刷新：getConfig 被再次调用
    await waitFor(() => expect(getConfigMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('存储与临时文件配置已保存，立即生效'))
      .toBeInTheDocument();
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

  it('关闭自动清理临时文件写入 upload_cleanup_enabled=0', async () => {
    renderPage();
    await waitForStorageCard();
    fireEvent.click(screen.getByRole('switch', { name: '自动清理临时文件' }));
    await waitFor(() => {
      expect(updateConfigMock).toHaveBeenCalledWith(
        UPLOAD_CLEANUP_KEYS.enabled,
        '0',
      );
    });
  });
});

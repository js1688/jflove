/**
 * 管理端「系统设置 → 存储与临时文件」（v1.9.0 引入，v1.9.1 精简）
 *
 * 判据：
 *   - 卡片只有 3 项：自动清理临时文件 / 临时文件期限 / 清理检查间隔，外加「立即清理」；
 *   - 已删除项不得回归：客户端类型多选、孤儿保留期、会话上限、合并/分片并发、弱服务端模式；
 *   - 保存逐项走 `configService.updateConfig`（只写数字项），成功后重新拉取；
 *   - 「立即清理」不受总开关限制（文案 + 调用 `configService.cleanupNow()`）；
 *   - 文案说明临时文件期限同时决定续传有效期与清理保护期。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const serviceMock = vi.hoisted(() => ({
  getConfig: vi.fn(),
  updateConfig: vi.fn(),
  cleanupNow: vi.fn(),
}));

vi.mock('../../../services/config-service', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../../services/config-service')>();
  return { ...actual, configService: serviceMock };
});

import { useToastStore } from '../../../components/ui/Overlay';
import { AdminSystemPage } from '../AdminSystemPage';

const renderPage = () =>
  render(
    <MemoryRouter>
      <AdminSystemPage />
    </MemoryRouter>,
  );

beforeEach(() => {
  useToastStore.setState({ items: [] });
  serviceMock.getConfig.mockReset();
  serviceMock.updateConfig.mockReset();
  serviceMock.cleanupNow.mockReset();
  serviceMock.getConfig.mockResolvedValue({ config: {} });
  serviceMock.updateConfig.mockResolvedValue({ message: 'ok' });
  serviceMock.cleanupNow.mockResolvedValue({
    removed_sessions: 2,
    removed_dirs: 1,
    removed_parts: 3,
    skipped: 4,
  });
});

describe('系统设置 · 存储与临时文件（v1.9.1 精简）', () => {
  it('只渲染 3 项并展示默认值与范围', async () => {
    renderPage();

    const ttl = (await screen.findByLabelText(
      '临时文件期限（小时）',
    )) as HTMLInputElement;
    expect(ttl.value).toBe('24');
    expect((screen.getByLabelText('清理检查间隔（分钟）') as HTMLInputElement).value)
      .toBe('60');
    expect(screen.getByLabelText('自动清理临时文件').getAttribute('aria-checked'))
      .toBe('true');

    expect(screen.getByText('允许范围 1~168')).toBeTruthy();
    expect(screen.getByText('允许范围 5~1440')).toBeTruthy();

    expect(
      screen.getByText(/临时文件期限同时决定断点续传有效期与清理保护期/),
    ).toBeTruthy();
    expect(
      screen.getByText(/关闭自动清理后，「立即清理」仍可手动执行一次/),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: '立即清理' })).toBeTruthy();
    expect(screen.getByText('保存存储配置')).toBeTruthy();
  });

  it('已删除的参数不得回归', async () => {
    renderPage();
    await screen.findByLabelText('临时文件期限（小时）');

    expect(screen.queryByText(/允许自动清理的客户端类型/)).toBeNull();
    expect(screen.queryByText(/孤儿临时文件保留期/)).toBeNull();
    expect(screen.queryByText(/每用户未完成上传会话上限/)).toBeNull();
    expect(screen.queryByText(/合并并发数/)).toBeNull();
    expect(screen.queryByText(/上传分片并发数/)).toBeNull();
    expect(screen.queryByText(/弱服务端模式/)).toBeNull();
    expect(screen.queryByText(/未知客户端/)).toBeNull();
  });

  it('服务端已有配置时以服务端值为准（遗留键不渲染）', async () => {
    serviceMock.getConfig.mockResolvedValue({
      config: {
        upload_cleanup_enabled: '0',
        upload_resume_ttl_hours: '48',
        upload_cleanup_interval_minutes: '30',
        upload_cleanup_client_types: 'desktop,web',
        upload_orphan_ttl_hours: '12',
        upload_max_sessions_per_user: '4',
      },
    });
    renderPage();

    const ttl = (await screen.findByLabelText(
      '临时文件期限（小时）',
    )) as HTMLInputElement;
    expect(ttl.value).toBe('48');
    expect((screen.getByLabelText('清理检查间隔（分钟）') as HTMLInputElement).value)
      .toBe('30');
    expect(screen.getByLabelText('自动清理临时文件').getAttribute('aria-checked'))
      .toBe('false');
    expect(screen.queryByText(/允许自动清理的客户端类型/)).toBeNull();
  });

  it('保存只写 2 个数字项并重新拉取（不再写已删除的键）', async () => {
    renderPage();
    const ttl = (await screen.findByLabelText(
      '临时文件期限（小时）',
    )) as HTMLInputElement;

    fireEvent.change(ttl, { target: { value: '12' } });
    fireEvent.click(screen.getByText('保存存储配置'));

    await waitFor(() =>
      expect(serviceMock.updateConfig).toHaveBeenCalledWith(
        'upload_resume_ttl_hours',
        '12',
      ),
    );
    expect(serviceMock.updateConfig).toHaveBeenCalledWith(
      'upload_cleanup_interval_minutes',
      '60',
    );
    expect(serviceMock.updateConfig).toHaveBeenCalledTimes(2);
    for (const call of serviceMock.updateConfig.mock.calls) {
      expect(call[0]).not.toBe('upload_cleanup_client_types');
      expect(call[0]).not.toBe('upload_orphan_ttl_hours');
      expect(call[0]).not.toBe('upload_max_sessions_per_user');
      expect(call[0]).not.toBe('upload_merge_concurrency');
      expect(call[0]).not.toBe('upload_chunk_concurrency');
    }
    // 保存后重新 load 一次（初始 1 次 + 保存后 1 次）
    await waitFor(() => expect(serviceMock.getConfig).toHaveBeenCalledTimes(2));
  });

  it('越界值前端拦截并提示范围，不写服务端', async () => {
    renderPage();
    const ttl = (await screen.findByLabelText(
      '临时文件期限（小时）',
    )) as HTMLInputElement;

    fireEvent.change(ttl, { target: { value: '500' } });
    fireEvent.click(screen.getByText('保存存储配置'));

    await waitFor(() => {
      const items = useToastStore.getState().items;
      expect(items.some((t) => (t.description ?? '').includes('1~168'))).toBe(true);
    });
    expect(serviceMock.updateConfig).not.toHaveBeenCalled();
  });

  it('立即清理调用 cleanupNow 并展示统计', async () => {
    const { container } = renderPage();
    await screen.findByRole('button', { name: '立即清理' });

    fireEvent.click(screen.getByRole('button', { name: '立即清理' }));

    await waitFor(() => expect(serviceMock.cleanupNow).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(container.textContent).toContain('删除会话 2 个'),
    );
    expect(container.textContent).toContain('合并残留 3 个');
    expect(container.textContent).toContain('跳过 4 个');
    const items = useToastStore.getState().items;
    expect(items.some((t) => t.title === '清理完成')).toBe(true);
  });

  it('清理失败时提示错误且不展示统计', async () => {
    serviceMock.cleanupNow.mockRejectedValue(new Error('无管理员权限'));
    const { container } = renderPage();
    await screen.findByRole('button', { name: '立即清理' });

    fireEvent.click(screen.getByRole('button', { name: '立即清理' }));

    await waitFor(() => {
      const items = useToastStore.getState().items;
      expect(items.some((t) => t.title === '清理失败')).toBe(true);
    });
    expect(container.textContent).not.toContain('最近一次：删除会话');
  });
});

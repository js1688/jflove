/**
 * 管理端「系统设置 → 存储与临时文件」（v1.9.0 / F51 / F52 / F56.5）
 *
 * 判据：
 *   - 卡片字段齐全（总开关 / TTL / 清理间隔 / 客户端类型多选 / 孤儿保留期 /
 *     每用户会话上限 / 合并并发 / 分片并发 / 弱服务端预设 / 立即清理）；
 *   - 默认值与后端 `UPLOAD_CONFIG_DEFAULTS` 同源，范围提示与 `validate_config_value` 一致；
 *   - 保存逐项走 `configService.updateConfig`，成功后重新拉取（保存立即刷新）；
 *   - 弱服务端模式写入合并并发 1 / 上传并发 2 / 每用户会话上限 4；
 *   - 立即清理走 `configService.cleanupNow()` 并展示删除/跳过数量；
 *   - TTL 文案强调「同时决定续传有效期与清理保护期」。
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

describe('系统设置 · 存储与临时文件（v1.9.0）', () => {
  it('渲染全部字段并展示默认值与范围', async () => {
    renderPage();

    const ttl = (await screen.findByLabelText(
      '断点续传保障时长（小时）',
    )) as HTMLInputElement;
    expect(ttl.value).toBe('24');
    expect((screen.getByLabelText('自动清理间隔（分钟）') as HTMLInputElement).value)
      .toBe('60');
    expect((screen.getByLabelText('孤儿临时文件保留期（小时）') as HTMLInputElement).value)
      .toBe('24');
    expect((screen.getByLabelText('每用户未完成上传会话上限') as HTMLInputElement).value)
      .toBe('8');
    expect((screen.getByLabelText('合并并发数') as HTMLInputElement).value).toBe('1');
    expect((screen.getByLabelText('上传分片并发数') as HTMLInputElement).value).toBe('2');

    const toggle = screen.getByLabelText('自动清理总开关');
    expect(toggle.getAttribute('aria-checked')).toBe('true');

    // 1~168 同时用于「断点续传保障时长」与「孤儿保留期」两项
    expect(screen.getAllByText('允许范围 1~168').length).toBe(2);
    expect(screen.getByText('允许范围 5~1440')).toBeTruthy();
    expect(screen.getByText('允许范围 0~4')).toBeTruthy();
    expect(screen.getByText('允许范围 1~8')).toBeTruthy();
    expect(
      screen.getByText(/「断点续传保障时长」同时决定断点续传有效期和临时文件清理保护期/),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: '立即清理' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '启用弱服务端模式' })).toBeTruthy();
  });

  it('服务端已有配置时以服务端值为准', async () => {
    serviceMock.getConfig.mockResolvedValue({
      config: {
        upload_cleanup_enabled: '0',
        upload_resume_ttl_hours: '48',
        upload_cleanup_interval_minutes: '30',
        upload_cleanup_client_types: 'desktop,web',
        upload_orphan_ttl_hours: '12',
        upload_max_sessions_per_user: '4',
        upload_merge_concurrency: '2',
        upload_chunk_concurrency: '6',
      },
    });
    renderPage();

    const ttl = (await screen.findByLabelText(
      '断点续传保障时长（小时）',
    )) as HTMLInputElement;
    expect(ttl.value).toBe('48');
    expect((screen.getByLabelText('自动清理间隔（分钟）') as HTMLInputElement).value)
      .toBe('30');
    expect((screen.getByLabelText('合并并发数') as HTMLInputElement).value).toBe('2');
    expect(screen.getByLabelText('自动清理总开关').getAttribute('aria-checked'))
      .toBe('false');
    expect(
      (screen.getByLabelText('允许清理客户端：桌面端') as HTMLInputElement).checked,
    ).toBe(true);
  });

  it('保存时逐项写 config-service 并重新拉取（保存立即刷新）', async () => {
    renderPage();
    const ttl = (await screen.findByLabelText(
      '断点续传保障时长（小时）',
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
    expect(serviceMock.updateConfig).toHaveBeenCalledWith(
      'upload_cleanup_client_types',
      '',
    );
    // 保存后重新 load 一次（初始 1 次 + 保存后 1 次）
    await waitFor(() => expect(serviceMock.getConfig).toHaveBeenCalledTimes(2));
  });

  it('越界值前端拦截并提示范围，不写服务端', async () => {
    renderPage();
    const ttl = (await screen.findByLabelText(
      '断点续传保障时长（小时）',
    )) as HTMLInputElement;

    fireEvent.change(ttl, { target: { value: '500' } });
    fireEvent.click(screen.getByText('保存存储配置'));

    await waitFor(() => {
      const items = useToastStore.getState().items;
      expect(items.some((t) => (t.description ?? '').includes('1~168'))).toBe(true);
    });
    expect(serviceMock.updateConfig).not.toHaveBeenCalled();
  });

  it('客户端类型多选按固定顺序写逗号串', async () => {
    renderPage();
    await screen.findByLabelText('允许清理客户端：Web 端');

    fireEvent.click(screen.getByLabelText('允许清理客户端：Web 端'));
    fireEvent.click(screen.getByLabelText('允许清理客户端：桌面端'));
    fireEvent.click(screen.getByText('保存存储配置'));

    await waitFor(() =>
      expect(serviceMock.updateConfig).toHaveBeenCalledWith(
        'upload_cleanup_client_types',
        'desktop,web',
      ),
    );
  });

  it('弱服务端模式一键预设写合并并发 1 / 上传并发 2 / 会话上限 4', async () => {
    renderPage();
    await screen.findByText('启用弱服务端模式');

    fireEvent.click(screen.getByText('启用弱服务端模式'));

    await waitFor(() =>
      expect(serviceMock.updateConfig).toHaveBeenCalledWith(
        'upload_merge_concurrency',
        '1',
      ),
    );
    expect(serviceMock.updateConfig).toHaveBeenCalledWith(
      'upload_chunk_concurrency',
      '2',
    );
    expect(serviceMock.updateConfig).toHaveBeenCalledWith(
      'upload_max_sessions_per_user',
      '4',
    );
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

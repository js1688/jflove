/**
 * 管理端「系统设置 → 离线下载」超时项（v1.8.2 / F37.6 / 设计 §4.2）
 *
 * 判据：
 *   - 新增 3 个数字项（解析总超时 / 连接超时 / 读取超时），默认值 90 / 15 / 60；
 *   - 范围提示与后端 `validate_config_value` 同源（30~180 / 5~30 / 15~120）；
 *   - 保存走既有 `configService.updateConfig`，键名与后端 settings.py 完全一致；
 *   - 越界时前端先拦（不写服务端），错误文案指出范围。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const serviceMock = vi.hoisted(() => ({
  getConfig: vi.fn(),
  updateConfig: vi.fn(),
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
  serviceMock.getConfig.mockResolvedValue({ config: {} });
  serviceMock.updateConfig.mockResolvedValue({ message: 'ok' });
});

describe('系统设置 · 离线下载超时（F37.6）', () => {
  it('渲染 3 个超时项，默认 90 / 15 / 60 秒并展示范围', async () => {
    renderPage();

    const analyze = (await screen.findByLabelText('解析总超时（秒）')) as HTMLInputElement;
    const connect = screen.getByLabelText('连接超时（秒）') as HTMLInputElement;
    const read = screen.getByLabelText('读取超时（秒）') as HTMLInputElement;

    expect(analyze.value).toBe('90');
    expect(connect.value).toBe('15');
    expect(read.value).toBe('60');

    expect(screen.getByText('允许范围 30~180')).toBeTruthy();
    expect(screen.getByText('允许范围 5~30')).toBeTruthy();
    expect(screen.getByText('允许范围 15~120')).toBeTruthy();
  });

  it('服务端已有配置时以服务端值为准', async () => {
    serviceMock.getConfig.mockResolvedValue({
      config: {
        offline_download_analyze_timeout_s: '120',
        offline_download_connect_timeout_s: '20',
        offline_download_read_timeout_s: '90',
      },
    });
    renderPage();

    const analyze = (await screen.findByLabelText('解析总超时（秒）')) as HTMLInputElement;
    expect(analyze.value).toBe('120');
    expect((screen.getByLabelText('连接超时（秒）') as HTMLInputElement).value).toBe('20');
    expect((screen.getByLabelText('读取超时（秒）') as HTMLInputElement).value).toBe('90');
  });

  it('保存时把 3 个超时键写入 config-service（键名与后端一致）', async () => {
    renderPage();
    const analyze = (await screen.findByLabelText('解析总超时（秒）')) as HTMLInputElement;

    fireEvent.change(analyze, { target: { value: '150' } });
    fireEvent.change(screen.getByLabelText('连接超时（秒）'), { target: { value: '25' } });
    fireEvent.change(screen.getByLabelText('读取超时（秒）'), { target: { value: '100' } });
    fireEvent.click(screen.getByText('保存数值配置'));

    await waitFor(() =>
      expect(serviceMock.updateConfig).toHaveBeenCalledWith(
        'offline_download_analyze_timeout_s',
        '150',
      ),
    );
    expect(serviceMock.updateConfig).toHaveBeenCalledWith(
      'offline_download_connect_timeout_s',
      '25',
    );
    expect(serviceMock.updateConfig).toHaveBeenCalledWith(
      'offline_download_read_timeout_s',
      '100',
    );
  });

  it('超时越界时前端拦截并提示范围，不写服务端', async () => {
    renderPage();
    const analyze = (await screen.findByLabelText('解析总超时（秒）')) as HTMLInputElement;

    fireEvent.change(analyze, { target: { value: '300' } });
    fireEvent.click(screen.getByText('保存数值配置'));

    await waitFor(() => {
      const items = useToastStore.getState().items;
      expect(items.some((t) => (t.description ?? '').includes('30~180'))).toBe(true);
    });
    expect(serviceMock.updateConfig).not.toHaveBeenCalled();
  });
});

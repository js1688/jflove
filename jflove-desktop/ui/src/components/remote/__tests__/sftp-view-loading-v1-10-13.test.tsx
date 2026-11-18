/**
 * SFTP 面板：加载耗时 / 取消 / 重试（v1.10.13）
 *
 * 判据：
 * 1. 加载中必须显示已用秒数（用户能区分「慢」与「卡死」）；
 * 2. 提供「取消加载」并调用 `sftp_cancel`（关闭 client，中断阻塞读）；
 * 3. 失败后显示原因 + 「重试」，重试会重新拉目录。
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { sftpList, sftpAbort } = vi.hoisted(() => ({
  sftpList: vi.fn(),
  sftpAbort: vi.fn(),
}));

vi.mock('../../../utils/desktop-bridge', () => ({
  callBridge: vi.fn(),
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
  whenBridgeReady: async () => undefined,
}));

vi.mock('../../../services/remote-service', () => ({
  remoteService: {
    sftpList,
    sftpAbort,
    sftpCancel: vi.fn(),
    sftpMkdir: vi.fn(),
    sftpRename: vi.fn(),
    sftpDelete: vi.fn(),
    sftpUpload: vi.fn(),
    sftpDownload: vi.fn(),
  },
  localTermService: {},
}));

import { SftpView } from '../SftpView';
import { useRemoteStore } from '../../../stores/remote-store';

function setReadyTab(): void {
  useRemoteStore.setState({
    tabs: [
      {
        id: 's1',
        kind: 'sftp',
        connectionId: 1,
        title: '测试',
        subtitle: 'tester@host',
        port: 22,
        token: 'tok',
        status: 'ready',
        view: 'files',
        jumpChain: [],
        message: '',
      },
    ],
    activeTabId: 's1',
    pendingPrompts: [],
  });
}

beforeEach(() => {
  sftpList.mockReset();
  sftpAbort.mockReset();
  act(() => setReadyTab());
});

afterEach(() => {
  cleanup();
  act(() => {
    useRemoteStore.setState({ tabs: [], activeTabId: null, pendingPrompts: [] });
  });
});

describe('SftpView · 加载耗时与取消（v1.10.13）', () => {
  it('加载中显示已用秒数；取消加载会调用 sftp_abort（不误伤传输）', async () => {
    let resolveList: ((value: unknown) => void) | null = null;
    sftpList.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveList = resolve;
        }),
    );
    render(<SftpView sessionId="s1" />);

    await waitFor(() => expect(screen.getByText(/加载中…（已用 0 秒）/)).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: '取消加载' }));
    await waitFor(() => expect(sftpAbort).toHaveBeenCalledWith('s1'));
    // 清理挂起的 Promise，避免测试结束后更新的警告
    if (resolveList) {
      await act(async () => {
        (resolveList as (value: unknown) => void)({ entries: [] });
      });
    }
  });

  it('加载失败显示原因，点「重试」会重新拉目录', async () => {
    sftpList.mockRejectedValueOnce(new Error('远端 SFTP 响应超时（服务器长时间无响应，请重试或检查网络）'));
    sftpList.mockResolvedValueOnce({
      entries: [{ name: 'opt', size: 0, mode: 0o040755, is_dir: true, mtime: 0 }],
    });
    render(<SftpView sessionId="s1" />);

    await waitFor(() =>
      expect(screen.getByText(/远端 SFTP 响应超时/)).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(sftpList).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText('opt')).toBeTruthy());
  });
});

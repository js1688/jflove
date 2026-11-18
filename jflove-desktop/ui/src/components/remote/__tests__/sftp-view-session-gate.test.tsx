/**
 * SFTP 文件视图：会话就绪门控（v1.10.12）
 *
 * ## 这条用例守的是什么
 *
 * 用户实测（2026-11-17）：SFTP 连上后（标签已是绿点「已连接」）文件面板红字
 * 「会话尚未建立」，目录不出来 —— 点「刷新」才正常。
 *
 * 根因：标签在 `session_open` 返回时就建好了（`stores/remote-store.ts::openRemoteSession`），
 * 那一刻 Python 侧还在后台建链；旧实现挂载即 `sftpList('.')`，只会拿到
 * `SshError("会话尚未建立")`，且**失败后永不重试**。
 *
 * 判据：
 * 1. `status = connecting` 时**不发** `sftp_list`，只显示等待占位；
 * 2. 状态变 `ready` 后**自动**拉一次目录并渲染；
 * 3. `status = failed` 时给「会话未就绪」提示、不发请求；
 * 4. store 里没有该会话（单测 / 独立使用）时保持旧行为：挂载即加载。
 */

import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { sftpList } = vi.hoisted(() => ({ sftpList: vi.fn() }));

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
    sftpMkdir: vi.fn(),
    sftpRename: vi.fn(),
    sftpDelete: vi.fn(),
    sftpUpload: vi.fn(),
    sftpDownload: vi.fn(),
    sftpCancel: vi.fn(),
    listConnections: vi.fn(),
    createConnection: vi.fn(),
    getConnection: vi.fn(),
    updateConnection: vi.fn(),
    deleteConnection: vi.fn(),
    duplicateConnection: vi.fn(),
    openSession: vi.fn(),
    closeSession: vi.fn(),
    listGroups: vi.fn(),
    createGroup: vi.fn(),
    updateGroup: vi.fn(),
    deleteGroup: vi.fn(),
    confirmHostKey: vi.fn(),
    confirmOtp: vi.fn(),
  },
  localTermService: {
    detectShells: vi.fn(),
    listGroups: vi.fn(),
    listProfiles: vi.fn(),
    open: vi.fn(),
  },
}));

import { SftpView } from '../SftpView';
import { useRemoteStore, type SessionStatus } from '../../../stores/remote-store';

/** 造一个指定状态的 SFTP 标签 */
function setTab(status: SessionStatus | null): void {
  useRemoteStore.setState({
    tabs:
      status === null
        ? []
        : [
            {
              id: 's1',
              kind: 'sftp',
              connectionId: 1,
              title: '测试-公司自建',
              subtitle: 'yeahpay@127.0.0.1',
              port: 22,
              token: 'tok',
              status,
              view: 'files',
              jumpChain: [],
              message: '',
            },
          ],
    activeTabId: status === null ? null : 's1',
  });
}

const ENTRIES = {
  entries: [
    { name: 'opt', size: 0, mode: 0o040755, is_dir: true, mtime: 1700000000 },
    { name: 'readme.txt', size: 12, mode: 0o100644, is_dir: false, mtime: 1700000000 },
  ],
};

beforeEach(() => {
  sftpList.mockReset();
  sftpList.mockResolvedValue(ENTRIES);
});

afterEach(() => {
  act(() => setTab(null));
});

describe('SftpView · 会话就绪门控（v1.10.12）', () => {
  it('连接中不拉目录；就绪后自动加载并渲染', async () => {
    setTab('connecting');
    render(<SftpView sessionId="s1" />);

    expect(screen.getByText(/会话正在连接/)).toBeTruthy();
    // 关键：连接中一次都不该问 Python 要目录（否则会拿到「会话尚未建立」并永久卡住）
    expect(sftpList).not.toHaveBeenCalled();

    await act(async () => {
      setTab('ready');
    });

    await waitFor(() => expect(sftpList).toHaveBeenCalledWith('s1', '.'));
    await waitFor(() => expect(screen.getByText('readme.txt')).toBeTruthy());
    expect(screen.queryByText(/会话正在连接/)).toBeNull();
  });

  it('会话失败：给「会话未就绪」提示且不发请求', async () => {
    setTab('failed');
    render(<SftpView sessionId="s1" />);

    expect(screen.getByText(/会话未就绪/)).toBeTruthy();
    expect(sftpList).not.toHaveBeenCalled();
  });

  it('store 里查不到该会话时保持旧行为：挂载即加载', async () => {
    setTab(null);
    render(<SftpView sessionId="s1" />);

    await waitFor(() => expect(sftpList).toHaveBeenCalledWith('s1', '.'));
    await waitFor(() => expect(screen.getByText('opt')).toBeTruthy());
  });

  /**
   * 兜底自愈：`remote.session_ready` 若在标签创建**之前**到达（Python 侧先起后台建链线程
   * 再返回 session_id），事件会打在还不存在的标签上 = no-op ⇒ store 永远 `connecting`。
   * 只信 store 会把面板永久停在「正在连接」，所以连接中要按秒探活。
   */
  it('就绪事件丢失（store 一直 connecting）⇒ 兜底探活自愈并渲染目录', async () => {
    vi.useFakeTimers();
    try {
      setTab('connecting');
      render(<SftpView sessionId="s1" />);
      expect(screen.getByText(/会话正在连接/)).toBeTruthy();
      expect(sftpList).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100);
      });

      expect(sftpList).toHaveBeenCalledTimes(1);
      expect(screen.getByText('readme.txt')).toBeTruthy();
      expect(screen.queryByText(/会话正在连接/)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

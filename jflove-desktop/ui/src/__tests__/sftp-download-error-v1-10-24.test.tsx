/**
 * SFTP 下载失败必须可见（v1.10.24）
 *
 * 用户实测：远程连接点「下载」无反应。根因之一是受限服务器拒绝第二条 SFTP 通道，
 * 桥返回错误后前端 `downloadOne` 没有 catch —— Promise 拒绝无人处理，界面无任何反馈。
 *
 * 判据：
 * 1. `sftpDownload` 拒绝 ⇒ 传输队列出现失败行（含文件名与原因）；
 * 2. 批量下载：单个失败不中断整批，结束时给成功/失败汇总；
 * 3. 完成/失败事件先于响应到达 ⇒ 行会被补建（不能丢状态）。
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { sftpList, sftpDownload, callBridge, handlers } = vi.hoisted(() => {
  const handlers = new Map<string, Set<(payload: unknown) => void>>();
  return {
    sftpList: vi.fn(),
    sftpDownload: vi.fn(),
    callBridge: vi.fn(),
    handlers,
  };
});

vi.mock('../utils/desktop-bridge', () => ({
  callBridge,
  isBridgeReady: () => true,
  onBridgeEvent: (topic: string, handler: (payload: unknown) => void) => {
    let set = handlers.get(topic);
    if (!set) {
      set = new Set();
      handlers.set(topic, set);
    }
    set.add(handler);
    return () => set?.delete(handler);
  },
  getChannelObject: () => null,
  whenBridgeReady: async () => undefined,
}));

vi.mock('../services/remote-service', () => ({
  remoteService: {
    sftpList,
    sftpDownload,
    sftpAbort: vi.fn(),
    sftpCancel: vi.fn(),
    sftpMkdir: vi.fn(),
    sftpRename: vi.fn(),
    sftpDelete: vi.fn(),
    sftpUpload: vi.fn(),
  },
  localTermService: {},
}));

import { SftpView } from '../components/remote/SftpView';
import { useRemoteStore } from '../stores/remote-store';

const ENTRIES = [
  { name: 'remote-a.txt', size: 10, mode: 0o100644, is_dir: false, mtime: 1 },
  { name: 'remote-b.txt', size: 20, mode: 0o100644, is_dir: false, mtime: 2 },
];

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

/** 触发一次桥事件（SftpView 的 useEffect 已订阅） */
function emit(topic: string, payload: unknown): void {
  const set = handlers.get(topic);
  if (!set) return;
  act(() => {
    set.forEach((handler) => handler(payload));
  });
}

beforeEach(() => {
  sftpList.mockReset();
  sftpDownload.mockReset();
  callBridge.mockReset();
  handlers.clear();
  callBridge.mockImplementation(async (_service: string, method: string) => {
    if (method === 'save_file') return { path: '/tmp/jf-download.bin' };
    if (method === 'select_directory') return { path: '/tmp/jf-dir' };
    return { files: [] };
  });
  sftpList.mockResolvedValue({ entries: ENTRIES });
  act(() => setReadyTab());
});

afterEach(() => cleanup());

describe('SFTP 下载失败可见性', () => {
  it('桥调用拒绝：传输队列显示失败原因（不再静默）', async () => {
    sftpDownload.mockRejectedValueOnce(
      new Error('服务器拒绝打开新的 SFTP 通道（可能限制了并发会话数）'),
    );
    render(<SftpView sessionId="s1" />);
    await screen.findByText('remote-a.txt');
    await waitFor(() => expect(sftpList).toHaveBeenCalledWith('s1', '.'));

    const downloadButtons = screen.getAllByText('下载');
    fireEvent.click(downloadButtons[0]);

    await waitFor(() =>
      expect(
        screen.getByText('服务器拒绝打开新的 SFTP 通道（可能限制了并发会话数）'),
      ).toBeTruthy(),
    );
    expect(screen.getByText('传输队列')).toBeTruthy();
    expect(sftpDownload).toHaveBeenCalledTimes(1);
  });

  it('批量下载：第一笔失败不中断第二笔，并显示汇总', async () => {
    sftpDownload
      .mockRejectedValueOnce(new Error('权限不足'))
      .mockResolvedValueOnce({ transfer_id: 't-ok' });
    render(<SftpView sessionId="s1" />);
    await screen.findByText('remote-a.txt');

    const checkboxes = screen.getAllByRole('checkbox');
    // [0]=全选，[1]=remote-a，[2]=remote-b
    fireEvent.click(checkboxes[1]);
    fireEvent.click(checkboxes[2]);
    fireEvent.click(screen.getByText(/下载所选/));

    await waitFor(() => expect(sftpDownload).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        screen.getByText('批量下载：已发起 2 个，1 个启动失败（最终结果见传输队列）'),
      ).toBeTruthy(),
    );
    // 成功的第二笔仍然建了行（文件列表与传输队列各一份）
    expect(screen.getAllByText('remote-b.txt').length).toBeGreaterThan(1);
  });

  it('下载完成事件先于响应到达：行被补建为「完成」', async () => {
    render(<SftpView sessionId="s1" />);
    await screen.findByText('remote-a.txt');
    emit('remote.sftp_done', {
      transfer_id: 't-early',
      name: 'early.bin',
      direction: 'download',
      bytes_total: 12,
    });
    expect(screen.getByText('early.bin')).toBeTruthy();
    expect(screen.getByText('传输队列')).toBeTruthy();
  });

  it('下载失败事件先于响应到达：行被补建并带原因', async () => {
    render(<SftpView sessionId="s1" />);
    await screen.findByText('remote-a.txt');
    emit('remote.sftp_failed', {
      transfer_id: 't-early-fail',
      name: 'early-fail.bin',
      direction: 'download',
      bytes_total: 7,
      message: '服务器拒绝打开新的 SFTP 通道',
    });
    expect(screen.getByText('early-fail.bin')).toBeTruthy();
    expect(screen.getByText('服务器拒绝打开新的 SFTP 通道')).toBeTruthy();
  });
});


  it('其他会话的事件不会在本标签建行（多标签常驻挂载）', async () => {
    render(<SftpView sessionId="s1" />);
    await screen.findByText('remote-a.txt');
    emit('remote.sftp_done', {
      transfer_id: 't-other',
      session_id: 's2',
      name: 'other-session.bin',
      direction: 'download',
      bytes_total: 12,
    });
    expect(screen.queryByText('other-session.bin')).toBeNull();

    emit('remote.sftp_done', {
      transfer_id: 't-self',
      session_id: 's1',
      name: 'self-session.bin',
      direction: 'download',
      bytes_total: 12,
    });
    expect(screen.getByText('self-session.bin')).toBeTruthy();
  });

/**
 * v1.10.10 远程会话回归
 *
 * 钉住三条产品语义：
 * 1. 只有**就绪过**的会话结束时才自动移除标签；连接失败/连接中的标签保留；
 * 2. 移除当前标签后激活剩余最后一个，全部移除后 activeTabId = null；
 * 3. 会话事件订阅常驻（AppLayout），表单不再出现分组字段。
 */
import { readFileSync } from 'node:fs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionTab } from '../stores/remote-store';

vi.mock('../services/remote-service', () => ({
  remoteService: {
    closeSession: vi.fn().mockResolvedValue({ ok: true }),
    listConnections: vi.fn().mockResolvedValue([]),
    createGroup: vi.fn(),
    deleteGroup: vi.fn(),
  },
  localTermService: {
    open: vi.fn(),
    status: vi.fn().mockResolvedValue({ status: 'pending' }),
  },
}));

import { useRemoteStore } from '../stores/remote-store';

function makeTab(id: string, status: SessionTab['status']): SessionTab {
  return {
    id,
    kind: 'ssh',
    connectionId: 1,
    title: `会话-${id}`,
    subtitle: 'root@example.com',
    port: 10001,
    token: 'token',
    status,
    view: 'terminal',
    jumpChain: [],
    message: '',
  };
}

const read = (path: string) => readFileSync(path, 'utf8');

describe('handleSessionExit：自动关闭语义', () => {
  beforeEach(() => {
    useRemoteStore.setState({ tabs: [], activeTabId: null });
  });

  it('就绪会话结束 → 移除标签、返回被关标签、清空激活标签', () => {
    useRemoteStore.setState({ tabs: [makeTab('s1', 'ready')], activeTabId: 's1' });

    const closed = useRemoteStore.getState().handleSessionExit('s1', '远端已断开');

    expect(closed?.id).toBe('s1');
    expect(closed?.message).toBe('远端已断开');
    expect(useRemoteStore.getState().tabs).toHaveLength(0);
    expect(useRemoteStore.getState().activeTabId).toBeNull();
  });

  it('关闭当前标签后激活剩余最后一个', () => {
    useRemoteStore.setState({
      tabs: [makeTab('s1', 'ready'), makeTab('s2', 'ready')],
      activeTabId: 's1',
    });

    useRemoteStore.getState().handleSessionExit('s1', '已断开');

    const state = useRemoteStore.getState();
    expect(state.tabs.map((tab) => tab.id)).toEqual(['s2']);
    expect(state.activeTabId).toBe('s2');
  });

  it('失败标签保留（用户要看错误原因）', () => {
    useRemoteStore.setState({ tabs: [makeTab('s1', 'failed')], activeTabId: 's1' });

    const closed = useRemoteStore.getState().handleSessionExit('s1', '认证失败');

    expect(closed).toBeNull();
    expect(useRemoteStore.getState().tabs).toHaveLength(1);
    expect(useRemoteStore.getState().tabs[0].status).toBe('failed');
  });

  it('连接中的标签不被 closed 事件误关（认证失败会先发 closed）', () => {
    useRemoteStore.setState({ tabs: [makeTab('s1', 'connecting')], activeTabId: 's1' });

    expect(useRemoteStore.getState().handleSessionExit('s1', '已断开')).toBeNull();
    expect(useRemoteStore.getState().tabs).toHaveLength(1);
  });

  it('重复的 closed 事件幂等（标签已移除 → no-op）', () => {
    useRemoteStore.setState({ tabs: [makeTab('s1', 'ready')], activeTabId: 's1' });
    useRemoteStore.getState().handleSessionExit('s1', '已断开');

    expect(useRemoteStore.getState().handleSessionExit('s1', '已断开')).toBeNull();
  });
});

describe('markSessionReady：首帧输出兜底', () => {
  beforeEach(() => {
    useRemoteStore.setState({ tabs: [], activeTabId: null });
  });

  it('connecting → ready', () => {
    useRemoteStore.setState({ tabs: [makeTab('s1', 'connecting')], activeTabId: 's1' });
    useRemoteStore.getState().markSessionReady('s1');
    expect(useRemoteStore.getState().tabs[0].status).toBe('ready');
  });

  it('不覆盖失败状态', () => {
    useRemoteStore.setState({ tabs: [makeTab('s1', 'failed')], activeTabId: 's1' });
    useRemoteStore.getState().markSessionReady('s1');
    expect(useRemoteStore.getState().tabs[0].status).toBe('failed');
  });
});

describe('源码约束：分组入口已移除 / 会话事件常驻', () => {
  it('远程连接表单不再有分组字段，也不再提交 ssh_group_id', () => {
    const source = read('src/components/remote/RemoteConnectionForm.tsx');
    expect(source).not.toContain('label="分组"');
    expect(source).not.toContain('ssh_group_id');
  });

  it('本机配置表单与遗留本机终端页不再有分组字段', () => {
    const remotePage = read('src/pages/remote/RemotePage.tsx');
    const localPage = read('src/pages/remote/LocalTerminalPage.tsx');
    expect(remotePage).not.toContain('label="分组"');
    expect(localPage).not.toContain('本地分组');
    expect(localPage).not.toContain('>分组<');
  });

  it('远程页左侧树有置顶「会话」节点，并列出 store 里的标签', () => {
    const source = read('src/pages/remote/RemotePage.tsx');
    expect(source).toContain('label="会话"');
    expect(source).toContain('TreeSession');
    expect(source).toContain('tabs.map');
  });

  it('会话事件订阅挂在 AppLayout 常驻布局', () => {
    const layout = read('src/layouts/AppLayout.tsx');
    const sync = read('src/utils/remote-session-sync.ts');
    expect(layout).toContain('useRemoteSessionSync()');
    expect(sync).toContain("'remote.session_status'");
    expect(sync).toContain("'local_term.session_status'");
    expect(sync).toContain('handleSessionExit');
    expect(sync).toContain('disposeOrphanTerminals');
  });

  it('会话页不再维护「已结束」横幅与重开入口', () => {
    const source = read('src/pages/remote/SessionPage.tsx');
    expect(source).not.toContain('已结束');
    expect(source).not.toContain('handleReopen');
    expect(source).not.toContain('reopenTab');
  });
});

/**
 * 远程/本机会话事件同步（v1.10.10）
 *
 * 为什么不在 `SessionPage` 里订阅：用户回到连接列表页、文件页后会话页已卸载，
 * 事件无人接收 → 标签状态冻结、`exit` 后标签关不掉（v1.10.9 的僵死标签）。
 *
 * 本 hook 挂在 `AppLayout`（登录后常驻），负责两件事：
 * 1. 把 Python 推送的会话状态写进 `remote-store`；
 * 2. 会话结束（`closed`）时按 store 语义自动关闭标签，并回收孤儿终端运行时。
 *
 * 注意：这里**不**处理会话失败（`session_failed`）的自动关闭 —— 失败标签要留给用户看原因。
 */
import { useEffect } from 'react';
import { onBridgeEvent } from './desktop-bridge';
import { useRemoteStore } from '../stores/remote-store';
import { disposeOrphanTerminals } from '../components/remote/TerminalView';
import { toast } from '../components/ui';

function asRecord(payload: unknown): Record<string, unknown> {
  return payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {};
}

export function useRemoteSessionSync(): void {
  useEffect(() => {
    /** 会话结束：仅就绪会话会被关闭；重复事件因标签已移除而自动 no-op */
    const closeIfEnded = (sessionId: string, message: string) => {
      const closed = useRemoteStore.getState().handleSessionExit(sessionId, message);
      if (!closed) return;
      toast.info(`会话已结束：${closed.title}`, message || '会话已断开');
    };

    const offs = [
      onBridgeEvent('remote.session_status', (payload) => {
        const data = asRecord(payload);
        const sessionId = String(data.session_id || '');
        const phase = String(data.phase || '');
        const message = String(data.message || '');
        const store = useRemoteStore.getState();
        const tab = store.tabs.find((item) => item.id === sessionId);
        if (!tab) return;
        if (phase === 'closed') {
          closeIfEnded(sessionId, message || '远端已断开');
          return;
        }
        if (phase === 'ready') store.markSessionReady(sessionId);
        const chain = [...tab.jumpChain];
        const index = Number(data.jump_index || 0);
        chain[index] = { label: message, phase };
        store.patchTab(sessionId, { jumpChain: chain, message });
      }),
      onBridgeEvent('remote.session_ready', (payload) => {
        const sessionId = String(asRecord(payload).session_id || '');
        useRemoteStore.getState().patchTab(sessionId, { status: 'ready', message: '已连接' });
      }),
      onBridgeEvent('remote.session_failed', (payload) => {
        const data = asRecord(payload);
        const sessionId = String(data.session_id || '');
        useRemoteStore.getState().patchTab(sessionId, {
          status: 'failed',
          message: String(data.message || '连接失败'),
        });
      }),
      onBridgeEvent('local_term.session_status', (payload) => {
        const data = asRecord(payload);
        const sessionId = String(data.session_id || '');
        const phase = String(data.phase || '');
        const message = String(data.message || '');
        const store = useRemoteStore.getState();
        if (!store.tabs.some((item) => item.id === sessionId)) return;
        if (phase === 'closed') {
          closeIfEnded(sessionId, message || '本机终端已退出');
          return;
        }
        if (phase === 'ready') {
          store.markSessionReady(sessionId);
          store.patchTab(sessionId, { message });
        }
      }),
      onBridgeEvent('local_term.session_ready', (payload) => {
        const sessionId = String(asRecord(payload).session_id || '');
        useRemoteStore.getState().patchTab(sessionId, { status: 'ready', message: '已打开' });
      }),
      onBridgeEvent('local_term.session_failed', (payload) => {
        const data = asRecord(payload);
        const sessionId = String(data.session_id || '');
        useRemoteStore.getState().patchTab(sessionId, {
          status: 'failed',
          message: String(data.message || '本机终端启动失败'),
        });
      }),
    ];
    return () => offs.forEach((off) => off());
  }, []);

  // 标签变化后回收不再属于任何标签的终端运行时（含自动关闭、树节点关闭、标签栏关闭）
  useEffect(() => {
    let prevTabs = useRemoteStore.getState().tabs;
    disposeOrphanTerminals(new Set(prevTabs.map((tab) => tab.id)));
    return useRemoteStore.subscribe((state) => {
      if (state.tabs === prevTabs) return;
      prevTabs = state.tabs;
      disposeOrphanTerminals(new Set(state.tabs.map((tab) => tab.id)));
    });
  }, []);
}

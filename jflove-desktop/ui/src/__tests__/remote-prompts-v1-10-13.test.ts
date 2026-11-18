/**
 * 会话提示队列（v1.10.13）
 *
 * 主机指纹 / 键盘交互事件可能早于 SessionPage 挂载到达；由 AppLayout 常驻 hook
 * 收进 store 排队，页面挂载后照常渲染。这里钉住队列的去重、按会话移除与关闭清理。
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { useRemoteStore } from '../stores/remote-store';

beforeEach(() => {
  useRemoteStore.setState({ pendingPrompts: [], tabs: [], activeTabId: null });
});

describe('pendingPrompts', () => {
  it('同一会话同一类型的提示只入队一次', () => {
    const store = useRemoteStore.getState();
    store.pushPrompt({
      kind: 'hostkey',
      sessionId: 's1',
      host: '10.0.0.8',
      fingerprint: 'SHA256:abc',
    });
    store.pushPrompt({
      kind: 'hostkey',
      sessionId: 's1',
      host: '10.0.0.8',
      fingerprint: 'SHA256:abc',
    });
    store.pushPrompt({ kind: 'otp', sessionId: 's1', prompt: 'Password: ' });
    expect(useRemoteStore.getState().pendingPrompts).toHaveLength(2);
  });

  it('回答后按 (sessionId, kind) 精确移除', () => {
    useRemoteStore.getState().pushPrompt({ kind: 'otp', sessionId: 's1', prompt: 'A' });
    useRemoteStore.getState().pushPrompt({ kind: 'otp', sessionId: 's2', prompt: 'B' });
    useRemoteStore.getState().resolvePrompt('s1', 'otp');
    const prompts = useRemoteStore.getState().pendingPrompts;
    expect(prompts).toHaveLength(1);
    expect(prompts[0]).toMatchObject({ sessionId: 's2' });
  });

  it('会话结束/关闭时清理该会话的全部提示', () => {
    useRemoteStore.getState().pushPrompt({ kind: 'hostkey', sessionId: 's1', host: 'h', fingerprint: 'f' });
    useRemoteStore.getState().pushPrompt({ kind: 'otp', sessionId: 's1', prompt: 'p' });
    useRemoteStore.getState().dropPrompts('s1');
    expect(useRemoteStore.getState().pendingPrompts).toHaveLength(0);
  });
});

/**
 * 终端视图回归（v1.10.3）
 *
 * 背景：v1.10.2 的 `TerminalView` 把父组件内联的 `onReady` 放进了 effect 依赖。
 * 连上后 `patchTab` 触发重渲染 → 新 `onReady` → effect 重跑 → 断开重连，
 * 表现为「终端不停闪屏、没有可输入的地方」。
 *
 * 这里用源码扫描锁死：effect 依赖只能是 `[sessionId, port, token]`。
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(
  'src/components/remote/TerminalView.tsx',
  'utf8',
);

describe('TerminalView 连接生命周期', () => {
  it('连接 effect 不依赖 onReady（否则会无限重连闪屏）', () => {
    expect(source).toContain('}, [sessionId, port, token]);');
    expect(source).not.toContain('}, [sessionId, port, token, onReady]);');
  });

  it('onReady 通过 ref 调用，避免父组件每次渲染都触发重连', () => {
    expect(source).toContain('onReadyRef.current');
  });

  it('WebSocket 绑定的是 127.0.0.1 的终端通道', () => {
    expect(source).toContain('ws://127.0.0.1:');
  });
});

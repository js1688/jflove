/**
 * 终端视图回归（v1.10.3 初版 → v1.10.8 扩充）
 *
 * ## 事故 1（v1.10.2/3）：连接 effect 依赖了父组件内联回调
 *
 * `onReady` 被放进 effect 依赖 → 连上后 `patchTab` 触发重渲染 → 新 `onReady`
 * → effect 重跑 → 断开重连，表现为「终端不停闪屏、没有可输入的地方」。
 *
 * ## 事故 2（v1.10.7）：TUI 一启动就"卡住"，切标签回来面板空白
 *
 * ① xterm 6.0.0 的 DECRQM 处理会抛 ReferenceError，终端永久卡死；
 * ② 组件随标签切换卸载重建 → 连接断开、屏幕清空；
 * ③ 用"回放历史输出"补偿，结果回放又触发终端应答 TUI 的能力查询，
 *    应答被 shell 回显成垃圾字符。
 *
 * 这里用源码扫描把三条结构性约束钉死（行为验证见
 * `scripts/verify/desktop_terminal_e2e.mjs` 的实机用例）。
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync('src/components/remote/TerminalView.tsx', 'utf8');

describe('TerminalView 连接生命周期', () => {
  it('连接 effect 不依赖父组件回调（否则会无限重连闪屏）', () => {
    expect(source).toContain('}, [sessionId, port, token]);');
    expect(source).not.toContain('}, [sessionId, port, token, onReady]);');
  });

  it('回调只写到运行时对象上，不进入连接依赖', () => {
    expect(source).toContain('runtime.onReady = onReady');
    expect(source).toContain('runtime.onStatus = onStatus');
  });

  it('WebSocket 绑定的是 127.0.0.1 的终端通道', () => {
    expect(source).toContain('ws://127.0.0.1:');
  });
});

describe('TerminalView 会话级实例注册表（v1.10.8）', () => {
  it('运行时挂在模块级 Map 上（路由切换不销毁）', () => {
    expect(source).toContain('const runtimes = new Map<string, SessionRuntime>();');
    expect(source).toContain('runtimes.get(sessionId)');
  });

  it('卸载只摘 DOM、不关会话', () => {
    expect(source).toContain('host.removeChild(runtime.wrapper)');
    // 旧实现卸载时会 send({type:'close'})，路由切一次就把用户 shell 杀掉
    expect(source).not.toContain("type: 'close'");
  });

  it('不再回放历史输出（回放会触发终端应答 TUI 查询，污染 shell）', () => {
    expect(source).not.toContain("message.type === 'snapshot'");
    expect(source).not.toContain("type: 'snapshot'");
  });

  it('具备 DECRQM 兼容层与写队列看门狗', () => {
    expect(source).toContain('installDecrqmCompat');
    expect(source).toContain('WRITE_WEDGE_MS');
    expect(source).toContain('recoverTerminal');
  });
});

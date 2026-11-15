/**
 * xterm.js DECRQM 兼容层（v1.10.8）
 *
 * ## 背景（真机事故）
 *
 * xterm.js 6.0.0 的生产构建里，`InputHandler.requestMode()` 内部的 `const enum`
 * 被编译成 `(k=>…)(void 0||(r={}))` —— `r` 是个没声明的变量，一调用就抛
 * `ReferenceError: r is not defined`。
 *
 * 触发条件很常见：**任何 TUI 都会用 DECRQM（`CSI ? m $ p`）探测终端能力**，
 * 例如 dsh TUI 每帧都发 `\x1b[?1049$p`。而 xterm 的 `WriteBuffer._innerWrite()`
 * 对解析异常**没有兜底**：抛出后当前 chunk 的 `_bufferOffset` 不前进、
 * 也不再有新的 `_innerWrite` 被调度 —— 终端从此**永久卡死**（画面不再更新，
 * 输入仍进 PTY，表现为"像卡住了一样"）。
 *
 * ## 做法
 *
 * 在 `term.parser` 上注册同标识的 CSI handler。xterm 的派发是**后注册者先执行**
 * （`EscapeSequenceParser` 从 handlerList 末尾往前遍历），因此这里注册的 handler
 * 会先于有缺陷的内置 handler 命中；返回 `true` 即阻止冒泡，异常路径根本不会执行。
 *
 * 回复格式按 DECRPM 规范：`CSI [?]mode;status$y`，status 取 0/1/2/3/4
 * （0 未识别、1 已置位、2 已复位、3 永久置位、4 永久复位）。
 * 值一律从 xterm 自己的 `term.modes` 推导，避免我们与渲染层状态不一致。
 */
import type { Terminal } from '@xterm/xterm';

/** DECRPM 状态码 */
export const DECRPM = {
  NOT_RECOGNIZED: 0,
  SET: 1,
  RESET: 2,
  PERMANENTLY_SET: 3,
  PERMANENTLY_RESET: 4,
} as const;

export type DecrpmStatus = (typeof DECRPM)[keyof typeof DECRPM];

/** 本层用到的终端模式快照（`Terminal.modes` 的子集，便于单测注入） */
export interface TerminalModeSnapshot {
  applicationCursorKeysMode: boolean;
  applicationKeypadMode: boolean;
  bracketedPasteMode: boolean;
  insertMode: boolean;
  mouseTrackingMode: 'none' | 'x10' | 'vt200' | 'drag' | 'any';
  originMode: boolean;
  reverseWraparoundMode: boolean;
  sendFocusMode: boolean;
  synchronizedOutputMode: boolean;
  wraparoundMode: boolean;
  /** 光标是否可见（`Terminal.modes` 未暴露，由调用方传入） */
  cursorVisible?: boolean;
  /** 是否允许回车转义（`convertEol`，默认 false） */
  convertEol?: boolean;
  /** 备用屏是否处于激活状态（由 `term.buffer.active.type` 推导） */
  altScreenActive?: boolean;
}

const b2v = (value: boolean): DecrpmStatus => (value ? DECRPM.SET : DECRPM.RESET);

/**
 * 计算某个模式的 DECRPM 状态码。
 *
 * @param mode 模式号
 * @param ansi `true` 表示 ANSI 模式（`CSI m $ p`），`false` 表示 DEC 私有模式（`CSI ? m $ p`）
 * @param m 终端模式快照
 * @returns DECRPM 状态码
 */
export function decrpmStatus(mode: number, ansi: boolean, m: TerminalModeSnapshot): DecrpmStatus {
  if (ansi) {
    if (mode === 2) return DECRPM.PERMANENTLY_RESET;
    if (mode === 4) return b2v(m.insertMode);
    if (mode === 12) return DECRPM.PERMANENTLY_SET;
    if (mode === 20) return b2v(Boolean(m.convertEol));
    return DECRPM.NOT_RECOGNIZED;
  }
  switch (mode) {
    case 1:
      return b2v(m.applicationCursorKeysMode);
    case 3:
      // 窗口尺寸模式（DECCOLM）不支持：xterm 仅在 windowOptions 开启时回答
      return DECRPM.NOT_RECOGNIZED;
    case 6:
      return b2v(m.originMode);
    case 7:
      return b2v(m.wraparoundMode);
    case 8:
      return DECRPM.PERMANENTLY_SET;
    case 9:
      return b2v(m.mouseTrackingMode === 'x10');
    case 12:
      return DECRPM.SET;
    case 25:
      return b2v(m.cursorVisible ?? true);
    case 45:
      return b2v(m.reverseWraparoundMode);
    case 66:
      return b2v(m.applicationKeypadMode);
    case 67:
      return DECRPM.PERMANENTLY_RESET;
    case 1000:
      return b2v(m.mouseTrackingMode === 'vt200');
    case 1002:
      return b2v(m.mouseTrackingMode === 'drag');
    case 1003:
      return b2v(m.mouseTrackingMode === 'any');
    case 1004:
      return b2v(m.sendFocusMode);
    case 1006:
      // SGR 编码与具体协议无关：只要开了鼠标上报，就说明 SGR 编码被采用
      return b2v(m.mouseTrackingMode !== 'none');
    case 1048:
      return DECRPM.SET;
    case 47:
    case 1047:
    case 1049:
      return b2v(Boolean(m.altScreenActive));
    case 2004:
      return b2v(m.bracketedPasteMode);
    case 2026:
      return b2v(m.synchronizedOutputMode);
    default:
      return DECRPM.NOT_RECOGNIZED;
  }
}

/** 拼出回复给 PTY 的 DECRPM 序列 */
export function buildDecrpmReply(mode: number, ansi: boolean, status: DecrpmStatus): string {
  return `\u001b[${ansi ? '' : '?'}${mode};${status}$y`;
}

/**
 * 取 DECRQM 的第一个参数。
 *
 * xterm 6 的 `registerCsiHandler` 回调直接收到参数数组（子参数为数组），
 * 这里只关心首个数值参数，缺失/非法一律按 0 处理。
 */
function firstParam(params: ArrayLike<number | number[]> | undefined): number {
  const value = params?.[0];
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

/** 从 xterm 实例读取模式快照 */
export function snapshotModes(term: Terminal): TerminalModeSnapshot {
  const modes = term.modes as unknown as TerminalModeSnapshot;
  return {
    ...modes,
    altScreenActive: term.buffer.active.type === 'alternate',
  };
}

/**
 * 安装 DECRQM 兼容 handler（幂等：调用方每次新建 terminal 时调用一次即可）。
 *
 * @param term xterm 实例
 * @param send 把回复写回 PTY 的回调（走终端 WebSocket 的 input 通道）
 * @returns 释放函数
 */
export function installDecrqmCompat(
  term: Terminal,
  send: (data: string) => void,
): () => void {
  const reply = (mode: number, ansi: boolean) => {
    const status = decrpmStatus(mode, ansi, snapshotModes(term));
    send(buildDecrpmReply(mode, ansi, status));
    return true;
  };
  // xterm 6 的 registerCsiHandler 返回值类型是 `IDisposable | void`
  const disposables: Array<{ dispose(): void } | void> = [
    term.parser.registerCsiHandler({ intermediates: '$', final: 'p' }, (params) =>
      reply(firstParam(params), true),
    ),
    term.parser.registerCsiHandler({ prefix: '?', intermediates: '$', final: 'p' }, (params) =>
      reply(firstParam(params), false),
    ),
  ];
  return () => disposables.forEach((item) => item?.dispose());
}

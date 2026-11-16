/**
 * 终端视图（xterm.js）
 *
 * 数据来自 Python 侧的本机 WebSocket 通道（只监听 127.0.0.1）：
 * 下行二进制帧 = PTY/SSH 输出的 UTF-8 字节；上行 JSON = 输入 / resize / close。
 * 渲染层不持有 SSH/PTY 句柄，也拿不到任何凭据（`文档记录/设计文档/v1.10.0.md` §3.4）。
 *
 * ## v1.10.8 重建（plans/desktop-terminal-base-v1.10.8.md）
 *
 * 1. **DECRQM 兼容层**：xterm 6.0.0 生产包的 DECRQM 处理会抛 `ReferenceError`
 *    并让终端**永久**卡死（TUI 每帧都探测终端能力）。见 `utils/xterm-decrqm.ts`。
 * 2. **实例注册表（关键）**：xterm 实例 + WebSocket 挂在模块级 registry 里，
 *    生命周期跟**会话**走而不是跟组件走。路由切换（去列表页再回来）只是把
 *    DOM 摘下来再挂回去，屏幕内容、滚动位置、通道全都不动。
 *    —— 旧实现把"重挂"当成"新终端"，回放历史时还会触发终端对 TUI 能力查询的
 *    应答（应答被 shell 回显成垃圾字符），这条路已经整条删掉。
 * 3. **写入看门狗**：xterm 的写队列一旦因解析异常卡住，后续写入永远排队。
 *    这里对"写入 5s 没被确认"的实例重建 xterm（保留 WS），再让远端整屏重绘。
 * 4. **输入法/中文**：不改 textarea 的 composition 行为，只做按键级快捷键拦截。
 */
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { Terminal, type ITheme } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { readTerminalShortcuts } from '../../utils/terminal-shortcuts';
import { installDecrqmCompat } from '../../utils/xterm-decrqm';
import { copyText, readClipboardText } from '../../utils/clipboard';
import { FONT_MIN, TUI_MIN_ROWS, readAutoFitRows } from '../../utils/terminal-prefs';
import { COMFORT_LINE_HEIGHT, buildFitCandidates, pickFitForMinRows } from '../../utils/terminal-fit';

export interface TerminalStatus {
  /** connecting = 通道建立中；ready = 可用；exited = 进程/链路已结束 */
  state: 'connecting' | 'ready' | 'exited';
  cols: number;
  rows: number;
  cursorRow: number;
  cursorCol: number;
  /** 结束原因（state=exited 时） */
  exitMessage: string;
  /** 用户设定字号 */
  userFontSize: number;
  /** 实际生效字号（窗口偏矮时会自动缩小） */
  effectiveFontSize: number;
  /** 是否因行数不足自动缩小了字号 */
  autoShrunk: boolean;
  /** 即使最小字号也不足 TUI 所需行数 */
  tooSmall: boolean;
  /** TUI 所需最小行数 */
  minRows: number;
}

export interface TerminalHandle {
  fit(): void;
  focus(): void;
  clear(): void;
  copy(): Promise<boolean>;
  paste(): Promise<boolean>;
  /** 请求整屏重绘（备用屏 TUI 靠 SIGWINCH 触发） */
  redraw(): void;
}

interface TerminalViewProps {
  sessionId: string;
  port: number;
  token: string;
  /** 是否为当前可见标签：可见时才 fit + 抢焦点 */
  active: boolean;
  fontSize: number;
  onReady?: () => void;
  onStatus?: (status: TerminalStatus) => void;
  onExit?: (message: string) => void;
}

/** 终端配色：与产品深色代码块同色系（对比度按 WCAG AA 调过） */
const THEME: ITheme = {
  background: '#0b1220',
  foreground: '#e6edf7',
  cursor: '#f8fafc',
  cursorAccent: '#0b1220',
  selectionBackground: 'rgba(99, 102, 241, 0.42)',
  black: '#0f172a',
  red: '#f87171',
  green: '#4ade80',
  yellow: '#fbbf24',
  blue: '#60a5fa',
  magenta: '#c084fc',
  cyan: '#22d3ee',
  white: '#e2e8f0',
  brightBlack: '#64748b',
  brightRed: '#fca5a5',
  brightGreen: '#86efac',
  brightYellow: '#fde047',
  brightBlue: '#93c5fd',
  brightMagenta: '#d8b4fe',
  brightCyan: '#67e8f9',
  brightWhite: '#f8fafc',
};

/** 等宽字体：优先终端常用字体，再补中文等宽，避免中文回退成比例字体 */
const FONT_FAMILY = [
  "'JetBrains Mono'",
  "'Cascadia Code'",
  "'Fira Code'",
  "'DejaVu Sans Mono'",
  "'Noto Sans Mono CJK SC'",
  "'Source Han Mono SC'",
  "'WenQuanYi Micro Hei Mono'",
  'monospace',
].join(', ');

/** 写入多久没被 xterm 确认就认为写队列卡死（毫秒） */
const WRITE_WEDGE_MS = 5000;

/** 单帧最多写到 xterm 的字节数（避免一次 write 处理过久导致掉帧） */
const WRITE_CHUNK = 64 * 1024;

interface ClipboardActions {
  copy(): Promise<boolean>;
  paste(): Promise<boolean>;
}

/** 一个终端会话的运行时（xterm + WebSocket），生命周期与会话一致 */
interface SessionRuntime {
  sessionId: string;
  port: number;
  token: string;
  /** xterm 挂载用的稳定容器（组件卸载时只摘 DOM，不销毁实例） */
  wrapper: HTMLDivElement;
  /**
   * xterm 实例。**必须等容器进了 DOM 才创建**：在游离节点上 `term.open()`
   * 拿不到字体度量，行列数会退回 80×24，随后 fit 再改尺寸 —— TUI 会先按
   * 80 列画一帧、再收到 SIGWINCH 局部重绘，屏幕就是花的（v1.10.8 实测）。
   */
  term: Terminal | null;
  fit: FitAddon | null;
  ws: WebSocket | null;
  ready: boolean;
  /** 用户设定字号（设置页 / 工具条 +/-） */
  userFontSize: number;
  /** 实际生效字号（按行数自适应后） */
  effectiveFontSize: number;
  /** 是否因为行数不足而缩小了字号 */
  autoShrunk: boolean;
  /** 即使缩到最小字号也不够 TUI 行数 */
  tooSmall: boolean;
  /** 正在做"字号→行数"测量：此期间的 resize 不往 PTY 发 */
  measuring: boolean;
  active: boolean;
  actions: ClipboardActions;
  onStatus?: (status: TerminalStatus) => void;
  onExit?: (message: string) => void;
  onReady?: () => void;
  /** 订阅释放函数（xterm 重建时重装） */
  disposal: Array<() => void>;
  /** 待写入队列（按帧合并） */
  pending: Uint8Array[];
  /** 待写入字节数（背压上限判断） */
  pendingBytes: number;
  frame: number;
  pendingSince: number;
  watchdog: number;
  resizeObserver: ResizeObserver | null;
  resizeTimer: number;
  /** 上一次上报状态的时间（光标移动会高频触发，必须节流） */
  lastStatusAt: number;
  /** WS 重连次数（本地通道偶发断开时自愈） */
  reconnectAttempts: number;
  disposed: boolean;
}

/** 会话 → 运行时（模块级：路由切换不销毁） */
const runtimes = new Map<string, SessionRuntime>();

const sendMessage = (runtime: SessionRuntime, payload: Record<string, unknown>) => {
  const ws = runtime.ws;
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
};

/** 状态节流间隔（毫秒）：光标移动在高刷新 TUI 里可能每帧都触发 */
const STATUS_INTERVAL_MS = 120;

const notifyStatus = (runtime: SessionRuntime, force = false) => {
  const term = runtime.term;
  if (!term) return;
  const now = performance.now();
  if (!force && now - runtime.lastStatusAt < STATUS_INTERVAL_MS) return;
  runtime.lastStatusAt = now;
  runtime.onStatus?.({
    state: runtime.ready ? 'ready' : 'connecting',
    cols: term.cols,
    rows: term.rows,
    cursorRow: term.buffer.active.cursorY + 1,
    cursorCol: term.buffer.active.cursorX + 1,
    exitMessage: '',
    userFontSize: runtime.userFontSize,
    effectiveFontSize: runtime.effectiveFontSize || runtime.userFontSize,
    autoShrunk: runtime.autoShrunk,
    tooSmall: runtime.tooSmall,
    minRows: TUI_MIN_ROWS,
  });
};

/**
 * 对齐尺寸（唯一入口）：先按行数反推字号，再 fit。
 *
 * 为什么需要：dsh TUI 在 ≤21 行会把输入框内容行画丢（tmux 同样复现，属上位程序缺陷）。
 * 这里保证终端**至少 TUI_MIN_ROWS 行**：窗口偏矮时把字号往下调（下限 FONT_MIN），
 * 窗口拉高后自动回到用户设定字号；实在放不下就把 tooSmall 上报给界面显示提示。
 */
const applyFit = (runtime: SessionRuntime) => {
  const term = runtime.term;
  const fit = runtime.fit;
  if (!term || !fit || runtime.disposed) return;
  if (!runtime.wrapper.isConnected) return;
  if (!runtime.wrapper.clientWidth || !runtime.wrapper.clientHeight) return;

  const userSize = Math.min(22, Math.max(FONT_MIN, Math.round(runtime.userFontSize)));
  const auto = readAutoFitRows();
  const candidates = auto
    ? buildFitCandidates(userSize, FONT_MIN)
    : [{ fontSize: userSize, lineHeight: COMFORT_LINE_HEIGHT }];

  runtime.measuring = true;
  let result;
  try {
    result = pickFitForMinRows(candidates, TUI_MIN_ROWS, (candidate) => {
      term.options.fontSize = candidate.fontSize;
      term.options.lineHeight = candidate.lineHeight;
      fit.fit();
      return term.rows;
    });
    // 测量过程会把参数停在中途候选，最后显式落到结果候选
    term.options.fontSize = result.fontSize;
    term.options.lineHeight = result.lineHeight;
    fit.fit();
  } finally {
    runtime.measuring = false;
  }

  runtime.effectiveFontSize = result.fontSize;
  runtime.autoShrunk = auto && result.compacted;
  runtime.tooSmall = term.rows < TUI_MIN_ROWS;
  term.refresh(0, Math.max(0, term.rows - 1));
  // 仅一次 resize 下发（测量期间的 onResize 被 measuring 拦掉）
  sendMessage(runtime, { type: 'resize', cols: term.cols, rows: term.rows });
  notifyStatus(runtime, true);
};

const flushPending = (runtime: SessionRuntime) => {
  runtime.frame = 0;
  if (runtime.pending.length === 0 || runtime.disposed) return;
  if (!runtime.term) {
    // 终端还没挂载（容器未进 DOM）：先攒着，挂载后由 mountTerminal 触发刷新
    runtime.frame = window.requestAnimationFrame(() => flushPending(runtime));
    return;
  }
  const total = runtime.pending.reduce((sum, item) => sum + item.length, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const item of runtime.pending) {
    merged.set(item, offset);
    offset += item.length;
  }
  runtime.pending = [];
  runtime.pendingBytes = 0;
  for (let start = 0; start < merged.length; start += WRITE_CHUNK) {
    const chunk = merged.subarray(start, Math.min(start + WRITE_CHUNK, merged.length));
    if (!runtime.pendingSince) runtime.pendingSince = performance.now();
    try {
      runtime.term.write(chunk, () => {
        runtime.pendingSince = 0;
      });
    } catch {
      // 解析器同步抛异常：直接标记超时，交给看门狗自愈
      runtime.pendingSince = performance.now() - WRITE_WEDGE_MS;
    }
  }
};

/** 待写入队列上限：超出后丢最旧的（宁可少显示，也不能把渲染进程撑爆） */
const PENDING_LIMIT = 8 * 1024 * 1024;

const enqueueOutput = (runtime: SessionRuntime, data: Uint8Array) => {
  if (data.length === 0) return;
  runtime.pending.push(data);
  runtime.pendingBytes += data.length;
  while (runtime.pendingBytes > PENDING_LIMIT && runtime.pending.length > 1) {
    const dropped = runtime.pending.shift();
    runtime.pendingBytes -= dropped?.length ?? 0;
    console.warn('[terminal] 输出积压超过上限，丢弃最旧的一段：', dropped?.length, '字节');
  }
  if (runtime.frame === 0) runtime.frame = window.requestAnimationFrame(() => flushPending(runtime));
};

const clearQueue = (runtime: SessionRuntime) => {
  runtime.pending = [];
  runtime.pendingBytes = 0;
  if (runtime.frame) {
    window.cancelAnimationFrame(runtime.frame);
    runtime.frame = 0;
  }
};

/** 释放 xterm 的订阅（保留 WS 与容器） */
const unsubscribeTerm = (runtime: SessionRuntime) => {
  runtime.disposal.forEach((dispose) => {
    try {
      dispose();
    } catch {
      // 释放阶段忽略
    }
  });
  runtime.disposal = [];
};

/** 创建并挂载 xterm（自愈时重复调用） */
const mountTerminal = (runtime: SessionRuntime) => {
  if (runtime.disposed || runtime.term) return;
  const term = new Terminal({
    fontFamily: FONT_FAMILY,
    fontSize: runtime.userFontSize,
    lineHeight: COMFORT_LINE_HEIGHT,
    letterSpacing: 0,
    cursorBlink: true,
    cursorStyle: 'block',
    scrollback: 10000,
    smoothScrollDuration: 0,
    allowProposedApi: true,
    convertEol: false,
    theme: THEME,
    screenReaderMode: false,
  });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(runtime.wrapper);
  runtime.term = term;
  runtime.fit = fit;

  const copySelection = async () => {
    const selection = term.getSelection();
    if (!selection) return false;
    return copyText(selection);
  };
  const pasteClipboard = async () => {
    const text = await readClipboardText();
    if (!text) return false;
    term.paste(text);
    return true;
  };
  runtime.actions = { copy: copySelection, paste: pasteClipboard };

  runtime.disposal.push(
    installDecrqmCompat(term, (data) => sendMessage(runtime, { type: 'input', data })),
    () => term.dispose(),
  );
  [
    term.onData((data) => sendMessage(runtime, { type: 'input', data })),
    term.onResize(({ cols, rows }) => {
      if (runtime.measuring) return; // 字号测量期间的中间尺寸不下发，由 applyFit 统一发一次
      sendMessage(runtime, { type: 'resize', cols, rows });
      notifyStatus(runtime);
    }),
    term.onCursorMove(() => notifyStatus(runtime)),
  ].forEach((sub) => runtime.disposal.push(() => sub.dispose()));

  // 复制 / 粘贴：模式来自设置页（默认 Ctrl+C / Ctrl+V）。
  // 注意 `attachCustomKeyEventHandler` 返回 void（xterm 在 dispose 时自行清理），
  // 不要塞进 disposal 数组。
  term.attachCustomKeyEventHandler((event) => {
    if (event.type !== 'keydown' || !event.ctrlKey) return true;
    const mode = readTerminalShortcuts().copyPasteMode;
    const withShift = event.shiftKey;
    if (event.code === 'KeyC') {
      if (mode === 'ctrlShift' && withShift) {
        void copySelection();
        return false;
      }
      if (mode === 'ctrl') {
        void copySelection();
        return false;
      }
      if (mode === 'smart' && term.hasSelection()) {
        void copySelection();
        return false;
      }
      return true; // smart 且无选区：交给 shell（SIGINT）
    }
    if (event.code === 'KeyV') {
      if (mode === 'ctrlShift' && !withShift) return true;
      void pasteClipboard();
      return false;
    }
    return true;
  });

  window.setTimeout(() => {
    if (runtime.disposed || !runtime.wrapper.isConnected || runtime.term !== term) return;
    applyFit(runtime);
  }, 30);
  if (runtime.active) term.focus();
  notifyStatus(runtime);
  flushPending(runtime);
};

/** 终端写队列卡死后的自愈：换一个 xterm，保留 WS，让远端整屏重绘 */
const recoverTerminal = (runtime: SessionRuntime, reason: string) => {
  if (runtime.disposed) return;
  console.warn('[terminal] 写入卡死，重建终端实例：', runtime.sessionId, reason);
  clearQueue(runtime);
  unsubscribeTerm(runtime);
  runtime.term = null;
  runtime.fit = null;
  mountTerminal(runtime);
  sendMessage(runtime, { type: 'redraw' });
};

const startWatchdog = (runtime: SessionRuntime) => {
  runtime.watchdog = window.setInterval(() => {
    if (runtime.disposed || !runtime.pendingSince) return;
    if (performance.now() - runtime.pendingSince < WRITE_WEDGE_MS) return;
    recoverTerminal(runtime, 'ack-timeout');
  }, 1000);
};

const handleControl = (runtime: SessionRuntime, raw: string) => {
  let message: Record<string, unknown>;
  try {
    message = JSON.parse(raw);
  } catch {
    return;
  }
  if (typeof message !== 'object' || message === null) return;
  if (message.type === 'exit') {
    runtime.ready = false;
    runtime.onExit?.(String(message.message || '终端已退出'));
    notifyStatus(runtime, true);
  }
};

/** 建立（或复用）某会话的运行时 */
const ensureRuntime = (sessionId: string, port: number, token: string): SessionRuntime => {
  const existing = runtimes.get(sessionId);
  if (existing) return existing;

  const wrapper = document.createElement('div');
  wrapper.className = 'h-full w-full';
  wrapper.dataset.testid = 'terminal-host';

  const runtime = {
    sessionId,
    port,
    token,
    wrapper,
    term: null as unknown as Terminal,
    fit: null as unknown as FitAddon,
    ws: null as WebSocket | null,
    ready: false,
    userFontSize: 13,
    effectiveFontSize: 13,
    autoShrunk: false,
    tooSmall: false,
    measuring: false,
    active: false,
    actions: {
      copy: async () => false,
      paste: async () => false,
    },
    disposal: [] as Array<() => void>,
    pending: [] as Uint8Array[],
    pendingBytes: 0,
    frame: 0,
    pendingSince: 0,
    watchdog: 0,
    resizeObserver: null as ResizeObserver | null,
    resizeTimer: 0,
    lastStatusAt: 0,
    reconnectAttempts: 0,
    disposed: false,
  } as SessionRuntime;

  runtimes.set(sessionId, runtime);
  startWatchdog(runtime);

  runtime.resizeObserver = new ResizeObserver(() => {
    if (runtime.resizeTimer) window.clearTimeout(runtime.resizeTimer);
    runtime.resizeTimer = window.setTimeout(() => {
      if (runtime.disposed || !runtime.wrapper.isConnected) return;
      if (!runtime.term || !runtime.fit) return;
      if (!wrapper.clientWidth || !wrapper.clientHeight) return;
      applyFit(runtime);
    }, 90);
  });
  runtime.resizeObserver.observe(wrapper);

  const ws = new WebSocket(`ws://127.0.0.1:${port}/term/${sessionId}?token=${token}`);
  attachSocket(runtime, ws);
  return runtime;
};

/** 装配一个 WebSocket（首次连接与重连共用） */
function attachSocket(runtime: SessionRuntime, ws: WebSocket): void {
  ws.binaryType = 'arraybuffer';
  runtime.ws = ws;
  ws.onopen = () => {
    runtime.ready = true;
    if (runtime.fit) {
      try {
        runtime.fit.fit();
      } catch {
        // 忽略
      }
    }
    if (runtime.term) {
      sendMessage(runtime, {
        type: 'resize',
        cols: runtime.term.cols,
        rows: runtime.term.rows,
      });
    }
    runtime.reconnectAttempts = 0;
    runtime.onReady?.();
    if (runtime.active) runtime.term?.focus();
    notifyStatus(runtime, true);
  };
  ws.onmessage = (event: MessageEvent) => {
    if (event.data instanceof ArrayBuffer) {
      enqueueOutput(runtime, new Uint8Array(event.data));
    } else if (typeof event.data === 'string') {
      handleControl(runtime, event.data);
    }
  };
  ws.onclose = () => {
    if (runtime.ready) {
      runtime.ready = false;
      notifyStatus(runtime, true);
    }
    // 本地通道偶发断开（例如渲染进程抖动）时自愈重连；会话本身在 Python 侧还活着
    if (runtime.disposed) return;
    if (runtime.reconnectAttempts >= 5) return;
    runtime.reconnectAttempts += 1;
    window.setTimeout(() => {
      if (runtime.disposed || runtime.ws?.readyState === WebSocket.OPEN) return;
      try {
        const retry = new WebSocket(
          `ws://127.0.0.1:${runtime.port}/term/${runtime.sessionId}?token=${runtime.token}`,
        );
        attachSocket(runtime, retry);
      } catch {
        // 重连失败等下一次
      }
    }, 600 * runtime.reconnectAttempts);
  };
}

/** 销毁某会话的运行时（标签关闭时调用） */
export function disposeTerminalSession(sessionId: string): void {
  const runtime = runtimes.get(sessionId);
  if (!runtime) return;
  runtimes.delete(sessionId);
  runtime.disposed = true;
  window.clearInterval(runtime.watchdog);
  window.clearTimeout(runtime.resizeTimer);
  if (runtime.frame) window.cancelAnimationFrame(runtime.frame);
  runtime.resizeObserver?.disconnect();
  try {
    runtime.ws?.close();
  } catch {
    // 关闭阶段忽略
  }
  unsubscribeTerm(runtime);
  runtime.term = null;
  runtime.wrapper.remove();
}

/** 清理不再属于任何标签的运行时（标签关闭） */
export function disposeOrphanTerminals(aliveSessionIds: ReadonlySet<string>): void {
  for (const sessionId of [...runtimes.keys()]) {
    if (!aliveSessionIds.has(sessionId)) disposeTerminalSession(sessionId);
  }
}

export const TerminalView = forwardRef<TerminalHandle, TerminalViewProps>(function TerminalView(
  { sessionId, port, token, active, fontSize, onReady, onStatus, onExit },
  ref,
) {
  const hostRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<SessionRuntime | null>(null);
  const activeRef = useRef(active);
  const fontSizeRef = useRef(fontSize);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    runtime.onReady = onReady;
    runtime.onStatus = onStatus;
    runtime.onExit = onExit;
  }, [onReady, onStatus, onExit]);

  useEffect(() => {
    fontSizeRef.current = fontSize;
    const runtime = runtimeRef.current;
    if (!runtime) return;
    runtime.userFontSize = fontSize;
    applyFit(runtime);
  }, [fontSize]);

  useEffect(() => {
    activeRef.current = active;
    const runtime = runtimeRef.current;
    if (!runtime) return undefined;
    runtime.active = active;
    if (!active) return undefined;
    const timer = window.setTimeout(() => {
      applyFit(runtime);
      runtime.term?.focus();
    }, 30);
    return () => window.clearTimeout(timer);
  }, [active]);

  useImperativeHandle(
    ref,
    () => ({
      fit: () => {
        const runtime = runtimeRef.current;
        if (runtime) applyFit(runtime);
      },
      focus: () => runtimeRef.current?.term?.focus(),
      clear: () => runtimeRef.current?.term?.clear(),
      copy: () => runtimeRef.current?.actions.copy() ?? Promise.resolve(false),
      paste: () => runtimeRef.current?.actions.paste() ?? Promise.resolve(false),
      redraw: () => {
        const runtime = runtimeRef.current;
        if (runtime) sendMessage(runtime, { type: 'redraw' });
      },
    }),
    [],
  );

  // 接管 / 交还 DOM：运行时本身不销毁，屏幕内容与通道都活着
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const runtime = ensureRuntime(sessionId, port, token);
    runtimeRef.current = runtime;
    runtime.userFontSize = fontSizeRef.current;
    runtime.active = activeRef.current;
    runtime.onReady = onReady;
    runtime.onStatus = onStatus;
    runtime.onExit = onExit;
    // ① 先进 DOM：xterm 在游离节点上量不到字体，会退回 80×24
    host.appendChild(runtime.wrapper);
    // ② 再创建/复用 xterm，并立刻按真实容器尺寸对齐
    runtime.userFontSize = fontSizeRef.current;
    if (!runtime.term) {
      mountTerminal(runtime);
    }
    applyFit(runtime);
    if (runtime.active) runtime.term?.focus();

    return () => {
      runtime.onReady = undefined;
      runtime.onStatus = undefined;
      runtime.onExit = undefined;
      if (runtime.wrapper.parentElement === host) host.removeChild(runtime.wrapper);
      runtimeRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, port, token]);

  return (
    <div
      ref={hostRef}
      data-testid="terminal-mount"
      className="h-full w-full overflow-hidden bg-[#0b1220] px-1.5 py-1"
    />
  );
});

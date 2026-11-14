/**
 * 终端视图（xterm.js）
 *
 * 数据来自 Python 侧的本机 WebSocket 通道（只监听 127.0.0.1）：
 * 下行二进制帧 = PTY/SSH 输出的 UTF-8 字节；上行 JSON = 输入 / resize / close。
 * 渲染层不持有 SSH/PTY 句柄，也拿不到任何凭据（`文档记录/设计文档/v1.10.0.md` §3.4）。
 */
import { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

interface TerminalViewProps {
  sessionId: string;
  port: number;
  token: string;
  onReady?: () => void;
}

/** 终端深色主题（与代码块同色系，亮色界面下也不刺眼） */
const THEME = {
  background: '#080d18',
  foreground: '#d6deeb',
  cursor: '#e2e8f0',
  selectionBackground: 'rgba(99, 102, 241, 0.35)',
  black: '#0f172a',
  red: '#f87171',
  green: '#22c55e',
  yellow: '#fbbf24',
  blue: '#60a5fa',
  magenta: '#c084fc',
  cyan: '#22d3ee',
  white: '#e2e8f0',
  brightBlack: '#475569',
  brightRed: '#fca5a5',
  brightGreen: '#4ade80',
  brightYellow: '#fde047',
  brightBlue: '#93c5fd',
  brightMagenta: '#d8b4fe',
  brightCyan: '#67e8f9',
  brightWhite: '#f8fafc',
};

export function TerminalView({ sessionId, port, token, onReady }: TerminalViewProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  // onReady 由父组件内联传入，每次渲染都会变；不能进 effect 依赖，
  // 否则「连接成功 → patchTab → 重渲染 → effect 重跑 → 断开重连」会无限闪屏。
  const onReadyRef = useRef(onReady);
  useEffect(() => {
    onReadyRef.current = onReady;
  }, [onReady]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;

    const term = new Terminal({
      fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace",
      fontSize: 13,
      lineHeight: 1.35,
      cursorBlink: true,
      scrollback: 5000,
      allowProposedApi: true,
      theme: THEME,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    // 首帧容器尺寸可能为 0，延迟一帧再 fit
    const fitTimer = window.setTimeout(() => {
      try {
        fit.fit();
      } catch {
        // 容器不可见时忽略
      }
    }, 30);

    const ws = new WebSocket(`ws://127.0.0.1:${port}/term/${sessionId}?token=${token}`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => onReadyRef.current?.();
    ws.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(event.data));
        return;
      }
      if (typeof event.data === 'string') term.write(event.data);
    };

    const dataSub = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }));
      }
    });
    const resizeSub = term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols, rows }));
      }
    });

    // 复制 / 粘贴：xterm 默认不吃 Ctrl+Shift+C/V
    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== 'keydown' || !event.ctrlKey || !event.shiftKey) return true;
      if (event.code === 'KeyC') {
        const selection = term.getSelection();
        if (selection) void navigator.clipboard?.writeText(selection).catch(() => undefined);
        return false;
      }
      if (event.code === 'KeyV') {
        void navigator.clipboard
          ?.readText()
          .then((text) => {
            if (text) term.paste(text);
          })
          .catch(() => undefined);
        return false;
      }
      return true;
    });

    const observer = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        // 容器尺寸为 0 时忽略
      }
    });
    observer.observe(host);

    return () => {
      window.clearTimeout(fitTimer);
      observer.disconnect();
      dataSub.dispose();
      resizeSub.dispose();
      ws.close();
      term.dispose();
    };
  }, [sessionId, port, token]);

  return <div ref={hostRef} className="h-full w-full" />;
}

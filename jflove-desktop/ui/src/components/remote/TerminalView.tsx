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
import { readTerminalShortcuts } from '../../utils/terminal-shortcuts';

interface TerminalViewProps {
  sessionId: string;
  port: number;
  token: string;
  onReady?: () => void;
}

/** 终端深色主题（与代码块同色系，亮色界面下也不刺眼） */
const THEME = {
  background: '#0b1220',
  foreground: '#e6edf7',
  cursor: '#e2e8f0',
  selectionBackground: 'rgba(99, 102, 241, 0.45)',
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
    term.focus();
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

    ws.onopen = () => {
      onReadyRef.current?.();
      // 首帧容器尺寸可能还没稳定，连接成功后再对齐一次行列数
      window.setTimeout(() => {
        try {
          fit.fit();
        } catch {
          // 容器不可见时忽略
        }
        term.focus();
      }, 50);
    };
    // 高频输出（vim / TUI）按帧批量写入，避免每条消息触发一次渲染
    let pending: Uint8Array[] = [];
    let raf = 0;
    const flush = () => {
      raf = 0;
      if (pending.length === 0) return;
      const merged = new Uint8Array(pending.reduce((n, item) => n + item.length, 0));
      let offset = 0;
      for (const item of pending) {
        merged.set(item, offset);
        offset += item.length;
      }
      pending = [];
      term.write(merged);
    };
    const enqueue = (data: Uint8Array) => {
      pending.push(data);
      if (raf === 0) raf = window.requestAnimationFrame(flush);
    };
    ws.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        enqueue(new Uint8Array(event.data));
        return;
      }
      if (typeof event.data === 'string') enqueue(new TextEncoder().encode(event.data));
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

    // 复制 / 粘贴：模式来自设置页（默认 Ctrl+C / Ctrl+V）
    const shortcutMode = readTerminalShortcuts().copyPasteMode;
    const copySelection = () => {
      const selection = term.getSelection();
      if (selection) void navigator.clipboard?.writeText(selection).catch(() => undefined);
    };
    const pasteClipboard = () => {
      void navigator.clipboard
        ?.readText()
        .then((text) => {
          if (text) term.paste(text);
        })
        .catch(() => undefined);
    };
    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== 'keydown' || !event.ctrlKey) return true;
      const withShift = event.shiftKey;
      if (event.code === 'KeyC') {
        if (shortcutMode === 'ctrlShift' && withShift) {
          copySelection();
          return false;
        }
        if (shortcutMode === 'ctrl') {
          copySelection();
          return false;
        }
        if (shortcutMode === 'smart' && term.hasSelection()) {
          copySelection();
          return false;
        }
        return true; // smart 且无选区：交给 shell（SIGINT）
      }
      if (event.code === 'KeyV') {
        if (shortcutMode === 'ctrlShift' && !withShift) return true;
        pasteClipboard();
        return false;
      }
      return true;
    });

    let resizeTimer = 0;
    const observer = new ResizeObserver(() => {
      if (resizeTimer) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        try {
          fit.fit();
        } catch {
          // 容器尺寸为 0 时忽略
        }
      }, 120);
    });
    observer.observe(host);
    const focusOnClick = () => term.focus();
    host.addEventListener('click', focusOnClick);

    return () => {
      window.clearTimeout(fitTimer);
      window.clearTimeout(resizeTimer);
      if (raf) window.cancelAnimationFrame(raf);
      host.removeEventListener('click', focusOnClick);
      observer.disconnect();
      dataSub.dispose();
      resizeSub.dispose();
      ws.close();
      term.dispose();
    };
  }, [sessionId, port, token]);

  return <div ref={hostRef} className="h-full w-full" />;
}

/**
 * 窗口标题栏（桌面端专属组件）
 *
 * Web 端没有这一层（浏览器自带窗口边框）。桌面端去掉了 Qt 原生外框
 * （`Qt.FramelessWindowHint`），因此标题栏由 Web 自绘：
 * 最小化 / 最大化-还原 / 关闭 + 拖拽移动 + 8 向边缘缩放。
 *
 * ## 为什么拖拽不能像 Electron 那样一行 CSS 搞定
 *
 * `-webkit-app-region: drag` 是 Electron 的私有扩展，QtWebEngine 不支持。
 * 因此必须由 JS 判定意图，经 QWebChannel 通知 Python 调
 * `QWindow.startSystemMove()` / `startSystemResize()`（P0-1 已实测通过）。
 *
 * ## 热区常量必须与 Python 侧一致
 *
 * 只有 JS 需要它（判定命中），Python 侧只负责把掩码转成 `Qt::Edges`。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { getChannelObject, whenBridgeReady } from '../../utils/desktop-bridge';
import { titleForPath } from '../../config/placeholders';
import { router } from '../../config/routes';

/** 边缘缩放热区宽度（px） */
const RESIZE_MARGIN = 6;

/** Qt::Edges 位掩码 */
const EDGE = { left: 1, top: 2, right: 4, bottom: 8 } as const;

/** 双击判定窗口（ms）与位移容差（px） */
const DOUBLE_CLICK_MS = 400;
const DOUBLE_CLICK_SLOP = 6;

/** Python 侧 `ShellControls` 的镜像类型 */
interface ShellControls {
  isMaximized: (cb: (value: boolean) => void) => void;
  minimize: () => void;
  toggleMaximize: () => void;
  close: () => void;
  startSystemMove: (cb?: (value: boolean) => void) => void;
  startSystemResize: (edges: number, cb?: (value: boolean) => void) => void;
  maximizedChanged: { connect: (cb: (value: boolean) => void) => void };
}

/** 计算某坐标命中的窗口边缘（0 = 不在热区内） */
function edgesAt(x: number, y: number): number {
  let edges = 0;
  if (x <= RESIZE_MARGIN) edges |= EDGE.left;
  else if (x >= window.innerWidth - RESIZE_MARGIN) edges |= EDGE.right;
  if (y <= RESIZE_MARGIN) edges |= EDGE.top;
  else if (y >= window.innerHeight - RESIZE_MARGIN) edges |= EDGE.bottom;
  return edges;
}

/** 边缘掩码 → CSS 缩放光标 */
function cursorFor(edges: number): string {
  const l = (edges & EDGE.left) !== 0;
  const r = (edges & EDGE.right) !== 0;
  const t = (edges & EDGE.top) !== 0;
  const b = (edges & EDGE.bottom) !== 0;
  if ((l && t) || (r && b)) return 'nwse-resize';
  if ((r && t) || (l && b)) return 'nesw-resize';
  if (l || r) return 'ew-resize';
  if (t || b) return 'ns-resize';
  return '';
}

const ICON_MIN = (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
    <path d="M0 5.5h10" stroke="currentColor" strokeWidth="1" fill="none" />
  </svg>
);

const ICON_MAX = (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
    <rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" />
  </svg>
);

const ICON_RESTORE = (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
    <rect x="0.5" y="2.5" width="7" height="7" fill="none" stroke="currentColor" />
    <path d="M2.5 2.5V0.5h7v7h-2" fill="none" stroke="currentColor" />
  </svg>
);

const ICON_CLOSE = (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
    <path d="M0.5 0.5l9 9M9.5 0.5l-9 9" stroke="currentColor" strokeWidth="1" fill="none" />
  </svg>
);

/** 窗口按钮统一样式（46px 宽、占满标题栏高度，与 Windows 习惯一致） */
const BTN_CLASS =
  'grid h-full w-[46px] place-items-center text-[var(--fg-muted)] ' +
  'transition-colors duration-100 hover:bg-hover hover:text-[var(--fg-default)]';

export function TitleBar() {
  const shellRef = useRef<ShellControls | null>(null);
  const [maximized, setMaximized] = useState(false);
  const [routePath, setRoutePath] = useState(() => router.state.location.pathname);
  const lastDownRef = useRef({ at: 0, x: 0, y: 0 });

  // 订阅路由变化来更新标题栏文字。
  //
  // **不能监听 `hashchange`**：react-router 的 hash 路由走的是
  // `history.pushState/replaceState`，那两个 API **不会**触发 hashchange
  // （实测：登录页标题栏一直显示成默认路由名）。必须订阅 router 自身。
  useEffect(() => {
    const update = () => setRoutePath(router.state.location.pathname);
    update();
    return router.subscribe(update);
  }, []);

  // ── 接上 Python 的窗口控制对象 ──
  useEffect(() => {
    let cancelled = false;
    whenBridgeReady()
      .then(() => {
        if (cancelled) return;
        const shell = getChannelObject<ShellControls>('shell');
        if (!shell) return;
        shellRef.current = shell;
        shell.maximizedChanged.connect((value: boolean) => setMaximized(Boolean(value)));
        shell.isMaximized((value: boolean) => setMaximized(Boolean(value)));
      })
      .catch(() => {
        // 浏览器里 `npm run dev` 时没有桥：标题栏只作静态展示
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ── 边缘热区光标 ──
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (maximized) {
        document.body.style.cursor = '';
        return;
      }
      const edges = edgesAt(e.clientX, e.clientY);
      document.body.style.cursor = edges ? cursorFor(edges) : '';
    };
    const onLeave = () => {
      document.body.style.cursor = '';
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseleave', onLeave);
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseleave', onLeave);
      document.body.style.cursor = '';
    };
  }, [maximized]);

  // ── 边缘缩放（优先级高于拖拽） ──
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (e.button !== 0 || maximized) return;
      const edges = edgesAt(e.clientX, e.clientY);
      if (!edges) return;
      e.preventDefault();
      shellRef.current?.startSystemResize(edges);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [maximized]);

  /** 标题栏空白处：拖拽移动 / 双击最大化 */
  const handleBarMouseDown = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      if (e.button !== 0) return;
      const target = e.target as HTMLElement;
      if (target.closest('.win-btn')) return;

      const now = Date.now();
      const last = lastDownRef.current;
      const isDouble =
        now - last.at < DOUBLE_CLICK_MS &&
        Math.abs(e.clientX - last.x) < DOUBLE_CLICK_SLOP &&
        Math.abs(e.clientY - last.y) < DOUBLE_CLICK_SLOP;

      lastDownRef.current = { at: isDouble ? 0 : now, x: e.clientX, y: e.clientY };

      if (isDouble) {
        // 双击必须自己实现：startSystemMove() 把控制权交给系统后，
        // 浏览器不会再派发 dblclick
        shellRef.current?.toggleMaximize();
        return;
      }
      e.preventDefault();
      shellRef.current?.startSystemMove();
    },
    [],
  );

  const title = titleForPath(routePath);

  return (
    <header
      className="flex h-[var(--header-h)] shrink-0 items-center border-b border-line-subtle bg-surface select-none"
      onMouseDown={handleBarMouseDown}
    >
      {/*
        只显示当前页面名。
        这里**刻意不放品牌 logo 与用户名**：侧栏顶部已有品牌区、底部已有用户卡，
        标题栏再重复一遍会出现「左上角两个标题」的套层感（用户反馈）。
      */}
      <div className="flex min-w-0 items-center pl-4">
        <span className="truncate text-[13px] font-medium text-muted">{title}</span>
      </div>

      {/* 拖拽热区 */}
      <div className="flex-1 self-stretch" />

      <div className="flex h-full items-stretch">
        <button
          type="button"
          className={`win-btn ${BTN_CLASS}`}
          title="最小化"
          aria-label="最小化"
          onClick={() => shellRef.current?.minimize()}
        >
          {ICON_MIN}
        </button>
        <button
          type="button"
          className={`win-btn ${BTN_CLASS}`}
          title={maximized ? '还原' : '最大化'}
          aria-label={maximized ? '还原' : '最大化'}
          onClick={() => shellRef.current?.toggleMaximize()}
        >
          {maximized ? ICON_RESTORE : ICON_MAX}
        </button>
        <button
          type="button"
          className={`win-btn ${BTN_CLASS} hover:bg-[var(--danger-500)] hover:text-white`}
          title="关闭"
          aria-label="关闭"
          onClick={() => shellRef.current?.close()}
        >
          {ICON_CLOSE}
        </button>
      </div>
    </header>
  );
}

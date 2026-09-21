/**
 * Mermaid 全屏查看器（Web）
 *
 * 需求 AC-10：全屏查看大图，可拖动平移、滚轮缩放，Esc 退出。
 * 安全：内容为**已清洗**的 SVG 字符串（`sanitizeSvg`），不执行任何脚本。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Icon, toast } from '../ui';

const ZOOM_MIN = 25;
const ZOOM_MAX = 800;
const ZOOM_STEP = 25;

export interface MermaidViewerProps {
  /** 已清洗的 SVG */
  svg: string;
  /** 图表类型（头部标签） */
  kind: string;
  /** 源码（用于复制） */
  code: string;
  onClose: () => void;
}

export function MermaidViewer({ svg, kind, code, onClose }: MermaidViewerProps) {
  const stageRef = useRef<HTMLDivElement>(null);
  const paperRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(100);
  const dragState = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  /* Esc 关闭 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  /* 滚轮缩放（Ctrl 或直接滚轮都生效，符合看图习惯） */
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((z) => clamp(z + (e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP)));
    };
    stage.addEventListener('wheel', onWheel, { passive: false });
    return () => stage.removeEventListener('wheel', onWheel);
  }, []);

  /* 注入 SVG */
  useEffect(() => {
    if (paperRef.current) paperRef.current.innerHTML = svg;
  }, [svg]);

  const clamp = (v: number) => Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, v));
  const reset = () => {
    setZoom(100);
    if (stageRef.current) {
      stageRef.current.scrollLeft = 0;
      stageRef.current.scrollTop = 0;
    }
  };

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    const stage = stageRef.current;
    if (!stage) return;
    dragState.current = {
      x: e.clientX,
      y: e.clientY,
      left: stage.scrollLeft,
      top: stage.scrollTop,
    };
    setDragging(true);
  }, []);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      const d = dragState.current;
      const stage = stageRef.current;
      if (!d || !stage) return;
      stage.scrollLeft = d.left - (e.clientX - d.x);
      stage.scrollTop = d.top - (e.clientY - d.y);
    };
    const onUp = () => {
      dragState.current = null;
      setDragging(false);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [dragging]);

  const handleDownload = () => {
    try {
      const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `diagram-${kind.toLowerCase()}.svg`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      toast.success('已导出 SVG');
    } catch {
      toast.error('导出失败');
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      toast.success('已复制图表源码');
    } catch {
      toast.error('复制失败');
    }
  };

  return (
    <div
      className="fixed inset-0 flex flex-col"
      style={{ backgroundColor: 'rgb(8 13 24 / 0.78)', backdropFilter: 'blur(6px)', zIndex: 90 }}
      role="dialog"
      aria-modal="true"
      aria-label={`${kind} 图表全屏查看`}
    >
      {/* 顶栏 */}
      <div className="flex h-[52px] shrink-0 items-center gap-3 px-5 text-neutral-100">
        <span className="text-sm font-semibold">{kind} 图表</span>
        <span className="text-[11px] text-neutral-400">滚轮缩放 · 拖拽平移 · Esc 退出</span>
        <div className="grow" />
        <div className="zoom-pill">
          <button type="button" onClick={() => setZoom((z) => clamp(z - ZOOM_STEP))} aria-label="缩小">
            <Icon name="zoomOut" size="sm" />
          </button>
          <span>{zoom}%</span>
          <button type="button" onClick={() => setZoom((z) => clamp(z + ZOOM_STEP))} aria-label="放大">
            <Icon name="zoomIn" size="sm" />
          </button>
        </div>
        <button type="button" className="btn btn-secondary btn-sm" onClick={reset}>
          <Icon name="refresh" size="sm" />
          重置
        </button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={handleDownload}>
          <Icon name="download" size="sm" />
          导出 SVG
        </button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={handleCopy}>
          <Icon name="copy" size="sm" />
          复制源码
        </button>
        <button
          type="button"
          className="icon-btn text-neutral-300 hover:text-white"
          aria-label="关闭"
          onClick={onClose}
        >
          <Icon name="close" />
        </button>
      </div>

      {/* 舞台 */}
      <div
        ref={stageRef}
        className="flex-1 overflow-auto p-8"
        style={{ cursor: dragging ? 'grabbing' : 'grab' }}
        onMouseDown={onMouseDown}
      >
        <div className="grid min-h-full place-items-center">
          <div
            ref={paperRef}
            className="mmd-viewer-paper rounded-2xl bg-surface p-8 shadow-e5"
            style={{ width: `${zoom}%`, maxWidth: 'none' }}
          />
        </div>
      </div>
    </div>
  );
}

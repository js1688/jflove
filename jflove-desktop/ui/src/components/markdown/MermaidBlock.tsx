/**
 * Mermaid 图块组件（Web）
 *
 * 能力对照需求 AC-8 ~ AC-14：
 *   - 进入视口才渲染（懒加载）——AC-13
 *   - 缓存命中即时显示 ——AC-22
 *   - 渲染中骨架占位且高度固定，不引发布局跳动 ——AC-21
 *   - 缩放 50%–400% / 全屏 / 导出 SVG / 复制源码 ——AC-10
 *   - 主题切换重新渲染 ——AC-11
 *   - 语法错误只降级本图块，含行号与「跳到源码」——AC-12
 *   - 引擎不可用/超时降级为源码卡 ——AC-14
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Icon, IconButton, toast } from '../ui';
import { renderMermaid } from '../../utils/mermaid/renderer';
import { sanitizeSvg } from '../../utils/mermaid/sanitize';
import type { MermaidResult } from '../../utils/mermaid/types';
import { detectDiagramKind } from '../../utils/mermaid/types';
import { useThemeName } from './use-theme-name';
import { MermaidViewer } from './MermaidViewer';

const ZOOM_MIN = 50;
const ZOOM_MAX = 400;
const ZOOM_STEP = 10;
/** 默认占位宽高比（未知尺寸时用），避免骨架高度为 0 导致跳动 */
const DEFAULT_ASPECT = 16 / 9;

export interface MermaidBlockProps {
  /** mermaid 源码 */
  code: string;
  /** 在文档中的序号（用于「跳到源码」定位与无障碍标签） */
  index: number;
  /** 点击「跳到源码」时回调（编辑器场景传入；预览场景可不传） */
  onJumpToSource?: (index: number) => void;
}

type Phase = 'idle' | 'loading' | 'done' | 'error';

export function MermaidBlock({ code, index, onJumpToSource }: MermaidBlockProps) {
  const theme = useThemeName();
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);

  const [phase, setPhase] = useState<Phase>('idle');
  const [result, setResult] = useState<MermaidResult | null>(null);
  const [zoom, setZoom] = useState(100);
  const [visible, setVisible] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);

  /** 图表在源码里的行号（用于错误定位提示；源码属于正文，不入日志） */
  const kind = useMemo(() => detectDiagramKind(code), [code]);

  /* ---------- 懒加载：进入视口才开始渲染 ---------- */
  useEffect(() => {
    const el = containerRef.current;
    if (!el || visible) return;
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true); // 环境不支持时直接渲染，保证功能可用
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin: '200px 0px' }, // 提前 200px 预渲染
    );
    io.observe(el);
    return () => io.disconnect();
  }, [visible]);

  /* ---------- 渲染（主题变化时重新渲染）---------- */
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    setPhase('loading');

    renderMermaid(code, theme)
      .then((r) => {
        if (cancelled) return;
        setResult(r);
        setPhase(r.ok ? 'done' : 'error');
      })
      .catch(() => {
        if (cancelled) return;
        setResult({ ok: false, error: { reason: 'runtime', line: 0, message: '渲染失败' } });
        setPhase('error');
      });

    return () => {
      cancelled = true;
    };
  }, [visible, code, theme]);

  /* ---------- 把 SVG 写入画布（经清洗）---------- */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (result?.ok) {
      canvas.innerHTML = sanitizeSvg(result.svg);
    } else {
      canvas.innerHTML = '';
    }
  }, [result]);

  /* ---------- 操作 ---------- */
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      toast.success('已复制图表源码');
    } catch {
      toast.error('复制失败', '浏览器未授予剪贴板权限');
    }
  }, [code]);

  const handleDownload = useCallback(() => {
    if (!result?.ok) return;
    try {
      const blob = new Blob([sanitizeSvg(result.svg)], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `diagram-${index + 1}.svg`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // 下一个事件循环释放，确保下载已开始
      setTimeout(() => URL.revokeObjectURL(url), 0);
      toast.success('已导出 SVG');
    } catch {
      toast.error('导出失败');
    }
  }, [result, index]);

  const clampZoom = (v: number) => Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, v));
  const zoomIn = () => setZoom((z) => clampZoom(z + ZOOM_STEP));
  const zoomOut = () => setZoom((z) => clampZoom(z - ZOOM_STEP));

  /* ---------- 错误降级：错误卡 / 源码卡 ---------- */
  if (phase === 'error' && result && !result.ok) {
    return <MermaidErrorCard code={code} error={result.error} index={index} onJumpToSource={onJumpToSource} onRetry={() => setPhase('idle')} />;
  }

  const aspect = result?.ok ? result.width / Math.max(1, result.height) : DEFAULT_ASPECT;
  const zoomed = zoom !== 100;
  const frameHeight = result?.ok ? Math.round((result.height * zoom) / 100) : undefined;

  return (
    <>
      <figure className="mmd" ref={containerRef}>
        <figcaption className="mmd-head">
          <span className="mmd-kind">
            <Icon name="diagram" size="sm" strokeWidth={2.2} className="!h-3 !w-3" />
            {kind}
          </span>
          <span className="text-[11px] text-subtle">图表 {index + 1}</span>
          <span className="mmd-tools">
            <IconButton icon="copy" label="复制源码" size="sm" onClick={handleCopy} />
            <IconButton
              icon="download"
              label="导出 SVG"
              size="sm"
              onClick={handleDownload}
              disabled={!result?.ok}
            />
            <IconButton
              icon="fullscreen"
              label="全屏查看"
              size="sm"
              onClick={() => setViewerOpen(true)}
              disabled={!result?.ok}
            />
          </span>
        </figcaption>

        {phase !== 'done' ? (
          /* 骨架：高度按预期宽高比固定，成图后不跳动（AC-21） */
          <div
            className="grid place-items-center p-5"
            style={{ aspectRatio: String(aspect), maxHeight: 560 }}
            aria-busy="true"
            aria-label={`图表 ${index + 1} 渲染中`}
          >
            <div className="flex flex-col items-center gap-2 text-subtle">
              <Icon name="loading" size="lg" className="animate-spin" />
              <span className="text-[11px]">渲染图表中…</span>
            </div>
          </div>
        ) : (
          <div
            ref={canvasRef}
            className="mmd-canvas"
            data-zoomed={zoomed}
            style={zoomed && frameHeight ? { maxHeight: 520, height: frameHeight } : undefined}
            role="img"
            aria-label={`${kind} 图表 ${index + 1}`}
          />
        )}

        {phase === 'done' && result?.ok && (
          <div className="mmd-foot">
            <span className="zoom-pill">
              <button type="button" onClick={zoomOut} aria-label="缩小" title="缩小">
                <Icon name="zoomOut" size="sm" />
              </button>
              <span>{zoom}%</span>
              <button type="button" onClick={zoomIn} aria-label="放大" title="放大">
                <Icon name="zoomIn" size="sm" />
              </button>
            </span>
            <span>渲染 {result.elapsed} ms</span>
            <span className="grow" />
            <span className="inline-flex items-center gap-1">
              <Icon name="checked" size="sm" className="text-success-500" />
              语法校验通过
            </span>
          </div>
        )}
      </figure>

      {viewerOpen && result?.ok && (
        <MermaidViewer
          svg={sanitizeSvg(result.svg)}
          kind={kind}
          code={code}
          onClose={() => setViewerOpen(false)}
        />
      )}
    </>
  );
}

/* ==================== 错误 / 源码卡片 ==================== */

function MermaidErrorCard({
  code,
  error,
  index,
  onJumpToSource,
  onRetry,
}: {
  code: string;
  error: { reason: string; line: number; message: string };
  index: number;
  onJumpToSource?: (index: number) => void;
  onRetry: () => void;
}) {
  const isParse = error.reason === 'parse';
  const title = isParse ? '语法错误' : error.reason === 'timeout' ? '渲染超时' : '渲染引擎不可用';

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      toast.success('已复制图表源码');
    } catch {
      toast.error('复制失败');
    }
  };

  return (
    <div className="mmd-error">
      <div className="mmd-head">
        <span className="mmd-kind">
          <Icon name="alert" size="sm" strokeWidth={2.2} className="!h-3 !w-3" />
          {title}
        </span>
        <span className="text-[11px]" style={{ color: 'var(--danger-700)' }}>
          {error.line > 0 ? `第 ${error.line} 行：${error.message}` : error.message}
        </span>
        <span className="mmd-tools">
          <IconButton icon="copy" label="复制源码" size="sm" onClick={handleCopy} />
          <IconButton icon="refresh" label="重新渲染" size="sm" onClick={onRetry} />
          {onJumpToSource && (
            <IconButton
              icon="edit"
              label="跳到源码"
              size="sm"
              onClick={() => onJumpToSource(index)}
            />
          )}
        </span>
      </div>
      <pre>{code}</pre>
      <div
        className="flex items-center gap-2 px-3.5 pb-3 text-[12.5px]"
        style={{ color: 'var(--danger-700)' }}
      >
        <Icon name="info" size="sm" />
        {isParse
          ? '只影响这一张图，文档其余内容照常显示；修正语法后会自动重新渲染。'
          : '图表源码已保留，可复制到 mermaid.live 查看；文档其余内容不受影响。'}
      </div>
    </div>
  );
}

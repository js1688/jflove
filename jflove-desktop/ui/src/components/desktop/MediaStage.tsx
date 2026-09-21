/**
 * 原生媒体舞台（桌面端专有）
 *
 * 把一块**由 Python 摆放的独立原生窗口**（`src/ui/media_overlay.py`）贴在下面这个
 * 占位 div 上。解码由 **OS 解码器**完成：
 *
 * - 视频/音频：`StreamProxy`（本地解密 + Range 206）→ `QMediaPlayer`
 * - 图片：Python 取回字节落临时文件 → Qt 原生解码（`QImageReader`）
 *
 * ## 为什么不用 `<video>` / `<img>`
 *
 * 用户明确要求"解码能力吊起本地解码"：Chromium 内置解码器格式受限
 * （mkv/avi/flac/HEVC 等常常放不出来），而 OS 解码器兼容性强得多。
 * 渲染层在这里**只负责画框与上报位置**，画面永远不经过 Web 渲染。
 *
 * 位置同步：`getBoundingClientRect()` → `media.set_rect`；
 * 滚出视口 → `media.set_visible(false)` 隐藏原生窗口（避免残影）。
 * 经 P0-4 实测：CSS 像素与 Qt 设备无关像素一一对应，**无需 dpr 换算**。
 */
import { useEffect, useRef } from 'react';
import { callBridge, onBridgeEvent } from '../../utils/desktop-bridge';

export interface MediaStageProps {
  /** 舞台宽高比提示（仅用于占位框的初始高度，避免布局跳动） */
  aspect?: string;
  /** 位置/时长等状态回调（由播放器控件消费） */
  onState?: (state: { position: number; duration: number; playing: boolean }) => void;
  /** 播放错误回调（渲染层据此显示友好提示或引导"修复"） */
  onError?: (message: string) => void;
  className?: string;
}

export function MediaStage({ aspect = '16 / 9', onState, onError, className }: MediaStageProps) {
  const boxRef = useRef<HTMLDivElement>(null);

  // 上报占位矩形（挂载 / 滚动 / 缩放都重新对齐）
  useEffect(() => {
    // 只在矩形**真的变化**时才上报。
    // 原先每 400ms 无条件上报一次，实测把日志刷爆（每秒 2 条 set_rect + set_visible），
    // 白耗桥调用与 CPU；浮层对齐只需要在变化时更新。
    let lastKey = '';
    const report = () => {
      const el = boxRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const visible = r.bottom > 0 && r.top < window.innerHeight && r.width > 0;
      const key = `${Math.round(r.left)},${Math.round(r.top)},${Math.round(r.width)},${Math.round(r.height)},${visible}`;
      if (key === lastKey) return;
      lastKey = key;
      void callBridge('media', 'set_rect', {
        x: r.left,
        y: r.top,
        w: r.width,
        h: r.height,
        visible,
      }).catch(() => {});
      void callBridge('media', 'set_visible', { visible }).catch(() => {});
    };

    report();
    // 滚动容器不一定是 window：用捕获阶段监听所有滚动
    window.addEventListener('scroll', report, true);
    window.addEventListener('resize', report);
    const timer = window.setInterval(report, 400); // 兜底：布局变化也能跟上
    return () => {
      window.removeEventListener('scroll', report, true);
      window.removeEventListener('resize', report);
      window.clearInterval(timer);
    };
  }, []);

  // 原生播放器的状态 → 渲染层（进度条/播放按钮）
  useEffect(() => {
    const offPos = onBridgeEvent('media.position', (p) => {
      const pos = Number((p as { position?: number } | null)?.position ?? 0);
      onState?.({ position: pos, duration: lastDuration.current, playing: lastPlaying.current });
    });
    const offDur = onBridgeEvent('media.duration', (p) => {
      lastDuration.current = Number((p as { duration?: number } | null)?.duration ?? 0);
      onState?.({ position: lastPosition.current, duration: lastDuration.current, playing: lastPlaying.current });
    });
    const offState = onBridgeEvent('media.state', (p) => {
      lastPlaying.current = Boolean((p as { playing?: boolean } | null)?.playing);
      onState?.({ position: lastPosition.current, duration: lastDuration.current, playing: lastPlaying.current });
    });
    const offErr = onBridgeEvent('media.error', (p) => {
      onError?.(String((p as { error?: string } | null)?.error ?? '播放失败'));
    });
    return () => {
      offPos();
      offDur();
      offState();
      offErr();
    };
  }, [onState, onError]);

  const lastPosition = useRef(0);
  const lastDuration = useRef(0);
  const lastPlaying = useRef(false);

  return (
    <div
      ref={boxRef}
      className={['w-full overflow-hidden rounded-lg border border-line-subtle bg-black', className]
        .filter(Boolean)
        .join(' ')}
      style={{ aspectRatio: aspect }}
      data-testid="media-stage"
    >
      {/* 原生窗口会盖在这块区域上；这里只放"由本地解码器渲染"的说明，
          用户若看到这段文字，说明原生浮层没贴上来（便于排查） */}
      <div className="grid h-full w-full place-items-center text-[12px] text-white/45">
        由本地解码器渲染（OS 解码器）
      </div>
    </div>
  );
}

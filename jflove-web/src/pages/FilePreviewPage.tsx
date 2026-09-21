import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import { fileService } from '../services/file-service';
import { repairService } from '../services/repair-service';
import { useFileStore } from '../stores/file-store';
import { PageHeader } from '../components/PageHeader';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { MarkdownRenderer } from '../components/markdown';
import { Button, ErrorState, Icon } from '../components/ui';
import { playWithMSE } from '../utils/media-source-player';

const TEXT_EXTS = ['md', 'txt', 'json', 'xml', 'yaml', 'yml', 'csv', 'ini', 'log', 'js', 'ts', 'py', 'html', 'css'];
const IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'];
const VIDEO_EXTS = ['mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', 'm4v', 'mpg', 'mpeg', 'ts', '3gp'];
const AUDIO_EXTS = ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'wma', 'opus'];

function mediaMime(ext: string): string {
  const map: Record<string, string> = {
    mp4: 'video/mp4', mkv: 'video/x-matroska', avi: 'video/x-msvideo', mov: 'video/quicktime',
    webm: 'video/webm', flv: 'video/x-flv', wmv: 'video/x-ms-wmv', m4v: 'video/mp4',
    mpg: 'video/mpeg', mpeg: 'video/mpeg', ts: 'video/mp2t', '3gp': 'video/3gpp',
    mp3: 'audio/mpeg', wav: 'audio/wav', ogg: 'audio/ogg', flac: 'audio/flac',
    m4a: 'audio/mp4', aac: 'audio/aac', wma: 'audio/x-ms-wma', opus: 'audio/opus',
  };
  return map[ext] || 'application/octet-stream';
}

/**
 * 文件预览页
 *
 * v1.5.0：emoji 图标与硬编码红/绿/靛换语义令牌；错误态与修复引导改用
 * 统一 ErrorState + Button；音频占位图标改矢量。
 *
 * 注意：以下关键实现保持不变（MSE 播放稳定性，勿动）：
 *   - effect 内 `setTimeout(0)` 延迟启动 playWithMSE（规避 StrictMode 双跑争用 <video>）；
 *   - video / audio 元素**始终渲染**（不受 loading/error 影响），src 由播放逻辑命令式管理；
 *   - 加载提示层始终渲染、用 class 控制可见性 —— 否则无 key 兄弟列表长度变化会让
 *     React 卸载重建媒体元素，playWithMSE 持有的元素引用失效，MSE 播放必然失败。
 */
export function FilePreviewPage() {
  const { diskId } = useParams<{ diskId: string }>();
  const navigate = useNavigate();
  const previewTarget = useFileStore(s => s.previewTarget);

  const filename = previewTarget?.name || '';
  const path = previewTarget?.path || '';
  // v1.4.2：修复产物验证播放（修复中心「验证播放」设置，>0 时媒体走产物流）
  const repairTaskId = previewTarget?.repairTaskId ?? 0;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // v1.4.2：损坏文件标志（[MEDIA_NEEDS_REPAIR]）→ 展示「立即修复」引导
  const [needsRepair, setNeedsRepair] = useState(false);
  const [repairBusy, setRepairBusy] = useState(false);
  const [repairNotice, setRepairNotice] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  // 回退完整下载时的进度百分比（null = 未在下载模式）；大文件下载时避免“卡死”误解
  const [downloadProgress, setDownloadProgress] = useState<number | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const isImage = IMAGE_EXTS.includes(ext);
  const isText = TEXT_EXTS.includes(ext);
  const isVideo = VIDEO_EXTS.includes(ext);
  const isAudio = AUDIO_EXTS.includes(ext);
  const isPdf = ext === 'pdf';

  /** v1.4.2：损坏文件「立即修复」——创建修复任务 */
  const handleRepairNow = async () => {
    if (!diskId) return;
    setRepairBusy(true);
    try {
      const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
      await repairService.create(Number(diskId), dir, filename);
      setError(null);
      setNeedsRepair(false);
      setRepairNotice('已加入修复队列，可在「修复中心」查看进度');
    } catch (e) {
      setRepairNotice(`发起修复失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRepairBusy(false);
    }
  };

  useEffect(() => {
    if (!diskId || !previewTarget) {
      setLoading(false);
      setError('请从文件列表进入预览');
      return;
    }
    let cancelled = false;
    let mseCleanup: (() => void) | null = null;
    let blobUrl: string | null = null;
    // 中止在途流的控制器（组件卸载 / 依赖变更时取消）
    const streamAbort = new AbortController();

    setLoading(true);
    setError(null);
    setNeedsRepair(false);
    setRepairNotice(null);
    setContent(null);
    setImageUrl(null);
    setDownloadProgress(null);

    // React StrictMode 会同步「mount→cleanup→mount」双跑 effect：若在 effect 内
    // 直接启动 playWithMSE，会产生两个并发实例争用同一个 <video>，后实例把先实例
    // 的 MediaSource 从 video 上顶掉，导致先实例 appendBuffer 抛 InvalidStateError
    // （表现为「边下边播准备中…」卡死 / Empty src，v1.4.1 修复）。
    // 用 setTimeout(0) 把异步播放逻辑延迟到双跑 effect 完成之后：第一次 cleanup 会
    // clearTimeout 取消第一次调度，只剩第二次调度真正执行，保证只有一个实例。
    const timer = setTimeout(() => {
      (async () => {
      try {
        if (isImage) {
          const bytes = await fileService.downloadRaw(Number(diskId), path, filename);
          if (cancelled) return;
          blobUrl = URL.createObjectURL(new Blob([bytes.buffer as ArrayBuffer]));
          setImageUrl(blobUrl);
        } else if (isText) {
          const bytes = await fileService.downloadRaw(Number(diskId), path, filename);
          if (cancelled) return;
          setContent(new TextDecoder('utf-8').decode(bytes));
        } else if (isVideo || isAudio) {
          // ① MSE 主路径（v1.4.2：仅 byte 模式，播放纯净化）。
          //    健康文件按字节 range 流式；repairTaskId>0 时为修复产物验证播放。
          //    mkv/avi 等 MSE 不支持的容器首帧失败后回退 Blob 下载。
          const mediaEl = isVideo ? videoRef.current : audioRef.current;
          if (mediaEl) {
            const cleanup = await playWithMSE(
              mediaEl, Number(diskId), path, filename,
              repairTaskId, streamAbort.signal,
            );
            if (cancelled) {
              cleanup?.();
              return;
            }
            if (cleanup) {
              mseCleanup = cleanup;
              return;
            }
          }
          // ② 兜底：完整下载 → Blob URL（MSE 不可用 / 容器不被支持时）
          const totalSize = previewTarget?.size || 0;
          let lastPct = -1;
          const bytes = await fileService.downloadRaw(
            Number(diskId), path, filename,
            (downloaded) => {
              if (cancelled) return;
              // 仅整数百分比变化时才更新 state（大文件每片 64KB，避免上千次无效渲染）
              const pct = totalSize > 0 ? Math.floor((downloaded / totalSize) * 100) : 0;
              if (pct !== lastPct) {
                lastPct = pct;
                setDownloadProgress(pct);
              }
            },
          );
          if (cancelled) return;
          blobUrl = URL.createObjectURL(
            new Blob([bytes.buffer as ArrayBuffer], { type: mediaMime(ext) }),
          );
          // video/audio 的 src 完全由播放逻辑命令式管理（非受控），避免 React
          // 受控 src 与 MSE 命令式 video.src 冲突导致 MediaSource 被 detach（v1.4.1）
          if (mediaEl) {
            mediaEl.src = blobUrl;
          }
        }
      } catch (e) {
        if (!cancelled) {
          // v1.4.2：损坏文件（服务端 415 [MEDIA_NEEDS_REPAIR]）→ 修复引导
          const msg = e instanceof Error ? e.message : '预览加载失败';
          if (msg.includes('[MEDIA_NEEDS_REPAIR]')) {
            setNeedsRepair(true);
            setError('该文件已损坏，无法在线播放');
          } else {
            setError(msg);
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
      })();
    }, 0);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      streamAbort.abort();
      if (mseCleanup) mseCleanup();
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [diskId, previewTarget, path, filename, repairTaskId, isImage, isText, isVideo, isAudio, ext]);

  return (
    <div>
      <PageHeader title={filename || '文件预览'} onBack={() => navigate(-1)} />
      {loading && !isVideo && !isAudio && <LoadingSpinner text="加载预览…" />}
      {!loading && error && (
        <div className="flex flex-col items-center">
          <ErrorState message={error} onRetry={() => navigate(0)} />
          {needsRepair && (
            <div className="-mt-6 flex flex-col items-center gap-3 px-6 pb-10">
              <Button
                variant="primary"
                icon="repair"
                loading={repairBusy}
                onClick={() => void handleRepairNow()}
              >
                {repairBusy ? '提交中…' : '立即修复'}
              </Button>
              <p className="m-0 text-center text-[11.5px] text-subtle">
                修复为异步任务，完成后可在「修复中心」验证播放并覆盖原文件
              </p>
            </div>
          )}
          {repairNotice && (
            <div className="mt-3 flex items-center gap-2 rounded-lg border border-brand-500/25 bg-brand-50 px-4 py-2 text-[13px] text-brand-700">
              <Icon name="info" size="sm" className="shrink-0" />
              {repairNotice}
            </div>
          )}
        </div>
      )}
      {!loading && !error && (
        <div className="p-6">
          {isImage && imageUrl && (
            <div className="flex items-center justify-center">
              <img
                src={imageUrl}
                alt={filename}
                className="max-h-[80vh] max-w-full rounded-lg object-contain shadow-e4"
              />
            </div>
          )}
          {isText && ext === 'md' && content !== null && (
            <MarkdownRenderer content={content} className="mx-auto max-w-[880px] pb-16" />
          )}
          {isText && ext !== 'md' && content !== null && (
            <pre className="mx-auto max-w-[880px] overflow-x-auto rounded-lg border border-line-subtle bg-sunken p-6 text-[13px] whitespace-pre-wrap">
              {content}
            </pre>
          )}
          {!isImage && !isText && !isVideo && !isAudio && (
            <div className="flex flex-col items-center justify-center py-16 text-subtle">
              <div className="empty-art mb-4">
                <Icon name={isPdf ? 'doc' : 'file'} size="xl" strokeWidth={1.4} className="!h-11 !w-11" />
              </div>
              <p className="text-[13.5px] font-semibold text-fg">
                {isPdf ? 'PDF 文件' : '不支持预览此文件类型'}
              </p>
              <p className="mt-1 text-xs">请下载后使用本地程序打开</p>
            </div>
          )}
        </div>
      )}
      {/* 视频/音频：元素始终渲染（供 ref 即时可用），src 由媒体状态驱动；加载时叠加提示。
          注意：spinner 必须始终渲染（用 class 控制可见性），否则 loading 切换会导致
          无 key 兄弟列表 [spinner, video] → [video]，React 卸载重建 video 元素，
          playWithMSE 持有的元素引用失效，MSE 播放必然失败（v1.4.0 修复）。 */}
      {isVideo && !error && (
        <div className="relative">
          <div className={`absolute inset-0 z-10 flex items-center justify-center bg-overlay/60 ${loading ? '' : 'hidden'}`}>
            <LoadingSpinner
              text={downloadProgress !== null ? `下载中 ${Math.round(downloadProgress)}%` : '边下边播准备中…'}
            />
          </div>
          <video
            ref={videoRef}
            controls
            className="mx-auto max-h-[80vh] max-w-full rounded-lg bg-code shadow-e3"
          />
        </div>
      )}
      {isAudio && !error && (
        <div className="flex flex-col items-center py-8">
          <span className="mb-4 grid h-20 w-20 place-items-center rounded-full bg-brand-50 text-brand-500">
            <Icon name="audio" size="xl" strokeWidth={1.4} className="!h-9 !w-9" />
          </span>
          <div className={loading ? '' : 'hidden'}>
            <LoadingSpinner
              text={downloadProgress !== null ? `下载中 ${Math.round(downloadProgress)}%` : '边下边播准备中…'}
            />
          </div>
          <audio ref={audioRef} controls className="w-full max-w-xl" />
        </div>
      )}
    </div>
  );
}

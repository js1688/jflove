import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import { fileService } from '../services/file-service';
import { repairService } from '../services/repair-service';
import { useFileStore } from '../stores/file-store';
import { PageHeader } from '../components/PageHeader';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { playWithMSE } from '../utils/media-source-player';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

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

  /** v1.4.2：损坏文件「立即修复」——创建修复任务，成功后跳转修复中心 */
  const handleRepairNow = async () => {
    if (!diskId) return;
    setRepairBusy(true);
    try {
      const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
      await repairService.create(Number(diskId), dir, filename);
      setError(null);
      setNeedsRepair(false);
      navigate('/repair');
    } catch (e) {
      setRepairNotice(`发起修复失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRepairBusy(false);
    }
  };

  /** v1.4.2 hotfix：媒体元素播放失败（blob 下载后仍无法解码）→ 引导修复 */
  const handleMediaError = () => {
    setLoading(false);
    setNeedsRepair(true);
    setError('该文件已损坏或格式不受支持，无法在线播放');
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
        <div className="flex flex-col items-center py-12">
          <div className="text-red-500">{error}</div>
          {needsRepair && (
            <div className="mt-4 flex flex-col items-center gap-3">
              <button
                type="button"
                disabled={repairBusy}
                onClick={() => void handleRepairNow()}
                className="rounded-lg bg-indigo-600 px-5 py-2 text-sm text-white
                  hover:bg-indigo-700 disabled:opacity-50"
              >
                {repairBusy ? '提交中…' : '🛠️ 立即修复'}
              </button>
              <p className="text-xs text-gray-400">
                修复为异步任务，完成后可在「修复中心」验证播放并覆盖原文件
              </p>
            </div>
          )}
          {repairNotice && (
            <div className="mt-3 rounded-lg bg-green-50 px-4 py-2 text-sm text-green-700">
              {repairNotice}
            </div>
          )}
        </div>
      )}
      {!loading && !error && (
        <div className="p-6">
          {isImage && imageUrl && (
            <div className="flex items-center justify-center">
              <img src={imageUrl} alt={filename} className="max-w-full max-h-[80vh] object-contain rounded-lg shadow-lg" />
            </div>
          )}
          {isText && ext === 'md' && content !== null && (
            <div className="markdown-body max-w-3xl mx-auto" dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }} />
          )}
          {isText && ext !== 'md' && content !== null && (
            <pre className="p-6 text-sm font-mono whitespace-pre-wrap overflow-x-auto max-w-3xl mx-auto bg-gray-50 rounded-lg">{content}</pre>
          )}
          {!isImage && !isText && !isVideo && !isAudio && (
            <div className="flex flex-col items-center justify-center py-16 text-gray-400">
              <span className="text-5xl mb-3">{isPdf ? '📄' : '📎'}</span>
              <p className="text-sm">{isPdf ? 'PDF 文件' : '不支持预览此文件类型'}</p>
              <p className="text-xs mt-1">请下载后使用本地程序打开</p>
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
          <div className={`absolute inset-0 z-10 flex items-center justify-center bg-black/40 ${loading ? '' : 'hidden'}`}>
            <LoadingSpinner
              text={downloadProgress !== null ? `下载中 ${Math.round(downloadProgress)}%` : '边下边播准备中…'}
            />
          </div>
          <video
            ref={videoRef}
            controls
            onError={handleMediaError}
            className="max-w-full max-h-[80vh] mx-auto rounded-lg shadow-lg bg-black"
          />
        </div>
      )}
      {isAudio && !error && (
        <div className="flex flex-col items-center py-8">
          <span className="text-6xl mb-4">🎵</span>
          <div className={loading ? '' : 'hidden'}>
            <LoadingSpinner
              text={downloadProgress !== null ? `下载中 ${Math.round(downloadProgress)}%` : '边下边播准备中…'}
            />
          </div>
          <audio ref={audioRef} controls onError={handleMediaError} className="w-full max-w-xl" />
        </div>
      )}
    </div>
  );
}

function renderMarkdown(content: string): string {
  try {
    const rawHtml = marked.parse(content, { async: false }) as string;
    return DOMPurify.sanitize(rawHtml);
  } catch {
    return content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
}

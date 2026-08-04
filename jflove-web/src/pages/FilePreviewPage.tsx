import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import { fileService } from '../services/file-service';
import { useFileStore } from '../stores/file-store';
import { PageHeader } from '../components/PageHeader';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { openStreamUrl, closeStreamUrl } from '../utils/stream-proxy';
import { isMSEAvailable, playWithMSE } from '../utils/media-source-player';
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

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [mediaSrc, setMediaSrc] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const isImage = IMAGE_EXTS.includes(ext);
  const isText = TEXT_EXTS.includes(ext);
  const isVideo = VIDEO_EXTS.includes(ext);
  const isAudio = AUDIO_EXTS.includes(ext);
  const isPdf = ext === 'pdf';

  useEffect(() => {
    if (!diskId || !previewTarget) {
      setLoading(false);
      setError('请从文件列表进入预览');
      return;
    }
    let cancelled = false;
    let streamUrl: string | null = null;
    let mseCleanup: (() => void) | null = null;
    let blobUrl: string | null = null;

    setLoading(true);
    setError(null);
    setContent(null);
    setImageUrl(null);
    setMediaSrc(null);

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
          // ① 首选 Service Worker 流式代理：真「边下边播 + 拖动 seek」（安全上下文）
          const swUrl = await openStreamUrl(Number(diskId), path, filename);
          if (cancelled) {
            if (swUrl) closeStreamUrl(swUrl);
            return;
          }
          if (swUrl) {
            streamUrl = swUrl;
            setMediaSrc(swUrl);
            return;
          }
          // ② MSE 回退：SW 不可用（非安全上下文）时，对 fMP4 / WebM / 简单音频边下边播
          const mediaEl = isVideo ? videoRef.current : audioRef.current;
          if (mediaEl && isMSEAvailable(filename)) {
            const cleanup = await playWithMSE(mediaEl, Number(diskId), path, filename);
            if (cancelled) {
              cleanup?.();
              return;
            }
            if (cleanup) {
              mseCleanup = cleanup;
              return;
            }
          }
          // ③ 最后兜底：完整下载 → Blob URL（仅当上述两者均不可行，浏览器能力极限）
          const bytes = await fileService.downloadRaw(Number(diskId), path, filename);
          if (cancelled) return;
          blobUrl = URL.createObjectURL(
            new Blob([bytes.buffer as ArrayBuffer], { type: mediaMime(ext) }),
          );
          setMediaSrc(blobUrl);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : '预览加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      if (streamUrl) closeStreamUrl(streamUrl);
      if (mseCleanup) mseCleanup();
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [diskId, previewTarget, path, filename, isImage, isText, isVideo, isAudio, ext]);

  return (
    <div>
      <PageHeader title={filename || '文件预览'} onBack={() => navigate(-1)} />
      {loading && !isVideo && !isAudio && <LoadingSpinner text="加载预览…" />}
      {!loading && error && (<div className="text-center py-12 text-red-500">{error}</div>)}
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
      {/* 视频/音频：元素始终渲染（供 ref 即时可用），src 由媒体状态驱动；加载时叠加提示 */}
      {isVideo && !error && (
        <div className="relative">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40">
              <LoadingSpinner text="边下边播准备中…" />
            </div>
          )}
          <video
            ref={videoRef}
            src={mediaSrc || undefined}
            controls
            className="max-w-full max-h-[80vh] mx-auto rounded-lg shadow-lg bg-black"
          />
        </div>
      )}
      {isAudio && !error && (
        <div className="flex flex-col items-center py-8">
          <span className="text-6xl mb-4">🎵</span>
          {loading && <LoadingSpinner text="边下边播准备中…" />}
          <audio ref={audioRef} src={mediaSrc || undefined} controls className="w-full max-w-xl" />
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

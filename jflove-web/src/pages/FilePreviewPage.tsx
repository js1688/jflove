import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import { fileService } from '../services/file-service';
import { useFileStore } from '../stores/file-store';
import { PageHeader } from '../components/PageHeader';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

const TEXT_EXTS = ['md', 'txt', 'json', 'xml', 'yaml', 'yml', 'csv', 'ini', 'log', 'js', 'ts', 'py', 'html', 'css'];
const IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'];
const VIDEO_EXTS = ['mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', 'm4v', 'mpg', 'mpeg', 'ts', '3gp'];
const AUDIO_EXTS = ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'wma', 'opus'];
const MAX_PREVIEW_BYTES = 500 * 1024 * 1024;

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
    setLoading(true);
    setError(null);
    setContent(null);
    setImageUrl(null);
    setMediaSrc(null);

    (async () => {
      try {
        if ((isVideo || isAudio) && (previewTarget.size ?? 0) > MAX_PREVIEW_BYTES) {
          setError('文件过大（超过 500 MB），请下载后使用本地播放器查看');
          setLoading(false);
          return;
        }
        if (isImage) {
          const bytes = await fileService.downloadRaw(Number(diskId), path, filename);
          if (cancelled) return;
          const blob = new Blob([bytes.buffer as ArrayBuffer]);
          setImageUrl(URL.createObjectURL(blob));
        } else if (isText) {
          const bytes = await fileService.downloadRaw(Number(diskId), path, filename);
          if (cancelled) return;
          setContent(new TextDecoder('utf-8').decode(bytes));
        } else if (isVideo || isAudio) {
          const bytes = await fileService.downloadRaw(Number(diskId), path, filename);
          if (cancelled) return;
          const blob = new Blob([bytes.buffer as ArrayBuffer], { type: mediaMime(ext) });
          const url = URL.createObjectURL(blob);
          if (isVideo && videoRef.current) videoRef.current.src = url;
          if (isAudio && audioRef.current) audioRef.current.src = url;
          setMediaSrc(url);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : '预览加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [diskId, previewTarget, path, filename, isImage, isText, isVideo, isAudio, ext]);

  useEffect(() => {
    return () => {
      if (mediaSrc && mediaSrc.startsWith('blob:')) URL.revokeObjectURL(mediaSrc);
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    };
  }, [mediaSrc, imageUrl]);

  return (
    <div>
      <PageHeader title={filename || '文件预览'} onBack={() => navigate(-1)} />
      {loading && <LoadingSpinner text="加载预览…" />}
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
          {isVideo && mediaSrc && (
            <video ref={videoRef} src={mediaSrc} controls className="max-w-full max-h-[80vh] mx-auto rounded-lg shadow-lg bg-black" />
          )}
          {isAudio && mediaSrc && (
            <div className="flex flex-col items-center py-8">
              <span className="text-6xl mb-4">🎵</span>
              <audio ref={audioRef} src={mediaSrc} controls className="w-full max-w-xl" />
            </div>
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

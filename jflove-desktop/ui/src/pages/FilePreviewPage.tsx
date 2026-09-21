/**
 * 文件预览页（桌面端）
 *
 * ## 与 Web 端 `jflove-web/src/pages/FilePreviewPage.tsx` 的差异
 *
 * **媒体一律走本地解码**（用户明确要求，不学 Web 的 `<video>` + MSE）：
 *
 * | 类型 | Web 端 | 桌面端 |
 * | --- | --- | --- |
 * | 图片 | `downloadRaw` → Blob → `<img>`（Chromium 解码） | `files.write_temp` → **Qt 原生解码** → 原生浮层 |
 * | 视频/音频 | `playWithMSE`（MediaSource + 帧解密） | **`StreamProxy` + `QMediaPlayer`**（OS 解码器）→ 原生浮层 |
 * | 文本/Markdown | `downloadRaw` → Blob → `file.text()` | 桥 `files.preview_text`（Python 解密后回文本） |
 *
 * 因此渲染层**永远不接触文件字节**；播放控制通过桥驱动原生播放器
 * （`media.play/pause/seek/set_volume`），进度由 `media.*` 事件回流。
 *
 * 不支持在线预览的类型（PDF、二进制等）给出**友好提示 + 下载入口**，
 * 而不是让它掉到路由错误页（历史问题，见下）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { useFileStore } from '../stores/file-store';
import { fileService } from '../services/file-service';
import { PageHeader } from '../components/PageHeader';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { MarkdownRenderer } from '../components/markdown';
import { MediaStage } from '../components/desktop/MediaStage';
import { Button, ErrorState, Icon, toast } from '../components/ui';
import { callBridge } from '../utils/desktop-bridge';

/** 可直接当文本读的扩展名 */
const TEXT_EXTS = [
  'md', 'txt', 'json', 'xml', 'yaml', 'yml', 'csv', 'ini', 'log',
  'js', 'ts', 'tsx', 'jsx', 'py', 'html', 'css', 'sh', 'bat', 'sql', 'toml', 'conf',
];
/** 图片（含 svg —— Qt 侧有 svg 图像插件，走原生解码） */
const IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'tif', 'tiff', 'ico', 'heic'];
/** 视频（含 mkv/avi 等 Chromium 放不出来的容器 —— 交给 OS 解码器） */
const VIDEO_EXTS = ['mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', 'm4v', 'mpg', 'mpeg', 'ts', '3gp', 'rmvb'];
/** 音频 */
const AUDIO_EXTS = ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'wma', 'opus', 'ape'];

/** 把毫秒格式化成 `mm:ss` */
function fmtTime(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '00:00';
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export function FilePreviewPage() {
  const { diskId } = useParams<{ diskId: string }>();
  const navigate = useNavigate();
  const previewTarget = useFileStore((s) => s.previewTarget);

  const filename = previewTarget?.name || '';
  const path = previewTarget?.path || '';
  const ext = filename.split('.').pop()?.toLowerCase() || '';

  const isImage = IMAGE_EXTS.includes(ext);
  const isVideo = VIDEO_EXTS.includes(ext);
  const isAudio = AUDIO_EXTS.includes(ext);
  const isText = TEXT_EXTS.includes(ext);
  const isMarkdown = ext === 'md';
  const isPdf = ext === 'pdf';
  //: 支持在线预览的类型（其余走友好提示 + 下载）
  const canPreview = isImage || isVideo || isAudio || isText;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [media, setMedia] = useState({ position: 0, duration: 0, playing: false });
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [volume, setVolume] = useState(100);
  const [fullscreen, setFullscreen] = useState(false);

  const numDiskId = Number(diskId);

  /** 回退到所在目录 */
  const goBack = useCallback(() => {
    navigate(`/files/${diskId}`);
  }, [navigate, diskId]);

  // 打开预览：按类型把内容准备好（文本走桥取文本；媒体起原生播放器）
  useEffect(() => {
    let cancelled = false;

    const open = async () => {
      if (!previewTarget || !diskId) {
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      setText(null);

      try {
        if (isText) {
          const result = await fileService.previewText(numDiskId, path);
          if (cancelled) return;
          setText(result.text);
          setTruncated(result.truncated);
        } else if (isImage) {
          // 落临时文件 → 交给 Qt 原生解码（不经过 Chromium）
          const temp = await fileService.writeTemp(numDiskId, path, filename);
          if (cancelled) return;
          await callBridge('media', 'show_image', { local_path: temp.local_path });
        } else if (isVideo || isAudio) {
          // StreamProxy 解密 → QMediaPlayer（OS 解码器）
          await callBridge('media', 'open_stream', {
            disk_id: numDiskId,
            rel_path: path,
            name: filename,
            kind: isVideo ? 'video' : 'audio',
            repair_task_id: previewTarget.repairTaskId ?? undefined,
          });
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : '预览失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void open();

    return () => {
      cancelled = true;
      // 关闭预览时要**先立刻隐藏原生窗口**再收尾。
      // 原生浮层是独立顶层窗口，会盖在 Web 内容之上；若只依赖异步的 close()
      // 或被吞掉的异常，返回列表后可能留下一块黑色原生窗口压住文件列表
      // （用户反馈："点返回后文件列表不见了"——这是桌面端独有的表现）。
      void callBridge('media', 'set_visible', { visible: false }).catch(() => {});
      void callBridge('media', 'close', {}).catch(() => {});
    };
    // 依赖只取真正影响"打开什么"的字段
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [diskId, filename, path, isText, isImage, isVideo, isAudio, numDiskId, previewTarget]);

  /** 下载到本地（原生「另存为」对话框 + Python 解密落盘） */
  const handleDownload = async () => {
    setDownloadBusy(true);
    try {
      const savePath = await fileService.pickSavePath(filename);
      if (!savePath) return;
      await fileService.download(numDiskId, path, savePath);
      toast.success('已保存', savePath);
    } catch (e) {
      toast.error('保存失败', e instanceof Error ? e.message : undefined);
    } finally {
      setDownloadBusy(false);
    }
  };

  const onMediaState = useCallback(
    (s: { position: number; duration: number; playing: boolean }) => setMedia(s),
    [],
  );
  const onMediaError = useCallback((message: string) => setError(message), []);

  const percent = useMemo(
    () => (media.duration > 0 ? Math.min(100, (media.position / media.duration) * 100) : 0),
    [media.duration, media.position],
  );

  /** 点击进度条跳转 */
  const seekTo = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    void callBridge('media', 'seek', { position: Math.round(ratio * media.duration) }).catch(() => {});
  };

  // ── 无预览目标（例如直接刷新页面：目标存在内存 store 里） ──
  if (!previewTarget) {
    return (
      <div className="flex h-full flex-col">
        <PageHeader title="预览" subtitle="文件管理" onBack={goBack} />
        <div className="px-4 py-3">
          <ErrorState
            message="没有可预览的文件（请从文件列表点进来看）"
            onRetry={goBack}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={filename}
        subtitle="文件管理 / 预览"
        onBack={goBack}
        actions={
          <div className="flex items-center gap-2">
            {/* 图标集里没有 pause，播放中用 stop（方块）表示「暂停」 */}
            {isVideo || isAudio ? (
              <Button size="sm" icon={media.playing ? 'stop' : 'play'} onClick={() => {
                void callBridge('media', media.playing ? 'pause' : 'play', {}).catch(() => {});
              }}>
                {media.playing ? '暂停' : '播放'}
              </Button>
            ) : null}
            <Button size="sm" icon="download" disabled={downloadBusy} onClick={() => void handleDownload()}>
              下载
            </Button>
          </div>
        }
      />

      {loading && <LoadingSpinner text="加载中…" />}

      {!loading && error && (
        <div className="px-4 py-3">
          <ErrorState message={error} onRetry={() => window.location.reload()} />
        </div>
      )}

      {/* 不支持在线预览：**友好提示 + 下载入口**（不能让用户看到路由错误页） */}
      {!loading && !error && !canPreview && (
        <div className="grid flex-1 place-items-center px-6">
          <div className="flex max-w-[420px] flex-col items-center gap-3 text-center">
            <div className="grid h-14 w-14 place-items-center rounded-2xl bg-sunken text-subtle">
              <Icon name={isPdf ? 'files' : 'close'} size="lg" />
            </div>
            <div className="text-[14px] font-semibold text-fg">
              {isPdf ? 'PDF 文件' : '不支持预览此文件类型'}
            </div>
            <div className="text-[12.5px] leading-relaxed text-subtle">
              该类型无法在应用内在线预览，请下载后用本地应用打开。
            </div>
            <Button variant="primary" size="sm" icon="download" disabled={downloadBusy} onClick={() => void handleDownload()}>
              下载文件
            </Button>
          </div>
        </div>
      )}

      {/* 图片 / 视频 / 音频：原生浮层盖在 MediaStage 的占位框上 */}
      {!loading && !error && (isImage || isVideo || isAudio) && (
        <div className="flex flex-1 flex-col gap-3 overflow-auto px-4 py-3">
          <MediaStage
            aspect={isAudio ? '21 / 6' : '16 / 9'}
            onState={onMediaState}
            onError={onMediaError}
          />
          {(isVideo || isAudio) && (
            <div className="flex items-center gap-2">
              <Icon name="stop" size="sm" className="hidden" />
              <Icon name="audio" size="sm" className="text-subtle" />
              <input
                type="range"
                min={0}
                max={100}
                value={volume}
                data-testid="media-volume"
                className="h-1.5 w-[110px] cursor-pointer accent-brand-500"
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setVolume(v);
                  void callBridge('media', 'set_volume', { volume: v }).catch(() => {});
                }}
              />
              <span className="tabular w-[34px] text-[11.5px] text-subtle">{volume}%</span>
              <div className="grow" />
              <Button
                size="sm"
                icon="fullscreen"
                data-testid="media-fullscreen"
                onClick={() => {
                  const next = !fullscreen;
                  setFullscreen(next);
                  void callBridge('media', 'set_fullscreen', { fullscreen: next }).catch(() => {});
                }}
              >
                {fullscreen ? '退出全屏' : '全屏'}
              </Button>
            </div>
          )}
          {(isVideo || isAudio) && (
            <div className="flex items-center gap-3">
              <span className="tabular w-[52px] text-right text-[11.5px] text-subtle">
                {fmtTime(media.position)}
              </span>
              <div
                className="h-1.5 flex-1 cursor-pointer rounded-full bg-sunken"
                onClick={seekTo}
                data-testid="media-progress"
              >
                <div
                  className="h-full rounded-full bg-brand-500 transition-[width] duration-200"
                  style={{ width: `${percent}%` }}
                />
              </div>
              <span className="tabular w-[52px] text-[11.5px] text-subtle">
                {fmtTime(media.duration)}
              </span>
            </div>
          )}
          <div className="text-[11.5px] text-subtle">
            画面由本地解码器（OS 解码器）渲染；应用内不经过浏览器解码。
          </div>
        </div>
      )}

      {/* 文本 / Markdown */}
      {!loading && !error && isText && (
        <div className="flex-1 overflow-auto px-4 py-3">
          {truncated && (
            <div className="mb-3 rounded-lg border border-line-subtle bg-sunken px-3 py-2 text-[12px] text-subtle">
              文件较大，仅显示前 2 MB 内容。
            </div>
          )}
          {isMarkdown ? (
            <MarkdownRenderer content={text ?? ''} className="mx-auto max-w-[880px]" />
          ) : (
            <pre className="overflow-x-auto rounded-lg border border-line-subtle bg-sunken p-4 text-[12.5px] leading-relaxed text-fg">
              {text ?? ''}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

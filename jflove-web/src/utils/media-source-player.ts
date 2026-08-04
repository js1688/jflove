/**
 * Media Source Extensions（MSE）流式播放器 —— 非安全上下文下的边下边播回退
 *
 * 原理：向后端 /api/v1/files/stream 打开加密 Range 流（openEncryptedStream 逐帧解密）
 * → append 到 MediaSource SourceBuffer，由浏览器解码播放，边下边播、不写磁盘。
 *
 * 适用性（重要，决定可靠性）：
 *  - MSE 仅对「可流式容器」可靠：fragmented MP4（moov 在前）、WebM、以及
 *    MP3 / FLAC / OGG 等简单音频。
 *  - 普通 MP4（moov 在尾部 / 非分片）、MKV / AVI / MOV / FLV / WMV 等 appendBuffer
 *    必然失败，playWithMSE 会在首帧 append 失败时返回 null，由调用方回退完整下载。
 *  - MSE 不要求安全上下文（HTTP 局域网可用），这是相对 Service Worker 的主要优势。
 *
 * 对比 Service Worker 流式代理：SW 为通用主路径（原生解码、全格式、拖动 seek 友好），
 * 本模块仅作「SW 不可用（非安全上下文）」时的回退。
 */

import { getSessionKey, getSessionId, getToken, getServerUrl } from './session';
import { openEncryptedStream } from './stream-frame';

/** H.264/AAC 系列 fMP4 视频 codec 候选（按优先级降序探测） */
const MP4_VIDEO_CODECS = [
  'video/mp4; codecs="avc1.64001F, mp4a.40.2"',   // H.264 High 3.1 + AAC-LC
  'video/mp4; codecs="avc1.4D401E, mp4a.40.2"',    // H.264 Main + AAC-LC
  'video/mp4; codecs="avc1.64001E, mp4a.40.2"',    // H.264 High + AAC-LC
  'video/mp4; codecs="avc1.42E01E, mp4a.40.2"',    // H.264 Baseline + AAC-LC
  'video/mp4',
];
/** fMP4 音频 codec 候选 */
const MP4_AUDIO_CODECS = [
  'audio/mp4; codecs="mp4a.40.2"',    // AAC-LC
  'audio/mp4; codecs="mp4a.40.5"',    // HE-AAC
  'audio/mp4; codecs="mp4a.40.29"',   // HE-AAC v2
  'audio/mp4',
];
/** WebM 视频 codec 候选 */
const WEBM_VIDEO_CODECS = [
  'video/webm; codecs="vp9, opus"',
  'video/webm; codecs="vp8, vorbis"',
  'video/webm',
];
/** MP3（MSE 简单音频流，Chrome 支持） */
const MP3_CODEC = 'audio/mpeg';
/** FLAC */
const FLAC_CODEC = 'audio/flac';
/** OGG / Opus 音频 */
const OGG_AUDIO_CODECS = [
  'audio/ogg; codecs="opus"',
  'audio/ogg; codecs="vorbis"',
  'audio/ogg',
];

/** 按格式探测可用的 MSE codec（不支持返回 null） */
function probeMseCodec(filename: string): string | null {
  const ext = (filename.split('.').pop() || '').toLowerCase();
  let candidates: string[];
  switch (ext) {
    case 'mp4': case 'm4v': case 'mov': case '3gp':
      candidates = MP4_VIDEO_CODECS;
      break;
    case 'webm':
      candidates = WEBM_VIDEO_CODECS;
      break;
    case 'm4a': case 'aac':
      candidates = MP4_AUDIO_CODECS;
      break;
    case 'mp3':
      candidates = [MP3_CODEC];
      break;
    case 'flac':
      candidates = [FLAC_CODEC];
      break;
    case 'ogg': case 'opus':
      candidates = OGG_AUDIO_CODECS;
      break;
    default:
      // wav / wma 等 MSE 不支持
      return null;
  }
  for (const c of candidates) {
    if (MediaSource.isTypeSupported(c)) return c;
  }
  return null;
}

/** 检测浏览器是否支持 MSE */
export function isMSESupported(): boolean {
  return (
    typeof MediaSource !== 'undefined'
    && typeof MediaSource.isTypeSupported === 'function'
  );
}

/** MSE 是否对当前文件类型可用（用于回退链判断） */
export function isMSEAvailable(filename: string): boolean {
  if (!isMSESupported()) return false;
  return probeMseCodec(filename) !== null;
}

/**
 * 使用 MSE 播放视频/音频（边下边播回退路径）。
 *
 * @returns 清理函数；文件类型 MSE 不可用 / 打开流失败 / 首帧 append 失败（如普通 MP4）
 *          时返回 null，由调用方回退到完整下载。
 */
export async function playWithMSE(
  video: HTMLVideoElement | HTMLAudioElement,
  diskId: number,
  path: string,
  filename: string,
): Promise<(() => void) | null> {
  if (!isMSESupported()) return null;
  const codec = probeMseCodec(filename);
  if (!codec) return null;

  const sessionKey = getSessionKey();
  const sessionId = getSessionId();
  const token = getToken();
  const serverUrl = getServerUrl();
  if (!sessionKey || !sessionId || !token) return null;

  const abortController = new AbortController();
  let mediaSource: MediaSource | null = null;
  let objectUrl = '';

  try {
    // 打开加密 Range 流（0..结尾），读取 meta 与逐帧解密的数据帧
    const { frames } = await openEncryptedStream(
      { sessionKey, sessionId, token, serverUrl },
      diskId,
      path,
      filename,
      0,
      -1,
      abortController.signal,
    );

    mediaSource = new MediaSource();
    objectUrl = URL.createObjectURL(mediaSource);
    video.src = objectUrl;

    // 等待 sourceopen
    await new Promise<void>((resolve, reject) => {
      const onOpen = () => {
        mediaSource?.removeEventListener('sourceopen', onOpen);
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(() => {
        mediaSource?.removeEventListener('sourceopen', onOpen);
        reject(new Error('MediaSource sourceopen 超时'));
      }, 10000);
      mediaSource?.addEventListener('sourceopen', onOpen);
    });

    let sourceBuffer: SourceBuffer;
    try {
      sourceBuffer = mediaSource.addSourceBuffer(codec);
    } catch {
      cleanup();
      return null;
    }

    // ── append 串行队列 + 首帧快速失败检测 ──
    // 普通 MP4（非 fMP4）appendBuffer 会立刻抛错或触发 error 事件，
    // 这里在首个 append 周期内捕获，尽快回退完整下载，避免白屏。
    const queue: Uint8Array[] = [];
    let pending = false;
    let ended = false;
    let failed = false;

    let resolveFirst!: (r: 'ok' | 'fail') => void;
    const firstPromise = new Promise<'ok' | 'fail'>((res) => { resolveFirst = res; });
    const firstTimer = setTimeout(() => resolveFirst('ok'), 2000);
    let firstSettled = false;
    const settleFirst = (r: 'ok' | 'fail') => {
      if (firstSettled) return;
      firstSettled = true;
      clearTimeout(firstTimer);
      resolveFirst(r);
    };

    const fail = () => {
      if (failed) return;
      failed = true;
      settleFirst('fail');
      cleanup();
    };

    const pump = () => {
      if (pending || queue.length === 0 || failed) return;
      pending = true;
      const data = queue.shift()!;
      try {
        // Uint8Array 即 BufferSource；显式断言规避 TS 对 SharedArrayBuffer 泛型的限制
        sourceBuffer.appendBuffer(data as unknown as BufferSource);
      } catch {
        settleFirst('fail');
        fail();
      }
    };

    const onUpdateEnd = () => {
      pending = false;
      settleFirst('ok');
      if (failed) return;
      if (queue.length > 0) pump();
      else if (ended) {
        try { mediaSource?.endOfStream(); } catch { /* ignore */ }
      }
    };
    sourceBuffer.addEventListener('updateend', onUpdateEnd);
    sourceBuffer.addEventListener('error', () => {
      settleFirst('fail');
      fail();
    });

    // 消费解密帧 → append 队列
    (async () => {
      try {
        for await (const chunk of frames) {
          if (failed) return;
          queue.push(chunk);
          pump();
        }
        ended = true;
        if (!pending && queue.length === 0) {
          try { mediaSource?.endOfStream(); } catch { /* ignore */ }
        }
      } catch {
        fail();
      }
    })();

    // 等待首个 append 结果：fail 说明容器不被 MSE 支持，回退完整下载
    const first = await firstPromise;
    if (first === 'fail') {
      return null;
    }

    function cleanup(): void {
      abortController.abort();
      try { void frames.return?.(undefined); } catch { /* ignore */ }
      if (objectUrl) {
        try { URL.revokeObjectURL(objectUrl); } catch { /* ignore */ }
      }
      if (mediaSource) {
        try {
          if (mediaSource.readyState === 'open') mediaSource.endOfStream();
          if (sourceBuffer && mediaSource.sourceBuffers.length > 0) {
            mediaSource.removeSourceBuffer(sourceBuffer);
          }
        } catch { /* ignore */ }
      }
      try { video.src = ''; } catch { /* ignore */ }
    }

    return cleanup;
  } catch {
    // 打开流 / sourceopen 失败 → 回退
    if (objectUrl) {
      try { URL.revokeObjectURL(objectUrl); } catch { /* ignore */ }
    }
    abortController.abort();
    return null;
  }
}

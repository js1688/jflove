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
 * 默认 fMP4 codec：probeMseCodec 不支持的扩展名（mkv/avi/flv/wmv/mpg/ts/aac/wma 等）
 * 在服务端修复开启时会被重封装为 fMP4，这里用 fMP4 默认 codec 尝试，
 * 首帧 append 失败再由调用方回退 Blob 下载（v1.4.0）。
 */
function defaultMp4Codec(filename: string): string {
  const ext = (filename.split('.').pop() || '').toLowerCase();
  const AUDIO_EXTS = ['mp3', 'wav', 'flac', 'ogg', 'opus', 'm4a', 'aac', 'wma'];
  // 修复流统一 fMP4（H.264 + AAC，或仅视频）。必须带 codecs 串——
  // 裸 'video/mp4' 不被 MediaSource.isTypeSupported 支持（测试确认）。
  // 声明的 codec 为"允许出现的流"，实际流可只有其中部分，MSE 接受。
  return AUDIO_EXTS.includes(ext)
    ? 'audio/mp4; codecs="mp4a.40.2"'
    : 'video/mp4; codecs="avc1.64001f, mp4a.40.2"';
}

/**
 * 使用 MSE 播放视频/音频（v1.4.0 主路径）。
 *
 * @returns 清理函数；打开流失败 / 首帧 append 失败（容器不被 MSE 支持且服务端未修复）
 *          时返回 null，由调用方回退到完整下载。
 */
export async function playWithMSE(
  video: HTMLVideoElement | HTMLAudioElement,
  diskId: number,
  path: string,
  filename: string,
): Promise<(() => void) | null> {
  if (!isMSESupported()) return null;
  // 初始 codec：健康文件用 MSE 探测/默认；修复流（time）在读取 meta 后
  // 用服务端解析的真实 codec（meta.codec）覆盖（v1.4.0 测试发现：codec 声明
  // 必须与实际流的全部 track 匹配，否则 append 报错）。
  let codec = probeMseCodec(filename) ?? defaultMp4Codec(filename);

  const sessionKey = getSessionKey();
  const sessionId = getSessionId();
  const token = getToken();
  const serverUrl = getServerUrl();
  if (!sessionKey || !sessionId || !token) return null;

  const abortController = new AbortController();
  let mediaSource: MediaSource | null = null;
  let objectUrl = '';
  // 当前流模式（v1.4.0）：byte=健康文件原文件字节 range；time=修复流时间 range
  let streamMode: 'byte' | 'time' = 'byte';
  // 当前活动流（seek 重拉时释放旧流）
  let currentFrames: AsyncGenerator<Uint8Array> | null = null;
  // 当前数据帧生成器（consume 引用；seek 重建后更新）
  let frames: AsyncGenerator<Uint8Array>;
  // 是否处于 seek 重建中（避免并发触发多次重拉）
  let seeking = false;
  // 当前是否已结束
  let ended = false;
  let failed = false;
  // seek 监听器的移除闭包（cleanup 可能在 onSeeked 初始化前被调用，
  // 用闭包标志避免直接引用后声明的 onSeeked 触发 TDZ 崩溃）
  let seekCleanup: (() => void) | null = null;

  /**
   * 打开加密流（初始与 seek 共用）。
   * 初始请求同时携带字节 range 与时间 range（range_start_seconds），
   * 服务端根据文件状态返回 byte（健康）或 time（需修复）meta。
   *
   * @param rangeStartSeconds 修复流时间起点（秒）；健康文件忽略
   */
  const openStream = async (rangeStartSeconds: number) => {
    if (currentFrames) {
      try { void currentFrames.return?.(undefined); } catch { /* ignore */ }
    }
    const opened = await openEncryptedStream(
      { sessionKey, sessionId, token, serverUrl },
      diskId,
      path,
      filename,
      0,
      -1,
      rangeStartSeconds,
      abortController.signal,
    );
    currentFrames = opened.frames;
    frames = opened.frames;
    streamMode = opened.meta.stream_mode === 'time' ? 'time' : 'byte';
    return opened;
  };

  try {
    // 初始打开（从 0 开始）；修复流 meta 携带真实 codec，覆盖默认探测值
    const initial = await openStream(0);
    if (initial.meta.stream_mode === 'time' && initial.meta.codec) {
      // 服务端 codec 为裸 codec 串（如 "avc1.64000c" / "avc1.64000c, mp4a.40.2"），
      // addSourceBuffer 需要完整 MIME（如 'video/mp4; codecs="avc1.64000c"'），
      // 这里按是否含视频轨包装成 video/mp4 或 audio/mp4（v1.4.0 修复：裸 codec 会
      // 抛 NotSupportedError 导致 MSE 回退 Blob 下载）。
      const raw = initial.meta.codec.trim();
      codec = raw.includes('avc1')
        ? `video/mp4; codecs="${raw}"`
        : `audio/mp4; codecs="${raw}"`;
    }

    mediaSource = new MediaSource();
    objectUrl = URL.createObjectURL(mediaSource);
    video.src = objectUrl;

    // 等待 sourceopen（初始与 seek 重建共用；读取当前 mediaSource 实例）
    const waitSourceOpen = (): Promise<void> => new Promise<void>((resolve, reject) => {
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
    await waitSourceOpen();

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

    // 消费解密帧 → append 队列（seek 重建后以新 frames 再次调用）
    const consume = async () => {
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
    };
    void consume();

    // 等待首个 append 结果：fail 说明容器不被 MSE 支持，回退完整下载
    const first = await firstPromise;
    if (first === 'fail') {
      return null;
    }

    // ── seek 处理（仅 time 修复流支持按时间重拉；byte 流走 MSE 原生已缓冲 seek）──
    // 整体重建 MediaSource（M2 修复）：彻底清空旧 SourceBuffer，避免 remove 失败
    // 残留旧 buffer 与新 buffer 数据重叠导致播放异常。
    const rebuildSource = async (): Promise<void> => {
      try {
        if (sourceBuffer && mediaSource && mediaSource.sourceBuffers.length > 0) {
          if (sourceBuffer.updating) {
            try { sourceBuffer.abort(); } catch { /* ignore */ }
          }
          mediaSource.removeSourceBuffer(sourceBuffer);
        }
      } catch { /* ignore */ }
      try {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
      } catch { /* ignore */ }
      // 全新 MediaSource + URL
      mediaSource = new MediaSource();
      objectUrl = URL.createObjectURL(mediaSource);
      video.src = objectUrl;
      await waitSourceOpen();
      sourceBuffer = mediaSource.addSourceBuffer(codec);
      sourceBuffer.addEventListener('updateend', onUpdateEnd);
      sourceBuffer.addEventListener('error', () => {
        settleFirst('ok');
        fail();
      });
      queue.length = 0;
      pending = false;
      ended = false;
    };

    const onSeeked = () => {
      if (streamMode !== 'time' || failed || seeking) return;
      const target = typeof video.currentTime === 'number' ? video.currentTime : 0;
      if (target <= 0) return;
      seeking = true;
      // 重建 MediaSource 并按目标时间重新拉取修复流（每次 seek 服务端重新 -ss）
      rebuildSource()
        .then(() => openStream(target))
        .then(() => {
          void consume();
        })
        .catch(() => { /* seek 重建/重拉失败：保持现状，不打断播放 */ })
        .finally(() => { seeking = false; });
    };
    video.addEventListener('seeked', onSeeked);
    seekCleanup = () => {
      try { video.removeEventListener('seeked', onSeeked); } catch { /* ignore */ }
    };

    function cleanup(): void {
      abortController.abort();
      try { void frames.return?.(undefined); } catch { /* ignore */ }
      if (currentFrames && currentFrames !== frames) {
        try { void currentFrames.return?.(undefined); } catch { /* ignore */ }
      }
      try { seekCleanup?.(); } catch { /* ignore */ }
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
      // 仅当 video 仍指向本实例的 objectUrl 时才清空 src：
      // React StrictMode 双跑 effect 时，旧实例的 cleanup 不应破坏
      // 新实例已设置的 MSE URL（v1.4.0 修复）。
      if (objectUrl && video.src === objectUrl) {
        try { video.src = ''; } catch { /* ignore */ }
      }
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

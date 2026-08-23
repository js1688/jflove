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

/** MP4 家族扩展名：byte 模式下需额外判断是否 fMP4（moov 是否前置） */
const MP4_LIKE_EXTS = ['mp4', 'm4v', 'mov', '3gp', 'm4a'];

/**
 * 判断一段 MP4 数据是否为可流式初始化段（fMP4：ftyp 后紧跟 moov）。
 *
 * 与后端 media_probe._moov_at_front 判定规则一致：
 *   - 首个非 ftyp box 为 moov → fMP4（可边下边播）
 *   - 首个非 ftyp box 为 mdat → 普通 MP4（moov 在尾部，MSE 无法初始化，
 *     Chrome 只无限等待、不报错 → 必须主动判定并回退完整下载）
 *   - 无法判定 → 返回 false（保守回退下载，下载后原生播放必然可用）
 */
function isFmp4Init(data: Uint8Array): boolean {
  if (data.length < 8) return false;
  let pos = 0;
  // 跳过 ftyp box（若存在）
  if (data[4] === 0x66 && data[5] === 0x74 && data[6] === 0x79 && data[7] === 0x70) {
    const ftypSize = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3];
    pos = ftypSize >= 8 ? ftypSize : 0;
  }
  // 扫描头部 box：首个业务 box 为 moov 且含 mvex 才是 fMP4；
  // 先见 mdat 则普通 MP4（moov 尾部）；moov 前置但无 mvex 是 faststart
  // 非分片 MP4，MSE 同样无法边下边播 → 均回退完整下载（v1.4.1）。
  while (pos + 8 <= data.length) {
    const boxSize = (data[pos] << 24) | (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3];
    const boxType = String.fromCharCode(
      data[pos + 4], data[pos + 5], data[pos + 6], data[pos + 7],
    );
    if (boxType === 'moov') {
      // 在 moov box 内查找 mvex（fragmented 标志）
      const moovEnd = Math.min(pos + boxSize, data.length);
      for (let p = pos + 8; p + 4 <= moovEnd; p++) {
        if (data[p] === 0x6d && data[p + 1] === 0x76 && data[p + 2] === 0x65 && data[p + 3] === 0x78) {
          return true;
        }
      }
      return false;
    }
    if (boxType === 'mdat') return false;
    if (boxSize < 8) break;
    pos += boxSize;
  }
  return false;
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
  signal?: AbortSignal,
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
  // React StrictMode 双跑 effect 会并发打开两个流；外部 signal 由页面在卸载时
  // abort，立即中止第一个实例的在途流，避免双流互相覆盖 src 导致卡死（v1.4.1）。
  if (signal) {
    if (signal.aborted) abortController.abort();
    else signal.addEventListener('abort', () => abortController.abort(), { once: true });
  }
  let mediaSource: MediaSource | null = null;
  let objectUrl = '';
  // 当前流模式（v1.4.0）：byte=健康文件原文件字节 range；time=修复流时间 range
  let streamMode: 'byte' | 'time' = 'byte';
  // 当前活动流（seek 重拉时释放旧流）
  let currentFrames: AsyncGenerator<Uint8Array> | null = null;
  // 当前流对应的中止控制器：seek 替换流时 abort，真正 cancel 旧 fetch，
  // 让服务端立即终止旧 ffmpeg、释放修复并发槽，否则新流会因并发占满而失败
  // （「拖拽后无法播放」的根因之一，v1.4.1）
  let currentStreamAbort: AbortController | null = null;
  // 当前数据帧生成器（consume 引用；seek 重建后更新）
  let frames: AsyncGenerator<Uint8Array>;
  // 是否处于 seek 重建中（避免并发触发多次重拉）
  let seeking = false;
  // 当前是否已结束
  let ended = false;
  let failed = false;
  // 完整视频时长（来自服务端 time meta 的 duration 字段）；empty_moov 的
  // fMP4 moov 无时长信息，浏览器无法知道总时长/进度条 → 用 meta 显式设置。
  // 该值恒为完整时长（不随 seek 缩减），保证进度条总时长恒定。
  let fullDuration = 0;
  // consume 代际计数：seek 重建时 +1，使旧 consume 循环失效，避免旧循环
  // 退出时把 ended/endOfStream 写到新流上（seek 播放失败根因，v1.4.1）
  let consumeGeneration = 0;
  // 待定位的 seek 目标（-1 = 无）：seek 重拉后等首个媒体分片 append 完成
  // （buffered 非空）再把播放头定位过去，避免「init 段 append 时 buffered 为空
  // → 触发 seeked → 死循环重拉」。
  let pendingSeekTarget = -1;
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
    // 中止旧流（真正 cancel 旧 fetch → 服务端终止旧 ffmpeg、释放并发槽），
    // 避免 seek 后旧流仍占着修复名额导致新流排队失败。
    if (currentStreamAbort) {
      try { currentStreamAbort.abort(); } catch { /* ignore */ }
    }
    if (currentFrames) {
      try { void currentFrames.return?.(undefined); } catch { /* ignore */ }
    }
    // 每个流独立 AbortController，并链上「会话级 abort（组件卸载）」与
    // 「外部 signal（StrictMode 卸载）」，任一触发都取消当前 fetch。
    currentStreamAbort = new AbortController();
    const chain = (s: AbortSignal | undefined) => {
      if (!s) return;
      if (s.aborted) currentStreamAbort?.abort();
      else s.addEventListener('abort', () => currentStreamAbort?.abort(), { once: true });
    };
    chain(abortController.signal);
    chain(signal);
    const opened = await openEncryptedStream(
      { sessionKey, sessionId, token, serverUrl },
      diskId,
      path,
      filename,
      0,
      -1,
      rangeStartSeconds,
      currentStreamAbort.signal,
    );
    currentFrames = opened.frames;
    frames = opened.frames;
    streamMode = opened.meta.stream_mode === 'time' ? 'time' : 'byte';
    // time 模式 meta 携带完整时长（秒），用于设置 MediaSource.duration
    if (streamMode === 'time' && typeof opened.meta.duration === 'number' && opened.meta.duration > 0) {
      fullDuration = Math.max(fullDuration, opened.meta.duration);
    }
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
      let raw = initial.meta.codec.trim();
      // v1.4.1：TS 等来源重封装出的 fMP4 常含 AAC 音频轨，但服务端解析的
      // codec 可能只含视频（音频 esds 缺 DecoderSpecificInfo）。此时若不声明
      // 音频 codec，Chrome 的 addSourceBuffer 会因「实际含音频轨但 codecs 未
      // 声明」而拒绝，导致 MSE 回退下载、TS 又无法原生播放 → 补 mp4a.40.2 兜底。
      if (raw.includes('avc1') && !raw.includes('mp4a')) {
        raw += ', mp4a.40.2';
      }
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

    // 设置媒体总时长（恒为完整时长，不随 seek 缩减），使进度条可用、
    // 点击可 seek；empty_moov 的 fMP4 moov 不含时长信息，必须显式设置。
    // 显式设置后 endOfStream() 不会覆盖该值，总时长保持恒定。
    if (fullDuration > 0) {
      try {
        mediaSource.duration = fullDuration;
      } catch { /* ignore */ }
    }

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
      // seek 目标定位：等首个媒体分片 append 完成（buffered 非空）后再把播放头
      // 定位到 target，避免 init 段（ftyp+moov）append 时 buffered 仍为空 →
      // 触发 seeked → 死循环重拉。
      if (pendingSeekTarget >= 0 && sourceBuffer && sourceBuffer.buffered.length > 0) {
        const t = pendingSeekTarget;
        pendingSeekTarget = -1;
        try { video.currentTime = t; } catch { /* ignore */ }
      }
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
    // v1.4.1：byte 模式下普通 MP4（moov 在尾）MSE 无法初始化，且 Chrome 不报错、
    // 只无限等待（首帧 append 不触发 error 事件），导致卡死在 readyState=0。
    // 首个数据帧到达时主动判断是否 fMP4，非 fMP4 立即失败回退完整下载。
    const needsFmp4Check = MP4_LIKE_EXTS.includes(
      (filename.split('.').pop() || '').toLowerCase(),
    );
    let firstChunkChecked = false;

    const consume = async () => {
      const gen = ++consumeGeneration;
      try {
        for await (const chunk of frames) {
          if (failed || gen !== consumeGeneration) return;
          if (!firstChunkChecked) {
            firstChunkChecked = true;
            // time 修复流由服务端保证输出 fMP4，无需检查；仅 byte 原文件流需要判定
            if (streamMode === 'byte' && needsFmp4Check && !isFmp4Init(chunk)) {
              settleFirst('fail');
              fail();
              return;
            }
          }
          queue.push(chunk);
          pump();
        }
        // 仅当前代际的 consume 才允许结束流；旧代际（seek 前）退出时
        // 不能把 ended/endOfStream 写到新流上
        if (gen !== consumeGeneration) return;
        ended = true;
        if (!pending && queue.length === 0) {
          try { mediaSource?.endOfStream(); } catch { /* ignore */ }
        }
      } catch {
        if (gen === consumeGeneration) fail();
      }
    };
    void consume();

    // 等待首个 append 结果：fail 说明容器不被 MSE 支持，回退完整下载
    const first = await firstPromise;
    if (first === 'fail') {
      return null;
    }

    // ── seek 处理（仅 time 修复流支持按时间重拉；byte 流走 MSE 原生已缓冲 seek）──
    // v1.4.1 修复：不重建 MediaSource（避免 currentTime 归零、总时长被改成剩余时长），
    // 而是移除旧 SourceBuffer、新建一个 SourceBuffer，并用 timestampOffset 把服务端
    // 「0 基准」的新流映射到 [target, 完整时长] 时间轴；MediaSource.duration 始终 =
    // 完整时长，因此进度条总时长恒定、播放头停在 target 而非归零。
    const isBuffered = (t: number): boolean => {
      const ranges = sourceBuffer?.buffered;
      if (!ranges) return false;
      for (let i = 0; i < ranges.length; i++) {
        if (t >= ranges.start(i) && t <= ranges.end(i)) return true;
      }
      return false;
    };

    const seekTo = async (target: number): Promise<void> => {
      // 使旧 consume 循环失效，防止其退出时把 ended/endOfStream 写到新流
      consumeGeneration++;
      // MediaSource 已 ended（例如播完后再拖回）→ 无法 addSourceBuffer，整体重建
      if (!mediaSource || mediaSource.readyState !== 'open') {
        try { if (objectUrl) URL.revokeObjectURL(objectUrl); } catch { /* ignore */ }
        mediaSource = new MediaSource();
        objectUrl = URL.createObjectURL(mediaSource);
        video.src = objectUrl;
        await waitSourceOpen();
        if (fullDuration > 0) {
          try { mediaSource.duration = fullDuration; } catch { /* ignore */ }
        }
      } else {
        try {
          if (sourceBuffer && mediaSource.sourceBuffers.length > 0) {
            if (sourceBuffer.updating) {
              try { sourceBuffer.abort(); } catch { /* ignore */ }
            }
            mediaSource.removeSourceBuffer(sourceBuffer);
          }
        } catch { /* ignore */ }
      }
      // 新建 SourceBuffer（同一 MediaSource 内，duration 保持完整时长不变）。
      // timestampOffset 将新流（0 基准）映射到 [target, ...]，故无需归零播放头。
      sourceBuffer = mediaSource.addSourceBuffer(codec);
      sourceBuffer.timestampOffset = target;
      sourceBuffer.addEventListener('updateend', onUpdateEnd);
      sourceBuffer.addEventListener('error', () => {
        settleFirst('ok');
        fail();
      });
      queue.length = 0;
      pending = false;
      ended = false;
      pendingSeekTarget = target;
      // 按目标时间重新拉取修复流（每次 seek 服务端重新 -ss）
      await openStream(target);
      void consume();
    };

    const onSeeked = () => {
      if (streamMode !== 'time' || failed || seeking) return;
      const target = typeof video.currentTime === 'number' ? video.currentTime : 0;
      if (target <= 0) return;
      // 目标点已在当前缓冲范围内 → 原生 seek，无需重拉
      if (isBuffered(target)) return;
      seeking = true;
      seekTo(target)
        .catch(() => { /* seek 重拉失败：保持现状，不打断播放 */ })
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

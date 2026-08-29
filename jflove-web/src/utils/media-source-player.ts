/**
 * Media Source Extensions（MSE）流式播放器 —— 非安全上下文下的边下边播
 *
 * v1.4.2 重构说明（架构变更：实时修复 → 手动离线修复 + 播放纯净化）：
 *  - 移除 time 修复流模式（timestampOffset / 代际计数 / -ss 重拉）——服务端
 *    已不再实时修复，本模块只处理健康文件的 byte 流
 *  - 新增 repairTaskId 支持（修复中心「验证播放」经 /stream?repair_task_id
 *    拉取修复产物，产物为 faststart MP4，走同一 byte 管线）
 *  - 新增 byte 模式前向 seek 按需重拉：MP4 家族文件拖到未缓冲区域时，
 *    按「目标时间/总时长 × 文件大小」估算字节偏移重拉（取 init 段 + 自偏移
 *    处起的数据），失败时保持顺序下载渐进到达（不劣于 v1.4.1 行为）
 *
 * 原理：向后端 /api/v1/files/stream 打开加密 Range 流（openEncryptedStream
 * 逐帧解密）→ append 到 MediaSource SourceBuffer，由浏览器解码播放，
 * 边下边播、不写磁盘。
 *
 * 适用性（重要，决定可靠性）：
 *  - MSE 仅对「可流式容器」可靠：fragmented MP4（moov 在前）、WebM、以及
 *    MP3 / FLAC / OGG 等简单音频。
 *  - 普通 MP4（moov 在尾部 / 非分片）、MKV / AVI / MOV / FLV / WMV 等
 *    appendBuffer 必然失败，playWithMSE 会在首帧 append 失败时返回 null，
 *    由调用方回退完整下载。
 *  - MSE 不要求安全上下文（HTTP 局域网可用）。
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
 * 默认 fMP4 codec：probeMseCodec 不支持的扩展名（mkv/avi/flv/wmv/mpg/ts 等）
 * 在服务端修复开启时会被重封装为 fMP4；v1.4.2 起服务端不再实时修复，这些
 * 格式不会走到 MSE（首帧 append 失败由调用方回退 Blob 下载）。
 */
function defaultMp4Codec(filename: string): string {
  const ext = (filename.split('.').pop() || '').toLowerCase();
  const AUDIO_EXTS = ['mp3', 'wav', 'flac', 'ogg', 'opus', 'm4a', 'aac', 'wma'];
  return AUDIO_EXTS.includes(ext)
    ? 'audio/mp4; codecs="mp4a.40.2"'
    : 'video/mp4; codecs="avc1.64001f, mp4a.40.2"';
}

/** MP4 家族扩展名：byte 模式下需额外判断是否 fMP4（moov 是否前置） */
const MP4_LIKE_EXTS = ['mp4', 'm4v', 'mov', '3gp', 'm4a'];

/**
 * 判断一段 MP4 数据是否为可流式初始化段（fMP4：ftyp 后紧跟 moov）。
 *
 * 与后端 media_probe._moov_at_front 判定规则一致（v1.4.2）：
 *   - 首个非 ftyp box 为 moov → 可边下边播（faststart 亦算 moov 前置，
 *     但非分片 MP4 仍无法 MSE 增量 append——此处保持 fMP4 判断用于回退）
 *   - 首个非 ftyp box 为 mdat → 普通 MP4（moov 在尾部，MSE 无法初始化，
 *     Chrome 只无限等待、不报错 → 必须主动判定并回退完整下载）
 *   - 无法判定 → 返回 false（保守回退下载）
 */
function isFmp4Init(data: Uint8Array): boolean {
  if (data.length < 8) return false;
  let pos = 0;
  if (data[4] === 0x66 && data[5] === 0x74 && data[6] === 0x79 && data[7] === 0x70) {
    const ftypSize = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3];
    pos = ftypSize >= 8 ? ftypSize : 0;
  }
  while (pos + 8 <= data.length) {
    const boxSize = (data[pos] << 24) | (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3];
    const boxType = String.fromCharCode(
      data[pos + 4], data[pos + 5], data[pos + 6], data[pos + 7],
    );
    if (boxType === 'moov') return true;
    if (boxType === 'mdat') return false;
    if (boxSize < 8) break;
    pos += boxSize;
  }
  return false;
}

/** 从 box 数据中定位 moov 结束偏移（供前向 seek 取 init 段；找不到返回 -1） */
function findMoovEnd(data: Uint8Array): number {
  let pos = 0;
  while (pos + 8 <= data.length) {
    const boxSize = (data[pos] << 24) | (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3];
    const boxType = String.fromCharCode(
      data[pos + 4], data[pos + 5], data[pos + 6], data[pos + 7],
    );
    if (boxType === 'moov') return pos + boxSize;
    if (boxSize < 8) break;
    pos += boxSize;
  }
  return -1;
}

/**
 * 在数据块中定位第一个完整 moof 分片起点（4B size + 'moof'）。
 *
 * 前向 seek 的字节偏移是时间估算值，通常落在某个 moof/mdat 内部；
 * 逐字节扫描 'moof' 标记并校验前 4 字节 size 合理（>=8 且不超过剩余
 * 数据量）。找不到返回 -1（调用方保持顺序下载，不劣化）。
 */
function findMoofStart(data: Uint8Array): number {
  for (let p = 0; p + 8 <= data.length; p++) {
    if (data[p + 4] === 0x6d && data[p + 5] === 0x6f && data[p + 6] === 0x6f && data[p + 7] === 0x66) {
      const size = (data[p] << 24) | (data[p + 1] << 16) | (data[p + 2] << 8) | data[p + 3];
      if (size >= 8 && p + size <= data.length) return p;
    }
  }
  return -1;
}

/**
 * 使用 MSE 播放视频/音频（v1.4.2：仅 byte 模式）。
 *
 * @returns 清理函数；打开流失败 / 首帧 append 失败（容器不被 MSE 支持）
 *          时返回 null，由调用方回退到完整下载。
 */
export async function playWithMSE(
  video: HTMLVideoElement | HTMLAudioElement,
  diskId: number,
  path: string,
  filename: string,
  repairTaskId = 0,
  signal?: AbortSignal,
): Promise<(() => void) | null> {
  if (!isMSESupported()) return null;
  const codec = probeMseCodec(filename) ?? defaultMp4Codec(filename);

  const sessionKey = getSessionKey();
  const sessionId = getSessionId();
  const token = getToken();
  const serverUrl = getServerUrl();
  if (!sessionKey || !sessionId || !token) return null;

  const abortController = new AbortController();
  // React StrictMode 双跑 effect 会并发打开两个流；外部 signal 由页面在卸载时
  // abort，立即中止第一个实例的在途流，避免双流互相覆盖 src 导致卡死。
  if (signal) {
    if (signal.aborted) abortController.abort();
    else signal.addEventListener('abort', () => abortController.abort(), { once: true });
  }
  let mediaSource: MediaSource | null = null;
  let objectUrl = '';
  // 当前活动流（seek 重拉时释放旧流）
  let currentFrames: AsyncGenerator<Uint8Array> | null = null;
  let currentStreamAbort: AbortController | null = null;
  // 当前数据帧生成器
  let frames: AsyncGenerator<Uint8Array>;
  // 是否处于 seek 重建中（避免并发触发多次重拉）
  let seeking = false;
  let ended = false;
  let failed = false;
  // 文件总字节（meta.file_size，前向 seek 字节估算用）
  let fileSize = 0;
  // consume 代际计数：重拉时 +1 使旧循环失效（旧循环不能写新流）
  let consumeGeneration = 0;
  // 等待 seek 的数据到达后定位（ended 后重建场景）
  let pendingSeekTarget = -1;
  let seekCleanup: (() => void) | null = null;

  /**
   * 打开加密字节流（初始与 seek 共用；v1.4.2 恒为 byte 模式）。
   */
  const openStream = async (rangeStart: number, rangeEnd: number) => {
    if (currentStreamAbort) {
      try { currentStreamAbort.abort(); } catch { /* ignore */ }
    }
    if (currentFrames) {
      try { void currentFrames.return?.(undefined); } catch { /* ignore */ }
    }
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
      rangeStart,
      rangeEnd,
      repairTaskId,
      currentStreamAbort.signal,
    );
    currentFrames = opened.frames;
    frames = opened.frames;
    if (typeof opened.meta.file_size === 'number' && opened.meta.file_size > 0) {
      fileSize = opened.meta.file_size;
    }
    return opened;
  };

  try {
    // 初始打开（全文件 range）
    await openStream(0, -1);

    mediaSource = new MediaSource();
    objectUrl = URL.createObjectURL(mediaSource);
    video.src = objectUrl;

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
      // seek 目标定位：等 buffered 覆盖目标后把播放头定位过去；
      // 目标落在分片缝隙（分片起点晚于目标）→ 定位到最近可用分片起点
      if (pendingSeekTarget >= 0 && sourceBuffer.buffered.length > 0) {
        const t = pendingSeekTarget;
        const ranges = sourceBuffer.buffered;
        let covered = false;
        let firstStart = Infinity;
        for (let i = 0; i < ranges.length; i++) {
          firstStart = Math.min(firstStart, ranges.start(i));
          if (t >= ranges.start(i) && t <= ranges.end(i)) { covered = true; break; }
        }
        if (covered || (Number.isFinite(firstStart) && firstStart > t)) {
          pendingSeekTarget = -1;
          try { video.currentTime = covered ? t : firstStart; } catch { /* ignore */ }
        }
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

    // 消费解密帧 → append 队列（v1.4.2：byte 模式；MP4 家族首帧非 fMP4 立即回退）
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
            if (needsFmp4Check && !isFmp4Init(chunk)) {
              settleFirst('fail');
              fail();
              return;
            }
          }
          queue.push(chunk);
          pump();
        }
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

    // ── seek 处理（v1.4.2：仅 byte 模式）──
    // 目标在已缓冲范围 → 浏览器原生 seek；未缓冲：
    //   - MP4 家族：估算字节偏移重拉（init 段 + 自偏移处起的数据）
    //   - 其他格式 / 重拉失败：保持顺序下载渐进到达（不劣化）
    const isBuffered = (t: number): boolean => {
      const ranges = sourceBuffer?.buffered;
      if (!ranges) return false;
      for (let i = 0; i < ranges.length; i++) {
        if (t >= ranges.start(i) && t <= ranges.end(i)) return true;
      }
      return false;
    };

    const seekTo = async (target: number): Promise<void> => {
      // S-2 修复：先使旧 consume 循环失效，再 abort 旧流——避免旧循环因 abort
      // 抛错触发 fail() 摧毁整个播放。
      consumeGeneration++;
      const duration = typeof video.duration === 'number' && video.duration > 0
        ? video.duration
        : 0;
      const ext = (filename.split('.').pop() || '').toLowerCase();
      if (!MP4_LIKE_EXTS.includes(ext) || fileSize <= 0 || duration <= 0) {
        return; // 非 MP4 或无估算依据：保持顺序下载
      }
      // 估算字节偏移：目标时间比例 × 文件大小（fMP4 分片粒度下误差 1 个分片内）
      const estimate = Math.floor((target / duration) * fileSize);
      try {
        // 1. 取 init 段（0 ~ moov 结束）
        const init = await openStream(0, 1024 * 1024);
        const initBytes: Uint8Array[] = [];
        for await (const c of init.frames) {
          initBytes.push(c);
          if (initBytes.reduce((n, b) => n + b.length, 0) > 1024 * 1024) break;
        }
        const initData = concatBytes(initBytes);
        const moovEnd = findMoovEnd(initData);
        if (moovEnd <= 0) {
          await rebuildFromStart(target);
          return;
        }

        // 2. 自估算偏移取数据并定位 moof 分片起点
        const data = await openStream(estimate, -1);
        const dataBytes: Uint8Array[] = [];
        let collected = 0;
        for await (const c of data.frames) {
          dataBytes.push(c);
          collected += c.length;
          if (collected > 8 * 1024 * 1024) break; // 8MB 内找不到分片边界即放弃
        }
        const dataAll = concatBytes(dataBytes);
        const moofAt = findMoofStart(dataAll);
        if (moofAt < 0) {
          await rebuildFromStart(target);
          return;
        }

        // 3. 重建 SourceBuffer：init 段 + 自 moof 起的数据
        if (mediaSource && mediaSource.readyState === 'open') {
          try {
            if (sourceBuffer.updating) { try { sourceBuffer.abort(); } catch { /* ignore */ } }
            mediaSource.removeSourceBuffer(sourceBuffer);
          } catch { /* ignore */ }
        }
        sourceBuffer = mediaSource!.addSourceBuffer(codec);
        sourceBuffer.addEventListener('updateend', onUpdateEnd);
        sourceBuffer.addEventListener('error', () => {
          settleFirst('ok');
          fail();
        });
        queue.length = 0;
        pending = false;
        ended = false;
        // 目标点可能落在「分片起点晚于目标」的缝隙：onUpdateEnd 覆盖判断失败时
        // 定位到 buffered.start（最近可用分片），保证拖拽后必然继续播放
        pendingSeekTarget = target;
        queue.push(initData.slice(0, moovEnd));
        queue.push(dataAll.slice(moofAt));
        pump();
      } catch {
        // S-2 修复：重拉失败 → 全流重建兜底（保证「失败不劣化」承诺成立）
        await rebuildFromStart(target);
      }
    };

    const onSeeked = () => {
      if (failed || seeking) return;
      const target = typeof video.currentTime === 'number' ? video.currentTime : 0;
      if (target <= 0) return;
      if (isBuffered(target)) return;
      // 流已结束（endOfStream 后拖回）→ 整体重建全文件流
      if (ended && mediaSource && mediaSource.readyState !== 'open') {
        seeking = true;
        rebuildFromStart(target)
          .catch(() => { /* 重建失败：保持现状 */ })
          .finally(() => { seeking = false; });
        return;
      }
      seeking = true;
      seekTo(target)
        .catch(() => { /* 重拉失败：保持顺序下载 */ })
        .finally(() => { seeking = false; });
    };

    const rebuildFromStart = async (target: number): Promise<void> => {
      // 全文件重开 + 新 MediaSource，append 覆盖到 target 后定位
      consumeGeneration++;
      if (objectUrl) { try { URL.revokeObjectURL(objectUrl); } catch { /* ignore */ } }
      mediaSource = new MediaSource();
      objectUrl = URL.createObjectURL(mediaSource);
      video.src = objectUrl;
      await waitSourceOpen();
      sourceBuffer = mediaSource.addSourceBuffer(codec);
      sourceBuffer.addEventListener('updateend', onUpdateEnd);
      sourceBuffer.addEventListener('error', () => { settleFirst('ok'); fail(); });
      queue.length = 0;
      pending = false;
      ended = false;
      firstChunkChecked = false;
      pendingSeekTarget = target;
      await openStream(0, -1);
      void consume();
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
      if (objectUrl && video.src === objectUrl) {
        try { video.src = ''; } catch { /* ignore */ }
      }
    }

    return cleanup;
  } catch {
    if (objectUrl) {
      try { URL.revokeObjectURL(objectUrl); } catch { /* ignore */ }
    }
    abortController.abort();
    return null;
  }
}

/** 拼接多个 Uint8Array 为一个（前向 seek 收集流数据用） */
function concatBytes(chunks: Uint8Array[]): Uint8Array {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out;
}

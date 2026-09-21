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
 * 判断一段 MP4 数据是否为可流式初始化段（分片 fMP4：ftyp 后紧跟含 mvex 的 moov）。
 *
 * v1.4.2 hotfix：MSE 边下边播只认分片 fMP4（moov 含 mvex + 后续 moof）。
 * faststart MP4（moov 前置但无 mvex）无法被 MSE 增量 append，必须判为
 * 不可流式并快速失败回退（否则 Chrome 不报错、只无限等待）。
 *   - 首个非 ftyp box 为 moov 且 moov 内含 mvex → 可流式
 *   - 首个非 ftyp box 为 mdat / moov 无 mvex / 无法判定 → 不可流式
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
    if (boxType === 'moov') {
      // moov 前置：仍需内部含 mvex（分片声明）才算可流式
      return hasBoxInRange(data, pos + 8, pos + boxSize, 'mvex');
    }
    if (boxType === 'mdat') return false;
    if (boxSize < 8) break;
    pos += boxSize;
  }
  return false;
}

/** 在 [start, end) 内扫描同层 box，判断是否含指定类型 box */
function hasBoxInRange(data: Uint8Array, start: number, end: number, target: string): boolean {
  let pos = start;
  while (pos + 8 <= end && pos + 8 <= data.length) {
    const boxSize = (data[pos] << 24) | (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3];
    const boxType = String.fromCharCode(
      data[pos + 4], data[pos + 5], data[pos + 6], data[pos + 7],
    );
    if (boxType === target) return true;
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

/** 在 [start, end) 内查找指定类型 box，返回 {start,size} 或 null */
function findBox(data: Uint8Array, start: number, target: string): { start: number; size: number } | null {
  let pos = start;
  while (pos + 8 <= data.length) {
    const size = (data[pos] << 24) | (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3];
    const type = String.fromCharCode(data[pos + 4], data[pos + 5], data[pos + 6], data[pos + 7]);
    if (type === target) return { start: pos, size };
    if (size < 8) break;
    pos += size;
  }
  return null;
}

/** 在 [start, end) 内查找所有指定类型 box */
function findBoxes(data: Uint8Array, start: number, end: number, target: string): { start: number; size: number }[] {
  const out: { start: number; size: number }[] = [];
  let pos = start;
  while (pos + 8 <= end && pos + 8 <= data.length) {
    const size = (data[pos] << 24) | (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3];
    const type = String.fromCharCode(data[pos + 4], data[pos + 5], data[pos + 6], data[pos + 7]);
    if (type === target) out.push({ start: pos, size });
    if (size < 8) break;
    pos += size;
  }
  return out;
}

/** 在字节数组中查找子序列，返回首个匹配下标或 -1 */
function indexOfBytes(data: Uint8Array, needle: number[]): number {
  outer: for (let i = 0; i + needle.length <= data.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (data[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

function hex2(n: number): string {
  return n.toString(16).padStart(2, '0');
}

/** 从 avc1/avc3 entry 提取 H.264 codec 字符串（avc1.xxyyzz） */
function avc1Codec(entry: Uint8Array): string | null {
  const idx = indexOfBytes(entry, [0x61, 0x76, 0x63, 0x43]); // 'avcC'
  if (idx < 0 || idx + 8 > entry.length) return null;
  // avcC payload: version(1)+profile(1)+compat(1)+level(1)
  return `avc1.${hex2(entry[idx + 5])}${hex2(entry[idx + 6])}${hex2(entry[idx + 7])}`;
}

/** 从 av01 entry 提取 AV1 codec 字符串（av01.P.LLT.DD） */
function av01Codec(entry: Uint8Array): string | null {
  const idx = indexOfBytes(entry, [0x61, 0x76, 0x31, 0x43]); // 'av1C'
  if (idx < 0 || idx + 7 > entry.length) return null;
  // av1C payload: marker/version(1) + seq_profile/level(1) + tier/bitdepth(1)
  const profileLevel = entry[idx + 5];
  const tierDepth = entry[idx + 6];
  const profile = (profileLevel >> 5) & 0x07;
  const levelIdx = profileLevel & 0x1f;
  const tier = (tierDepth >> 7) & 0x01;
  const highBitdepth = (tierDepth >> 6) & 0x01;
  const twelveBit = (tierDepth >> 5) & 0x01;
  const major = 2 + (levelIdx >> 2);
  const minor = levelIdx & 0x03;
  const bitdepth = twelveBit ? 12 : highBitdepth ? 10 : 8;
  return `av01.${profile}.${major}${minor}${tier ? 'H' : 'M'}.${String(bitdepth).padStart(2, '0')}`;
}

/** 从 mp4a entry 提取 AAC codec 字符串（服务端修复已统一 AAC-LC，采用 mp4a.40.2） */
function mp4aCodec(): string {
  return 'mp4a.40.2';
}

/**
 * 从 fMP4 init 段解析 codec 字符串（avc1/av01/mp4a，逗号分隔），失败返回 null。
 *
 * v1.4.2 hotfix：MSE 需在 appendBuffer 前用精确 codec 声明 SourceBuffer；
 * 按扩展名猜 codec 会误判 AV1 文件为 h264。此处直接解析 init 段 stsd，
 * 与后端 media_probe.parse_fmp4_codec 语义对齐。
 */
function parseFmp4Codec(init: Uint8Array): string | null {
  const moov = findBox(init, 0, 'moov');
  if (!moov) return null;
  let video: string | null = null;
  let audio: string | null = null;
  const moovEnd = moov.start + moov.size;
  for (const trak of findBoxes(init, moov.start + 8, moovEnd, 'trak')) {
    for (const mdia of findBoxes(init, trak.start + 8, trak.start + trak.size, 'mdia')) {
      for (const minf of findBoxes(init, mdia.start + 8, mdia.start + mdia.size, 'minf')) {
        for (const stbl of findBoxes(init, minf.start + 8, minf.start + minf.size, 'stbl')) {
          for (const stsd of findBoxes(init, stbl.start + 8, stbl.start + stbl.size, 'stsd')) {
            const stsdEnd = stsd.start + stsd.size;
            let ep = stsd.start + 16; // version/flags(4) + entry_count(4)
            while (ep + 8 <= stsdEnd && ep + 8 <= init.length) {
              const esz = (init[ep] << 24) | (init[ep + 1] << 16) | (init[ep + 2] << 8) | init[ep + 3];
              const etype = String.fromCharCode(init[ep + 4], init[ep + 5], init[ep + 6], init[ep + 7]);
              const entry = init.subarray(ep, Math.min(ep + esz, init.length));
              if (etype === 'avc1' || etype === 'avc3') video = avc1Codec(entry);
              else if (etype === 'av01') video = av01Codec(entry);
              else if (etype === 'mp4a') audio = mp4aCodec();
              if (esz < 8) break;
              ep += esz;
            }
          }
        }
      }
    }
  }
  return [video, audio].filter(Boolean).join(', ') || null;
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
  let codec = probeMseCodec(filename) ?? defaultMp4Codec(filename);

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
  // 当前数据帧生成器（openStream 内赋值，恒先于使用）
  let frames!: AsyncGenerator<Uint8Array>;
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

    // v1.4.2 hotfix：预读 fMP4 init 段解析真实 codec（avc1/av01/mp4a），
    // 避免按扩展名猜错（如 AV1 文件误判为 h264 导致 addSourceBuffer 失败）。
    // 解析失败则保持 probeMseCodec 的默认 codec，行为不劣化。
    const initChunks: Uint8Array[] = [];
    let parsedCodec: string | null = null;
    try {
      for await (const c of frames) {
        initChunks.push(c);
        const acc = initChunks.length === 1 ? c : concatBytes(initChunks);
        parsedCodec = parseFmp4Codec(acc);
        if (parsedCodec || acc.length > 4 * 1024 * 1024) break;
      }
    } catch {
      /* init 预读失败：保持默认 codec，回退原逻辑 */
    }
    if (parsedCodec) {
      const isAudioOnly = parsedCodec.split(',').every(p => p.trim().startsWith('mp4a.'));
      codec = isAudioOnly
        ? `audio/mp4; codecs="${parsedCodec}"`
        : `video/mp4; codecs="${parsedCodec}"`;
    }
    // 将预读的 init 段回填到流头，consume 循环无需感知预读
    if (initChunks.length > 0) {
      const rest = frames;
      frames = (async function* () {
        for (const c of initChunks) yield c;
        for await (const c of rest) yield c;
      })();
    }

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
    // v1.4.2 hotfix：大文件边下边播——SourceBuffer 缓冲超过内存配额时
    // appendBuffer 抛 QuotaExceededError。此时暂停 append，等播放推进后
    // 移除已播放数据再继续（否则大文件直接 fail，表现为「只能播后半段/损坏」）
    let quotaPaused = false;

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

    /** 缓冲超配额后的恢复：移除播放点之前的数据（保留 30s 前置缓冲）后继续 append */
    const tryEvictAndResume = () => {
      if (!quotaPaused || failed) return;
      const cur = typeof video.currentTime === 'number' ? video.currentTime : 0;
      try {
        if (cur > 30 && sourceBuffer.buffered.length > 0) {
          const start = sourceBuffer.buffered.start(0);
          // 仅当可移除范围 >10s 才执行，避免 no-op remove 触发 updateend 忙循环
          if (cur - 30 - start > 10) {
            sourceBuffer.remove(start, cur - 30);
            // remove 也触发 updateend → onUpdateEnd 里继续 pump
            quotaPaused = false;
            return;
          }
        }
      } catch { /* ignore */ }
      // 无法释放足够空间：等待播放推进（timeupdate 会再次触发重试）
      setTimeout(() => {
        if (quotaPaused && !failed) tryEvictAndResume();
      }, 1000);
    };

    const onTimeUpdate = () => {
      if (quotaPaused) tryEvictAndResume();
    };

    const pump = () => {
      if (pending || queue.length === 0 || failed || quotaPaused) return;
      pending = true;
      const data = queue.shift()!;
      try {
        sourceBuffer.appendBuffer(data as unknown as BufferSource);
      } catch (e) {
        const name = e instanceof DOMException
          ? e.name
          : (e as { name?: string } | null | undefined)?.name;
        if (name === 'QuotaExceededError') {
          // 缓冲超配额：放回数据，暂停 append，等播放推进后清理已播放数据
          queue.unshift(data);
          pending = false;
          quotaPaused = true;
          tryEvictAndResume();
          return;
        }
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
      if (quotaPaused) {
        tryEvictAndResume();
        return;
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
    video.addEventListener('timeupdate', onTimeUpdate);

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
          // 缓冲超配额暂停时：暂停消费（形成网络背压，避免 queue 无限增长），
          // 播放推进后 quotaPaused 解除，恢复消费
          while (quotaPaused && !failed && gen === consumeGeneration) {
            await new Promise(r => setTimeout(r, 200));
          }
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
        let moofAt = -1;
        let collected = 0;
        for await (const c of data.frames) {
          dataBytes.push(c);
          collected += c.length;
          const acc = concatBytes(dataBytes);
          moofAt = findMoofStart(acc);
          if (moofAt >= 0) break; // 找到分片边界即停
          if (collected > 8 * 1024 * 1024) break; // 8MB 内找不到分片边界即放弃
        }
        if (moofAt < 0) {
          await rebuildFromStart(target);
          return;
        }
        const dataAll = concatBytes(dataBytes);

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
        quotaPaused = false;
        // 目标点可能落在「分片起点晚于目标」的缝隙：onUpdateEnd 覆盖判断失败时
        // 定位到 buffered.start（最近可用分片），保证拖拽后必然继续播放
        pendingSeekTarget = target;
        queue.push(initData.slice(0, moovEnd));
        queue.push(dataAll.slice(moofAt));
        pump();

        // 4. 继续消费估算偏移处的剩余流数据（seek 后播放可持续，不因 8MB 截断卡停）
        const rest = data.frames;
        frames = (async function* () {
          for await (const c of rest) yield c;
        })();
        firstChunkChecked = true; // init 段已单独 append，跳过 fmp4 init 检查
        void consume();
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
      quotaPaused = false;
      firstChunkChecked = false;
      pendingSeekTarget = target;
      await openStream(0, -1);
      void consume();
    };
    video.addEventListener('seeked', onSeeked);
    seekCleanup = () => {
      try { video.removeEventListener('seeked', onSeeked); } catch { /* ignore */ }
      try { video.removeEventListener('timeupdate', onTimeUpdate); } catch { /* ignore */ }
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
  } catch (e) {
    if (objectUrl) {
      try { URL.revokeObjectURL(objectUrl); } catch { /* ignore */ }
    }
    abortController.abort();
    // v1.4.2 hotfix：服务端 415 [MEDIA_NEEDS_REPAIR]（不可流式/损坏）需向上
    // 传播，由页面显示「立即修复」引导；其余错误（容器不支持等）回退完整下载。
    if (e instanceof Error && e.message.includes('[MEDIA_NEEDS_REPAIR]')) {
      throw e;
    }
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

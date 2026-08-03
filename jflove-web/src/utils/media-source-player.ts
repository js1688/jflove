/**
 * Media Source Extensions 流式播放器
 *
 * 替代 Service Worker 方案，直接在页面中 fetch 加密流 → 解密 → append 到
 * MediaSource SourceBuffer → video/audio 边下边播。
 * MSE 不需要 HTTPS / 安全上下文，96.4% 浏览器支持。
 */
import { getSessionKey, getSessionId, getToken, getServerUrl } from './session';
import { encryptEnvelope, decryptStreamChunk } from './crypto';
import { resyncSession } from './http-client';

/** 常见 mp4 视频 codec 候选（按优先级降序探测） */
const MP4_VIDEO_CODECS = [
  'video/mp4; codecs="avc1.42E01E, mp4a.40.2"',   // H.264 Baseline + AAC-LC
  'video/mp4; codecs="avc1.64001E, mp4a.40.2"',    // H.264 High + AAC-LC
  'video/mp4; codecs="avc1.4D401E, mp4a.40.2"',    // H.264 Main + AAC-LC
  'video/mp4; codecs="avc1.64001F, mp4a.40.2"',    // H.264 High 3.1 + AAC-LC
];

/** mp4 音频 codec 候选 */
const MP4_AUDIO_CODECS = [
  'audio/mp4; codecs="mp4a.40.2"',   // AAC-LC
  'audio/mp4; codecs="mp4a.40.5"',   // HE-AAC
  'audio/mp4; codecs="mp4a.40.29"',  // HE-AAC v2
];

// ── 帧协议常量（与后端 v2 帧协议一致） ──
const FRAME_HEADER_LEN = 4;

/** 检测浏览器是否支持 MSE */
export function isMSESupported(): boolean {
  return (
    typeof MediaSource !== 'undefined'
    && typeof MediaSource.isTypeSupported === 'function'
  );
}

/** 探测可用的 mp4 codec */
function probeCodec(isAudio: boolean): string | null {
  const candidates = isAudio ? MP4_AUDIO_CODECS : MP4_VIDEO_CODECS;
  for (const c of candidates) {
    if (MediaSource.isTypeSupported(c)) return c;
  }
  return null;
}

/** 从加密帧流中读取一帧（4B 大端长度 + body） */
function readFrame(buffer: Uint8Array): { body: Uint8Array; rest: Uint8Array } | null {
  if (buffer.length < FRAME_HEADER_LEN) return null;
  const frameLen = new DataView(buffer.buffer, buffer.byteOffset, FRAME_HEADER_LEN).getUint32(0, false);
  if (buffer.length < FRAME_HEADER_LEN + frameLen) return null;
  const body = buffer.subarray(FRAME_HEADER_LEN, FRAME_HEADER_LEN + frameLen);
  const rest = buffer.subarray(FRAME_HEADER_LEN + frameLen);
  return { body, rest };
}

/** 连接两个 Uint8Array */
function concat(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a);
  out.set(b, a.length);
  return out;
}

/**
 * 获取后端加密流并逐帧解密 → ReadableStream<Uint8Array>
 */
async function fetchDecryptedStream(
  diskId: number,
  path: string,
  filename: string,
  rangeStart: number,
): Promise<{
  reader: ReadableStreamDefaultReader<Uint8Array>;
  meta: Record<string, unknown>;
  abort: () => void;
} | null> {
  let sessionKey = getSessionKey();
  let sessionId = getSessionId();
  const token = getToken();
  const serverUrl = getServerUrl();

  // 免登录恢复后 session_key 不持久化，需先等待密钥交换完成（http-client 单飞锁）
  if (!sessionKey || !sessionId) {
    console.error('[MSE] session 缺失，等待 resyncSession...');
    try {
      await resyncSession();
    } catch (e) {
      console.error('[MSE] resyncSession 失败:', e);
      return null;
    }
    sessionKey = getSessionKey();
    sessionId = getSessionId();
  }
  if (!sessionKey || !sessionId || !token) {
    console.error('[MSE] session 仍不完整: sk='+!!sessionKey+' sid='+!!sessionId+' tok='+!!token);
    return null;
  }

  const payload = JSON.stringify({
    token,
    disk_id: diskId,
    path,
    filename,
    range_start: rangeStart,
    range_end: -1,
  });
  const { nonce, ciphertext } = encryptEnvelope(
    sessionKey,
    new TextEncoder().encode(payload),
  );
  const query = new URLSearchParams({ nonce, ciphertext }).toString();

  const controller = new AbortController();
  const fetchUrl = `${serverUrl}/api/v1/files/stream?${query}`;
  console.error('[MSE] fetch stream:', fetchUrl.slice(0, 80) + '...');
  const resp = await fetch(fetchUrl, {
    headers: { 'X-Session-ID': sessionId },
    signal: controller.signal,
  });
  if (!resp.ok || !resp.body) {
    console.error('[MSE] stream fetch 失败: status=' + resp.status);
    return null;
  }

  const reader = resp.body.getReader();
  // 读首帧 meta
  let buffer: Uint8Array = new Uint8Array(0);
  let meta: Record<string, unknown> | null = null;

  while (meta === null) {
    const { done, value } = await reader.read();
    if (done) return null;
    buffer = concat(buffer, value);
    const frame = readFrame(buffer);
    if (!frame) continue;
    try {
      const plaintext = decryptStreamChunk(sessionKey, frame.body);
      meta = JSON.parse(new TextDecoder().decode(plaintext)) as Record<string, unknown>;
      buffer = frame.rest;
    } catch {
      // 不是 meta 帧（可能是加密 payload 帧），继续读取
      buffer = frame.rest;
      continue;
    }
  }

  if (!meta || typeof meta.file_size !== 'number') {
    reader.releaseLock();
    return null;
  }

  // 创建解密流：将剩余 buffer（已读取但未解密的 meta 帧后面的数据）+ 后续 reader 数据
  const decryptedStream = new ReadableStream<Uint8Array>({
    start(ctrl) {
      // 处理 buffer 中的剩余帧
      processBuffer(buffer, sessionKey, reader, ctrl);
    },
    cancel() {
      reader.cancel();
    },
  });

  return {
    reader: decryptedStream.getReader(),
    meta,
    abort: () => controller.abort(),
  };
}

/** 递归处理加密帧缓冲并推入解密数据到流 */
async function processBuffer(
  buffer: Uint8Array,
  sessionKey: Uint8Array,
  upstreamReader: ReadableStreamDefaultReader<Uint8Array>,
  controller: ReadableStreamDefaultController<Uint8Array>,
): Promise<void> {
  let buf = buffer;
  try {
    while (true) {
      // 尽量从上游读取更多数据
      if (buf.length < FRAME_HEADER_LEN + 28) { // 最小帧 = 4 + 12 + 1 + 16 = 33，取 32 作为阈值
        try {
          const { done, value } = await upstreamReader.read();
          if (done) {
            controller.close();
            return;
          }
          buf = concat(buf, value);
        } catch {
          controller.close();
          return;
        }
      }
      const frame = readFrame(buf);
      if (!frame) continue; // buffer 不足，继续读取
      try {
        const plaintext = decryptStreamChunk(sessionKey, frame.body);
        controller.enqueue(plaintext);
      } catch {
        // 解密失败，跳过该帧
      }
      buf = frame.rest;
    }
  } catch {
    controller.close();
  }
}

/**
 * 使用 MSE 播放视频/音频（媒体文件二进制数据）
 * @returns 清理函数，调用后释放资源
 */
export async function playWithMSE(
  video: HTMLVideoElement | HTMLAudioElement,
  diskId: number,
  path: string,
  filename: string,
): Promise<(() => void) | null> {
  if (!isMSESupported()) { console.error('[MSE] MediaSource 不支持'); return null; }

  const ext = (filename.split('.').pop() || '').toLowerCase();
  const isAudio = ['mp3', 'aac', 'm4a', 'ogg', 'wav', 'flac', 'opus', 'wma'].includes(ext);
  const mimeCodec = probeCodec(isAudio);
  if (!mimeCodec) { console.error('[MSE] 无可用 codec'); return null; }
  console.error('[MSE] codec=' + mimeCodec + ' file=' + filename);

  const stream = await fetchDecryptedStream(diskId, path, filename, 0);
  if (!stream) { console.error('[MSE] fetchDecryptedStream 返回 null'); return null; }
  console.error('[MSE] 流已建立, file_size=' + stream.meta.file_size);

  const mediaSource = new MediaSource();
  const objectUrl = URL.createObjectURL(mediaSource);
  video.src = objectUrl;

  // sourceopen 等待
  console.error('[MSE] 等待 sourceopen...');
  await new Promise<void>((resolve, reject) => {
    const onOpen = () => {
      mediaSource.removeEventListener('sourceopen', onOpen);
      console.error('[MSE] sourceopen 触发');
      resolve();
    };
    mediaSource.addEventListener('sourceopen', onOpen);
    // 超时保护
    setTimeout(() => {
      mediaSource.removeEventListener('sourceopen', onOpen);
      reject(new Error('MediaSource sourceopen 超时'));
    }, 10000);
  });

  let sourceBuffer: SourceBuffer;
  try {
    sourceBuffer = mediaSource.addSourceBuffer(mimeCodec);
  } catch {
    stream.abort();
    URL.revokeObjectURL(objectUrl);
    return null;
  }

  const appendQueue: Uint8Array[] = [];
  let pendingAppend = false;
  let ended = false;

  // 递归处理 append 队列
  function processQueue(): void {
    if (pendingAppend || appendQueue.length === 0) return;
    pendingAppend = true;
    const data = appendQueue.shift()!;
    try {
      sourceBuffer.appendBuffer(data as BufferSource);
    } catch {
      // append 失败（可能 codec 不匹配），回退
      stream?.abort();
      cleanup();
    }
  }

  sourceBuffer.addEventListener('updateend', () => {
    pendingAppend = false;
    if (appendQueue.length > 0) {
      processQueue();
    } else if (ended) {
      try { mediaSource.endOfStream(); } catch { /* ignore */ }
    }
  });

  sourceBuffer.addEventListener('error', () => {
    stream.abort();
    cleanup();
  });

  // 持续读取解密流并加入 append 队列
  (async () => {
    try {
      while (true) {
        const { done, value } = await stream.reader.read();
        if (done) {
          ended = true;
          if (!pendingAppend && appendQueue.length === 0) {
            try { mediaSource.endOfStream(); } catch { /* ignore */ }
          }
          return;
        }
        appendQueue.push(value);
        processQueue();
      }
    } catch {
      cleanup();
    }
  })();

  function cleanup() {
    try { stream?.abort(); } catch { /* ignore */ }
    try { stream?.reader.cancel(); } catch { /* ignore */ }
    try { URL.revokeObjectURL(objectUrl); } catch { /* ignore */ }
    if (mediaSource.readyState === 'open') {
      try { mediaSource.endOfStream(); } catch { /* ignore */ }
    }
    // 移除 sourceBuffer
    try {
      if (sourceBuffer && mediaSource.sourceBuffers.length > 0) {
        mediaSource.removeSourceBuffer(sourceBuffer);
      }
    } catch { /* ignore */ }
    video.src = '';
  }

  return cleanup;
}

/** 检测 MSE 是否对当前文件类型可用 */
export function isMSEAvailable(filename: string): boolean {
  if (!isMSESupported()) return false;
  const ext = (filename.split('.').pop() || '').toLowerCase();
  const isAudio = ['mp3', 'aac', 'm4a', 'ogg', 'wav', 'flac', 'opus', 'wma'].includes(ext);
  const isVideo = ['mp4', 'mov', 'm4v', '3gp'].includes(ext);
  if (!isAudio && !isVideo) return false;
  return probeCodec(isAudio) !== null;
}

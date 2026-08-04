/**
 * 流式帧解析器
 *
 * 对标桌面端 parse_stream_frame 和移动端 stream_frame.dart。
 * 从加密流式响应中逐帧解密。
 *
 * 帧格式：[4B 大端长度][12B nonce][密文+16B Poly1305 tag]
 *
 * 同时提供：
 *  - parseRangeHeader：解析 HTTP Range 请求头（供 SW 流式代理 / MSE 使用）
 *  - openEncryptedStream：向后端 /api/v1/files/stream 打开加密 Range 流，
 *    读取首帧 meta 后返回逐帧解密的明文生成器（页面 MSE 与 Service Worker 共用；
 *    Service Worker 无法访问 localStorage，故会话参数全部显式传入）。
 */

import { decryptStreamChunk, encryptEnvelope } from './crypto';

/** 流式会话参数（SW 场景下由页面 postMessage 传入） */
export interface StreamSessionParams {
  sessionKey: Uint8Array;
  sessionId: string;
  token: string;
  serverUrl: string;
}

/** /api/v1/files/stream 首帧元数据 */
export interface StreamMeta {
  type: string;
  file_size: number;
  range_start: number;
  range_end: number;
  content_type: string;
}

/**
 * 解析 HTTP Range 请求头，返回 (start, endExclusive)。
 *
 * 支持 RFC 7233 三种格式：
 *   bytes=X-Y  → (X, Y+1)            绝对范围
 *   bytes=X-   → (X, fileSize)       从 X 到结尾
 *   bytes=-N   → (fileSize-N, fileSize)  最后 N 字节（suffix range）
 * 无 / 非法 Range 时返回整个文件范围。
 *
 * 返回的 start / end 均钳制到 [0, fileSize]，保证 Content-Length 与后端一致。
 */
export function parseRangeHeader(
  raw: string | null,
  fileSize: number,
): { start: number; end: number } {
  let start = 0;
  let end = fileSize;
  if (raw && raw.startsWith('bytes=')) {
    const parts = raw.slice('bytes='.length).split('-', 2);
    try {
      if (!parts[0]) {
        // Suffix range: bytes=-N，表示文件最后 N 字节
        const suffix = parseInt(parts[1] || '', 10);
        if (!Number.isNaN(suffix) && suffix > 0) {
          start = Math.max(0, fileSize - suffix);
          end = fileSize;
        }
      } else {
        const parsedStart = parseInt(parts[0], 10);
        if (!Number.isNaN(parsedStart)) {
          start = parsedStart;
          end = parts[1] ? parseInt(parts[1], 10) + 1 : fileSize;
          if (Number.isNaN(end)) end = fileSize;
        }
      }
    } catch {
      /* 非法 Range，回退到整个文件 */
    }
  }
  // 钳制到文件实际范围
  start = Math.max(0, Math.min(start, fileSize));
  end = Math.max(start, Math.min(end, fileSize));
  return { start, end };
}

/**
 * 读取加密流并解析出首帧（meta 帧），返回其明文与剩余字节缓冲。
 *
 * 该函数会消费掉首帧；剩余缓冲与后续 reader 数据交给 parseStreamFrames 继续解析数据帧。
 */
async function readMetaFrame(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  sessionKey: Uint8Array,
): Promise<{ meta: StreamMeta; restBuffer: Uint8Array }> {
  let buffer: Uint8Array = new Uint8Array(0);

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      throw new Error('流在 meta 帧前结束');
    }
    buffer = concatBytes(buffer, value);

    // 尝试从缓冲中取出首帧（4B 大端长度 + body）
    if (buffer.length < 4) continue;
    const view = new DataView(buffer.buffer, buffer.byteOffset, 4);
    const frameLen = view.getUint32(0, false);
    const totalNeeded = 4 + frameLen;
    if (buffer.length < totalNeeded) continue;

    const frameBody = buffer.slice(4, totalNeeded);
    let plaintext: Uint8Array;
    try {
      plaintext = decryptStreamChunk(sessionKey, frameBody);
    } catch (e) {
      throw new Error(`meta 帧解密失败：${e instanceof Error ? e.message : String(e)}`);
    }

    const meta = JSON.parse(new TextDecoder().decode(plaintext)) as StreamMeta;
    if (meta.type === 'error') {
      throw new Error(meta.content_type || '服务端流式返回错误');
    }
    return { meta, restBuffer: buffer.slice(totalNeeded) };
  }
}

/** 拼接两个 Uint8Array（避免污染原缓冲） */
function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/**
 * 向后端 /api/v1/files/stream 打开加密 Range 流（v2 帧协议）。
 *
 * 帧 0 为 meta 元数据帧（file_size / content_type / range 等），本函数读取并返回；
 * 随后返回的数据帧生成器逐帧解密产出明文（不含 meta 帧）。
 *
 * @param session 加密会话（sessionKey / sessionId / token / serverUrl）
 * @param diskId  虚拟磁盘 ID
 * @param path    文件所在目录（磁盘内相对路径）
 * @param filename 文件名
 * @param rangeStart 字节起点（0 = 开头；负数 = 从末尾倒数）
 * @param rangeEnd   字节终点，不含（-1 = 文件结尾）
 * @param signal   可选 AbortSignal（页面侧可在卸载时中止）
 */
export async function openEncryptedStream(
  session: StreamSessionParams,
  diskId: number,
  path: string,
  filename: string,
  rangeStart: number,
  rangeEnd: number,
  signal?: AbortSignal,
): Promise<{ meta: StreamMeta; frames: AsyncGenerator<Uint8Array> }> {
  // /stream 接口约定：path=文件所在目录、filename=文件名（桌面/移动端一致）。
  // Web 端 FileItem.path 是「完整路径（含文件名）」，这里归一化为目录：
  // 若 path 以 /filename 结尾则截掉文件名，避免后端 os.path.join(path, filename)
  // 双重拼接导致「文件不存在」404。两种传法（目录 / 完整路径）均兼容。
  const dirPath = filename && path.endsWith(`/${filename}`)
    ? path.slice(0, path.length - filename.length - 1)
    : path;

  // 加密 query 信封（与 http-client 的 getQueryEncrypted 同款：URL 不泄露业务明文）
  const payload = JSON.stringify({
    token: session.token,
    disk_id: diskId,
    path: dirPath,
    filename,
    range_start: rangeStart,
    range_end: rangeEnd,
  });
  const { nonce, ciphertext } = encryptEnvelope(
    session.sessionKey,
    new TextEncoder().encode(payload),
  );
  const query = new URLSearchParams({ nonce, ciphertext }).toString();
  const url = `${session.serverUrl}/api/v1/files/stream?${query}`;

  const controller = new AbortController();
  // 外部 signal 中止时级联中止后端请求
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  const resp = await fetch(url, {
    method: 'GET',
    headers: { 'X-Session-ID': session.sessionId },
    signal: controller.signal,
  });
  if (!resp.ok) {
    throw new Error(`流式请求失败：HTTP ${resp.status}`);
  }
  if (!resp.body) {
    throw new Error('流式响应体为空');
  }

  const reader = resp.body.getReader();
  const { meta, restBuffer } = await readMetaFrame(reader, session.sessionKey);

  // 数据帧生成器：复用 parseStreamFrames（initialBuffer 为 meta 帧后的剩余字节）
  const frames = parseStreamFrames(reader, session.sessionKey, restBuffer);
  return { meta, frames };
}

/**
 * 解析加密流式帧的异步生成器。
 *
 * 用法：
 *   for await (const chunk of parseStreamFrames(reader, sessionKey)) {
 *     // chunk 是解密后的明文 Uint8Array
 *   }
 *
 * initialBuffer：可选，已读但未解析的缓冲（Service Worker 先读首帧 meta 后
 * 把剩余字节传入，继续解析数据帧）。
 */
export async function* parseStreamFrames(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  sessionKey: Uint8Array,
  initialBuffer: Uint8Array = new Uint8Array(0),
): AsyncGenerator<Uint8Array> {
  let buffer = initialBuffer;

  while (true) {
    // 先解析缓冲中已有的完整帧。
    // 关键：readMetaFrame 可能把 meta 帧与部分/全部数据帧放在同一个网络分片，
    // initialBuffer 就是读 meta 后的剩余字节——若先 read() 再解析，单分片流会遇到
    // done 而丢弃剩余数据帧（「流结束但缓冲区仍有未解析数据」）。故先解析再读取。
    while (buffer.length >= 4) {
      // 读取 4 字节大端长度
      const view = new DataView(buffer.buffer, buffer.byteOffset, 4);
      const frameLength = view.getUint32(0, false);

      if (frameLength < 28) {
        // 最小帧：12B nonce + 16B tag（空明文），帧头 4B
        throw new Error(`帧长度异常：${frameLength}`);
      }

      const totalNeeded = 4 + frameLength;
      if (buffer.length < totalNeeded) {
        break; // 帧不完整，等待更多数据
      }

      const frameBody = buffer.slice(4, totalNeeded);

      try {
        const plaintext = decryptStreamChunk(sessionKey, frameBody);
        yield plaintext;
      } catch (e) {
        throw new Error(`帧解密失败：${e instanceof Error ? e.message : String(e)}`);
      }

      buffer = buffer.slice(totalNeeded);
    }

    const { done, value } = await reader.read();
    if (done) {
      if (buffer.length > 0) {
        throw new Error('流结束但缓冲区仍有未解析数据');
      }
      return;
    }

    // 追加到缓冲区
    const newBuffer = new Uint8Array(buffer.length + value.length);
    newBuffer.set(buffer);
    newBuffer.set(value, buffer.length);
    buffer = newBuffer;
  }
}

/**
 * 从 ReadableStream 创建 reader 并解析帧。
 * 便捷封装，自动创建 reader。
 */
export async function* decryptStream(
  stream: ReadableStream<Uint8Array>,
  sessionKey: Uint8Array,
): AsyncGenerator<Uint8Array> {
  const reader = stream.getReader();
  try {
    yield* parseStreamFrames(reader, sessionKey);
  } finally {
    reader.releaseLock();
  }
}

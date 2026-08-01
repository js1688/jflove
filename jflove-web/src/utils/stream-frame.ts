/**
 * 流式帧解析器
 *
 * 对标桌面端 parse_stream_frame 和移动端 stream_frame.dart。
 * 从加密流式响应中逐帧解密。
 *
 * 帧格式：[4B 大端长度][12B nonce][密文+16B Poly1305 tag]
 */

import { decryptStreamChunk } from './crypto';

/**
 * 解析加密流式帧的异步生成器。
 *
 * 用法：
 *   for await (const chunk of parseStreamFrames(reader, sessionKey)) {
 *     // chunk 是解密后的明文 Uint8Array
 *   }
 */
export async function* parseStreamFrames(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  sessionKey: Uint8Array,
): AsyncGenerator<Uint8Array> {
  let buffer = new Uint8Array(0);

  while (true) {
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

    // 解析所有完整帧
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

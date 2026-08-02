/**
 * 流式帧端到端测试
 *
 * 对应安全宪法 §9.6 testing 第③类：文件下载流可被客户端正确解密、篡改后认证失败。
 */

import { describe, it, expect } from 'vitest';
import {
  generateKeyPair, deriveSessionKey,
  encryptStreamChunk, decryptStreamChunk,
} from '../../src/utils/crypto';
import { decryptStream, parseStreamFrames } from '../../src/utils/stream-frame';

/** 生成测试用 session_key */
async function makeSessionKey(): Promise<Uint8Array> {
  const alice = await generateKeyPair();
  const bob = await generateKeyPair();
  return deriveSessionKey(alice.privateKey, bob.publicKeyB64);
}

/** 将字节数组拼接为单个 Uint8Array */
function concat(chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((sum, c) => sum + c.length, 0);
  const result = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    result.set(c, offset);
    offset += c.length;
  }
  return result;
}

/** 构造一个按帧加密的 ReadableStream */
function makeEncryptedStream(
  sessionKey: Uint8Array,
  plaintexts: Uint8Array[],
  chunkSize?: number,
): ReadableStream<Uint8Array> {
  const frames = plaintexts.map(p => encryptStreamChunk(sessionKey, p));
  const all = concat(frames);

  return new ReadableStream<Uint8Array>({
    start(controller) {
      if (chunkSize) {
        // 模拟网络分片：按 chunkSize 切分发送
        for (let i = 0; i < all.length; i += chunkSize) {
          controller.enqueue(all.slice(i, i + chunkSize));
        }
      } else {
        controller.enqueue(all);
      }
      controller.close();
    },
  });
}

describe('stream-frame 流式帧端到端', () => {
  it('多帧加密 → 解密 → 内容逐字节一致', async () => {
    const sessionKey = await makeSessionKey();
    const plaintexts = [
      new TextEncoder().encode('第一帧中文内容'),
      new TextEncoder().encode('second frame'),
      new Uint8Array([0, 1, 2, 255, 254, 253]), // 二进制
    ];

    const stream = makeEncryptedStream(sessionKey, plaintexts);
    const result: Uint8Array[] = [];

    for await (const chunk of decryptStream(stream, sessionKey)) {
      result.push(chunk);
    }

    expect(result.length).toBe(3);
    expect(new TextDecoder().decode(result[0])).toBe('第一帧中文内容');
    expect(new TextDecoder().decode(result[1])).toBe('second frame');
    expect(result[2]).toEqual(new Uint8Array([0, 1, 2, 255, 254, 253]));
  });

  it('网络分片（跨帧边界拆包）仍能正确解析', async () => {
    const sessionKey = await makeSessionKey();
    const plaintexts = [
      new TextEncoder().encode('A'.repeat(1024)),
      new TextEncoder().encode('B'.repeat(2048)),
      new TextEncoder().encode('C'.repeat(512)),
    ];

    // 用 100 字节的小片模拟网络分包，跨越帧边界
    const stream = makeEncryptedStream(sessionKey, plaintexts, 100);
    const result: Uint8Array[] = [];

    for await (const chunk of decryptStream(stream, sessionKey)) {
      result.push(chunk);
    }

    expect(result.length).toBe(3);
    expect(new TextDecoder().decode(result[0])).toBe('A'.repeat(1024));
    expect(new TextDecoder().decode(result[1])).toBe('B'.repeat(2048));
    expect(new TextDecoder().decode(result[2])).toBe('C'.repeat(512));
  });

  it('篡改密文帧 → 解密认证失败', async () => {
    const sessionKey = await makeSessionKey();
    const frame = encryptStreamChunk(sessionKey, new TextEncoder().encode('secret-data'));

    // 篡改密文（修改最后一个字节）
    const tampered = new Uint8Array(frame);
    tampered[tampered.length - 1] ^= 0x01;

    // 单独解密篡改帧应抛错（Poly1305 tag 校验失败）
    const frameBody = tampered.slice(4);
    expect(() => decryptStreamChunk(sessionKey, frameBody)).toThrow();
  });

  it('使用错误 session_key 解析流 → 抛解密失败', async () => {
    const sessionKey = await makeSessionKey();
    const wrongKey = await makeSessionKey();
    const plaintexts = [new TextEncoder().encode('hello')];

    const stream = makeEncryptedStream(sessionKey, plaintexts);
    const reader = stream.getReader();

    let threw = false;
    try {
      for await (const _c of parseStreamFrames(reader, wrongKey)) {
        void _c;
      }
    } catch {
      threw = true;
    }
    expect(threw).toBe(true);
  });

  it('空明文帧（长度 28B 边界）可正常解析', async () => {
    const sessionKey = await makeSessionKey();
    const emptyPlain = new Uint8Array(0);

    const frame = encryptStreamChunk(sessionKey, emptyPlain);
    // 空明文帧：4B 头 + 12B nonce + 16B tag = 32B
    expect(frame.length).toBe(32);

    const frameBody = frame.slice(4);
    const decrypted = decryptStreamChunk(sessionKey, frameBody);
    expect(decrypted.length).toBe(0);
  });
});

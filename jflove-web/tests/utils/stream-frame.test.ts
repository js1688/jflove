/**
 * 流式帧端到端测试
 *
 * 对应安全宪法 §9.6 testing 第③类：文件下载流可被客户端正确解密、篡改后认证失败。
 */

import { describe, it, expect, vi } from 'vitest';
import {
  generateKeyPair, deriveSessionKey,
  encryptStreamChunk, decryptStreamChunk, decryptEnvelope,
} from '../../src/utils/crypto';
import {
  decryptStream, parseStreamFrames,
  parseRangeHeader, openEncryptedStream,
} from '../../src/utils/stream-frame';

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

describe('parseRangeHeader', () => {
  it('bytes=X-Y 绝对范围', () => {
    expect(parseRangeHeader('bytes=10-99', 100)).toEqual({ start: 10, end: 100 });
  });

  it('bytes=X- 到文件结尾', () => {
    expect(parseRangeHeader('bytes=50-', 100)).toEqual({ start: 50, end: 100 });
  });

  it('bytes=-N suffix 范围（最后 N 字节）', () => {
    expect(parseRangeHeader('bytes=-10', 100)).toEqual({ start: 90, end: 100 });
    // suffix 超过文件大小时钳制到 0
    expect(parseRangeHeader('bytes=-200', 100)).toEqual({ start: 0, end: 100 });
  });

  it('无 Range / 非法 Range 返回整个文件', () => {
    expect(parseRangeHeader(null, 100)).toEqual({ start: 0, end: 100 });
    expect(parseRangeHeader('', 100)).toEqual({ start: 0, end: 100 });
    expect(parseRangeHeader('garbage', 100)).toEqual({ start: 0, end: 100 });
  });

  it('终点/起点超界时钳制到文件大小（保证 Content-Length 一致）', () => {
    expect(parseRangeHeader('bytes=0-199', 100)).toEqual({ start: 0, end: 100 });
    // 起点超出文件大小 → 空范围（触发 SW 侧 416）
    expect(parseRangeHeader('bytes=200-', 100)).toEqual({ start: 100, end: 100 });
  });
});

describe('openEncryptedStream', () => {
  it('读取 meta 帧并逐帧解密数据帧', async () => {
    const sessionKey = await makeSessionKey();
    const metaPlain = new TextEncoder().encode(JSON.stringify({
      type: 'meta',
      file_size: 100,
      range_start: 0,
      range_end: 100,
      content_type: 'video/mp4',
    }));
    const data1 = new TextEncoder().encode('hello ');
    const data2 = new TextEncoder().encode('world');
    const stream = makeEncryptedStream(sessionKey, [metaPlain, data1, data2], 7);

    const originalFetch = globalThis.fetch;
    // 测试桩：返回简化 Response 对象（含 ReadableStream body）
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      body: stream,
    })) as unknown as typeof fetch;

    try {
      const session = {
        sessionKey,
        sessionId: 'test-sid',
        token: 'test-token',
        serverUrl: 'http://localhost:8989',
      };
      const { meta, frames } = await openEncryptedStream(
        session, 1, '/', 'sample.mp4', 0, -1,
      );

      // meta 帧解析正确
      expect(meta.file_size).toBe(100);
      expect(meta.content_type).toBe('video/mp4');
      expect(meta.range_start).toBe(0);
      expect(meta.range_end).toBe(100);

      // 数据帧（不含 meta 帧）逐帧解密拼接
      const chunks: Uint8Array[] = [];
      for await (const c of frames) chunks.push(c);
      const all = concat(chunks);
      expect(new TextDecoder().decode(all)).toBe('hello world');
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe('openEncryptedStream 路径归一化（回归：修复 /stream 404「文件不存在」）', () => {
  it('传入「完整路径（含文件名）」时，后端收到 path=目录 + filename=文件名', async () => {
    const sessionKey = await makeSessionKey();
    const metaPlain = new TextEncoder().encode(JSON.stringify({
      type: 'meta',
      file_size: 100,
      range_start: 0,
      range_end: 100,
      content_type: 'video/mp4',
    }));
    const data = new TextEncoder().encode('payload');
    const stream = makeEncryptedStream(sessionKey, [metaPlain, data]);

    let capturedUrl = '';
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (url: unknown) => {
      capturedUrl = String(url);
      return { ok: true, status: 200, body: stream };
    }) as unknown as typeof fetch;

    try {
      const session = {
        sessionKey,
        sessionId: 'test-sid',
        token: 'test-token',
        serverUrl: 'http://localhost:8989',
      };
      // Web 端 FileItem.path 是完整路径（如 /叶方/IMG_0443.MP4），filename 是文件名
      const { frames } = await openEncryptedStream(
        session, 2, '/叶方/IMG_0443.MP4', 'IMG_0443.MP4', 0, -1,
      );
      // 消费帧，确保请求已发出
      for await (const _ of frames) { void _; }

      // 从请求 URL 解密 query 信封，验证 path 已归一化为目录
      const url = new URL(capturedUrl);
      const nonce = url.searchParams.get('nonce') || '';
      const ciphertext = url.searchParams.get('ciphertext') || '';
      const decrypted = decryptEnvelope(sessionKey, nonce, ciphertext);
      const body = JSON.parse(new TextDecoder().decode(decrypted)) as Record<string, unknown>;

      expect(body.path).toBe('/叶方');
      expect(body.filename).toBe('IMG_0443.MP4');
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('传入「目录 + 文件名」时保持不变（兼容桌面/移动端传法）', async () => {
    const sessionKey = await makeSessionKey();
    const metaPlain = new TextEncoder().encode(JSON.stringify({
      type: 'meta', file_size: 100, range_start: 0, range_end: 100, content_type: 'video/mp4',
    }));
    const stream = makeEncryptedStream(sessionKey, [metaPlain]);

    let capturedUrl = '';
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (url: unknown) => {
      capturedUrl = String(url);
      return { ok: true, status: 200, body: stream };
    }) as unknown as typeof fetch;

    try {
      const session = {
        sessionKey,
        sessionId: 'test-sid',
        token: 'test-token',
        serverUrl: 'http://localhost:8989',
      };
      const { frames } = await openEncryptedStream(
        session, 2, '/叶方', 'IMG_0443.MP4', 0, -1,
      );
      for await (const _ of frames) { void _; }

      const url = new URL(capturedUrl);
      const decrypted = decryptEnvelope(
        sessionKey,
        url.searchParams.get('nonce') || '',
        url.searchParams.get('ciphertext') || '',
      );
      const body = JSON.parse(new TextDecoder().decode(decrypted)) as Record<string, unknown>;
      expect(body.path).toBe('/叶方');
      expect(body.filename).toBe('IMG_0443.MP4');
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

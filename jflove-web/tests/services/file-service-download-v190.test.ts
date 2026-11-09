/**
 * 下载断点续传测试（v1.9.0，AC-332 / AC-335 / AC-336 / AC-337）
 *
 * 服务层单测（http-client / 本地存储均 mock）：
 *   - 有本地 part：请求带 `range_start=已存偏移`，完成后从存储拼 Blob 导出并清理临时数据；
 *   - 远端版本 / 大小变化、服务端未按偏移返回、416 → 删除本地 part 从头下载（不拼坏文件）；
 *   - 浏览器不支持 OPFS/IndexedDB → 回退整文件下载并**明确提示**，不得静默；
 *   - 中断 / 失败保留本地 part（不 remove），用户「放弃」时才清理；
 *   - 每次下载前清理超过 7 天的本地 part（D33）。
 *
 * 加密帧由真实 `encryptStreamChunk` 生成，`fileService` 走真实 stream-frame 解密，
 * 覆盖「下载流可被客户端正确解密」的安全用例。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fileService } from '../../src/services/file-service';
import {
  DOWNLOAD_PART_TTL_MS,
  downloadPartKey,
  type DownloadPartMeta,
  type DownloadPartStore,
} from '../../src/utils/download-store';
import {
  generateKeyPair,
  deriveSessionKey,
  encryptStreamChunk,
} from '../../src/utils/crypto';
import {
  setSessionKey,
  setSessionId,
  setToken,
  clearSession,
} from '../../src/utils/session';
import { ApiError } from '../../src/types/models';
import type { DownloadStreamHeaders } from '../../src/utils/http-client';

const mocks = vi.hoisted(() => ({
  downloadStream: vi.fn(),
  getStore: vi.fn(),
}));

vi.mock('../../src/utils/http-client', () => ({
  httpClient: { downloadStream: mocks.downloadStream },
}));

vi.mock('../../src/utils/download-store', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../src/utils/download-store')>();
  return { ...actual, getDownloadPartStore: mocks.getStore };
});

/** 内存版下载续传存储 */
class MemoryStore implements DownloadPartStore {
  kind = 'indexeddb' as const;
  metas = new Map<string, DownloadPartMeta>();
  chunks = new Map<string, Uint8Array[]>();
  removedKeys: string[] = [];
  cleanupMaxAge: number | null = null;

  async readMeta(key: string): Promise<DownloadPartMeta | null> {
    return this.metas.get(key) ?? null;
  }

  async writeMeta(key: string, meta: DownloadPartMeta): Promise<void> {
    this.metas.set(key, meta);
  }

  async appendChunk(key: string, _index: number, data: Uint8Array): Promise<void> {
    const list = this.chunks.get(key) ?? [];
    list.push(new Uint8Array(data));
    this.chunks.set(key, list);
  }

  async readAll(key: string, _chunkCount: number): Promise<Uint8Array[]> {
    return this.chunks.get(key) ?? [];
  }

  async remove(key: string): Promise<void> {
    this.removedKeys.push(key);
    this.metas.delete(key);
    this.chunks.delete(key);
  }

  async cleanupExpired(maxAgeMs: number): Promise<number> {
    this.cleanupMaxAge = maxAgeMs;
    return 0;
  }
}

/** 用真实加密帧构造成一段「服务端流」 */
function encryptedStream(
  parts: Uint8Array[],
  sessionKey: Uint8Array,
): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const part of parts) {
        controller.enqueue(encryptStreamChunk(sessionKey, part));
      }
      controller.close();
    },
  });
}

function headers(partial: Partial<DownloadStreamHeaders>): DownloadStreamHeaders {
  return {
    streamOffset: 0,
    streamTotal: null,
    fileVersion: null,
    encryptedStream: 'v1',
    ...partial,
  };
}

function bytes(start: number, length: number): Uint8Array {
  const data = new Uint8Array(length);
  for (let i = 0; i < length; i += 1) data[i] = (start + i) % 251;
  return data;
}

function textOf(data: Uint8Array): string {
  return new TextDecoder().decode(data);
}

/** jsdom 的 Blob 未实现 arrayBuffer()，用 FileReader 读回 Blob 字节 */
function blobToBytes(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error ?? new Error('读取 Blob 失败'));
    reader.readAsArrayBuffer(blob);
  });
}

const createObjectURL = vi.fn(() => 'blob:mock');
let sessionKey: Uint8Array;

beforeEach(async () => {
  mocks.downloadStream.mockReset();
  mocks.getStore.mockReset();
  // 真实会话密钥 + 真实加密帧（安全用例：客户端必须能逐帧解密）
  const alice = await generateKeyPair();
  const bob = await generateKeyPair();
  sessionKey = await deriveSessionKey(alice.privateKey, bob.publicKeyB64);
  setSessionKey(sessionKey);
  setSessionId('download-session-id');
  setToken('test-jwt-token');
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    writable: true,
    value: createObjectURL,
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    writable: true,
    value: vi.fn(),
  });
  // jsdom 未实现真实导航：导出用的 <a>.click() 只验证「被点击」，不触发跳转
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});

afterEach(() => {
  createObjectURL.mockClear();
  clearSession();
  vi.restoreAllMocks();
});

describe('下载断点续传 · 续传与导出', () => {
  it('从本地已存偏移续传：带 range_start、提示已从 X% 继续、完成后拼 Blob 并清理临时数据', async () => {
    const store = new MemoryStore();
    const key = downloadPartKey(1, 'dir/a.bin');
    store.metas.set(key, {
      version: '2048:1760000000',
      size: 2048,
      offset: 1024,
      chunkCount: 1,
      filename: 'a.bin',
      updatedAt: Date.now(),
    });
    store.chunks.set(key, [bytes(0, 1024)]);
    mocks.getStore.mockResolvedValue(store);

    const second = bytes(1024, 1024);
    mocks.downloadStream.mockResolvedValue({
      stream: encryptedStream([second], sessionKey),
      headers: headers({
        streamOffset: 1024,
        streamTotal: 2048,
        fileVersion: '2048:1760000000',
      }),
    });

    const statuses: Array<{ kind: string; offset: number; message: string }> = [];
    const progress: number[] = [];
    const result = await fileService.downloadResumable(1, 'dir/a.bin', 'a.bin', {
      onStatus: event => statuses.push(event),
      onProgress: received => progress.push(received),
    });

    const requestBody = mocks.downloadStream.mock.calls[0][1] as Record<string, unknown>;
    expect(requestBody.range_start).toBe(1024);
    expect(mocks.downloadStream.mock.calls[0][0]).toBe('/api/v1/files/download');

    expect(statuses[0]).toMatchObject({ kind: 'resume', offset: 1024 });
    expect(statuses[0].message).toContain('50%');
    expect(result).toEqual({ storage: 'indexeddb', resumedFrom: 1024, size: 2048 });
    expect(store.cleanupMaxAge).toBe(DOWNLOAD_PART_TTL_MS);
    expect(store.removedKeys).toContain(key);

    const blob = createObjectURL.mock.calls[0][0] as Blob;
    const merged = await blobToBytes(blob);
    expect(merged).toHaveLength(2048);
    expect(textOf(merged.slice(0, 16))).toBe(textOf(bytes(0, 16)));
    expect(textOf(merged.slice(1024, 1040))).toBe(textOf(bytes(1024, 16)));
    expect(progress[progress.length - 1]).toBe(2048);
  });

  it('首次下载（无本地 part）：从头下载并导出，完成后清理', async () => {
    const store = new MemoryStore();
    mocks.getStore.mockResolvedValue(store);
    mocks.downloadStream.mockResolvedValue({
      stream: encryptedStream([bytes(0, 512), bytes(512, 512)], sessionKey),
      headers: headers({ streamOffset: 0, streamTotal: 1024, fileVersion: '1024:1' }),
    });

    const result = await fileService.downloadResumable(2, 'b.bin', 'b.bin');
    expect((mocks.downloadStream.mock.calls[0][1] as Record<string, unknown>).range_start)
      .toBe(0);
    expect(result).toEqual({ storage: 'indexeddb', resumedFrom: 0, size: 1024 });
    expect(store.removedKeys).toContain(downloadPartKey(2, 'b.bin'));
    expect(store.metas.size).toBe(0);
  });
});

describe('下载断点续传 · 远端变化与降级', () => {
  it('远端文件版本变化 → 删除本地 part 从头下载，并提示「文件已更新」', async () => {
    const store = new MemoryStore();
    const key = downloadPartKey(1, 'dir/a.bin');
    store.metas.set(key, {
      version: '2048:100',
      size: 2048,
      offset: 1024,
      chunkCount: 1,
      filename: 'a.bin',
      updatedAt: Date.now(),
    });
    store.chunks.set(key, [bytes(0, 1024)]);
    mocks.getStore.mockResolvedValue(store);

    mocks.downloadStream
      .mockResolvedValueOnce({
        stream: encryptedStream([bytes(1024, 1024)], sessionKey),
        headers: headers({
          streamOffset: 1024,
          streamTotal: 2048,
          fileVersion: '2048:200',
        }),
      })
      .mockResolvedValueOnce({
        stream: encryptedStream([bytes(500, 1024), bytes(500, 1024)], sessionKey),
        headers: headers({
          streamOffset: 0,
          streamTotal: 2048,
          fileVersion: '2048:200',
        }),
      });

    const statuses: Array<{ kind: string; message: string }> = [];
    const result = await fileService.downloadResumable(1, 'dir/a.bin', 'a.bin', {
      onStatus: event => statuses.push(event),
    });

    expect(mocks.downloadStream).toHaveBeenCalledTimes(2);
    expect((mocks.downloadStream.mock.calls[1][1] as Record<string, unknown>).range_start)
      .toBe(0);
    expect(statuses[0].kind).toBe('restart');
    expect(statuses[0].message).toContain('文件已更新');
    expect(result.resumedFrom).toBe(0);
    expect(store.removedKeys).toContain(key);
  });

  it('服务端未按请求偏移返回（旧服务端忽略 range_start）→ 从头下载，不拼坏文件', async () => {
    const store = new MemoryStore();
    const key = downloadPartKey(3, 'c.bin');
    store.metas.set(key, {
      version: null,
      size: null,
      offset: 1024,
      chunkCount: 1,
      filename: 'c.bin',
      updatedAt: Date.now(),
    });
    mocks.getStore.mockResolvedValue(store);

    mocks.downloadStream.mockResolvedValue({
      stream: encryptedStream([bytes(0, 1024)], sessionKey),
      headers: headers({ streamOffset: null, streamTotal: null, fileVersion: null }),
    });

    const statuses: Array<{ kind: string; message: string }> = [];
    const result = await fileService.downloadResumable(3, 'c.bin', 'c.bin', {
      onStatus: event => statuses.push(event),
    });

    expect(mocks.downloadStream).toHaveBeenCalledTimes(2);
    expect((mocks.downloadStream.mock.calls[1][1] as Record<string, unknown>).range_start)
      .toBe(0);
    expect(statuses[0].kind).toBe('restart');
    expect(statuses[0].message).toContain('第 0 字节');
    expect(result.resumedFrom).toBe(0);
  });

  it('偏移越界（416）→ 清空本地 part 从头下载', async () => {
    const store = new MemoryStore();
    mocks.getStore.mockResolvedValue(store);
    mocks.downloadStream
      .mockRejectedValueOnce(new ApiError(416, '下载范围越界'))
      .mockResolvedValueOnce({
        stream: encryptedStream([bytes(0, 256)], sessionKey),
        headers: headers({ streamOffset: 0, streamTotal: 256, fileVersion: '256:1' }),
      });
    store.metas.set(downloadPartKey(4, 'd.bin'), {
      version: '999:1',
      size: 999,
      offset: 999,
      chunkCount: 1,
      filename: 'd.bin',
      updatedAt: Date.now(),
    });

    const statuses: Array<{ kind: string; message: string }> = [];
    const result = await fileService.downloadResumable(4, 'd.bin', 'd.bin', {
      onStatus: event => statuses.push(event),
    });
    expect(statuses[0].kind).toBe('restart');
    expect(statuses[0].message).toContain('超出');
    expect(result.size).toBe(256);
  });

  it('浏览器不支持断点续传 → 回退整文件下载并明确提示（不得静默）', async () => {
    mocks.getStore.mockResolvedValue(null);
    mocks.downloadStream.mockResolvedValue({
      stream: encryptedStream([bytes(0, 300)], sessionKey),
      headers: headers({ streamOffset: 0, streamTotal: 300, fileVersion: '300:1' }),
    });

    const statuses: Array<{ kind: string; message: string }> = [];
    const result = await fileService.downloadResumable(5, 'e.bin', 'e.bin', {
      onStatus: event => statuses.push(event),
    });

    expect(mocks.downloadStream).toHaveBeenCalledTimes(1);
    expect((mocks.downloadStream.mock.calls[0][1] as Record<string, unknown>).range_start)
      .toBe(0);
    expect(result.storage).toBe('none');
    expect(statuses[0].kind).toBe('unsupported');
    expect(statuses[0].message).toContain('不支持断点续传');
    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });
});

describe('下载断点续传 · 失败保留与放弃清理', () => {
  it('流未下完（总大小不符）→ 抛错但保留本地 part 供续传', async () => {
    const store = new MemoryStore();
    mocks.getStore.mockResolvedValue(store);
    mocks.downloadStream.mockResolvedValue({
      stream: encryptedStream([bytes(0, 50)], sessionKey),
      headers: headers({ streamOffset: 0, streamTotal: 100, fileVersion: '100:1' }),
    });

    const key = downloadPartKey(6, 'f.bin');
    await expect(
      fileService.downloadResumable(6, 'f.bin', 'f.bin'),
    ).rejects.toThrow(/下载不完整/);

    // 失败不得清理：meta 保留已落盘偏移，下次可从此续传
    expect(store.removedKeys).not.toContain(key);
    expect(store.metas.get(key)?.offset).toBe(50);
    expect(store.cleanupMaxAge).toBe(DOWNLOAD_PART_TTL_MS);
  });

  it('本地分块被浏览器清理后不完整 → 清理残数据并提示重新下载（不陷在续传死循环）', async () => {
    const store = new MemoryStore();
    mocks.getStore.mockResolvedValue(store);
    const key = downloadPartKey(9, 'h.bin');
    // meta 声称已存 512B，但分块数据已丢失（浏览器清理）
    store.metas.set(key, {
      version: '1024:1',
      size: 1024,
      offset: 512,
      chunkCount: 1,
      filename: 'h.bin',
      updatedAt: Date.now(),
    });
    mocks.downloadStream.mockResolvedValue({
      stream: encryptedStream([bytes(0, 512)], sessionKey),
      headers: headers({ streamOffset: 512, streamTotal: 1024, fileVersion: '1024:1' }),
    });

    await expect(
      fileService.downloadResumable(9, 'h.bin', 'h.bin'),
    ).rejects.toThrow(/本地续传数据不完整/);
    // 残数据必须清掉，否则下次续传还会拼出坏文件
    expect(store.removedKeys).toContain(key);
  });

  it('用户「放弃下载」→ 清理本地 part；不支持续传时返回 false', async () => {
    const store = new MemoryStore();
    mocks.getStore.mockResolvedValue(store);
    const key = downloadPartKey(7, 'g.bin');
    store.metas.set(key, {
      version: null, size: 10, offset: 5, chunkCount: 1, filename: 'g.bin', updatedAt: Date.now(),
    });

    await expect(fileService.abandonDownload(7, 'g.bin')).resolves.toBe(true);
    expect(store.removedKeys).toContain(key);

    mocks.getStore.mockResolvedValue(null);
    await expect(fileService.abandonDownload(7, 'g.bin')).resolves.toBe(false);
  });
});

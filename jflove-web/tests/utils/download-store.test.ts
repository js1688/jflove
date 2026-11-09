/**
 * 下载续传本地存储测试（v1.9.0，AC-335 / AC-336 / AC-337）
 *
 * 覆盖三件事：
 *   1. 存储选型顺序：OPFS 优先 → IndexedDB 回退 → 两者都不可用返回 none；
 *   2. OPFS 实现的分块读写 / meta 回写 / 删除 / 7 天过期清理；
 *   3. 过期判定边界（7 天整不算过期）。
 *
 * jsdom 不自带 OPFS，这里用一份内存版 FileSystemDirectoryHandle 替身驱动真实实现。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  DOWNLOAD_PART_TTL_MS,
  detectDownloadStorageKind,
  downloadPartKey,
  getDownloadPartStore,
  isPartExpired,
  resetDownloadPartStoreForTest,
  type DownloadPartMeta,
  type DownloadPartStore,
} from '../../src/utils/download-store';

/** 内存版文件句柄（实现 createWritable / getFile 的最小契约） */
class FakeFile {
  data: Uint8Array = new Uint8Array(0);

  async createWritable(): Promise<{
    write: (input: unknown) => Promise<void>;
    close: () => Promise<void>;
  }> {
    let text: string | null = null;
    let buffer: ArrayBuffer | null = null;
    return {
      write: async (input: unknown) => {
        if (typeof input === 'string') {
          text = input;
        } else if (input instanceof ArrayBuffer) {
          buffer = input.slice(0);
        } else if (input instanceof Uint8Array) {
          buffer = new Uint8Array(input).buffer;
        }
      },
      close: async () => {
        this.data = buffer
          ? new Uint8Array(buffer)
          : new TextEncoder().encode(text ?? '');
      },
    };
  }

  async getFile(): Promise<File> {
    const data = this.data;
    return {
      text: async () => new TextDecoder().decode(data),
      arrayBuffer: async () => new Uint8Array(data).buffer,
    } as unknown as File;
  }
}

/** 内存版目录句柄 */
class FakeDir {
  dirs = new Map<string, FakeDir>();
  files = new Map<string, FakeFile>();

  async getDirectoryHandle(
    name: string,
    options?: { create?: boolean },
  ): Promise<FakeDir> {
    let dir = this.dirs.get(name);
    if (!dir) {
      if (!options?.create) throw new Error(`NotFound: ${name}`);
      dir = new FakeDir();
      this.dirs.set(name, dir);
    }
    return dir;
  }

  async getFileHandle(
    name: string,
    options?: { create?: boolean },
  ): Promise<FakeFile> {
    let file = this.files.get(name);
    if (!file) {
      if (!options?.create) throw new Error(`NotFound: ${name}`);
      file = new FakeFile();
      this.files.set(name, file);
    }
    return file;
  }

  async removeEntry(name: string, options?: { recursive?: boolean }): Promise<void> {
    if (this.dirs.has(name)) {
      if (!options?.recursive) throw new Error('目录非空');
      this.dirs.delete(name);
      return;
    }
    if (!this.files.delete(name)) throw new Error(`NotFound: ${name}`);
  }
}

const originalStorage = Object.getOwnPropertyDescriptor(navigator, 'storage');

function installFakeStorage(getDirectory: () => Promise<unknown>): void {
  Object.defineProperty(navigator, 'storage', {
    configurable: true,
    value: { getDirectory },
  });
}

afterEach(() => {
  resetDownloadPartStoreForTest();
  if (originalStorage) {
    Object.defineProperty(navigator, 'storage', originalStorage);
  } else {
    delete (navigator as unknown as { storage?: unknown }).storage;
  }
  vi.unstubAllGlobals();
});

function metaAt(offset: number, updatedAt: number, chunkCount = 1): DownloadPartMeta {
  return {
    version: '2048:1760000000',
    size: 2048,
    offset,
    chunkCount,
    filename: 'a.bin',
    updatedAt,
  };
}

describe('下载续传存储 · 选型与过期判定', () => {
  it('默认环境（无 OPFS / 无 IndexedDB）判定为 none', () => {
    expect(typeof navigator.storage?.getDirectory).toBe('undefined');
    expect(detectDownloadStorageKind()).toBe('none');
  });

  it('navigator.storage.getDirectory 可用时优先 OPFS', () => {
    installFakeStorage(async () => new FakeDir());
    expect(detectDownloadStorageKind()).toBe('opfs');
  });

  it('OPFS 不可用但存在 IndexedDB 时回退 IndexedDB', () => {
    vi.stubGlobal('indexedDB', { open: vi.fn() });
    expect(detectDownloadStorageKind()).toBe('indexeddb');
  });

  it('OPFS 打开失败且无 IndexedDB 时返回 null（调用方回退整文件下载）', async () => {
    installFakeStorage(async () => {
      throw new DOMException('GetDirectory 不可用', 'NotSupportedError');
    });
    await expect(getDownloadPartStore()).resolves.toBeNull();
  });

  it('7 天整不算过期，超过 1ms 才算过期（D33）', () => {
    const now = Date.now();
    expect(isPartExpired({ updatedAt: now - DOWNLOAD_PART_TTL_MS }, now)).toBe(false);
    expect(isPartExpired({ updatedAt: now - DOWNLOAD_PART_TTL_MS - 1 }, now)).toBe(true);
  });

  it('同一磁盘 + 同一路径得到稳定 key，不同路径互不干扰', () => {
    expect(downloadPartKey(1, 'dir/a.bin')).toBe('d1|dir/a.bin');
    expect(downloadPartKey(2, 'dir/a.bin')).toBe('d2|dir/a.bin');
  });
});

describe('下载续传存储 · OPFS 实现', () => {
  async function opfsStore(): Promise<DownloadPartStore> {
    const root = new FakeDir();
    installFakeStorage(async () => root);
    resetDownloadPartStoreForTest();
    const store = await getDownloadPartStore();
    expect(store?.kind).toBe('opfs');
    return store!;
  }

  it('meta 与分块可写可读，remove 后清空', async () => {
    const store = await opfsStore();
    const key = downloadPartKey(7, 'movies/a.bin');
    await store.writeMeta(key, metaAt(1024, Date.now(), 1));
    await store.appendChunk(key, 0, new Uint8Array([1, 2, 3, 4]));

    expect(await store.readMeta(key)).toEqual(metaAt(1024, (await store.readMeta(key))!.updatedAt, 1));
    const chunks = await store.readAll(key, 1);
    expect(chunks).toHaveLength(1);
    expect(Array.from(chunks[0])).toEqual([1, 2, 3, 4]);

    await store.remove(key);
    expect(await store.readMeta(key)).toBeNull();
  });

  it('cleanupExpired 只删除超过 7 天的 part，并保留未过期数据', async () => {
    const store = await opfsStore();
    const now = Date.now();
    const expiredKey = downloadPartKey(8, 'old.bin');
    const freshKey = downloadPartKey(8, 'fresh.bin');
    await store.writeMeta(expiredKey, metaAt(1024, now - DOWNLOAD_PART_TTL_MS - 60_000));
    await store.appendChunk(expiredKey, 0, new Uint8Array([9]));
    await store.writeMeta(freshKey, metaAt(512, now));

    const removed = await store.cleanupExpired(DOWNLOAD_PART_TTL_MS);
    expect(removed).toBe(1);
    expect(await store.readMeta(expiredKey)).toBeNull();
    expect(await store.readMeta(freshKey)).toEqual(metaAt(512, now));
  });
});

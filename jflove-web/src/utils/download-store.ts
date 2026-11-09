/**
 * 下载续传本地存储（v1.9.0，AC-335 / AC-336 / AC-337）
 *
 * 浏览器端下载断点续传的「本地 part」实现：按分块保存**已解密**的明文字节 + 一份 meta
 * （远端版本 / 大小 / 已保存偏移），刷新页面后据此用 `range_start` 续传。
 *
 * 存储选型（设计文档 §3.4，硬性顺序）：
 *   1. **OPFS 优先**：`navigator.storage.getDirectory()`（仅安全上下文可用）；
 *   2. **OPFS 不可用 → IndexedDB**；
 *   3. 两者都不可用 → 返回 null，调用方回退「整文件下载」并明确提示不支持断点续传。
 *
 * 生命周期：本地 part 保留 **7 天**（D33），每次续传前顺手清理超期数据。
 * 安全：meta 只存远端元数据（size / version / 偏移），不保存 token / session_key；
 * 所有数据都在浏览器私有存储里，不进入 URL，也不回传服务端。
 */

/** 本地 part 保留时长（D33：7 天） */
export const DOWNLOAD_PART_TTL_MS = 7 * 24 * 60 * 60 * 1000;

/** 存储后端类型（none = 浏览器不支持断点续传） */
export type DownloadStorageKind = 'opfs' | 'indexeddb' | 'none';

/** 下载续传元数据（与分块数据同目录/同库保存） */
export interface DownloadPartMeta {
  /** 远端文件版本标识（X-File-Version；旧服务端无此头时 null） */
  version: string | null;
  /** 远端文件总字节（X-Stream-Total） */
  size: number | null;
  /** 已保存的解密字节数（= 续传时的 range_start） */
  offset: number;
  /** 已保存的分块数量（读取/拼装按 index 顺序） */
  chunkCount: number;
  /** 导出用文件名（仅本地使用，不进入 URL） */
  filename: string;
  /** 最后更新时间（毫秒），用于 7 天过期清理 */
  updatedAt: number;
}

/** 下载续传存储抽象（OPFS 与 IndexedDB 实现同一套语义；测试可注入内存实现） */
export interface DownloadPartStore {
  readonly kind: DownloadStorageKind;
  readMeta(key: string): Promise<DownloadPartMeta | null>;
  writeMeta(key: string, meta: DownloadPartMeta): Promise<void>;
  /** 追加一个已解密分块（index 从 0 递增） */
  appendChunk(key: string, index: number, data: Uint8Array): Promise<void>;
  /** 按 index 顺序读出全部分块 */
  readAll(key: string, chunkCount: number): Promise<Uint8Array[]>;
  /** 删除该文件的本地 part（用户「放弃」/远端变化时调用） */
  remove(key: string): Promise<void>;
  /** 清理超过 maxAgeMs 的 part，返回删除数量 */
  cleanupExpired(maxAgeMs: number): Promise<number>;
}

/** part 是否已超过保留期（纯函数便于单测） */
export function isPartExpired(
  meta: Pick<DownloadPartMeta, 'updatedAt'>,
  now: number,
  maxAgeMs: number = DOWNLOAD_PART_TTL_MS,
): boolean {
  return now - meta.updatedAt > maxAgeMs;
}

/** 本地 part 的键：同一磁盘 + 同一路径视为同一个文件（文件名已含在 path 内） */
export function downloadPartKey(diskId: number, path: string): string {
  return `d${diskId}|${path}`;
}

/** 检测可用的存储后端（只判能力，不创建存储；失败由调用方降级） */
export function detectDownloadStorageKind(): DownloadStorageKind {
  if (
    typeof navigator !== 'undefined'
    && typeof navigator.storage === 'object'
    && typeof navigator.storage?.getDirectory === 'function'
  ) {
    return 'opfs';
  }
  if (typeof indexedDB !== 'undefined') {
    return 'indexeddb';
  }
  return 'none';
}

// ── OPFS 实现 ─────────────────────────────────────────

const OPFS_ROOT_DIR = 'jflove-downloads';
const OPFS_INDEX_FILE = 'index.json';
const OPFS_META_FILE = 'meta.json';
const OPFS_CHUNK_PREFIX = 'chunk-';

/** OPFS 索引：目录名 → { key, updatedAt }，用于 7 天过期清理与列举 */
type OpfsIndex = Record<string, { key: string; updatedAt: number }>;

function opfsChunkName(index: number): string {
  return `${OPFS_CHUNK_PREFIX}${String(index).padStart(6, '0')}.bin`;
}

function opfsDirName(key: string): string {
  // 目录名不能含 `/`：encodeURIComponent 会把 `/` 编成 %2F，且中文路径也可安全落盘
  return encodeURIComponent(key);
}

async function writeTextFile(
  dir: FileSystemDirectoryHandle,
  name: string,
  text: string,
): Promise<void> {
  const handle = await dir.getFileHandle(name, { create: true });
  const writable = await handle.createWritable();
  await writable.write(text);
  await writable.close();
}

async function readTextFile(
  dir: FileSystemDirectoryHandle,
  name: string,
): Promise<string | null> {
  try {
    const handle = await dir.getFileHandle(name);
    return await (await handle.getFile()).text();
  } catch {
    return null;
  }
}

async function createOpfsStore(): Promise<DownloadPartStore> {
  const root = await navigator.storage.getDirectory();
  const dir = await root.getDirectoryHandle(OPFS_ROOT_DIR, { create: true });
  // 索引读改写串行化：同一页面里多个下载同时写 meta 时不互相覆盖
  let indexQueue: Promise<unknown> = Promise.resolve();

  const readIndex = async (): Promise<OpfsIndex> => {
    const raw = await readTextFile(dir, OPFS_INDEX_FILE);
    if (!raw) return {};
    try {
      return JSON.parse(raw) as OpfsIndex;
    } catch {
      return {};
    }
  };

  const updateIndex = (mutate: (index: OpfsIndex) => void): Promise<void> => {
    const task = indexQueue.then(async () => {
      const index = await readIndex();
      mutate(index);
      await writeTextFile(dir, OPFS_INDEX_FILE, JSON.stringify(index));
    });
    indexQueue = task.catch(() => undefined);
    return task;
  };

  const removePartDir = async (key: string): Promise<void> => {
    const name = opfsDirName(key);
    try {
      await dir.removeEntry(name, { recursive: true });
    } catch {
      // 目录不存在即视为已清理
    }
  };

  return {
    kind: 'opfs',

    async readMeta(key) {
      const partDir = await dir.getDirectoryHandle(opfsDirName(key), { create: false })
        .catch(() => null);
      if (!partDir) return null;
      const raw = await readTextFile(partDir, OPFS_META_FILE);
      if (!raw) return null;
      try {
        return JSON.parse(raw) as DownloadPartMeta;
      } catch {
        return null;
      }
    },

    async writeMeta(key, meta) {
      const partDir = await dir.getDirectoryHandle(opfsDirName(key), { create: true });
      await writeTextFile(partDir, OPFS_META_FILE, JSON.stringify(meta));
      await updateIndex((index) => {
        index[opfsDirName(key)] = { key, updatedAt: meta.updatedAt };
      });
    },

    async appendChunk(key, index, data) {
      const partDir = await dir.getDirectoryHandle(opfsDirName(key), { create: true });
      const handle = await partDir.getFileHandle(opfsChunkName(index), { create: true });
      const writable = await handle.createWritable();
      // TS 5.7：`Uint8Array<ArrayBufferLike>` 不满足 FileSystemWriteChunkType，显式复制成 ArrayBuffer
      const buffer = new ArrayBuffer(data.byteLength);
      new Uint8Array(buffer).set(data);
      await writable.write(buffer);
      await writable.close();
    },

    async readAll(key, chunkCount) {
      const partDir = await dir.getDirectoryHandle(opfsDirName(key), { create: false })
        .catch(() => null);
      if (!partDir) return [];
      const chunks: Uint8Array[] = [];
      for (let i = 0; i < chunkCount; i += 1) {
        try {
          const handle = await partDir.getFileHandle(opfsChunkName(i));
          chunks.push(new Uint8Array(await (await handle.getFile()).arrayBuffer()));
        } catch {
          // 分块缺失（用户清理了浏览器数据等）：返回已读到的部分，由调用方校验长度
          break;
        }
      }
      return chunks;
    },

    async remove(key) {
      await removePartDir(key);
      await updateIndex((index) => {
        delete index[opfsDirName(key)];
      });
    },

    async cleanupExpired(maxAgeMs) {
      const index = await readIndex();
      const now = Date.now();
      let removed = 0;
      for (const [dirName, entry] of Object.entries(index)) {
        if (isPartExpired({ updatedAt: entry.updatedAt }, now, maxAgeMs)) {
          try {
            await dir.removeEntry(dirName, { recursive: true });
            removed += 1;
          } catch {
            // 已被并发清理 / 不存在：忽略并继续
          }
          delete index[dirName];
        }
      }
      if (removed > 0) {
        await writeTextFile(dir, OPFS_INDEX_FILE, JSON.stringify(index));
      }
      return removed;
    },
  };
}

// ── IndexedDB 实现 ────────────────────────────────────

const IDB_NAME = 'jflove-downloads';
const IDB_VERSION = 1;
const IDB_META_STORE = 'meta';
const IDB_CHUNK_STORE = 'chunks';

interface IdbMetaRecord {
  key: string;
  meta: DownloadPartMeta;
}

interface IdbChunkRecord {
  key: string;
  index: number;
  data: Uint8Array;
}

function idbRequest<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('IndexedDB 操作失败'));
  });
}

async function createIdbStore(): Promise<DownloadPartStore> {
  let dbPromise: Promise<IDBDatabase> | null = null;

  const getDb = (): Promise<IDBDatabase> => {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(IDB_NAME, IDB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(IDB_META_STORE)) {
          db.createObjectStore(IDB_META_STORE, { keyPath: 'key' });
        }
        if (!db.objectStoreNames.contains(IDB_CHUNK_STORE)) {
          db.createObjectStore(IDB_CHUNK_STORE, { keyPath: ['key', 'index'] });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error('打开 IndexedDB 失败'));
    });
    return dbPromise;
  };

  const withStores = async <T>(
    stores: string[],
    mode: IDBTransactionMode,
    run: (tx: IDBTransaction) => Promise<T>,
  ): Promise<T> => {
    const db = await getDb();
    const tx = db.transaction(stores, mode);
    const result = await run(tx);
    return new Promise<T>((resolve, reject) => {
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error ?? new Error('IndexedDB 事务失败'));
      tx.onabort = () => reject(tx.error ?? new Error('IndexedDB 事务中止'));
    });
  };

  const chunkRange = (key: string, chunkCount: number): IDBKeyRange | null => {
    if (chunkCount <= 0) return null;
    return IDBKeyRange.bound([key, 0], [key, chunkCount - 1]);
  };

  return {
    kind: 'indexeddb',

    async readMeta(key) {
      const db = await getDb();
      const tx = db.transaction(IDB_META_STORE, 'readonly');
      const record = await idbRequest<IdbMetaRecord | undefined>(
        tx.objectStore(IDB_META_STORE).get(key),
      );
      return record?.meta ?? null;
    },

    async writeMeta(key, meta) {
      const record: IdbMetaRecord = { key, meta };
      await withStores([IDB_META_STORE], 'readwrite', async (tx) => {
        tx.objectStore(IDB_META_STORE).put(record);
      });
    },

    async appendChunk(key, index, data) {
      const record: IdbChunkRecord = { key, index, data };
      await withStores([IDB_CHUNK_STORE], 'readwrite', async (tx) => {
        tx.objectStore(IDB_CHUNK_STORE).put(record);
      });
    },

    async readAll(key, chunkCount) {
      const db = await getDb();
      const range = chunkRange(key, chunkCount);
      if (!range) return [];
      const tx = db.transaction(IDB_CHUNK_STORE, 'readonly');
      const records = await idbRequest<IdbChunkRecord[]>(
        tx.objectStore(IDB_CHUNK_STORE).getAll(range),
      );
      return records
        .sort((a, b) => a.index - b.index)
        .map(record => record.data);
    },

    async remove(key) {
      await withStores([IDB_META_STORE, IDB_CHUNK_STORE], 'readwrite', async (tx) => {
        tx.objectStore(IDB_META_STORE).delete(key);
        tx.objectStore(IDB_CHUNK_STORE).delete(
          IDBKeyRange.bound([key, 0], [key, Number.MAX_SAFE_INTEGER]),
        );
      });
    },

    async cleanupExpired(maxAgeMs) {
      const db = await getDb();
      const now = Date.now();
      const tx = db.transaction([IDB_META_STORE, IDB_CHUNK_STORE], 'readwrite');
      const metaStore = tx.objectStore(IDB_META_STORE);
      const chunkStore = tx.objectStore(IDB_CHUNK_STORE);
      const records = await idbRequest<IdbMetaRecord[]>(metaStore.getAll());
      let removed = 0;
      for (const record of records) {
        if (isPartExpired(record.meta, now, maxAgeMs)) {
          metaStore.delete(record.key);
          chunkStore.delete(
            IDBKeyRange.bound([record.key, 0], [record.key, Number.MAX_SAFE_INTEGER]),
          );
          removed += 1;
        }
      }
      return new Promise<number>((resolve, reject) => {
        tx.oncomplete = () => resolve(removed);
        tx.onerror = () => reject(tx.error ?? new Error('IndexedDB 事务失败'));
      });
    },
  };
}

// ── 对外入口 ──────────────────────────────────────────

let cachedStore: Promise<DownloadPartStore | null> | null = null;

/**
 * 获取下载续传存储（进程内缓存，避免每次下载重复探测/打开数据库）。
 *
 * OPFS 探测成功但实际不可用（隐私模式 / 权限拒绝）→ 回退 IndexedDB；
 * 两者都不可用时返回 null（调用方必须明确提示「浏览器不支持断点续传」）。
 */
export function getDownloadPartStore(): Promise<DownloadPartStore | null> {
  if (cachedStore) return cachedStore;
  cachedStore = (async () => {
    const kind = detectDownloadStorageKind();
    if (kind === 'opfs') {
      try {
        return await createOpfsStore();
      } catch {
        // 落到下面的 IndexedDB 兜底
      }
    }
    if (typeof indexedDB !== 'undefined') {
      try {
        return await createIdbStore();
      } catch {
        return null;
      }
    }
    return null;
  })();
  return cachedStore;
}

/** 仅供测试重置缓存（生产代码不调用） */
export function resetDownloadPartStoreForTest(): void {
  cachedStore = null;
}

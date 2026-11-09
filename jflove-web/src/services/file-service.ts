/**
 * 文件管理服务
 *
 * 对标桌面端 src/services/file_service.py。
 * 文件浏览 / 上传 / 下载 / 预览 / 重命名 / 移动 / 删除 / 创建目录。
 *
 * v1.9.0（Phase 4c）新增：
 *   - 上传断点续传：`init` 带 `client_type:"web"`、按 `received_chunks` 跳过已传分片、
 *     分片 512KB、单片失败指数退避重试（≥5 次）、网络/合并失败不删服务端会话（仅用户取消才删）；
 *   - 合并异步化：`complete` 带 `async_mode:true`，每 2.5s 轮询 `upload/progress`，
 *     处理 `queued` / `merging` / `done` / `failed`；旧服务端「无 state 字段」时才静默降级为同步完成。
 *   - 下载断点续传：`range_start` + `X-Stream-Offset/X-Stream-Total/X-File-Version` 响应头，
 *     本地分块优先 OPFS、回退 IndexedDB、都没有则整文件下载并明确提示。
 */

import { httpClient } from '../utils/http-client';
import type { DownloadStreamHeaders, DownloadStreamResult } from '../utils/http-client';
import { arrayBufferToBase64, isWebCryptoAvailable } from '../utils/crypto';
import { getSessionKey } from '../utils/session';
import { decryptStream } from '../utils/stream-frame';
import {
  DOWNLOAD_PART_TTL_MS,
  downloadPartKey,
  getDownloadPartStore,
  type DownloadPartStore,
  type DownloadStorageKind,
} from '../utils/download-store';
import { sha256 } from '@noble/hashes/sha2.js';
import { ApiError } from '../types/models';
import type { VirtualDisk, FileItem } from '../types/models';

/** 上传分片大小（v1.9.0 D35/F56.4：三端统一默认 512KB，服务端上限 1MB） */
export const UPLOAD_CHUNK_SIZE = 512 * 1024;

/** 单片上传最大尝试次数（F46.5：至少 5 次，指数退避） */
export const UPLOAD_CHUNK_MAX_ATTEMPTS = 5;

/** 分片重试基础退避（500ms → 1s → 2s → 4s） */
export const UPLOAD_CHUNK_RETRY_BASE_DELAY_MS = 500;

/** 合并进度轮询间隔（设计 §3.1：2.5 秒） */
export const UPLOAD_PROGRESS_POLL_INTERVAL_MS = 2500;

/** 合并等待上限（防止服务端丢失进度记录时无限轮询） */
export const UPLOAD_MERGE_MAX_WAIT_MS = 30 * 60 * 1000;

/** 下载落盘分块大小（已解密明文；OPFS/IndexedDB 每次追加的粒度） */
export const DOWNLOAD_BLOCK_SIZE = 1024 * 1024;

// ── 上传相关类型（API 契约见设计文档 §4） ──

/** `POST /api/v1/files/upload/init` 响应（新协议字段可选，旧服务端缺省即按 0 片处理） */
export interface UploadInitResponse {
  upload_id: string;
  /** 服务端已实际落盘的已收分片索引（以磁盘为准，F46.1/F46.3） */
  received_chunks?: number[];
  /** 是否为复用既有会话的续传 */
  resumed?: boolean;
  /** 可续传至（秒级时间戳，F53.1 展示用） */
  expires_at?: number;
  /** 服务端允许的单分片上限（客户端分片不得超过它） */
  max_chunk_size?: number;
  /** 服务端建议的并发（Web 端默认 1，仅作参考） */
  recommended_concurrency?: number;
  /** 目标文件已存在且大小一致（F46.6，无需上传） */
  already_exists?: boolean;
  already_path?: string;
  /** 旧会话已失败/过期，服务端返回新会话，客户端需从第 0 片开始 */
  restart_required?: boolean;
}

/** `POST /api/v1/files/upload/complete` 响应（强制异步；旧服务端无 state 字段） */
export interface UploadCompleteResponse {
  state?: string;
  upload_id?: string;
  /** 建议轮询间隔（秒），本版固定 2.5s，仅作协议兼容保留 */
  poll_interval_s?: number;
  queue_position?: number;
  path?: string;
  error?: string;
  missing_chunks?: number[];
}

/** `POST /api/v1/files/upload/progress` 响应 */
export interface UploadProgressResponse {
  state?: string;
  upload_id?: string;
  processed?: number;
  total?: number;
  percent?: number;
  elapsed_seconds?: number;
  queue_position?: number;
  path?: string;
  error?: string;
  missing_chunks?: number[];
}

/** 上传阶段状态（供 UI 展示「续传中 / 服务端合并中」等文案） */
export interface UploadStatusEvent {
  state: 'uploading' | 'resumed' | 'queued' | 'merging' | 'done';
  /** 面向用户的中文提示 */
  message: string;
  /** 本次开始的已收片起点（0 = 从头传） */
  startChunk: number;
  /** 已收分片索引（续传时用于展示） */
  receivedChunks?: number[];
}

/** 上传可选参数（`signal` 仅用于「用户明确取消」；失败时绝不主动触发 DELETE） */
export interface UploadOptions {
  signal?: AbortSignal;
  onStatus?: (event: UploadStatusEvent) => void;
}

/** 上传结果 */
export interface UploadResult {
  resumed: boolean;
  startChunk: number;
  alreadyExists: boolean;
  path?: string;
}

// ── 下载相关类型（API 契约见设计文档 §4 / §2.8） ──

/** 下载状态回调：续传 / 远端变化重下 / 浏览器不支持断点续传 */
export type DownloadStatusKind = 'resume' | 'restart' | 'unsupported';

export interface DownloadStatusEvent {
  kind: DownloadStatusKind;
  /** 面向用户的中文提示 */
  message: string;
  /** 本次（重）开始时的字节偏移 */
  offset: number;
}

/** 下载可选参数 */
export interface DownloadOptions {
  signal?: AbortSignal;
  /** 进度回调：已解密总字节数（含续传起点） */
  onProgress?: (received: number) => void;
  onStatus?: (event: DownloadStatusEvent) => void;
  /** 测试注入的存储实现；生产不传时按 OPFS → IndexedDB → 无 自动选型 */
  store?: DownloadPartStore;
}

/** 下载结果 */
export interface DownloadResult {
  /** 实际使用的存储后端（none = 不支持断点续传，整文件下载） */
  storage: DownloadStorageKind;
  /** 本次续传起点（0 = 从头下载） */
  resumedFrom: number;
  /** 文件总字节（服务端未提供时为实际收到字节） */
  size: number;
}

export const fileService = {
  /** 获取用户可访问的虚拟磁盘列表 */
  async listDisks(): Promise<VirtualDisk[]> {
    const resp = await httpClient.get<{ disks: VirtualDisk[] }>(
      '/api/v1/files/disks',
    );
    return resp.disks;
  },

  /** 列出磁盘内文件/目录（服务端为 GET 只读接口，走加密 GET + URL query 信封） */
  async listFiles(diskId: number, path: string): Promise<FileItem[]> {
    const resp = await httpClient.get<{ files: FileItem[] }>(
      '/api/v1/files/list',
      { disk_id: diskId, path },
    );
    return resp.files;
  },

  /** 创建目录 */
  /**
   * 创建目录
   *
   * **BUG 修复（v1.5.0）**：服务端 `/api/v1/files/mkdir` **只读 `path`**，
   * 其语义是「**要创建的目录的完整相对路径**」（等同 `mkdir -p`）。
   * 原来发的是 `{path: 当前目录, dir_name: 新目录名}` —— `dir_name` 服务端根本不读，
   * 于是把**当前目录**又创建了一遍：接口静默返回 200，新目录却从未出现
   * （表现为「点新建目录没反应」）。
   *
   * 现在在客户端拼出完整相对路径，与桌面端服务层 `make_dir(disk_id, rel_path)`
   * 的契约保持一致。调用签名不变，页面无需改动。
   */
  async createDir(diskId: number, path: string, dirName: string): Promise<void> {
    const relPath = path ? `${path}/${dirName}` : dirName;
    await httpClient.post('/api/v1/files/mkdir', {
      disk_id: diskId,
      path: relPath,
    });
  },

  /** 重命名文件/目录 */
  async rename(diskId: number, path: string, newName: string): Promise<void> {
    await httpClient.post('/api/v1/files/rename', {
      disk_id: diskId,
      path,
      new_name: newName,
    });
  },

  /** 移动文件/目录 */
  async move(diskId: number, srcPath: string, dstDirPath: string): Promise<void> {
    await httpClient.post('/api/v1/files/move', {
      disk_id: diskId,
      src_path: srcPath,
      dst_dir_path: dstDirPath,
    });
  },

  /**
   * 删除文件/目录
   *
   * **BUG 修复（v1.5.0）**：服务端的删除路由是 `DELETE /api/v1/files`（`@router.delete("")`），
   * 原来请求的是 `/api/v1/files/delete` —— **该路由不存在，服务端返回 404**，
   * 于是"右键删除"看起来走完了流程（请求发出、无前端报错），文件却纹丝不动。
   * 移动端与桌面端服务层一直用的是正确路由，只有这里写错了。
   */
  /** 删除文件/目录 */
  async delete(diskId: number, path: string): Promise<void> {
    await httpClient.delete('/api/v1/files', {
      disk_id: diskId,
      path,
    });
  },

  /**
   * 打开流式下载（v1 纯加密数据帧，GET /files/download）。
   *
   * v1.9.0：支持 `range_start`（字节偏移，只进加密 body，不进 URL 明文）；
   * 返回加密流 + 续传响应头（X-Stream-Offset / X-Stream-Total / X-File-Version）。
   * 注：不使用 /files/stream（v2）——其首帧为 meta 元数据帧，
   * 会导致下载文件头部混入 JSON 明文。
   */
  async downloadStream(
    diskId: number,
    path: string,
    filename: string,
    rangeStart = 0,
    signal?: AbortSignal,
  ): Promise<DownloadStreamResult> {
    return httpClient.downloadStream(
      '/api/v1/files/download',
      { disk_id: diskId, path, filename, range_start: rangeStart },
      { signal },
    );
  },

  /**
   * 下载文件并解密为完整字节（用于预览）。
   * 复用 /files/download（v1 加密流），逐帧解密合并；预览不做断点续传。
   *
   * @param onProgress 下载进度回调（已解密字节数累计值，供 UI 展示大文件下载进度）
   */
  async downloadRaw(
    diskId: number,
    path: string,
    filename: string,
    onProgress?: (downloaded: number) => void,
  ): Promise<Uint8Array> {
    const { stream } = await this.downloadStream(diskId, path, filename);
    const sessionKey = getSessionKey();
    if (!sessionKey) throw new Error('未建立加密会话');

    const chunks: Uint8Array[] = [];
    let total = 0;
    for await (const chunk of decryptStream(stream, sessionKey)) {
      chunks.push(chunk);
      total += chunk.length;
      onProgress?.(total);
    }

    const result = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      result.set(chunk, offset);
      offset += chunk.length;
    }
    return result;
  },

  /**
   * 下载文件（v1.9.0 断点续传主路径，AC-335 / AC-336 / AC-337）。
   *
   * 流程（设计 §3.4 / §5.2）：
   *   1. 读本地 part（OPFS 优先 / IndexedDB 回退）；都没有 → 整文件下载并提示不支持续传；
   *   2. 带 `range_start = 已存字节数` 请求；按响应头校验远端版本/大小/实际偏移；
   *      远端变化或服务端未按偏移返回 → 删除本地 part 从头下载（不拼坏文件）；
   *   3. 逐帧解密，按 1MB 分块追加到本地存储并回写 meta（失败/中断保留，供下次续传）；
   *   4. 完成后按大小校验，拼 Blob 导出为文件，清理本地临时数据。
   *
   * **失败语义**：异常直接抛出，但已保存的分块与 meta 保留；用户主动「放弃」时才清理
   * （调用 `abandonDownload`）。
   */
  async downloadResumable(
    diskId: number,
    path: string,
    filename: string,
    options: DownloadOptions = {},
  ): Promise<DownloadResult> {
    const store = options.store ?? (await getDownloadPartStore());

    // ① 浏览器不支持 OPFS / IndexedDB：回退整文件下载，并明确提示（不得静默）
    if (!store) {
      options.onStatus?.({
        kind: 'unsupported',
        message: '当前浏览器不支持断点续传，将从头下载整个文件',
        offset: 0,
      });
      const bytes = await this.downloadToBytes(diskId, path, filename, options);
      exportBlobToFile(blobFromBytes([bytes]), filename);
      options.onProgress?.(bytes.length);
      return { storage: 'none', resumedFrom: 0, size: bytes.length };
    }

    // ② 顺手清理超过 7 天的本地 part（D33 / AC-337）
    await store.cleanupExpired(DOWNLOAD_PART_TTL_MS).catch(() => 0);

    const key = downloadPartKey(diskId, path);
    let meta = await store.readMeta(key).catch(() => null);
    // 同名路径但文件名变化（目录里同名文件不会发生；防御性处理旧记录）
    if (meta && meta.filename !== filename) {
      await store.remove(key);
      meta = null;
    }
    let start = meta?.offset ?? 0;

    // 偏移越界（416）：清空本地 part 从头重下（服务端返回明确错误，AC-332）
    let response: DownloadStreamResult;
    try {
      response = await this.downloadStream(diskId, path, filename, start, options.signal);
    } catch (e) {
      if (e instanceof ApiError && e.status === 416 && start > 0) {
        await store.remove(key);
        meta = null;
        start = 0;
        options.onStatus?.({
          kind: 'restart',
          message: '下载位置已超出远端文件范围，将从第 0 字节重新下载',
          offset: 0,
        });
        response = await this.downloadStream(diskId, path, filename, 0, options.signal);
      } else {
        throw e;
      }
    }

    // ③ 远端版本 / 大小 / 实际偏移校验：任一不符 → 删除 part 从头下载
    const restartReason = this.describeRestartReason(meta, response.headers, start);
    if (restartReason) {
      await cancelStream(response.stream);
      await store.remove(key);
      meta = null;
      start = 0;
      response = await this.downloadStream(diskId, path, filename, 0, options.signal);
      options.onStatus?.({ kind: 'restart', message: restartReason, offset: 0 });
    } else if (start > 0) {
      const totalForPercent = response.headers.streamTotal ?? meta?.size ?? 0;
      const percent = totalForPercent > 0 ? Math.floor((start / totalForPercent) * 100) : 0;
      options.onStatus?.({
        kind: 'resume',
        message: `已从 ${percent}% 继续下载`,
        offset: start,
      });
    }

    const sessionKey = getSessionKey();
    if (!sessionKey) throw new Error('未建立加密会话');

    const remoteVersion = response.headers.fileVersion ?? meta?.version ?? null;
    const expectedTotal = response.headers.streamTotal ?? meta?.size ?? null;

    let chunkIndex = meta?.chunkCount ?? 0;
    let persisted = start;
    let consumed = 0;
    let pending: Uint8Array[] = [];
    let pendingBytes = 0;

    const flush = async (): Promise<void> => {
      if (pendingBytes === 0) return;
      const block = concatChunks(pending);
      pending = [];
      pendingBytes = 0;
      await store.appendChunk(key, chunkIndex, block);
      chunkIndex += 1;
      persisted += block.length;
      await store.writeMeta(key, {
        version: remoteVersion,
        size: expectedTotal,
        offset: persisted,
        chunkCount: chunkIndex,
        filename,
        updatedAt: Date.now(),
      });
    };

    try {
      for await (const chunk of decryptStream(response.stream, sessionKey)) {
        assertNotAborted(options.signal, '下载已取消');
        pending.push(chunk);
        pendingBytes += chunk.length;
        consumed += chunk.length;
        // 进度按「已解密字节」展示（比落盘时机更及时）
        options.onProgress?.(start + consumed);
        if (pendingBytes >= DOWNLOAD_BLOCK_SIZE) {
          await flush();
        }
      }
    } catch (e) {
      // 用户取消：主动取消响应流（否则 fetch 仍在后台把余下字节读进缓冲区）
      if (options.signal?.aborted) await cancelStream(response.stream);
      throw e;
    }
    await flush();

    const finalSize = start + consumed;
    if (expectedTotal !== null && finalSize !== expectedTotal) {
      throw new Error(
        `下载不完整（已下 ${finalSize} / 共 ${expectedTotal} 字节），`
        + '已保留临时数据，可重试续传',
      );
    }

    // ④ 拼 Blob 导出，并清理本地临时数据（完成后不留 part）
    const parts = await store.readAll(key, chunkIndex);
    const storedBytes = parts.reduce((sum, part) => sum + part.length, 0);
    if (storedBytes !== finalSize) {
      // 本地分块被浏览器清理/损坏：清掉残数据，下次从头下载（F48.3「分片不存在时重新开始即可」）
      await store.remove(key);
      throw new Error('本地续传数据不完整（可能被浏览器清理），已清理残数据，请重新下载');
    }
    exportBlobToFile(blobFromBytes(parts), filename);
    await store.remove(key);
    options.onProgress?.(finalSize);

    return { storage: store.kind, resumedFrom: start, size: finalSize };
  },

  /**
   * 用户「放弃下载」：清理该文件的本地 part（失败/中断默认保留，仅显式放弃才删除）。
   * @returns 是否确实清理了本地数据（浏览器不支持续传时返回 false）
   */
  async abandonDownload(diskId: number, path: string): Promise<boolean> {
    const store = await getDownloadPartStore();
    if (!store) return false;
    await store.remove(downloadPartKey(diskId, path));
    return true;
  },

  /** 整文件下载并解密为字节（不支持续传时的回退路径） */
  async downloadToBytes(
    diskId: number,
    path: string,
    filename: string,
    options: DownloadOptions,
  ): Promise<Uint8Array> {
    const { stream } = await this.downloadStream(diskId, path, filename, 0, options.signal);
    const sessionKey = getSessionKey();
    if (!sessionKey) throw new Error('未建立加密会话');
    const chunks: Uint8Array[] = [];
    let received = 0;
    for await (const chunk of decryptStream(stream, sessionKey)) {
      assertNotAborted(options.signal, '下载已取消');
      chunks.push(chunk);
      received += chunk.length;
      options.onProgress?.(received);
    }
    return concatChunks(chunks);
  },

  /**
   * 分片上传文件（v1.9.0 断点续传 + 异步合并）。
   *
   * 实现策略（设计 §3.4 / §5.1）：
   *   1. 先用远端列表核验「同名同大小」是否已存在（F46.6，避免响应丢失后整文件重传）；
   *   2. POST /api/v1/files/upload/init（带 `client_type:"web"`，按内容指纹复用会话）
   *      → 读 `received_chunks` 跳过已传分片；`already_exists` 直接成功；
   *   3. 逐片 POST /api/v1/files/upload/chunk（512KB；单片失败指数退避重试 5 次）
   *   4. POST /api/v1/files/upload/complete（`async_mode:true`）→ 每 2.5s 轮询 progress；
   *   5. 失败**不删除**服务端会话（保留供续传），只有 `signal` 触发（用户取消）才 DELETE。
   *
   * @param diskId 目标磁盘 ID
   * @param remotePath 远端目录路径
   * @param file 浏览器 File 对象
   * @param onProgress 进度回调 (uploaded, total)
   * @param options 取消信号 / 阶段状态回调
   */
  async uploadFile(
    diskId: number,
    remotePath: string,
    file: File,
    onProgress?: (uploaded: number, total: number) => void,
    options: UploadOptions = {},
  ): Promise<UploadResult> {
    // 远端列表核验（F46.6）：同名且大小一致 → 视为已上传完成，避免整文件重传。
    // 服务端不再自动判定（同大小不代表同内容），客户端按需求以「大小一致」为判据；
    // 列表读取失败（无读权限等）不影响正常上传，继续走 init。
    const remoteExists = await remoteFileMatches(
      diskId,
      remotePath,
      file.name,
      file.size,
    ).catch(() => false);
    if (remoteExists) {
      onProgress?.(file.size, file.size);
      options.onStatus?.({
        state: 'done',
        message: '远端已存在同名且大小一致的文件，无需重复上传',
        startChunk: Math.ceil(file.size / UPLOAD_CHUNK_SIZE),
      });
      return {
        resumed: false,
        startChunk: Math.ceil(file.size / UPLOAD_CHUNK_SIZE),
        alreadyExists: true,
        path: remotePath ? `${remotePath}/${file.name}` : file.name,
      };
    }

    const totalChunks = Math.ceil(file.size / UPLOAD_CHUNK_SIZE);

    // 计算文件 SHA256（后端 upload/init 强制要求，用于完整性校验与指纹复用）
    const fileHash = await computeFileSha256(file);
    assertNotAborted(options.signal, '上传已取消');

    // Step 1: 初始化上传（client_type = web，供服务端清理类型维度与旧客户端识别）
    const init = await httpClient.post<UploadInitResponse>(
      '/api/v1/files/upload/init',
      {
        disk_id: diskId,
        path: remotePath,
        filename: file.name,
        file_size: file.size,
        total_chunks: totalChunks,
        file_hash: fileHash,
        client_type: 'web',
      },
    );

    // 远端已存在同大小文件：无需上传（F46.6）
    if (init.already_exists) {
      onProgress?.(file.size, file.size);
      options.onStatus?.({
        state: 'done',
        message: '远端已存在同名且大小一致的文件，无需重复上传',
        startChunk: totalChunks,
      });
      return {
        resumed: false,
        startChunk: totalChunks,
        alreadyExists: true,
        path: init.already_path,
      };
    }

    const uploadId = init.upload_id;
    // 服务端明确「需重新开始」（旧会话已失败/过期）→ 从第 0 片重传
    const receivedSet = init.restart_required
      ? new Set<number>()
      : new Set(
        (init.received_chunks ?? []).filter(
          index => Number.isInteger(index) && index >= 0 && index < totalChunks,
        ),
      );
    const startChunk = firstMissingChunk(receivedSet, totalChunks);

    let uploaded = 0;
    for (const index of receivedSet) {
      uploaded += chunkLengthAt(file.size, index);
    }
    onProgress?.(uploaded, file.size);

    if (init.restart_required) {
      options.onStatus?.({
        state: 'resumed',
        message: '原分片已过期或已失败，将从第 0 片重新上传',
        startChunk: 0,
      });
    } else if (receivedSet.size > 0) {
      options.onStatus?.({
        state: 'resumed',
        message: `检测到未完成上传，从第 ${startChunk} 片继续（已收 ${receivedSet.size}/${totalChunks} 片）`,
        startChunk,
        receivedChunks: [...receivedSet].sort((a, b) => a - b),
      });
    } else {
      options.onStatus?.({
        state: 'uploading',
        message: `共 ${totalChunks} 片，开始上传`,
        startChunk: 0,
      });
    }

    let mergeSubmitted = false;
    try {
      // Step 2: 分片上传（跳过已收分片；单片失败指数退避重试）
      for (let index = 0; index < totalChunks; index += 1) {
        if (receivedSet.has(index)) continue;
        assertNotAborted(options.signal, '上传已取消');
        const start = index * UPLOAD_CHUNK_SIZE;
        const end = Math.min(start + UPLOAD_CHUNK_SIZE, file.size);
        const chunkData = new Uint8Array(await file.slice(start, end).arrayBuffer());
        const chunkBase64 = arrayBufferToBase64(chunkData);

        await uploadChunkWithRetry(
          uploadId,
          index,
          totalChunks,
          chunkBase64,
          options.signal,
        );

        uploaded += chunkData.byteLength;
        onProgress?.(uploaded, file.size);
      }

      // Step 3: 提交合并（强制异步，D27）
      mergeSubmitted = true;
      const complete = await httpClient.post<UploadCompleteResponse>(
        '/api/v1/files/upload/complete',
        { upload_id: uploadId, async_mode: true },
      );

      // 旧服务端没有 state 字段（同步完成）时才静默降级；本版新协议一律异步
      if (!complete.state) {
        onProgress?.(file.size, file.size);
        options.onStatus?.({ state: 'done', message: '上传完成', startChunk });
        return {
          resumed: startChunk > 0,
          startChunk,
          alreadyExists: false,
          path: complete.path,
        };
      }
      if (complete.state === 'done') {
        onProgress?.(file.size, file.size);
        options.onStatus?.({ state: 'done', message: '上传完成', startChunk });
        return {
          resumed: startChunk > 0,
          startChunk,
          alreadyExists: false,
          path: complete.path,
        };
      }
      if (complete.state === 'failed') {
        throw new ApiError(
          400,
          describeMergeError(complete.error, complete.missing_chunks),
        );
      }

      // Step 4: 轮询合并进度（queued / merging → done / failed）
      const progress = await pollMergeProgress(uploadId, startChunk, options);
      onProgress?.(file.size, file.size);
      options.onStatus?.({ state: 'done', message: '上传完成', startChunk });
      return {
        resumed: startChunk > 0,
        startChunk,
        alreadyExists: false,
        path: progress.path,
      };
    } catch (e) {
      // 用户取消才清理服务端会话；网络失败 / 合并失败一律保留分片供续传（F46.5）
      if (options.signal?.aborted && !mergeSubmitted) {
        await fileService.cancelUpload(uploadId).catch(() => undefined);
        throw new DOMException('上传已取消', 'AbortError');
      }
      if (options.signal?.aborted) {
        throw new DOMException('上传已取消', 'AbortError');
      }
      // 兜底（F46.6）：合并成功但响应丢失时，用远端列表核验大小，避免整文件重传
      const alreadyOnServer = await remoteFileMatches(
        diskId,
        remotePath,
        file.name,
        file.size,
      ).catch(() => false);
      if (alreadyOnServer) {
        onProgress?.(file.size, file.size);
        options.onStatus?.({
          state: 'done',
          message: '服务端已有完整文件，上传视为完成',
          startChunk,
        });
        return { resumed: startChunk > 0, startChunk, alreadyExists: true };
      }
      throw e;
    }
  },

  /** 用户明确取消上传：清理服务端会话与已传分片（唯一允许 DELETE 的路径） */
  async cancelUpload(uploadId: string): Promise<void> {
    await httpClient.delete(`/api/v1/files/upload/${uploadId}`);
  },

  /** 判断响应头是否表明远端文件已变化（供 UI / 测试复用） */
  describeRestartReason(
    meta: { version: string | null; size: number | null; offset: number } | null,
    headers: DownloadStreamHeaders,
    requestedOffset: number,
  ): string | null {
    if (!meta || requestedOffset <= 0) return null;
    if (headers.fileVersion !== null && headers.fileVersion !== meta.version) {
      return '文件已更新，将重新下载';
    }
    if (
      headers.streamTotal !== null
      && meta.size !== null
      && headers.streamTotal !== meta.size
    ) {
      return '文件大小已变化，将重新下载';
    }
    // 服务端没按请求偏移返回（旧服务端忽略 range_start）→ 不能续接，必须从头
    if (headers.streamOffset === null || headers.streamOffset !== requestedOffset) {
      return '服务端未按续传位置返回数据，将从第 0 字节重新下载';
    }
    return null;
  },
};

// ── 内部辅助 ──────────────────────────────────────────

/** 严格按字节切片计算某一片的明文长度 */
function chunkLengthAt(fileSize: number, index: number): number {
  const start = index * UPLOAD_CHUNK_SIZE;
  return Math.min(UPLOAD_CHUNK_SIZE, fileSize - start);
}

/** 第一个缺失分片索引（全部已收时返回 totalChunks） */
function firstMissingChunk(received: Set<number>, totalChunks: number): number {
  for (let index = 0; index < totalChunks; index += 1) {
    if (!received.has(index)) return index;
  }
  return totalChunks;
}

/** 把合并失败错误码翻译为可展示的中文（F47.4） */
export function describeMergeError(error?: string, missingChunks?: number[]): string {
  if (error === 'missing_chunks') {
    return missingChunks && missingChunks.length > 0
      ? `服务端缺少分片 ${missingChunks.join('、')}，请重试（客户端会只补缺失片）`
      : '服务端缺少分片，请重试（客户端会只补缺失片）';
  }
  if (error === 'hash_mismatch') {
    return '文件校验失败（SHA256 不一致），请重新上传整个文件';
  }
  if (error === 'io_error') {
    return '服务端磁盘写入失败（可能磁盘满 / 权限不足 / 文件被占用），请稍后重试';
  }
  return error || '服务端合并失败，请稍后重试';
}

/** 抛出 DOMException('AbortError')，供上层识别「用户取消」 */
function assertNotAborted(signal: AbortSignal | undefined, message: string): void {
  if (signal?.aborted) {
    throw new DOMException(message, 'AbortError');
  }
}

/** 可中断的 sleep（用于分片重试退避与合并进度轮询） */
function sleepWithAbort(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('操作已取消', 'AbortError'));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException('操作已取消', 'AbortError'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/**
 * 单片上传（指数退避重试，≥5 次）。
 * 失败只抛最后一次异常，**不越过调用方去删除会话**。
 */
async function uploadChunkWithRetry(
  uploadId: string,
  chunkIndex: number,
  totalChunks: number,
  chunkBase64: string,
  signal?: AbortSignal,
): Promise<void> {
  let lastError: unknown = null;
  for (let attempt = 1; attempt <= UPLOAD_CHUNK_MAX_ATTEMPTS; attempt += 1) {
    assertNotAborted(signal, '上传已取消');
    try {
      await httpClient.post('/api/v1/files/upload/chunk', {
        upload_id: uploadId,
        chunk_index: chunkIndex,
        total_chunks: totalChunks,
        chunk_data: chunkBase64,
      });
      return;
    } catch (e) {
      lastError = e;
      if (signal?.aborted) throw new DOMException('上传已取消', 'AbortError');
      if (attempt === UPLOAD_CHUNK_MAX_ATTEMPTS) break;
      const delay = UPLOAD_CHUNK_RETRY_BASE_DELAY_MS * 2 ** (attempt - 1);
      await sleepWithAbort(delay, signal);
    }
  }
  if (lastError instanceof Error) throw lastError;
  throw new Error('分片上传失败');
}

/** 轮询合并进度直到 done / failed；queued / merging 只在状态变化时回调一次 */
async function pollMergeProgress(
  uploadId: string,
  startChunk: number,
  options: UploadOptions,
): Promise<UploadProgressResponse> {
  const beganAt = Date.now();
  let lastState = '';
  while (true) {
    assertNotAborted(options.signal, '上传已取消');
    if (Date.now() - beganAt > UPLOAD_MERGE_MAX_WAIT_MS) {
      throw new Error('服务端合并等待超时；会话与分片已保留，请稍后重试');
    }
    await sleepWithAbort(UPLOAD_PROGRESS_POLL_INTERVAL_MS, options.signal);

    const progress = await httpClient.post<UploadProgressResponse>(
      '/api/v1/files/upload/progress',
      { upload_id: uploadId },
    );

    const state = progress.state ?? 'unknown';
    if (state === 'done') {
      return progress;
    }
    if (state === 'failed') {
      throw new ApiError(
        400,
        describeMergeError(progress.error, progress.missing_chunks),
      );
    }
    if (state === 'unknown') {
      throw new ApiError(
        400,
        '服务端未返回合并进度（进度记录可能已过期），请稍后重试或检查远端文件',
      );
    }
    if (state !== lastState) {
      lastState = state;
      const message = state === 'queued'
        ? `服务端合并排队中${progress.queue_position ? `（第 ${progress.queue_position} 位）` : ''}`
        : `服务端合并中，请勿关闭页面（${progress.percent ?? 0}%）`;
      options.onStatus?.({
        state: state === 'queued' ? 'queued' : 'merging',
        message,
        startChunk,
      });
    }
  }
}

/** 远端列表核验：目标文件存在且大小一致（F46.6 兜底） */
async function remoteFileMatches(
  diskId: number,
  remotePath: string,
  filename: string,
  fileSize: number,
): Promise<boolean> {
  const resp = await httpClient.get<{ files: FileItem[] }>(
    '/api/v1/files/list',
    { disk_id: diskId, path: remotePath },
  );
  return resp.files.some(
    item => !item.is_dir && item.name === filename && item.size === fileSize,
  );
}

/**
 * 把明文字节包装为 Blob。
 *
 * TS 5.7 的 `Uint8Array<ArrayBufferLike>` 不满足 `BlobPart`（要求 `ArrayBuffer` 视图），
 * 这里显式复制为独立的 `ArrayBuffer`，避免把 SharedArrayBuffer 视图带进 Blob。
 */
function blobFromBytes(chunks: Uint8Array[]): Blob {
  const parts = chunks.map((chunk) => {
    const buffer = new ArrayBuffer(chunk.byteLength);
    new Uint8Array(buffer).set(chunk);
    return buffer;
  });
  return new Blob(parts);
}

/** 拼接多段明文分块 */
function concatChunks(chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const result = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}

/** 取消未消费完的响应流（远端变化 / 偏移不符时避免继续下载脏数据） */
async function cancelStream(stream: ReadableStream<Uint8Array>): Promise<void> {
  try {
    await stream.cancel();
  } catch {
    // 已关闭 / 已锁定：忽略
  }
}

/** 触发浏览器「另存为」下载（Blob 仅停留在内存，不写服务端） */
function exportBlobToFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

/** 计算文件 SHA256 哈希（返回 64 位十六进制小写，与后端 hexdigest 一致；crypto.subtle 不可用时纯 JS 回退） */
async function computeFileSha256(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  if (isWebCryptoAvailable()) {
    const hashBuffer = await crypto.subtle.digest('SHA-256', buffer);
    const bytes = new Uint8Array(hashBuffer);
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  }
  // 非安全上下文（HTTP 域名）回退：@noble/hashes 纯 JS SHA-256
  const hash = sha256(new Uint8Array(buffer));
  return Array.from(hash, b => b.toString(16).padStart(2, '0')).join('');
}

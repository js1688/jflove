/**
 * 文件管理服务
 *
 * 对标桌面端 src/services/file_service.py。
 * 文件浏览 / 上传 / 下载 / 预览 / 重命名 / 移动 / 删除 / 创建目录。
 */

import { httpClient } from '../utils/http-client';
import { arrayBufferToBase64, isWebCryptoAvailable } from '../utils/crypto';
import { getSessionKey } from '../utils/session';
import { decryptStream } from '../utils/stream-frame';
import { sha256 } from '@noble/hashes/sha2.js';
import type { VirtualDisk, FileItem } from '../types/models';

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
  async createDir(diskId: number, path: string, dirName: string): Promise<void> {
    await httpClient.post('/api/v1/files/mkdir', {
      disk_id: diskId,
      path,
      dir_name: dirName,
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

  /** 删除文件/目录 */
  async delete(diskId: number, path: string): Promise<void> {
    await httpClient.delete('/api/v1/files/delete', {
      disk_id: diskId,
      path,
    });
  },

  /**
   * 流式下载文件（v1 纯加密数据帧，POST /files/download）。
   * 返回 ReadableStream，调用方通过 stream-frame 逐帧解密。
   * 注：不使用 /files/stream（v2）——其首帧为 meta 元数据帧，
   * 会导致下载文件头部混入 JSON 明文。
   */
  async downloadStream(
    diskId: number,
    path: string,
    filename: string,
  ): Promise<ReadableStream<Uint8Array>> {
    return httpClient.downloadStream('/api/v1/files/download', {
      disk_id: diskId,
      path,
      filename,
    });
  },

  /**
   * 下载文件并解密为完整字节（用于预览 / 保存）。
   * 复用 /files/download（v1 加密流），逐帧解密合并。
   */
  async downloadRaw(
    diskId: number,
    path: string,
    filename: string,
  ): Promise<Uint8Array> {
    const stream = await this.downloadStream(diskId, path, filename);
    const sessionKey = getSessionKey();
    if (!sessionKey) throw new Error('未建立加密会话');

    const chunks: Uint8Array[] = [];
    let total = 0;
    for await (const chunk of decryptStream(stream, sessionKey)) {
      chunks.push(chunk);
      total += chunk.length;
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
   * 分片上传文件。
   * 实现策略：
   *   1. POST /api/v1/files/upload/init → upload_id
   *   2. 循环 POST /api/v1/files/upload/chunk（每片 1MB）
   *   3. POST /api/v1/files/upload/complete
   *
   * @param diskId 目标磁盘 ID
   * @param remotePath 远端目录路径
   * @param file 浏览器 File 对象
   * @param onProgress 进度回调 (uploaded, total)
   */
  async uploadFile(
    diskId: number,
    remotePath: string,
    file: File,
    onProgress?: (uploaded: number, total: number) => void,
  ): Promise<void> {
    const CHUNK_SIZE = 1024 * 1024; // 1 MB
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

    // 计算文件 SHA256（后端 upload/init 强制要求，用于完整性校验）
    const fileHash = await computeFileSha256(file);

    // Step 1: 初始化上传
    const { upload_id } = await httpClient.post<{ upload_id: string }>(
      '/api/v1/files/upload/init',
      {
        disk_id: diskId,
        path: remotePath,
        filename: file.name,
        file_size: file.size,
        total_chunks: totalChunks,
        file_hash: fileHash,
      },
    );

    // Step 2: 分片上传（totalChunks 已在上面计算）
    let uploaded = 0;

    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const chunk = file.slice(start, end);
      const chunkData = await chunk.arrayBuffer();
      const chunkBase64 = arrayBufferToBase64(chunkData);

      await httpClient.post('/api/v1/files/upload/chunk', {
        upload_id,
        chunk_index: i,
        total_chunks: totalChunks,
        chunk_data: chunkBase64,
      });

      uploaded += chunkData.byteLength;
      onProgress?.(uploaded, file.size);
    }

    // Step 3: 完成上传
    await httpClient.post('/api/v1/files/upload/complete', { upload_id });
  },
};

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

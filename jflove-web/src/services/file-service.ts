/**
 * 文件管理服务
 *
 * 对标桌面端 src/services/file_service.py。
 * 文件浏览 / 上传 / 下载 / 预览 / 重命名 / 移动 / 删除 / 创建目录。
 */

import { httpClient } from '../utils/http-client';
import { arrayBufferToBase64 } from '../utils/crypto';
import type { VirtualDisk, FileItem, PreviewResult } from '../types/models';

export const fileService = {
  /** 获取用户可访问的虚拟磁盘列表 */
  async listDisks(): Promise<VirtualDisk[]> {
    return httpClient.get<VirtualDisk[]>('/api/v1/files/disks');
  },

  /** 列出磁盘内文件/目录 */
  async listFiles(diskId: number, path: string): Promise<FileItem[]> {
    const resp = await httpClient.post<{ files: FileItem[] }>(
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
    await httpClient.post('/api/v1/files/delete', {
      disk_id: diskId,
      path,
    });
  },

  /** 获取文件预览内容（文本/图片/Markdown） */
  async getPreview(
    diskId: number,
    path: string,
    filename: string,
  ): Promise<PreviewResult> {
    return httpClient.post<PreviewResult>('/api/v1/files/preview', {
      disk_id: diskId,
      path,
      filename,
    });
  },

  /** 流式下载/预览（返回 ReadableStream，调用方负责逐帧解密） */
  async downloadStream(
    diskId: number,
    path: string,
    filename: string,
  ): Promise<ReadableStream<Uint8Array>> {
    return httpClient.downloadStream('/api/v1/files/stream', {
      disk_id: diskId,
      path,
      filename,
    });
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

    // Step 1: 初始化上传
    const { upload_id } = await httpClient.post<{ upload_id: string }>(
      '/api/v1/files/upload/init',
      {
        disk_id: diskId,
        path: remotePath,
        filename: file.name,
        file_size: file.size,
      },
    );

    // Step 2: 分片上传
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
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

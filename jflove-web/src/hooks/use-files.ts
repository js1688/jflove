/**
 * 文件操作 Hook
 */

import { useCallback } from 'react';
import { useFileStore } from '../stores/file-store';
import { fileService } from '../services/file-service';
import { useTransferStore } from '../stores/transfer-store';
import { decryptStream } from '../utils/stream-frame';
import { getSessionKey } from '../utils/session';
import type { TaskKind } from '../types/models';

export function useFiles() {
  const store = useFileStore();
  const transfer = useTransferStore();

  /** 上传文件 */
  const uploadFiles = useCallback(async (
    diskId: number,
    remotePath: string,
    files: FileList | File[],
  ) => {
    const fileArray = Array.from(files);
    for (const file of fileArray) {
      const taskId = transfer.generateTaskId();

      transfer.addTask({
        id: taskId,
        kind: 'upload' as TaskKind,
        filename: file.name,
        localPath: file.name,
        diskId,
        remotePath,
        fileSize: file.size,
        transferred: 0,
        percent: 0,
        status: 'pending',
      });

      try {
        transfer.updateTask(taskId, { status: 'running' });
        await fileService.uploadFile(
          diskId,
          remotePath,
          file,
          (uploaded, total) => {
            transfer.updateTask(taskId, {
              transferred: uploaded,
              percent: Math.round((uploaded / total) * 100),
            });
          },
        );
        transfer.updateTask(taskId, { status: 'completed', percent: 100 });
      } catch (e) {
        transfer.updateTask(taskId, {
          status: 'failed',
          error: e instanceof Error ? e.message : '上传失败',
        });
      }
    }

    // 刷新文件列表
    await store.loadFiles(diskId, remotePath);
  }, [store, transfer]);

  /** 下载文件 */
  const downloadFile = useCallback(async (
    diskId: number,
    path: string,
    filename: string,
    fileSize: number,
  ) => {
    const taskId = transfer.generateTaskId();

    transfer.addTask({
      id: taskId,
      kind: 'download' as TaskKind,
      filename,
      localPath: filename,
      diskId,
      remotePath: path,
      fileSize,
      transferred: 0,
      percent: 0,
      status: 'pending',
    });

    try {
      transfer.updateTask(taskId, { status: 'running' });
      const stream = await fileService.downloadStream(diskId, path, filename);

      // 使用流式帧解析下载
      const sessionKey = getSessionKey();
      if (!sessionKey) throw new Error('未建立加密会话');

      const chunks: Uint8Array[] = [];
      let downloaded = 0;

      for await (const chunk of decryptStream(stream, sessionKey)) {
        chunks.push(chunk);
        downloaded += chunk.length;
        transfer.updateTask(taskId, {
          transferred: downloaded,
          percent: fileSize > 0 ? Math.round((downloaded / fileSize) * 100) : 0,
        });
      }

      // 合并所有分片并触发浏览器下载
      const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
      const result = new Uint8Array(totalLength);
      let offset = 0;
      for (const chunk of chunks) {
        result.set(chunk, offset);
        offset += chunk.length;
      }

      const blob = new Blob([result]);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      transfer.updateTask(taskId, { status: 'completed', percent: 100 });
    } catch (e) {
      transfer.updateTask(taskId, {
        status: 'failed',
        error: e instanceof Error ? e.message : '下载失败',
      });
    }
  }, [transfer]);

  return {
    ...store,
    uploadFiles,
    downloadFile,
  };
}

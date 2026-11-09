/**
 * 文件操作 Hook
 *
 * v1.9.0：
 *   - 上传走「断点续传 + 异步合并轮询」，续传/排队/合并阶段用 Toast 明确提示；
 *   - 下载走「OPFS / IndexedDB 分块续传」，失败保留本地临时数据，
 *     用户点「放弃续传」才清理（见 `abandonDownload`）；
 *   - 两端的「取消」通过 transfer-store 的取消回调中止在途请求：
 *     上传取消会清理服务端会话，下载取消保留已下分块供续传。
 */

import { useCallback } from 'react';
import { useFileStore } from '../stores/file-store';
import { fileService, type UploadStatusEvent } from '../services/file-service';
import { useTransferStore } from '../stores/transfer-store';
import { toast } from '../components/ui';
import type { TaskKind } from '../types/models';

/** 上传阶段提示：只在关键节点弹一次，避免每片都弹 */
function notifyUploadStatus(event: UploadStatusEvent): void {
  if (event.state === 'resumed') {
    toast.info('继续上传', event.message);
  } else if (event.state === 'queued') {
    toast.info('服务端合并排队中', event.message);
  } else if (event.state === 'merging') {
    toast.info('服务端合并中', event.message);
  }
}

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

      const controller = new AbortController();
      const unregister = transfer.registerCancelHandler(taskId, () => controller.abort());

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
          {
            signal: controller.signal,
            onStatus: notifyUploadStatus,
          },
        );
        transfer.updateTask(taskId, { status: 'completed', percent: 100 });
        toast.success('上传完成', file.name);
      } catch (e) {
        if (controller.signal.aborted) {
          // 用户取消：服务端会话已由 uploadFile 清理
          transfer.updateTask(taskId, { status: 'cancelled' });
        } else {
          const message = e instanceof Error ? e.message : '上传失败';
          transfer.updateTask(taskId, { status: 'failed', error: message });
          toast.error('上传失败', `${file.name}：${message}`);
        }
      } finally {
        unregister();
      }
    }

    // 刷新文件列表（失败单独提示，不影响上传结果）
    try {
      await store.loadFiles(diskId, remotePath);
    } catch (e) {
      const message = e instanceof Error ? e.message : '刷新文件列表失败';
      toast.error('刷新文件列表失败', message);
    }
  }, [store, transfer]);

  /** 下载文件（断点续传；失败保留本地分块，可重试续传） */
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

    const controller = new AbortController();
    const unregister = transfer.registerCancelHandler(taskId, () => controller.abort());

    try {
      transfer.updateTask(taskId, { status: 'running' });
      await fileService.downloadResumable(diskId, path, filename, {
        signal: controller.signal,
        onProgress: (received) => {
          transfer.updateTask(taskId, {
            transferred: received,
            percent: fileSize > 0 ? Math.round((received / fileSize) * 100) : 0,
          });
        },
        onStatus: (event) => {
          if (event.kind === 'resume') {
            toast.info('继续下载', event.message);
          } else if (event.kind === 'restart') {
            toast.info('重新下载', event.message);
          } else {
            // 不支持断点续传必须明确提示，不得静默
            toast.info('无法断点续传', event.message);
          }
        },
      });
      transfer.updateTask(taskId, { status: 'completed', percent: 100 });
      toast.success('下载完成', filename);
    } catch (e) {
      if (controller.signal.aborted) {
        // 用户取消：保留已下载分块供下次续传（不删除本地 part）
        transfer.updateTask(taskId, { status: 'cancelled' });
      } else {
        const message = e instanceof Error ? e.message : '下载失败';
        transfer.updateTask(taskId, { status: 'failed', error: message });
        toast.error('下载失败', `${filename}：${message}`);
      }
    } finally {
      unregister();
    }
  }, [transfer]);

  /**
   * 用户明确「放弃」某个下载：清理该文件的本地续传数据。
   * 失败/中断默认**不**清理（保留供续传），只有这个显式动作才删。
   */
  const abandonDownload = useCallback(async (
    diskId: number,
    path: string,
    filename: string,
  ) => {
    try {
      const cleared = await fileService.abandonDownload(diskId, path);
      if (cleared) {
        toast.success('已放弃续传数据', `${filename}：本地临时分块已清理`);
      } else {
        toast.info('无可清理的续传数据', `${filename}：当前浏览器不支持断点续传`);
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : '清理失败';
      toast.error('清理失败', `${filename}：${message}`);
    }
  }, []);

  return {
    ...store,
    uploadFiles,
    downloadFile,
    abandonDownload,
  };
}

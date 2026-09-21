/**
 * 文件操作 Hook（桌面端）
 *
 * ## 与 Web 端 `jflove-web/src/hooks/use-files.ts` 的关键差异
 *
 * Web 端在渲染层处理字节：上传时把 `File` 对象分片并由 JS 加密上传；
 * 下载时用 `decryptStream` + `session_key` 逐帧解密，再拼 Blob 触发浏览器下载。
 *
 * 桌面端**不做这些**（安全宪法 §9.4/§9.5：加密链路只有 Python 一份实现），
 * 平台差异全部收敛在本 Hook：
 *
 * | 步骤 | Web | 桌面端 |
 * | --- | --- | --- |
 * | 选文件 | 隐藏 `<input type="file">` | 原生「打开」对话框（`dialogs.open_files`） |
 * | 上传 | JS 分片 + 加密 | 桥 `files.upload`（Python 分片 + SHA256 + 加密） |
 * | 选保存位置 | 浏览器下载目录 | 原生「另存为」对话框（`dialogs.save_file`） |
 * | 下载 | JS 逐帧解密 + Blob | 桥 `files.download`（Python 解密后落盘） |
 * | 进度 | 上传/下载回调 | 桥推送的 `files.upload_progress` / `files.download_progress` 事件 |
 */

import { useCallback, useEffect, useRef } from 'react';
import { useFileStore } from '../stores/file-store';
import { useTransferStore } from '../stores/transfer-store';
import { fileService } from '../services/file-service';
import { onBridgeEvent } from '../utils/desktop-bridge';
import type { TaskKind } from '../types/models';

/** 原生「打开」对话框选出的本地文件 */
export interface LocalFile {
  path: string;
  name: string;
  size: number;
}

export function useFiles() {
  const store = useFileStore();
  const transfer = useTransferStore();
  //: 当前正在传输的任务 id（进度事件按此归属）
  const activeTaskId = useRef<string | null>(null);

  // 桥推送的传输进度 → 更新传输任务（N10 的传输任务页直接复用这些任务）
  useEffect(() => {
    const offUp = onBridgeEvent('files.upload_progress', (payload) => {
      const p = payload as { sent?: number; total?: number } | null;
      const id = activeTaskId.current;
      if (!id || !p) return;
      const total = Number(p.total ?? 0);
      const sent = Number(p.sent ?? 0);
      transfer.updateTask(id, {
        transferred: sent,
        percent: total > 0 ? Math.round((sent / total) * 100) : 0,
      });
    });
    const offDown = onBridgeEvent('files.download_progress', (payload) => {
      const p = payload as { received?: number; total?: number } | null;
      const id = activeTaskId.current;
      if (!id || !p) return;
      const total = Number(p.total ?? 0);
      const received = Number(p.received ?? 0);
      transfer.updateTask(id, {
        transferred: received,
        percent: total > 0 ? Math.round((received / total) * 100) : 0,
      });
    });
    return () => {
      offUp();
      offDown();
    };
  }, [transfer]);

  /**
   * 上传：先弹**原生文件对话框**，再交给 Python 分片上传。
   *
   * 参数与 Web 端同名（页面逐字复用），但第三个参数是对话框选出的本地文件描述，
   * 而不是浏览器 `File` 对象。
   */
  const uploadFiles = useCallback(
    async (diskId: number, remotePath: string, files: LocalFile[]) => {
      for (const file of files) {
        const taskId = transfer.generateTaskId();
        activeTaskId.current = taskId;

        transfer.addTask({
          id: taskId,
          kind: 'upload' as TaskKind,
          filename: file.name,
          localPath: file.path,
          diskId,
          remotePath,
          fileSize: file.size,
          transferred: 0,
          percent: 0,
          status: 'pending',
        });

        try {
          transfer.updateTask(taskId, { status: 'running' });
          await fileService.uploadFile(diskId, remotePath, file.path);
          transfer.updateTask(taskId, { status: 'completed', percent: 100 });
        } catch (e) {
          transfer.updateTask(taskId, {
            status: 'failed',
            error: e instanceof Error ? e.message : '上传失败',
          });
        } finally {
          activeTaskId.current = null;
        }
      }

      // 刷新文件列表
      await store.loadFiles(diskId, remotePath);
    },
    [store, transfer],
  );

  /** 弹出原生文件对话框并上传（页面「上传」按钮直接调用） */
  const pickAndUpload = useCallback(
    async (diskId: number, remotePath: string) => {
      const picked = await fileService.pickLocalFiles();
      if (picked.length === 0) return;
      await uploadFiles(diskId, remotePath, picked);
    },
    [uploadFiles],
  );

  /**
   * 下载：先弹**原生另存为对话框**，再由 Python 解密后落盘。
   *
   * 用户取消对话框时不产生任务（避免出现"失败"的空任务）。
   */
  const downloadFile = useCallback(
    async (diskId: number, path: string, filename: string, fileSize: number) => {
      const savePath = await fileService.pickSavePath(filename);
      if (!savePath) return;

      const taskId = transfer.generateTaskId();
      activeTaskId.current = taskId;

      transfer.addTask({
        id: taskId,
        kind: 'download' as TaskKind,
        filename,
        localPath: savePath,
        diskId,
        remotePath: path,
        fileSize,
        transferred: 0,
        percent: 0,
        status: 'pending',
      });

      try {
        transfer.updateTask(taskId, { status: 'running' });
        await fileService.download(diskId, path, savePath);
        transfer.updateTask(taskId, { status: 'completed', percent: 100 });
      } catch (e) {
        transfer.updateTask(taskId, {
          status: 'failed',
          error: e instanceof Error ? e.message : '下载失败',
        });
      } finally {
        activeTaskId.current = null;
      }
    },
    [transfer],
  );

  return {
    ...store,
    uploadFiles,
    pickAndUpload,
    downloadFile,
  };
}

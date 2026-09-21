/**
 * 文件操作 Hook（桌面端）
 *
 * ## 与 Web 端的差异
 *
 * | 步骤 | Web 端 | 桌面端 |
 * | --- | --- | --- |
 * | 选文件 | 隐藏 `<input type="file">` | 原生「打开」对话框（`dialogs.open_files`） |
 * | 上传 | JS 分片 + 加密 | 桥 `files.upload`（Python 分片 + SHA256 + 加密 + 进度） |
 * | 选保存位置 | 浏览器下载目录 | 原生「另存为」对话框（`dialogs.save_file`） |
 * | 下载 | JS 逐帧解密 + Blob | 桥 `files.download`（Python 解密后落盘） |
 * | **任务与取消** | 渲染层自己持有任务、取消只改本地状态 | **交给 Python 的 `TransferManager`**（`transfer.*`），真实可取消 |
 *
 * 说明：上传/下载仍走 `files.*`（那是**执行**通路），但**任务对象与取消**由
 * `transfer.*` 负责 —— 渲染层只是它的投影（见 `stores/transfer-store.ts`）。
 * 这样「取消」是真的（工作线程在取消检查点退出），而不是只把界面改成"已取消"。
 */

import { useCallback, useEffect, useRef } from 'react';
import { useFileStore } from '../stores/file-store';
import { useTransferStore } from '../stores/transfer-store';
import { fileService } from '../services/file-service';
import { callBridge, onBridgeEvent } from '../utils/desktop-bridge';
import type { TaskKind, TransferTask } from '../types/models';

/** 原生「打开」对话框选出的本地文件 */
export interface LocalFile {
  path: string;
  name: string;
  size: number;
}

export function useFiles() {
  const store = useFileStore();
  const transfer = useTransferStore();
  //: 当前正在浏览的目录（上传/下载完成后据此刷新列表）
  const currentRef = useRef<{ diskId: number; path: string }>({ diskId: 0, path: '' });

  // 任务事件由 transfer-store 统一消费；这里只处理"传完了刷新文件列表"
  useEffect(() => {
    return onBridgeEvent('transfer.updated', (payload) => {
      const task = payload as TransferTask | null;
      const cur = currentRef.current;
      if (!task || task.status !== 'completed') return;
      if (Number(task.diskId) !== cur.diskId) return;
      void store.loadFiles(cur.diskId, cur.path);
    });
  }, [store]);

  /**
   * 上传：先弹**原生文件对话框**，再交给 Python 分片上传。
   *
   * 第三个参数是对话框选出的本地文件描述（不是浏览器 `File` 对象）。
   */
  const uploadFiles = useCallback(
    async (diskId: number, remotePath: string, files: LocalFile[]) => {
      currentRef.current = { diskId, path: remotePath };
      for (const file of files) {
        try {
          // 提交后立即返回任务 id；进度/完成/失败都经 transfer.* 事件回流
          await callBridge<{ id: string }>('transfer', 'upload', {
            disk_id: diskId,
            rel_dir: remotePath,
            local_path: file.path,
          });
        } catch (e) {
          transfer.addTask({
            id: transfer.generateTaskId(),
            kind: 'upload' as TaskKind,
            filename: file.name,
            localPath: file.path,
            diskId,
            remotePath,
            fileSize: file.size,
            transferred: 0,
            percent: 0,
            status: 'failed',
            error: e instanceof Error ? e.message : '提交上传失败',
          } as TransferTask);
        }
      }
      // 上传是异步的：完成后由 transfer.updated 事件触发列表刷新
    },
    [transfer],
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
   * 下载：先弹**原生另存为对话框**，再由 Python 解密后落盘（可真实取消）。
   *
   * 用户取消对话框时不产生任务。
   */
  const downloadFile = useCallback(
    async (diskId: number, path: string, filename: string, fileSize: number) => {
      const savePath = await fileService.pickSavePath(filename);
      if (!savePath) return;
      currentRef.current = { diskId, path: path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '' };
      try {
        await callBridge<{ id: string }>('transfer', 'download', {
          disk_id: diskId,
          rel_path: path,
          save_path: savePath,
          filename,
          file_size: fileSize,
        });
      } catch (e) {
        transfer.addTask({
          id: transfer.generateTaskId(),
          kind: 'download' as TaskKind,
          filename,
          localPath: savePath,
          diskId,
          remotePath: path,
          fileSize,
          transferred: 0,
          percent: 0,
          status: 'failed',
          error: e instanceof Error ? e.message : '提交下载失败',
        } as TransferTask);
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

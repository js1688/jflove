/**
 * 文件浏览状态管理
 */

import { create } from 'zustand';
import { fileService } from '../services/file-service';
import type { VirtualDisk, FileItem } from '../types/models';

interface FileState {
  // 磁盘列表
  disks: VirtualDisk[];
  disksLoading: boolean;

  // 当前浏览
  currentDiskId: number | null;
  currentPath: string;
  currentFiles: FileItem[];
  filesLoading: boolean;
  canWrite: boolean;

  // 预览上下文（不走 URL，避免业务数据明文暴露）
  previewTarget: { path: string; name: string; size?: number } | null;

  // 排序
  sortBy: 'name' | 'size' | 'modified_at';
  sortAsc: boolean;

  // 操作
  loadDisks: () => Promise<void>;
  loadFiles: (diskId: number, path: string) => Promise<void>;
  setSortBy: (field: 'name' | 'size' | 'modified_at') => void;
  toggleSortOrder: () => void;
  setPreviewTarget: (target: { path: string; name: string; size?: number } | null) => void;

  // 文件操作
  createDir: (diskId: number, path: string, dirName: string) => Promise<void>;
  renameFile: (diskId: number, path: string, newName: string) => Promise<void>;
  moveFile: (diskId: number, srcPath: string, dstDirPath: string) => Promise<void>;
  deleteFile: (diskId: number, path: string) => Promise<void>;
}

export const useFileStore = create<FileState>((set, get) => ({
  disks: [],
  disksLoading: false,
  currentDiskId: null,
  currentPath: '',
  currentFiles: [],
  filesLoading: false,
  canWrite: false,
  previewTarget: null,
  sortBy: 'name',
  sortAsc: true,

  loadDisks: async () => {
    set({ disksLoading: true });
    try {
      const disks = await fileService.listDisks();
      set({ disks, disksLoading: false });
    } catch (e) {
      set({ disksLoading: false });
      // 保留原始错误信息（如 405 后端版本提示），便于页面明确展示
      throw e instanceof Error ? e : new Error('加载磁盘列表失败');
    }
  },

  loadFiles: async (diskId, path) => {
    set({ filesLoading: true, currentDiskId: diskId, currentPath: path });
    try {
      const files = await fileService.listFiles(diskId, path);
      // 后端不返回 path 字段，前端按"当前目录 + 名称"拼接相对路径（与桌面端一致）
      const items: FileItem[] = files.map(f => ({
        ...f,
        path: path ? `${path}/${f.name}` : f.name,
      }));
      // 判断写权限
      const disks = get().disks;
      const disk = disks.find(d => d.id === diskId);
      set({
        currentFiles: items,
        canWrite: disk?.can_write ?? false,
        filesLoading: false,
      });
    } catch (e) {
      set({ filesLoading: false });
      throw e instanceof Error ? e : new Error('加载文件列表失败');
    }
  },

  setSortBy: (field) => set({ sortBy: field }),
  toggleSortOrder: () => set(s => ({ sortAsc: !s.sortAsc })),

  setPreviewTarget: (target) => set({ previewTarget: target }),

  createDir: async (diskId, path, dirName) => {
    await fileService.createDir(diskId, path, dirName);
    await get().loadFiles(diskId, path);
  },

  renameFile: async (diskId, path, newName) => {
    await fileService.rename(diskId, path, newName);
    await get().loadFiles(diskId, get().currentPath);
  },

  moveFile: async (diskId, srcPath, dstDirPath) => {
    await fileService.move(diskId, srcPath, dstDirPath);
    await get().loadFiles(diskId, get().currentPath);
  },

  deleteFile: async (diskId, path) => {
    await fileService.delete(diskId, path);
    await get().loadFiles(diskId, get().currentPath);
  },
}));

import { useEffect, useState, useMemo, useRef } from 'react';
import { useParams, useNavigate } from 'react-router';
import { useFileStore } from '../stores/file-store';
import { useFiles } from '../hooks/use-files';
import { PageHeader } from '../components/PageHeader';
import { PathBreadcrumb } from '../components/PathBreadcrumb';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { DirTreeModal } from '../components/DirTreeModal';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { EmptyState } from '../components/EmptyState';
import { ErrorBanner } from '../components/ErrorBanner';
import type { FileItem } from '../types/models';

/** 格式化文件大小 */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/** 格式化修改时间 */
function formatTime(ts: number): string {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

/** 文件类型图标 */
function fileIcon(item: FileItem): string {
  if (item.is_dir) return '📁';
  const ext = item.name.split('.').pop()?.toLowerCase() || '';
  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'];
  const videoExts = ['mp4', 'webm', 'mkv', 'avi', 'mov'];
  const audioExts = ['mp3', 'wav', 'flac', 'aac', 'ogg'];
  const docExts = ['md', 'txt', 'json', 'xml', 'yaml', 'csv', 'pdf'];
  if (imageExts.includes(ext)) return '🖼️';
  if (videoExts.includes(ext)) return '🎬';
  if (audioExts.includes(ext)) return '🎵';
  if (docExts.includes(ext)) return '📄';
  return '📎';
}

export function DiskBrowserPage() {
  const { diskId } = useParams<{ diskId: string }>();
  const navigate = useNavigate();
  const store = useFileStore();
  const { uploadFiles, downloadFile } = useFiles();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [path, setPath] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<{
    x: number; y: number; item: FileItem;
  } | null>(null);
  const [renameTarget, setRenameTarget] = useState<FileItem | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<FileItem | null>(null);
  const [moveTarget, setMoveTarget] = useState<FileItem | null>(null);
  const [newDirName, setNewDirName] = useState('');
  const [showNewDir, setShowNewDir] = useState(false);
  // 拖拽上传状态（PC 端）
  const [isDragOver, setIsDragOver] = useState(false);

  const numDiskId = Number(diskId);
  const disk = store.disks.find(d => d.id === numDiskId);
  const diskName = disk?.name || `磁盘 ${diskId}`;

  // 加载文件列表（若磁盘列表尚未加载则先加载，用于磁盘名与写权限判断）
  useEffect(() => {
    if (!diskId) return;
    const s = useFileStore.getState();
    if (s.disks.length === 0) {
      s.loadDisks()
        .then(() => useFileStore.getState().loadFiles(Number(diskId), path))
        .catch(() => {});
    } else {
      // 使用 getState 避免把 store 整体引用加入依赖（zustand 状态变化会导致重复请求）
      useFileStore.getState().loadFiles(Number(diskId), path).catch(() => {});
    }
  }, [diskId, path]);

  // 排序
  const sortedFiles = useMemo(() => {
    const files = [...store.currentFiles];
    files.sort((a, b) => {
      // 目录始终排前面
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      let cmp = 0;
      switch (store.sortBy) {
        case 'name': cmp = a.name.localeCompare(b.name); break;
        case 'size': cmp = a.size - b.size; break;
        case 'modified_at': cmp = (a.modified_at || 0) - (b.modified_at || 0); break;
      }
      return store.sortAsc ? cmp : -cmp;
    });
    return files;
  }, [store.currentFiles, store.sortBy, store.sortAsc]);

  // 导航
  const handleNavigate = (newPath: string) => setPath(newPath);
  const handleBackToDisks = () => navigate('/files');
  const handleEnterDir = (item: FileItem) => {
    if (item.is_dir) setPath(item.path);
  };
  const handlePreview = (item: FileItem) => {
    // 预览上下文走 store（不放入 URL，避免业务数据明文暴露，见 §9.1.4）
    useFileStore.getState().setPreviewTarget({ path: item.path, name: item.name, size: item.size });
    navigate(`/files/${diskId}/preview`);
  };

  // 文件操作
  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    await uploadFiles(numDiskId, path, files);
  };

  const handleRename = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    try {
      await store.renameFile(numDiskId, renameTarget.path, renameValue.trim());
      setRenameTarget(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '重命名失败');
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await store.deleteFile(numDiskId, deleteTarget.path);
      setDeleteTarget(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败');
    }
  };

  const handleMove = async (dstPath: string) => {
    if (!moveTarget) return;
    try {
      await store.moveFile(numDiskId, moveTarget.path, dstPath);
      setMoveTarget(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '移动失败');
    }
  };

  const handleCreateDir = async () => {
    if (!newDirName.trim()) return;
    try {
      await store.createDir(numDiskId, path, newDirName.trim());
      setNewDirName('');
      setShowNewDir(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建目录失败');
    }
  };

  // 上下文菜单
  const handleContextMenu = (e: React.MouseEvent, item: FileItem) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, item });
  };

  // 拖拽上传处理（PC 端）
  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    if (store.canWrite) setIsDragOver(true);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (store.canWrite) setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (!store.canWrite) return;
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleUpload(e.dataTransfer.files);
    }
  };

  useEffect(() => {
    const close = () => setContextMenu(null);
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, []);

  if (!diskId) return <EmptyState title="无效的磁盘 ID" />;

  return (
    <div>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <PageHeader
        title={diskName}
        onBack={handleBackToDisks}
        actions={
          store.canWrite ? (
            <>
              <button
                onClick={() => fileInputRef.current?.click()}
                className="px-3 py-1.5 text-xs bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
              >
                📤 上传
              </button>
              <button
                onClick={() => setShowNewDir(true)}
                className="px-3 py-1.5 text-xs border border-gray-200 rounded-lg hover:bg-gray-50"
              >
                📁 新建目录
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={e => handleUpload(e.target.files)}
              />
            </>
          ) : undefined
        }
      />

      <PathBreadcrumb
        diskName={diskName}
        path={path}
        onNavigate={handleNavigate}
        onBackToDisks={handleBackToDisks}
      />

      {/* 排序工具栏 */}
      <div className="flex items-center gap-2 px-4 py-2 text-xs text-gray-400 border-b border-gray-50 bg-white">
        <span>排序：</span>
        {(['name', 'size', 'modified_at'] as const).map(field => (
          <button
            key={field}
            onClick={() => {
              if (store.sortBy === field) store.toggleSortOrder();
              else store.setSortBy(field);
            }}
            className={`px-2 py-0.5 rounded ${
              store.sortBy === field ? 'bg-indigo-50 text-indigo-600' : 'hover:bg-gray-50'
            }`}
          >
            {field === 'name' ? '名称' : field === 'size' ? '大小' : '时间'}
            {store.sortBy === field && (store.sortAsc ? ' ↑' : ' ↓')}
          </button>
        ))}
      </div>

      {/* 新建目录弹窗 */}
      {showNewDir && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h3 className="font-semibold text-gray-800 mb-3">新建目录</h3>
            <input
              type="text"
              value={newDirName}
              onChange={e => setNewDirName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreateDir()}
              placeholder="目录名称"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 mb-4"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => { setShowNewDir(false); setNewDirName(''); }}
                className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
              >
                取消
              </button>
              <button
                onClick={handleCreateDir}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
              >
                创建
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 文件列表（支持拖拽上传，PC 端） */}
      <div
        className={`p-2 transition-colors ${isDragOver ? 'bg-indigo-50 ring-2 ring-indigo-300 rounded-lg' : ''}`}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {isDragOver && (
          <div className="flex flex-col items-center justify-center py-12 text-indigo-500">
            <span className="text-3xl mb-2">📤</span>
            <span className="text-sm font-medium">松开以上传到当前目录</span>
          </div>
        )}

        {store.filesLoading && <LoadingSpinner />}

        {!store.filesLoading && sortedFiles.length === 0 && (
          <EmptyState icon="📂" title="此目录为空" />
        )}

        {!store.filesLoading && sortedFiles.map(item => (
          <div
            key={item.path}
            onClick={() => item.is_dir ? handleEnterDir(item) : handlePreview(item)}
            onContextMenu={e => handleContextMenu(e, item)}
            className="flex items-center gap-3 px-4 py-3 hover:bg-gray-50 rounded-lg cursor-pointer transition-colors border-b border-gray-50"
          >
            <span className="text-xl w-8 text-center">{fileIcon(item)}</span>
            <div className="flex-1 min-w-0">
              <div className="text-sm text-gray-800 truncate">{item.name}</div>
              <div className="text-xs text-gray-400">
                {!item.is_dir && `${formatSize(item.size)} · `}
                {formatTime(item.modified_at)}
              </div>
            </div>
            <span className="text-gray-300 text-xs">→</span>
          </div>
        ))}
      </div>

      {/* 上下文菜单 */}
      {contextMenu && (
        <div
          className="fixed z-50 bg-white rounded-xl shadow-lg border border-gray-100 py-1 min-w-[160px]"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          {!contextMenu.item.is_dir && (
            <>
              <button
                onClick={() => {
                  downloadFile(numDiskId, contextMenu.item.path, contextMenu.item.name, contextMenu.item.size);
                  setContextMenu(null);
                }}
                className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
              >
                ⬇️ 下载
              </button>
              <button
                onClick={() => {
                  handlePreview(contextMenu.item);
                  setContextMenu(null);
                }}
                className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
              >
                👁️ 预览
              </button>
              <div className="border-t border-gray-100 my-1" />
            </>
          )}
          {store.canWrite && (
            <>
              <button
                onClick={() => {
                  setRenameTarget(contextMenu.item);
                  setRenameValue(contextMenu.item.name);
                  setContextMenu(null);
                }}
                className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
              >
                ✏️ 重命名
              </button>
              <button
                onClick={() => {
                  setMoveTarget(contextMenu.item);
                  setContextMenu(null);
                }}
                className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
              >
                📦 移动到…
              </button>
              <div className="border-t border-gray-100 my-1" />
              <button
                onClick={() => {
                  setDeleteTarget(contextMenu.item);
                  setContextMenu(null);
                }}
                className="w-full px-4 py-2 text-left text-sm text-red-500 hover:bg-red-50"
              >
                🗑️ 删除
              </button>
            </>
          )}
        </div>
      )}

      {/* 重命名弹窗 */}
      {renameTarget && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h3 className="font-semibold text-gray-800 mb-3">重命名</h3>
            <input
              type="text"
              value={renameValue}
              onChange={e => setRenameValue(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleRename()}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 mb-4"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setRenameTarget(null)}
                className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
              >
                取消
              </button>
              <button
                onClick={handleRename}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
              >
                确认
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认 */}
      {deleteTarget && (
        <ConfirmDialog
          title="确认删除"
          message={`确定要删除「${deleteTarget.name}」吗？此操作不可撤销。`}
          confirmLabel="删除"
          danger
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {/* 移动到… */}
      {moveTarget && disk && (
        <DirTreeModal
          diskId={numDiskId}
          diskName={diskName}
          excludePath={moveTarget.is_dir ? moveTarget.path : undefined}
          onSelect={handleMove}
          onClose={() => setMoveTarget(null)}
        />
      )}
    </div>
  );
}

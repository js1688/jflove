import { useEffect, useState, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router';
import { useFileStore } from '../stores/file-store';
import { onBridgeEvent } from '../utils/desktop-bridge';
import { repairService } from '../services/repair-service';
import { useFiles } from '../hooks/use-files';
import { PageHeader } from '../components/PageHeader';
import { PathBreadcrumb } from '../components/PathBreadcrumb';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { DirTreeModal } from '../components/DirTreeModal';
import { ErrorBanner } from '../components/ErrorBanner';
import {
  Badge,
  Button,
  EmptyState,
  Icon,
  ListSkeleton,
  Modal,
  toast,
  type IconName,
} from '../components/ui';
import { fileVisual } from '../utils/file-visual';
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

/** v1.4.2：可发起修复的媒体扩展名（对齐桌面/移动端） */
const MEDIA_EXTS = new Set([
  'mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', 'm4v', 'mpg', 'mpeg', 'ts', '3gp',
  'mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'wma', 'opus',
]);
function isMediaFile(name: string): boolean {
  const dot = name.lastIndexOf('.');
  if (dot < 0) return false;
  return MEDIA_EXTS.has(name.slice(dot + 1).toLowerCase());
}

/** 上下文菜单项（v1.5.0 新增：统一菜单行，emoji 换矢量图标） */
function MenuItem({
  icon,
  label,
  onClick,
  danger = false,
}: {
  icon: IconName;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="flex w-full items-center gap-2.5 rounded-sm px-3 py-2 text-left text-[13px] transition-colors hover:bg-hover"
      style={danger ? { color: 'var(--danger-700)' } : undefined}
    >
      <Icon name={icon} size="sm" />
      {label}
    </button>
  );
}

export function DiskBrowserPage() {
  const { diskId } = useParams<{ diskId: string }>();
  const navigate = useNavigate();
  const store = useFileStore();
  // 桌面端：上传/下载都走**原生对话框**（选文件 / 另存为），
  // 字节与加密全部在 Python 侧完成 —— 不再需要隐藏的 <input type="file">
  const { pickAndUpload, uploadFiles, downloadFile } = useFiles();

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
  // v1.5.0：操作结果改用全站统一 Toast（原先本页自建一套 fixed 定位提示）

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

  /** v1.4.2：右键「修复损坏媒体」——创建异步修复任务 */
  const handleRepair = async (item: FileItem) => {
    try {
      const dir = item.path.includes('/') ? item.path.slice(0, item.path.lastIndexOf('/')) : '';
      await repairService.create(numDiskId, dir, item.name);
      toast.success('已加入修复队列', '可在「修复中心」查看进度');
    } catch (e) {
      toast.error('发起修复失败', e instanceof Error ? e.message : String(e));
    }
  };

  // 文件操作
  const handleUpload = async () => {
    // 桌面端：先弹原生文件对话框，选完再上传（Python 侧分片 + 加密 + 进度事件）
    await pickAndUpload(numDiskId, path);
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

  // 拖放上传：**由 Python 侧拦截**（`DropAwareWebView.dropEvent` 读 QMimeData.urls()，
  // 因为浏览器安全模型下渲染层拿不到真实本地路径），再把文件描述推回来。
  useEffect(() => {
    return onBridgeEvent('dnd.files_dropped', (payload) => {
      const files = (payload as { files?: { path: string; name: string; size: number }[] } | null)
        ?.files;
      // 诊断（自动化可读）：为何没走上传
      (window as unknown as Record<string, unknown>).__JF_DND__ = {
        received: Array.isArray(files) ? files.length : 0,
        payload,
        canWrite: store.canWrite,
        diskId: numDiskId,
        dirPath: path,
      };
      if (!files || files.length === 0) return;
      if (!store.canWrite) {
        toast.error('当前目录不可写', '无法上传文件');
        return;
      }
      void uploadFiles(numDiskId, path, files);
    });
  }, [store.canWrite, uploadFiles, numDiskId, path]);

  const handleDrop = (e: React.DragEvent) => {
    // 真正的工作在 Python 侧完成；这里只负责视觉状态
    e.preventDefault();
    setIsDragOver(false);
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
        subtitle={path ? `当前位置：${path}` : '根目录'}
        actions={
          store.canWrite ? (
            <>
              <Button
                variant="primary"
                size="sm"
                icon="upload"
                onClick={() => void handleUpload()}
              >
                上传
              </Button>
              <Button size="sm" icon="folder" onClick={() => setShowNewDir(true)}>
                新建目录
              </Button>
            </>
          ) : (
            <Badge tone="neutral" icon="locked">
              只读
            </Badge>
          )
        }
      />

      <PathBreadcrumb
        diskName={diskName}
        path={path}
        onNavigate={handleNavigate}
        onBackToDisks={handleBackToDisks}
      />

      {/* 排序工具栏（v1.5.0：箭头字符换矢量图标，配色走令牌） */}
      <div className="flex items-center gap-1 border-b border-line-subtle bg-surface px-4 py-2">
        <span className="mr-1 text-[11.5px] text-subtle">排序</span>
        {(['name', 'size', 'modified_at'] as const).map(field => {
          const on = store.sortBy === field;
          return (
            <button
              key={field}
              type="button"
              onClick={() => {
                if (on) store.toggleSortOrder();
                else store.setSortBy(field);
              }}
              data-on={on}
              className="tb !w-auto gap-1 px-2"
            >
              {field === 'name' ? '名称' : field === 'size' ? '大小' : '时间'}
              {on && (
                <Icon
                  name="expand"
                  size="sm"
                  className={store.sortAsc ? '' : 'rotate-180'}
                />
              )}
            </button>
          );
        })}
      </div>

      {/* 新建目录弹窗 */}
      <Modal
        open={showNewDir}
        title="新建目录"
        icon="folder"
        width={420}
        onClose={() => {
          setShowNewDir(false);
          setNewDirName('');
        }}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setShowNewDir(false);
                setNewDirName('');
              }}
            >
              取消
            </Button>
            <Button variant="primary" icon="checked" onClick={handleCreateDir}>
              创建
            </Button>
          </>
        }
      >
        <input
          type="text"
          value={newDirName}
          onChange={e => setNewDirName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleCreateDir()}
          placeholder="目录名称"
          className="input"
          autoFocus
        />
      </Modal>

      {/* 文件列表（支持拖拽上传，PC 端） */}
      <div
        className={[
          'p-2 transition-colors',
          isDragOver && 'rounded-lg bg-brand-50 ring-2 ring-brand-300',
        ]
          .filter(Boolean)
          .join(' ')}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {isDragOver && (
          <div className="flex flex-col items-center justify-center py-12 text-brand-500">
            <Icon name="upload" size="xl" strokeWidth={1.5} className="mb-2" />
            <span className="text-[13px] font-medium">松开以上传到当前目录</span>
            <span className="mt-1 text-[11.5px] text-subtle">
              分片 64 KB · ChaCha20-Poly1305 端到端加密
            </span>
          </div>
        )}

        {store.filesLoading && <ListSkeleton rows={6} />}

        {!store.filesLoading && sortedFiles.length === 0 && (
          <EmptyState icon="files" title="此目录为空" description="可上传文件或新建目录" />
        )}

        {!store.filesLoading &&
          sortedFiles.map(item => {
            const v = fileVisual(item.name, item.is_dir);
            return (
              <div
                key={item.path}
                onClick={() => (item.is_dir ? handleEnterDir(item) : handlePreview(item))}
                onContextMenu={e => handleContextMenu(e, item)}
                className="list-row cursor-pointer rounded-lg border-b border-line-subtle"
              >
                <span className={`file-ico file-ico-${v.tone}`}>
                  <Icon name={v.icon} size="lg" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-medium text-fg">{item.name}</div>
                  <div className="tabular text-[11.5px] text-subtle">
                    {!item.is_dir && `${formatSize(item.size)} · `}
                    {formatTime(item.modified_at)}
                  </div>
                </div>
                <Icon name="next" size="sm" className="shrink-0 text-subtle" />
              </div>
            );
          })}
      </div>

      {/* 上下文菜单（v1.5.0：全部 emoji 换矢量图标） */}
      {contextMenu && (
        <div
          className="card fixed z-50 min-w-[176px] p-1"
          style={{ left: contextMenu.x, top: contextMenu.y, boxShadow: 'var(--e4)' }}
          role="menu"
        >
          {!contextMenu.item.is_dir && (
            <>
              <MenuItem
                icon="download"
                label="下载"
                onClick={() => {
                  downloadFile(
                    numDiskId,
                    contextMenu.item.path,
                    contextMenu.item.name,
                    contextMenu.item.size,
                  );
                  setContextMenu(null);
                }}
              />
              <MenuItem
                icon="view"
                label="预览"
                onClick={() => {
                  handlePreview(contextMenu.item);
                  setContextMenu(null);
                }}
              />
              {isMediaFile(contextMenu.item.name) && store.canWrite && store.canDelete && (
                <MenuItem
                  icon="repair"
                  label="修复损坏媒体"
                  onClick={() => {
                    void handleRepair(contextMenu.item);
                    setContextMenu(null);
                  }}
                />
              )}
              <div className="mx-2 my-1 border-t border-line-subtle" />
            </>
          )}
          {store.canWrite && (
            <>
              <MenuItem
                icon="edit"
                label="重命名"
                onClick={() => {
                  setRenameTarget(contextMenu.item);
                  setRenameValue(contextMenu.item.name);
                  setContextMenu(null);
                }}
              />
              <MenuItem
                icon="folder"
                label="移动到…"
                onClick={() => {
                  setMoveTarget(contextMenu.item);
                  setContextMenu(null);
                }}
              />
              <div className="mx-2 my-1 border-t border-line-subtle" />
              <MenuItem
                icon="delete"
                label="删除"
                danger
                onClick={() => {
                  setDeleteTarget(contextMenu.item);
                  setContextMenu(null);
                }}
              />
            </>
          )}
        </div>
      )}

      {/* 重命名弹窗 */}
      <Modal
        open={Boolean(renameTarget)}
        title="重命名"
        icon="edit"
        width={420}
        onClose={() => setRenameTarget(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRenameTarget(null)}>
              取消
            </Button>
            <Button variant="primary" icon="checked" onClick={handleRename}>
              确认
            </Button>
          </>
        }
      >
        <input
          type="text"
          value={renameValue}
          onChange={e => setRenameValue(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleRename()}
          className="input"
          autoFocus
        />
      </Modal>

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

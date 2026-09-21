import { useEffect, useState } from 'react';
import type { DiskDir } from '../types/models';
import { diskService } from '../services/disk-service';
import { Button, Icon, IconButton } from './ui';

interface DirTreeModalProps {
  diskId: number;
  diskName: string;
  excludePath?: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}

/**
 * 目录树选择弹窗。
 * 对标桌面端 _RemoteDirBrowserDialog / MoveTargetDialog。
 * 懒加载子目录，不可选当前目录及子目录。
 */
export function DirTreeModal({
  diskId,
  diskName,
  excludePath,
  onSelect,
  onClose,
}: DirTreeModalProps) {
  const [pathStack, setPathStack] = useState<string[]>(['']);
  const [dirs, setDirs] = useState<DiskDir[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currentBrowsePath = pathStack[pathStack.length - 1];

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    diskService.browseDirs(diskId, currentBrowsePath)
      .then(data => {
        if (!cancelled) {
          setDirs(data.filter(d => {
            // 过滤掉不可选的路径（当前目录及子目录）
            if (excludePath && d.path.startsWith(excludePath)) return false;
            return true;
          }));
        }
      })
      .catch(e => {
        if (!cancelled) setError(`加载失败：${e.message}`);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [diskId, currentBrowsePath, excludePath]);

  const enterDir = (path: string) => {
    setPathStack(prev => [...prev, path]);
  };

  const goBack = () => {
    if (pathStack.length > 1) {
      setPathStack(prev => prev.slice(0, -1));
    }
  };

  const goRoot = () => {
    setPathStack(['']);
  };

  return (
    <div className="overlay">
      <div className="modal flex max-h-[80vh] w-full max-w-lg flex-col">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-line-subtle px-5 py-4">
          <h3 className="flex items-center gap-2 text-[15px] font-semibold">
            <Icon name="folder" className="text-warning-500" />
            选择目录 · {diskName}
          </h3>
          <IconButton icon="close" label="关闭" size="sm" onClick={onClose} />
        </div>

        {/* 当前路径 + 导航 */}
        <div className="flex items-center gap-2 border-b border-line-subtle bg-sunken px-5 py-2 text-[13px]">
          <span className="text-subtle">路径</span>
          <span className="mono truncate text-[12px] font-medium">/{currentBrowsePath || ''}</span>
          <div className="ml-auto flex gap-1">
            <Button size="sm" icon="back" disabled={pathStack.length <= 1} onClick={goBack}>
              上级
            </Button>
            <Button size="sm" disabled={currentBrowsePath === ''} onClick={goRoot}>
              根目录
            </Button>
          </div>
        </div>

        {/* 目录列表 */}
        <div className="min-h-[200px] flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center justify-center gap-2 py-8 text-subtle">
              <Icon name="loading" className="animate-spin" />
              加载中…
            </div>
          )}

          {error && <div className="px-5 py-4 text-[13px] text-danger-700">{error}</div>}

          {!loading && !error && dirs.length === 0 && (
            <div className="px-5 py-8 text-center text-[13px] text-subtle">
              此目录下没有子文件夹
            </div>
          )}

          {!loading &&
            dirs.map((dir) => (
              <button
                key={dir.path}
                type="button"
                onClick={() => enterDir(dir.path)}
                className="flex w-full items-center gap-3 border-b border-line-subtle px-5 py-3 text-left transition-colors hover:bg-hover"
              >
                <Icon name="folder" size="lg" className="text-warning-500" />
                <span className="truncate text-[13px]">{dir.name}</span>
                <span className="ml-auto text-[11.5px] text-subtle">点击进入</span>
              </button>
            ))}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 border-t border-line-subtle bg-sunken px-5 py-3">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" icon="checked" onClick={() => onSelect(currentBrowsePath)}>
            选择此目录
          </Button>
        </div>
      </div>
    </div>
  );
}

import { useEffect, useState } from 'react';
import type { DiskDir } from '../types/models';
import { diskService } from '../services/disk-service';

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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h3 className="font-semibold text-gray-800">
            选择目录 - {diskName}
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">
            ✕
          </button>
        </div>

        {/* 当前路径 + 导航 */}
        <div className="flex items-center gap-2 px-5 py-2 text-sm border-b border-gray-50 bg-gray-50">
          <span className="text-gray-400">路径：</span>
          <span className="font-medium text-gray-700 font-mono text-xs truncate">
            /{currentBrowsePath || ''}
          </span>
          <div className="flex gap-1 ml-auto">
            <button
              onClick={goBack}
              disabled={pathStack.length <= 1}
              className="px-2 py-1 text-xs rounded bg-white border border-gray-200 disabled:opacity-30"
            >
              ← 上级
            </button>
            <button
              onClick={goRoot}
              disabled={currentBrowsePath === ''}
              className="px-2 py-1 text-xs rounded bg-white border border-gray-200 disabled:opacity-30"
            >
              根目录
            </button>
          </div>
        </div>

        {/* 目录列表 */}
        <div className="flex-1 overflow-y-auto min-h-[200px]">
          {loading && (
            <div className="flex items-center justify-center py-8 text-gray-400">
              <div className="animate-spin w-5 h-5 border-2 border-indigo-500 border-t-transparent rounded-full mr-2" />
              加载中…
            </div>
          )}

          {error && (
            <div className="px-5 py-4 text-red-500 text-sm">{error}</div>
          )}

          {!loading && !error && dirs.length === 0 && (
            <div className="px-5 py-8 text-center text-gray-400 text-sm">
              此目录下没有子文件夹
            </div>
          )}

          {!loading && dirs.map(dir => (
            <button
              key={dir.path}
              onDoubleClick={() => enterDir(dir.path)}
              onClick={() => enterDir(dir.path)}
              className="w-full flex items-center gap-3 px-5 py-3 text-left hover:bg-gray-50 border-b border-gray-50 transition-colors"
            >
              <span className="text-lg">📁</span>
              <span className="text-sm text-gray-700 truncate">{dir.name}</span>
              <span className="ml-auto text-xs text-gray-300">双击进入</span>
            </button>
          ))}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-100">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
          >
            取消
          </button>
          <button
            onClick={() => onSelect(currentBrowsePath)}
            className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
          >
            选择此目录
          </button>
        </div>
      </div>
    </div>
  );
}

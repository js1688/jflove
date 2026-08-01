import { useEffect } from 'react';
import { useNavigate } from 'react-router';
import { useFileStore } from '../stores/file-store';
import { PageHeader } from '../components/PageHeader';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { EmptyState } from '../components/EmptyState';

/** 虚拟磁盘列表页 */
export function FileListPage() {
  const navigate = useNavigate();
  const { disks, disksLoading, loadDisks } = useFileStore();

  useEffect(() => {
    loadDisks().catch(() => {});
  }, [loadDisks]);

  return (
    <div>
      <PageHeader title="文件管理" />
      <div className="p-4">
        {disksLoading && <LoadingSpinner text="加载磁盘列表…" />}

        {!disksLoading && disks.length === 0 && (
          <EmptyState
            icon="💾"
            title="暂无可用磁盘"
            description="请联系管理员分配磁盘权限"
          />
        )}

        {!disksLoading && disks.length > 0 && (
          <div className="space-y-2">
            {disks.map(disk => (
              <button
                key={disk.id}
                onClick={() => navigate(`/files/${disk.id}`)}
                className="w-full flex items-center gap-4 bg-white rounded-xl p-4 shadow-sm border border-gray-100 hover:shadow-md hover:border-indigo-200 transition-all text-left"
              >
                <span className="text-2xl">💾</span>
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-gray-800 text-sm truncate">
                    {disk.name}
                  </div>
                  <div className="text-xs text-gray-400 truncate mt-0.5">
                    {disk.path}
                  </div>
                </div>
                <span className="text-gray-300">→</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

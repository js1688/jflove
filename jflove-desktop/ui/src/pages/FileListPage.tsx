import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useFileStore } from '../stores/file-store';
import { PageHeader } from '../components/PageHeader';
import { Badge, EmptyState, ErrorState, Icon, ListSkeleton } from '../components/ui';

/**
 * 虚拟磁盘列表页
 *
 * v1.5.0 改版：emoji 💾/→ 换矢量图标；卡片套设计令牌（描边 + 极浅阴影 + 悬停抬升）；
 * 加载态由转圈改骨架屏；错误态用统一组件（需求 AC-4/AC-6/AC-7）。
 */
export function FileListPage() {
  const navigate = useNavigate();
  const { disks, disksLoading, loadDisks } = useFileStore();
  const [loadError, setLoadError] = useState<string | null>(null);

  const reload = () => {
    loadDisks()
      .then(() => setLoadError(null))
      .catch((e) => setLoadError(e instanceof Error ? e.message : '加载磁盘列表失败'));
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅首屏加载，重试走 reload
  }, [loadDisks]);

  return (
    <div>
      <PageHeader title="文件管理" subtitle="选择磁盘开始浏览" />
      <div className="p-4">
        {disksLoading && <ListSkeleton rows={4} />}

        {!disksLoading && loadError && <ErrorState message={loadError} onRetry={reload} />}

        {!disksLoading && !loadError && disks.length === 0 && (
          <EmptyState
            icon="disks"
            title="暂无可用磁盘"
            description="请联系管理员为你分配磁盘权限"
          />
        )}

        {!disksLoading && !loadError && disks.length > 0 && (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {disks.map((disk) => (
              <button
                key={disk.id}
                type="button"
                onClick={() => navigate(`/files/${disk.id}`)}
                className="card card-hover flex items-center gap-3.5 p-4 text-left"
              >
                <span className="stat-ico stat-ico-brand">
                  <Icon name="disks" size="lg" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13.5px] font-semibold">{disk.name}</span>
                  <span className="mt-0.5 block truncate text-[11.5px] text-subtle">
                    {disk.path || '/'}
                  </span>
                </span>
                <Icon name="next" size="sm" className="shrink-0 text-subtle" />
              </button>
            ))}
          </div>
        )}

        {/* 权限说明（原先无此提示；磁盘可写性来自后端权限） */}
        {!disksLoading && !loadError && disks.length > 0 && (
          <div className="mt-4">
            <Badge tone="neutral" icon="locked">
              所有文件传输均走端到端加密
            </Badge>
          </div>
        )}
      </div>
    </div>
  );
}

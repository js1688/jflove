import { useTransferStore } from '../stores/transfer-store';
import { PageHeader } from '../components/PageHeader';
import { Badge, Button, EmptyState, Icon, Progress, toast, type BadgeTone } from '../components/ui';
import { formatSize } from '../utils/format';

const STATUS_LABELS: Record<string, string> = {
  pending: '等待中',
  hashing: '校验中',
  running: '传输中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

const STATUS_TONE: Record<string, BadgeTone> = {
  pending: 'neutral',
  hashing: 'brand',
  running: 'brand',
  completed: 'success',
  failed: 'danger',
  cancelled: 'neutral',
};

/**
 * 传输任务内容（不含页面外框）
 *
 * 拆成独立组件的用途：移动端布局会把「修复中心」并入本页，
 * 由 `TransferTabsPage` 在同一页内用标签页切换两块内容（需求 §2.5）。
 */
export function TransferTasksView() {
  const { tasks, stats, cancelTask, clearFinished } = useTransferStore();

  const finishedCount = stats.completed + stats.failed + stats.cancelled;

  const handleClearFinished = () => {
    clearFinished();
    if (finishedCount > 0) {
      toast.success(`已清除 ${finishedCount} 个已结束任务`);
    }
  };

  return (
    <>
      {/* 统计条（v1.5.0：改用徽标，配色走令牌） */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line-subtle bg-surface px-4 py-2.5">
        <span className="tabular text-[11.5px] text-muted">共 {stats.total} 个</span>
        <span className="h-3 w-px bg-line" />
        <Badge tone="brand">进行中 {stats.running}</Badge>
        <Badge tone="neutral">等待 {stats.pending}</Badge>
        <Badge tone="success">完成 {stats.completed}</Badge>
        {stats.failed > 0 && <Badge tone="danger">失败 {stats.failed}</Badge>}
        {stats.cancelled > 0 && <Badge tone="neutral">取消 {stats.cancelled}</Badge>}
        <div className="grow" />
        {finishedCount > 0 && (
          <Button size="sm" icon="delete" onClick={handleClearFinished}>
            清除已结束
          </Button>
        )}
      </div>

      <div className="flex flex-col gap-2.5 p-3">
        {tasks.length === 0 && (
          <EmptyState
            icon="transfer"
            title="暂无传输任务"
            description="在「文件管理」页面选择磁盘后即可上传或下载文件"
          />
        )}

        {tasks.map((task) => {
          const tone = STATUS_TONE[task.status] ?? 'neutral';
          const done = task.status === 'completed';
          const failed = task.status === 'failed';
          return (
            <div key={task.id} className="card p-4">
              <div className="mb-2.5 flex items-center gap-3">
                <span className={`stat-ico stat-ico-${failed ? 'rose' : 'brand'}`}>
                  <Icon name={task.kind === 'upload' ? 'upload' : 'download'} size="lg" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-semibold">{task.filename}</div>
                  <div className="mt-0.5 flex items-center gap-2">
                    <Badge tone={tone}>{STATUS_LABELS[task.status] || task.status}</Badge>
                    <span className="text-[11.5px] text-subtle">
                      {task.kind === 'upload' ? '上传' : '下载'}
                    </span>
                  </div>
                </div>
                {(task.status === 'running' || task.status === 'pending') && (
                  <Button size="sm" variant="ghost" icon="close" onClick={() => cancelTask(task.id)}>
                    取消
                  </Button>
                )}
              </div>

              <Progress value={done ? 100 : task.percent} />

              <div className="tabular mt-1.5 flex justify-between text-[11.5px] text-subtle">
                <span>
                  {formatSize(task.transferred)} / {formatSize(task.fileSize)}
                </span>
                <span>{done ? '已完成' : `${task.percent}%`}</span>
              </div>

              {task.error && (
                <div
                  className="mt-2 flex items-start gap-1.5 text-[11.5px]"
                  style={{ color: 'var(--danger-700)' }}
                >
                  <Icon name="error" size="sm" className="mt-px shrink-0" />
                  <span className="break-words">{task.error}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

/**
 * 传输任务页
 *
 * v1.5.0：emoji 📤/📥/📊 换矢量图标；进度条与状态徽标套令牌；
 * 原先手写 `document.createElement('div')` 的临时提示改为全站统一 Toast。
 */
export function TransferPage() {
  return (
    <div>
      <PageHeader title="传输任务" subtitle="上传与下载进度" />
      <TransferTasksView />
    </div>
  );
}

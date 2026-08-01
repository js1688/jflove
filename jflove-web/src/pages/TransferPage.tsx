import { useTransferStore } from '../stores/transfer-store';
import { PageHeader } from '../components/PageHeader';
import { EmptyState } from '../components/EmptyState';

const STATUS_LABELS: Record<string, string> = {
  pending: '等待中',
  hashing: '校验中',
  running: '传输中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

/** 传输任务页 */
export function TransferPage() {
  const { tasks, stats, cancelTask, clearFinished } = useTransferStore();

  return (
    <div>
      <PageHeader
        title="传输任务"
        actions={
          stats.completed > 0 ? (
            <button
              onClick={clearFinished}
              className="px-3 py-1.5 text-xs border border-gray-200 rounded-lg hover:bg-gray-50"
            >
              清除已完成
            </button>
          ) : undefined
        }
      />

      {/* 统计 */}
      <div className="flex gap-4 px-4 py-2 text-xs text-gray-400 bg-white border-b border-gray-100">
        <span>共 {stats.total} 个</span>
        <span>进行中 {stats.running}</span>
        <span>已完成 {stats.completed}</span>
      </div>

      {/* 任务列表 */}
      <div className="p-2">
        {tasks.length === 0 && (
          <EmptyState icon="📊" title="暂无传输任务" description="上传或下载文件时将在此显示进度" />
        )}

        {tasks.map(task => (
          <div
            key={task.id}
            className="bg-white rounded-xl p-4 mb-2 shadow-sm border border-gray-100"
          >
            <div className="flex items-center gap-3 mb-2">
              <span className="text-lg">{task.kind === 'upload' ? '📤' : '📥'}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-gray-800 truncate">{task.filename}</div>
                <div className="text-xs text-gray-400">
                  {STATUS_LABELS[task.status] || task.status}
                </div>
              </div>
              {task.status === 'running' || task.status === 'pending' ? (
                <button
                  onClick={() => cancelTask(task.id)}
                  className="px-2 py-1 text-xs text-red-500 hover:bg-red-50 rounded"
                >
                  取消
                </button>
              ) : null}
            </div>

            {/* 进度条 */}
            <div className="w-full bg-gray-100 rounded-full h-2 mb-1">
              <div
                className={`h-2 rounded-full transition-all duration-300 ${
                  task.status === 'failed' ? 'bg-red-400' :
                  task.status === 'completed' ? 'bg-green-400' :
                  task.status === 'cancelled' ? 'bg-gray-400' :
                  'bg-indigo-500'
                }`}
                style={{ width: `${task.percent}%` }}
              />
            </div>

            <div className="flex justify-between text-xs text-gray-400">
              <span>
                {formatSize(task.transferred)} / {formatSize(task.fileSize)}
              </span>
              <span>{task.percent}%</span>
            </div>

            {task.error && (
              <div className="mt-1 text-xs text-red-500">{task.error}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

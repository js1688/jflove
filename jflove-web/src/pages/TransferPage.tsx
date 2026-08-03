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

  const handleClearFinished = () => {
    const count = stats.completed + stats.failed + stats.cancelled;
    clearFinished();
    // 对标桌面端：清除完成后 Toast 提示
    if (count > 0) {
      const toast = document.createElement('div');
      toast.className = 'fixed top-4 right-4 z-50 px-4 py-2 bg-green-50 border border-green-200 text-green-700 text-sm rounded-lg shadow';
      toast.textContent = `已清除 ${count} 个已结束任务`;
      document.body.appendChild(toast);
      setTimeout(() => toast.remove(), 3000);
    }
  };

  const finishedCount = stats.completed + stats.failed + stats.cancelled;

  return (
    <div>
      <PageHeader
        title="传输任务"
        actions={
          finishedCount > 0 ? (
            <button
              onClick={handleClearFinished}
              className="px-3 py-1.5 text-xs border border-gray-200 rounded-lg hover:bg-gray-50"
            >
              清除已完成
            </button>
          ) : undefined
        }
      />

      {/* 统计 — 对标桌面端：共 N | 进行中 N | 等待 N | 完成 N | 失败 N | 取消 N */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 px-4 py-2 text-xs text-gray-400 bg-white border-b border-gray-100">
        <span>共 {stats.total} 个</span>
        <span>进行中 {stats.running}</span>
        <span>等待 {stats.pending}</span>
        <span>完成 {stats.completed}</span>
        <span>失败 {stats.failed}</span>
        <span>取消 {stats.cancelled}</span>
      </div>

      {/* 任务列表 */}
      <div className="p-2">
        {tasks.length === 0 && (
          <EmptyState icon="📊" title="暂无传输任务" description="暂无传输任务。可在「文档管理」页面发起上传或下载。" />
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

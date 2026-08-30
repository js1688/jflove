import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { repairService, type RepairTask } from '../services/repair-service';
import { useFileStore } from '../stores/file-store';
import { useAuthStore } from '../stores/auth-store';
import { PageHeader } from '../components/PageHeader';
import { LoadingSpinner } from '../components/LoadingSpinner';

/** 任务状态中文映射 */
const STATUS_TEXT: Record<string, string> = {
  pending: '排队中',
  running: '执行中',
  verifying: '验证中',
  success: '修复成功',
  failed: '修复失败',
  canceled: '已取消',
  overridden: '已覆盖',
};

/** 状态徽标配色 */
function statusClass(status: string): string {
  switch (status) {
    case 'success': return 'bg-green-50 text-green-700';
    case 'failed': return 'bg-red-50 text-red-600';
    case 'running':
    case 'verifying':
    case 'pending': return 'bg-indigo-50 text-indigo-600';
    default: return 'bg-gray-100 text-gray-500';
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/**
 * 修复中心页面（v1.4.2）
 *
 * 全平台共享的媒体修复任务列表（所有登录用户可见；只读账号可看不可操作），
 * 按状态执行操作：
 *   - 排队中/执行中/验证中：取消
 *   - 成功：验证播放（预览修复产物）、覆盖原文件（重点二次确认）、删除产物
 *   - 失败/已取消/已覆盖：删除记录
 * 轮询间隔 2.5s；操作权限 = 磁盘写+删并存（UI 禁用，接口 403 兜底）。
 */
export function RepairCenterPage() {
  const navigate = useNavigate();
  const isAdmin = useAuthStore(s => s.isAdmin);
  const loadDisks = useFileStore(s => s.loadDisks);
  const disks = useFileStore(s => s.disks);

  const [tasks, setTasks] = useState<RepairTask[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyTaskId, setBusyTaskId] = useState<number | null>(null);
  // 覆盖确认对话框目标任务
  const [overrideTarget, setOverrideTarget] = useState<RepairTask | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await repairService.list(1, 100);
      setTasks(data.tasks);
      setTotal(data.total);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载修复任务失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // 磁盘权限（写+删并存）用于按钮可用性；管理员由服务端放行
    if (disks.length === 0) {
      loadDisks().catch(() => {});
    }
    void refresh();
    const t = setInterval(() => { void refresh(); }, 2500);
    return () => clearInterval(t);
  }, [refresh, loadDisks, disks.length]);

  const canOperate = (task: RepairTask): boolean => {
    if (isAdmin) return true;
    const disk = disks.find(d => d.id === task.disk_id);
    return Boolean(disk?.can_write && disk?.can_delete);
  };

  const runOp = async (fn: () => Promise<unknown>, taskId: number, successMsg: string) => {
    setBusyTaskId(taskId);
    setNotice(null);
    try {
      await fn();
      setNotice(successMsg);
      await refresh();
    } catch (e) {
      setNotice(`操作失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyTaskId(null);
    }
  };

  /** 验证播放：设置预览目标（含 repairTaskId）后进入预览页 */
  const handleVerify = (task: RepairTask) => {
    useFileStore.getState().setPreviewTarget({
      path: task.filename,
      name: task.filename,
      size: task.source_size,
      repairTaskId: task.id,
    });
    navigate(`/files/${task.disk_id}/preview`);
  };

  const handleOverride = async () => {
    if (!overrideTarget) return;
    const taskId = overrideTarget.id;
    setOverrideTarget(null);
    await runOp(() => repairService.override(taskId), taskId, '已覆盖原文件');
  };

  if (loading) {
    return (
      <div>
        <PageHeader title="修复中心" onBack={() => navigate(-1)} />
        <LoadingSpinner text="加载修复任务…" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl p-4 sm:p-6">
      <PageHeader title="修复中心" onBack={() => navigate(-1)} />

      <p className="mb-4 text-xs text-gray-500">
        对损坏的视频/音频发起离线修复：修复产物验证播放满意后，可覆盖原文件
        （原文件将被直接删除、不可恢复）。文件管理右键「修复损坏媒体」发起修复。
      </p>

      {error && (
        <div className="my-4 rounded-lg bg-red-50 p-4 text-sm text-red-600">{error}</div>
      )}
      {notice && (
        <div className="my-4 rounded-lg bg-green-50 p-3 text-sm text-green-700">{notice}</div>
      )}

      {tasks.length === 0 && !error ? (
        <div className="py-16 text-center text-sm text-gray-400">
          暂无修复任务
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <div className="divide-y divide-gray-100">
            {tasks.map(task => {
              const status = task.status;
              const busy = busyTaskId === task.id;
              return (
                <div key={task.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-gray-800">{task.filename}</p>
                    <p className="text-xs text-gray-400">
                      {task.username} · {formatSize(task.source_size)}
                      {(status === 'running' || status === 'verifying') && ` · ${task.progress}%`}
                    </p>
                    {task.error_message && (
                      <p className="text-xs text-red-500">{task.error_message}</p>
                    )}
                  </div>
                  <span className={`rounded-full px-2.5 py-0.5 text-xs ${statusClass(status)}`}>
                    {STATUS_TEXT[status] ?? status}
                  </span>
                  <div className="flex items-center gap-2">
                    {(status === 'pending' || status === 'running' || status === 'verifying') && (
                      <button
                        type="button"
                        disabled={!canOperate(task) || busy}
                        onClick={() => void runOp(
                          () => repairService.cancel(task.id), task.id, '任务已取消',
                        )}
                        className="rounded-md border border-gray-300 px-3 py-1 text-xs
                          text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                      >
                        取消
                      </button>
                    )}
                    {status === 'success' && (
                      <>
                        <button
                          type="button"
                          onClick={() => handleVerify(task)}
                          className="rounded-md bg-indigo-600 px-3 py-1 text-xs text-white
                            hover:bg-indigo-700"
                        >
                          验证播放
                        </button>
                        <button
                          type="button"
                          disabled={!canOperate(task) || busy}
                          onClick={() => setOverrideTarget(task)}
                          className="rounded-md border border-indigo-200 px-3 py-1 text-xs
                            text-indigo-600 hover:bg-indigo-50 disabled:opacity-40"
                        >
                          覆盖原文件
                        </button>
                        <button
                          type="button"
                          disabled={!canOperate(task) || busy}
                          onClick={() => void runOp(
                            async () => {
                              // v1.4.2 hotfix：删除产物后同时删除任务记录，
                              // 否则记录仍占用「同文件互斥」，无法重新发起修复
                              await repairService.deleteArtifact(task.id);
                              await repairService.deleteRecord(task.id);
                            },
                            task.id, '产物已删除',
                          )}
                          className="rounded-md border border-gray-300 px-3 py-1 text-xs
                            text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                        >
                          删除产物
                        </button>
                      </>
                    )}
                    {(status === 'failed' || status === 'canceled' || status === 'overridden') && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void runOp(
                          () => repairService.deleteRecord(task.id), task.id, '记录已删除',
                        )}
                        className="rounded-md border border-gray-300 px-3 py-1 text-xs
                          text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                      >
                        删除记录
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {total > tasks.length && (
            <p className="border-t border-gray-100 px-4 py-2 text-xs text-gray-400">
              共 {total} 条任务，仅展示最近 {tasks.length} 条
            </p>
          )}
        </div>
      )}

      {/* 覆盖原文件：重点二次确认（原损坏文件将被删除、不可恢复） */}
      {overrideTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-base font-semibold text-gray-800">覆盖原文件（不可恢复）</h3>
            <p className="mb-4 text-sm text-gray-600">
              即将用修复产物覆盖「{overrideTarget.filename}」。<br />
              <span className="font-medium text-red-600">
                ⚠ 原损坏文件将被直接删除、无法恢复！
              </span><br />
              请确认已通过「验证播放」确认修复产物可用。
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setOverrideTarget(null)}
                className="rounded-lg px-4 py-2 text-sm text-gray-600 hover:bg-gray-100"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleOverride()}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700"
              >
                确认覆盖
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

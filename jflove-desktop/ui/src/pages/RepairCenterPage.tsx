import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { repairService, type RepairTask } from '../services/repair-service';
import { useFileStore } from '../stores/file-store';
import { useAuthStore } from '../stores/auth-store';
import { PageHeader } from '../components/PageHeader';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { Badge, Button, EmptyState, Icon, Modal, type BadgeTone } from '../components/ui';
import { formatSize } from '../utils/format';

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

/**
 * 修复中心页面（v1.4.2）
 *
 * 全平台共享的媒体修复任务列表（所有登录用户可见；只读账号可看不可操作），
 * 按状态执行操作：
 *   - 排队中/执行中/验证中：取消
 *   - 成功：验证播放（预览修复产物）、覆盖原文件（重点二次确认）、删除产物
 *   - 失败/已取消/已覆盖：删除记录
 * 轮询间隔 2.5s；操作权限 = 磁盘写+删并存（UI 禁用，接口 403 兜底）。
 *
 * v1.5.0：拆成 `RepairCenterContent`（自带数据获取，可复用）+ `RepairCenterPage`（带页面外框），
 * 以便传输页在同一页内以标签页形式复用修复中心（需求 §2.5）。
 */
export function RepairCenterPage() {
  const navigate = useNavigate();
  return (
    <div>
      <PageHeader title="修复中心" subtitle="共享修复任务与产物管理" onBack={() => navigate(-1)} />
      <RepairCenterContent />
    </div>
  );
}

/**
 * 修复中心内容（自带数据获取、轮询与操作）
 *
 * 供两处复用：
 *   - `/repair` 独立页（保留深链兼容）；
 *   - `/transfer` 传输页的「修复」标签（需求 §2.5 / AC-18）。
 */
export function RepairCenterContent() {
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

  // 磁盘列表单独拉取：不能和上面的轮询 effect 写在一起。
  // 原实现把 `disks.length` 放进轮询 effect 的依赖数组，而 effect 体内又会调
  // `loadDisks()` 把它从 0 变成 1 —— effect 自己把自己触发重跑，导致
  // `repair/tasks` 在 19ms 内被连发两次（生产构建实测，非 StrictMode 假象）。
  useEffect(() => {
    if (useFileStore.getState().disks.length === 0) {
      loadDisks().catch(() => {});
    }
  }, [loadDisks]);

  useEffect(() => {
    void refresh();
    const t = setInterval(() => { void refresh(); }, 2500);
    return () => clearInterval(t);
  }, [refresh]);

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
    return <LoadingSpinner text="加载修复任务…" />;
  }

  return (
    <div>
      <RepairTasksView
        tasks={tasks}
        total={total}
        error={error}
        notice={notice}
        busyTaskId={busyTaskId}
        canOperate={canOperate}
        onCancelTask={(id) => void runOp(() => repairService.cancel(id), id, '任务已取消')}
        onVerify={handleVerify}
        onRequestOverride={setOverrideTarget}
        onDeleteArtifact={(id) =>
          void runOp(() => repairService.deleteArtifact(id), id, '产物已删除')
        }
        onDeleteRecord={(id) => void runOp(() => repairService.deleteRecord(id), id, '记录已删除')}
        overrideTarget={overrideTarget}
        onCloseOverride={() => setOverrideTarget(null)}
        onConfirmOverride={() => void handleOverride()}
      />
    </div>
  );
}

/* ==================== 内容视图（供本页与传输页复用） ==================== */

export interface RepairTasksViewProps {
  tasks: RepairTask[];
  total: number;
  error: string | null;
  notice: string | null;
  busyTaskId: number | null;
  canOperate: (t: RepairTask) => boolean;
  onCancelTask: (id: number) => void;
  onVerify: (t: RepairTask) => void;
  onRequestOverride: (t: RepairTask) => void;
  onDeleteArtifact: (id: number) => void;
  onDeleteRecord: (id: number) => void;
  overrideTarget: RepairTask | null;
  onCloseOverride: () => void;
  onConfirmOverride: () => void;
}

/**
 * 修复任务列表内容（不含页面外框与数据获取）
 *
 * v1.5.0 抽出为独立组件，供两处复用：
 *   - `/repair` 独立页（保留深链兼容）；
 *   - `/transfer` 传输页的「修复」标签（需求 §2.5：移动端导航 6→5 后修复中心并入传输页）。
 */
export function RepairTasksView({
  tasks,
  total,
  error,
  notice,
  busyTaskId,
  canOperate,
  onCancelTask,
  onVerify,
  onRequestOverride,
  onDeleteArtifact,
  onDeleteRecord,
  overrideTarget,
  onCloseOverride,
  onConfirmOverride,
}: RepairTasksViewProps) {
  return (
    <div className="mx-auto max-w-4xl p-4 sm:p-6">
      <p className="mb-4 text-[12.5px] leading-relaxed text-muted">
        对损坏的视频/音频发起离线修复：修复产物验证播放满意后，可覆盖原文件（原文件将被直接删除、
        不可恢复）。在「文件管理」中右键文件即可发起修复。
      </p>

      {error && (
        <div className="my-4 flex items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3.5 text-[13px] text-danger-700">
          <Icon name="error" size="sm" className="mt-px shrink-0" />
          <span className="break-words">{error}</span>
        </div>
      )}
      {notice && (
        <div className="my-4 flex items-start gap-2 rounded-lg border border-success-500/25 bg-success-50 p-3 text-[13px] text-success-700">
          <Icon name="success" size="sm" className="mt-px shrink-0" />
          <span className="break-words">{notice}</span>
        </div>
      )}

      {tasks.length === 0 && !error ? (
        <EmptyState
          icon="repair"
          title="暂无修复任务"
          description="在文件管理中右键损坏的视频/音频文件，选择「修复损坏媒体」"
        />
      ) : (
        <div className="card overflow-hidden" style={{ padding: 0 }}>
          {tasks.map((task) => {
            const status = task.status;
            const busy = busyTaskId === task.id;
            const tone: BadgeTone =
              status === 'success'
                ? 'success'
                : status === 'failed'
                  ? 'danger'
                  : status === 'overridden'
                    ? 'brand'
                    : status === 'canceled'
                      ? 'neutral'
                      : 'brand';
            return (
              <div
                key={task.id}
                className="flex flex-wrap items-center gap-3 border-b border-line-subtle px-4 py-3 last:border-b-0"
              >
                <span className="file-ico file-ico-video">
                  <Icon name="video" size="lg" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium">{task.filename}</p>
                  <p className="tabular text-[11.5px] text-subtle">
                    {task.username} · {formatSize(task.source_size)}
                    {(status === 'running' || status === 'verifying') && ` · ${task.progress}%`}
                  </p>
                  {task.error_message && (
                    <p className="text-[11.5px]" style={{ color: 'var(--danger-700)' }}>
                      {task.error_message}
                    </p>
                  )}
                </div>

                <Badge tone={tone}>{STATUS_TEXT[status] ?? status}</Badge>

                <div className="flex items-center gap-1.5">
                  {(status === 'pending' || status === 'running' || status === 'verifying') && (
                    <Button
                      size="sm"
                      icon="close"
                      disabled={!canOperate(task) || busy}
                      onClick={() => onCancelTask(task.id)}
                    >
                      取消
                    </Button>
                  )}
                  {status === 'success' && (
                    <>
                      <Button
                        size="sm"
                        variant="primary"
                        icon="play"
                        onClick={() => onVerify(task)}
                      >
                        验证播放
                      </Button>
                      <Button
                        size="sm"
                        icon="warning"
                        disabled={!canOperate(task) || busy}
                        onClick={() => onRequestOverride(task)}
                      >
                        覆盖原文件
                      </Button>
                      <Button
                        size="sm"
                        icon="delete"
                        disabled={!canOperate(task) || busy}
                        onClick={() => onDeleteArtifact(task.id)}
                      >
                        删除产物
                      </Button>
                    </>
                  )}
                  {(status === 'failed' || status === 'canceled' || status === 'overridden') && (
                    <Button
                      size="sm"
                      icon="delete"
                      disabled={busy}
                      onClick={() => onDeleteRecord(task.id)}
                    >
                      删除记录
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
          {total > tasks.length && (
            <p className="border-t border-line-subtle px-4 py-2 text-[11.5px] text-subtle">
              共 {total} 条任务，仅展示最近 {tasks.length} 条
            </p>
          )}
        </div>
      )}

      {/* 覆盖原文件：重点二次确认（原损坏文件将被删除、不可恢复） */}
      {overrideTarget && (
        <Modal
          open
          title="覆盖原文件（不可恢复）"
          icon="alert"
          width={480}
          onClose={onCloseOverride}
          footer={
            <>
              <Button variant="ghost" onClick={onCloseOverride}>
                取消
              </Button>
              <Button variant="danger" icon="warning" onClick={onConfirmOverride}>
                确认覆盖
              </Button>
            </>
          }
        >
          <p className="m-0 text-[13.5px] leading-relaxed text-muted">
            即将用修复产物覆盖「{overrideTarget.filename}」。
            <br />
            <span className="font-semibold" style={{ color: 'var(--danger-700)' }}>
              原损坏文件将被直接删除、无法恢复！
            </span>
            <br />
            请确认已通过「验证播放」确认修复产物可用。
          </p>
        </Modal>
      )}
    </div>
  );
}

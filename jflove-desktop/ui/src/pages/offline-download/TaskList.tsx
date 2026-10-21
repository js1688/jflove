/**
 * 离线下载 · 任务中心（v1.8.0）
 *
 * 对应需求 F27 / 设计 §3.1：
 *   - 只展示**当前账号自己的**任务（服务端按 user_id 过滤，客户端不做二次筛选）；
 *   - 每行展示状态徽章 / 进度条 / 速度 / 脱敏来源域名 / 产物名（页面标题）/ 目标 / 失败原因；
 *   - 操作：取消、重试、删除记录、在文件管理中打开、待选择任务「去选择」；
 *   - 轮询 2.5s（来自 `/meta.poll_interval_ms`），页面后台暂停、任务全部终态暂停、卸载清理。
 *
 * 安全：只通过 `offline-download-service` 走 `httpClient`（Python 桥加密），
 * 本组件不接触 token / session_key，也不在渲染层发起任何对外请求。
 */

import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router';
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  Icon,
  ListSkeleton,
  Progress,
  toast,
} from '../../components/ui';
import type { BadgeTone } from '../../components/ui';
import { useVisiblePolling } from '../../hooks/use-polling';
import { formatSize } from '../../utils/format';
import {
  offlineDownloadService,
  type OfflineTask,
  type OfflineWritableDisk,
} from '../../services/offline-download-service';
import {
  canOperateTask,
  canRetryTask,
  errorSuggestion,
  formatIsoTime,
  formatSpeed,
  isTerminalStatus,
  maskSourceDomain,
  offlineErrorText,
  taskPercent,
} from './offline-utils';

/** 列表一次拉取的上限（服务端 `page_size` 上限 100） */
const PAGE_SIZE = 100;

/** 状态 → 中文（兜底；正常以服务端 `status_text` 为准） */
const STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  analyzing: '分析中',
  ready: '待选择',
  downloading: '下载中',
  merging: '合并中',
  success: '已完成',
  failed: '失败',
  canceled: '已取消',
};

/** 状态 → 徽章配色 */
const STATUS_TONE: Record<string, BadgeTone> = {
  queued: 'neutral',
  analyzing: 'brand',
  ready: 'warning',
  downloading: 'brand',
  merging: 'brand',
  success: 'success',
  failed: 'danger',
  canceled: 'neutral',
};

const RETRY_BLOCKED_TITLE = '该失败类型不可重试';

interface TaskListProps {
  /** 轮询间隔（来自 `/offline/meta`，默认 2500ms） */
  pollIntervalMs: number;
  /** 账号可写磁盘（用来把「目标磁盘名」映射成文件管理路由的 disk_id） */
  writableDisks: OfflineWritableDisk[];
  /** 待选择任务 → 切到「新建任务」页签并加载该任务 */
  onSelectTask: (taskId: number) => void;
}

/** 任务中心内容（自带数据获取、轮询与操作） */
export function TaskList({ pollIntervalMs, writableDisks, onSelectTask }: TaskListProps) {
  const navigate = useNavigate();

  const [tasks, setTasks] = useState<OfflineTask[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyTaskId, setBusyTaskId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<OfflineTask | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await offlineDownloadService.listTasks(1, PAGE_SIZE);
      setTasks(data.tasks);
      setTotal(data.total);
      setError(null);
    } catch (e) {
      setError(offlineErrorText(e, '加载离线下载任务失败'));
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * 是否继续自动刷新。
   *
   * F27.3：任务全部终态后停止轮询。列表为空时**保持轮询**——否则同一账号
   * 在 Web 端新建的任务（AC-219：2.5 秒内可见）永远不会自动出现。
   */
  const hasActiveTask = tasks.some((task) => !isTerminalStatus(task.status));
  const pollingEnabled = tasks.length === 0 || hasActiveTask;

  useVisiblePolling(refresh, pollIntervalMs, pollingEnabled);

  // 操作成功后立即刷新一次（同时把终态 → 活跃的状态变化反馈到轮询开关）
  const runOp = async (
    action: () => Promise<unknown>,
    taskId: number,
    successMessage: string,
  ) => {
    setBusyTaskId(taskId);
    try {
      await action();
      toast.success(successMessage);
      await refresh();
    } catch (e) {
      toast.error('操作失败', offlineErrorText(e));
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleOpenInFiles = (task: OfflineTask) => {
    const disk = writableDisks.find((item) => item.name === task.target_disk_name);
    if (!disk) {
      toast.info('无法定位目标磁盘', '请在「文件管理」中手动打开目标目录');
      navigate('/files');
      return;
    }
    const query = task.target_rel_path ? `?path=${encodeURIComponent(task.target_rel_path)}` : '';
    navigate(`/files/${disk.disk_id}${query}`);
  };

  const handleConfirmDelete = async () => {
    const target = deleteTarget;
    setDeleteTarget(null);
    if (!target) return;
    await runOp(
      () => offlineDownloadService.deleteRecord(target.id),
      target.id,
      '任务记录已删除（磁盘文件保留）',
    );
  };

  const refreshLabel = `刷新（每 ${(pollIntervalMs / 1000).toFixed(1)} 秒自动）`;

  return (
    <div className="mx-auto max-w-4xl p-4 sm:p-6">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <p className="m-0 grow text-[12.5px] leading-relaxed text-muted">
          任务按账号隔离，仅显示你自己创建的记录。页面在前台时自动刷新，切到后台即暂停。
        </p>
        <Badge tone="neutral">共 {total} 条</Badge>
        <Button
          size="sm"
          icon="refresh"
          loading={loading && tasks.length > 0}
          onClick={() => void refresh()}
          aria-label={refreshLabel}
          title={refreshLabel}
        >
          刷新
        </Button>
      </div>

      {error && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3.5 text-[13px] text-danger-700">
          <Icon name="error" size="sm" className="mt-px shrink-0" />
          <span className="break-words">{error}</span>
        </div>
      )}

      {loading && tasks.length === 0 && !error && <ListSkeleton rows={4} />}

      {!loading && tasks.length === 0 && !error && (
        <EmptyState
          icon="download"
          title="暂无离线下载任务"
          description="切到「新建任务」页签，粘贴一个网页地址即可开始分析"
        />
      )}

      {tasks.length > 0 && (
        <div className="card overflow-hidden" style={{ padding: 0 }}>
          {tasks.map((task) => {
            const percent = taskPercent(task);
            const tone = STATUS_TONE[task.status] ?? 'neutral';
            const label = task.status_text || STATUS_TEXT[task.status] || task.status;
            const busy = busyTaskId === task.id;
            const active = !isTerminalStatus(task.status) && task.status !== 'ready';
            const operable = canOperateTask(task);
            const retryable = canRetryTask(task.status, task.error_code);
            const suggestion = errorSuggestion(task.error_code);
            const productName = task.page_title || task.source_domain || '—';
            const targetLabel = task.target_disk_name
              ? `${task.target_disk_name} · ${task.target_rel_path ? `/${task.target_rel_path}` : '磁盘根目录'}`
              : '—';
            const showOpen =
              Boolean(task.target_disk_name && task.target_rel_path) &&
              (task.status === 'success' ||
                task.status === 'failed' ||
                task.status === 'canceled');
            const showDelete = isTerminalStatus(task.status) || task.status === 'ready';

            return (
              <div
                key={task.id}
                className="border-b border-line-subtle px-4 py-3.5 last:border-b-0"
              >
                <div className="flex flex-wrap items-center gap-2.5">
                  <span className="stat-ico stat-ico-brand shrink-0">
                    <Icon name="download" size="lg" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-medium" title={productName}>
                      产物：{productName}
                    </p>
                    <p className="tabular truncate text-[11.5px] text-subtle">
                      #{task.id} · 来源 {maskSourceDomain(task.source_domain)} · 创建{' '}
                      {formatIsoTime(task.created_at)}
                      {task.finished_at ? ` · 完成 ${formatIsoTime(task.finished_at)}` : ''}
                    </p>
                  </div>
                  <Badge tone={tone}>{label}</Badge>
                  {!operable && task.target_disk_name && <Badge tone="neutral">只读</Badge>}
                </div>

                <div className="mt-2.5">
                  <Progress value={percent} />
                  <div className="tabular mt-1 flex items-center justify-between gap-2 text-[11.5px] text-subtle">
                    <span className="truncate">
                      {task.status === 'ready'
                        ? `已分析 ${task.item_total} 项，等待勾选`
                        : task.status === 'queued' || task.status === 'analyzing'
                          ? '服务端正在分析页面…'
                          : `${formatSize(task.bytes_done)} / ${formatSize(task.bytes_total)}`}
                    </span>
                    <span className="shrink-0">
                      {percent}%
                      {active && task.speed_bps > 0 ? ` · ${formatSpeed(task.speed_bps)}` : ''}
                    </span>
                  </div>
                </div>

                <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-subtle">
                  <span className="min-w-0 truncate" title={targetLabel}>
                    目标：{targetLabel}
                  </span>
                  {task.item_total > 0 && <span>共 {task.item_total} 项</span>}
                </div>

                {task.error_message && (
                  <details className="mt-1.5 text-[11.5px]" style={{ color: 'var(--danger-700)' }}>
                    <summary className="cursor-pointer break-words">
                      失败原因：{task.error_message}
                    </summary>
                    {suggestion && (
                      <p className="mt-1 mb-0 pl-4 text-subtle">建议：{suggestion}</p>
                    )}
                  </details>
                )}

                <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                  {task.status === 'ready' && (
                    <Button
                      size="sm"
                      variant="primary"
                      icon="checked"
                      disabled={busy}
                      onClick={() => onSelectTask(task.id)}
                    >
                      去选择
                    </Button>
                  )}
                  {active && operable && (
                    <Button
                      size="sm"
                      icon="close"
                      disabled={busy}
                      onClick={() =>
                        void runOp(
                          () => offlineDownloadService.cancel(task.id),
                          task.id,
                          '任务已取消',
                        )
                      }
                    >
                      取消
                    </Button>
                  )}
                  {(task.status === 'failed' || task.status === 'canceled') && operable && (
                    <Button
                      size="sm"
                      icon="refresh"
                      disabled={!retryable || busy}
                      title={retryable ? undefined : RETRY_BLOCKED_TITLE}
                      onClick={() =>
                        void runOp(
                          () => offlineDownloadService.retry(task.id),
                          task.id,
                          '已重新加入队列',
                        )
                      }
                    >
                      重试
                    </Button>
                  )}
                  {showOpen && (
                    <Button size="sm" icon="folder" disabled={busy} onClick={() => handleOpenInFiles(task)}>
                      在文件管理中打开
                    </Button>
                  )}
                  {showDelete && (
                    <Button
                      size="sm"
                      variant="ghost"
                      icon="delete"
                      disabled={busy}
                      onClick={() => setDeleteTarget(task)}
                    >
                      删除记录
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {total > tasks.length && (
        <p className="mt-2 text-[11.5px] text-subtle">
          共 {total} 条任务，仅展示最近 {tasks.length} 条
        </p>
      )}

      {!loading && tasks.length > 0 && !hasActiveTask && (
        <p className="mt-2 text-[11.5px] text-subtle">
          全部任务已结束，自动刷新已暂停；可点「刷新」查看其它端新建的任务。
        </p>
      )}

      {/* 删除记录：只删任务行，不删磁盘文件（F27.2） */}
      {deleteTarget && (
        <ConfirmDialog
          title="删除任务记录"
          message={`仅删除任务记录，不会删除磁盘上的文件。任务：${
            deleteTarget.page_title || deleteTarget.source_domain
          }（#${deleteTarget.id}）`}
          confirmLabel="确认删除"
          danger
          loading={busyTaskId === deleteTarget.id}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => void handleConfirmDelete()}
        />
      )}
    </div>
  );
}

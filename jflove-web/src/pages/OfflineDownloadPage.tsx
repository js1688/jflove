/**
 * 离线下载页（v1.8.0 新增）
 *
 * 需求 F23~F30 / 设计文档 §4。单页双布局：
 *   - PC（≥1024px）：`DesktopLayout` 侧栏入口 + 宽内容区；
 *   - 移动浏览器（<1024px）：`MobileLayout` 底部菜单入口 + 卡片式列表、全屏操作区。
 *
 * 页面职责：
 *   1. 「任务中心」：任务列表（状态 / 进度 / 来源 / 目标 / 失败原因 / 时间）+ 取消 / 重试 /
 *      删除记录 / 去选择 /「在文件管理中打开」；
 *   2. 「新建任务」：委托 `NewTaskPanel`（粘贴网址 → 分析 → 勾选 → 目录 → 开始下载 → 继续抓下一页）；
 *   3. 「我的默认目录」：委托 `OfflineSettingsModal`（按账号保存，服务端存储）；
 *   4. 轮询纪律（F27.3 / AC-232）：间隔取 `/offline/meta` 的 `poll_interval_ms`（默认 2500ms），
 *      页面不在前台或任务全部终态时**停止**，组件卸载清理定时器。
 *
 * 安全（`AGENTS.md` §9.6 / §9.7）：
 *   - 所有请求走 `offline-download-service` → `http-client.ts` 加密信封，无明文接口、无新加解密代码；
 *   - token / session_key 不落 localStorage / DOM / console；URL 与路径只提交给服务端，
 *     页面自身**不**发起任何对外请求。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { PageHeader } from '../components/PageHeader';
import { NewTaskPanel } from '../components/offline/NewTaskPanel';
import { OfflineSettingsModal } from '../components/offline/OfflineSettingsModal';
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Icon,
  IconButton,
  ListSkeleton,
  Progress,
  Segmented,
  toast,
} from '../components/ui';
import { offlineDownloadService } from '../services/offline-download-service';
import { useFileStore } from '../stores/file-store';
import {
  allTasksTerminal,
  canCancelTask,
  canDeleteRecord,
  canOperateTask,
  canRetryTask,
  formatIsoTime,
  isReadOnlyTask,
  resolvePollInterval,
  taskProgress,
  taskProgressText,
  taskStatusTone,
  unusableNotice,
} from '../utils/offline-download';
import type { OfflineMeta, OfflineTask } from '../types/models';

type Tab = 'tasks' | 'new';

/** 从异常里取可读消息 */
function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function OfflineDownloadPage() {
  const navigate = useNavigate();

  // ── 元信息（可写磁盘 / 默认目录 / 轮询间隔 / 不可用原因）──
  const [meta, setMeta] = useState<OfflineMeta | null>(null);
  const [metaLoading, setMetaLoading] = useState(true);
  const [metaError, setMetaError] = useState<string | null>(null);

  // ── 任务列表 ──
  const [tasks, setTasks] = useState<OfflineTask[]>([]);
  const [total, setTotal] = useState(0);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasksError, setTasksError] = useState<string | null>(null);
  const [busyTaskId, setBusyTaskId] = useState<number | null>(null);

  // ── 交互状态 ──
  const [tab, setTab] = useState<Tab>('tasks');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<OfflineTask | null>(null);
  /** 新建任务面板当前任务 id（任务中心「去选择」可带入 ready 任务） */
  const [draftTaskId, setDraftTaskId] = useState<number | null>(null);
  /** 自增触发轮询 effect 重新拉起（新建/操作任务后轮询可能已因全终态停止） */
  const [pollEpoch, setPollEpoch] = useState(0);
  /** 轮询是否正在运行（仅用于提示文案） */
  const [polling, setPolling] = useState(false);

  const pollMs = useMemo(
    () => resolvePollInterval(meta?.poll_interval_ms),
    [meta?.poll_interval_ms],
  );

  const loadMeta = useCallback(async (): Promise<boolean> => {
    try {
      const data = await offlineDownloadService.meta();
      setMeta(data);
      setMetaError(null);
      return true;
    } catch (e) {
      setMetaError(messageOf(e));
      return false;
    } finally {
      setMetaLoading(false);
    }
  }, []);

  const loadTasks = useCallback(async (): Promise<boolean> => {
    try {
      const data = await offlineDownloadService.listTasks(1, 50);
      setTasks(data.tasks);
      setTotal(data.total);
      setTasksError(null);
      // 返回「是否已全部终态」：true ⇒ 调用方停止轮询（AC-232）
      return allTasksTerminal(data.tasks);
    } catch (e) {
      setTasksError(messageOf(e));
      return false;
    } finally {
      setTasksLoading(false);
    }
  }, []);

  // 元信息只在挂载时拉一次（保存设置后由回调刷新）
  useEffect(() => {
    void loadMeta();
  }, [loadMeta]);

  // 磁盘列表用于「在文件管理中打开」按磁盘名反查 id（与修复中心同款做法）
  useEffect(() => {
    if (useFileStore.getState().disks.length === 0) {
      useFileStore.getState().loadDisks().catch(() => {
        /* 磁盘列表失败不影响离线下载页主流程 */
      });
    }
  }, []);

  /**
   * 任务列表轮询（F27.3 / AC-232）
   *
   * 停止条件：① `document.visibilityState !== 'visible'`（切后台/关页面）② 任务全部终态。
   * `visibilitychange` 回到前台时重新拉起；新建/操作任务后由 `pollEpoch` 重新拉起。
   *
   * 依赖数组**不含** tasks —— 否则每次 setTasks 都会重建定时器（SKILL.md 硬约束 6）。
   */
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
      if (!disposed) setPolling(false);
    };

    const start = () => {
      if (disposed || timer !== null) return;
      setPolling(true);
      timer = setInterval(() => {
        void tick();
      }, pollMs);
    };

    const tick = async () => {
      if (disposed) return;
      // 页面不在前台：不发请求并停表（后台不轮询）
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
        stop();
        return;
      }
      const terminal = await loadTasks();
      if (!disposed && terminal) stop();
    };

    void tick();
    start();

    const onVisibility = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'visible') {
        void tick();
        start();
      } else {
        stop();
      }
    };
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      disposed = true;
      if (timer !== null) clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [loadTasks, pollMs, pollEpoch]);

  /** 重新拉起轮询并立即刷新列表 */
  const refreshTasks = useCallback(() => {
    setPollEpoch((value) => value + 1);
  }, []);

  const handleRefresh = async () => {
    try {
      const ok = await loadMeta();
      refreshTasks();
      if (ok) toast.info('已刷新', '任务列表与可用状态已更新');
      else toast.error('刷新失败', '读取离线下载配置失败，请稍后重试');
    } catch (e) {
      toast.error('刷新失败', messageOf(e));
    }
  };

  const handleCancelTask = async (task: OfflineTask) => {
    setBusyTaskId(task.id);
    try {
      await offlineDownloadService.cancel(task.id);
      toast.success('已取消', `任务 #${task.id} 已停止，临时文件会被清理`);
      refreshTasks();
    } catch (e) {
      toast.error('取消失败', messageOf(e));
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleRetryTask = async (task: OfflineTask) => {
    setBusyTaskId(task.id);
    try {
      await offlineDownloadService.retry(task.id);
      toast.success('已重新排队', `任务 #${task.id} 将重新开始`);
      refreshTasks();
    } catch (e) {
      toast.error('重试失败', messageOf(e));
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleDeleteRecord = async (task: OfflineTask) => {
    setDeleteTarget(null);
    setBusyTaskId(task.id);
    try {
      await offlineDownloadService.deleteRecord(task.id);
      toast.success('记录已删除', '磁盘上的已下载文件不受影响');
      refreshTasks();
    } catch (e) {
      toast.error('删除记录失败', messageOf(e));
    } finally {
      setBusyTaskId(null);
    }
  };

  /** 「在文件管理中打开」：按磁盘名反查磁盘 id，路径走 router state（不放进 URL） */
  const handleOpenInFiles = (task: OfflineTask) => {
    const disks = useFileStore.getState().disks;
    const disk = disks.find((d) => d.name === task.target_disk_name);
    if (!disk) {
      toast.error(
        '无法打开所在目录',
        `找不到磁盘「${task.target_disk_name || '未知'}」，可能已被删除或无访问权限`,
      );
      return;
    }
    // 说明：目标路径不写进 URL（§9.4 不把用户内容放进地址栏），改用路由 state 传出
    navigate(`/files/${disk.id}`, {
      state: { browsePath: task.target_rel_path ?? '' },
    });
  };

  const handleSelectTask = (task: OfflineTask) => {
    setDraftTaskId(task.id);
    setTab('new');
  };

  const notice = meta ? unusableNotice(meta.unusable_reason) : null;
  const creationDisabled = !meta || !meta.can_use || !meta.configured;
  const stopped = !tasksLoading && tasks.length > 0 && allTasksTerminal(tasks) && !polling;

  /* ==================== 渲染 ==================== */

  return (
    <div>
      <PageHeader
        title="离线下载"
        subtitle="粘贴网址，抓取视频 / 音频 / 图片"
        actions={
          <>
            <IconButton
              icon="refresh"
              label="刷新"
              size="sm"
              onClick={() => {
                void handleRefresh();
              }}
            />
            <Button
              size="sm"
              icon="settings"
              onClick={() => {
                if (meta) {
                  setSettingsOpen(true);
                } else {
                  toast.error('配置尚未加载', '请先点「刷新」读取离线下载配置');
                }
              }}
            >
              设置
            </Button>
          </>
        }
      />

      <div className="mx-auto max-w-5xl p-4 sm:p-6">
        {/* ── 不可用原因四态（F25.3 / AC-214 / AC-214b / AC-214c）── */}
        {metaLoading && <ListSkeleton rows={4} />}

        {!metaLoading && metaError && (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3.5 text-[13px] text-danger-700">
            <Icon name="error" size="sm" className="mt-px shrink-0" />
            <span className="break-words">读取离线下载配置失败：{metaError}</span>
          </div>
        )}

        {!metaLoading && notice && (
          <div
            className={[
              'mb-4 flex items-start gap-2 rounded-lg border p-3.5 text-[13px]',
              notice.needsConfigure
                ? 'border-warning-500/30 bg-warning-50 text-warning-700'
                : 'border-danger-500/25 bg-danger-50 text-danger-700',
            ].join(' ')}
            role="status"
          >
            <Icon name="warning" size="sm" className="mt-px shrink-0" />
            <div className="min-w-0">
              <p className="m-0 font-semibold">{notice.title}</p>
              <p className="m-0 mt-0.5 text-[12px] leading-relaxed opacity-90">
                {notice.description}
              </p>
              {notice.needsConfigure && (
                <Button
                  className="mt-2"
                  size="sm"
                  variant="primary"
                  icon="settings"
                  onClick={() => setSettingsOpen(true)}
                >
                  去设置目录
                </Button>
              )}
            </div>
          </div>
        )}

        {!metaLoading && meta && !notice && (
          <p className="mb-3 text-[11.5px] text-subtle">
            默认目录：{meta.writable_disks.find((d) => d.disk_id === meta.config.disk_id)?.name ?? '—'}
            {' / '}
            {meta.config.rel_path || '根目录'}
            {meta.deep_analyze_enabled && !meta.deep_analyze_available && (
              <span> · 当前部署未启用深度分析</span>
            )}
          </p>
        )}

        <div className="mb-4">
          <Segmented<Tab>
            value={tab}
            onChange={setTab}
            options={[
              { value: 'tasks', label: '任务中心', icon: 'transfer' },
              { value: 'new', label: '新建任务', icon: 'plus' },
            ]}
          />
        </div>

        {tab === 'tasks' ? (
          <div>
            {tasksError && (
              <div className="mb-4 flex items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3.5 text-[13px] text-danger-700">
                <Icon name="error" size="sm" className="mt-px shrink-0" />
                <span className="break-words">加载任务列表失败：{tasksError}</span>
              </div>
            )}

            {tasksLoading ? (
              <ListSkeleton rows={4} />
            ) : tasks.length === 0 ? (
              <EmptyState
                icon="download"
                title="暂无离线下载任务"
                description="到「新建任务」粘贴一个公开网址，分析出清单后即可下载"
                action={
                  <Button variant="primary" icon="plus" onClick={() => setTab('new')}>
                    新建任务
                  </Button>
                }
              />
            ) : (
              <div className="flex flex-col gap-2">
                {tasks.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    busy={busyTaskId === task.id}
                    onCancel={handleCancelTask}
                    onRetry={handleRetryTask}
                    onDeleteRecord={setDeleteTarget}
                    onSelect={handleSelectTask}
                    onOpenInFiles={handleOpenInFiles}
                  />
                ))}
                <p className="mt-1 text-[11.5px] text-subtle">
                  共 {total} 条任务
                  {stopped
                    ? ' · 全部任务已结束，轮询已暂停（可点右上刷新查看最新）'
                    : ` · 每 ${Math.round(pollMs / 100) / 10} 秒自动刷新（页面在后台时暂停）`}
                </p>
              </div>
            )}

            {creationDisabled && (
              <p className="mt-4 text-[11.5px] text-subtle">
                {meta && !meta.can_use
                  ? '当前账号不可使用离线下载：分析 / 下载入口已禁用。'
                  : '尚未配置默认目录：新建任务暂不可用。'}
              </p>
            )}
          </div>
        ) : meta ? (
          <NewTaskPanel
            meta={meta}
            pollMs={pollMs}
            taskId={draftTaskId}
            onTaskIdChange={setDraftTaskId}
            onTasksChanged={refreshTasks}
            onOpenSettings={() => setSettingsOpen(true)}
            onGotoTasks={() => setTab('tasks')}
          />
        ) : (
          <Card>
            <p className="m-0 text-[13px] text-muted">
              正在读取离线下载配置…若长时间无响应，请点右上「刷新」。
            </p>
          </Card>
        )}
      </div>

      {settingsOpen && meta && (
        <OfflineSettingsModal
          meta={meta}
          onClose={() => setSettingsOpen(false)}
          onSaved={() => {
            setSettingsOpen(false);
            void loadMeta();
            refreshTasks();
          }}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="删除任务记录"
          message={
            `将删除任务 #${deleteTarget.id} 的记录与临时文件。` +
            '已下载完成的文件不会被删除（删文件请到文件管理）。'
          }
          confirmLabel="删除记录"
          danger
          onConfirm={() => {
            void handleDeleteRecord(deleteTarget);
          }}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}

/* ==================== 任务卡片 ==================== */

interface TaskRowProps {
  task: OfflineTask;
  busy: boolean;
  onCancel: (task: OfflineTask) => Promise<void>;
  onRetry: (task: OfflineTask) => Promise<void>;
  onDeleteRecord: (task: OfflineTask) => void;
  onSelect: (task: OfflineTask) => void;
  onOpenInFiles: (task: OfflineTask) => void;
}

/**
 * 单条任务卡片（PC / 移动浏览器共用；窄屏自动换行）
 *
 * 按钮可见性严格按状态机（后端还会二次校验，前端只做引导）：
 *   - 进行中（排队/分析/下载/合并）→ 取消
 *   - 待选择（ready）→ 去选择 / 取消 / 删除记录
 *   - 成功 → 在文件管理中打开 / 删除记录
 *   - 失败/已取消 → 重试（可重试类） / 删除记录
 *
 * 写操作的可用性用 `canOperateTask` 判定，而不是直接看 `task.can_operate`：
 * 分析中/待选择阶段任务尚未绑定目标磁盘，服务端必然返回 `can_operate=false`，
 * 那是"还没选目录"而不是"没有权限"（详见 `utils/offline-download.ts` 的注释）。
 */
function TaskRow({
  task,
  busy,
  onCancel,
  onRetry,
  onDeleteRecord,
  onSelect,
  onOpenInFiles,
}: TaskRowProps) {
  const percent = taskProgress(task);
  const operable = canOperateTask(task) && !busy;

  return (
    <Card pad="none">
      <div className="flex flex-wrap items-start gap-3 px-4 py-3">
        <span className="file-ico file-ico-video">
          <Icon name="download" size="lg" />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 truncate text-[13px] font-medium">
              {task.page_title || '（未命名页面）'}
            </span>
            <Badge tone={taskStatusTone(task.status)}>{task.status_text}</Badge>
            {isReadOnlyTask(task) && <Badge tone="neutral">只读</Badge>}
          </div>

          <p className="mt-0.5 text-[11.5px] text-subtle">
            #{task.id} · {task.source_domain || '未知来源'} · 创建 {formatIsoTime(task.created_at)}
            {task.finished_at ? ` · 完成 ${formatIsoTime(task.finished_at)}` : ''}
          </p>

          {/* 进度条对所有状态可见：成功任务固定 100%（v1.8.1 / AC-256） */}
          <div className="mt-2">
            <Progress value={percent} />
          </div>
          <p className="tabular mt-1 text-[11.5px] text-muted">{taskProgressText(task)}</p>

          <p className="mt-0.5 text-[11.5px] text-subtle">
            目标：{task.target_disk_name || '未指定'} / {task.target_rel_path || '—'}
          </p>

          {task.error_message && (
            <p className="mt-0.5 text-[11.5px] text-danger-700">
              {task.error_code ? `${task.error_message}（${task.error_code}）` : task.error_message}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {canCancelTask(task) && (
            <Button size="sm" icon="close" disabled={!operable} onClick={() => void onCancel(task)}>
              取消
            </Button>
          )}

          {task.status === 'ready' && (
            <Button
              size="sm"
              variant="primary"
              icon="next"
              onClick={() => onSelect(task)}
            >
              去选择
            </Button>
          )}

          {canRetryTask(task) && (
            <Button
              size="sm"
              icon="refresh"
              disabled={!operable}
              onClick={() => void onRetry(task)}
            >
              重试
            </Button>
          )}

          {task.status === 'success' && (
            <Button size="sm" icon="folder" onClick={() => onOpenInFiles(task)}>
              在文件管理中打开
            </Button>
          )}

          {canDeleteRecord(task) && (
            <Button
              size="sm"
              icon="delete"
              disabled={!operable}
              onClick={() => onDeleteRecord(task)}
            >
              删除记录
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

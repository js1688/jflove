/**
 * 离线下载 · 新建任务（v1.8.0）
 *
 * 对应需求 F23 / F24 / F25 / 设计 §3.1 / §3.4：
 *   1. 粘贴 URL → `POST /offline/analyze`，随后按 `poll_interval_ms` 轮询 `/detail`
 *      直到 `ready`（分析中超过 5 秒给出「最多 30 秒」提示）；
 *   2. 清单勾选（全选 / 只要最高清 / 只要音频 / 只要图片）→ 可「继续抓下一页」；
 *   3. 存放位置以 `/meta.config` 预填，可临时改（**不**回写账号默认目录）；
 *   4. 开始下载 → `POST /offline/select`，成功后交给父组件切回任务中心。
 *
 * 兜底：任务一旦进入 `ready` 但账号默认目录已失效，仍允许用户临时选一个可写目录
 * （`select` 带显式 `disk_id/target_path` 时服务端不依赖默认配置）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  EmptyState,
  Icon,
  Input,
  toast,
} from '../../components/ui';
import { DirTreeModal } from '../../components/DirTreeModal';
import { useVisiblePolling } from '../../hooks/use-polling';
import {
  offlineDownloadService,
  type OfflineDetail,
  type OfflineItem,
  type OfflineMeta,
  type OfflineTask,
} from '../../services/offline-download-service';
import { ItemPicker } from './ItemPicker';
import {
  errorSuggestion,
  isTerminalStatus,
  maskSourceDomain,
  offlineErrorText,
  selectedSummaryLabel,
} from './offline-utils';

type Phase = 'input' | 'analyzing' | 'ready';

interface NewTaskDialogProps {
  meta: OfflineMeta;
  /** 轮询间隔（来自 `/meta.poll_interval_ms`，默认 2500ms） */
  pollIntervalMs: number;
  /** 从任务中心「去选择」带入的待选择任务 ID */
  presetTaskId: number | null;
  /** 已消费 presetTaskId（父组件清空，避免重复加载） */
  onPresetConsumed: () => void;
  /** 已成功提交下载 → 父组件切回任务中心 */
  onStarted: (taskId: number) => void;
  /** 打开设置面板（配置默认目录） */
  onOpenSettings: () => void;
}

/** 从 meta 计算初始存放位置：默认目录有效则预填，否则退回第一个可写磁盘根目录 */
function initialTarget(meta: OfflineMeta): { diskId: number; relPath: string } {
  const config = meta.config;
  if (config.valid && meta.writable_disks.some((disk) => disk.disk_id === config.disk_id)) {
    return { diskId: config.disk_id, relPath: config.rel_path };
  }
  const first = meta.writable_disks[0];
  return { diskId: first ? first.disk_id : 0, relPath: '' };
}

/** 账号不可用（总开关关闭 / 无可写磁盘）时的中文原因 */
function hardDisabledReason(meta: OfflineMeta): string {
  if (!meta.enabled) return '离线下载功能已关闭，请联系管理员开启。';
  if (meta.writable_disks.length === 0) {
    return '你没有可写入的磁盘，无法使用离线下载。请联系管理员为你的账号分配可写磁盘。';
  }
  return '';
}

/** 新建任务面板 */
export function NewTaskDialog({
  meta,
  pollIntervalMs,
  presetTaskId,
  onPresetConsumed,
  onStarted,
  onOpenSettings,
}: NewTaskDialogProps) {
  const initial = initialTarget(meta);
  const [url, setUrl] = useState('');
  const [phase, setPhase] = useState<Phase>('input');
  const [taskId, setTaskId] = useState<number | null>(null);
  const [task, setTask] = useState<OfflineTask | null>(null);
  const [items, setItems] = useState<OfflineItem[]>([]);
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [hasMore, setHasMore] = useState(false);
  const [sourceDomain, setSourceDomain] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [nextLoading, setNextLoading] = useState(false);
  const [slowHint, setSlowHint] = useState(false);
  const [diskId, setDiskId] = useState(initial.diskId);
  const [relPath, setRelPath] = useState(initial.relPath);
  const [packZip, setPackZip] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);

  const hardDisabled = !meta.enabled || meta.writable_disks.length === 0;
  const targetDisk = meta.writable_disks.find((disk) => disk.disk_id === diskId);

  // meta 变化（设置保存后重新拉取）时重置存放位置为新的默认值
  useEffect(() => {
    const next = initialTarget(meta);
    setDiskId(next.diskId);
    setRelPath(next.relPath);
    setPackZip(false);
  }, [meta]);

  /** 回到「输入网址」状态（保留已填 URL，便于换地址重试） */
  const resetToInput = useCallback(() => {
    setPhase('input');
    setTaskId(null);
    setTask(null);
    setItems([]);
    setSelected(new Set());
    setHasMore(false);
    setSlowHint(false);
    setError(null);
  }, []);

  /** 应用 `/detail`：清单到齐 → ready，并默认勾选全部项（服务端 is_selected 默认 0） */
  const applyDetail = useCallback((detail: OfflineDetail) => {
    setTask(detail.task);
    setItems(detail.items);
    setHasMore(detail.has_more_images);
    setSourceDomain(detail.task.source_domain);
    const selectedOnServer = detail.items.filter((item) => item.is_selected).map((item) => item.id);
    setSelected(new Set(selectedOnServer.length > 0 ? selectedOnServer : detail.items.map((i) => i.id)));
    setTaskId(detail.task.id);
    setPhase('ready');
    setError(null);
  }, []);

  /**
   * 分析阶段轮询详情：`ready` → 展示清单；失败/取消/过期 → 中文错误并回到输入态。
   * 轮询本身由 `useVisiblePolling` 做可见性守卫与卸载清理。
   */
  const pollDetail = useCallback(async () => {
    if (taskId === null) return;
    try {
      const detail = await offlineDownloadService.getDetail(taskId);
      if (detail.task.status === 'ready') {
        applyDetail(detail);
        return;
      }
      if (isTerminalStatus(detail.task.status)) {
        const suggestion = errorSuggestion(detail.task.error_code);
        // 先回到输入态（resetToInput 会清空 error），再写入失败原因
        resetToInput();
        setError(
          `${detail.task.error_message || '分析失败'}${suggestion ? `（${suggestion}）` : ''}`,
        );
      }
    } catch (e) {
      resetToInput();
      setError(offlineErrorText(e, '读取分析结果失败'));
    }
  }, [applyDetail, resetToInput, taskId]);

  useVisiblePolling(pollDetail, pollIntervalMs, phase === 'analyzing' && taskId !== null);

  // 从任务中心「去选择」进入：直接加载该任务的详情
  useEffect(() => {
    if (presetTaskId === null) return;
    setPhase('analyzing');
    setTaskId(presetTaskId);
    setTask(null);
    setItems([]);
    setSelected(new Set());
    setHasMore(false);
    setSlowHint(false);
    setError(null);
    setSourceDomain('');
    onPresetConsumed();
  }, [onPresetConsumed, presetTaskId]);

  // 分析中超过 5 秒后提示「深度分析最多 30 秒」（设计 §3.2）
  useEffect(() => {
    if (phase !== 'analyzing') {
      setSlowHint(false);
      return;
    }
    const timer = window.setTimeout(() => setSlowHint(true), 5000);
    return () => window.clearTimeout(timer);
  }, [phase, taskId]);

  const handleAnalyze = async () => {
    const value = url.trim();
    if (!value) {
      setError('请输入网页地址');
      return;
    }
    if (!meta.configured) {
      setError(
        meta.config.valid
          ? '请先设置离线下载目录后再分析。'
          : '离线下载目录已失效，请重新配置后再分析。',
      );
      return;
    }
    setError(null);
    setTask(null);
    setTaskId(null);
    setItems([]);
    setSelected(new Set());
    setHasMore(false);
    setSlowHint(false);
    setSourceDomain('');
    setPhase('analyzing');
    setRequesting(true);
    try {
      const result = await offlineDownloadService.analyze(value);
      setTaskId(result.task_id);
      setSourceDomain(result.source_domain);
    } catch (e) {
      setPhase('input');
      setTaskId(null);
      setError(offlineErrorText(e, '分析失败'));
    } finally {
      setRequesting(false);
    }
  };

  const handleCancelAnalysis = async () => {
    if (taskId !== null) {
      try {
        await offlineDownloadService.cancel(taskId);
        toast.success('已取消分析');
      } catch (e) {
        toast.error('取消分析失败', offlineErrorText(e));
      }
    }
    resetToInput();
  };

  const handleToggle = (itemId: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  };

  const handleSelectionChange = (ids: number[]) => {
    setSelected(new Set(ids));
  };

  const handleNextPage = async () => {
    if (taskId === null) return;
    setNextLoading(true);
    setError(null);
    try {
      const before = new Set(items.map((item) => item.id));
      const result = await offlineDownloadService.nextPage(taskId);
      const detail = await offlineDownloadService.getDetail(taskId);
      setTask(detail.task);
      setItems(detail.items);
      setHasMore(detail.has_more_images);
      const added = detail.items.filter((item) => !before.has(item.id)).map((item) => item.id);
      setSelected((prev) => new Set([...prev, ...added]));
      toast.success(`已追加 ${result.added} 项`);
    } catch (e) {
      setError(offlineErrorText(e, '继续抓取下一页失败'));
    } finally {
      setNextLoading(false);
    }
  };

  const hasSelectedImage = useMemo(
    () => items.some((item) => item.kind === 'image' && selected.has(item.id)),
    [items, selected],
  );

  const handleStart = async () => {
    if (taskId === null) return;
    if (selected.size === 0) {
      setError('请至少勾选一项');
      return;
    }
    if (diskId <= 0) {
      setError('请选择目标磁盘');
      return;
    }
    setStarting(true);
    setError(null);
    try {
      await offlineDownloadService.select(
        taskId,
        [...selected],
        diskId,
        relPath.trim(),
        hasSelectedImage && packZip,
      );
      toast.success('已开始下载，可在任务中心查看进度');
      resetToInput();
      setUrl('');
      onStarted(taskId);
    } catch (e) {
      setError(offlineErrorText(e, '开始下载失败'));
    } finally {
      setStarting(false);
    }
  };

  const disabledReason = hardDisabledReason(meta);
  const isTemporaryTarget =
    meta.config.valid &&
    (diskId !== meta.config.disk_id || relPath.trim() !== meta.config.rel_path);

  if (hardDisabled) {
    return (
      <div className="mx-auto max-w-4xl p-4 sm:p-6">
        <EmptyState
          icon="download"
          title="离线下载不可用"
          description={disabledReason}
          action={
            <Button icon="settings" onClick={onOpenSettings}>
              查看设置
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl p-4 sm:p-6">
      {/* 1. 地址输入 */}
      <section className="card p-5">
        <h2 className="m-0 flex items-center gap-2 text-[14px] font-semibold">
          <span className="badge badge-brand">1</span>
          粘贴网页地址
        </h2>
        <p className="mt-1.5 mb-0 text-[12.5px] leading-relaxed text-muted">
          只分析页面中的可下载内容（视频/音频/图片），不会在渲染层发起任何外部请求。
          URL 仅提交给服务端进行分析。
        </p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <Input
            className="flex-1"
            value={url}
            placeholder="https://example.com/video-page"
            disabled={requesting || phase === 'ready'}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && phase !== 'ready' && !requesting) {
                void handleAnalyze();
              }
            }}
            aria-label="网页地址"
          />
          {phase !== 'ready' ? (
            <Button
              variant="primary"
              icon="search"
              loading={requesting}
              disabled={!meta.configured || requesting || !url.trim()}
              onClick={() => void handleAnalyze()}
            >
              分析网址
            </Button>
          ) : (
            <Button icon="refresh" onClick={resetToInput}>
              换一个网址
            </Button>
          )}
        </div>

        {!meta.configured && (
          <div className="mt-3 flex flex-wrap items-start gap-2 rounded-lg border border-warning-500/25 bg-warning-50 p-3 text-[12.5px] text-warning-700">
            <Icon name="warning" size="sm" className="mt-px shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="m-0 font-semibold">
                {meta.config.valid
                  ? '请先设置离线下载目录'
                  : '离线下载目录已失效，请重新配置'}
              </p>
              <p className="mt-0.5 mb-0">
                服务端要求账号先有可写的默认目录才会发起分析，避免分析完无处存放。
              </p>
            </div>
            <Button size="sm" icon="settings" onClick={onOpenSettings}>
              去设置
            </Button>
          </div>
        )}

        {!meta.deep_analyze_available && (
          <p className="mt-2 mb-0 text-[11.5px] text-subtle">
            当前部署未启用深度分析：只能解析静态页面与播放列表，JS 动态注入的站点可能无法出清单。
          </p>
        )}
      </section>

      {error && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3.5 text-[13px] text-danger-700">
          <Icon name="error" size="sm" className="mt-px shrink-0" />
          <span className="break-words">{error}</span>
        </div>
      )}

      {/* 分析中 */}
      {phase === 'analyzing' && (
        <section className="card mt-3 p-5">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <Icon name="loading" className="animate-spin text-brand-500" />
            正在分析页面…
          </div>
          <p className="mt-1.5 mb-0 text-[12.5px] leading-relaxed text-muted">
            {maskSourceDomain(sourceDomain)}
            {slowHint
              ? ' · 正在深度分析，最多 30 秒（离开本页不会中断服务端分析，可稍后在任务中心找到结果）'
              : ' · 正在抓取页面中的可下载内容…'}
          </p>
          <Button size="sm" icon="close" className="mt-3" onClick={() => void handleCancelAnalysis()}>
            取消分析
          </Button>
        </section>
      )}

      {/* 2. 清单 + 3. 目录 */}
      {phase === 'ready' && task && (
        <>
          <section className="card mt-3 p-5">
            <div className="flex flex-wrap items-center gap-2">
              <Icon name="checked" className="text-success-500" />
              <div className="min-w-0 flex-1">
                <p className="m-0 truncate text-[13px] font-medium" title={task.page_title}>
                  {task.page_title || task.source_domain || '分析完成'}
                </p>
                <p className="tabular m-0 mt-0.5 text-[11.5px] text-subtle">
                  来源 {maskSourceDomain(task.source_domain)} · 共 {items.length} 项 · 任务 #{task.id}
                </p>
              </div>
              <Badge tone="success">分析完成</Badge>
            </div>
          </section>

          <div className="mt-3">
            <h2 className="m-0 mb-2 flex items-center gap-2 text-[14px] font-semibold">
              <span className="badge badge-brand">2</span>
              勾选可下载项
            </h2>
            <ItemPicker
              items={items}
              selectedIds={selected}
              disabled={starting}
              hasMore={hasMore}
              nextPageLoading={nextLoading}
              onToggle={handleToggle}
              onSelectionChange={handleSelectionChange}
              onNextPage={() => void handleNextPage()}
            />
          </div>

          <section className="card mt-3 p-5">
            <h2 className="m-0 flex items-center gap-2 text-[14px] font-semibold">
              <span className="badge badge-brand">3</span>
              选择存放位置
            </h2>
            <p className="mt-1.5 mb-0 text-[12.5px] leading-relaxed text-muted">
              默认目录来自服务端账号配置（桌面端/Web 端共用）。此处修改只影响本次任务，
              <span className="font-medium">不会写回默认目录</span>。
            </p>

            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
              <label className="block text-[12.5px]">
                <span className="mb-1 block font-medium text-muted">目标磁盘</span>
                <select
                  className="input bg-surface"
                  value={diskId}
                  disabled={starting}
                  onChange={(e) => setDiskId(Number(e.target.value))}
                  aria-label="目标磁盘"
                >
                  {meta.writable_disks.map((disk) => (
                    <option key={disk.disk_id} value={disk.disk_id}>
                      {disk.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="text-[12.5px] sm:col-span-2">
                <span className="mb-1 block font-medium text-muted">目录（相对路径）</span>
                <div className="flex gap-2">
                  <Input
                    className="flex-1"
                    value={relPath}
                    disabled={starting}
                    placeholder="如 影视/离线下载（留空表示磁盘根目录）"
                    onChange={(e) => setRelPath(e.target.value)}
                    aria-label="目标目录"
                  />
                  <Button
                    icon="folder"
                    disabled={starting || diskId <= 0}
                    onClick={() => setBrowseOpen(true)}
                  >
                    浏览…
                  </Button>
                </div>
              </div>
            </div>

            {isTemporaryTarget && (
              <p className="mt-2 mb-0 text-[11.5px] text-warning-700">
                已临时修改目录：本次下载会写入所选位置，账号默认目录保持不变。
              </p>
            )}

            {hasSelectedImage && (
              <label className="mt-3 flex items-center gap-2 text-[12.5px] text-muted">
                <input
                  type="checkbox"
                  className="accent-brand-500"
                  checked={packZip}
                  disabled={starting}
                  onChange={(e) => setPackZip(e.target.checked)}
                />
                打包为 zip（可选；zip 在文件管理中不能逐张预览）
              </label>
            )}
          </section>

          <div className="card mt-3 flex flex-wrap items-center gap-2 p-4">
            <Icon name="checked" size="sm" className="text-brand-500" />
            <span className="tabular text-[12.5px] text-muted">
              {selectedSummaryLabel(items, selected)}
            </span>
            <div className="grow" />
            <Button variant="ghost" icon="refresh" disabled={starting} onClick={resetToInput}>
              重新分析
            </Button>
            <Button
              variant="primary"
              icon="download"
              loading={starting}
              disabled={selected.size === 0 || diskId <= 0 || starting}
              title={selected.size === 0 ? '请至少勾选一项' : undefined}
              onClick={() => void handleStart()}
            >
              开始下载
            </Button>
          </div>
        </>
      )}

      {browseOpen && targetDisk && (
        <DirTreeModal
          diskId={targetDisk.disk_id}
          diskName={targetDisk.name}
          onSelect={(path) => {
            setRelPath(path);
            setBrowseOpen(false);
          }}
          onClose={() => setBrowseOpen(false)}
        />
      )}
    </div>
  );
}

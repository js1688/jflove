/**
 * 离线下载 · 新建任务面板（v1.8.0）
 *
 * 流程（需求 F23 / F24 / F25，设计文档 §3.1 / §4）：
 *   粘贴网址 → 分析（服务端队列）→ 清单勾选（全选 / 快捷选择）→ 目录（默认值预填、可临时改）
 *   → 开始下载 → 「继续抓下一页」追加图片
 *
 * 纪律：
 *   - 分析中按 `poll_interval_ms` 轮询 `/offline/detail`，进入终态/ready 后**停止**；
 *   - 目录默认值来自 `/offline/meta`，**不写 localStorage**；此处临时修改**不回写**默认值（F25.5）；
 *   - 所有会发请求的 handler 必须 try/catch + `toast.error`，失败时不重置界面（SKILL.md 硬约束 1）。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { DirTreeModal } from '../DirTreeModal';
import { Badge, Button, Card, Icon, ListSkeleton, toast } from '../ui';
import { offlineDownloadService } from '../../services/offline-download-service';
import { formatSize } from '../../utils/format';
import {
  canAnalyze,
  formatIsoTime,
  hasItemKind,
  itemMetaText,
  itemSizeText,
  pickByQuickSelect,
  pickNearestSelection,
  summarizeSelection,
  type QuickPickMode,
} from '../../utils/offline-download';
import type {
  OfflineItem,
  OfflineMeta,
  OfflineTaskDetail,
} from '../../types/models';

export interface NewTaskPanelProps {
  meta: OfflineMeta;
  /** 轮询间隔（毫秒，来自 `/offline/meta`） */
  pollMs: number;
  /** 由任务中心「去选择」带入的任务 id；`null` 表示从粘贴网址开始 */
  taskId: number | null;
  onTaskIdChange: (taskId: number | null) => void;
  /** 任务状态变化（分析排队/完成/提交下载）后通知父级重新拉任务列表并拉起轮询 */
  onTasksChanged: () => void;
  /** 打开「我的默认目录」设置 */
  onOpenSettings: () => void;
  /** 切回任务中心 */
  onGotoTasks: () => void;
}

/** 从异常里取可读消息 */
function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 清单项类型图标 */
const KIND_ICON: Record<string, { icon: 'video' | 'audio' | 'image' | 'doc'; box: string }> = {
  video: { icon: 'video', box: 'file-ico file-ico-video' },
  audio: { icon: 'audio', box: 'file-ico file-ico-doc' },
  image: { icon: 'image', box: 'file-ico file-ico-img' },
  subtitle: { icon: 'doc', box: 'file-ico file-ico-doc' },
};

export function NewTaskPanel({
  meta,
  pollMs,
  taskId,
  onTaskIdChange,
  onTasksChanged,
  onOpenSettings,
  onGotoTasks,
}: NewTaskPanelProps) {
  const analyzeAllowed = canAnalyze(meta);

  const [url, setUrl] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [draft, setDraft] = useState<OfflineTaskDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  /** 自增触发重新拉详情（「继续抓下一页」后用） */
  const [detailEpoch, setDetailEpoch] = useState(0);
  const [selected, setSelected] = useState<Set<number> | null>(null);
  const [nextPageBusy, setNextPageBusy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [slowAnalyze, setSlowAnalyze] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [packZip, setPackZip] = useState(false);

  // 任务内临时目标（默认取账号默认目录；临时改不回写）
  const [diskId, setDiskId] = useState<number>(() => {
    if (meta.config.disk_id > 0) return meta.config.disk_id;
    return meta.writable_disks[0]?.disk_id ?? 0;
  });
  const [relPath, setRelPath] = useState(meta.config.rel_path ?? '');

  /** 分析开始时间（用于「已超过 5 秒」的深度分析提示） */
  const analyzeStartedRef = useRef<number | null>(null);

  // 打开另一个任务（或重新开始）时清空草稿
  useEffect(() => {
    setDraft(null);
    setDetailError(null);
    setSelected(null);
    setSlowAnalyze(false);
    analyzeStartedRef.current = null;
  }, [taskId]);

  // 默认目录被保存/刷新后同步到临时目标（只在用户尚未提交时使用）
  useEffect(() => {
    setDiskId(meta.config.disk_id > 0 ? meta.config.disk_id : meta.writable_disks[0]?.disk_id ?? 0);
    setRelPath(meta.config.rel_path ?? '');
  }, [meta.config.disk_id, meta.config.rel_path, meta.writable_disks]);

  // 详情轮询：分析中每 pollMs 拉一次，进入 ready / 终态即停
  useEffect(() => {
    if (!taskId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const data = await offlineDownloadService.detail(taskId);
        if (disposed) return;
        setDraft(data);
        setDetailError(null);
        if (data.task.status === 'ready') {
          // 分析完成：通知任务中心刷新一次（之后不再轮询详情）
          onTasksChanged();
        }
        if (data.task.status === 'queued' || data.task.status === 'analyzing') {
          timer = setTimeout(() => {
            void tick();
          }, pollMs);
        }
      } catch (e) {
        if (!disposed) setDetailError(messageOf(e));
      }
    };

    void tick();
    return () => {
      disposed = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [taskId, pollMs, detailEpoch, onTasksChanged]);

  // 首次进入 ready 时默认勾选「最高清」一项（之后不再覆盖用户选择）
  useEffect(() => {
    if (!draft || draft.task.status !== 'ready') return;
    setSelected((prev) => prev ?? new Set(pickNearestSelection(draft.items)));
  }, [draft]);

  // 分析超过 5 秒时提示「正在深度分析，最多 30 秒」（设计文档 §3.2）
  useEffect(() => {
    const status = draft?.task.status;
    if (status !== 'queued' && status !== 'analyzing') {
      setSlowAnalyze(false);
      return;
    }
    const started = analyzeStartedRef.current ?? Date.now();
    analyzeStartedRef.current = started;
    const timer = setTimeout(
      () => setSlowAnalyze(true),
      Math.max(0, 5000 - (Date.now() - started)),
    );
    return () => clearTimeout(timer);
  }, [draft?.task.status]);

  const items = useMemo<OfflineItem[]>(() => draft?.items ?? [], [draft]);
  const selectedIds = useMemo<Set<number>>(
    () => selected ?? new Set<number>(),
    [selected],
  );
  const summary = useMemo(
    () => summarizeSelection(items, selectedIds),
    [items, selectedIds],
  );

  const diskName = meta.writable_disks.find((d) => d.disk_id === diskId)?.name ?? '';

  /** 是否就是账号默认目录（空相对路径时只有命中默认目录才允许提交） */
  const isDefaultTarget =
    meta.configured &&
    meta.config.valid &&
    meta.config.disk_id === diskId &&
    (meta.config.rel_path ?? '') === relPath.trim();

  const handleAnalyze = async () => {
    const target = url.trim();
    if (!target) {
      toast.error('请先填写网址', '粘贴一个 http(s) 网址后再分析');
      return;
    }
    setAnalyzing(true);
    analyzeStartedRef.current = Date.now();
    try {
      const result = await offlineDownloadService.analyze(target);
      toast.success('已开始分析', `来源 ${result.source_domain || '未知站点'}，通常几秒内完成`);
      onTaskIdChange(result.task_id);
      onTasksChanged();
    } catch (e) {
      toast.error('分析失败', messageOf(e));
    } finally {
      setAnalyzing(false);
    }
  };

  const handleNextPage = async () => {
    if (!taskId) return;
    setNextPageBusy(true);
    try {
      const result = await offlineDownloadService.nextPage(taskId);
      toast.success(
        `已追加 ${result.added} 项`,
        result.has_more ? `当前共 ${result.item_total} 项，可继续抓下一页` : '已到最后一页',
      );
      setDetailEpoch((value) => value + 1);
      onTasksChanged();
    } catch (e) {
      toast.error('继续抓下一页失败', messageOf(e));
    } finally {
      setNextPageBusy(false);
    }
  };

  const applyQuickPick = (mode: QuickPickMode) => {
    setSelected(new Set(pickByQuickSelect(items, mode)));
  };

  const toggleItem = (itemId: number) => {
    setSelected((prev) => {
      const next = new Set(prev ?? []);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  };

  const handleSubmit = async () => {
    if (!taskId || !draft) return;
    if (summary.count === 0) {
      toast.error('未选择任何项目', '请至少勾选一项再开始下载');
      return;
    }
    if (diskId <= 0) {
      toast.error('请选择目标磁盘', '你没有可写入的磁盘，无法开始下载');
      return;
    }
    if (!relPath.trim() && !isDefaultTarget) {
      toast.error('请填写目标目录', '除了使用默认目录，临时目标需要指定磁盘内的相对路径');
      return;
    }
    setSubmitting(true);
    try {
      await offlineDownloadService.select({
        taskId,
        itemIds: [...selectedIds],
        diskId,
        targetPath: relPath.trim(),
        packZip,
      });
      toast.success('已开始下载', '可在「任务中心」查看进度，完成后到文件管理中查看产物');
      setUrl('');
      onTaskIdChange(null);
      onTasksChanged();
    } catch (e) {
      // 失败保留勾选与目录，便于修正后重试
      toast.error('开始下载失败', messageOf(e));
    } finally {
      setSubmitting(false);
    }
  };

  /** 放弃当前任务回到「粘贴网址」（保留已输入的网址，便于重新分析） */
  const handleReset = () => {
    onTaskIdChange(null);
  };

  /* ==================== ① 粘贴网址 ==================== */

  if (!taskId) {
    return (
      <Card className="mx-auto max-w-3xl">
        <div className="flex items-center gap-2">
          <Icon name="link" className="text-brand-500" />
          <span className="text-[14px] font-semibold">粘贴网址开始分析</span>
        </div>
        <p className="mt-1 text-[12.5px] leading-relaxed text-muted">
          只需<b>无需登录</b>的公开页面地址（视频页 / 音频页 / 图集页）。服务端会用三级分析
          （站点解析 → 静态抓取 → 深度分析）列出可下载项；分析只取清单，不会立刻下载媒体。
        </p>

        {!analyzeAllowed && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-warning-500/30 bg-warning-50 p-3 text-[12.5px] text-warning-700">
            <Icon name="warning" size="sm" className="mt-px shrink-0" />
            <span>
              {meta.enabled
                ? '尚未配置离线下载目录（或原目录已失效），请先在右上「设置」中配置自己的默认目录。'
                : '管理员关闭了离线下载功能。'}
            </span>
          </div>
        )}

        {meta.enabled && !meta.deep_analyze_available && (
          <p className="mt-3 text-[11.5px] text-subtle">
            当前部署未启用深度分析：只能靠前两级分析，遇到「地址由 JS 动态注入」的站点可能抓不到内容。
          </p>
        )}

        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input
            className="input flex-1"
            placeholder="https://example.com/video/123"
            value={url}
            disabled={!analyzeAllowed || analyzing}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && analyzeAllowed && !analyzing) void handleAnalyze();
            }}
          />
          <Button
            variant="primary"
            icon="search"
            loading={analyzing}
            disabled={!analyzeAllowed}
            onClick={handleAnalyze}
          >
            分析网址
          </Button>
          {!analyzeAllowed && meta.enabled && (
            <Button icon="settings" onClick={onOpenSettings}>
              设置目录
            </Button>
          )}
        </div>

        <p className="mt-3 text-[11.5px] text-subtle">
          分析结果在服务端保留 10 分钟；媒体地址带签名会过期，过期后重新分析即可。
        </p>
      </Card>
    );
  }

  /* ==================== ② 分析中 / 失败 ==================== */

  if (!draft) {
    return (
      <Card className="mx-auto max-w-3xl">
        {detailError ? (
          <div className="flex flex-col gap-3">
            <div className="flex items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3 text-[13px] text-danger-700">
              <Icon name="error" size="sm" className="mt-px shrink-0" />
              <span className="break-words">{detailError}</span>
            </div>
            <div className="flex gap-2">
              <Button icon="refresh" onClick={() => setDetailEpoch((v) => v + 1)}>
                重试
              </Button>
              <Button variant="ghost" icon="close" onClick={handleReset}>
                重新粘贴网址
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 text-[13px] font-medium">
              <Icon name="loading" className="animate-spin text-brand-500" />
              正在分析页面…
            </div>
            <ListSkeleton rows={3} />
          </>
        )}
      </Card>
    );
  }

  const status = draft.task.status;

  if (status === 'queued' || status === 'analyzing') {
    return (
      <Card className="mx-auto max-w-3xl">
        <div className="flex items-center gap-2 text-[13px] font-medium">
          <Icon name="loading" className="animate-spin text-brand-500" />
          {status === 'queued' ? '排队中，等待服务端调度…' : '正在分析页面…'}
        </div>
        <p className="mt-2 text-[12.5px] text-muted">
          {slowAnalyze
            ? '正在深度分析，最多 30 秒（需要启动无头浏览器，比前两级慢）。'
            : '通常几秒内完成；分析结果会列出可下载的视频 / 音频 / 图片清单。'}
        </p>
        <div className="mt-3">
          <ListSkeleton rows={3} />
        </div>
        <Button className="mt-3" variant="ghost" icon="close" onClick={handleReset}>
          取消并重新粘贴
        </Button>
      </Card>
    );
  }

  if (status === 'failed' || status === 'canceled') {
    return (
      <Card className="mx-auto max-w-3xl">
        <div className="flex items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3 text-[13px] text-danger-700">
          <Icon name="error" size="sm" className="mt-px shrink-0" />
          <div className="break-words">
            <p className="m-0 font-medium">
              {draft.task.error_message || (status === 'canceled' ? '任务已取消' : '分析失败')}
            </p>
            {draft.task.error_code && (
              <p className="m-0 mt-1 text-[11.5px] opacity-80">错误码：{draft.task.error_code}</p>
            )}
          </div>
        </div>
        <p className="mt-3 text-[11.5px] text-subtle">
          任务 #{draft.task.id} · {draft.task.source_domain || '未知来源'} · 创建于{' '}
          {formatIsoTime(draft.task.created_at)}
        </p>
        <div className="mt-3 flex gap-2">
          <Button icon="refresh" onClick={handleReset}>
            重新分析
          </Button>
          <Button variant="ghost" icon="transfer" onClick={onGotoTasks}>
            回到任务中心
          </Button>
        </div>
      </Card>
    );
  }

  if (status !== 'ready') {
    // downloading / merging / success：已提交，交给任务中心看进度
    return (
      <Card className="mx-auto max-w-3xl">
        <div className="flex items-center gap-2">
          <Icon name="success" className="text-success-500" />
          <span className="text-[13.5px] font-semibold">任务已提交：{draft.task.status_text}</span>
        </div>
        <p className="mt-2 text-[12.5px] text-muted">
          进度、速度与产物在「任务中心」查看；下载在服务端后台进行，关闭页面不会中断。
        </p>
        <div className="mt-3 flex gap-2">
          <Button variant="primary" icon="transfer" onClick={onGotoTasks}>
            去任务中心
          </Button>
          <Button variant="ghost" icon="plus" onClick={handleReset}>
            再粘贴一个网址
          </Button>
        </div>
      </Card>
    );
  }

  /* ==================== ③ 清单勾选 + 目录 + 开始下载 ==================== */

  const hasAudio = hasItemKind(items, 'audio');
  const hasImage = hasItemKind(items, 'image');
  const atItemLimit = items.length >= meta.max_items;

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-3">
      <Card pad="none">
        <div className="border-b border-line-subtle px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Icon name="link" size="sm" className="text-brand-500" />
            <span className="min-w-0 flex-1 truncate text-[13.5px] font-semibold">
              {draft.task.page_title || '已分析页面'}
            </span>
            <Badge tone="success">分析完成</Badge>
          </div>
          <p className="mt-0.5 text-[11.5px] text-subtle">
            {draft.task.source_domain || '未知来源'} · 共 {items.length} 项
            {atItemLimit && '（已达单次清单上限，仅显示前若干项）'} · 任务 #{draft.task.id}
          </p>
        </div>

        {/* 快捷选择（F24 / AC-210） */}
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line-subtle bg-sunken px-4 py-2.5">
          <Button size="sm" icon="checkAll" onClick={() => applyQuickPick('best')}>
            只要最高清
          </Button>
          <Button
            size="sm"
            icon="audio"
            disabled={!hasAudio}
            onClick={() => applyQuickPick('audio')}
          >
            只要音频
          </Button>
          <Button
            size="sm"
            icon="image"
            disabled={!hasImage}
            onClick={() => applyQuickPick('image')}
          >
            只要图片
          </Button>
          <span className="mx-1 h-4 w-px bg-line-subtle" aria-hidden="true" />
          <Button size="sm" icon="checked" onClick={() => setSelected(new Set(items.map((i) => i.id)))}>
            全选
          </Button>
          <Button size="sm" icon="minus" onClick={() => setSelected(new Set())}>
            全不选
          </Button>
          <div className="grow" />
          {draft.has_more_images && (
            <Button
              size="sm"
              variant="primary"
              icon="next"
              loading={nextPageBusy}
              onClick={handleNextPage}
            >
              继续抓下一页
            </Button>
          )}
        </div>

        {/* 清单 */}
        <div className="max-h-[46vh] overflow-y-auto">
          {items.map((item) => {
            const visual = KIND_ICON[item.kind] ?? KIND_ICON.doc;
            const checked = selectedIds.has(item.id);
            return (
              <label
                key={item.id}
                className="flex cursor-pointer items-center gap-3 border-b border-line-subtle px-4 py-2.5 transition-colors last:border-b-0 hover:bg-hover"
              >
                <input
                  type="checkbox"
                  className="h-4 w-4 shrink-0 accent-brand-500"
                  checked={checked}
                  onChange={() => toggleItem(item.id)}
                />
                <span className={visual.box}>
                  <Icon name={visual.icon} size="sm" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px]">{item.display_name}</span>
                  <span className="block text-[11.5px] text-subtle">
                    {[itemMetaText(item), itemSizeText(item)].filter(Boolean).join(' · ')}
                  </span>
                </span>
                {item.status === 'failed' && item.error_message && (
                  <span className="max-w-[40%] truncate text-[11.5px] text-danger-700">
                    {item.error_message}
                  </span>
                )}
              </label>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-line-subtle px-4 py-2.5 text-[12px]">
          <span className="font-medium">
            已选 {summary.count} 项
            {summary.bytes > 0 && ` · ${summary.estimated ? '约 ' : ''}${formatSize(summary.bytes)}`}
          </span>
          {summary.count === 0 && <span className="text-danger-700">请至少勾选一项</span>}
          <div className="grow" />
          <label className="flex cursor-pointer items-center gap-2 text-muted">
            <input
              type="checkbox"
              className="h-4 w-4 accent-brand-500"
              checked={packZip}
              onChange={(e) => setPackZip(e.target.checked)}
            />
            图片打包为 zip
          </label>
        </div>
      </Card>

      {/* 存放位置：默认值来自 /offline/meta，可临时改（不回写默认目录） */}
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <Icon name="folder" className="text-warning-500" />
          <span className="text-[13.5px] font-semibold">存放位置</span>
          <Badge tone={isDefaultTarget ? 'brand' : 'neutral'}>
            {isDefaultTarget ? '账号默认目录' : '本次临时修改'}
          </Badge>
        </div>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <select
            className="input bg-surface sm:w-[220px]"
            value={diskId}
            disabled={submitting}
            onChange={(e) => setDiskId(Number(e.target.value))}
          >
            {meta.writable_disks.map((disk) => (
              <option key={disk.disk_id} value={disk.disk_id}>
                {disk.name}
              </option>
            ))}
          </select>
          <div className="flex flex-1 gap-2">
            <input
              className="input flex-1"
              placeholder="磁盘内相对路径（留空 = 使用账号默认目录）"
              value={relPath}
              disabled={submitting}
              onChange={(e) => setRelPath(e.target.value)}
            />
            <Button
              icon="folder"
              disabled={submitting || diskId <= 0}
              onClick={() => setBrowseOpen(true)}
            >
              浏览
            </Button>
          </div>
        </div>
        <p className="mt-2 text-[11.5px] text-subtle">
          默认目录来自「我的默认目录」（服务端按账号保存）。这里的临时修改<b>不会</b>写回默认值；
          <button type="button" className="text-brand-600 underline" onClick={onOpenSettings}>
            去修改默认目录
          </button>
        </p>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="lg"
          icon="download"
          loading={submitting}
          disabled={summary.count === 0 || diskId <= 0}
          onClick={handleSubmit}
        >
          开始下载
        </Button>
        <Button variant="ghost" icon="close" disabled={submitting} onClick={handleReset}>
          放弃本次任务
        </Button>
        <span className="text-[11.5px] text-subtle">
          下载在服务端后台进行，关闭页面不会中断
        </span>
      </div>

      {browseOpen && diskId > 0 && (
        <DirTreeModal
          diskId={diskId}
          diskName={diskName}
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

/**
 * 离线下载纯函数工具（v1.8.0）
 *
 * 从页面里抽出来的**无副作用**逻辑：快捷选择、终态判定、进度换算、中文文案映射。
 * 抽出目的有两个：
 *   1. 让「只要最高清 / 只要音频 / 只要图片」这类容易做错的规则可被单测锁死（F24 / AC-210）；
 *   2. 页面只负责渲染与事件绑定（`web-frontend/SKILL.md` 架构分层）。
 */

import type { BadgeTone } from '../components/ui';
import { formatSize } from './format';
import type { OfflineItem, OfflineMeta, OfflineTask } from '../types/models';

/** 轮询默认间隔（毫秒）：设计文档 §3.2 / 需求 F27.3 口径 2.5s */
export const DEFAULT_POLL_INTERVAL_MS = 2500;

/** 快捷选择的三种模式（需求 F24） */
export type QuickPickMode = 'best' | 'audio' | 'image';

/** 这些失败类型**不可重试**（与后端 `retry_task` 的拒绝清单保持一致） */
export const NON_RETRYABLE_ERROR_CODES: ReadonlySet<string> = new Set([
  'drm_protected',
  'login_required',
  'too_large',
  'disk_full',
  'no_writable_disk',
  'target_not_configured',
  'config_invalid',
]);

/**
 * 规范化轮询间隔
 *
 * 后端 `/offline/meta` 给 `poll_interval_ms`（当前 2500）。异常值（缺字段 / 0 / 负数 /
 * 非数字）一律回落到 2500；同时夹到 [100ms, 60s]，避免"每秒一次"或"永不再轮询"。
 */
export function resolvePollInterval(raw: unknown): number {
  const value = typeof raw === 'number' ? raw : Number(raw);
  if (!Number.isFinite(value) || value <= 0) return DEFAULT_POLL_INTERVAL_MS;
  return Math.min(Math.max(Math.round(value), 100), 60_000);
}

/** 任务是否处于**进行中**（需要轮询刷新进度） */
export function isTaskActive(status: string): boolean {
  return (
    status === 'queued' ||
    status === 'analyzing' ||
    status === 'downloading' ||
    status === 'merging'
  );
}

/** 任务是否终态（成功 / 失败 / 已取消） */
export function isTerminalStatus(status: string): boolean {
  return status === 'success' || status === 'failed' || status === 'canceled';
}

/**
 * 任务列表是否已全部终态（轮询停止条件之一，AC-232）
 *
 * 空列表视为"无进行中任务"（同样停轮询，避免空列表也每 2.5s 打一次接口）；
 * 用户可点「刷新」或新建任务重新拉起轮询。
 */
export function allTasksTerminal(tasks: OfflineTask[]): boolean {
  return tasks.every((task) => isTerminalStatus(task.status));
}

/**
 * 任务进度百分比（0~100）。
 *
 * 产品口径（v1.8.1 / D15）：`status=success` 时无条件 100%，不按字节估算值回退
 * （成功任务的 bytes_total 可能是估算值，会出现「已完成却 99%」的矛盾显示）。
 * 其余状态：字节优先，字节未知（图片等）时按项数。
 */
export function taskProgress(task: OfflineTask): number {
  const clamp = (v: number) => Math.max(0, Math.min(100, v));
  if (task.status === 'success') return 100;
  if (task.bytes_total > 0) return clamp((task.bytes_done / task.bytes_total) * 100);
  if (task.item_total > 0) return clamp((task.item_done / task.item_total) * 100);
  return 0;
}

/** 速度文案（`1.2 MB/s`）；未知返回空串 */
export function formatSpeed(bytesPerSecond: number): string {
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return '';
  return `${formatSize(bytesPerSecond)}/s`;
}

/** 时长（毫秒 → `3:34` / `1:02:03`）；未知返回空串 */
export function formatDurationMs(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '';
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** ISO8601（服务端当前时间格式）→ 本地 `YYYY-MM-DD HH:mm`；空/非法返回 `—` */
export function formatIsoTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** 任务状态徽章配色 */
export function taskStatusTone(status: string): BadgeTone {
  if (status === 'success') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'canceled') return 'neutral';
  if (status === 'ready') return 'warning';
  return 'brand';
}

/** 清单项是否有音频 / 图片（快捷按钮可用性判定） */
export function hasItemKind(items: OfflineItem[], kind: string): boolean {
  return items.some((item) => item.kind === kind);
}

/** 分辨率得分（宽 × 高）；缺分辨率时为 0，排序时排最后 */
function resolutionScore(item: OfflineItem): number {
  if (item.width <= 0 || item.height <= 0) return 0;
  return item.width * item.height;
}

/**
 * 「最高清」项：优先分辨率最大的**视频**；没有视频时取预估体积最大的音频；
 * 都没有则取第一项。返回 `null` 表示清单为空。
 *
 * 说明：服务端已把音视频分离的流合并成一条（F24 / I-7），因此这里不需要再按编解码器合并。
 */
export function pickBestItem(items: OfflineItem[]): OfflineItem | null {
  if (items.length === 0) return null;
  const videos = items.filter((item) => item.kind === 'video');
  const pool =
    videos.length > 0 ? videos : items.filter((item) => item.kind === 'audio');
  if (pool.length > 0) {
    return pool.reduce((best, current) =>
      resolutionScore(current) > resolutionScore(best) ||
      (resolutionScore(current) === resolutionScore(best) && current.est_size > best.est_size)
        ? current
        : best,
    );
  }
  return items[0];
}

/** 清单默认勾选：只选「最高清」一项（避免图片页一进来就勾上几百张） */
export function pickNearestSelection(items: OfflineItem[]): number[] {
  const best = pickBestItem(items);
  return best ? [best.id] : [];
}

/**
 * 快捷选择结果（需求 F24 / AC-210）
 *
 * - `best`：只要最高清（恰好 1 项）
 * - `audio`：只要音频（可能 0 项，由调用方禁用按钮）
 * - `image`：只要图片
 */
export function pickByQuickSelect(items: OfflineItem[], mode: QuickPickMode): number[] {
  if (mode === 'audio') {
    return items.filter((item) => item.kind === 'audio').map((item) => item.id);
  }
  if (mode === 'image') {
    return items.filter((item) => item.kind === 'image').map((item) => item.id);
  }
  return pickNearestSelection(items);
}

/** 勾选汇总（已选 N 项 · 约 X） */
export function summarizeSelection(
  items: OfflineItem[],
  selectedIds: ReadonlySet<number>,
): { count: number; bytes: number; estimated: boolean } {
  let count = 0;
  let bytes = 0;
  let estimated = false;
  for (const item of items) {
    if (!selectedIds.has(item.id)) continue;
    count += 1;
    bytes += Math.max(0, item.est_size);
    if (item.is_estimated) estimated = true;
  }
  return { count, bytes, estimated };
}

/** 清单项副标题：`1920×1080 · MP4 · 3:34`（缺项自动省略） */
export function itemMetaText(item: OfflineItem): string {
  const parts: string[] = [];
  if (item.width > 0 && item.height > 0) parts.push(`${item.width}×${item.height}`);
  if (item.ext) parts.push(item.ext.toUpperCase());
  const duration = formatDurationMs(item.duration_ms);
  if (duration) parts.push(duration);
  return parts.join(' · ');
}

/** 清单项大小文案：无 Content-Length 时标「约」（F23.2 要求） */
export function itemSizeText(item: OfflineItem): string {
  if (!Number.isFinite(item.est_size) || item.est_size <= 0) return '';
  return item.is_estimated ? `约 ${formatSize(item.est_size)}` : formatSize(item.est_size);
}

/** 进度行文案（任务卡片的第二行） */
export function taskProgressText(task: OfflineTask): string {
  const percent = Math.round(taskProgress(task));
  switch (task.status) {
    case 'queued':
      return '排队中，等待服务端调度';
    case 'analyzing':
      return '正在分析页面，最多 30 秒';
    case 'ready':
      return `已分析出 ${task.item_total} 项，等待勾选`;
    case 'downloading':
    case 'merging': {
      const parts = [`${percent}%`];
      if (task.bytes_total > 0) {
        parts.push(`${formatSize(task.bytes_done)} / ${formatSize(task.bytes_total)}`);
      } else if (task.item_total > 0) {
        parts.push(`${task.item_done} / ${task.item_total} 项`);
      }
      const speed = formatSpeed(task.speed_bps);
      if (speed) parts.push(speed);
      return parts.join(' · ');
    }
    case 'success':
      // v1.8.1 / AC-256：成功任务与桌面端同口径，明确显示 100%
      return `已完成 ${task.item_done || task.item_total} 项${
        task.bytes_total > 0 ? ` · ${formatSize(task.bytes_total)}` : ''
      } · 100%`;
    case 'failed':
      return '下载失败';
    case 'canceled':
      return '任务已取消';
    default:
      return task.status_text || task.status;
  }
}

/** 不可用原因的四态中文文案（`/offline/meta` 的 `unusable_reason`） */
export interface UnusableNotice {
  title: string;
  description: string;
  /** 是否引导用户去配置默认目录 */
  needsConfigure: boolean;
}

export function unusableNotice(reason: string): UnusableNotice | null {
  switch (reason) {
    case 'disabled':
      return {
        title: '离线下载功能已关闭',
        description: '管理员尚未开启该功能（总开关默认关闭），开启后本页会自动可用。',
        needsConfigure: false,
      };
    case 'no_writable_disk':
      return {
        title: '你没有可写入的磁盘，无法使用离线下载',
        description: '请联系管理员为你分配一个可写权限的虚拟磁盘；在此之前分析与下载都会被服务端拒绝。',
        needsConfigure: false,
      };
    case 'not_configured':
      return {
        title: '请先设置离线下载目录',
        description: '离线下载需要一个「虚拟磁盘 + 相对路径」作为默认存放位置，设置后即可粘贴网址。',
        needsConfigure: true,
      };
    case 'config_invalid':
      return {
        title: '离线下载目录已失效，请重新配置',
        description: '原磁盘被删除、写权限被收回或目录已不存在；重新选择目录后即可继续使用。',
        needsConfigure: true,
      };
    default:
      return null;
  }
}

/**
 * 是否可以发起分析
 *
 * 服务端 `POST /analyze` 会依次校验：总开关 → 有可写磁盘 → 已配置且仍有效 → SSRF → 队列。
 * 前端据此禁用按钮，避免"点了才被拒"的空分析（AC-214 / AC-214b）。
 */
export function canAnalyze(meta: OfflineMeta | null): boolean {
  if (!meta) return false;
  return Boolean(meta.enabled && meta.writable_disks.length > 0 && meta.configured);
}

/** 任务是否可重试（状态 + 失败类型双重判定；磁盘写权限由 `canOperateTask` 另行判定） */
export function canRetryTask(task: OfflineTask): boolean {
  if (task.status !== 'failed' && task.status !== 'canceled') return false;
  return !NON_RETRYABLE_ERROR_CODES.has(task.error_code);
}

/** 终态或待选择状态才能「删除记录」（进行中先取消，语义更清楚） */
export function canDeleteRecord(task: OfflineTask): boolean {
  return isTerminalStatus(task.status) || task.status === 'ready';
}

/** 任务是否可取消（分析/待选择/下载/合并） */
export function canCancelTask(task: OfflineTask): boolean {
  return isTaskActive(task.status) || task.status === 'ready';
}

/**
 * 界面层是否允许对该任务执行写操作（取消 / 重试 / 删除记录）
 *
 * 服务端的 `can_operate` = 「自己的任务」**且**「对**目标磁盘**仍有 write 权限」，
 * 但任务在 `queued/analyzing/ready` 阶段**还没有绑定目标磁盘**（`target_disk_id=0`，
 * `target_disk_name=''`），此时服务端必然算成 `can_operate=false` —— 这是"尚未选目录"，
 * 不是"没有权限"，不能因此把「取消 / 删除记录」也禁用掉。
 *
 * 因此判定拆成：
 *   - 未绑定目标磁盘（`target_disk_name === ''`）→ 只看归属，允许操作；
 *   - 已绑定目标磁盘 → 必须 `can_operate`（AC-229 / AC-242：权限被收回后不能重试）。
 */
export function canOperateTask(task: OfflineTask): boolean {
  if (task.target_disk_name === '') return true;
  return task.can_operate;
}

/** 是否需要在界面上标注「只读」（已有目标磁盘但写权限已被收回） */
export function isReadOnlyTask(task: OfflineTask): boolean {
  return !task.can_operate && task.target_disk_name !== '';
}

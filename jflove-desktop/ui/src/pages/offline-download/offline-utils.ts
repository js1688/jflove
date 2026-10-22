/**
 * 离线下载页面纯函数工具（无 React / 无网络）
 *
 * 把「状态判定 / 域名脱敏 / 快捷选择 / 文案」从组件里抽出来，原因有二：
 *   1. 这些判据是 AC-210（快捷选择）、F27.1（来源脱敏）、F29（中文失败原因）的落点，
 *      抽成纯函数后可以用单元测试直接锁定，不必把整个页面拖进 jsdom；
 *   2. 组件里保持"只编排 UI"，避免同一规则在任务中心 / 新建任务两处各写一份。
 *
 * 安全口径：本模块只接收服务端已脱敏的字段（`source_domain` 不含完整 URL），
 * 不做任何请求，也不读取 token / session_key。
 */

import { ApiError } from '../../types/models';
import { formatSize } from '../../utils/format';
import type { OfflineItem } from '../../services/offline-download-service';

/** 任务终态：这些状态不再有进度变化（与后端 `TERMINAL_STATUSES` 一致） */
export const OFFLINE_TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'success',
  'failed',
  'canceled',
]);

/** 服务端明确不可重试的失败类型（对应 `retry_task()` 的拒绝名单） */
export const OFFLINE_NON_RETRYABLE_ERRORS: ReadonlySet<string> = new Set([
  'drm_protected',
  'login_required',
  'too_large',
  'disk_full',
  'no_writable_disk',
  'target_not_configured',
  'config_invalid',
]);

/** 任务是否已到终态 */
export function isTerminalStatus(status: string): boolean {
  return OFFLINE_TERMINAL_STATUSES.has(status);
}

/**
 * 界面是否允许对任务执行写操作（取消 / 重试）。
 *
 * 服务端 `can_operate` =「自己的任务」且「对**目标磁盘**仍有 write 权限」。
 * 但 `queued/analyzing/ready` 阶段任务**尚未绑定目标磁盘**（`target_disk_id=0`），
 * 此时服务端必然返回 `can_operate=false` —— 那是"还没选目录"，不是"没有权限"；
 * 若直接看 `can_operate`，用户连"取消一个正在分析的任务"都做不到。
 *
 * 因此：未绑定目标磁盘 → 归属已由接口保证（他人任务 404），允许操作；
 * 已绑定 → 必须 `can_operate`（权限被收回后不能重试，AC-229 / AC-242）。
 */
export function canOperateTask(task: {
  target_disk_name: string;
  can_operate: boolean;
}): boolean {
  if (!task.target_disk_name) return true;
  return task.can_operate;
}

/** 任务是否可重试（终态里只有 failed/canceled，且排除服务端拒绝的失败类型） */
export function canRetryTask(status: string, errorCode: string): boolean {
  if (status !== 'failed' && status !== 'canceled') return false;
  return !OFFLINE_NON_RETRYABLE_ERRORS.has(errorCode || '');
}

/**
 * 来源域名脱敏（F27.1：不展示完整 URL）。
 *
 * 服务端只下发域名；这里再把主域名的中间字符隐去，例如：
 *   `youtube.com` → `y***.com`
 *   `video.example.com` → `v***.example.com`
 *
 * 判据：只保留第一段首字符 + 其余段，长度超过 64 时截断（防超长域名撑破行）。
 */
export function maskSourceDomain(domain: string): string {
  const value = (domain || '').trim();
  if (!value) return '—';
  const labels = value.split('.').filter(Boolean);
  if (labels.length === 0) return '—';
  const head = labels[0];
  labels[0] = head.length <= 1 ? `${head}***` : `${head.slice(0, 1)}***`;
  const masked = labels.join('.');
  return masked.length > 64 ? `${masked.slice(0, 61)}…` : masked;
}

/** ISO8601 字符串 → 本地 `YYYY-MM-DD HH:mm`（无法解析时返回 —） */
export function formatIsoTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

/** 速度：`1.2 MB/s`（0 或非法值返回 —） */
export function formatSpeed(bytesPerSecond: number): string {
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return '—';
  return `${formatSize(bytesPerSecond)}/s`;
}

/**
 * 任务进度百分比（0~100）。
 *
 * 产品口径（v1.8.1 / D15）：`status=success` 时无条件 100%，不按字节估算值回退
 * （成功任务的 bytes_total 可能是估算值，会出现「已完成却 99%」的矛盾显示）。
 * 其余状态：字节优先，字节未知时按已选项目数。
 */
export function taskPercent(task: {
  status: string;
  bytes_done: number;
  bytes_total: number;
  item_done: number;
  item_total: number;
}): number {
  if (task.status === 'success') return 100;
  const total = Number(task.bytes_total) || 0;
  const done = Number(task.bytes_done) || 0;
  if (total > 0) return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
  const itemTotal = Number(task.item_total) || 0;
  const itemDone = Number(task.item_done) || 0;
  if (itemTotal > 0) {
    return Math.max(0, Math.min(100, Math.round((itemDone / itemTotal) * 100)));
  }
  return 0;
}

/** 时长毫秒 → `3:34` / `1:02:03`（0 表示无时长，返回空串） */
export function formatDurationMs(durationMs: number): string {
  if (!Number.isFinite(durationMs) || durationMs <= 0) return '';
  const totalSeconds = Math.round(durationMs / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

/** 清单项预估大小：`≈ 139 MB`（`is_estimated=false` 时不带约号） */
export function itemSizeLabel(item: Pick<OfflineItem, 'est_size' | 'is_estimated'>): string {
  const size = Number(item.est_size) || 0;
  if (size <= 0) return '大小未知';
  return item.is_estimated ? `≈ ${formatSize(size)}` : formatSize(size);
}

/** 勾选汇总：项数 + 预估总字节；任一项为估算值时 `estimated=true`（界面加「约」） */
export function selectedSummary(
  items: OfflineItem[],
  selectedIds: ReadonlySet<number>,
): { count: number; bytes: number; estimated: boolean } {
  let count = 0;
  let bytes = 0;
  let estimated = false;
  for (const item of items) {
    if (!selectedIds.has(item.id)) continue;
    count += 1;
    bytes += Number(item.est_size) || 0;
    if (item.is_estimated) estimated = true;
  }
  return { count, bytes, estimated };
}

/** 勾选汇总文案：`已选 3 项 · 约 1.2 GB` */
export function selectedSummaryLabel(items: OfflineItem[], selectedIds: ReadonlySet<number>): string {
  const { count, bytes, estimated } = selectedSummary(items, selectedIds);
  const approx = estimated && bytes > 0 ? '约 ' : '';
  return `已选 ${count} 项 · ${approx}${bytes > 0 ? formatSize(bytes) : '0 B'}`;
}

/** 全选：返回全部项 ID */
export function pickAllIds(items: OfflineItem[]): number[] {
  return items.map((item) => item.id);
}

/** 按类型选择（「只要音频」/「只要图片」）；无匹配项返回空数组 */
export function pickByKind(items: OfflineItem[], kind: 'video' | 'audio' | 'image'): number[] {
  return items.filter((item) => item.kind === kind).map((item) => item.id);
}

/**
 * 「只要最高清」：返回**恰好一项**的 ID（AC-210 判据之一）。
 *
 * 优先级：分辨率（宽×高）→ 预估大小 → ID 小的在前（保证结果稳定）。
 * 没有 video 项时（纯音频/图片页）回退到全部项，按预估大小挑最大的一项，
 * 避免按钮点了没反应。
 */
export function pickHighestQuality(items: OfflineItem[]): number[] {
  if (items.length === 0) return [];
  const videos = items.filter((item) => item.kind === 'video');
  const pool = videos.length > 0 ? videos : items;
  const best = [...pool].sort((a, b) => {
    const areaA = (Number(a.width) || 0) * (Number(a.height) || 0);
    const areaB = (Number(b.width) || 0) * (Number(b.height) || 0);
    if (areaA !== areaB) return areaB - areaA;
    const sizeA = Number(a.est_size) || 0;
    const sizeB = Number(b.est_size) || 0;
    if (sizeA !== sizeB) return sizeB - sizeA;
    return a.id - b.id;
  })[0];
  return best ? [best.id] : [];
}

/** 失败类型的下一步建议（F29「可展开看建议动作」） */
const ERROR_SUGGESTIONS: Record<string, string> = {
  unsupported_site: '换一个地址，或把地址反馈给管理员。',
  no_media: '确认页面确实含媒体；若页面靠 JS 注入，请联系管理员开启深度分析。',
  deep_analyze_disabled: '请联系管理员开启深度分析后重试。',
  login_required: '本功能不支持登录态站点，请换一个公开地址。',
  drm_protected: '受版权保护的内容无法下载。',
  url_expired: '请重新分析该地址后再试。',
  referer_denied: '可稍后重试；若持续失败，请换一个地址。',
  net_timeout: '可重试；若持续超时，请检查服务端网络。',
  ssrf_blocked: '请使用公网 http(s) 地址。',
  disk_full: '清理目标磁盘空间后重试。',
  too_large: '换更低的清晰度，或联系管理员调整体积上限。',
  target_not_writable: '换一个目标目录后重试。',
  target_not_configured: '先在「离线下载 → 设置」中配置默认目录。',
  config_invalid: '默认目录已失效，请重新配置。',
  no_writable_disk: '请联系管理员为你的账号分配可写磁盘。',
  parser_outdated: '请等待服务端升级解析组件。',
  restart_interrupted: '可直接重试，已完成的分片不会重下。',
  canceled: '可点击重试继续下载。',
  page_limit: '已达抓取页数上限，不能继续追加。',
  invalid_path: '请选择磁盘内已存在的目录（可在「设置」中新建后保存）。',
  merge_failed: '可重试；若持续失败，请联系管理员。',
  http_error: '源站返回异常状态，稍后重试。',
  network_error: '检查网络后重试。',
};

/** 取失败类型的建议动作；未知类型返回空串（界面不显示「建议」行） */
export function errorSuggestion(errorCode: string): string {
  return ERROR_SUGGESTIONS[(errorCode || '').trim()] || '';
}

/**
 * 异常 → 用户可见中文文案。
 *
 * `ApiError.detail` 是服务端的中文 `detail`（全局异常处理器加密回传），
 * 比 `ApiError.message` 的 `[400] ...` 前缀更适合直接展示。
 */
export function offlineErrorText(error: unknown, fallback = '操作失败'): string {
  if (error instanceof ApiError) return error.detail || fallback;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/**
 * 通用格式化工具
 *
 * v1.4.2 里 `formatSize` / `formatTime` / `formatDate` 在多个页面各写一份
 * （`DiskBrowserPage`、`NoteListPage`、`TransferPage` 等），且格式略有差异。
 * 现统一到本模块（需求 AC-7：三端信息呈现一致）。
 */

/** 文件大小：B / KB / MB / GB / TB */
export function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

/** Unix 秒 → `YYYY-MM-DD HH:mm` */
export function formatTime(ts: number): string {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / 昨天 HH:mm / YYYY-MM-DD */
export function formatRelative(ts: number): string {
  if (!ts) return '—';
  const now = Date.now();
  const t = ts * 1000;
  const diff = now - t;
  if (diff < 60_000) return '刚刚';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 172_800_000) {
    const d = new Date(t);
    const p = (n: number) => String(n).padStart(2, '0');
    return `昨天 ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  return formatTime(ts);
}

/** 耗时秒 → `1m 20s` / `45s`（紧凑形式，用于统计展示） */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/** 已持续时长（中文，用于「已持续 N 分钟」这类文案） */
export function formatElapsed(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const s = Math.round(seconds);
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分钟`;
  const h = Math.floor(m / 60);
  return `${h} 小时 ${m % 60} 分钟`;
}

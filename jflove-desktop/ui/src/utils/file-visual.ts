/**
 * 文件类型 → 图标名 + 色调
 *
 * v1.4.2 各页面各写一套 emoji 映射（`📁`/`🖼️`/`🎬`/`🎵`/`📄`/`📎`），
 * 且 `DiskBrowserPage`、`FileListPage`、`TransferPage` 三处各有一份。
 * 现统一到这里，并给出配套的 `.file-ico-*` 色调类（设计文档 §5「文件行」）。
 */
import type { IconName } from '../components/ui';

export type FileTone = 'folder' | 'doc' | 'img' | 'video' | 'zip';

export interface FileVisual {
  icon: IconName;
  tone: FileTone;
}

const IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'ico', 'heic', 'avif'];
const VIDEO_EXTS = [
  'mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', 'm4v', 'mpg', 'mpeg', 'ts', '3gp',
];
const AUDIO_EXTS = ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'wma', 'opus'];
const DOC_EXTS = ['md', 'markdown', 'mdown', 'mkd', 'txt', 'pdf', 'doc', 'docx', 'rtf'];
const CODE_EXTS = ['js', 'ts', 'tsx', 'jsx', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'h', 'json', 'xml', 'yaml', 'yml', 'html', 'css', 'sh'];
const ARCHIVE_EXTS = ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'];

/** 取扩展名（小写，无点） */
export function extOf(filename: string): string {
  const idx = filename.lastIndexOf('.');
  return idx > 0 ? filename.slice(idx + 1).toLowerCase() : '';
}

/**
 * 取文件的视觉标识。
 *
 * @param filename 文件名
 * @param isDir    是否目录
 */
export function fileVisual(filename: string, isDir = false): FileVisual {
  if (isDir) return { icon: 'folder', tone: 'folder' };

  const ext = extOf(filename);
  if (IMAGE_EXTS.includes(ext)) return { icon: 'image', tone: 'img' };
  if (VIDEO_EXTS.includes(ext)) return { icon: 'video', tone: 'video' };
  if (AUDIO_EXTS.includes(ext)) return { icon: 'audio', tone: 'video' };
  if (DOC_EXTS.includes(ext)) return { icon: 'doc', tone: 'doc' };
  if (CODE_EXTS.includes(ext)) return { icon: 'code', tone: 'doc' };
  if (ARCHIVE_EXTS.includes(ext)) return { icon: 'archive', tone: 'zip' };
  return { icon: 'file', tone: 'zip' };
}

/** 文件大小格式化（B/KB/MB/GB，保留 1 位小数） */
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

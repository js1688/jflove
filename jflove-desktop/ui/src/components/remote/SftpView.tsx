/**
 * SFTP 文件视图（v1.10.2）
 *
 * - 远端操作全部由 Python 侧完成（渲染层无文件系统/网络出口）
 * - 传输队列显示「已传 / 总量」、速率、百分比与中文状态
 * - 支持复选框多选、批量下载到指定目录
 * - 路径跳转 / 新建目录 / 重命名使用应用内弹层，不再用 window.prompt
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, EmptyState, Icon } from '../../components/ui';
import { callBridge, onBridgeEvent } from '../../utils/desktop-bridge';
import { remoteService, type SftpEntry } from '../../services/remote-service';
import { PromptModal } from './PromptModal';

interface TransferItem {
  id: string;
  name: string;
  done: number;
  total: number;
  direction: 'upload' | 'download';
  status: 'running' | 'done' | 'failed';
  message?: string;
  startedAt: number;
  lastAt: number;
  lastBytes: number;
  speed: number;
}

interface SftpViewProps {
  sessionId: string;
}

function joinPath(base: string, name: string): string {
  if (base === '.' || base === '') return name;
  if (base === '/') return `/${name}`;
  return `${base.replace(/\/+$/, '')}/${name}`;
}

function parentPath(path: string): string {
  if (path === '.' || path === '/' || !path.includes('/')) return '.';
  const parts = path.split('/').filter(Boolean);
  parts.pop();
  return parts.length ? parts.join('/') : '.';
}

function asRecord(payload: unknown): Record<string, unknown> {
  return payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {};
}

/** 把字节数格式化成人类可读 */
function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

function formatSpeed(bytesPerSecond: number): string {
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return '—';
  return `${formatBytes(bytesPerSecond)}/s`;
}

export function SftpView({ sessionId }: SftpViewProps) {
  const [path, setPath] = useState('.');
  const [entries, setEntries] = useState<SftpEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [transfers, setTransfers] = useState<TransferItem[]>([]);
  const [prompt, setPrompt] = useState<{
    title: string;
    initial: string;
    placeholder?: string;
    onConfirm: (value: string) => void;
  } | null>(null);
  const pathRef = useRef('.');
  const reloadTimer = useRef<number | null>(null);

  const load = useCallback(
    async (target: string) => {
      setLoading(true);
      setError('');
      try {
        const result = await remoteService.sftpList(sessionId, target);
        setEntries(result.entries);
        setPath(target);
        pathRef.current = target;
        setSelected(new Set());
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [sessionId],
  );

  useEffect(() => {
    void load('.');
  }, [load]);

  /** 传输结束后合并刷新（批量下载时避免每个文件刷一次） */
  const scheduleReload = useCallback(() => {
    if (reloadTimer.current !== null) window.clearTimeout(reloadTimer.current);
    reloadTimer.current = window.setTimeout(() => {
      void load(pathRef.current);
    }, 400);
  }, [load]);

  useEffect(() => {
    const offs = [
      onBridgeEvent('remote.sftp_progress', (payload) => {
        const data = asRecord(payload);
        const id = String(data.transfer_id || '');
        if (!id) return;
        const done = Number(data.bytes_done || 0);
        const total = Number(data.bytes_total || 0);
        const now = performance.now();
        setTransfers((prev) =>
          prev.map((item) => {
            if (item.id !== id) return item;
            const elapsed = Math.max(0.001, (now - item.lastAt) / 1000);
            const delta = Math.max(0, done - item.lastBytes);
            return {
              ...item,
              done,
              total: total || item.total,
              lastAt: now,
              lastBytes: done,
              speed: delta / elapsed,
            };
          }),
        );
      }),
      onBridgeEvent('remote.sftp_done', (payload) => {
        const id = String(asRecord(payload).transfer_id || '');
        setTransfers((prev) =>
          prev.map((item) => (item.id === id ? { ...item, status: 'done', speed: 0 } : item)),
        );
        scheduleReload();
      }),
      onBridgeEvent('remote.sftp_failed', (payload) => {
        const data = asRecord(payload);
        const id = String(data.transfer_id || '');
        setTransfers((prev) =>
          prev.map((item) =>
            item.id === id
              ? { ...item, status: 'failed', message: String(data.message || '传输失败'), speed: 0 }
              : item,
          ),
        );
        scheduleReload();
      }),
    ];
    return () => {
      offs.forEach((off) => off());
      if (reloadTimer.current !== null) window.clearTimeout(reloadTimer.current);
    };
  }, [scheduleReload]);

  const trackTransfer = (
    transferId: string,
    name: string,
    total: number,
    direction: 'upload' | 'download',
  ) => {
    const now = performance.now();
    setTransfers((prev) => [
      ...prev,
      {
        id: transferId,
        name,
        done: 0,
        total,
        direction,
        status: 'running',
        startedAt: now,
        lastAt: now,
        lastBytes: 0,
        speed: 0,
      },
    ]);
  };

  const handleUpload = async () => {
    const picked = await callBridge<{ files: Array<{ path: string; name: string; size: number }> }>(
      'dialogs',
      'open_files',
    );
    for (const file of picked.files || []) {
      const transfer = await remoteService.sftpUpload(sessionId, file.path, joinPath(path, file.name));
      trackTransfer(transfer.transfer_id, file.name, file.size || 0, 'upload');
    }
  };

  const downloadOne = async (entry: SftpEntry, targetDir?: string) => {
    let localPath = '';
    if (targetDir) {
      localPath = `${targetDir.replace(/[\\/]+$/, '')}/${entry.name}`;
    } else {
      const target = await callBridge<{ path: string }>('dialogs', 'save_file', {
        default_name: entry.name,
      });
      localPath = target.path;
    }
    if (!localPath) return;
    const transfer = await remoteService.sftpDownload(
      sessionId,
      joinPath(path, entry.name),
      localPath,
    );
    trackTransfer(transfer.transfer_id, entry.name, entry.size, 'download');
  };

  const handleDownloadSelected = async () => {
    const files = entries.filter((entry) => selected.has(entry.name) && !entry.is_dir);
    if (files.length === 0) return;
    const dir = await callBridge<{ path: string }>('dialogs', 'select_directory', {
      title: '选择下载目录',
    });
    if (!dir.path) return;
    for (const file of files) {
      await downloadOne(file, dir.path);
    }
  };

  const askPrompt = (
    title: string,
    initial: string,
    onConfirm: (value: string) => void,
    placeholder?: string,
  ) => setPrompt({ title, initial, onConfirm, placeholder });

  const handleMkdir = () => {
    askPrompt('新建目录', '', (name) => {
      if (!name) return;
      void remoteService.sftpMkdir(sessionId, joinPath(path, name)).then(() => load(path));
    });
  };

  const handleRename = (entry: SftpEntry) => {
    askPrompt('重命名为', entry.name, (name) => {
      if (!name || name === entry.name) return;
      void remoteService
        .sftpRename(sessionId, joinPath(path, entry.name), joinPath(path, name))
        .then(() => load(path));
    });
  };

  const handleDelete = async (entry: SftpEntry) => {
    if (!window.confirm(`确定删除「${entry.name}」？`)) return;
    await remoteService.sftpDelete(sessionId, joinPath(path, entry.name), entry.is_dir);
    await load(path);
  };

  const allFiles = entries.filter((entry) => !entry.is_dir);
  const allSelected = allFiles.length > 0 && allFiles.every((entry) => selected.has(entry.name));
  const selectedCount = allFiles.filter((entry) => selected.has(entry.name)).length;

  const toggleAll = () => {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(allFiles.map((entry) => entry.name)));
  };

  const toggleOne = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      <div className="flex h-[42px] shrink-0 items-center gap-2 border-b border-line-subtle bg-surface px-3">
        <button
          type="button"
          onClick={() => void load(parentPath(path))}
          className="flex h-7 w-7 items-center justify-center rounded text-muted hover:bg-hover"
          title="上级目录"
        >
          <Icon name="prev" size="sm" />
        </button>
        <button
          type="button"
          className="min-w-0 flex-1 truncate rounded-md bg-sunken px-2 py-1 text-left font-mono text-xs"
          title="点击跳转到路径"
          onClick={() =>
            askPrompt('跳转到路径', path, (next) => {
              if (next) void load(next);
            })
          }
        >
          {path}
        </button>
        <Button size="sm" variant="ghost" icon="refresh" onClick={() => void load(path)}>
          刷新
        </Button>
        <Button size="sm" variant="ghost" icon="folder" onClick={handleMkdir}>
          新建目录
        </Button>
        <Button size="sm" variant="ghost" icon="download" onClick={() => void handleDownloadSelected()}>
          下载所选{selectedCount > 0 ? ` (${selectedCount})` : ''}
        </Button>
        <Button size="sm" variant="primary" icon="upload" onClick={() => void handleUpload()}>
          上传
        </Button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center gap-2 border-b border-line-subtle px-3 py-1 text-[11px] text-muted">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={toggleAll}
            disabled={allFiles.length === 0}
            title="全选文件"
          />
          <span className="flex-1">名称</span>
          <span className="w-24 text-right">大小</span>
          <span className="w-36 text-right">修改时间</span>
          <span className="w-16 text-right">操作</span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-2 py-1">
          {loading && <div className="p-6 text-center text-sm text-muted">加载中…</div>}
          {!loading && error && <div className="p-6 text-center text-sm text-danger-500">{error}</div>}
          {!loading && !error && entries.length === 0 && (
            <EmptyState icon="folder" title="空目录" description="点击右上角上传文件" />
          )}
          {entries.map((entry) => (
            <div
              key={entry.name}
              className="group flex h-8 items-center gap-2 rounded px-2 text-sm hover:bg-hover"
              onDoubleClick={() => {
                if (entry.is_dir) void load(joinPath(path, entry.name));
                else void downloadOne(entry);
              }}
            >
              <input
                type="checkbox"
                checked={!entry.is_dir && selected.has(entry.name)}
                disabled={entry.is_dir}
                onChange={() => toggleOne(entry.name)}
              />
              <Icon name={entry.is_dir ? 'folder' : 'file'} size="sm" />
              <span className="min-w-0 flex-1 truncate">{entry.name}</span>
              <span className="w-24 text-right font-mono text-[11px] text-muted">
                {entry.is_dir ? '—' : formatBytes(entry.size)}
              </span>
              <span className="w-36 text-right font-mono text-[11px] text-subtle">
                {new Date(entry.mtime * 1000).toLocaleString()}
              </span>
              <span className="flex w-16 justify-end gap-1">
                {!entry.is_dir && (
                  <button
                    type="button"
                    className="rounded px-1.5 py-0.5 text-[11px] text-brand-600 hover:bg-brand-50"
                    onClick={() => void downloadOne(entry)}
                  >
                    下载
                  </button>
                )}
                <button
                  type="button"
                  className="hidden rounded px-1.5 py-0.5 text-[11px] text-muted hover:bg-hover group-hover:block"
                  onClick={() => handleRename(entry)}
                >
                  改名
                </button>
                <button
                  type="button"
                  className="hidden rounded px-1.5 py-0.5 text-[11px] text-danger-500 hover:bg-danger-50 group-hover:block"
                  onClick={() => void handleDelete(entry)}
                >
                  删除
                </button>
              </span>
            </div>
          ))}
        </div>
      </div>

      {transfers.length > 0 && (
        <div className="h-[168px] shrink-0 border-t border-line bg-surface">
          <div className="flex h-8 items-center justify-between border-b border-line-subtle px-3 text-xs text-muted">
            <span>传输队列</span>
            <button type="button" onClick={() => setTransfers([])}>
              清空
            </button>
          </div>
          <div className="h-[calc(100%-32px)] overflow-auto px-3 py-1">
            {transfers.map((item) => {
              const percent =
                item.total > 0 ? Math.min(100, Math.round((item.done / item.total) * 100)) : 0;
              return (
                <div key={item.id} className="flex items-center gap-2 py-1 text-xs">
                  <Icon name={item.direction === 'upload' ? 'upload' : 'download'} size="sm" />
                  <span className="w-44 truncate" title={item.name}>
                    {item.name}
                  </span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-line">
                    <div
                      className={[
                        'h-full',
                        item.status === 'failed' ? 'bg-danger-500' : 'bg-brand-500',
                      ].join(' ')}
                      style={{ width: `${item.status === 'done' ? 100 : percent}%` }}
                    />
                  </div>
                  <span className="w-40 text-right font-mono text-[11px] text-muted">
                    {item.status === 'failed'
                      ? item.message || '失败'
                      : item.status === 'done'
                        ? `完成 · ${formatBytes(item.total || item.done)}`
                        : `${formatBytes(item.done)} / ${formatBytes(item.total)} · ${formatSpeed(item.speed)}`}
                  </span>
                  {item.status === 'running' && (
                    <button
                      type="button"
                      className="text-[11px] text-muted hover:text-danger-500"
                      onClick={() => void remoteService.sftpCancel(sessionId)}
                    >
                      取消
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {prompt && (
        <PromptModal
          title={prompt.title}
          initial={prompt.initial}
          placeholder={prompt.placeholder}
          onCancel={() => setPrompt(null)}
          onConfirm={(value) => {
            setPrompt(null);
            prompt.onConfirm(value.trim());
          }}
        />
      )}
    </div>
  );
}


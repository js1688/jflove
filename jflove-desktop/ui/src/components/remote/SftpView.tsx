/**
 * SFTP 文件视图（v1.10.0）
 *
 * 远端文件操作全部由 Python 侧完成（渲染层无文件系统/网络出口）。
 * 上传/下载通过原生对话框选择路径，进度经 `remote.sftp_progress` 事件推送。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, EmptyState, Icon } from '../../components/ui';
import { callBridge, onBridgeEvent } from '../../utils/desktop-bridge';
import { remoteService, type SftpEntry } from '../../services/remote-service';

interface TransferItem {
  id: string;
  name: string;
  done: number;
  total: number;
  direction: 'upload' | 'download';
  status: 'running' | 'done' | 'failed';
  message?: string;
}

interface SftpViewProps {
  sessionId: string;
}

/** 拼接远端路径（统一用 `/`，根为 `.`） */
function joinPath(base: string, name: string): string {
  if (base === '.' || base === '/') return base === '/' ? `/${name}` : name;
  return `${base.replace(/\/+$/, '')}/${name}`;
}

/** 返回上级目录 */
function parentPath(path: string): string {
  if (path === '.' || path === '/' || !path.includes('/')) return '.';
  const parts = path.split('/').filter(Boolean);
  parts.pop();
  return parts.length ? parts.join('/') : '.';
}

/** 把事件 payload 收敛成对象 */
function asRecord(payload: unknown): Record<string, unknown> {
  return payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {};
}

export function SftpView({ sessionId }: SftpViewProps) {
  const [path, setPath] = useState('.');
  const [entries, setEntries] = useState<SftpEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [transfers, setTransfers] = useState<TransferItem[]>([]);
  /** 当前远端路径（供事件回调刷新，避免闭包拿到旧值） */
  const pathRef = useRef('.');

  const load = useCallback(
    async (target: string) => {
      setLoading(true);
      setError('');
      try {
        const result = await remoteService.sftpList(sessionId, target);
        setEntries(result.entries);
        setPath(target);
        pathRef.current = target;
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

  useEffect(() => {
    const offs = [
      onBridgeEvent('remote.sftp_progress', (payload) => {
        const data = asRecord(payload);
        const id = String(data.transfer_id || '');
        if (!id) return;
        setTransfers((prev) => {
          const existing = prev.find((item) => item.id === id);
          if (existing) {
            return prev.map((item) =>
              item.id === id
                ? { ...item, done: Number(data.bytes_done || 0), total: Number(data.bytes_total || 0) }
                : item,
            );
          }
          return [
            ...prev,
            {
              id,
              name: '传输中',
              done: Number(data.bytes_done || 0),
              total: Number(data.bytes_total || 0),
              direction: data.direction === 'upload' ? 'upload' : 'download',
              status: 'running',
            },
          ];
        });
      }),
      onBridgeEvent('remote.sftp_done', (payload) => {
        const id = String(asRecord(payload).transfer_id || '');
        setTransfers((prev) =>
          prev.map((item) => (item.id === id ? { ...item, status: 'done' } : item)),
        );
        // 上传/下载完成后刷新当前目录，避免用户看到旧列表
        void load(pathRef.current);
      }),
      onBridgeEvent('remote.sftp_failed', (payload) => {
        const data = asRecord(payload);
        const id = String(data.transfer_id || '');
        setTransfers((prev) =>
          prev.map((item) =>
            item.id === id
              ? { ...item, status: 'failed', message: String(data.message || '传输失败') }
              : item,
          ),
        );
      }),
    ];
    return () => offs.forEach((off) => off());
  }, [load]);

  const handleUpload = async () => {
    const picked = await callBridge<{ files: Array<{ path: string; name: string }> }>(
      'dialogs',
      'open_files',
    );
    for (const file of picked.files || []) {
      const transfer = await remoteService.sftpUpload(sessionId, file.path, joinPath(path, file.name));
      setTransfers((prev) => [
        ...prev,
        {
          id: transfer.transfer_id,
          name: file.name,
          done: 0,
          total: 0,
          direction: 'upload',
          status: 'running',
        },
      ]);
    }
  };

  const handleDownload = async (entry: SftpEntry) => {
    const target = await callBridge<{ path: string }>('dialogs', 'save_file', {
      default_name: entry.name,
    });
    if (!target.path) return;
    const transfer = await remoteService.sftpDownload(
      sessionId,
      joinPath(path, entry.name),
      target.path,
    );
    setTransfers((prev) => [
      ...prev,
      {
        id: transfer.transfer_id,
        name: entry.name,
        done: 0,
        total: entry.size,
        direction: 'download',
        status: 'running',
      },
    ]);
  };

  const handleMkdir = async () => {
    const name = window.prompt('新建目录名称');
    if (!name) return;
    await remoteService.sftpMkdir(sessionId, joinPath(path, name));
    await load(path);
  };

  const handleDelete = async (entry: SftpEntry) => {
    if (!window.confirm(`确定删除「${entry.name}」？`)) return;
    await remoteService.sftpDelete(sessionId, joinPath(path, entry.name), entry.is_dir);
    await load(path);
  };

  const handleRename = async (entry: SftpEntry) => {
    const name = window.prompt('重命名为', entry.name);
    if (!name || name === entry.name) return;
    await remoteService.sftpRename(
      sessionId,
      joinPath(path, entry.name),
      joinPath(path, name),
    );
    await load(path);
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      <div className="flex h-[42px] shrink-0 items-center gap-2 border-b border-line-subtle bg-surface px-3">
        <IconButtonBack onClick={() => void load(parentPath(path))} />
        <button
          type="button"
          className="min-w-0 flex-1 truncate rounded-md bg-sunken px-2 py-1 text-left font-mono text-xs"
          title={path}
          onClick={() => {
            const next = window.prompt('跳转到路径', path);
            if (next) void load(next);
          }}
        >
          {path}
        </button>
        <Button size="sm" variant="ghost" icon="refresh" onClick={() => void load(path)}>
          刷新
        </Button>
        <Button size="sm" variant="ghost" icon="folder" onClick={() => void handleMkdir()}>
          新建目录
        </Button>
        <Button size="sm" variant="primary" icon="upload" onClick={() => void handleUpload()}>
          上传
        </Button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 border-b border-line-subtle px-3 py-1 text-[11px] text-muted">
          <span className="flex-1">名称</span>
          <span className="w-24 text-right">大小</span>
          <span className="w-36 text-right">修改时间</span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-2 py-1">
          {loading && <div className="p-6 text-center text-sm text-muted">加载中…</div>}
          {!loading && error && (
            <div className="p-6 text-center text-sm text-danger-500">{error}</div>
          )}
          {!loading && !error && entries.length === 0 && (
            <EmptyState icon="folder" title="空目录" description="拖入或点击上传" />
          )}
          {entries.map((entry) => (
            <div
              key={entry.name}
              className="group flex h-8 items-center gap-2 rounded px-2 text-sm hover:bg-hover"
              onDoubleClick={() => {
                if (entry.is_dir) void load(joinPath(path, entry.name));
                else void handleDownload(entry);
              }}
            >
              <Icon name={entry.is_dir ? 'folder' : 'file'} size="sm" />
              <span className="min-w-0 flex-1 truncate">{entry.name}</span>
              <span className="w-24 text-right font-mono text-[11px] text-muted">
                {entry.is_dir ? '—' : `${Math.max(1, Math.round(entry.size / 1024))} KB`}
              </span>
              <span className="w-36 text-right font-mono text-[11px] text-subtle">
                {new Date(entry.mtime * 1000).toLocaleString()}
              </span>
              <span className="hidden gap-1 group-hover:flex">
                <Button size="sm" variant="ghost" onClick={() => void handleRename(entry)}>
                  重命名
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void handleDelete(entry)}>
                  删除
                </Button>
              </span>
            </div>
          ))}
        </div>
      </div>

      {transfers.length > 0 && (
        <div className="h-[132px] shrink-0 border-t border-line bg-surface">
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
                  <span className="w-44 truncate">{item.name}</span>
                  <div className="h-1 flex-1 overflow-hidden rounded-full bg-line">
                    <div
                      className="h-full bg-brand-500"
                      style={{ width: `${item.status === 'done' ? 100 : percent}%` }}
                    />
                  </div>
                  <span className="w-28 text-right font-mono text-[11px] text-muted">
                    {item.status === 'failed'
                      ? item.message || '失败'
                      : item.status === 'done'
                        ? '完成'
                        : `${percent}%`}
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
    </div>
  );
}

function IconButtonBack({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-7 w-7 items-center justify-center rounded text-muted hover:bg-hover"
      title="上级目录"
    >
      <Icon name="prev" size="sm" />
    </button>
  );
}

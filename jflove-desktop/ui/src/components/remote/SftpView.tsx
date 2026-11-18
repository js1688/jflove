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
import { useRemoteStore } from '../../stores/remote-store';
import { PromptModal } from './PromptModal';
import { ConfirmModal } from './ConfirmModal';

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
  /** 当前一次目录加载已用秒数（远端慢时让用户分得清「慢」与「卡死」） */
  const [loadingSeconds, setLoadingSeconds] = useState(0);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [transfers, setTransfers] = useState<TransferItem[]>([]);
  const [confirmDelete, setConfirmDelete] = useState<SftpEntry | null>(null);
  const [prompt, setPrompt] = useState<{
    title: string;
    initial: string;
    placeholder?: string;
    onConfirm: (value: string) => void;
  } | null>(null);
  const pathRef = useRef('.');
  const reloadTimer = useRef<number | null>(null);
  /**
   * 会话是否已就绪（v1.10.12 修复）。
   *
   * 标签是在 `session_open` 返回时立刻建的（`stores/remote-store.ts`），那一刻 Python 侧
   * 还在后台线程建链；旧实现挂载即 `load('.')`，只会拿到「会话尚未建立」且**永不重试**
   * —— 面板红字常驻，点「刷新」才会出目录。现在改为按 store 的会话状态门控：
   * `connecting` 只显示占位，`ready` 后自动加载一次。
   *
   * store 里查不到该会话（单测 / 独立使用）时按已就绪处理，保持旧行为。
   */
  const tabStatus = useRemoteStore(
    (state) => state.tabs.find((tab) => tab.id === sessionId)?.status,
  );
  const storeReady = tabStatus === undefined || tabStatus === 'ready';
  const sessionUnavailable = tabStatus === 'failed' || tabStatus === 'closed';
  /**
   * 兜底探活成功标记。
   *
   * `remote.session_ready` / `phase=ready` 有可能**早于标签创建**到达（标签是在
   * `session_open` 返回后由 store 建的，而 Python 侧先启动后台建链线程再返回）。
   * 事件早到 ⇒ `patchTab` 打在还不存在的标签上 = no-op ⇒ store 永远停在 `connecting`。
   * 只信 store 会让面板永久停在「正在连接」，所以连接中按秒探活一次（最多 30 次），
   * 探到就按就绪处理 —— 事件是主路径，探活只是自愈兜底。
   */
  const [probedReady, setProbedReady] = useState(false);
  const sessionReady = storeReady || probedReady;
  /** 已初始化过的会话 id（避免状态抖动时重复 `load('.')` 覆盖用户当前路径） */
  const bootedRef = useRef('');

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
        setError('');
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [sessionId],
  );

  useEffect(() => {
    if (!sessionReady) return;
    if (bootedRef.current === sessionId) return;
    bootedRef.current = sessionId;
    void load('.');
  }, [load, sessionId, sessionReady]);

  /** 加载计时（仅展示，不改判据；超时/失败仍以 Python 侧超时为准） */
  useEffect(() => {
    if (!loading) {
      setLoadingSeconds(0);
      return undefined;
    }
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setLoadingSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [loading]);

  /**
   * 取消当前目录加载：关闭该会话的 SFTP client（阻塞中的读会立即失败）。
   *
   * Python 侧的 listdir 有 45s 上限；这里给的是一次「不想等了」的主动出口。
   */
  const cancelLoad = async () => {
    // 先给即时反馈：关闭 client 后阻塞中的 listdir 会失败，但可能需要一点时间传播
    setLoading(false);
    setError('已取消加载，可点「重试」重新拉取目录');
    try {
      await remoteService.sftpAbort(sessionId);
    } catch {
      // 取消本身失败不覆盖真正的加载错误
    }
  };

  /**
   * 连接中兜底探活（见 `probedReady` 注释）：每 1s 试一次目录，最多 30 次；
   * 探到即按就绪处理并停表。就绪事件正常到达时本效果随即被 deps 变化取消，不会重复请求。
   */
  useEffect(() => {
    if (sessionReady || sessionUnavailable) return undefined;
    let cancelled = false;
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (attempts > 30) {
        window.clearInterval(timer);
        return;
      }
      void remoteService
        .sftpList(sessionId, '.')
        .then((result) => {
          if (cancelled) return;
          window.clearInterval(timer);
          bootedRef.current = sessionId; // 已加载过：就绪效果不再重复拉一次
          setEntries(result.entries);
          setPath('.');
          pathRef.current = '.';
          setSelected(new Set());
          setError('');
          setProbedReady(true);
        })
        .catch(() => {
          /* 还没就绪：保持占位，等下一轮 */
        });
    }, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sessionId, sessionReady, sessionUnavailable]);

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

  const handleDelete = (entry: SftpEntry) => setConfirmDelete(entry);

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
          disabled={!sessionReady}
          className="flex h-7 w-7 items-center justify-center rounded text-muted hover:bg-hover disabled:opacity-40"
          title="上级目录"
        >
          <Icon name="prev" size="sm" />
        </button>
        <button
          type="button"
          className="min-w-0 flex-1 truncate rounded-md bg-sunken px-2 py-1 text-left font-mono text-xs"
          title="点击跳转到路径"
          disabled={!sessionReady}
          onClick={() =>
            askPrompt('跳转到路径', path, (next) => {
              if (next) void load(next);
            })
          }
        >
          {path}
        </button>
        <Button
          size="sm"
          variant="ghost"
          icon="refresh"
          disabled={!sessionReady}
          onClick={() => void load(path)}
        >
          刷新
        </Button>
        <Button size="sm" variant="ghost" icon="folder" disabled={!sessionReady} onClick={handleMkdir}>
          新建目录
        </Button>
        <Button
          size="sm"
          variant="ghost"
          icon="download"
          disabled={!sessionReady}
          onClick={() => void handleDownloadSelected()}
        >
          下载所选{selectedCount > 0 ? ` (${selectedCount})` : ''}
        </Button>
        <Button
          size="sm"
          variant="primary"
          icon="upload"
          disabled={!sessionReady}
          onClick={() => void handleUpload()}
        >
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
          {/* 会话未就绪：给明确的等待/失败语义，不再把「会话尚未建立」当成目录错误 */}
          {!sessionReady && !sessionUnavailable && (
            <div className="p-6 text-center text-sm text-muted">
              会话正在连接，就绪后自动加载目录…
            </div>
          )}
          {!sessionReady && sessionUnavailable && (
            <div className="p-6 text-center text-sm text-danger-500">
              会话未就绪，请关闭标签后重新连接
            </div>
          )}
          {sessionReady && loading && (
            <div className="p-6 text-center text-sm text-muted">
              <div>加载中…（已用 {loadingSeconds} 秒）</div>
              <div className="mt-1 text-[11px] text-subtle">
                远端目录或网络较慢；超过 45 秒会自动失败并给出原因
              </div>
              <button
                type="button"
                className="mt-3 rounded border border-line px-3 py-1 text-xs text-muted hover:bg-hover hover:text-fg"
                onClick={() => void cancelLoad()}
              >
                取消加载
              </button>
            </div>
          )}
          {sessionReady && !loading && error && (
            <div className="p-6 text-center text-sm text-danger-500">
              <div>{error}</div>
              <button
                type="button"
                className="mt-3 rounded border border-line px-3 py-1 text-xs text-muted hover:bg-hover hover:text-fg"
                onClick={() => void load(path)}
              >
                重试
              </button>
            </div>
          )}
          {sessionReady && !loading && !error && entries.length === 0 && (
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
                  onClick={() => handleDelete(entry)}
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

      {confirmDelete && (
        <ConfirmModal
          title="删除远端文件"
          message={`确定删除「${confirmDelete.name}」？`}
          danger
          onCancel={() => setConfirmDelete(null)}
          onConfirm={() => {
            const entry = confirmDelete;
            setConfirmDelete(null);
            void remoteService
              .sftpDelete(sessionId, joinPath(path, entry.name), entry.is_dir)
              .then(() => load(path));
          }}
        />
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


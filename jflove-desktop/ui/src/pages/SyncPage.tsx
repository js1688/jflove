/**
 * 云盘挂载页（v1.7.0，替代原「同步管理」）
 *
 * 模型：**挂载点** =（远端目录 → 本地目录），两端**自动保持一致**：
 *   - 远端新增 / 修改 / 删除 → 自动落到本地（远端删除会删掉本地缓存）；
 *   - 本地新增 / 修改 → 自动上传到远端；
 *   - **本地删除只删本地缓存，绝不回传远端**（远端删除只能在文件管理页做）；
 *   - 读取「仅在线」文件时由文件系统（Windows CfAPI / Linux FUSE）按需下载。
 *
 * 因此本页**不提供下载 / 释放 / 全量对账按钮** —— 一切都自动完成，
 * 页面只负责如实显示每个文件当前处于哪种状态。
 *
 * 配置真相在 Python（`mounts_{username}.json`），渲染层不落盘任何配置。
 * 文件字节不进渲染层：传输由 Python 侧 `transfer_manager` 完成。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { PageHeader } from '../components/PageHeader';
import { DirTreeModal } from '../components/DirTreeModal';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { LoadingSpinner } from '../components/LoadingSpinner';
import {
  Badge,
  Button,
  EmptyState,
  Icon,
  IconButton,
  Modal,
  Switch,
  toast,
} from '../components/ui';
import { diskService } from '../services/disk-service';
import { onBridgeEvent } from '../utils/desktop-bridge';
import { fileService } from '../services/file-service';
import {
  mountService,
  type MountFile,
  type MountInfo,
  type MountInput,
} from '../services/mount-service';

/** 字节数 → 紧凑可读大小 */
function fmtSize(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

/** 单页最多渲染的文件数（避免上万条时卡顿） */
const MAX_RENDER_FILES = 500;

/** 文件状态轮询间隔（毫秒）：读本地索引 + stat，用于显示实时进度 */
const POLL_MS = 1000;

/** 挂载点统计轮询间隔（毫秒）：要遍历全部文件，放慢一些 */
const MOUNT_POLL_MS = 3000;

/** 状态 → 展示文案 */
const FILE_STATE_LABEL: Record<MountFile['state'], string> = {
  synced: '已同步',
  pending: '待上传',
  hashing: '校验中',
  uploading: '上传中',
  finalizing: '服务端处理中',
  downloading: '下载中',
  failed: '失败',
  online: '在线',
};

/** 状态 → 徽标色调 */
function stateTone(state: MountFile['state']): 'success' | 'brand' | 'warning' | 'danger' | 'neutral' {
  if (state === 'synced') return 'success';
  if (state === 'hashing' || state === 'uploading' || state === 'finalizing' ||
    state === 'downloading') return 'brand';
  if (state === 'pending') return 'warning';
  if (state === 'failed') return 'danger';
  return 'neutral';
}

/** 秒数 → 紧凑时长（45 秒 / 3 分 05 秒 / 12 分） */
function fmtElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return rest ? `${m} 分 ${String(rest).padStart(2, '0')} 秒` : `${m} 分`;
}

/** 状态 → 动作描述（用于「正在传输」列表） */
function stateAction(state: MountFile['state']): string {
  if (state === 'hashing') return '正在校验';
  if (state === 'uploading') return '正在上传';
  if (state === 'finalizing') return '服务端合并校验中';
  if (state === 'downloading') return '正在下载';
  if (state === 'pending') return '等待上传';
  if (state === 'failed') return '上传失败';
  return '';
}

const EMPTY_FORM: MountInput = {
  name: '',
  disk_id: 0,
  remote_path: '',
  local_path: '',
};

export function SyncPage() {
  const [mounts, setMounts] = useState<MountInfo[]>([]);
  const [disks, setDisks] = useState<{ id: number; name: string }[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [files, setFiles] = useState<MountFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [filesLoading, setFilesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 新建 / 编辑弹窗
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState('');
  const [form, setForm] = useState<MountInput>(EMPTY_FORM);
  const [diskOpen, setDiskOpen] = useState(false);
  const [showRemotePicker, setShowRemotePicker] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<MountInfo | null>(null);

  const selected = useMemo(
    () => mounts.find((m) => m.id === selectedId) ?? null,
    [mounts, selectedId],
  );
  const selectedDiskName =
    disks.find((d) => d.id === form.disk_id)?.name ?? '';

  const loadMounts = useCallback(async () => {
    try {
      const list = await mountService.list();
      setMounts(list);
      setError(null);
      setSelectedId((prev) =>
        prev && list.some((m) => m.id === prev) ? prev : (list[0]?.id ?? ''),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取挂载点失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadFiles = useCallback(async (mountId: string) => {
    if (!mountId) {
      setFiles([]);
      return;
    }
    setFilesLoading(true);
    try {
      setFiles(await mountService.files(mountId));
    } catch (e) {
      toast.error('读取文件列表失败', e instanceof Error ? e.message : '未知错误');
      setFiles([]);
    } finally {
      setFilesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMounts();
    diskService
      .listAllDisks()
      .then((rows) => setDisks(rows.map((d) => ({ id: d.id, name: d.name }))))
      .catch(() => {});
  }, [loadMounts]);

  useEffect(() => {
    void loadFiles(selectedId);
  }, [selectedId, loadFiles]);

  // 自动同步在后台上传 / 下载：文件状态 1s 刷新（进度实时），挂载点统计 3s 刷新
  useEffect(() => {
    if (!selectedId || !selected?.enabled) return;
    const filesTimer = window.setInterval(() => {
      void mountService
        .files(selectedId)
        .then(setFiles)
        .catch(() => {});
    }, POLL_MS);
    const mountsTimer = window.setInterval(() => {
      void mountService
        .list()
        .then(setMounts)
        .catch(() => {});
    }, MOUNT_POLL_MS);
    return () => {
      window.clearInterval(filesTimer);
      window.clearInterval(mountsTimer);
    };
  }, [selectedId, selected?.enabled]);

  // 进度**不能只靠定时器**：窗口在后台时 Chromium 会把 setInterval 节流到每分钟一次，
  // 大文件上传/合并期间界面看起来像卡住。传输进度本身由 Python 主动推送事件，
  // 这里订阅事件即时刷新（事件推送不受定时器节流影响）。
  useEffect(() => {
    if (!selectedId) return;
    let pendingRefresh = 0;
    const refresh = () => {
      if (pendingRefresh) return; // 合并突发事件：最多每 300ms 刷新一次
      pendingRefresh = window.setTimeout(() => {
        pendingRefresh = 0;
        void mountService.files(selectedId).then(setFiles).catch(() => {});
        void mountService.list().then(setMounts).catch(() => {});
      }, 300);
    };
    const offs = [
      onBridgeEvent('transfer.added', refresh),
      onBridgeEvent('transfer.updated', refresh),
      onBridgeEvent('transfer.removed', refresh),
    ];
    return () => {
      offs.forEach((off) => off());
      if (pendingRefresh) window.clearTimeout(pendingRefresh);
    };
  }, [selectedId]);

  const openCreate = async () => {
    let available = disks;
    if (available.length === 0) {
      try {
        const rows = await diskService.listAllDisks();
        available = rows.map((d) => ({ id: d.id, name: d.name }));
        setDisks(available);
      } catch {
        // 忽略：下面仍会用 0，保存时给出明确校验提示
      }
    }
    setEditingId('');
    setForm({ ...EMPTY_FORM, disk_id: available[0]?.id ?? 0 });
    setShowForm(true);
  };

  const openEdit = (mount: MountInfo) => {
    setEditingId(mount.id);
    setForm({
      name: mount.name,
      disk_id: mount.disk_id,
      remote_path: mount.remote_path,
      local_path: mount.local_path,
    });
    setShowForm(true);
  };

  const submitForm = async () => {
    try {
      if (editingId) {
        await mountService.update(editingId, form);
        toast.success('挂载点已更新');
      } else {
        await mountService.create(form);
        toast.success('挂载点已创建', '开关默认关闭，请手动打开');
      }
      setShowForm(false);
      await loadMounts();
    } catch (e) {
      toast.error('保存失败', e instanceof Error ? e.message : '未知错误');
    }
  };

  const toggleEnabled = async (mount: MountInfo, enabled: boolean) => {
    try {
      await mountService.setEnabled(mount.id, enabled);
      if (enabled) {
        // 打开开关后立刻拉一次增量，让文件列表有内容
        await mountService.refresh(mount.id);
      }
      await loadMounts();
      if (mount.id === selectedId) await loadFiles(mount.id);
      toast.success(enabled ? '挂载点已启用' : '挂载点已停用');
    } catch (e) {
      toast.error(enabled ? '启用失败' : '停用失败', e instanceof Error ? e.message : '未知错误');
    }
  };

  const refreshSelected = async () => {
    if (!selected) return;
    try {
      await mountService.refresh(selected.id);
      await loadMounts();
      await loadFiles(selected.id);
    } catch (e) {
      toast.error('同步失败', e instanceof Error ? e.message : '未知错误');
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await mountService.remove(deleteTarget.id);
      setDeleteTarget(null);
      await loadMounts();
      toast.success('挂载点已删除', '本地已下载的文件不会被删除');
    } catch (e) {
      toast.error('删除失败', e instanceof Error ? e.message : '未知错误');
    }
  };

  const browseLocal = async () => {
    try {
      const path = await fileService.pickDirectory('选择本地缓存目录');
      if (path) setForm((f) => ({ ...f, local_path: path }));
    } catch (e) {
      toast.error('选择目录失败', e instanceof Error ? e.message : '未知错误');
    }
  };

  const renderedFiles = files.slice(0, MAX_RENDER_FILES);
  const filesOnly = files.filter((f) => !f.is_dir);
  const countOf = (state: MountFile['state']) =>
    filesOnly.filter((f) => f.state === state).length;
  const activeFiles = filesOnly.filter(
    (f) => f.state === 'hashing' || f.state === 'uploading' ||
      f.state === 'finalizing' || f.state === 'downloading' ||
      f.state === 'pending' || f.state === 'failed',
  );

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="云盘挂载"
        subtitle="本地与远端自动保持一致：改动自动上传，读取时自动下载；本地删除不会影响远端"
        actions={
          <Button variant="primary" icon="plus" onClick={() => void openCreate()}>
            新建挂载点
          </Button>
        }
      />

      {loading && <LoadingSpinner text="加载挂载点…" />}

      {!loading && error && (
        <div className="px-4 py-3">
          <div className="card flex items-center gap-2 text-[13px] text-danger-600">
            <Icon name="error" size="sm" />
            {error}
          </div>
        </div>
      )}

      {!loading && !error && mounts.length === 0 && (
        <EmptyState
          icon="sync"
          title="还没有挂载点"
          description="新建一个挂载点，把远端目录映射到本地缓存目录；开关默认关闭，可随时停用"
        />
      )}

      {!loading && !error && mounts.length > 0 && (
        <div className="flex min-h-0 flex-1">
          {/* 左：挂载点列表 */}
          <div className="w-[340px] shrink-0 space-y-2 overflow-y-auto border-r border-line-subtle p-3">
            {mounts.map((mount) => {
              const active = mount.id === selectedId;
              return (
                <button
                  key={mount.id}
                  type="button"
                  data-mount-id={mount.id}
                  className={`w-full rounded-xl border p-3 text-left transition ${
                    active
                      ? 'border-brand-600 bg-brand-50'
                      : 'border-line-subtle bg-surface hover:bg-hover'
                  }`}
                  onClick={() => setSelectedId(mount.id)}
                >
                  <div className="flex items-center gap-2">
                    <Icon name="sync" size="sm" className="text-brand-600" />
                    <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-fg">
                      {mount.name}
                    </span>
                    <Badge tone={mount.enabled ? 'success' : 'neutral'}>
                      {mount.enabled ? '已启用' : '已停用'}
                    </Badge>
                  </div>
                  <div className="mt-2 space-y-0.5 text-[11.5px] text-subtle">
                    <div className="truncate">
                      远端：磁盘 {mount.disk_id}
                      {mount.remote_path ? ` / ${mount.remote_path}` : ' / 根目录'}
                    </div>
                    <div className="truncate" title={mount.local_path}>
                      本地：{mount.local_path}
                    </div>
                    <div>
                      共 {mount.status.total} 项 · 已同步 {mount.status.synced}
                      {(mount.status.hashing + mount.status.uploading +
                        mount.status.finalizing + mount.status.downloading +
                        mount.status.pending) > 0 &&
                        ` · 待同步 ${mount.status.hashing + mount.status.uploading +
                          mount.status.finalizing + mount.status.downloading +
                          mount.status.pending}`}
                      {mount.status.failed > 0 && ` · 失败 ${mount.status.failed}`}
                      {mount.status.online > 0 && ` · 在线 ${mount.status.online}`}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          {/* 右：选中挂载点的文件与状态 */}
          <div className="flex min-w-0 flex-1 flex-col">
            {!selected && (
              <div className="p-4 text-[13px] text-subtle">请选择左侧的挂载点</div>
            )}

            {selected && (
              <>
                <div className="flex flex-wrap items-center gap-2 border-b border-line-subtle px-4 py-3">
                  <span className="text-[13px] font-semibold text-fg">{selected.name}</span>
                  <span className="text-[11.5px] text-subtle">
                    {selected.enabled ? '已启用' : '已停用'}
                  </span>
                  <div className="ml-auto flex items-center gap-1">
                    <Switch
                      checked={selected.enabled}
                      onChange={(v) => void toggleEnabled(selected, v)}
                      label="启用挂载点"
                    />
                    <Button
                      size="sm"
                      icon="refresh"
                      disabled={!selected.enabled}
                      onClick={() => void refreshSelected()}
                    >
                      刷新
                    </Button>
                    <IconButton
                      icon="edit"
                      label="编辑挂载点"
                      size="sm"
                      onClick={() => openEdit(selected)}
                    />
                    <IconButton
                      icon="delete"
                      label="删除挂载点"
                      size="sm"
                      onClick={() => setDeleteTarget(selected)}
                    />
                  </div>
                </div>

                {!selected.enabled && (
                  <div className="px-4 py-3 text-[12.5px] text-subtle">
                    挂载点默认关闭。打开上方开关后会拉取远端清单。
                  </div>
                )}

                {selected.enabled && filesLoading && (
                  <LoadingSpinner text="读取文件列表…" />
                )}

                {selected.enabled && !filesLoading && files.length === 0 && (
                  <EmptyState
                    icon="files"
                    title="还没有文件"
                    description="往本地目录添加文件会自动上传；远端新增也会自动出现"
                  />
                )}

                {selected.enabled && !filesLoading && files.length > 0 && (
                  <div className="min-h-0 flex-1 overflow-y-auto">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line-subtle px-4 py-2 text-[11.5px] text-subtle">
                      <span>共 {filesOnly.length} 项</span>
                      <span className="text-success-700">已同步 {countOf('synced')}</span>
                      {countOf('pending') > 0 && (
                        <span className="text-warning-700">待上传 {countOf('pending')}</span>
                      )}
                      {countOf('hashing') > 0 && (
                        <span className="text-brand-600">校验中 {countOf('hashing')}</span>
                      )}
                      {countOf('uploading') > 0 && (
                        <span className="text-brand-600">上传中 {countOf('uploading')}</span>
                      )}
                      {countOf('finalizing') > 0 && (
                        <span className="text-brand-600">处理中 {countOf('finalizing')}</span>
                      )}
                      {countOf('downloading') > 0 && (
                        <span className="text-brand-600">下载中 {countOf('downloading')}</span>
                      )}
                      {countOf('failed') > 0 && (
                        <span className="text-danger-700">失败 {countOf('failed')}</span>
                      )}
                      {countOf('online') > 0 && <span>在线 {countOf('online')}</span>}
                      {files.length > renderedFiles.length && (
                        <span>（仅显示前 {renderedFiles.length} 项）</span>
                      )}
                    </div>

                    {/* 正在传输 / 待上传 / 失败：直接列出文件名，不用猜 */}
                    {activeFiles.length > 0 && (
                      <div className="border-b border-line-subtle bg-hover/40 px-4 py-2">
                        <div className="mb-1 text-[11.5px] font-medium text-muted">
                          正在同步 {activeFiles.length} 项
                        </div>
                        {activeFiles.slice(0, 8).map((file) => (
                          <div
                            key={`active-${file.rel_path}`}
                            className="flex items-center gap-2 py-0.5 text-[12px]"
                          >
                            <Icon
                              name={file.direction === 'download' ? 'download' : 'upload'}
                              size="sm"
                              className="shrink-0 text-subtle"
                            />
                            <span className="min-w-0 flex-1 truncate text-fg">
                              {file.rel_path}
                            </span>
                            <span
                              className={`shrink-0 text-[11.5px] ${
                                file.stalled > 0 ? 'text-warning-700' : 'text-subtle'
                              }`}
                            >
                              {stateAction(file.state)}
                              {file.progress > 0 ? ` ${file.progress}%` : ''}
                              {file.state === 'finalizing' && file.elapsed > 0
                                ? ` · 已 ${fmtElapsed(file.elapsed)}`
                                : ''}
                              {file.stalled > 0
                                ? ` · 已 ${fmtElapsed(file.stalled)}无进展`
                                : ''}
                            </span>
                          </div>
                        ))}
                        {activeFiles.length > 8 && (
                          <div className="pt-0.5 text-[11.5px] text-subtle">
                            还有 {activeFiles.length - 8} 项…
                          </div>
                        )}
                        <div className="pt-1 text-[11px] leading-relaxed text-subtle">
                          上传期间文件以分片暂存在服务端临时目录；传完还要服务端合并校验，
                          全部通过后才会出现在远端目录。「校验中 100%」= 哈希算完、即将开始传；
                          「服务端处理中 100%」= 分片已传完、正在合并，稍等即可。
                        </div>
                      </div>
                    )}
                    {renderedFiles.map((file) => (
                      <div
                        key={file.rel_path}
                        className="flex items-center gap-3 border-b border-line-subtle px-4 py-2"
                      >
                        <Icon
                          name={file.is_dir ? 'folder' : 'files'}
                          size="sm"
                          className="shrink-0 text-subtle"
                        />
                        <span className="min-w-0 flex-1 truncate text-[12.5px] text-fg">
                          {file.rel_path}
                        </span>
                        <span className="w-[88px] shrink-0 text-right text-[11.5px] text-subtle">
                          {file.is_dir ? '目录' : fmtSize(file.size)}
                        </span>
                        <span className="w-[96px] shrink-0 text-right">
                          {file.is_dir ? (
                            <span className="text-[11.5px] text-subtle">—</span>
                          ) : (
                            <span title={file.error || undefined}>
                              <Badge tone={stateTone(file.state)}>
                                {(file.state === 'uploading' ||
                                  file.state === 'downloading') && file.progress > 0
                                  ? `${FILE_STATE_LABEL[file.state]} ${file.progress}%`
                                  : FILE_STATE_LABEL[file.state]}
                              </Badge>
                            </span>
                          )}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* 新建 / 编辑 */}
      {showForm && (
        <Modal
          open
          title={editingId ? '编辑挂载点' : '新建挂载点'}
          icon="sync"
          width={560}
          onClose={() => setShowForm(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setShowForm(false)}>
                取消
              </Button>
              <Button variant="primary" icon="checked" onClick={() => void submitForm()}>
                保存
              </Button>
            </>
          }
        >
          <div className="space-y-3">
            <div>
              <div className="mb-1.5 text-[12px] font-medium text-muted">名称</div>
              <input
                className="input"
                placeholder="例如：照片库"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>

            <div>
              <div className="mb-1.5 text-[12px] font-medium text-muted">远端磁盘</div>
              <div className="relative">
                <button
                  type="button"
                  className="input flex w-full items-center justify-between text-left"
                  onClick={() => setDiskOpen((v) => !v)}
                >
                  <span className={selectedDiskName ? 'text-fg' : 'text-subtle'}>
                    {selectedDiskName || '请选择磁盘'}
                  </span>
                  <Icon name="expand" size="sm" />
                </button>
                {diskOpen && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setDiskOpen(false)} />
                    <div
                      className="absolute left-0 right-0 top-[calc(100%+6px)] z-20 overflow-hidden rounded-xl border border-line-subtle bg-surface py-1"
                      style={{ boxShadow: 'var(--e3)' }}
                    >
                      {disks.map((disk) => (
                        <button
                          key={disk.id}
                          type="button"
                          className="flex w-full items-center justify-between px-3 py-2 text-left text-[13px] hover:bg-hover"
                          onClick={() => {
                            setForm((f) => ({ ...f, disk_id: disk.id, remote_path: '' }));
                            setDiskOpen(false);
                          }}
                        >
                          <span>{disk.name}</span>
                          {disk.id === form.disk_id && (
                            <Icon name="checked" size="sm" className="text-brand-600" />
                          )}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>

            <div>
              <div className="mb-1.5 text-[12px] font-medium text-muted">远端子目录</div>
              <div className="flex items-center gap-2">
                <input
                  className="input flex-1"
                  placeholder="磁盘内子目录，留空表示根目录"
                  value={form.remote_path}
                  onChange={(e) => setForm((f) => ({ ...f, remote_path: e.target.value }))}
                />
                <Button
                  size="sm"
                  icon="folder"
                  disabled={!form.disk_id}
                  onClick={() => setShowRemotePicker(true)}
                >
                  选择…
                </Button>
              </div>
            </div>

            <div>
              <div className="mb-1.5 text-[12px] font-medium text-muted">本地缓存目录</div>
              <div className="flex items-center gap-2">
                <input
                  className="input flex-1"
                  placeholder="选择或粘贴本地目录绝对路径"
                  value={form.local_path}
                  onChange={(e) => setForm((f) => ({ ...f, local_path: e.target.value }))}
                />
                <Button size="sm" icon="folder" onClick={() => void browseLocal()}>
                  浏览…
                </Button>
              </div>
            </div>

            <div className="rounded-lg border border-line-subtle px-3 py-2.5 text-[11.5px] text-subtle">
              新建后开关默认关闭；打开后本地目录与远端自动保持一致。
              本地删除只删本地缓存，不会删除远端文件。
            </div>
          </div>
        </Modal>
      )}

      {/* 远端目录选择（复用目录树弹窗） */}
      {showRemotePicker && form.disk_id > 0 && (
        <DirTreeModal
          diskId={form.disk_id}
          diskName={selectedDiskName}
          onSelect={(path) => {
            setForm((f) => ({ ...f, remote_path: path }));
            setShowRemotePicker(false);
          }}
          onClose={() => setShowRemotePicker(false)}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="确认删除"
          message={`确定要删除挂载点「${deleteTarget.name}」吗？远端文件与本地已下载的文件都不会被删除。`}
          confirmLabel="删除"
          danger
          onConfirm={() => void confirmDelete()}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}

/**
 * 云盘挂载页（v1.7.0，替代原「同步管理」）
 *
 * 模型变化：
 *   - 旧版：整目录双向同步（全量扫描 + size/mtime 猜），不知道上传了没有。
 *   - 新版：**挂载点** =（远端目录 → 本地缓存目录），远端文件在这里**全部可见**，
 *     并逐个标记「已缓存 / 仅在线」，可手动下载 / 释放；增量靠服务端变更日志。
 *
 * 配置真相在 Python（`mounts_{username}.json`），渲染层不落盘任何配置。
 * 文件字节不进渲染层：下载由 Python 侧 `transfer_manager` 完成。
 *
 * 已知边界（如实标注）：当前为**应用内挂载视图**，尚未注册系统级虚拟盘
 * （Windows CfAPI / Linux FUSE），因此资源管理器里只能看到已下载的文件。
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

  const refreshSelected = async (mode: 'refresh' | 'reconcile') => {
    if (!selected) return;
    try {
      const result =
        mode === 'refresh'
          ? await mountService.refresh(selected.id)
          : await mountService.reconcile(selected.id);
      toast.success(
        mode === 'refresh' ? '增量同步完成' : '全量对账完成',
        `更新 ${result.applied} 项，移除 ${result.deleted} 项`,
      );
      await loadMounts();
      await loadFiles(selected.id);
    } catch (e) {
      toast.error('同步失败', e instanceof Error ? e.message : '未知错误');
    }
  };

  const downloadFile = async (file: MountFile) => {
    if (!selected) return;
    try {
      await mountService.download(selected.id, file.rel_path);
      toast.info('已加入下载队列', file.rel_path);
    } catch (e) {
      toast.error('下载失败', e instanceof Error ? e.message : '未知错误');
    }
  };

  const releaseFile = async (file: MountFile) => {
    if (!selected) return;
    try {
      await mountService.release(selected.id, file.rel_path);
      await loadFiles(selected.id);
      toast.success('已释放本地缓存', file.rel_path);
    } catch (e) {
      toast.error('释放失败', e instanceof Error ? e.message : '未知错误');
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

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="云盘挂载"
        subtitle="把远端目录挂到本地：全部文件可见，按需下载，默认关闭"
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
                      共 {mount.status.total} 项 · 已缓存 {mount.status.cached} · 仅在线{' '}
                      {mount.status.online}
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
                      onClick={() => void refreshSelected('refresh')}
                    >
                      刷新
                    </Button>
                    <Button
                      size="sm"
                      disabled={!selected.enabled}
                      onClick={() => void refreshSelected('reconcile')}
                    >
                      全量对账
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
                    title="远端目录是空的"
                    description="点「刷新」重新拉取；若刚启用，请稍候"
                  />
                )}

                {selected.enabled && !filesLoading && files.length > 0 && (
                  <div className="min-h-0 flex-1 overflow-y-auto">
                    <div className="px-4 py-2 text-[11.5px] text-subtle">
                      共 {files.length} 项（显示前 {renderedFiles.length} 项）
                    </div>
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
                        <span className="w-[72px] shrink-0 text-right">
                          <Badge tone={file.cached ? 'success' : 'neutral'}>
                            {file.cached ? '已缓存' : '仅在线'}
                          </Badge>
                        </span>
                        <div className="w-[120px] shrink-0 text-right">
                          {file.is_dir ? null : file.cached ? (
                            <Button size="sm" onClick={() => void releaseFile(file)}>
                              释放
                            </Button>
                          ) : (
                            <Button
                              size="sm"
                              icon="download"
                              onClick={() => void downloadFile(file)}
                            >
                              下载
                            </Button>
                          )}
                        </div>
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
              新建后开关默认关闭；打开后才会连接远端并拉取清单。
              当前为应用内挂载视图，尚未注册系统级虚拟盘。
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

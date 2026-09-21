/**
 * 同步管理页（N9，桌面端专有）
 *
 * Web 端此页是**降级说明页**（浏览器不能访问本地文件系统），所以本页没有 Web 参考，
 * 按**原桌面端**的同步能力实现（`sync_page.py` + `sync_engine.py`），
 * 视觉沿用 Web 端那套组件（PageHeader / Card / Modal / Switch / Toast），保证三端观感一致。
 *
 * 能力：
 *   - 规则列表：别名 / 本地目录 / 目标磁盘与远端目录 / 自动同步与间隔 / 上次同步 / 启停
 *   - 新建·编辑：本地目录走**原生目录选择对话框**，远端目录复用目录树弹窗
 *   - 立即同步：调引擎触发一次，进度与结果由桥事件回流
 *
 * 规则真相在 Python（本地 JSON），渲染层不落盘任何规则数据。
 */
import { useCallback, useEffect, useState } from 'react';
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
import { syncService, type SyncConfig, type SyncConfigInput } from '../services/sync-service';
import { onBridgeEvent } from '../utils/desktop-bridge';

/** 把 ISO 时间格式化成 `MM-DD HH:mm`（与其它页一致的紧凑写法） */
function fmtTime(iso: string | null): string {
  if (!iso) return '从未同步';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '从未同步';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 间隔秒数 → 人话 */
function fmtInterval(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 3600)} 小时`;
}

export function SyncPage() {
  const [configs, setConfigs] = useState<SyncConfig[]>([]);
  const [disks, setDisks] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [runningIds, setRunningIds] = useState<string[]>([]);

  // 新建 / 编辑弹窗
  const [editing, setEditing] = useState<SyncConfig | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<SyncConfigInput>({
    name: '',
    local_path: '',
    disk_id: 0,
    remote_path: '',
    auto_sync: true,
    sync_interval: 300,
    enabled: true,
  });
  const [diskOpen, setDiskOpen] = useState(false);
  const [showRemotePicker, setShowRemotePicker] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<SyncConfig | null>(null);

  const reload = useCallback(async () => {
    try {
      const list = await syncService.list();
      setConfigs(list);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取同步规则失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    diskService
      .listAllDisks()
      .then((rows) => setDisks(rows.map((d) => ({ id: d.id, name: d.name }))))
      .catch(() => {});
    // 规则变更后让 Python 侧引擎重载（自动同步定时器据此更新）
    void syncService.reload().catch(() => {});
  }, [reload]);

  // 引擎事件：进度与结果回流
  useEffect(() => {
    const offStart = onBridgeEvent('sync.started', (p) => {
      const id = String((p as { config_id?: string } | null)?.config_id ?? '');
      if (id) {
        setRunningIds((prev) => (prev.includes(id) ? prev : [...prev, id]));
        toast.info('开始同步');
      }
    });
    const offDone = onBridgeEvent('sync.finished', (p) => {
      const r = (p ?? {}) as { config_id?: string; total_actions?: number };
      setRunningIds((prev) => prev.filter((id) => id !== r.config_id));
      toast.success(
        '同步完成',
        typeof r.total_actions === 'number' ? `本次处理 ${r.total_actions} 项` : undefined,
      );
      void reload();
    });
    const offErr = onBridgeEvent('sync.error', (p) => {
      const r = (p ?? {}) as { config_id?: string; message?: string };
      setRunningIds((prev) => prev.filter((id) => id !== r.config_id));
      toast.error('同步失败', r.message);
    });
    return () => {
      offStart();
      offDone();
      offErr();
    };
  }, [reload]);

  const openCreate = async () => {
    setEditing(null);
    // 磁盘列表可能还没加载完（或加载失败）：这里补一次，避免新规则落在 disk_id=0
    // 上被保存校验拦下（表现为"点了保存没反应"）。
    let available = disks;
    if (available.length === 0) {
      try {
        const rows = await diskService.listAllDisks();
        available = rows.map((d) => ({ id: d.id, name: d.name }));
        setDisks(available);
      } catch {
        // 忽略：下面仍会用 0，保存时会给出明确校验提示
      }
    }
    setForm({
      name: '',
      local_path: '',
      disk_id: available[0]?.id ?? 0,
      remote_path: '',
      auto_sync: true,
      sync_interval: 300,
      enabled: true,
    });
    setShowForm(true);
  };

  const openEdit = (config: SyncConfig) => {
    setEditing(config);
    setForm({
      name: config.name,
      local_path: config.local_path,
      disk_id: config.disk_id,
      remote_path: config.remote_path,
      auto_sync: config.auto_sync,
      sync_interval: config.sync_interval,
      enabled: config.enabled,
    });
    setShowForm(true);
  };

  const submit = async () => {
    if (!form.name.trim() || !form.local_path.trim() || !form.disk_id) {
      toast.error('请填写别名、本地目录并选择目标磁盘');
      return;
    }
    try {
      if (editing) {
        await syncService.update(editing.id, form);
      } else {
        await syncService.create(form);
      }
      await syncService.reload();
      setShowForm(false);
      toast.success(editing ? '规则已更新' : '规则已创建');
      await reload();
    } catch (e) {
      toast.error('保存失败', e instanceof Error ? e.message : undefined);
    }
  };

  const browseLocal = async () => {
    const path = await fileService.pickDirectory('选择要同步的本地目录');
    if (path) setForm((f) => ({ ...f, local_path: path }));
  };

  const runNow = async (config: SyncConfig) => {
    try {
      const started = await syncService.run(config.id);
      if (!started) toast.error('无法开始同步', '该规则可能已在同步中');
    } catch (e) {
      toast.error('同步启动失败', e instanceof Error ? e.message : undefined);
    }
  };

  const toggleEnabled = async (config: SyncConfig) => {
    try {
      await syncService.update(config.id, {
        name: config.name,
        local_path: config.local_path,
        disk_id: config.disk_id,
        remote_path: config.remote_path,
        auto_sync: config.auto_sync,
        sync_interval: config.sync_interval,
        enabled: !config.enabled,
      });
      await syncService.reload();
      await reload();
    } catch (e) {
      toast.error('操作失败', e instanceof Error ? e.message : undefined);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await syncService.remove(deleteTarget.id);
      await syncService.reload();
      setDeleteTarget(null);
      toast.success('规则已删除');
      await reload();
    } catch (e) {
      toast.error('删除失败', e instanceof Error ? e.message : undefined);
    }
  };

  const selectedDiskName = disks.find((d) => d.id === form.disk_id)?.name ?? '';

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="同步管理"
        subtitle={`本地目录与私有云双向同步 · 共 ${configs.length} 条规则`}
        actions={
          <Button variant="primary" size="sm" icon="plus" onClick={() => void openCreate()}>
            新建规则
          </Button>
        }
      />

      {loading && <LoadingSpinner text="加载同步规则…" />}

      {!loading && error && (
        <div className="px-4 py-3">
          <div className="card flex items-center gap-2 text-[13px] text-danger-600">
            <Icon name="error" size="sm" />
            {error}
          </div>
        </div>
      )}

      {!loading && !error && configs.length === 0 && (
        <EmptyState
          icon="sync"
          title="还没有同步规则"
          description="创建一条规则，把本地目录与私有云磁盘做双向增量同步"
        />
      )}

      {!loading && !error && configs.length > 0 && (
        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
          {configs.map((config) => {
            const running = runningIds.includes(config.id);
            const diskName = disks.find((d) => d.id === config.disk_id)?.name ?? `磁盘 ${config.disk_id}`;
            return (
              <div key={config.id} className="card" data-sync-id={config.id}>
                <div className="flex items-start gap-3">
                  <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-50 text-brand-600">
                    <Icon name="sync" size="md" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[13.5px] font-semibold text-fg">
                        {config.name}
                      </span>
                      {config.enabled ? (
                        <Badge tone="success" icon="checked">
                          已启用
                        </Badge>
                      ) : (
                        <Badge tone="neutral">已停用</Badge>
                      )}
                      {config.auto_sync && (
                        <Badge tone="brand">每 {fmtInterval(config.sync_interval)}</Badge>
                      )}
                      {running && <Badge tone="warning">同步中</Badge>}
                    </div>
                    <div className="mt-1.5 space-y-0.5 text-[11.5px] text-subtle">
                      <div className="truncate">
                        本地：<span className="text-muted">{config.local_path}</span>
                      </div>
                      <div className="truncate">
                        远端：<span className="text-muted">{diskName}</span>
                        {config.remote_path ? ` / ${config.remote_path}` : ' / 根目录'}
                      </div>
                      <div>上次同步：{fmtTime(config.last_synced_at)}</div>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="sm"
                      icon="play"
                      disabled={running || !config.enabled}
                      onClick={() => void runNow(config)}
                    >
                      立即同步
                    </Button>
                    <Button size="sm" onClick={() => openEdit(config)}>
                      编辑
                    </Button>
                    <IconButton
                      icon={config.enabled ? 'stop' : 'play'}
                      label={config.enabled ? '停用' : '启用'}
                      size="sm"
                      onClick={() => void toggleEnabled(config)}
                    />
                    <IconButton
                      icon="delete"
                      label="删除规则"
                      size="sm"
                      onClick={() => setDeleteTarget(config)}
                    />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 新建 / 编辑 */}
      {showForm && (
        <Modal
          open
          title={editing ? '编辑同步规则' : '新建同步规则'}
          icon="sync"
          width={520}
          onClose={() => setShowForm(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setShowForm(false)}>
                取消
              </Button>
              <Button variant="primary" icon="checked" onClick={() => void submit()}>
                保存
              </Button>
            </>
          }
        >
          <div className="space-y-3">
            <div>
              <div className="mb-1.5 text-[12px] font-medium text-muted">别名</div>
              <input
                className="input"
                placeholder="例如：工作文档"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>

            <div>
              <div className="mb-1.5 text-[12px] font-medium text-muted">本地目录</div>
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

            <div>
              <div className="mb-1.5 text-[12px] font-medium text-muted">目标磁盘</div>
              {/* 不用原生 select（观感差且不可控），用与全站一致的自绘下拉 */}
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
              <div className="mb-1.5 text-[12px] font-medium text-muted">远端目录</div>
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

            <div className="flex items-center justify-between rounded-lg border border-line-subtle px-3 py-2.5">
              <div>
                <div className="text-[13px] text-fg">自动同步</div>
                <div className="text-[11.5px] text-subtle">按下方间隔在后台自动执行</div>
              </div>
              <Switch
                checked={form.auto_sync}
                onChange={(checked) => setForm((f) => ({ ...f, auto_sync: checked }))}
                label="自动同步"
              />
            </div>

            <div>
              <div className="mb-1.5 text-[12px] font-medium text-muted">
                同步间隔（秒，最小 30）
              </div>
              <input
                className="input"
                type="number"
                min={30}
                value={form.sync_interval}
                onChange={(e) =>
                  setForm((f) => ({ ...f, sync_interval: Number(e.target.value) || 300 }))
                }
              />
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
          message={`确定要删除同步规则「${deleteTarget.name}」吗？本地与云端文件都不会被删除。`}
          confirmLabel="删除"
          danger
          onConfirm={() => void confirmDelete()}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}

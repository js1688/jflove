import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { diskService } from '../../services/disk-service';
import { PageHeader } from '../../components/PageHeader';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { useIsPC } from '../../hooks/use-responsive';
import {
  Button,
  EmptyState,
  ErrorState,
  Icon,
  IconButton,
  ListSkeleton,
  Modal,
  toast,
  type IconName,
} from '../../components/ui';
import type { VirtualDisk } from '../../types/models';

/** 磁盘路径展示名（后端 real_path 优先，回退逻辑路径） */
function diskPathOf(disk: VirtualDisk): string {
  return disk.real_path || disk.path;
}

/**
 * 管理员 - 磁盘管理
 *
 * v1.5.0：emoji 与符号图标换矢量图标；表格与卡片全部改语义令牌；
 * 加载态换骨架屏、空态/错误态换统一组件、弹窗换统一 Modal、提示换 toast。
 * 业务逻辑（services 调用、字段、权限门禁）与 v1.4.2 完全一致。
 */
export function AdminDisksPage() {
  const navigate = useNavigate();
  const isPC = useIsPC();
  const [disks, setDisks] = useState<VirtualDisk[]>([]);
  const [loading, setLoading] = useState(true);
  // 加载失败原因（原先静默吞掉，界面显示空列表与真实"无数据"无法区分）
  const [loadError, setLoadError] = useState<string | null>(null);

  // 移动端三点菜单展开的磁盘 ID
  const [menuDiskId, setMenuDiskId] = useState<number | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createPath, setCreatePath] = useState('');

  const [editDisk, setEditDisk] = useState<VirtualDisk | null>(null);
  const [editName, setEditName] = useState('');
  const [editPath, setEditPath] = useState('');

  const [deleteDisk, setDeleteDisk] = useState<VirtualDisk | null>(null);

  const loadDisks = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setDisks(await diskService.listAllDisks());
    } catch (e) {
      // 原实现静默吞掉异常：保留"不弹全局错误"的行为，但把原因展示到错误态
      setDisks([]);
      setLoadError(e instanceof Error ? e.message : '加载磁盘列表失败');
    }
    setLoading(false);
  };

  useEffect(() => { loadDisks(); }, []);

  /** 统一的操作错误反馈：接口报错必须让用户看到（原先被静默吞掉） */
  const reportError = (e: unknown, fallback: string) => {
    toast.error(fallback, e instanceof Error ? e.message : String(e));
  };

  const handleCreate = async () => {
    if (!createName.trim() || !createPath.trim()) return;
    try {
      await diskService.createDisk(createName.trim(), createPath.trim());
    } catch (e) {
      reportError(e, '创建磁盘失败');
      return;
    }
    setShowCreate(false); setCreateName(''); setCreatePath('');
    toast.success('磁盘已创建');
    await loadDisks();
  };

  const handleEdit = async () => {
    if (!editDisk || !editName.trim() || !editPath.trim()) return;
    try {
      await diskService.updateDisk(editDisk.id, editName.trim(), editPath.trim());
    } catch (e) {
      reportError(e, '更新磁盘失败');
      return;
    }
    setEditDisk(null);
    toast.success('磁盘已更新');
    await loadDisks();
  };

  const handleDelete = async () => {
    if (!deleteDisk) return;
    try {
      await diskService.deleteDisk(deleteDisk.id);
    } catch (e) {
      reportError(e, '删除磁盘失败');
      return;
    }
    setDeleteDisk(null);
    toast.success('磁盘已删除');
    await loadDisks();
  };

  /** 打开移动端三点菜单 */
  const toggleMenu = (diskId: number) => {
    setMenuDiskId(menuDiskId === diskId ? null : diskId);
  };

  return (
    <div>
      <PageHeader
        title="磁盘管理"
        onBack={() => navigate(-1)}
        actions={
          <Button variant="primary" size="sm" icon="plus" onClick={() => setShowCreate(true)}>
            添加磁盘
          </Button>
        }
      />

      {loading && <ListSkeleton rows={4} />}

      {!loading && loadError && (
        <ErrorState message={loadError} onRetry={() => void loadDisks()} />
      )}

      {!loading && !loadError && (
        isPC ? (
          <div className="p-4">
            <div className="card overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-line bg-sunken text-left text-[11.5px] tracking-[0.04em] text-subtle uppercase">
                      <th className="px-4 py-2.5 font-semibold">ID</th>
                      <th className="px-4 py-2.5 font-semibold">名称</th>
                      <th className="px-4 py-2.5 font-semibold">路径</th>
                      <th className="px-4 py-2.5 font-semibold">创建时间</th>
                      <th className="px-4 py-2.5 font-semibold">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {disks.map(disk => (
                      <tr
                        key={disk.id}
                        className="border-b border-line-subtle transition-colors last:border-b-0 hover:bg-hover"
                      >
                        <td className="tabular px-4 py-3 text-muted">{disk.id}</td>
                        <td className="px-4 py-3 font-medium text-fg">{disk.name}</td>
                        <td className="max-w-[220px] truncate px-4 py-3 font-mono text-[11.5px] text-muted">
                          {diskPathOf(disk)}
                        </td>
                        <td className="tabular px-4 py-3 text-[11.5px] text-subtle">
                          {disk.created_at ? new Date(disk.created_at).toLocaleString('zh-CN') : '-'}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1">
                            <Button
                              size="sm"
                              variant="ghost"
                              icon="edit"
                              onClick={() => {
                                setEditDisk(disk);
                                setEditName(disk.name);
                                setEditPath(diskPathOf(disk));
                              }}
                            >
                              编辑
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              icon="delete"
                              className="btn-danger-ghost"
                              onClick={() => setDeleteDisk(disk)}
                            >
                              删除
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {disks.length === 0 && (
                      <tr>
                        <td colSpan={5} className="px-4 py-2">
                          <EmptyState
                            icon="disks"
                            title="暂无磁盘"
                            description="点击右上角「添加磁盘」创建第一个虚拟磁盘"
                          />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        ) : (
          /* 移动端对齐安卓 App：卡片 + ListTile（磁盘图标 + id.名称 + 路径 + 三点菜单） */
          <div className="space-y-2 p-3">
            {disks.map(disk => (
              <div
                key={disk.id}
                className="flex items-center gap-3 rounded-xl border border-line-subtle bg-surface p-3 shadow-e1"
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-brand-50 text-brand-500">
                  <Icon name="disks" size="lg" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-medium text-fg">
                    {disk.id}. {disk.name}
                  </div>
                  <div className="mt-0.5 truncate font-mono text-[11.5px] text-subtle">
                    {diskPathOf(disk)}
                  </div>
                </div>
                {/* 三点操作菜单 */}
                <div className="relative shrink-0">
                  <IconButton
                    icon="more"
                    label="操作菜单"
                    onClick={() => toggleMenu(disk.id)}
                    aria-haspopup="menu"
                    aria-expanded={menuDiskId === disk.id}
                  />
                  {menuDiskId === disk.id && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setMenuDiskId(null)} />
                      <div className="card absolute top-10 right-0 z-20 w-36 rounded-lg py-1" role="menu">
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            setMenuDiskId(null);
                            setEditDisk(disk);
                            setEditName(disk.name);
                            setEditPath(diskPathOf(disk));
                          }}
                          className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] text-fg transition-colors hover:bg-hover"
                        >
                          <Icon name="edit" size="sm" />
                          编辑
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => { setMenuDiskId(null); setDeleteDisk(disk); }}
                          className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] transition-colors hover:bg-hover"
                          style={{ color: 'var(--danger-700)' }}
                        >
                          <Icon name="delete" size="sm" />
                          删除
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            ))}
            {disks.length === 0 && (
              <EmptyState
                icon="disks"
                title="暂无磁盘"
                description="点击右上角「添加磁盘」创建第一个虚拟磁盘"
              />
            )}
          </div>
        )
      )}

      {/* 创建磁盘 */}
      {showCreate && (
        <DiskFormDialog
          title="创建磁盘"
          icon="plus"
          name={createName}
          onNameChange={setCreateName}
          path={createPath}
          onPathChange={setCreatePath}
          onConfirm={handleCreate}
          onCancel={() => setShowCreate(false)}
        />
      )}

      {/* 编辑磁盘 */}
      {editDisk && (
        <DiskFormDialog
          title="编辑磁盘"
          icon="edit"
          name={editName}
          onNameChange={setEditName}
          path={editPath}
          onPathChange={setEditPath}
          onConfirm={handleEdit}
          onCancel={() => setEditDisk(null)}
        />
      )}

      {/* 删除 */}
      {deleteDisk && (
        <ConfirmDialog
          title="确认删除"
          message={`确定要删除磁盘「${deleteDisk.name}」吗？磁盘内的所有文件将被删除。`}
          confirmLabel="删除"
          danger
          onConfirm={handleDelete}
          onCancel={() => setDeleteDisk(null)}
        />
      )}
    </div>
  );
}

/** 磁盘表单弹窗（创建 / 编辑共用，v1.5.0 改用统一 Modal） */
function DiskFormDialog({
  title, icon, name, onNameChange, path, onPathChange, onConfirm, onCancel,
}: {
  title: string; icon: IconName;
  name: string; onNameChange: (v: string) => void; path: string; onPathChange: (v: string) => void;
  onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <Modal
      open
      title={title}
      icon={icon}
      width={420}
      onClose={onCancel}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel}>
            取消
          </Button>
          <Button variant="primary" icon="checked" onClick={onConfirm}>
            确认
          </Button>
        </>
      }
    >
      <input
        type="text"
        placeholder="磁盘名称"
        value={name}
        onChange={e => onNameChange(e.target.value)}
        className="input mb-3"
      />
      <input
        type="text"
        placeholder="磁盘路径"
        value={path}
        onChange={e => onPathChange(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && onConfirm()}
        className="input"
      />
    </Modal>
  );
}

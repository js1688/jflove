import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { permissionService } from '../../services/permission-service';
import { userService } from '../../services/user-service';
import { diskService } from '../../services/disk-service';
import { PageHeader } from '../../components/PageHeader';
import {
  EmptyState,
  ErrorState,
  Icon,
  ListSkeleton,
  toast,
} from '../../components/ui';
import { useIsPC } from '../../hooks/use-responsive';
import type { DiskPermission, User, VirtualDisk } from '../../types/models';

/** 权限字段 → 中文名（读/写/删） */
const FIELD_LABEL: Record<'can_read' | 'can_write' | 'can_delete', string> = {
  can_read: '读',
  can_write: '写',
  can_delete: '删',
};

/**
 * 管理员 - 权限配置
 *
 * v1.5.0：emoji 用户图标、字符箭头与字符对勾换矢量图标；三栏（用户 / 磁盘 / 权限）
 * 布局与 QSplitter 等价结构保留；配色全部改语义令牌。
 * 权限计算与保存逻辑（三项全空即删除记录）与 v1.4.2 完全一致。
 */
export function AdminPermissionsPage() {
  const navigate = useNavigate();
  const isPC = useIsPC();
  const [users, setUsers] = useState<User[]>([]);
  const [disks, setDisks] = useState<VirtualDisk[]>([]);
  const [permissions, setPermissions] = useState<DiskPermission[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  // 移动端底部弹窗：当前配置权限的用户
  const [sheetUser, setSheetUser] = useState<User | null>(null);

  const loadData = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      // 用户列表 + 磁盘列表（权限按选中用户单独加载）
      const [u, d] = await Promise.all([
        userService.listUsers(),
        diskService.listAllDisks(),
      ]);
      setUsers(u.filter(x => x.role !== 'admin'));
      setDisks(d);
    } catch (e) {
      // 原实现静默吞掉异常：保留"不弹全局错误"的行为，但把原因展示到错误态
      setLoadError(e instanceof Error ? e.message : '加载用户与磁盘列表失败');
      setUsers([]);
      setDisks([]);
    }
    setLoading(false);
  };

  // 加载指定用户的磁盘权限
  const loadPermissions = async (userId: number) => {
    try {
      setPermissions(await permissionService.listPermissions(userId));
    } catch {
      setPermissions([]);
    }
  };

  useEffect(() => { loadData(); }, []);

  // 选中用户变化时重新加载该用户权限
  useEffect(() => {
    if (selectedUserId !== null) {
      loadPermissions(selectedUserId);
    }
  }, [selectedUserId]);

  // 已按用户加载，无需再按 user_id 过滤
  const selectedUserPerms = selectedUserId ? permissions : [];

  const handleTogglePermission = async (diskId: number, field: 'can_read' | 'can_write' | 'can_delete', current: boolean) => {
    if (!selectedUserId) return;
    const existing = selectedUserPerms.find(p => p.virtual_disk_id === diskId);
    // 计算切换后的三项权限
    const canRead = field === 'can_read' ? !current : (existing?.can_read ?? false);
    const canWrite = field === 'can_write' ? !current : (existing?.can_write ?? false);
    const canDelete = field === 'can_delete' ? !current : (existing?.can_delete ?? false);

    if (!canRead && !canWrite && !canDelete) {
      // 全部取消 → 删除权限记录（对标桌面端 permission_page._save_permissions）
      try {
        await permissionService.deletePermission(selectedUserId, diskId);
      } catch (e) {
        // 接口报错必须让用户看到（原先裸 await，异常被静默吞掉）
        toast.error('取消磁盘权限失败', e instanceof Error ? e.message : String(e));
        return;
      }
    } else {
      try {
        await permissionService.setPermission(selectedUserId, diskId, canRead, canWrite, canDelete);
      } catch (e) {
        toast.error('保存磁盘权限失败', e instanceof Error ? e.message : String(e));
        return;
      }
    }
    await loadPermissions(selectedUserId);
  };

  /** 渲染权限开关（读/写/删通用）：选中态用矢量对勾，未选中态为空心方框 */
  const renderToggle = (diskId: number, field: 'can_read' | 'can_write' | 'can_delete', checked: boolean) => (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={`${FIELD_LABEL[field]}${checked ? '权限（已允许，点击取消）' : '权限（未允许，点击授权）'}`}
      onClick={() => handleTogglePermission(diskId, field, checked)}
      data-on={checked}
      className="toggle-box"
    >
      {checked && <Icon name="checked" size="sm" strokeWidth={2.6} />}
    </button>
  );

  return (
    <div>
      <PageHeader title="权限配置" onBack={() => navigate(-1)} />

      {loading && <ListSkeleton rows={4} />}

      {!loading && loadError && (
        <ErrorState message={loadError} onRetry={() => void loadData()} />
      )}

      {!loading && !loadError && (
        isPC ? (
          <div className="flex flex-col divide-y divide-line-subtle lg:flex-row lg:divide-x lg:divide-y-0">
            {/* 左侧：用户列表 */}
            <div className="shrink-0 lg:w-64">
              <div className="px-4 py-2 text-[11.5px] font-semibold tracking-[0.04em] text-subtle uppercase">
                选择用户
              </div>
              {users.map(user => {
                const active = selectedUserId === user.id;
                return (
                  <button
                    key={user.id}
                    type="button"
                    onClick={() => setSelectedUserId(user.id)}
                    data-active={active}
                    className="nav-item rounded-none"
                  >
                    <Icon name="user" size="sm" className="shrink-0" />
                    <span className="truncate">{user.username}</span>
                  </button>
                );
              })}
              {users.length === 0 && (
                <div className="px-4 py-4 text-[11.5px] text-subtle">
                  暂无普通用户
                </div>
              )}
            </div>

            {/* 右侧：权限表格 */}
            <div className="min-w-0 flex-1">
              {selectedUserId ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-[13px]">
                    <thead>
                      <tr className="border-b border-line bg-sunken text-left text-[11.5px] tracking-[0.04em] text-subtle uppercase">
                        <th className="px-4 py-2.5 font-semibold">磁盘</th>
                        <th className="px-4 py-2.5 text-center font-semibold">读取</th>
                        <th className="px-4 py-2.5 text-center font-semibold">写入</th>
                        <th className="px-4 py-2.5 text-center font-semibold">删除</th>
                      </tr>
                    </thead>
                    <tbody>
                      {disks.map(disk => {
                        const perm = selectedUserPerms.find(p => p.virtual_disk_id === disk.id);
                        return (
                          <tr
                            key={disk.id}
                            className="border-b border-line-subtle transition-colors last:border-b-0 hover:bg-hover"
                          >
                            <td className="px-4 py-3 text-fg">{disk.name}</td>
                            <td className="px-4 py-3">
                              <div className="flex justify-center">
                                {renderToggle(disk.id, 'can_read', perm?.can_read ?? false)}
                              </div>
                            </td>
                            <td className="px-4 py-3">
                              <div className="flex justify-center">
                                {renderToggle(disk.id, 'can_write', perm?.can_write ?? false)}
                              </div>
                            </td>
                            <td className="px-4 py-3">
                              <div className="flex justify-center">
                                {renderToggle(disk.id, 'can_delete', perm?.can_delete ?? false)}
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                      {disks.length === 0 && (
                        <tr>
                          <td colSpan={4} className="px-4 py-2">
                            <EmptyState icon="disks" title="暂无磁盘" />
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState icon="user" title="请先选择左侧用户" description="选择用户后即可配置其磁盘权限" />
              )}
            </div>
          </div>
        ) : (
          /* 移动端对齐安卓 App：用户卡片列表（首字母头像 + 点击配置权限 + 右箭头） */
          <div className="space-y-2 p-3">
            {users.map(user => (
              <button
                key={user.id}
                type="button"
                onClick={() => { setSheetUser(user); setSelectedUserId(user.id); }}
                className="card card-hover flex w-full items-center gap-3 p-3 text-left"
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-brand-50 text-[13px] font-semibold text-brand-500">
                  {user.username.slice(0, 1).toUpperCase()}
                </span>
                <div className="min-w-0 flex-1 text-left">
                  <div className="truncate text-[13px] font-medium text-fg">{user.username}</div>
                  <div className="mt-0.5 text-[11.5px] text-subtle">点击配置磁盘权限</div>
                </div>
                <Icon name="next" size="sm" className="shrink-0 text-subtle" />
              </button>
            ))}
            {users.length === 0 && (
              <EmptyState icon="users" title="暂无普通用户" />
            )}
          </div>
        )
      )}

      {/* 移动端：权限配置底部弹窗（对齐安卓 App BottomSheet + CheckboxListTile） */}
      {sheetUser && !isPC && (
        <div
          className="fixed inset-0 z-50 flex items-end bg-overlay"
          onClick={() => setSheetUser(null)}
        >
          <div
            className="card max-h-[75vh] w-full overflow-y-auto rounded-t-2xl rounded-b-none p-4 pb-6"
            onClick={e => e.stopPropagation()}
          >
            <div className="mb-1 text-[13px] font-semibold text-fg">{sheetUser.username} 的磁盘权限</div>
            <div className="mb-2 text-[11.5px] text-subtle">切换即保存</div>
            {disks.map(disk => {
              const perm = selectedUserPerms.find(p => p.virtual_disk_id === disk.id);
              return (
                <div
                  key={disk.id}
                  className="flex items-center justify-between gap-3 border-b border-line-subtle py-2.5 last:border-b-0"
                >
                  <span className="min-w-0 flex-1 truncate text-[13px] text-fg">{disk.name}</span>
                  <div className="flex shrink-0 items-center gap-4">
                    {(['can_read', 'can_write', 'can_delete'] as const).map(field => (
                      <label key={field} className="flex flex-col items-center gap-0.5 text-[10.5px] text-subtle">
                        {renderToggle(disk.id, field, perm?.[field] ?? false)}
                        {FIELD_LABEL[field]}
                      </label>
                    ))}
                  </div>
                </div>
              );
            })}
            {disks.length === 0 && (
              <EmptyState icon="disks" title="暂无磁盘" />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

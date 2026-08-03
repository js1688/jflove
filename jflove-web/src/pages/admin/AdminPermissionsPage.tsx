import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { permissionService } from '../../services/permission-service';
import { userService } from '../../services/user-service';
import { diskService } from '../../services/disk-service';
import { PageHeader } from '../../components/PageHeader';
import { LoadingSpinner } from '../../components/LoadingSpinner';
import { useIsPC } from '../../hooks/use-responsive';
import type { DiskPermission, User, VirtualDisk } from '../../types/models';

/** 管理员 - 权限配置 */
export function AdminPermissionsPage() {
  const navigate = useNavigate();
  const isPC = useIsPC();
  const [users, setUsers] = useState<User[]>([]);
  const [disks, setDisks] = useState<VirtualDisk[]>([]);
  const [permissions, setPermissions] = useState<DiskPermission[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  // 移动端底部弹窗：当前配置权限的用户
  const [sheetUser, setSheetUser] = useState<User | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      // 用户列表 + 磁盘列表（权限按选中用户单独加载）
      const [u, d] = await Promise.all([
        userService.listUsers(),
        diskService.listAllDisks(),
      ]);
      setUsers(u.filter(x => x.role !== 'admin'));
      setDisks(d);
    } catch { /* 加载失败静默，界面显示空列表 */ }
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
      await permissionService.deletePermission(selectedUserId, diskId);
    } else {
      await permissionService.setPermission(selectedUserId, diskId, canRead, canWrite, canDelete);
    }
    await loadPermissions(selectedUserId);
  };

  /** 渲染权限开关（读/写/删通用） */
  const renderToggle = (diskId: number, field: 'can_read' | 'can_write' | 'can_delete', checked: boolean) => (
    <button
      onClick={() => handleTogglePermission(diskId, field, checked)}
      className={`w-6 h-6 rounded border-2 ${checked ? 'bg-green-500 border-green-500' : 'border-gray-300'}`}
    >
      {checked && <span className="text-white text-xs">✓</span>}
    </button>
  );

  return (
    <div>
      <PageHeader title="权限配置" onBack={() => navigate(-1)} />

      {loading && <LoadingSpinner />}

      {!loading && (
        isPC ? (
          <div className="flex flex-col lg:flex-row divide-y lg:divide-y-0 lg:divide-x divide-gray-100">
            {/* 左侧：用户列表 */}
            <div className="lg:w-64 flex-shrink-0">
              <div className="px-4 py-2 text-xs text-gray-400 uppercase font-medium">选择用户</div>
              {users.map(user => (
                <button
                  key={user.id}
                  onClick={() => setSelectedUserId(user.id)}
                  className={`w-full flex items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-gray-50 transition-colors ${
                    selectedUserId === user.id ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-600'
                  }`}
                >
                  👤 {user.username}
                </button>
              ))}
              {users.length === 0 && (
                <div className="px-4 py-4 text-xs text-gray-400">暂无普通用户</div>
              )}
            </div>

            {/* 右侧：权限表格 */}
            <div className="flex-1">
              {selectedUserId ? (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-100 text-left text-xs text-gray-400 uppercase">
                      <th className="px-4 py-3 font-medium">磁盘</th>
                      <th className="px-4 py-3 font-medium text-center">读取</th>
                      <th className="px-4 py-3 font-medium text-center">写入</th>
                      <th className="px-4 py-3 font-medium text-center">删除</th>
                    </tr>
                  </thead>
                  <tbody>
                    {disks.map(disk => {
                      const perm = selectedUserPerms.find(p => p.virtual_disk_id === disk.id);
                      return (
                        <tr key={disk.id} className="border-b border-gray-50 hover:bg-gray-50">
                          <td className="px-4 py-3 text-gray-800">{disk.name}</td>
                          <td className="px-4 py-3 text-center">
                            {renderToggle(disk.id, 'can_read', perm?.can_read ?? false)}
                          </td>
                          <td className="px-4 py-3 text-center">
                            {renderToggle(disk.id, 'can_write', perm?.can_write ?? false)}
                          </td>
                          <td className="px-4 py-3 text-center">
                            {renderToggle(disk.id, 'can_delete', perm?.can_delete ?? false)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <div className="px-4 py-12 text-center text-gray-400 text-sm">
                  ← 请先选择左侧用户
                </div>
              )}
            </div>
          </div>
        ) : (
          /* 移动端对齐安卓 App：用户卡片列表（首字母头像 + 点击配置权限 + 右箭头） */
          <div className="p-3 space-y-2">
            {users.map(user => (
              <button
                key={user.id}
                onClick={() => { setSheetUser(user); setSelectedUserId(user.id); }}
                className="w-full bg-white rounded-xl border border-gray-100 p-3 flex items-center gap-3"
              >
                <div className="w-10 h-10 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center font-semibold text-sm shrink-0">
                  {user.username.slice(0, 1).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0 text-left">
                  <div className="font-medium text-gray-800 text-sm truncate">{user.username}</div>
                  <div className="text-xs text-gray-400 mt-0.5">点击配置磁盘权限</div>
                </div>
                <span className="text-gray-300 shrink-0">→</span>
              </button>
            ))}
            {users.length === 0 && (
              <div className="py-8 text-center text-gray-400 text-sm">暂无普通用户</div>
            )}
          </div>
        )
      )}

      {/* 移动端：权限配置底部弹窗（对齐安卓 App BottomSheet + CheckboxListTile） */}
      {sheetUser && !isPC && (
        <div
          className="fixed inset-0 z-50 flex items-end bg-black/30"
          onClick={() => setSheetUser(null)}
        >
          <div
            className="w-full bg-white rounded-t-2xl p-4 pb-6 max-h-[75vh] overflow-y-auto"
            onClick={e => e.stopPropagation()}
          >
            <div className="text-sm font-semibold text-gray-800 mb-1">{sheetUser.username} 的磁盘权限</div>
            <div className="text-xs text-gray-400 mb-2">切换即保存</div>
            {disks.map(disk => {
              const perm = selectedUserPerms.find(p => p.virtual_disk_id === disk.id);
              return (
                <div key={disk.id} className="flex items-center justify-between py-2.5 border-b border-gray-50 gap-3">
                  <span className="text-sm text-gray-700 truncate flex-1">{disk.name}</span>
                  <div className="flex items-center gap-4 shrink-0">
                    {(['can_read', 'can_write', 'can_delete'] as const).map(field => (
                      <label key={field} className="flex flex-col items-center gap-0.5 text-[10px] text-gray-400">
                        {renderToggle(disk.id, field, perm?.[field] ?? false)}
                        {field === 'can_read' ? '读' : field === 'can_write' ? '写' : '删'}
                      </label>
                    ))}
                  </div>
                </div>
              );
            })}
            {disks.length === 0 && (
              <div className="py-6 text-center text-gray-400 text-sm">暂无磁盘</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

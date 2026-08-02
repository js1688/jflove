import { useEffect, useState } from 'react';
import { permissionService } from '../../services/permission-service';
import { userService } from '../../services/user-service';
import { diskService } from '../../services/disk-service';
import { PageHeader } from '../../components/PageHeader';
import { LoadingSpinner } from '../../components/LoadingSpinner';
import type { DiskPermission, User, VirtualDisk } from '../../types/models';

/** 管理员 - 权限配置 */
export function AdminPermissionsPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [disks, setDisks] = useState<VirtualDisk[]>([]);
  const [permissions, setPermissions] = useState<DiskPermission[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      const [u, d, p] = await Promise.all([
        userService.listUsers(),
        diskService.listAllDisks(),
        permissionService.listPermissions(),
      ]);
      setUsers(u.filter(x => x.role !== 'admin'));
      setDisks(d);
      setPermissions(p);
    } catch { /* 加载失败静默，界面显示空列表 */ }
    setLoading(false);
  };

  useEffect(() => { loadData(); }, []);

  const selectedUserPerms = permissions.filter(p => p.user_id === selectedUserId);

  const handleTogglePermission = async (diskId: number, field: 'can_read' | 'can_write', current: boolean) => {
    if (!selectedUserId) return;
    const existing = selectedUserPerms.find(p => p.virtual_disk_id === diskId);
    await permissionService.setPermission(
      selectedUserId, diskId,
      field === 'can_read' ? !current : (existing?.can_read ?? false),
      field === 'can_write' ? !current : (existing?.can_write ?? false),
    );
    await loadData();
  };

  return (
    <div>
      <PageHeader title="权限配置" />

      {loading && <LoadingSpinner />}

      {!loading && (
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
                  </tr>
                </thead>
                <tbody>
                  {disks.map(disk => {
                    const perm = selectedUserPerms.find(p => p.virtual_disk_id === disk.id);
                    return (
                      <tr key={disk.id} className="border-b border-gray-50 hover:bg-gray-50">
                        <td className="px-4 py-3 text-gray-800">{disk.name}</td>
                        <td className="px-4 py-3 text-center">
                          <button
                            onClick={() => handleTogglePermission(disk.id, 'can_read', perm?.can_read ?? false)}
                            className={`w-6 h-6 rounded border-2 ${
                              perm?.can_read
                                ? 'bg-green-500 border-green-500'
                                : 'border-gray-300'
                            }`}
                          >
                            {perm?.can_read && <span className="text-white text-xs">✓</span>}
                          </button>
                        </td>
                        <td className="px-4 py-3 text-center">
                          <button
                            onClick={() => handleTogglePermission(disk.id, 'can_write', perm?.can_write ?? false)}
                            className={`w-6 h-6 rounded border-2 ${
                              perm?.can_write
                                ? 'bg-green-500 border-green-500'
                                : 'border-gray-300'
                            }`}
                          >
                            {perm?.can_write && <span className="text-white text-xs">✓</span>}
                          </button>
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
      )}
    </div>
  );
}

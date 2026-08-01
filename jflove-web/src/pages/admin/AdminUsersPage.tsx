import { useEffect, useState } from 'react';
import { userService } from '../../services/user-service';
import { PageHeader } from '../../components/PageHeader';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { LoadingSpinner } from '../../components/LoadingSpinner';
import type { User } from '../../types/models';

/** 管理员 - 用户管理 */
export function AdminUsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  // 对话框状态
  const [showCreate, setShowCreate] = useState(false);
  const [createUsername, setCreateUsername] = useState('');
  const [createPassword, setCreatePassword] = useState('');

  const [changePwUser, setChangePwUser] = useState<User | null>(null);
  const [newPassword, setNewPassword] = useState('');

  const [deleteUser, setDeleteUser] = useState<User | null>(null);

  const loadUsers = async () => {
    setLoading(true);
    try {
      setUsers(await userService.listUsers());
    } catch { /* ignore */ }
    setLoading(false);
  };

  useEffect(() => { loadUsers(); }, []);

  const handleCreate = async () => {
    if (!createUsername.trim() || !createPassword) return;
    await userService.createUser(createUsername.trim(), createPassword);
    setShowCreate(false);
    setCreateUsername('');
    setCreatePassword('');
    await loadUsers();
  };

  const handleChangePw = async () => {
    if (!changePwUser || !newPassword) return;
    await userService.changePassword(changePwUser.id, newPassword);
    setChangePwUser(null);
    setNewPassword('');
  };

  const handleDelete = async () => {
    if (!deleteUser) return;
    await userService.deleteUser(deleteUser.id);
    setDeleteUser(null);
    await loadUsers();
  };

  const handleToggleEnabled = async (user: User) => {
    await userService.setEnabled(user.id, !user.enabled);
    await loadUsers();
  };

  return (
    <div>
      <PageHeader
        title="用户管理"
        actions={
          <button
            onClick={() => setShowCreate(true)}
            className="px-3 py-1.5 text-xs bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
          >
            ＋ 添加用户
          </button>
        }
      />

      {loading && <LoadingSpinner />}

      {!loading && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-left text-xs text-gray-400 uppercase">
                <th className="px-4 py-3 font-medium">ID</th>
                <th className="px-4 py-3 font-medium">用户名</th>
                <th className="px-4 py-3 font-medium">角色</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">创建时间</th>
                <th className="px-4 py-3 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.filter(u => u.role !== 'admin').map(user => (
                <tr key={user.id} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="px-4 py-3 text-gray-500">{user.id}</td>
                  <td className="px-4 py-3 font-medium text-gray-800">{user.username}</td>
                  <td className="px-4 py-3 text-gray-500">{user.role}</td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => handleToggleEnabled(user)}
                      className={`px-2 py-0.5 text-xs rounded-full ${
                        user.enabled
                          ? 'bg-green-100 text-green-700'
                          : 'bg-gray-100 text-gray-400'
                      }`}
                    >
                      {user.enabled ? '启用' : '禁用'}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-gray-400 text-xs">
                    {user.created_at ? new Date(user.created_at).toLocaleString('zh-CN') : '-'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1">
                      <button
                        onClick={() => { setChangePwUser(user); setNewPassword(''); }}
                        className="px-2 py-1 text-xs text-indigo-600 hover:bg-indigo-50 rounded"
                      >
                        改密
                      </button>
                      <button
                        onClick={() => setDeleteUser(user)}
                        className="px-2 py-1 text-xs text-red-500 hover:bg-red-50 rounded"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {users.filter(u => u.role !== 'admin').length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                    暂无普通用户
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* 创建用户 */}
      {showCreate && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h3 className="font-semibold text-gray-800 mb-4">创建用户</h3>
            <input
              type="text" placeholder="用户名" value={createUsername}
              onChange={e => setCreateUsername(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <input
              type="password" placeholder="密码" value={createPassword}
              onChange={e => setCreatePassword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowCreate(false)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">取消</button>
              <button onClick={handleCreate} className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">创建</button>
            </div>
          </div>
        </div>
      )}

      {/* 修改密码 */}
      {changePwUser && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h3 className="font-semibold text-gray-800 mb-4">修改密码 - {changePwUser.username}</h3>
            <input
              type="password" placeholder="新密码" value={newPassword}
              onChange={e => setNewPassword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleChangePw()}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setChangePwUser(null)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">取消</button>
              <button onClick={handleChangePw} className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">确认</button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认 */}
      {deleteUser && (
        <ConfirmDialog
          title="确认删除"
          message={`确定要删除用户「${deleteUser.username}」吗？`}
          confirmLabel="删除" danger
          onConfirm={handleDelete}
          onCancel={() => setDeleteUser(null)}
        />
      )}
    </div>
  );
}

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { userService } from '../../services/user-service';
import { PageHeader } from '../../components/PageHeader';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { LoadingSpinner } from '../../components/LoadingSpinner';
import { useIsPC } from '../../hooks/use-responsive';
import type { User } from '../../types/models';

/** 管理员 - 用户管理 */
export function AdminUsersPage() {
  const navigate = useNavigate();
  const isPC = useIsPC();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // 移动端三点菜单展开的用户 ID
  const [menuUserId, setMenuUserId] = useState<number | null>(null);

  // 对话框状态
  const [showCreate, setShowCreate] = useState(false);
  const [createUsername, setCreateUsername] = useState('');
  const [createPassword, setCreatePassword] = useState('');

  const [changePwUser, setChangePwUser] = useState<User | null>(null);
  const [newPassword, setNewPassword] = useState('');

  const [deleteUser, setDeleteUser] = useState<User | null>(null);

  const loadUsers = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setUsers(await userService.listUsers());
    } catch (e) {
      // 区分"请求失败"与"确实无数据"，避免误导（Bug#10）
      setLoadError(e instanceof Error ? e.message : '加载用户列表失败');
      setUsers([]);
    }
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
        onBack={() => navigate(-1)}
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

      {!loading && loadError && (
        <div className="mx-4 mt-2 px-4 py-3 text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg">
          加载用户列表失败：{loadError}
        </div>
      )}

      {!loading && !loadError && (
        isPC ? (
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
                {/* 设计意图：隐藏管理员行，防止管理员在 UI 上修改/删除其他管理员 */}
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
        ) : (
          /* 移动端对齐安卓 App：卡片 + ListTile（首字母头像 + 用户名 + 状态 + 三点菜单） */
          <div className="p-3 space-y-2">
            {users.filter(u => u.role !== 'admin').map(user => (
              <div key={user.id} className="bg-white rounded-xl border border-gray-100 p-3 flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center font-semibold text-sm shrink-0">
                  {user.username.slice(0, 1).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-gray-800 text-sm truncate">{user.username}</div>
                  <div className="text-xs text-gray-400 mt-0.5">
                    普通用户
                    <span className={`ml-1.5 inline-block px-1.5 py-0.5 rounded-full text-[10px] ${
                      user.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-400'
                    }`}>
                      {user.enabled ? '启用' : '禁用'}
                    </span>
                  </div>
                </div>
                {/* 三点操作菜单 */}
                <div className="relative shrink-0">
                  <button
                    onClick={() => setMenuUserId(menuUserId === user.id ? null : user.id)}
                    className="w-8 h-8 flex items-center justify-center text-gray-400 hover:text-gray-600 rounded-full hover:bg-gray-50"
                    aria-label="操作菜单"
                  >
                    ⋮
                  </button>
                  {menuUserId === user.id && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setMenuUserId(null)} />
                      <div className="absolute right-0 top-9 z-20 w-36 bg-white rounded-lg shadow-lg border border-gray-100 py-1">
                        <button
                          onClick={() => { setMenuUserId(null); setChangePwUser(user); setNewPassword(''); }}
                          className="w-full px-3 py-2 text-left text-sm text-gray-600 hover:bg-gray-50"
                        >
                          修改密码
                        </button>
                        <button
                          onClick={() => { setMenuUserId(null); handleToggleEnabled(user); }}
                          className="w-full px-3 py-2 text-left text-sm text-gray-600 hover:bg-gray-50"
                        >
                          {user.enabled ? '禁用账号' : '启用账号'}
                        </button>
                        <button
                          onClick={() => { setMenuUserId(null); setDeleteUser(user); }}
                          className="w-full px-3 py-2 text-left text-sm text-red-500 hover:bg-red-50"
                        >
                          删除
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            ))}
            {users.filter(u => u.role !== 'admin').length === 0 && (
              <div className="py-8 text-center text-gray-400 text-sm">暂无普通用户</div>
            )}
          </div>
        )
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

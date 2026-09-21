import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { userService } from '../../services/user-service';
import { PageHeader } from '../../components/PageHeader';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { useIsPC } from '../../hooks/use-responsive';
import {
  Badge,
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
import type { User } from '../../types/models';

/**
 * 管理员 - 用户管理
 *
 * v1.5.0：emoji 与符号图标、手写弹窗/提示条换统一组件（Modal / Badge / ErrorState / toast）；
 * 表格与卡片全部改语义令牌，暗色零改动。
 * 业务逻辑（含「列表隐藏管理员行」的设计意图、启用/禁用、改密、删除）与 v1.4.2 一致。
 */
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

  /**
   * 统一的操作错误反馈（v1.5.0 反馈修复）。
   *
   * 起因：这些 handler 原本是**裸 await**，接口报错时异常被吞掉 ——
   * 弹窗照样关闭、界面毫无反应。用户反馈「web 端隐藏了接口的响应信息，
   * 添加相同用户应该报错但 web 端没有提示」（安卓端有）。
   *
   * 服务端的错误 detail 经加密信封返回，http-client 解密后抛 ApiError，
   * 这里把它原样展示给用户，不再静默。
   */
  const reportError = (e: unknown, fallback: string) => {
    toast.error(fallback, e instanceof Error ? e.message : String(e));
  };

  const handleCreate = async () => {
    if (!createUsername.trim() || !createPassword) return;
    try {
      await userService.createUser(createUsername.trim(), createPassword);
    } catch (e) {
      // 失败时**保持弹窗打开**并保留已填内容，方便改名重试
      reportError(e, '添加用户失败');
      return;
    }
    setShowCreate(false);
    setCreateUsername('');
    setCreatePassword('');
    toast.success('用户已创建');
    await loadUsers();
  };

  const handleChangePw = async () => {
    if (!changePwUser || !newPassword) return;
    try {
      await userService.changePassword(changePwUser.id, newPassword);
    } catch (e) {
      reportError(e, '修改密码失败');
      return;
    }
    setChangePwUser(null);
    setNewPassword('');
    toast.success('密码已修改');
  };

  const handleDelete = async () => {
    if (!deleteUser) return;
    try {
      await userService.deleteUser(deleteUser.id);
    } catch (e) {
      reportError(e, '删除用户失败');
      return;
    }
    setDeleteUser(null);
    toast.success('用户已删除');
    await loadUsers();
  };

  const handleToggleEnabled = async (user: User) => {
    try {
      await userService.setEnabled(user.id, !user.enabled);
    } catch (e) {
      reportError(e, user.enabled ? '禁用用户失败' : '启用用户失败');
      return;
    }
    await loadUsers();
  };

  /** 普通用户列表（设计意图：隐藏管理员行，防止管理员在 UI 上修改/删除其他管理员） */
  const normalUsers = users.filter(u => u.role !== 'admin');

  return (
    <div>
      <PageHeader
        title="用户管理"
        onBack={() => navigate(-1)}
        actions={
          <Button variant="primary" size="sm" icon="userAdd" onClick={() => setShowCreate(true)}>
            添加用户
          </Button>
        }
      />

      {loading && <ListSkeleton rows={4} />}

      {!loading && loadError && (
        <ErrorState message={`加载用户列表失败：${loadError}`} onRetry={() => void loadUsers()} />
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
                      <th className="px-4 py-2.5 font-semibold">用户名</th>
                      <th className="px-4 py-2.5 font-semibold">角色</th>
                      <th className="px-4 py-2.5 font-semibold">状态</th>
                      <th className="px-4 py-2.5 font-semibold">创建时间</th>
                      <th className="px-4 py-2.5 font-semibold">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {normalUsers.map(user => (
                      <tr
                        key={user.id}
                        className="border-b border-line-subtle transition-colors last:border-b-0 hover:bg-hover"
                      >
                        <td className="tabular px-4 py-3 text-muted">{user.id}</td>
                        <td className="px-4 py-3 font-medium text-fg">{user.username}</td>
                        <td className="px-4 py-3 text-muted">{user.role}</td>
                        <td className="px-4 py-3">
                          {/* 启用状态徽标即开关：点击切换启用/禁用 */}
                          <button
                            type="button"
                            onClick={() => handleToggleEnabled(user)}
                            className="cursor-pointer"
                            title={user.enabled ? '点击禁用该账号' : '点击启用该账号'}
                          >
                            <Badge
                              tone={user.enabled ? 'success' : 'neutral'}
                              icon={user.enabled ? 'checked' : 'close'}
                            >
                              {user.enabled ? '启用' : '禁用'}
                            </Badge>
                          </button>
                        </td>
                        <td className="tabular px-4 py-3 text-[11.5px] text-subtle">
                          {user.created_at ? new Date(user.created_at).toLocaleString('zh-CN') : '-'}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1">
                            <Button
                              size="sm"
                              variant="ghost"
                              icon="permissions"
                              onClick={() => { setChangePwUser(user); setNewPassword(''); }}
                            >
                              改密
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              icon="delete"
                              className="btn-danger-ghost"
                              onClick={() => setDeleteUser(user)}
                            >
                              删除
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {normalUsers.length === 0 && (
                      <tr>
                        <td colSpan={6} className="px-4 py-2">
                          <EmptyState
                            icon="users"
                            title="暂无普通用户"
                            description="点击右上角「添加用户」创建第一个普通用户"
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
          /* 移动端对齐安卓 App：卡片 + ListTile（首字母头像 + 用户名 + 状态 + 三点菜单） */
          <div className="space-y-2 p-3">
            {normalUsers.map(user => (
              <div
                key={user.id}
                className="flex items-center gap-3 rounded-xl border border-line-subtle bg-surface p-3 shadow-e1"
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-brand-50 text-[13px] font-semibold text-brand-500">
                  {user.username.slice(0, 1).toUpperCase()}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-medium text-fg">{user.username}</div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-subtle">
                    普通用户
                    <Badge
                      tone={user.enabled ? 'success' : 'neutral'}
                      icon={user.enabled ? 'checked' : 'close'}
                      className="!h-[18px] !text-[10.5px]"
                    >
                      {user.enabled ? '启用' : '禁用'}
                    </Badge>
                  </div>
                </div>
                {/* 三点操作菜单 */}
                <div className="relative shrink-0">
                  <IconButton
                    icon="more"
                    label="操作菜单"
                    onClick={() => setMenuUserId(menuUserId === user.id ? null : user.id)}
                    aria-haspopup="menu"
                    aria-expanded={menuUserId === user.id}
                  />
                  {menuUserId === user.id && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setMenuUserId(null)} />
                      <div className="card absolute top-10 right-0 z-20 w-36 rounded-lg py-1" role="menu">
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => { setMenuUserId(null); setChangePwUser(user); setNewPassword(''); }}
                          className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] text-fg transition-colors hover:bg-hover"
                        >
                          <Icon name="permissions" size="sm" />
                          修改密码
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => { setMenuUserId(null); void handleToggleEnabled(user); }}
                          className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] text-fg transition-colors hover:bg-hover"
                        >
                          <Icon name={user.enabled ? 'close' : 'checked'} size="sm" />
                          {user.enabled ? '禁用账号' : '启用账号'}
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => { setMenuUserId(null); setDeleteUser(user); }}
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
            {normalUsers.length === 0 && (
              <EmptyState
                icon="users"
                title="暂无普通用户"
                description="点击右上角「添加用户」创建第一个普通用户"
              />
            )}
          </div>
        )
      )}

      {/* 创建用户 */}
      {showCreate && (
        <UserFormDialog
          title="创建用户"
          icon="userAdd"
          onClose={() => setShowCreate(false)}
          onConfirm={handleCreate}
          confirmLabel="创建"
        >
          <input
            type="text"
            placeholder="用户名"
            value={createUsername}
            onChange={e => setCreateUsername(e.target.value)}
            className="input mb-3"
          />
          <input
            type="password"
            placeholder="密码"
            value={createPassword}
            onChange={e => setCreatePassword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleCreate()}
            className="input"
          />
        </UserFormDialog>
      )}

      {/* 修改密码 */}
      {changePwUser && (
        <UserFormDialog
          title={`修改密码 - ${changePwUser.username}`}
          icon="permissions"
          onClose={() => setChangePwUser(null)}
          onConfirm={handleChangePw}
          confirmLabel="确认"
        >
          <input
            type="password"
            placeholder="新密码"
            value={newPassword}
            onChange={e => setNewPassword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleChangePw()}
            className="input"
          />
        </UserFormDialog>
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

/** 用户表单弹窗（创建用户 / 修改密码共用，v1.5.0 改用统一 Modal） */
function UserFormDialog({
  title, icon, onClose, onConfirm, confirmLabel, children,
}: {
  title: string; icon: IconName;
  onClose: () => void; onConfirm: () => void; confirmLabel: string;
  children: React.ReactNode;
}) {
  return (
    <Modal
      open
      title={title}
      icon={icon}
      width={420}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" icon="checked" onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Modal>
  );
}

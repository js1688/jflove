/**
 * 新建 / 编辑远程连接（右侧抽屉）
 *
 * 凭据语义：编辑时输入框留空 = 不修改；只有用户重新输入才会覆盖。
 * 渲染层永远拿不到已保存的密码/私钥原文（服务端只回 `has_*` 与指纹）。
 */
import { useState } from 'react';
import type { ReactNode } from 'react';
import { Button } from '../../components/ui';
import type {
  AuthType,
  RemoteConnectionDetail,
  RemoteConnectionInput,
  RemoteGroup,
} from '../../services/remote-service';

interface FormProps {
  mode: 'create' | 'edit';
  detail?: RemoteConnectionDetail;
  groups: RemoteGroup[];
  connections: Array<{ id: number; name: string }>;
  onCancel: () => void;
  onSave: (payload: RemoteConnectionInput) => Promise<number | null>;
}

const AUTH_OPTIONS: Array<{ value: AuthType; label: string }> = [
  { value: 'password', label: '密码' },
  { value: 'publickey', label: '密钥' },
  { value: 'password_keyboard', label: '密码 + OTP' },
];

export function RemoteConnectionForm({
  mode,
  detail,
  groups,
  connections,
  onCancel,
  onSave,
}: FormProps) {
  const [name, setName] = useState(detail?.name ?? '');
  const [host, setHost] = useState(detail?.host ?? '');
  const [port, setPort] = useState(String(detail?.port ?? 22));
  const [username, setUsername] = useState(detail?.username ?? '');
  const [authType, setAuthType] = useState<AuthType>(detail?.auth_type ?? 'password');
  const [password, setPassword] = useState('');
  const [privateKey, setPrivateKey] = useState('');
  const [passphrase, setPassphrase] = useState('');
  const [jumpId, setJumpId] = useState(String(detail?.jump_connection_id ?? 0));
  const [groupId, setGroupId] = useState(String(detail?.ssh_group_id ?? 0));
  const [initCommand, setInitCommand] = useState(detail?.init_command ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const needsPassword = authType === 'password' || authType === 'password_keyboard';
  const hasSavedPassword = Boolean(detail?.has_password);
  const hasSavedKey = Boolean(detail?.has_private_key);

  const submit = async () => {
    setError('');
    if (!name.trim()) return setError('名称不能为空');
    if (!host.trim()) return setError('主机不能为空');
    if (needsPassword && mode === 'create' && !password) return setError('该认证方式需要密码');
    if (authType === 'publickey' && mode === 'create' && !privateKey) {
      return setError('该认证方式需要私钥');
    }
    if (initCommand.includes('\n') || initCommand.includes('\r')) {
      return setError('连接后执行命令只能是单行');
    }
    const payload: RemoteConnectionInput = {
      name: name.trim(),
      host: host.trim(),
      port: Number(port) || 22,
      username,
      auth_type: authType,
      ssh_group_id: Number(groupId) || 0,
      jump_connection_id: Number(jumpId) || 0,
      terminal_type: detail?.terminal_type || 'xterm-256color',
      charset: detail?.charset || 'UTF-8',
      init_command: initCommand,
    };
    if (password) payload.password = password;
    if (privateKey) {
      payload.private_key = privateKey;
      payload.passphrase = passphrase;
    }
    setSaving(true);
    const saved = await onSave(payload);
    setSaving(false);
    if (saved === null) setError('保存失败，请检查服务端连接');
  };

  return (
    <aside className="flex w-[380px] shrink-0 flex-col border-l border-line bg-surface">
      <div className="flex h-[52px] shrink-0 items-center justify-between border-b border-line-subtle px-4">
        <span className="text-sm font-semibold">{mode === 'edit' ? '编辑连接' : '新建连接'}</span>
        <span className="text-xs text-subtle">{detail?.name ?? ''}</span>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 py-4">
        <Field label="名称">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <div className="flex gap-3">
          <Field label="主机" className="flex-1">
            <input className="input" value={host} onChange={(e) => setHost(e.target.value)} />
          </Field>
          <Field label="端口" className="w-24">
            <input className="input" value={port} onChange={(e) => setPort(e.target.value)} />
          </Field>
        </div>
        <Field label="用户名">
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>

        <Field label="认证方式">
          <div className="flex gap-1 rounded-md bg-sunken p-1">
            {AUTH_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setAuthType(option.value)}
                className={[
                  'flex-1 rounded px-2 py-1.5 text-xs',
                  authType === option.value
                    ? 'bg-surface font-semibold text-fg shadow-sm'
                    : 'text-muted',
                ].join(' ')}
              >
                {option.label}
              </button>
            ))}
          </div>
        </Field>

        {needsPassword && (
          <Field label="密码">
            <input
              className="input"
              type="password"
              value={password}
              placeholder={hasSavedPassword ? '已设置，留空不修改' : '输入密码'}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
        )}

        {authType === 'publickey' && (
          <>
            <Field label="私钥内容">
              <textarea
                className="input min-h-[88px] font-mono text-[11px]"
                value={privateKey}
                placeholder={
                  hasSavedKey ? '已设置，留空不修改' : '-----BEGIN OPENSSH PRIVATE KEY-----'
                }
                onChange={(e) => setPrivateKey(e.target.value)}
              />
            </Field>
            <Field label="私钥口令（可选）">
              <input
                className="input"
                type="password"
                value={passphrase}
                placeholder={hasSavedKey ? '已设置，留空不修改' : '未设置'}
                onChange={(e) => setPassphrase(e.target.value)}
              />
            </Field>
          </>
        )}

        <Field label="跳板连接">
          <select
            className="input"
            value={jumpId}
            onChange={(e) => setJumpId(e.target.value)}
          >
            <option value="0">直连</option>
            {connections
              .filter((item) => item.id !== detail?.id)
              .map((item) => (
                <option key={item.id} value={String(item.id)}>
                  {item.name}
                </option>
              ))}
          </select>
        </Field>

        <div className="flex gap-3">
          <Field label="分组" className="flex-1">
            <select
              className="input"
              value={groupId}
              onChange={(e) => setGroupId(e.target.value)}
            >
              <option value="0">未分组</option>
              {groups.map((group) => (
                <option key={group.id} value={String(group.id)}>
                  {group.name}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="连接后执行（可选）">
          <input
            className="input font-mono text-[11.5px]"
            value={initCommand}
            placeholder="cd /opt/app && ls"
            onChange={(e) => setInitCommand(e.target.value)}
          />
          <p className="mt-1 text-[11px] text-subtle">
            登录后自动回显并执行，结束仍停在 shell；仅允许单行
          </p>
        </Field>

        {error && <div className="rounded bg-danger-50 px-3 py-2 text-xs text-danger-700">{error}</div>}
      </div>

      <div className="flex justify-end gap-2 border-t border-line-subtle px-4 py-3">
        <Button variant="ghost" onClick={onCancel}>
          取消
        </Button>
        <Button variant="primary" loading={saving} onClick={() => void submit()}>
          保存
        </Button>
      </div>
    </aside>
  );
}

function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <div className="mb-1 text-[11.5px] font-semibold text-muted">{label}</div>
      {children}
    </div>
  );
}

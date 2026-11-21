/**
 * 新建 / 编辑远程连接（右侧抽屉）
 *
 * 凭据语义（v1.10.13）：
 * - 编辑时输入框留空 = **不修改**；
 * - 要真正删除已保存的凭据，点字段右上角「清空」——保存时发送
 *   `clear_password` / `clear_private_key` / `clear_passphrase`，由服务端把列写成空串；
 * - 渲染层永远拿不到已保存的密码/私钥原文（服务端只回 `has_*` 与指纹）。
 *
 * 「密码」与「私钥口令」不是一回事（SSH 里是两个不同的东西）：
 * - 私钥口令：解锁**私钥文件**的本机口令（只用于本地解密，不会发给服务器）；
 * - 密码：**服务器账号**登录密码（按 password 方法认证时提交给服务器）。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Button, Icon, toast } from '../../components/ui';
import { callBridge } from '../../utils/desktop-bridge';
import { describeErrorWithFallback } from '../../utils/error-text';
import type {
  AuthType,
  RemoteConnectionDetail,
  RemoteConnectionInput,
} from '../../services/remote-service';

interface ConnectionOption {
  id: number;
  name: string;
  /** 仅用于排除「把当前连接当成下游跳板」的循环 */
  jump_connection_id?: number;
}

interface FormProps {
  mode: 'create' | 'edit';
  /** 新建时预选连接类型（ssh / sftp） */
  connectionMode?: 'ssh' | 'sftp';
  detail?: RemoteConnectionDetail;
  connections: ConnectionOption[];
  onCancel: () => void;
  onSave: (payload: RemoteConnectionInput) => Promise<number | null>;
}

const AUTH_OPTIONS: Array<{ value: AuthType; label: string }> = [
  { value: 'password', label: '密码' },
  { value: 'publickey', label: '密钥' },
  { value: 'password_publickey', label: '密码 + 密钥' },
  { value: 'password_keyboard', label: '密码 + OTP' },
];

export function RemoteConnectionForm({
  mode,
  connectionMode,
  detail,
  connections,
  onCancel,
  onSave,
}: FormProps) {
  const [name, setName] = useState(detail?.name ?? '');
  const [host, setHost] = useState(detail?.host ?? '');
  const [port, setPort] = useState(String(detail?.port ?? 22));
  const [username, setUsername] = useState(detail?.username ?? '');
  const [authType, setAuthType] = useState<AuthType>(detail?.auth_type ?? 'password');
  const [connectionType, setConnectionType] = useState<'ssh' | 'sftp'>(
    detail?.mode ?? connectionMode ?? 'ssh',
  );
  const [password, setPassword] = useState('');
  const [privateKey, setPrivateKey] = useState('');
  const [passphrase, setPassphrase] = useState('');
  /** 选文件后由 Python 侧暂存的 token（私钥字节不进渲染层） */
  const [keyToken, setKeyToken] = useState('');
  const [keyFileName, setKeyFileName] = useState('');
  const [keyEncrypted, setKeyEncrypted] = useState(false);
  const [jumpId, setJumpId] = useState(String(detail?.jump_connection_id ?? 0));
  const [initCommand, setInitCommand] = useState(detail?.init_command ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  /**
   * 选文件失败的**就地**原因（v1.10.12 修复）。
   *
   * 抽屉比可视区高，底部 `error` 用户看不到 —— 选 `.ppk` 时观感就是「毫无反应」。
   * 这里在私钥字段内直接显示，并同时弹一次 toast。
   */
  const [keyError, setKeyError] = useState('');

  /** 已保存凭据的清空标记：只有「保存」才真正落库 */
  const [clearPassword, setClearPassword] = useState(false);
  const [clearPrivateKey, setClearPrivateKey] = useState(false);
  const [clearPassphrase, setClearPassphrase] = useState(false);

  const hasSavedPassword = Boolean(detail?.has_password);
  const hasSavedKey = Boolean(detail?.has_private_key);
  /**
   * 私钥口令是否真的存过（v1.10.16 修复）。
   *
   * 以前用 `hasSavedKey` 推断「口令已设置」，于是只要存过私钥，
   * 口令框就永远显示「已设置，留空不修改」、清空按钮永远可点 ——
   * 用户会以为库里有个自己没设过的口令（实际是空串）。这里只认服务端的
   * `has_passphrase`，与 `has_private_key` 解耦。
   */
  const hasSavedPassphrase = Boolean(detail?.has_passphrase);

  /**
   * 选择本机私钥文件。
   *
   * Python 侧读取文件内容并暂存，渲染层只拿 token / 文件名；
   * 保存连接时由桥把**内容**发给服务端落库（之后连接不再依赖本地文件）。
   */
  const pickKeyFile = async () => {
    setError('');
    setKeyError('');
    try {
      const picked = await callBridge<{
        token?: string;
        name?: string;
        encrypted?: boolean;
        cancelled?: boolean;
      }>('remote', 'pick_private_key');
      if (picked?.cancelled || !picked?.token) return;
      setKeyToken(picked.token);
      setKeyFileName(picked.name ?? '');
      setKeyEncrypted(Boolean(picked.encrypted));
      setPrivateKey('');
      setClearPrivateKey(false);
      setClearPassphrase(false);
    } catch (e) {
      const message = describeErrorWithFallback(e, '无法读取私钥文件');
      setKeyError(message);
      toast.error('私钥文件无法使用', message);
    }
  };

  const needsPassword =
    authType === 'password' || authType === 'password_keyboard' || authType === 'password_publickey';
  /** 密钥类认证：只填密钥 / 密码 + 密钥（双因素） */
  const needsKey = authType === 'publickey' || authType === 'password_publickey';

  /**
   * 「跳板连接」选项：按 id 去重 + 排除自己 + 排除会形成环的下游连接。
   *
   * v1.10.13：不再使用原生 `<select>`（Windows 多显示器混合 DPI 下弹层会持续变大）；
   * 这里只产数据，渲染交给 `JumpSelect`（页内列表）。
   */
  const jumpOptions = useMemo(() => {
    const currentId = Number(detail?.id ?? 0);
    const byId = new Map<number, ConnectionOption>();
    for (const item of connections) {
      if (!byId.has(item.id)) byId.set(item.id, item);
    }
    /** 候选连接是否（直接或间接）以当前连接为跳板 */
    const createsCycle = (candidateId: number): boolean => {
      if (!currentId) return false;
      const seen = new Set<number>();
      let cursor = candidateId;
      while (cursor) {
        if (cursor === currentId) return true;
        if (seen.has(cursor)) return true; // 历史脏数据里的环，直接排除
        seen.add(cursor);
        cursor = Number(byId.get(cursor)?.jump_connection_id ?? 0);
      }
      return false;
    };
    const options: Array<{ id: number; name: string }> = [{ id: 0, name: '直连' }];
    for (const item of connections) {
      if (item.id === currentId) continue;
      if (options.some((option) => option.id === item.id)) continue; // 防御重复条目
      if (createsCycle(item.id)) continue;
      options.push({ id: item.id, name: item.name });
    }
    // 已保存的跳板可能已被删除/不可见：保留一条占位，避免下拉显示「直连」却仍在提交旧 ID
    const savedJump = Number(detail?.jump_connection_id ?? 0);
    if (savedJump && !options.some((option) => option.id === savedJump)) {
      options.push({ id: savedJump, name: `跳板 #${savedJump}（已不可见）` });
    }
    return options;
  }, [connections, detail?.id, detail?.jump_connection_id]);

  const submit = async () => {
    setError('');
    if (!name.trim()) return setError('名称不能为空');
    if (!host.trim()) return setError('主机不能为空');
    if (needsKey && mode === 'create' && !privateKey && !keyToken) {
      return setError('该认证方式需要私钥（粘贴内容或选择文件）');
    }
    /**
     * 「密码 + 密钥」允许密码留空（与服务端同口径：`ssh_service.normalize` 在有私钥时放行）。
     *
     * 这类网关在保存时**根本拿不到账号密码**（标准图形客户端也是连上后才弹框），
     * 逼用户先填只会让他反复试错。判据必须是「有私钥」——
     * 新建看本次输入，编辑看库里已存的那把（`hasSavedKey`）。
     */
    const keyAvailable = privateKey.trim() !== '' || keyToken !== '' || hasSavedKey;
    const passwordOptional = authType === 'password_publickey' && keyAvailable;
    if (needsPassword && !passwordOptional && mode === 'create' && !password) {
      return setError('该认证方式需要密码');
    }
    if (initCommand.includes('\n') || initCommand.includes('\r')) {
      return setError('连接后执行命令只能是单行');
    }
    // 有新的私钥（粘贴或选文件）时先本地校验，避免保存成功、连接时才报错
    if (needsKey && (privateKey || keyToken)) {
      try {
        await callBridge('remote', 'validate_private_key', {
          private_key: privateKey,
          private_key_file_token: keyToken,
          passphrase,
        });
      } catch (e) {
        return setError(describeErrorWithFallback(e, '私钥校验失败'));
      }
    }
    const payload: RemoteConnectionInput = {
      name: name.trim(),
      host: host.trim(),
      port: Number(port) || 22,
      username,
      auth_type: authType,
      mode: connectionType,
      jump_connection_id: Number(jumpId) || 0,
      terminal_type: detail?.terminal_type || 'xterm-256color',
      charset: detail?.charset || 'UTF-8',
      init_command: initCommand,
    };
    if (password) payload.password = password;
    if (privateKey) {
      payload.private_key = privateKey;
      payload.passphrase = passphrase;
    } else if (keyToken) {
      // 桥按 token 取出文件内容填入 private_key，服务端最终存的是内容
      payload.private_key_file_token = keyToken;
      payload.passphrase = passphrase;
    } else if (clearPassphrase && !clearPrivateKey) {
      // 只清口令、不动私钥：服务端单独支持 clear_passphrase
      payload.clear_passphrase = true;
    }
    if (clearPassword) payload.clear_password = true;
    if (clearPrivateKey) payload.clear_private_key = true;
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
        <Field label="连接类型">
          <div className="flex gap-1 rounded-md bg-sunken p-1">
            <button
              type="button"
              onClick={() => setConnectionType('ssh')}
              className={[
                'flex-1 rounded px-2 py-1.5 text-xs',
                connectionType === 'ssh'
                  ? 'bg-surface font-semibold text-fg shadow-sm'
                  : 'text-muted',
              ].join(' ')}
            >
              远程终端（SSH）
            </button>
            <button
              type="button"
              onClick={() => setConnectionType('sftp')}
              className={[
                'flex-1 rounded px-2 py-1.5 text-xs',
                connectionType === 'sftp'
                  ? 'bg-surface font-semibold text-fg shadow-sm'
                  : 'text-muted',
              ].join(' ')}
            >
              仅 SFTP
            </button>
          </div>
          <p className="mt-1 text-[11px] text-subtle">
            仅 SFTP 不建立 shell，适合只开放 SFTP 的服务器
          </p>
        </Field>
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
          <div className="grid grid-cols-2 gap-1 rounded-md bg-sunken p-1">
            {AUTH_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setAuthType(option.value)}
                className={[
                  'rounded px-2 py-1.5 text-xs',
                  authType === option.value
                    ? 'bg-surface font-semibold text-fg shadow-sm'
                    : 'text-muted',
                ].join(' ')}
              >
                {option.label}
              </button>
            ))}
          </div>
          <p className="mt-1 text-[11px] leading-4 text-subtle">
            服务器先验密钥、再要求账号密码时选「密码 + 密钥」：私钥口令解锁私钥，密码是登录密码。
          </p>
        </Field>

        {needsPassword && (
          <Field
            label="密码"
            action={
              <ClearAction
                scheduled={clearPassword}
                disabled={mode === 'create' ? !password : !password && !hasSavedPassword}
                onClear={() => {
                  setPassword('');
                  if (mode === 'edit' && hasSavedPassword) setClearPassword(true);
                }}
                onUndo={() => setClearPassword(false)}
              />
            }
          >
            <input
              className="input"
              type="password"
              value={password}
              placeholder={
                clearPassword
                  ? '保存后清空'
                  : hasSavedPassword
                    ? '已设置，留空不修改'
                    : '输入密码'
              }
              onChange={(e) => {
                setPassword(e.target.value);
                setClearPassword(false);
              }}
            />
            {clearPassword && (
              <p className="mt-1 text-[11px] text-warning-700">已标记清空：保存后服务端密码列写为空</p>
            )}
            <p className="mt-1 text-[11px] leading-4 text-subtle">
              服务器账号登录密码。服务器要求继续认证（password 方法）时提交给服务器；
              留空则连接时弹框再输入。
            </p>
          </Field>
        )}

        {needsKey && (
          <>
            <Field
              label="私钥内容或文件路径"
              action={
                <ClearAction
                  scheduled={clearPrivateKey}
                  disabled={
                    mode === 'create'
                      ? !privateKey && !keyToken
                      : !privateKey && !keyToken && !hasSavedKey
                  }
                  onClear={() => {
                    setPrivateKey('');
                    setKeyToken('');
                    setKeyFileName('');
                    setKeyEncrypted(false);
                    setKeyError('');
                    setPassphrase('');
                    if (mode === 'edit' && hasSavedKey) {
                      setClearPrivateKey(true);
                      setClearPassphrase(true);
                    }
                  }}
                  onUndo={() => {
                    setClearPrivateKey(false);
                    setClearPassphrase(false);
                  }}
                />
              }
            >
              <textarea
                className="input min-h-[88px] font-mono text-[11px]"
                value={privateKey}
                placeholder={
                  clearPrivateKey
                    ? '保存后清空已保存的私钥'
                    : hasSavedKey
                      ? '已设置，留空不修改'
                      : '-----BEGIN OPENSSH PRIVATE KEY-----'
                }
                onChange={(e) => {
                  setPrivateKey(e.target.value);
                  setKeyToken('');
                  setKeyFileName('');
                  setKeyEncrypted(false);
                  setKeyError('');
                  setClearPrivateKey(false);
                  setClearPassphrase(false);
                }}
              />
              <div className="mt-1 flex items-center justify-between gap-2">
                <p className="text-[11px] leading-4 text-subtle">
                  粘贴 OpenSSH/PEM 文本，或点「选择文件…」（内容存服务端，不保存本地路径）；PuTTY .ppk 需先转换
                </p>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm shrink-0"
                  onClick={() => void pickKeyFile()}
                >
                  选择文件…
                </button>
              </div>
              {keyError && (
                <div className="mt-1 rounded bg-danger-50 px-2 py-1.5 text-[11px] leading-4 text-danger-700">
                  {keyError}
                </div>
              )}
              {keyToken && (
                <div className="mt-1 rounded bg-success-50 px-2 py-1.5 text-[11px] text-success-700">
                  已选择：{keyFileName}
                  {keyEncrypted ? '（已加密，请填写私钥口令）' : ''}
                  <span className="text-subtle"> · 保存时读取内容并上传服务端</span>
                </div>
              )}
              {clearPrivateKey && (
                <p className="mt-1 text-[11px] text-warning-700">
                  已标记清空：保存后服务端私钥与私钥口令都写为空
                </p>
              )}
            </Field>
            <Field
              label="私钥口令（可选）"
              action={
                <ClearAction
                  scheduled={clearPassphrase}
                  disabled={
                    mode === 'create' ? !passphrase : !passphrase && !hasSavedPassphrase
                  }
                  onClear={() => {
                    setPassphrase('');
                    if (mode === 'edit' && hasSavedPassphrase) setClearPassphrase(true);
                  }}
                  onUndo={() => setClearPassphrase(false)}
                />
              }
            >
              <input
                className="input"
                type="password"
                value={passphrase}
                disabled={clearPrivateKey}
                placeholder={
                  clearPrivateKey
                    ? '私钥清空时口令一并清空'
                    : clearPassphrase
                      ? '保存后清空'
                      : hasSavedPassphrase
                        ? '已设置，留空不修改'
                        : '私钥未加密则留空'
                }
                onChange={(e) => {
                  setPassphrase(e.target.value);
                  setClearPassphrase(false);
                }}
              />
              {clearPassphrase && !clearPrivateKey && (
                <p className="mt-1 text-[11px] text-warning-700">已标记清空：保存后口令列写为空</p>
              )}
              <p className="mt-1 text-[11px] leading-4 text-subtle">
                解锁私钥文件的本机口令（不会发给服务器），不是服务器登录密码。
              </p>
            </Field>
          </>
        )}

        <Field label="跳板连接">
          <JumpSelect
            value={Number(jumpId) || 0}
            options={jumpOptions}
            onChange={(id) => setJumpId(String(id))}
          />
          <p className="mt-1 text-[11px] text-subtle">
            选项来自全部已保存连接，已排除当前连接与会造成循环的下游连接
          </p>
        </Field>

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

/** 字段右上角的「清空 / 撤销清空」按钮（凭据类字段专用） */
function ClearAction({
  scheduled,
  disabled,
  onClear,
  onUndo,
}: {
  scheduled: boolean;
  disabled: boolean;
  onClear: () => void;
  onUndo: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled && !scheduled}
      className="text-[11px] text-brand-600 hover:underline disabled:text-subtle disabled:no-underline"
      onClick={() => (scheduled ? onUndo() : onClear())}
    >
      {scheduled ? '撤销清空' : '清空'}
    </button>
  );
}

/**
 * 页内跳板下拉（替代原生 `<select>`）。
 *
 * 原生 select 的弹出列表是系统/QtWebEngine 级弹层：Windows 多显示器混合 DPI 下
 * 窗口跨屏后弹层会持续变大（用户实测「点击后展示框慢慢变大」）。这里改成
 * 页面内列表：DOM 渲染、限高滚动、无系统弹层依赖。
 */
function JumpSelect({
  value,
  options,
  onChange,
}: {
  value: number;
  options: Array<{ id: number; name: string }>;
  onChange: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const selected = options.find((option) => option.id === value);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (target && boxRef.current && !boxRef.current.contains(target)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div ref={boxRef}>
      <button
        type="button"
        data-testid="jump-select"
        className="input flex w-full items-center justify-between gap-2 text-left"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="truncate">{selected?.name ?? '直连'}</span>
        <Icon name="next" size="sm" className={open ? 'rotate-90 text-subtle transition-transform' : 'text-subtle transition-transform'} />
      </button>
      {open && (
        <div
          className="mt-1 max-h-56 overflow-auto rounded-md border border-line bg-surface p-1 shadow-e2"
          role="listbox"
        >
          {options.map((option) => {
            const active = option.id === value;
            return (
              <button
                key={option.id}
                type="button"
                role="option"
                aria-selected={active}
                className={[
                  'flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-xs',
                  active ? 'bg-active font-semibold text-brand-700' : 'text-fg hover:bg-hover',
                ].join(' ')}
                onClick={() => {
                  onChange(option.id);
                  setOpen(false);
                }}
              >
                <span className="truncate">{option.name}</span>
                {active && <Icon name="checked" size="sm" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  children,
  className,
  action,
}: {
  label: string;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  return (
    <div className={className}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="text-[11.5px] font-semibold text-muted">{label}</div>
        {action}
      </div>
      {children}
    </div>
  );
}

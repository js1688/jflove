/**
 * 远程连接管理页（v1.10.3）
 *
 * 左侧是一棵合并树：根节点固定「本地 / 远程」，各自可建分组与条目。
 * - 本地：本机终端配置，存本地 SQLite（group_id / shell / 默认命令）
 * - 远程：SSH / 仅 SFTP 连接，存服务端
 *
 * 新建时按当前选中的一侧决定写本地还是写服务端。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { Button, EmptyState, Icon, Skeleton } from '../../components/ui';
import { RemoteConnectionForm } from '../../components/remote/RemoteConnectionForm';
import { PromptModal } from '../../components/remote/PromptModal';
import { useRemoteStore } from '../../stores/remote-store';
import { localTermService, type LocalProfile, type LocalShell } from '../../services/remote-service';
import type { RemoteConnectionSummary } from '../../services/remote-service';

interface LocalGroup {
  id: number;
  name: string;
  sort_order: number;
}

type Side = 'local' | 'remote';
type Selection = { side: Side; groupId: number | null };

interface LocalFormState {
  id?: number;
  name: string;
  shellPath: string;
  cwd: string;
  initCommand: string;
  groupId: number;
}

const EMPTY_LOCAL_FORM: LocalFormState = {
  name: '',
  shellPath: '',
  cwd: '',
  initCommand: '',
  groupId: 0,
};

export function RemotePage() {
  const {
    groups,
    connections,
    keyword,
    loading,
    error,
    editor,
    load,
    setKeyword,
    openEditor,
    closeEditor,
    saveConnection,
    deleteConnection,
    duplicateConnection,
    createGroup,
    deleteGroup,
    openRemoteSession,
    openLocalSession,
  } = useRemoteStore();
  const navigate = useNavigate();

  const [selection, setSelection] = useState<Selection>({ side: 'remote', groupId: null });
  const [createOpen, setCreateOpen] = useState(false);
  const [groupPrompt, setGroupPrompt] = useState<{ side: Side } | null>(null);
  const [localGroups, setLocalGroups] = useState<LocalGroup[]>([]);
  const [localProfiles, setLocalProfiles] = useState<LocalProfile[]>([]);
  const [shells, setShells] = useState<LocalShell[]>([]);
  const [localLoading, setLocalLoading] = useState(false);
  const [localForm, setLocalForm] = useState<LocalFormState | null>(null);

  const loadLocal = useCallback(async () => {
    setLocalLoading(true);
    try {
      const [groupResult, profileResult, shellResult] = await Promise.all([
        localTermService.listGroups(),
        localTermService.listProfiles(),
        localTermService.detectShells(),
      ]);
      setLocalGroups(groupResult.groups);
      setLocalProfiles(profileResult.profiles);
      setShells(shellResult.shells);
    } finally {
      setLocalLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadLocal();
  }, [load, loadLocal]);

  const remoteFiltered = useMemo(() => {
    if (selection.groupId === null) return connections;
    return connections.filter((item) => item.ssh_group_id === selection.groupId);
  }, [connections, selection]);

  const localFiltered = useMemo(() => {
    if (selection.groupId === null) return localProfiles;
    return localProfiles.filter((item) => item.group_id === selection.groupId);
  }, [localProfiles, selection]);

  const openRemote = async (connection: RemoteConnectionSummary) => {
    await openRemoteSession(connection);
    navigate('/remote/session');
  };

  const openLocal = async (profile: LocalProfile) => {
    await openLocalSession(profile.id, profile.name);
    navigate('/remote/session');
  };

  const handleCreate = () => {
    if (selection.side === 'local') {
      const first = shells.find((shell) => shell.available);
      setLocalForm({
        ...EMPTY_LOCAL_FORM,
        shellPath: first?.path ?? '',
        groupId: selection.groupId ?? 0,
      });
      return;
    }
    setCreateOpen(true);
  };

  const handleCreateGroup = (name: string) => {
    if (!groupPrompt) return;
    if (groupPrompt.side === 'local') {
      void localTermService.createGroup(name.trim()).then(loadLocal);
    } else {
      void createGroup(name.trim());
    }
  };

  const saveLocal = async () => {
    if (!localForm || !localForm.name.trim()) return;
    const payload = {
      name: localForm.name.trim(),
      shell_kind: 'local',
      shell_path: localForm.shellPath,
      args: '',
      cwd: localForm.cwd,
      env: {},
      init_command: localForm.initCommand,
      group_id: localForm.groupId,
    };
    if (localForm.id) await localTermService.updateProfile(localForm.id, payload);
    else await localTermService.createProfile(payload);
    setLocalForm(null);
    await loadLocal();
  };

  const deleteLocal = async (profile: LocalProfile) => {
    if (!window.confirm(`删除本机配置「${profile.name}」？`)) return;
    await localTermService.deleteProfile(profile.id);
    await loadLocal();
  };

  return (
    <div className="flex h-full min-h-0 bg-canvas">
      <aside className="flex w-[260px] shrink-0 flex-col border-r border-line-subtle bg-surface">
        <div className="flex h-12 items-center justify-between border-b border-line-subtle px-3 text-xs font-semibold text-muted">
          <span>连接</span>
          <button
            type="button"
            className="text-brand-600"
            onClick={() => setGroupPrompt({ side: selection.side })}
            title={`在「${selection.side === 'local' ? '本地' : '远程'}」下新建分组`}
          >
            ＋ 分组
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-2">
          <TreeRoot
            label="本地"
            icon="terminal"
            active={selection.side === 'local'}
            onClick={() => setSelection({ side: 'local', groupId: null })}
            count={localProfiles.length}
          >
            {localGroups.map((group) => (
              <TreeGroup
                key={group.id}
                label={group.name}
                active={selection.side === 'local' && selection.groupId === group.id}
                count={localProfiles.filter((p) => p.group_id === group.id).length}
                onClick={() => setSelection({ side: 'local', groupId: group.id })}
                onDelete={() => {
                  if (window.confirm(`删除本地分组「${group.name}」？`)) {
                    void localTermService.deleteGroup(group.id).then(loadLocal);
                  }
                }}
              />
            ))}
            {localProfiles
              .filter((p) => p.group_id === 0)
              .map((profile) => (
                <TreeLeaf
                  key={`local-${profile.id}`}
                  label={profile.name}
                  hint={profile.shell_path || '默认 shell'}
                  onClick={() => setSelection({ side: 'local', groupId: null })}
                  onOpen={() => void openLocal(profile)}
                />
              ))}
            {localGroups.length === 0 && localLoading && (
              <div className="px-2 py-1 text-[11px] text-subtle">加载中…</div>
            )}
          </TreeRoot>

          <TreeRoot
            label="远程"
            icon="server"
            active={selection.side === 'remote'}
            onClick={() => setSelection({ side: 'remote', groupId: null })}
            count={connections.length}
          >
            {groups.map((group) => (
              <TreeGroup
                key={group.id}
                label={group.name}
                active={selection.side === 'remote' && selection.groupId === group.id}
                count={group.connection_count}
                onClick={() => setSelection({ side: 'remote', groupId: group.id })}
                onDelete={() => {
                  if (window.confirm(`删除分组「${group.name}」？连接会落到未分组`)) {
                    void deleteGroup(group.id);
                  }
                }}
              />
            ))}
            {connections
              .filter((c) => c.ssh_group_id === 0)
              .map((connection) => (
                <TreeLeaf
                  key={`remote-${connection.id}`}
                  label={connection.name}
                  hint={`${connection.host}:${connection.port}`}
                  onClick={() => setSelection({ side: 'remote', groupId: null })}
                  onOpen={() => void openRemote(connection)}
                />
              ))}
          </TreeRoot>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line-subtle px-5">
          <h2 className="text-[15px] font-semibold">
            {selection.side === 'local' ? '本地' : '远程'}
            {selection.groupId !== null && (
              <span className="ml-1 text-xs font-normal text-muted">
                / {selection.side === 'local'
                  ? localGroups.find((g) => g.id === selection.groupId)?.name
                  : groups.find((g) => g.id === selection.groupId)?.name}
              </span>
            )}
          </h2>
          {selection.side === 'remote' && (
            <div className="search-field !h-8 w-[220px]">
              <Icon name="search" size="sm" />
              <input
                value={keyword}
                placeholder="搜索名称 / 主机 / 用户"
                onChange={(e) => {
                  setKeyword(e.target.value);
                  void load();
                }}
              />
            </div>
          )}
          <div className="flex-1" />
          <Button variant="primary" icon="plus" onClick={handleCreate}>
            新建{selection.side === 'local' ? '本机配置' : '连接'}
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-5">
          {error && <div className="mb-3 rounded bg-danger-50 px-3 py-2 text-xs text-danger-700">{error}</div>}
          {selection.side === 'remote' && loading && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
            </div>
          )}

          {selection.side === 'remote' && !loading && remoteFiltered.length === 0 && (
            <EmptyState icon="terminal" title="还没有远程连接" description="点击右上角新建" />
          )}
          {selection.side === 'remote' && !loading && remoteFiltered.length > 0 && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
              {remoteFiltered.map((connection) => (
                <RemoteCard
                  key={connection.id}
                  connection={connection}
                  jumpName={
                    connection.jump_connection_id
                      ? connections.find((item) => item.id === connection.jump_connection_id)?.name
                      : undefined
                  }
                  onOpen={() => void openRemote(connection)}
                  onEdit={() => void openEditor('edit', connection.id)}
                  onDuplicate={() => void duplicateConnection(connection.id)}
                  onDelete={() => {
                    if (window.confirm(`删除连接「${connection.name}」？`)) {
                      void deleteConnection(connection.id);
                    }
                  }}
                />
              ))}
            </div>
          )}

          {selection.side === 'local' && localFiltered.length === 0 && (
            <EmptyState
              icon="terminal"
              title="还没有本机配置"
              description="新建一个，选择 shell、工作目录与默认命令"
            />
          )}
          {selection.side === 'local' && localFiltered.length > 0 && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
              {localFiltered.map((profile) => (
                <LocalCard
                  key={profile.id}
                  profile={profile}
                  onOpen={() => void openLocal(profile)}
                  onEdit={() =>
                    setLocalForm({
                      id: profile.id,
                      name: profile.name,
                      shellPath: profile.shell_path,
                      cwd: profile.cwd,
                      initCommand: profile.init_command,
                      groupId: profile.group_id,
                    })
                  }
                  onDelete={() => void deleteLocal(profile)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {groupPrompt && (
        <PromptModal
          title={`在「${groupPrompt.side === 'local' ? '本地' : '远程'}」下新建分组`}
          placeholder="例如：生产环境"
          onCancel={() => setGroupPrompt(null)}
          onConfirm={(name) => {
            const target = groupPrompt;
            setGroupPrompt(null);
            if (name.trim()) {
              if (target.side === 'local') {
                void localTermService.createGroup(name.trim()).then(loadLocal);
              } else {
                void handleCreateGroup(name.trim());
              }
            }
          }}
        />
      )}

      {createOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
          onClick={() => setCreateOpen(false)}
        >
          <div
            className="w-[440px] rounded-xl border border-line bg-surface p-5 shadow-e4"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-1 text-base font-semibold">新建远程连接</div>
            <p className="mb-4 text-xs text-muted">先选择要建立的连接类型</p>
            <div className="space-y-2">
              <TypeOption
                title="远程终端（SSH）"
                desc="打开终端，也可在同一条连接里浏览文件（SFTP）"
                onClick={() => {
                  setCreateOpen(false);
                  void openEditor('create', undefined, 'ssh');
                }}
              />
              <TypeOption
                title="仅 SFTP"
                desc="只浏览/上传/下载文件，不建立 shell（适合只开 SFTP 的服务器）"
                onClick={() => {
                  setCreateOpen(false);
                  void openEditor('create', undefined, 'sftp');
                }}
              />
            </div>
          </div>
        </div>
      )}

      {localForm && (
        <LocalForm
          form={localForm}
          shells={shells}
          groups={localGroups}
          onChange={setLocalForm}
          onCancel={() => setLocalForm(null)}
          onSave={() => void saveLocal()}
        />
      )}

      {editor && (
        <RemoteConnectionForm
          mode={editor.mode}
          connectionMode={editor.connectionMode}
          detail={editor.detail}
          groups={groups}
          connections={connections}
          onCancel={closeEditor}
          onSave={saveConnection}
        />
      )}
    </div>
  );
}

/* ────────────────────── 树与卡片 ────────────────────── */

function TreeRoot({
  label,
  icon,
  active,
  count,
  onClick,
  children,
}: {
  label: string;
  icon: 'terminal' | 'server';
  active: boolean;
  count: number;
  onClick: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={onClick}
        className={[
          'flex h-8 w-full items-center gap-2 rounded px-2 text-[12.5px] font-semibold',
          active ? 'bg-active text-brand-600' : 'hover:bg-hover',
        ].join(' ')}
      >
        <Icon name={icon} size="sm" />
        <span className="flex-1 text-left">{label}</span>
        <span className="rounded-full bg-sunken px-1.5 text-[10.5px] text-subtle">{count}</span>
      </button>
      <div className="ml-3 border-l border-line-subtle pl-1">{children}</div>
    </div>
  );
}

function TreeGroup({
  label,
  active,
  count,
  onClick,
  onDelete,
}: {
  label: string;
  active: boolean;
  count: number;
  onClick: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={[
        'group flex h-7 items-center gap-1 rounded px-2 text-[12px]',
        active ? 'bg-active font-semibold text-brand-600' : 'hover:bg-hover',
      ].join(' ')}
    >
      <button type="button" className="flex min-w-0 flex-1 items-center gap-1 text-left" onClick={onClick}>
        <span className="truncate">{label}</span>
        <span className="ml-auto text-[10.5px] text-subtle">{count}</span>
      </button>
      <button
        type="button"
        className="hidden text-[11px] text-subtle hover:text-danger-500 group-hover:block"
        onClick={onDelete}
      >
        ×
      </button>
    </div>
  );
}

function TreeLeaf({
  label,
  hint,
  onClick,
  onOpen,
}: {
  label: string;
  hint: string;
  onClick: () => void;
  onOpen: () => void;
}) {
  return (
    <div className="group flex h-7 items-center gap-1 rounded px-2 text-[12px] hover:bg-hover">
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-1 truncate text-left"
        onClick={onClick}
        onDoubleClick={onOpen}
        title={`${label} · ${hint}`}
      >
        <span className="truncate">{label}</span>
      </button>
      <button
        type="button"
        className="hidden shrink-0 text-[10.5px] text-brand-600 group-hover:block"
        onClick={onOpen}
      >
        打开
      </button>
    </div>
  );
}

function RemoteCard({
  connection,
  jumpName,
  onOpen,
  onEdit,
  onDuplicate,
  onDelete,
}: {
  connection: RemoteConnectionSummary;
  jumpName?: string;
  onOpen: () => void;
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const authLabel = {
    password: '密码',
    publickey: '密钥',
    password_keyboard: '密码 + OTP',
  }[connection.auth_type];
  const modeLabel = connection.mode === 'sftp' ? '仅 SFTP' : 'SSH 终端';

  return (
    <div
      className="group relative rounded-xl border border-line-subtle bg-surface p-3.5 shadow-e1 transition hover:-translate-y-px hover:border-brand-200 hover:shadow-e2"
      onDoubleClick={onOpen}
    >
      <div className="flex items-start gap-2.5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <Icon name="terminal" size="sm" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-[13.5px] font-semibold">{connection.name}</div>
          <div className="truncate font-mono text-[11.5px] text-muted">
            {connection.host}:{connection.port}
          </div>
        </div>
        <div className="ml-auto hidden gap-1 group-hover:flex">
          <button type="button" className="btn-icon" title="打开" onClick={onOpen}>
            <Icon name="play" size="sm" />
          </button>
          <button type="button" className="btn-icon" title="编辑" onClick={onEdit}>
            <Icon name="edit" size="sm" />
          </button>
          <button type="button" className="btn-icon" title="复制" onClick={onDuplicate}>
            <Icon name="copy" size="sm" />
          </button>
          <button type="button" className="btn-icon" title="删除" onClick={onDelete}>
            <Icon name="delete" size="sm" />
          </button>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className="rounded-full bg-sunken px-2 py-0.5 text-[10.5px] text-muted">{modeLabel}</span>
        <span className="rounded-full bg-sunken px-2 py-0.5 text-[10.5px] text-muted">{authLabel}</span>
        {connection.username && (
          <span className="rounded-full bg-sunken px-2 py-0.5 text-[10.5px] text-muted">
            {connection.username}
          </span>
        )}
        {jumpName && (
          <span className="rounded-full bg-warning-50 px-2 py-0.5 text-[10.5px] text-warning-700">
            ↳ 跳板 {jumpName}
          </span>
        )}
      </div>
    </div>
  );
}

function LocalCard({
  profile,
  onOpen,
  onEdit,
  onDelete,
}: {
  profile: LocalProfile;
  onOpen: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className="group rounded-xl border border-line-subtle bg-surface p-3.5 shadow-e1 hover:border-brand-200"
      onDoubleClick={onOpen}
    >
      <div className="flex items-start gap-2.5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <Icon name="terminal" size="sm" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-[13.5px] font-semibold">{profile.name}</div>
          <div className="truncate font-mono text-[11.5px] text-muted">
            {profile.shell_path || '默认 shell'}
          </div>
        </div>
        <div className="ml-auto hidden gap-1 group-hover:flex">
          <button type="button" className="btn-icon" title="打开" onClick={onOpen}>
            <Icon name="play" size="sm" />
          </button>
          <button type="button" className="btn-icon" title="编辑" onClick={onEdit}>
            <Icon name="edit" size="sm" />
          </button>
          <button type="button" className="btn-icon" title="删除" onClick={onDelete}>
            <Icon name="delete" size="sm" />
          </button>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {profile.cwd && (
          <span className="rounded-full bg-sunken px-2 py-0.5 font-mono text-[10.5px] text-muted">
            {profile.cwd}
          </span>
        )}
        {profile.init_command && (
          <span className="max-w-full truncate rounded-full bg-sunken px-2 py-0.5 font-mono text-[10.5px] text-muted">
            启动: {profile.init_command}
          </span>
        )}
      </div>
    </div>
  );
}

function LocalForm({
  form,
  shells,
  groups,
  onChange,
  onCancel,
  onSave,
}: {
  form: LocalFormState;
  shells: LocalShell[];
  groups: LocalGroup[];
  onChange: (form: LocalFormState) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <aside className="flex w-[380px] shrink-0 flex-col border-l border-line bg-surface">
      <div className="flex h-[52px] shrink-0 items-center justify-between border-b border-line-subtle px-4">
        <span className="text-sm font-semibold">{form.id ? '编辑本机配置' : '新建本机配置'}</span>
        <span className="text-xs text-subtle">{form.name}</span>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 py-4">
        <div>
          <div className="mb-1 text-[11.5px] font-semibold text-muted">名称</div>
          <input
            className="input"
            value={form.name}
            placeholder="例如：PowerShell / WSL"
            onChange={(event) => onChange({ ...form, name: event.target.value })}
          />
        </div>
        <div>
          <div className="mb-1 text-[11.5px] font-semibold text-muted">Shell</div>
          <select
            className="input"
            value={form.shellPath}
            onChange={(event) => onChange({ ...form, shellPath: event.target.value })}
          >
            {shells.map((shell) => (
              <option key={shell.path} value={shell.path} disabled={!shell.available}>
                {shell.label} · {shell.path}
              </option>
            ))}
          </select>
        </div>
        <div>
          <div className="mb-1 text-[11.5px] font-semibold text-muted">工作目录（可选）</div>
          <input
            className="input font-mono text-[11.5px]"
            value={form.cwd}
            placeholder="/home/user/project"
            onChange={(event) => onChange({ ...form, cwd: event.target.value })}
          />
        </div>
        <div>
          <div className="mb-1 text-[11.5px] font-semibold text-muted">默认命令（可选）</div>
          <input
            className="input font-mono text-[11.5px]"
            value={form.initCommand}
            placeholder="cd ~/apps && ls"
            onChange={(event) => onChange({ ...form, initCommand: event.target.value })}
          />
          <p className="mt-1 text-[11px] text-subtle">打开后自动执行，仅允许单行</p>
        </div>
        <div>
          <div className="mb-1 text-[11.5px] font-semibold text-muted">分组</div>
          <select
            className="input"
            value={String(form.groupId)}
            onChange={(event) => onChange({ ...form, groupId: Number(event.target.value) })}
          >
            <option value="0">未分组</option>
            {groups.map((group) => (
              <option key={group.id} value={String(group.id)}>
                {group.name}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="flex justify-end gap-2 border-t border-line-subtle px-4 py-3">
        <Button variant="ghost" onClick={onCancel}>
          取消
        </Button>
        <Button variant="primary" onClick={onSave}>
          保存
        </Button>
      </div>
    </aside>
  );
}

function TypeOption({
  title,
  desc,
  onClick,
}: {
  title: string;
  desc: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full rounded-lg border border-line-subtle px-3 py-2.5 text-left transition hover:border-brand-300 hover:bg-hover"
    >
      <div className="text-[13px] font-semibold">{title}</div>
      <div className="mt-0.5 text-[11.5px] text-muted">{desc}</div>
    </button>
  );
}

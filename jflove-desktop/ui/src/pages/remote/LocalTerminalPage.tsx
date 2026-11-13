/**
 * 本机终端管理页（D3）
 *
 * 配置与分组只存本地 SQLite，不参与服务端同步（`文档记录/设计文档/v1.10.0.md` §3.7）。
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { Button, EmptyState, Icon } from '../../components/ui';
import { useRemoteStore } from '../../stores/remote-store';
import {
  localTermService,
  type LocalProfile,
  type LocalShell,
} from '../../services/remote-service';

interface LocalGroup {
  id: number;
  name: string;
  sort_order: number;
}

interface FormState {
  id?: number;
  name: string;
  shellPath: string;
  cwd: string;
  initCommand: string;
  groupId: number;
}

const EMPTY_FORM: FormState = {
  name: '',
  shellPath: '',
  cwd: '',
  initCommand: '',
  groupId: 0,
};

export function LocalTerminalPage() {
  const { openLocalSession } = useRemoteStore();
  const navigate = useNavigate();
  const [shells, setShells] = useState<LocalShell[]>([]);
  const [profiles, setProfiles] = useState<LocalProfile[]>([]);
  const [groups, setGroups] = useState<LocalGroup[]>([]);
  const [form, setForm] = useState<FormState | null>(null);

  const reload = useCallback(async () => {
    const [shellResult, profileResult, groupResult] = await Promise.all([
      localTermService.detectShells(),
      localTermService.listProfiles(),
      localTermService.listGroups(),
    ]);
    setShells(shellResult.shells);
    setProfiles(profileResult.profiles);
    setGroups(groupResult.groups);
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const openProfile = async (profile: LocalProfile) => {
    await openLocalSession(profile.id, profile.name);
    navigate('/remote/session');
  };

  const openDefault = async () => {
    await openLocalSession(0, '本机终端');
    navigate('/remote/session');
  };

  const save = async () => {
    if (!form || !form.name.trim()) return;
    const payload = {
      name: form.name.trim(),
      shell_kind: 'local',
      shell_path: form.shellPath,
      args: '',
      cwd: form.cwd,
      env: {},
      init_command: form.initCommand,
      group_id: form.groupId,
    };
    if (form.id) await localTermService.updateProfile(form.id, payload);
    else await localTermService.createProfile(payload);
    setForm(null);
    await reload();
  };

  return (
    <div className="flex h-full min-h-0 bg-canvas">
      <aside className="flex w-[222px] shrink-0 flex-col border-r border-line-subtle bg-surface">
        <div className="flex h-12 items-center justify-between border-b border-line-subtle px-3 text-xs font-semibold text-muted">
          <span>本地分组</span>
          <button
            type="button"
            className="text-brand-600"
            onClick={async () => {
              const name = window.prompt('新建分组名称');
              if (name) {
                await localTermService.createGroup(name);
                await reload();
              }
            }}
          >
            ＋
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-0.5 overflow-auto p-2">
          {groups.map((group) => (
            <div key={group.id} className="group flex h-8 items-center gap-2 rounded px-2 text-[12.5px]">
              <span className="truncate">{group.name}</span>
              <button
                type="button"
                className="ml-auto hidden text-[11px] text-subtle hover:text-danger-500 group-hover:block"
                onClick={async () => {
                  if (!window.confirm(`删除分组「${group.name}」？`)) return;
                  await localTermService.deleteGroup(group.id);
                  await reload();
                }}
              >
                ×
              </button>
            </div>
          ))}
          {groups.length === 0 && <div className="px-2 py-2 text-[11.5px] text-subtle">暂无分组</div>}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line-subtle px-5">
          <h2 className="text-[15px] font-semibold">本机终端</h2>
          <span className="rounded-full bg-sunken px-2 py-0.5 text-[10.5px] text-muted">
            仅本地存储
          </span>
          <div className="flex-1" />
          <Button variant="ghost" icon="terminal" onClick={() => void openDefault()}>
            打开默认终端
          </Button>
          <Button
            variant="primary"
            icon="plus"
            onClick={() => {
              const first = shells.find((shell) => shell.available);
              setForm({ ...EMPTY_FORM, shellPath: first?.path ?? '' });
            }}
          >
            新建终端配置
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-5">
          {profiles.length === 0 ? (
            <EmptyState
              icon="terminal"
              title="还没有本机终端配置"
              description="新建一个，指定 shell、工作目录和启动命令"
            />
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
              {profiles.map((profile) => (
                <div
                  key={profile.id}
                  className="group rounded-xl border border-line-subtle bg-surface p-3.5 shadow-e1 hover:border-brand-200"
                  onDoubleClick={() => void openProfile(profile)}
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
                      <button
                        type="button"
                        className="btn-icon"
                        title="打开"
                        onClick={() => void openProfile(profile)}
                      >
                        <Icon name="play" size="sm" />
                      </button>
                      <button
                        type="button"
                        className="btn-icon"
                        title="编辑"
                        onClick={() =>
                          setForm({
                            id: profile.id,
                            name: profile.name,
                            shellPath: profile.shell_path,
                            cwd: profile.cwd,
                            initCommand: profile.init_command,
                            groupId: profile.group_id,
                          })
                        }
                      >
                        <Icon name="edit" size="sm" />
                      </button>
                      <button
                        type="button"
                        className="btn-icon"
                        title="删除"
                        onClick={async () => {
                          if (!window.confirm(`删除「${profile.name}」？`)) return;
                          await localTermService.deleteProfile(profile.id);
                          await reload();
                        }}
                      >
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
              ))}
            </div>
          )}
        </div>
      </div>

      {form && (
        <aside className="flex w-[360px] shrink-0 flex-col border-l border-line bg-surface">
          <div className="flex h-[52px] shrink-0 items-center justify-between border-b border-line-subtle px-4 text-sm font-semibold">
            <span>{form.id ? '编辑终端配置' : '新建终端配置'}</span>
          </div>
          <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 py-4">
            <div>
              <div className="mb-1 text-[11.5px] font-semibold text-muted">名称</div>
              <input
                className="input"
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </div>
            <div>
              <div className="mb-1 text-[11.5px] font-semibold text-muted">Shell</div>
              <select
                className="input"
                value={form.shellPath}
                onChange={(event) => setForm({ ...form, shellPath: event.target.value })}
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
                onChange={(event) => setForm({ ...form, cwd: event.target.value })}
              />
            </div>
            <div>
              <div className="mb-1 text-[11.5px] font-semibold text-muted">连接后执行（可选）</div>
              <input
                className="input font-mono text-[11.5px]"
                value={form.initCommand}
                placeholder="cd ~/apps && ls"
                onChange={(event) => setForm({ ...form, initCommand: event.target.value })}
              />
              <p className="mt-1 text-[11px] text-subtle">打开后自动回显并执行；仅允许单行</p>
            </div>
            <div>
              <div className="mb-1 text-[11.5px] font-semibold text-muted">分组</div>
              <select
                className="input"
                value={String(form.groupId)}
                onChange={(event) => setForm({ ...form, groupId: Number(event.target.value) })}
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
            <Button variant="ghost" onClick={() => setForm(null)}>
              取消
            </Button>
            <Button variant="primary" onClick={() => void save()}>
              保存
            </Button>
          </div>
        </aside>
      )}
    </div>
  );
}

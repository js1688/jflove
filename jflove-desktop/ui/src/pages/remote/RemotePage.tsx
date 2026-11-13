/**
 * 远程连接管理页（D1）
 *
 * 左：分组树；中：连接卡片；右：编辑抽屉（非弹窗）。
 * 打开会话后切到 `/remote/session` 工作区。
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { Button, EmptyState, Icon, Skeleton } from '../../components/ui';
import { RemoteConnectionForm } from '../../components/remote/RemoteConnectionForm';
import { useRemoteStore } from '../../stores/remote-store';
import type { RemoteConnectionSummary } from '../../services/remote-service';

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
  } = useRemoteStore();
  const [selected, setSelected] = useState<'all' | 'none' | number>('all');
  const navigate = useNavigate();

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    if (selected === 'all') return connections;
    if (selected === 'none') return connections.filter((item) => item.ssh_group_id === 0);
    return connections.filter((item) => item.ssh_group_id === selected);
  }, [connections, selected]);

  const openConnection = async (connection: RemoteConnectionSummary) => {
    await openRemoteSession(connection);
    navigate('/remote/session');
  };

  return (
    <div className="flex h-full min-h-0 bg-canvas">
      <aside className="flex w-[222px] shrink-0 flex-col border-r border-line-subtle bg-surface">
        <div className="flex h-12 items-center justify-between border-b border-line-subtle px-3 text-xs font-semibold text-muted">
          <span>分组</span>
          <button
            type="button"
            className="text-brand-600"
            onClick={() => {
              const name = window.prompt('新建分组名称');
              if (name) void createGroup(name);
            }}
          >
            ＋
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-0.5 overflow-auto p-2">
          <GroupRow
            active={selected === 'all'}
            label="全部连接"
            count={connections.length}
            onClick={() => setSelected('all')}
          />
          {groups.map((group) => (
            <GroupRow
              key={group.id}
              active={selected === group.id}
              label={group.name}
              count={group.connection_count}
              onClick={() => setSelected(group.id)}
              onDelete={() => {
                if (window.confirm(`删除分组「${group.name}」？连接会落到未分组`)) {
                  void deleteGroup(group.id);
                }
              }}
            />
          ))}
          <GroupRow
            active={selected === 'none'}
            label="未分组"
            count={connections.filter((item) => item.ssh_group_id === 0).length}
            onClick={() => setSelected('none')}
          />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line-subtle px-5">
          <h2 className="text-[15px] font-semibold">远程连接</h2>
          <div className="search-field !h-8 w-[240px]">
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
          <div className="flex-1" />
          <Button variant="ghost" icon="terminal" onClick={() => navigate('/remote/local')}>
            本机终端
          </Button>
          <Button variant="primary" icon="plus" onClick={() => void openEditor('create')}>
            新建连接
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-5">
          {error && <div className="mb-3 rounded bg-danger-50 px-3 py-2 text-xs text-danger-700">{error}</div>}
          {loading && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <EmptyState
              icon="terminal"
              title="还没有连接"
              description="新建一条 SSH 连接，配置会存到服务端"
            />
          )}
          {!loading && filtered.length > 0 && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
              {filtered.map((connection) => (
                <ConnectionCard
                  key={connection.id}
                  connection={connection}
                  jumpName={
                    connection.jump_connection_id
                      ? connections.find((item) => item.id === connection.jump_connection_id)?.name
                      : undefined
                  }
                  onOpen={() => void openConnection(connection)}
                  onEdit={() => void openEditor('edit', connection.id)}
                  onDuplicate={() => void duplicateConnection(connection.id)}
                  onDelete={() => {
                    if (window.confirm(`删除连接「${connection.name}」？`)) {
                      void deleteConnection(connection.id);
                    }
                  }}
                />
              ))}
              <button
                type="button"
                onClick={() => void openEditor('create')}
                className="flex min-h-[96px] items-center justify-center rounded-xl border border-dashed border-line text-xs text-subtle hover:border-brand-300 hover:text-brand-600"
              >
                ＋ 新建连接
              </button>
            </div>
          )}
        </div>
      </div>

      {editor && (
        <RemoteConnectionForm
          mode={editor.mode}
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

function GroupRow({
  active,
  label,
  count,
  onClick,
  onDelete,
}: {
  active: boolean;
  label: string;
  count: number;
  onClick: () => void;
  onDelete?: () => void;
}) {
  return (
    <div
      className={[
        'group flex h-8 items-center gap-2 rounded px-2 text-[12.5px]',
        active ? 'bg-active font-semibold text-brand-600' : 'hover:bg-hover',
      ].join(' ')}
    >
      <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={onClick}>
        <span className="truncate">{label}</span>
        <span className="ml-auto rounded-full bg-sunken px-1.5 text-[10.5px] text-subtle">{count}</span>
      </button>
      {onDelete && (
        <button
          type="button"
          className="hidden text-[11px] text-subtle hover:text-danger-500 group-hover:block"
          onClick={onDelete}
        >
          ×
        </button>
      )}
    </div>
  );
}

function ConnectionCard({
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
        {connection.last_connected_at && (
          <span className="ml-auto text-[10.5px] text-subtle">
            {new Date(connection.last_connected_at).toLocaleDateString()}
          </span>
        )}
      </div>
    </div>
  );
}

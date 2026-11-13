/**
 * 会话工作区（D2）
 *
 * 多标签：每个标签一个会话（SSH 终端 / SFTP / 本机终端）。
 * 同一标签内「终端 / 文件」切换不重建连接。
 * 跳板链路与主机指纹确认通过桥事件驱动。
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { Button, EmptyState, Icon } from '../../components/ui';
import { TerminalView } from '../../components/remote/TerminalView';
import { SftpView } from '../../components/remote/SftpView';
import { useRemoteStore } from '../../stores/remote-store';
import { onBridgeEvent } from '../../utils/desktop-bridge';
import { remoteService } from '../../services/remote-service';

function asRecord(payload: unknown): Record<string, unknown> {
  return payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {};
}

export function SessionPage() {
  const {
    tabs,
    activeTabId,
    groups,
    connections,
    load,
    openRemoteSession,
    setActiveTab,
    setTabView,
    closeTab,
    patchTab,
  } = useRemoteStore();
  const navigate = useNavigate();

  const [hostkeyAsk, setHostkeyAsk] = useState<{
    sessionId: string;
    host: string;
    fingerprint: string;
  } | null>(null);
  const [otpAsk, setOtpAsk] = useState<{ sessionId: string; prompt: string } | null>(null);
  const [otpCode, setOtpCode] = useState('');

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const offs = [
      onBridgeEvent('remote.session_status', (payload) => {
        const data = asRecord(payload);
        const sessionId = String(data.session_id || '');
        const phase = String(data.phase || '');
        const message = String(data.message || '');
        const tab = useRemoteStore.getState().tabs.find((item) => item.id === sessionId);
        if (!tab) return;
        const chain = [...tab.jumpChain];
        const index = Number(data.jump_index || 0);
        chain[index] = { label: message, phase };
        patchTab(sessionId, { jumpChain: chain, message });
      }),
      onBridgeEvent('remote.session_ready', (payload) => {
        const sessionId = String(asRecord(payload).session_id || '');
        patchTab(sessionId, { status: 'ready', message: '已连接' });
      }),
      onBridgeEvent('remote.session_failed', (payload) => {
        const data = asRecord(payload);
        const sessionId = String(data.session_id || '');
        patchTab(sessionId, {
          status: 'failed',
          message: String(data.message || '连接失败'),
        });
      }),
      onBridgeEvent('remote.hostkey_ask', (payload) => {
        const data = asRecord(payload);
        setHostkeyAsk({
          sessionId: String(data.session_id || ''),
          host: String(data.host || ''),
          fingerprint: String(data.fingerprint || ''),
        });
      }),
      onBridgeEvent('remote.otp_ask', (payload) => {
        const data = asRecord(payload);
        setOtpAsk({ sessionId: String(data.session_id || ''), prompt: String(data.prompt || '') });
      }),
    ];
    return () => offs.forEach((off) => off());
  }, [patchTab]);

  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? null;

  const confirmHostKey = async (accept: boolean) => {
    if (!hostkeyAsk) return;
    await remoteService.confirmHostKey(hostkeyAsk.sessionId, accept);
    setHostkeyAsk(null);
  };

  const confirmOtp = async () => {
    if (!otpAsk) return;
    await remoteService.confirmOtp(otpAsk.sessionId, otpCode);
    setOtpAsk(null);
    setOtpCode('');
  };

  return (
    <div className="flex h-full min-h-0 bg-canvas">
      <aside className="flex w-[236px] shrink-0 flex-col border-r border-line-subtle bg-surface">
        <div className="flex h-12 items-center justify-between border-b border-line-subtle px-3 text-xs font-semibold text-muted">
          <span>会话</span>
          <button type="button" className="text-brand-600" onClick={() => void load()}>
            ↻
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-2">
          {groups.map((group) => {
            const items = connections.filter((item) => item.ssh_group_id === group.id);
            if (items.length === 0) return null;
            return (
              <div key={group.id}>
                <div className="px-2 pb-1 pt-2 text-[10.5px] font-semibold tracking-wide text-subtle">
                  {group.name}
                </div>
                {items.map((item) => (
                  <SessionTreeItem
                    key={item.id}
                    name={item.name}
                    host={`${item.host}:${item.port}`}
                    onClick={() => void openRemoteSession(item)}
                  />
                ))}
              </div>
            );
          })}
          {connections.filter((item) => item.ssh_group_id === 0).length > 0 && (
            <div>
              <div className="px-2 pb-1 pt-2 text-[10.5px] font-semibold tracking-wide text-subtle">
                未分组
              </div>
              {connections
                .filter((item) => item.ssh_group_id === 0)
                .map((item) => (
                  <SessionTreeItem
                    key={item.id}
                    name={item.name}
                    host={`${item.host}:${item.port}`}
                    onClick={() => void openRemoteSession(item)}
                  />
                ))}
            </div>
          )}
          {connections.length === 0 && <div className="px-2 py-3 text-xs text-subtle">还没有连接</div>}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-[42px] shrink-0 items-stretch gap-px overflow-x-auto border-b border-line bg-sunken px-2 pt-1">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={[
                'flex h-9 items-center gap-2 whitespace-nowrap rounded-t-md border border-b-0 px-3 text-xs',
                tab.id === activeTabId
                  ? 'border-line bg-surface font-semibold'
                  : 'border-transparent text-muted hover:bg-hover',
              ].join(' ')}
            >
              <span
                className={[
                  'h-1.5 w-1.5 rounded-full',
                  tab.status === 'ready'
                    ? 'bg-success-500'
                    : tab.status === 'failed'
                      ? 'bg-danger-500'
                      : 'bg-warning-500',
                ].join(' ')}
              />
              <span className="max-w-[160px] truncate">{tab.title}</span>
              <span
                role="button"
                tabIndex={0}
                className="rounded px-1 text-subtle hover:bg-hover"
                onClick={(event) => {
                  event.stopPropagation();
                  void closeTab(tab.id);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void closeTab(tab.id);
                }}
              >
                ×
              </span>
            </button>
          ))}
          <button
            type="button"
            className="mb-1 flex w-8 items-center justify-center rounded text-muted hover:bg-hover"
            title="返回连接列表"
            onClick={() => navigate('/remote')}
          >
            ＋
          </button>
        </div>

        {activeTab ? (
          <>
            <div className="flex h-11 shrink-0 items-center gap-3 border-b border-line-subtle bg-surface px-3">
              <div className="min-w-0">
                <div className="truncate text-[12.5px] font-semibold">{activeTab.title}</div>
                <div className="truncate font-mono text-[11px] text-muted">{activeTab.subtitle}</div>
              </div>
              <div className="ml-auto flex items-center rounded-md bg-sunken p-0.5">
                <button
                  type="button"
                  onClick={() => setTabView(activeTab.id, 'terminal')}
                  className={[
                    'rounded px-3 py-1 text-[11.5px]',
                    activeTab.view === 'terminal' ? 'bg-surface font-semibold shadow-sm' : 'text-muted',
                  ].join(' ')}
                >
                  终端
                </button>
                <button
                  type="button"
                  onClick={() => setTabView(activeTab.id, 'files')}
                  className={[
                    'rounded px-3 py-1 text-[11.5px]',
                    activeTab.view === 'files' ? 'bg-surface font-semibold shadow-sm' : 'text-muted',
                  ].join(' ')}
                >
                  文件
                </button>
              </div>
              <Button size="sm" variant="ghost" icon="close" onClick={() => void closeTab(activeTab.id)}>
                断开
              </Button>
            </div>

            {activeTab.jumpChain.length > 0 && (
              <div className="flex h-8 shrink-0 items-center gap-1.5 overflow-x-auto border-b border-line-subtle bg-sunken px-3 text-[11px] text-muted">
                <span>链路</span>
                <span className="rounded-full bg-success-50 px-2 py-0.5 text-success-700">本机</span>
                {activeTab.jumpChain.map((jump, index) => (
                  <span key={index} className="flex items-center gap-1.5">
                    <span>→</span>
                    <span className="rounded-full bg-success-50 px-2 py-0.5 text-success-700">
                      {jump.label}
                    </span>
                  </span>
                ))}
              </div>
            )}

            {activeTab.status === 'failed' && (
              <div className="flex items-center gap-2 border-b border-danger-50 bg-danger-50 px-3 py-2 text-xs text-danger-700">
                <Icon name="error" size="sm" />
                <span className="min-w-0 flex-1">{activeTab.message}</span>
              </div>
            )}

            <div className="min-h-0 flex-1">
              {activeTab.view === 'terminal' ? (
                <TerminalView
                  sessionId={activeTab.id}
                  port={activeTab.port}
                  token={activeTab.token}
                  onReady={() => patchTab(activeTab.id, { status: 'ready' })}
                />
              ) : (
                <SftpView sessionId={activeTab.id} />
              )}
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center">
            <EmptyState
              icon="terminal"
              title="没有打开的会话"
              description="从左侧选择连接，或返回列表新建"
            />
          </div>
        )}
      </div>

      {hostkeyAsk && (
        <div className="fixed inset-x-0 top-16 z-40 mx-auto flex w-[520px] items-start gap-3 rounded-lg border border-warning-50 bg-warning-50 p-3 text-xs shadow-e3">
          <Icon name="warning" size="sm" className="mt-0.5 text-warning-700" />
          <div className="min-w-0 flex-1">
            <div className="font-semibold text-warning-700">确认主机指纹</div>
            <div className="mt-1 break-all font-mono text-[11px]">{hostkeyAsk.fingerprint}</div>
            <div className="mt-1 text-[11px] text-muted">
              首次连接 {hostkeyAsk.host}，请与服务器管理员核对指纹
            </div>
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => void confirmHostKey(false)}>
              拒绝
            </Button>
            <Button size="sm" variant="primary" onClick={() => void confirmHostKey(true)}>
              接受
            </Button>
          </div>
        </div>
      )}

      {otpAsk && (
        <div className="fixed inset-x-0 top-16 z-40 mx-auto flex w-[420px] items-center gap-2 rounded-lg border border-brand-200 bg-surface p-3 text-xs shadow-e3">
          <span className="min-w-0 flex-1 truncate">{otpAsk.prompt || '请输入验证码'}</span>
          <input
            className="input w-28"
            value={otpCode}
            onChange={(event) => setOtpCode(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void confirmOtp();
            }}
          />
          <Button size="sm" variant="primary" onClick={() => void confirmOtp()}>
            确定
          </Button>
        </div>
      )}
    </div>
  );
}

function SessionTreeItem({
  name,
  host,
  onClick,
}: {
  name: string;
  host: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-8 w-full items-center gap-2 rounded px-2 text-left text-[12.5px] hover:bg-hover"
      title={`${name} · ${host}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-subtle" />
      <span className="min-w-0 truncate">{name}</span>
      <span className="ml-auto truncate font-mono text-[10px] text-subtle">{host}</span>
    </button>
  );
}

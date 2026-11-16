/**
 * 会话工作区（D2）
 *
 * 多标签：每个标签一个会话（SSH 终端 / SFTP / 本机终端）。
 *
 * ## v1.10.8 重建（plans/desktop-terminal-base-v1.10.8.md）
 *
 * - **所有标签常驻 DOM**：切标签只切 `hidden`，不卸载 `<TerminalView>`。
 *   旧实现只渲染 activeTab，切走即销毁 xterm + 断开 WS，切回是新建的空终端
 *   （用户实测"切回来面板是空的"）。
 * - 观感重做：标签栏 / 工具条 / 终端卡片 / 状态栏四段式，字号可调，操作按钮齐备。
 * - 会话结束后不再留一块死屏：显示"已结束"横幅，可就地重开。
 * - 跳板链路与主机指纹确认通过桥事件驱动（逻辑未改）。
 */
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { Button, EmptyState, Icon, IconButton, toast } from '../../components/ui';
import {
  TerminalView,
  disposeOrphanTerminals,
  type TerminalHandle,
  type TerminalStatus,
} from '../../components/remote/TerminalView';
import { SftpView } from '../../components/remote/SftpView';
import { useRemoteStore, type SessionTab } from '../../stores/remote-store';
import { onBridgeEvent } from '../../utils/desktop-bridge';
import {
  FONT_MAX,
  FONT_MIN,
  readTerminalFontSize,
  writeTerminalFontSize,
} from '../../utils/terminal-prefs';
import { remoteService } from '../../services/remote-service';

function asRecord(payload: unknown): Record<string, unknown> {
  return payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {};
}

const KIND_LABEL: Record<SessionTab['kind'], string> = {
  local: '本机',
  ssh: 'SSH',
  sftp: 'SFTP',
};

/** 终端类型标签（本机由 Python 侧强制 `xterm-256color`；SSH 默认同值） */
const TERM_NAME = 'xterm-256color';

export function SessionPage() {
  const {
    tabs,
    activeTabId,
    setActiveTab,
    setTabView,
    closeTab,
    patchTab,
    reopenTab,
  } = useRemoteStore();
  const navigate = useNavigate();

  const [hostkeyAsk, setHostkeyAsk] = useState<{
    sessionId: string;
    host: string;
    fingerprint: string;
  } | null>(null);
  const [otpAsk, setOtpAsk] = useState<{ sessionId: string; prompt: string } | null>(null);
  const [otpCode, setOtpCode] = useState('');
  const [fontSize, setFontSize] = useState(readTerminalFontSize);
  const [statuses, setStatuses] = useState<Record<string, TerminalStatus>>({});
  const [exits, setExits] = useState<Record<string, string>>({});
  const terminalRefs = useRef<Record<string, TerminalHandle | null>>({});

  // 标签关闭后回收对应终端运行时（实例跟随"会话"而不是"组件"，必须显式清理）
  useEffect(() => {
    disposeOrphanTerminals(new Set(tabs.map((tab) => tab.id)));
  }, [tabs]);

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
      onBridgeEvent('local_term.session_failed', (payload) => {
        const data = asRecord(payload);
        const sessionId = String(data.session_id || '');
        patchTab(sessionId, {
          status: 'failed',
          message: String(data.message || '本机终端启动失败'),
        });
      }),
      onBridgeEvent('local_term.session_ready', (payload) => {
        const sessionId = String(asRecord(payload).session_id || '');
        patchTab(sessionId, { status: 'ready', message: '已打开' });
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
  const activeStatus = activeTab ? statuses[activeTab.id] : undefined;
  const activeExit = activeTab ? exits[activeTab.id] : undefined;
  const exited = Boolean(activeExit) || activeTab?.status === 'failed';

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

  const updateStatus = (sessionId: string, status: TerminalStatus) => {
    setStatuses((current) => ({ ...current, [sessionId]: status }));
  };

  const handleExit = (sessionId: string, message: string) => {
    setExits((current) => ({ ...current, [sessionId]: message }));
    patchTab(sessionId, { status: 'closed', message });
  };

  const changeFont = (delta: number) => {
    const next = writeTerminalFontSize(fontSize + delta);
    setFontSize(next);
  };

  const closeAndForget = async (id: string) => {
    setExits((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    await closeTab(id);
  };

  const handleReopen = async (id: string) => {
    setExits((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    await reopenTab(id);
  };

  /** 工具条动作：全部打到当前标签的终端句柄上 */
  const withActiveTerminal = (action: (handle: TerminalHandle) => void) => {
    if (!activeTab) return;
    const handle = terminalRefs.current[activeTab.id];
    if (handle) action(handle);
  };

  /** 粘贴：拿不到剪贴板内容时给出可操作提示，而不是"点了没反应" */
  const pasteIntoActiveTerminal = () => {
    withActiveTerminal((handle) => {
      void handle.paste().then((ok) => {
        if (!ok) toast.info('剪贴板为空或不可读，无法粘贴');
      });
    });
  };

  const terminalVisible = activeTab
    ? activeTab.kind !== 'sftp' && (activeTab.kind === 'local' || activeTab.view === 'terminal')
    : false;

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      {/* ① 标签栏 */}
      <div className="flex h-[44px] shrink-0 items-end gap-1 overflow-x-auto border-b border-line bg-sunken px-2 pt-1">
        {tabs.map((tab) => {
          const isActive = tab.id === activeTabId;
          const exited = Boolean(exits[tab.id]) || tab.status === 'failed';
          return (
            <div
              key={tab.id}
              role="tab"
              tabIndex={0}
              aria-selected={isActive}
              onClick={() => setActiveTab(tab.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') setActiveTab(tab.id);
              }}
              className={[
                'group flex h-[34px] max-w-[220px] cursor-pointer items-center gap-2 whitespace-nowrap rounded-t-lg border border-b-0 px-3 text-xs transition-colors',
                isActive
                  ? 'border-line bg-surface font-semibold text-fg shadow-[0_-1px_2px_rgb(15_23_42_/_0.04)]'
                  : 'border-transparent text-muted hover:bg-hover',
              ].join(' ')}
            >
              <span
                className={[
                  'h-1.5 w-1.5 shrink-0 rounded-full',
                  exited
                    ? 'bg-danger-500'
                    : tab.status === 'ready'
                      ? 'bg-success-500'
                      : 'bg-warning-500',
                ].join(' ')}
              />
              <Icon
                name={tab.kind === 'sftp' ? 'files' : 'terminal'}
                size="sm"
                className={isActive ? 'text-brand-600' : 'text-subtle'}
              />
              <span className="truncate">{tab.title}</span>
              <button
                type="button"
                aria-label={`关闭 ${tab.title}`}
                className="ml-0.5 rounded p-0.5 text-subtle opacity-0 transition-opacity hover:bg-sunken hover:text-danger-600 group-hover:opacity-100"
                onClick={(event) => {
                  event.stopPropagation();
                  void closeAndForget(tab.id);
                }}
              >
                <Icon name="close" size="sm" />
              </button>
            </div>
          );
        })}
        <button
          type="button"
          title="返回连接列表"
          className="mb-1 flex h-[34px] w-9 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:bg-hover hover:text-brand-600"
          onClick={() => navigate('/remote')}
        >
          <Icon name="plus" size="sm" />
        </button>
        <div className="flex-1" />
      </div>

      {activeTab ? (
        <>
          {/* ② 工具条 */}
          <div className="flex h-[52px] shrink-0 items-center gap-3 border-b border-line-subtle bg-surface px-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="truncate text-[13.5px] font-semibold">{activeTab.title}</span>
                <span className="rounded-full bg-sunken px-1.5 py-0.5 text-[10px] font-medium text-muted">
                  {KIND_LABEL[activeTab.kind]}
                </span>
                {activeTab.kind === 'ssh' && (
                  <span className="rounded-full bg-brand-50 px-1.5 py-0.5 text-[10px] font-medium text-brand-700">
                    {TERM_NAME}
                  </span>
                )}
              </div>
              <div className="truncate font-mono text-[11px] text-muted">{activeTab.subtitle}</div>
            </div>

            {activeTab.kind === 'ssh' && (
              <div className="ml-auto flex items-center rounded-md bg-sunken p-0.5">
                <button
                  type="button"
                  onClick={() => setTabView(activeTab.id, 'terminal')}
                  className={[
                    'rounded px-3 py-1 text-[11.5px] transition-colors',
                    activeTab.view === 'terminal'
                      ? 'bg-surface font-semibold text-fg shadow-sm'
                      : 'text-muted hover:text-fg',
                  ].join(' ')}
                >
                  终端
                </button>
                <button
                  type="button"
                  onClick={() => setTabView(activeTab.id, 'files')}
                  className={[
                    'rounded px-3 py-1 text-[11.5px] transition-colors',
                    activeTab.view === 'files'
                      ? 'bg-surface font-semibold text-fg shadow-sm'
                      : 'text-muted hover:text-fg',
                  ].join(' ')}
                >
                  文件
                </button>
              </div>
            )}

            <div className={activeTab.kind === 'ssh' ? 'flex items-center gap-1' : 'ml-auto flex items-center gap-1'}>
              <span
                className={[
                  'mr-1 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-medium',
                  exited || activeTab.status === 'failed'
                    ? 'bg-danger-50 text-danger-700'
                    : activeTab.status === 'ready'
                      ? 'bg-success-50 text-success-700'
                      : 'bg-warning-50 text-warning-700',
                ].join(' ')}
              >
                <span
                  className={[
                    'h-1.5 w-1.5 rounded-full',
                    exited || activeTab.status === 'failed'
                      ? 'bg-danger-500'
                      : activeTab.status === 'ready'
                        ? 'bg-success-500'
                        : 'bg-warning-500',
                  ].join(' ')}
                />
                {exited || activeTab.status === 'failed'
                  ? '已结束'
                  : activeTab.status === 'ready'
                    ? '已连接'
                    : '连接中'}
              </span>

              {activeTab.kind !== 'sftp' && (
                <>
                  <IconButton
                    icon="copy"
                    label="复制选中内容"
                    size="sm"
                    onClick={() => void withActiveTerminal((handle) => void handle.copy())}
                  />
                  <IconButton
                    icon="paste"
                    label="粘贴"
                    size="sm"
                    onClick={pasteIntoActiveTerminal}
                  />
                  <IconButton
                    icon="refresh"
                    label="清屏（保留会话）"
                    size="sm"
                    onClick={() => withActiveTerminal((handle) => handle.clear())}
                  />
                  <IconButton
                    icon="view"
                    label="重绘整屏（TUI 画面卡住时用）"
                    size="sm"
                    onClick={() => withActiveTerminal((handle) => handle.redraw())}
                  />
                  <div className="mx-1 flex items-center rounded-md border border-line-subtle">
                    <button
                      type="button"
                      title="缩小字号"
                      aria-label="缩小字号"
                      className="px-1.5 py-1 text-muted hover:text-fg disabled:opacity-40"
                      disabled={fontSize <= FONT_MIN}
                      onClick={() => changeFont(-1)}
                    >
                      <Icon name="minus" size="sm" />
                    </button>
                    <span className="min-w-[28px] text-center text-[11px] tabular-nums text-muted">
                      {fontSize}
                    </span>
                    <button
                      type="button"
                      title="放大字号"
                      aria-label="放大字号"
                      className="px-1.5 py-1 text-muted hover:text-fg disabled:opacity-40"
                      disabled={fontSize >= FONT_MAX}
                      onClick={() => changeFont(1)}
                    >
                      <Icon name="plus" size="sm" />
                    </button>
                  </div>
                </>
              )}
              <Button size="sm" variant="ghost" icon="close" onClick={() => void closeAndForget(activeTab.id)}>
                断开
              </Button>
            </div>
          </div>

          {/* 跳板链路（仅 SSH） */}
          {activeTab.kind === 'ssh' && activeTab.jumpChain.length > 0 && (
            <div className="flex h-8 shrink-0 items-center gap-1.5 overflow-x-auto border-b border-line-subtle bg-sunken px-3 text-[11px] text-muted">
              <Icon name="link" size="sm" />
              <span>本机</span>
              {activeTab.jumpChain.map((jump, index) => (
                <span key={index} className="flex items-center gap-1.5">
                  <Icon name="next" size="sm" />
                  <span className="rounded-full bg-success-50 px-2 py-0.5 text-success-700">
                    {jump.label}
                  </span>
                </span>
              ))}
            </div>
          )}

          {/* 失败 / 结束提示 */}
          {(activeTab.status === 'failed' || activeExit) && (
            <div className="flex shrink-0 items-center gap-2 border-b border-danger-50 bg-danger-50 px-3 py-2 text-xs text-danger-700">
              <Icon name="error" size="sm" />
              <span className="min-w-0 flex-1 truncate">{activeExit || activeTab.message}</span>
              {activeExit && (
                <Button size="sm" variant="ghost" onClick={() => void handleReopen(activeTab.id)}>
                  重开
                </Button>
              )}
            </div>
          )}

          {/* 行数不足：dsh TUI 在 ≤21 行会画丢输入行（tmux 同样复现），这里明确提示 */}
          {activeStatus?.tooSmall && (
            <div className="flex shrink-0 items-center gap-2 border-b border-warning-50 bg-warning-50 px-3 py-2 text-xs text-warning-700">
              <Icon name="warning" size="sm" />
              <span className="min-w-0 flex-1">
                窗口高度不足：终端只有 {activeStatus.rows} 行，全屏 TUI 需要至少{' '}
                {activeStatus.minRows} 行。请拉高窗口（字号已缩到最小）。
              </span>
            </div>
          )}

          {/* ③ 终端卡片：所有标签常驻，只切显隐 */}
          <div className="relative min-h-0 flex-1 px-3 pb-2 pt-2">
            <div className="relative h-full overflow-hidden rounded-xl border border-line-subtle bg-[#0b1220] shadow-e1">
              {tabs.map((tab) => {
                const isActive = tab.id === activeTabId;
                if (tab.kind === 'sftp') {
                  return (
                    <div key={tab.id} className={isActive ? 'absolute inset-0' : 'hidden'}>
                      <SftpView sessionId={tab.id} />
                    </div>
                  );
                }
                const showTerminal = tab.kind === 'local' || tab.view === 'terminal';
                return (
                  <div key={tab.id} className={isActive ? 'absolute inset-0' : 'hidden'}>
                    <div className={showTerminal ? 'h-full' : 'hidden'}>
                      <TerminalView
                        ref={(handle) => {
                          terminalRefs.current[tab.id] = handle;
                        }}
                        sessionId={tab.id}
                        port={tab.port}
                        token={tab.token}
                        active={isActive && showTerminal}
                        fontSize={fontSize}
                        onReady={() => patchTab(tab.id, { status: 'ready' })}
                        onStatus={(status) => updateStatus(tab.id, status)}
                        onExit={(message) => handleExit(tab.id, message)}
                      />
                    </div>
                    {tab.kind === 'ssh' && tab.view === 'files' && (
                      <div className="h-full bg-surface">
                        <SftpView sessionId={tab.id} />
                      </div>
                    )}
                  </div>
                );
              })}
              {tabs.length === 0 && (
                <div className="flex h-full items-center justify-center">
                  <EmptyState
                    icon="terminal"
                    title="没有打开的会话"
                    description="从左侧选择连接，或返回列表新建"
                  />
                </div>
              )}
            </div>
          </div>

          {/* ④ 状态栏 */}
          <div className="flex h-[30px] shrink-0 items-center gap-4 border-t border-line-subtle bg-surface px-4 text-[11px] text-muted">
            <span className="inline-flex items-center gap-1">
              <span
                className={[
                  'h-1.5 w-1.5 rounded-full',
                  activeStatus?.state === 'ready' ? 'bg-success-500' : 'bg-slate-300',
                ].join(' ')}
              />
              {activeStatus?.state === 'ready' ? '通道就绪' : '通道未连接'}
            </span>
            <span className="font-mono">{TERM_NAME}</span>
            <span>UTF-8</span>
            <span className="ml-auto font-mono tabular-nums">
              {activeStatus ? `${activeStatus.cols}×${activeStatus.rows}` : '—'}
            </span>
            <span className="font-mono tabular-nums">
              {activeStatus ? `行 ${activeStatus.cursorRow} 列 ${activeStatus.cursorCol}` : '—'}
            </span>
            <span className={activeStatus?.autoShrunk ? 'text-warning-700' : 'text-subtle'}>
              ⌥ 字号 {fontSize}
              {activeStatus?.autoShrunk ? `→${activeStatus.effectiveFontSize}（自适应）` : ''}
            </span>
            <span className="text-subtle">{terminalVisible ? '终端' : '文件'}</span>
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

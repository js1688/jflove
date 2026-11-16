/**
 * 远程连接状态（v1.10.0）
 *
 * 只保存「页面需要展示的东西」；凭据原文不在此 store 中出现。
 */
import { create } from 'zustand';
import {
  localTermService,
  remoteService,
  type RemoteConnectionDetail,
  type RemoteConnectionInput,
  type RemoteConnectionSummary,
} from '../services/remote-service';

export type SessionStatus = 'connecting' | 'ready' | 'failed' | 'closed';

/** 会话类型：ssh 有终端+文件；sftp 只有文件；local 只有终端 */
export type SessionKind = 'ssh' | 'sftp' | 'local';

export interface SessionTab {
  id: string;
  kind: SessionKind;
  /** SSH/SFTP 连接 ID；kind=local 时是本地终端配置 ID（0=默认终端） */
  connectionId: number;
  title: string;
  subtitle: string;
  port: number;
  token: string;
  status: SessionStatus;
  view: 'terminal' | 'files';
  jumpChain: Array<{ label: string; phase: string }>;
  message: string;
}

interface EditorState {
  mode: 'create' | 'edit';
  id?: number;
  detail?: RemoteConnectionDetail;
  /** 新建时预选的连接类型；编辑时由 detail.mode 决定 */
  connectionMode?: 'ssh' | 'sftp';
}

interface RemoteState {
  connections: RemoteConnectionSummary[];
  keyword: string;
  loading: boolean;
  error: string;
  editor: EditorState | null;
  tabs: SessionTab[];
  activeTabId: string | null;

  load: () => Promise<void>;
  setKeyword: (keyword: string) => void;
  openEditor: (mode: 'create' | 'edit', id?: number, connectionMode?: 'ssh' | 'sftp') => Promise<void>;
  closeEditor: () => void;
  saveConnection: (payload: RemoteConnectionInput) => Promise<number | null>;
  deleteConnection: (id: number) => Promise<void>;
  duplicateConnection: (id: number) => Promise<void>;

  openRemoteSession: (
    connection: Pick<RemoteConnectionSummary, 'id' | 'name' | 'host' | 'username' | 'mode'>,
  ) => Promise<void>;
  openLocalSession: (profileId: number, title: string) => Promise<void>;
  closeTab: (id: string) => Promise<void>;
  /**
   * 会话结束后处理标签（v1.10.10）。
   *
   * 只有**真正就绪过**的标签才自动关闭；连接失败/连接中的标签保留，
   * 让用户能看到 `session_failed` 给出的原因。
   *
   * @returns 被关闭的标签（用于 toast）；未关闭时返回 null
   */
  handleSessionExit: (id: string, message: string) => SessionTab | null;
  /** 会话就绪兜底：终端首次输出 / 状态事件 ready 时调用（仅 connecting → ready） */
  markSessionReady: (id: string) => void;
  setActiveTab: (id: string) => void;
  setTabView: (id: string, view: 'terminal' | 'files') => void;
  patchTab: (id: string, patch: Partial<SessionTab>) => void;
}

/** 从标签列表移除一个会话，并修正当前激活标签（自动关闭/手动关闭共用） */
function removeTab(
  state: Pick<RemoteState, 'tabs' | 'activeTabId'>,
  id: string,
): Pick<RemoteState, 'tabs' | 'activeTabId'> {
  const tabs = state.tabs.filter((item) => item.id !== id);
  const activeTabId =
    state.activeTabId === id ? (tabs[tabs.length - 1]?.id ?? null) : state.activeTabId;
  return { tabs, activeTabId };
}

export const useRemoteStore = create<RemoteState>((set, get) => ({
  connections: [],
  keyword: '',
  loading: false,
  error: '',
  editor: null,
  tabs: [],
  activeTabId: null,

  load: async () => {
    set({ loading: true, error: '' });
    try {
      const connections = await remoteService.listConnections(get().keyword);
      set({ connections });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    } finally {
      set({ loading: false });
    }
  },

  setKeyword: (keyword) => set({ keyword }),

  openEditor: async (mode, id, connectionMode) => {
    if (mode === 'edit' && id !== undefined) {
      const { connection } = await remoteService.getConnection(id);
      set({ editor: { mode, id, detail: connection } });
      return;
    }
    set({ editor: { mode, connectionMode } });
  },

  closeEditor: () => set({ editor: null }),

  saveConnection: async (payload) => {
    const { editor } = get();
    try {
      if (editor?.mode === 'edit' && editor.id !== undefined) {
        await remoteService.updateConnection(editor.id, payload);
        set({ editor: null });
        await get().load();
        return editor.id;
      }
      const { id } = await remoteService.createConnection(payload);
      set({ editor: null });
      await get().load();
      return id;
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
      return null;
    }
  },

  deleteConnection: async (id) => {
    await remoteService.deleteConnection(id);
    await get().load();
  },

  duplicateConnection: async (id) => {
    await remoteService.duplicateConnection(id);
    await get().load();
  },

  openRemoteSession: async (connection) => {
    const result = await remoteService.openSession(connection.id, 80, 24);
    const kind: SessionKind = connection.mode === 'sftp' ? 'sftp' : 'ssh';
    const tab: SessionTab = {
      id: result.session_id,
      kind,
      connectionId: connection.id,
      title: connection.name,
      subtitle: `${connection.username}@${connection.host}`,
      port: result.port,
      token: result.token,
      status: 'connecting',
      view: kind === 'sftp' ? 'files' : 'terminal',
      jumpChain: [],
      message: '',
    };
    set((state) => ({ tabs: [...state.tabs, tab], activeTabId: tab.id }));
  },

  openLocalSession: async (profileId, title) => {
    const result = await localTermService.open(profileId, 80, 24);
    const tab: SessionTab = {
      id: result.session_id,
      kind: 'local',
      connectionId: profileId,
      title,
      subtitle: '本机终端',
      port: result.port,
      token: result.token,
      status: 'connecting',
      view: 'terminal',
      jumpChain: [],
      message: '',
    };
    set((state) => ({ tabs: [...state.tabs, tab], activeTabId: tab.id }));
    // 标签页建好后再补查一次：启动失败的事件可能早于本方法返回（v1.10.7 审查 S1），
    // 若不补查，失败信息会被 patchTab 丢掉，用户只看到一直"连接中"的黑屏。
    void localTermService
      .status(tab.id)
      .then((state) => {
        if (state.status === 'failed') {
          get().patchTab(tab.id, {
            status: 'failed',
            message: state.message || '本机终端启动失败',
          });
        }
      })
      .catch(() => {
        // 补查失败不影响主流程（事件通道仍在）
      });
  },

  closeTab: async (id) => {
    if (!get().tabs.some((item) => item.id === id)) return;
    // 先摘标签再关 Python 会话：手动关闭触发的 closed 事件不能再算「自动结束」，
    // 否则会多弹一次「会话已结束」toast（事件到达时已查不到标签）。
    set((state) => removeTab(state, id));
    try {
      // Python 侧 `_h_session_close` 对 SSH 与本机会话都有效（统一从会话表取）
      await remoteService.closeSession(id);
    } catch {
      // 关闭失败不阻塞 UI
    }
  },

  handleSessionExit: (id, message) => {
    const tab = get().tabs.find((item) => item.id === id);
    // 连接失败/未就绪的标签必须保留：用户要能看到失败原因并修正配置
    if (!tab || tab.status !== 'ready') return null;
    // 会话已经结束，这里只是兜底回收 Python 侧残留（失败不影响 UI）
    void remoteService.closeSession(id).catch(() => {
      // 清理失败忽略：标签已经从界面移除
    });
    set((state) => removeTab(state, id));
    return { ...tab, message };
  },

  markSessionReady: (id) =>
    set((state) => {
      const tab = state.tabs.find((item) => item.id === id);
      if (!tab || tab.status !== 'connecting') return state;
      return {
        tabs: state.tabs.map((item) =>
          item.id === id ? { ...item, status: 'ready' } : item,
        ),
      };
    }),

  setActiveTab: (id) => set({ activeTabId: id }),

  setTabView: (id, view) =>
    set((state) => ({
      tabs: state.tabs.map((tab) => (tab.id === id ? { ...tab, view } : tab)),
    })),

  patchTab: (id, patch) =>
    set((state) => {
      // 标签可能已被关闭（迟到事件）：无命中就返回原 state，避免无意义的重渲染
      if (!state.tabs.some((tab) => tab.id === id)) return state;
      return {
        tabs: state.tabs.map((tab) => (tab.id === id ? { ...tab, ...patch } : tab)),
      };
    }),
}));

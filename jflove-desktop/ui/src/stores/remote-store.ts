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
  type RemoteGroup,
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
  groups: RemoteGroup[];
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
  createGroup: (name: string) => Promise<void>;
  deleteGroup: (id: number) => Promise<void>;

  openRemoteSession: (
    connection: Pick<RemoteConnectionSummary, 'id' | 'name' | 'host' | 'username' | 'mode'>,
  ) => Promise<void>;
  openLocalSession: (profileId: number, title: string) => Promise<void>;
  closeTab: (id: string) => Promise<void>;
  /** 会话结束（进程退出/断链）后按同一配置重开一个标签 */
  reopenTab: (id: string) => Promise<void>;
  setActiveTab: (id: string) => void;
  setTabView: (id: string, view: 'terminal' | 'files') => void;
  patchTab: (id: string, patch: Partial<SessionTab>) => void;
}

export const useRemoteStore = create<RemoteState>((set, get) => ({
  groups: [],
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
      const [groups, connections] = await Promise.all([
        remoteService.listGroups(),
        remoteService.listConnections(get().keyword),
      ]);
      set({ groups, connections });
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

  createGroup: async (name) => {
    await remoteService.createGroup(name);
    await get().load();
  },

  deleteGroup: async (id) => {
    await remoteService.deleteGroup(id);
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
    const tab = get().tabs.find((item) => item.id === id);
    if (!tab) return;
    try {
      // Python 侧 `_h_session_close` 对 SSH 与本机会话都有效（统一从会话表取）
      await remoteService.closeSession(id);
    } catch {
      // 关闭失败不阻塞 UI
    }
    set((state) => {
      const tabs = state.tabs.filter((item) => item.id !== id);
      const activeTabId =
        state.activeTabId === id ? (tabs[tabs.length - 1]?.id ?? null) : state.activeTabId;
      return { tabs, activeTabId };
    });
  },

  reopenTab: async (id) => {
    const tab = get().tabs.find((item) => item.id === id);
    if (!tab) return;
    if (tab.kind === 'local') {
      await get().openLocalSession(tab.connectionId, tab.title);
    } else {
      // SSH / SFTP：重新取一次连接配置（密码在 Python 侧，渲染层只拿摘要）
      const { connection } = await remoteService.getConnection(tab.connectionId);
      await get().openRemoteSession(connection);
    }
    await get().closeTab(id);
  },

  setActiveTab: (id) => set({ activeTabId: id }),

  setTabView: (id, view) =>
    set((state) => ({
      tabs: state.tabs.map((tab) => (tab.id === id ? { ...tab, view } : tab)),
    })),

  patchTab: (id, patch) =>
    set((state) => ({
      tabs: state.tabs.map((tab) => (tab.id === id ? { ...tab, ...patch } : tab)),
    })),
}));

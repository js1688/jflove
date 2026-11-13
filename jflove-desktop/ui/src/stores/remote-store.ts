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

export interface SessionTab {
  id: string;
  kind: 'ssh' | 'local';
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
  openEditor: (mode: 'create' | 'edit', id?: number) => Promise<void>;
  closeEditor: () => void;
  saveConnection: (payload: RemoteConnectionInput) => Promise<number | null>;
  deleteConnection: (id: number) => Promise<void>;
  duplicateConnection: (id: number) => Promise<void>;
  createGroup: (name: string) => Promise<void>;
  deleteGroup: (id: number) => Promise<void>;

  openRemoteSession: (connection: RemoteConnectionSummary) => Promise<void>;
  openLocalSession: (profileId: number, title: string) => Promise<void>;
  closeTab: (id: string) => Promise<void>;
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

  openEditor: async (mode, id) => {
    if (mode === 'edit' && id !== undefined) {
      const { connection } = await remoteService.getConnection(id);
      set({ editor: { mode, id, detail: connection } });
      return;
    }
    set({ editor: { mode } });
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
    const tab: SessionTab = {
      id: result.session_id,
      kind: 'ssh',
      connectionId: connection.id,
      title: connection.name,
      subtitle: `${connection.username}@${connection.host}`,
      port: result.port,
      token: result.token,
      status: 'connecting',
      view: 'terminal',
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
      connectionId: 0,
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

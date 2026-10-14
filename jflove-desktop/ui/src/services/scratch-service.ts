/**
 * 文本编辑器桥调用服务（v1.6.0）—— `editor.*` / `quit.*` / `file_assoc.*` 的**唯一出口**
 *
 * ## 为什么必须唯一
 *
 * 渲染层没有网络出口，也没有文件系统出口（AGENTS.md §9.6）：本版本所有本地文件读写、草稿库、
 * 原生对话框、文件关联注册**全部在 Python 侧**完成，渲染层只经 `utils/desktop-bridge.ts` 的
 * 白名单桥说话。因此**任何** `editor.*` / `quit.*` / `file_assoc.*` 调用都必须经过本文件，
 * 组件与 store 不得直接 `callBridge('editor', …)`（便于统一校验参数、统一错误语义、统一审计）。
 *
 * ## 零远端（AC-42 / §7.3）
 *
 * 本服务**不触碰** `/api/v1/*`，**不新增**任何网络请求，也不走 `http-client`。
 *
 * ## 方法名与字段名
 *
 * 逐字对齐设计 §7.1 的桥方法表（参数键名、返回字段名都与 Python 侧 `src/bridge/editor.py` 约定一致）。
 * 渲染层**不打印**路径与内容明文（§7.4：只用 id / 数字 / 长度做调试）。
 */

import { callBridge, isBridgeReady, onBridgeEvent } from '../utils/desktop-bridge';
import { DETECT_AND_READ_MAX_PATHS } from '../types/scratch';
import type {
  ClearAllDraftsResult,
  DetectAndReadResult,
  DraftDiscardResult,
  DraftListResult,
  DraftPaneMeta,
  DraftSaveResult,
  DraftSavedPayload,
  EditorNotice,
  FileAssocSetResult,
  FileAssocState,
  OkResult,
  OpenDialogParams,
  OpenDialogResult,
  OpenPathsPayload,
  PaneCaret,
  QuitPendingResult,
  QuitRequestedPayload,
  ReadItem,
  ScratchPane,
  ShortcutsFileV1,
  ShortcutsSaveResult,
  StatPathResult,
  SystemDefaultsResult,
  WriteTextParams,
  WriteTextResult,
} from '../types/scratch';

/** 桥就绪判定（渲染层只想"问一句"时用它，避免各组件自己 import desktop-bridge） */
export function isEditorBridgeReady(): boolean {
  return isBridgeReady();
}

/**
 * 把窗格状态转成草稿桥方法要求的 `pane` 字段（§7.1 #6 / #8 的混合命名）。
 *
 * `caret` / `scrollTop` 允许由调用方覆盖（CM6 的实时值比 store 里缓存的更新）。
 */
export function toDraftPaneMeta(
  pane: ScratchPane,
  override?: { caret?: PaneCaret; scrollTop?: number },
): DraftPaneMeta {
  return {
    id: pane.id,
    title: pane.title,
    path: pane.path,
    language: pane.language,
    encoding: pane.encoding,
    lineEnd: pane.lineEnding,
    read_only: pane.readOnly,
    dirty: pane.dirty,
    geometry: { ...pane.geometry },
    caret: { ...(override?.caret ?? pane.caret) },
    scroll_top: override?.scrollTop ?? pane.scrollTop,
    font_size: pane.fontSize,
    show_line_numbers: pane.showLineNumbers,
    word_wrap: pane.wordWrap,
  };
}

/**
 * `editor.*` / `quit.*` / `file_assoc.*` 的桥调用集合。
 *
 * 错误语义（§7.1「通用约定」）：Python 侧抛异常 → 桥回 `{ok:false, error}` → 这里 reject（Error）。
 * 业务上的"取消 / 不存在 / 单条失败"**不是**错误，走正常返回值。
 */
export const scratchService = {
  // ── 原生对话框（GUI 线程，§7.1 #1）──

  /** 原生文件对话框（`mode` 缺省 `open`；取消 = `cancelled:true`，不是错误） */
  async openDialog(params: OpenDialogParams = {}): Promise<OpenDialogResult> {
    const resp = await callBridge<OpenDialogResult>('editor', 'openDialog', {
      mode: params.mode ?? 'open',
      ...(params.purpose ? { purpose: params.purpose } : {}),
      ...(params.defaultName ? { defaultName: params.defaultName } : {}),
      ...(params.filters ? { filters: params.filters } : {}),
    });
    return { cancelled: Boolean(resp?.cancelled), paths: resp?.paths, path: resp?.path ?? null };
  },

  /** 选择若干本机文件（打开入口；取消返回空数组） */
  async pickFiles(): Promise<string[]> {
    const resp = await scratchService.openDialog({ mode: 'open' });
    return resp.cancelled ? [] : (resp.paths ?? []);
  },

  /** 挑选单个文件用于**只读**用途（工具箱 diff 的第二段「本地文件」；取消返回 null） */
  async pickReadOnlyFile(): Promise<string | null> {
    const resp = await scratchService.openDialog({ mode: 'open', purpose: 'pick' });
    if (resp.cancelled) return null;
    return resp.paths?.[0] ?? null;
  },

  /** 指定保存位置（草稿形态首次保存；取消返回 null ⇒ 磁盘零文件、dirty 保持） */
  async pickSaveTarget(defaultName?: string): Promise<string | null> {
    const resp = await scratchService.openDialog({ mode: 'save', defaultName });
    return resp.cancelled ? null : (resp.path ?? null);
  },

  // ── 读取 / 写入（§7.1 #2~#5）──

  /**
   * 类型校验 + 读取（§5.2 / AC-49~AC-53）。
   *
   * 参数非法（非数组 / 超过 64 条）→ **抛错**；单条失败不抛，进 `items[].ok:false`。
   * 空数组直接返回空结果（不必打扰 Python 侧）。
   */
  async detectAndRead(paths: string[]): Promise<DetectAndReadResult> {
    if (!Array.isArray(paths)) throw new Error('detectAndRead 的 paths 必须是数组');
    if (paths.length > DETECT_AND_READ_MAX_PATHS) {
      throw new Error(`detectAndRead 一次最多 ${DETECT_AND_READ_MAX_PATHS} 条路径`);
    }
    if (paths.length === 0) return { items: [] };
    const resp = await callBridge<DetectAndReadResult>('editor', 'detectAndRead', { paths });
    return { items: resp?.items ?? [] };
  },

  /** 只读读取单条（用于 diff 第二段，§7.1 #4） */
  async readTextOnly(path: string): Promise<ReadItem> {
    return callBridge<ReadItem>('editor', 'readTextOnly', { path });
  },

  /**
   * 写回用户文件（**工作线程**；只读窗格会被 Python 侧再兜一层拒绝）。
   *
   * ⚠ 只有 `path != null` 的窗格才允许调用（AC-40）；草稿形态一律走 `draftSave`。
   */
  async writeText(params: WriteTextParams): Promise<WriteTextResult> {
    return callBridge<WriteTextResult>('editor', 'writeText', {
      path: params.path,
      text: params.text,
      encoding: params.encoding,
      lineEnding: params.lineEnding,
      ...(params.expectedSha256 ? { expectedSha256: params.expectedSha256 } : {}),
    });
  },

  /** 探测路径状态（不存在返回 `{exists:false}`，**不抛**） */
  async statPath(path: string): Promise<StatPathResult> {
    return callBridge<StatPathResult>('editor', 'statPath', { path });
  },

  // ── 草稿库（§7.1 #6~#9 / §6.3）──

  /** 写单个窗格草稿（Python 侧先写内容文件再原子替换索引，§6.3.2） */
  async draftSave(pane: DraftPaneMeta, content: string): Promise<DraftSaveResult> {
    return callBridge<DraftSaveResult>('editor', 'draftSave', { pane, content });
  },

  /** 丢弃单个窗格草稿（幂等：不存在也返回 ok） */
  async draftDiscard(paneId: string): Promise<DraftDiscardResult> {
    return callBridge<DraftDiscardResult>('editor', 'draftDiscard', { paneId });
  },

  /** 列出可还原的窗格（索引损坏返回可用条目 + `warnings`，**不抛**） */
  async draftList(): Promise<DraftListResult> {
    const resp = await callBridge<DraftListResult>('editor', 'draftList', {});
    return {
      cleanExit: Boolean(resp?.cleanExit),
      entries: resp?.entries ?? [],
      warnings: resp?.warnings ?? [],
    };
  },

  /** 清空全部草稿（设置页「清空草稿」，二次确认后调用；**不影响**用户已保存文件） */
  async clearAllDrafts(): Promise<ClearAllDraftsResult> {
    return callBridge<ClearAllDraftsResult>('editor', 'clearAllDrafts', {});
  },

  // ── 退出握手（§7.1 #10~#13 / §6.4）──

  /** 上报脏窗格数量（退出握手前置） */
  async reportDirty(count: number): Promise<OkResult> {
    return callBridge<OkResult>('editor', 'reportDirty', { count });
  },

  /** 退出回执：`retain=true` 保留下次还原的草稿（GUI 线程内联执行） */
  async confirmQuit(retain: boolean): Promise<OkResult> {
    return callBridge<OkResult>('editor', 'confirmQuit', { retain });
  },

  /** 取消退出（清 Python 侧 `_quitting` 状态） */
  async cancelQuit(): Promise<OkResult> {
    return callBridge<OkResult>('editor', 'cancelQuit', {});
  },

  /** 查询是否有待处理的退出请求（`quit` 命名空间，§7.1 #13） */
  async quitPending(): Promise<QuitPendingResult> {
    const resp = await callBridge<QuitPendingResult>('quit', 'pending', {});
    return { pending: Boolean(resp?.pending), dirtyCount: resp?.dirtyCount ?? 0 };
  },

  // ── 文件关联（§7.1 #14~#16 / §4.3）──

  /** 取关联状态（manifest 缺失时 `supported:[]` + `note`，**不抛**） */
  async fileAssocGet(): Promise<FileAssocState> {
    const resp = await callBridge<FileAssocState>('file_assoc', 'get', {});
    return {
      selected: resp?.selected ?? [],
      supported: resp?.supported ?? [],
      platform: resp?.platform ?? 'other',
      osRegistered: Boolean(resp?.osRegistered),
      note: resp?.note ?? '',
    };
  },

  /** 保存勾选（含不支持的扩展名 → Python 侧抛错，**不静默过滤**） */
  async fileAssocSet(selected: string[]): Promise<FileAssocSetResult> {
    return callBridge<FileAssocSetResult>('file_assoc', 'set', { selected });
  },

  /** 打开系统「默认应用」设置页（GUI 线程；平台不支持时 `{ok:false, hint}`） */
  async fileAssocOpenSystemDefaults(): Promise<SystemDefaultsResult> {
    return callBridge<SystemDefaultsResult>('file_assoc', 'openSystemDefaults', {});
  },

  // ── 快捷键偏好落盘（§4.2.4）──

  /**
   * 读取 `USER_DATA_DIR/storage/shortcuts.json`。
   *
   * ⚠ **设计缺口说明**：§7.1 的桥方法表**没有**列出快捷键偏好的读写方法（只有 §4.2.4 规定了
   * 存储位置与结构）。为满足"渲染层禁止直接访问文件系统 / 禁止 localStorage（R-1 实测默认 profile
   * 为 off-the-record）"，这里按命名空间一致性补 `editor.shortcutsLoad` / `editor.shortcutsSave`
   * 两个方法 —— **需要 Python 侧 `editor_bridge.HANDLERS` 同步登记**，否则桥会回
   * `{ok:false, error:"未登记的方法：…"}`。已在交付说明中登记为待对齐项。
   */
  async shortcutsLoad(): Promise<ShortcutsFileV1> {
    const resp = await callBridge<ShortcutsFileV1>('editor', 'shortcutsLoad', {});
    return {
      version: resp?.version ?? 0,
      bindings: resp?.bindings ?? {},
      blockSelectModifier: resp?.blockSelectModifier,
    };
  },

  /** 写回快捷键偏好（**只在用户点「保存」/「一键重置」时**调用，§4.2.4 不做防抖自动写） */
  async shortcutsSave(file: ShortcutsFileV1): Promise<ShortcutsSaveResult> {
    return callBridge<ShortcutsSaveResult>('editor', 'shortcutsSave', {
      version: file.version,
      bindings: file.bindings,
      ...(file.blockSelectModifier ? { blockSelectModifier: file.blockSelectModifier } : {}),
    });
  },

  // ── Python → 渲染层事件（§7.1 事件表，共 4 个 topic）──

  /**
   * `editor.openPaths`：命令行路径 / 单实例转发。
   *
   * ⚠ 订阅本身**不要求**桥握手完成（`desktop-bridge.ts` 的注册表是模块级的），
   * 所以启动期可用它做「事件早于 UI」的兜底 —— 见 `utils/scratch/open-paths-queue.ts`。
   */
  onEditorOpenPaths(handler: (payload: OpenPathsPayload) => void): () => void {
    return onBridgeEvent('editor.openPaths', (raw) => {
      const payload = parseOpenPathsPayload(raw);
      if (payload) handler(payload);
    });
  },

  /** `shell.quitRequested`：弹确认（保存 / 放弃 / 取消 + 全局保留选择）→ 回 confirmQuit / cancelQuit */
  onShellQuitRequested(handler: (payload: QuitRequestedPayload) => void): () => void {
    return onBridgeEvent('shell.quitRequested', (raw) => {
      const timeoutMs = (raw as { timeoutMs?: unknown } | null)?.timeoutMs;
      if (typeof timeoutMs !== 'number') return;
      handler({ timeoutMs });
    });
  },

  /** `editor.draftSaved`：草稿写成功（仅更新 UI 提示，不改变内容） */
  onEditorDraftSaved(handler: (payload: DraftSavedPayload) => void): () => void {
    return onBridgeEvent('editor.draftSaved', (raw) => {
      const data = raw as { paneId?: unknown; at?: unknown } | null;
      if (typeof data?.paneId !== 'string' || typeof data.at !== 'number') return;
      handler({ paneId: data.paneId, at: data.at });
    });
  },

  /** `editor.notice`：类型校验拒绝 / 编码不可判定 / 打开失败 → Toast（文案不含完整路径与内容） */
  onEditorNotice(handler: (notice: EditorNotice) => void): () => void {
    return onBridgeEvent('editor.notice', (raw) => {
      const notice = parseEditorNotice(raw);
      if (notice) handler(notice);
    });
  },
};

/**
 * 校验 `editor.openPaths` 的 payload（桥的另一端在 Python，按**不可信输入**处理）。
 *
 * 只保留非空字符串路径；`paths` 不是数组或过滤后为空 ⇒ 视为无效事件（丢弃）。
 */
function parseOpenPathsPayload(raw: unknown): OpenPathsPayload | null {
  const data = raw as { paths?: unknown; source?: unknown } | null;
  if (!data || !Array.isArray(data.paths)) return null;
  const paths = data.paths.filter(
    (item): item is string => typeof item === 'string' && item.trim().length > 0,
  );
  if (paths.length === 0) return null;
  const source = data.source === 'single_instance' ? 'single_instance' : 'cli';
  return { paths, source };
}

/** 校验 `editor.notice` 的 payload（字段类型不合法即丢弃，避免 UI 收到半截对象） */
function parseEditorNotice(raw: unknown): EditorNotice | null {
  const data = raw as { level?: unknown; code?: unknown; message?: unknown } | null;
  if (!data) return null;
  const level = data.level === 'warn' || data.level === 'error' ? data.level : 'info';
  if (typeof data.code !== 'string' || typeof data.message !== 'string') return null;
  return { level, code: data.code, message: data.message };
}

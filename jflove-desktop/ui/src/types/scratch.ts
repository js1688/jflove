/**
 * 文本编辑器（v1.6.0）领域模型与桥 DTO 类型 —— **跨施工面契约**
 *
 * 依据：设计文档 v1.6.0 §4.1.2（窗格状态模型）/ §6.3.1（index.json）/ §7.1（桥方法入参出参）。
 *
 * ## 命名口径（**不要"顺手归一"**）
 *
 * 字段名**逐字对齐设计原文**，哪怕同一份契约里命名风格不统一：
 *   - `draftSave` 的 `pane` 字段是 `lineEnd` + `read_only` + `scroll_top` + `font_size` +
 *     `show_line_numbers` + `word_wrap` 的混合写法（§7.1 #6）；
 *   - `ReadItem`（`detectAndRead` 的返回）用的是 `lineEnding` + `readOnly`（§7.1 #2）。
 * 二者**不可互换**；要改先回看设计并同步 Python 侧 `src/bridge/editor.py`。
 *
 * ## 安全边界（AGENTS.md §9.6 / §9.14）
 *
 * 本文件只描述**数据形状**：渲染层不持有文件字节、不拼路径、不打日志。
 * `text` / `path` 只在渲染层内存中流转，**禁止**写入 console、禁止进 localStorage（R-1 实测默认 profile 为 off-the-record）。
 */

// ── 语言无关的基础类型 ──

/** 文本编码（§4.1.2；由 Python 侧 `text_detect_service` 判定后回填） */
export type TextEncoding = 'utf-8' | 'utf-8-bom' | 'gbk' | 'utf-16le' | 'utf-16be';

/** 换行符（保存时按原样回写，AC-20 的 BOM / CRLF 往返） */
export type LineEnding = 'lf' | 'crlf';

/** 窗格视图模式（§4.1.2） */
export type ScratchViewMode = 'edit' | 'preview' | 'split' | 'table' | 'tree';

/** 窗格几何（§4.1.2 的 `geometry` 字段；`z` 为层级，越大越靠前） */
export interface PaneGeometry {
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
  /** 最小化标记 */
  min: boolean;
  /** 最大化标记 */
  maxed: boolean;
}

/** 光标位置（1 基，用于草稿还原） */
export interface PaneCaret {
  line: number;
  col: number;
}

// ── 窗格模型（§4.1.2）──

/**
 * 单个编辑窗格的完整状态（草稿持久化的字段与之对齐）。
 *
 * 注意：**这里没有 `text` 字段** —— 正文由 CM6 实例持有（`ScratchEditorHost`），
 * 未挂载窗格的初始正文走 `stores/scratch-store.ts` 的 `initialTexts` 暂存区，
 * 避免同一份内容在渲染层存两份（AC-60 的内存判据）。
 */
export interface ScratchPane {
  /** `crypto.randomUUID()`，同时是草稿内容文件名 `pane-<id>.txt` */
  id: string;
  /** 显示名：文件名 或「未命名」 */
  title: string;
  /** 本地目标绝对路径；`null` = **草稿形态**（永不写用户文件系统） */
  path: string | null;
  /** 语言 id（见 `config/scratch-types.ts` 的 `TYPES`），可手动切换 */
  language: string;
  /** 识别出的语言（手动切换后仍保留原识别值，供「恢复自动」） */
  detected: string;
  viewMode: ScratchViewMode;
  encoding: TextEncoding;
  lineEnding: LineEnding;
  /** F8 阈值降级 / `.diff` 类只读 */
  readOnly: boolean;
  dirty: boolean;
  geometry: PaneGeometry;
  /** 1 基，用于还原 */
  caret: PaneCaret;
  scrollTop: number;
  fontSize: number;
  showLineNumbers: boolean;
  wordWrap: boolean;
  /** 懒挂载标记：`false` = 只登记不建 CM6 实例（§4.1.3） */
  mounted: boolean;
}

// ── 桥 DTO：`editor.*`（§7.1）──

/** `editor.openDialog` 的入参（`mode` 缺省为 `open`） */
export interface OpenDialogParams {
  mode?: 'open' | 'save';
  /** `pick` = 只读挑选（工具箱 diff 第二段的「本地文件」入口） */
  purpose?: 'pick';
  defaultName?: string;
  filters?: { name: string; patterns: string[] }[];
}

/** `editor.openDialog` 的返回：`open` 回 `paths`，`save` 回 `path`（取消 = `cancelled:true`，不是错误） */
export interface OpenDialogResult {
  cancelled: boolean;
  paths?: string[];
  path?: string | null;
}

/** `detectAndRead` 成功条目（§7.1 #2，字段名逐字对齐） */
export interface ReadItemOk {
  path: string;
  ok: true;
  name: string;
  text: string;
  size: number;
  language: string;
  encoding: TextEncoding;
  lineEnding: LineEnding;
  readOnly: boolean;
  warnings: string[];
}

/** 单条失败原因（§7.1 #2；单条失败**不抛**，进 `items[].ok:false`） */
export type ReadFailureReason =
  | 'binary'
  | 'too_large'
  | 'unknown_encoding'
  | 'not_found'
  | 'io_error';

/** `detectAndRead` 失败条目 */
export interface ReadItemErr {
  path: string;
  ok: false;
  reason: ReadFailureReason;
  /** 用户可见文案（**不含完整路径**，§7.4） */
  message: string;
  size?: number;
}

export type ReadItem = ReadItemOk | ReadItemErr;

/** `editor.detectAndRead` 的返回（每批 ≤ 64 条） */
export interface DetectAndReadResult {
  items: ReadItem[];
}

/** 一次 `detectAndRead` 调用的上限（§7.1 #2「≤ 64 条」） */
export const DETECT_AND_READ_MAX_PATHS = 64;

/** `editor.writeText` 的入参（`path` 必填，`expectedSha256` 用于「保存前文件被外部改动」检测） */
export interface WriteTextParams {
  path: string;
  text: string;
  encoding: TextEncoding;
  lineEnding: LineEnding;
  expectedSha256?: string;
}

/** `editor.writeText` 的返回 */
export interface WriteTextResult {
  ok: true;
  size: number;
  mtime: number;
  sha256: string;
}

/** `editor.statPath` 的返回（不存在返回 `{exists:false}`，**不抛**） */
export interface StatPathResult {
  exists: boolean;
  size: number;
  mtime: number;
  isDir: boolean;
}

/**
 * 草稿索引里的窗格元数据 = `editor.draftSave` 的 `pane` 字段（§7.1 #6 / #8）。
 *
 * ⚠ 字段名是设计原文的**混合命名**（`lineEnd` 驼峰 + 其余下划线），不要改。
 */
export interface DraftPaneMeta {
  id: string;
  title: string;
  path: string | null;
  language: string;
  encoding: TextEncoding;
  lineEnd: LineEnding;
  read_only: boolean;
  dirty: boolean;
  geometry: PaneGeometry;
  caret: PaneCaret;
  scroll_top: number;
  font_size: number;
  show_line_numbers: boolean;
  word_wrap: boolean;
}

/** `editor.draftSave` 的返回 */
export interface DraftSaveResult {
  ok: true;
  at: number;
}

/** `editor.draftList` 的返回（索引损坏时带 `warnings`，**不抛**） */
export interface DraftListResult {
  /** `false` = 上次非干净退出 ⇒ 触发还原（§6.3.2） */
  cleanExit: boolean;
  entries: DraftPaneMeta[];
  warnings?: string[];
}

/** `editor.draftDiscard` / `editor.clearAllDrafts` 的返回 */
export interface DraftDiscardResult {
  ok: true;
}

export interface ClearAllDraftsResult {
  ok: true;
  removed: number;
}

/** `editor.reportDirty` / `editor.confirmQuit` / `editor.cancelQuit` 的返回 */
export interface OkResult {
  ok: true;
}

/** `quit.pending` 的返回（退出握手前置） */
export interface QuitPendingResult {
  pending: boolean;
  dirtyCount: number;
}

// ── 桥 DTO：`file_assoc.*`（§7.1 #14~#16）──

/** `file_assoc.get` 的返回（manifest 缺失时 `supported:[]` + `note`，**不抛**） */
export interface FileAssocState {
  selected: string[];
  supported: string[];
  platform: 'win32' | 'linux' | 'other';
  osRegistered: boolean;
  note: string;
}

/** `file_assoc.set` 的返回（不支持的扩展名 → 抛错，**不静默过滤**） */
export interface FileAssocSetResult {
  ok: true;
  selected: string[];
}

/** `file_assoc.openSystemDefaults` 的返回 */
export interface SystemDefaultsResult {
  ok: boolean;
  hint: string;
}

// ── 桥 DTO：快捷键偏好（§4.2.4 落盘结构）──

/** 块选择修饰键的**可取值**（§6.1.2：默认 `Alt`；改 `Ctrl` 后 `Alt` 退化为普通选择） */
export type BlockSelectModifier = 'Alt' | 'Ctrl';

/**
 * `USER_DATA_DIR/storage/shortcuts.json` 的结构（§4.2.4）。
 *
 * `bindings` **只存与默认值不同的项**（overrides 语义）；`version` 不匹配或键名不存在时
 * 由渲染层忽略该项并回默认（**不阻塞启动**）。读写都由 Python 侧完成 —— 渲染层不碰文件系统。
 */
export interface ShortcutsFileV1 {
  version: number;
  bindings: Record<string, string>;
  blockSelectModifier?: BlockSelectModifier;
}

/** 快捷键落盘结果 */
export interface ShortcutsSaveResult {
  ok: true;
}

// ── 桥 DTO：Python → 渲染层事件（§7.1 事件表）──

/** `editor.openPaths` 的 payload（`source` 区分命令行 / 单实例转发） */
export interface OpenPathsPayload {
  paths: string[];
  source: 'cli' | 'single_instance';
}

/** `shell.quitRequested` 的 payload */
export interface QuitRequestedPayload {
  timeoutMs: number;
}

/** `editor.draftSaved` 的 payload */
export interface DraftSavedPayload {
  paneId: string;
  at: number;
}

/** `editor.notice` 的 payload（文案不含完整路径与内容） */
export interface EditorNotice {
  level: 'info' | 'warn' | 'error';
  code: string;
  message: string;
}

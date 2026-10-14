/**
 * CodeMirror 6 离线分包（`public/vendor/editor.bundle.js`）的**手写**全局类型声明
 *
 * 为什么是手写：CM6 的 20+ 个包**不进** `ui/package.json`（设计文档 §6.1.1 / DS-7），
 * 源码里没有任何 `import '@codemirror/...'`，所以 TypeScript 拿不到任何 CM6 类型。
 * 代价是「类型不会随版本自动更新」⇒ **版本变化时必须同步更新本文件**（见下）。
 *
 * ⚠ **同步义务**：本文件与 `public/vendor/editor.version.json` /
 * `scripts/editor-bundle/package.json` 的钉死版本是一组。
 * 只要 `editor.version.json` 的 `codemirror`（= `@codemirror/view` 版本）或 `merge` 变化，
 * **必须**重新核对本文件的签名（构造函数参数、静态 facet、实例方法名），
 * 至少确认 `scripts/build-codemirror-bundle.mjs` 的自检键仍在。
 * 版本不一致只告警不阻断（`load-editor-bundle.ts`），但类型不符会让 `tsc --noEmit` 变红。
 *
 * 类型策略：CM6 的内核类型（`Extension` / `StateField` / `ChangeSet` …）是**不透明**的 ——
 * 本文件用 `Extension = unknown` 之类的宽松标记换取「不引入 npm 依赖」；
 * 只有宿主真正会读写的成员（`doc` / `selection` / `dispatch` / `facet()` …）才给出具体签名。
 */

/* ============================ 基础不透明类型 ============================ */

/**
 * CM6 扩展（由厂商函数返回的不透明值）。
 *
 * 用 `unknown` 而非 `any`：任何东西都能放进 `extensions: [...]`（赋值方向成立），
 * 但**不能**随便读它的属性 —— 想读就得先收窄类型，这与"手写最小签名"的定位一致。
 */
export type Extension = unknown;

/** 文档变更描述（`{from, to, insert}` 等），透传给 `dispatch` */
export type ChangeSpec = unknown;

/** 状态效果（`StateEffect` 的实例数组），透传给 `dispatch` */
export type StateEffectSpec = unknown;

/**
 * 状态分片（facet）：`Facet.of(value)` 产出一个扩展。
 * 静态 facet 都在 `EditorState` / `EditorView` 上。
 */
export interface StateFacet<Value> {
  of(value: Value | ((state: EditorState) => Value)): Extension;
}

/* ============================ 文档与选区 ============================ */

/** 只读文本（CM6 的 `Text`）。宿主常用：长度、行数、按行/按位置取值、整体转字符串 */
export interface Text {
  /** 字符数（UTF-16 code unit；状态栏"字符数"口径见设计 §4.1.4，按码点自行展开） */
  readonly length: number;
  /** 行数（至少 1） */
  readonly lines: number;
  /** 第 n 行（1 基） */
  line(n: number): { from: number; to: number; number: number; text: string; length: number };
  /** 包含 pos 的那一行 */
  lineAt(pos: number): { from: number; to: number; number: number; text: string; length: number };
  /** 取 [from, to) 的文本 */
  slice(from?: number, to?: number): string;
  /** 取 [from, to) 的文本（同 slice） */
  sliceString(from?: number, to?: number): string;
  toString(): string;
}

/** 单个选区段（`EditorSelection.range`） */
export interface SelectionRange {
  readonly anchor: number;
  readonly head: number;
  readonly from: number;
  readonly to: number;
  readonly empty: boolean;
}

/** 选区（可能含多个 range —— 矩形选择正是多 range，设计 §6.1.2） */
export interface EditorSelection {
  readonly ranges: readonly SelectionRange[];
  readonly main: SelectionRange;
  readonly mainIndex: number;
}

/* ============================ 事务 ============================ */

/** 一次状态变更（`EditorState.update()` 的产物，由 `EditorView.dispatch` 提交） */
export interface Transaction {
  readonly startState: EditorState;
  readonly state: EditorState;
  /** 文档是否被改动（auto-save 判据） */
  readonly docChanged: boolean;
  /** 本次是否设置了选区 */
  readonly selectionSet: boolean;
  readonly newSelection: EditorSelection;
  readonly changes: ChangeSpec;
  readonly effects: readonly StateEffectSpec[];
  readonly scrollIntoView: boolean;
}

/** `dispatch` / `EditorState.update` 的入参 */
export interface TransactionSpec {
  changes?: ChangeSpec;
  selection?: EditorSelection | { anchor: number; head?: number };
  effects?: StateEffectSpec | readonly StateEffectSpec[];
  scrollIntoView?: boolean;
  filter?: boolean;
  annotations?: unknown;
  userEvent?: string;
}

/** `EditorView.updateListener` 的回调入参 */
export interface ViewUpdate {
  readonly view: EditorView;
  readonly state: EditorState;
  readonly transactions: readonly Transaction[];
  readonly docChanged: boolean;
  readonly selectionSet: boolean;
  readonly focusChanged: boolean;
  readonly heightChanged: boolean;
  readonly geometryChanged: boolean;
  readonly viewportChanged: boolean;
}

/* ============================ EditorState ============================ */

export interface EditorStateConfig {
  doc?: string | Text;
  selection?: EditorSelection | { anchor: number; head?: number };
  extensions?: Extension | readonly Extension[];
}

export interface EditorState {
  readonly doc: Text;
  readonly selection: EditorSelection;
  /** 只读（`.diff` 只读预览 / 大文件降级，设计 §6.2.2） */
  readonly readOnly: boolean;
  /** 取某个 facet 的当前值 */
  facet<Value>(facet: StateFacet<Value>): Value;
  /** 从当前状态派生新状态（不提交给视图；视图侧用 `view.dispatch`） */
  update(...specs: readonly TransactionSpec[]): Transaction;
  /** 取 [from, to) 的文档文本 */
  sliceDoc(from?: number, to?: number): string;
  replaceSelection(text: string): Transaction;
  toJSON(): unknown;
}

export interface EditorStateStatic {
  new (config: EditorStateConfig): EditorState;
  create(config: EditorStateConfig): EditorState;
  /** 只读分片：`EditorState.readOnly.of(true)` */
  readonly readOnly: StateFacet<boolean>;
  /** 缩进宽度：`EditorState.tabSize.of(2)` */
  readonly tabSize: StateFacet<number>;
  /** 多选区开关（矩形选择 / 多光标依赖它） */
  readonly allowMultipleSelections: StateFacet<boolean>;
  /** 换行符分片 */
  readonly lineSeparator: StateFacet<string | undefined>;
}

/* ============================ EditorView ============================ */

export interface EditorViewConfig {
  state?: EditorState;
  parent?: HTMLElement;
  root?: Document | ShadowRoot;
  /** 传入后事务**不会**自动回写视图，需由调用方 `view.update([tr])`（React 受控用法） */
  dispatch?: (tr: Transaction) => void;
  scrollTo?: unknown;
}

export interface EditorView {
  readonly state: EditorState;
  readonly dom: HTMLElement;
  readonly contentDOM: HTMLElement;
  readonly scrollDOM: HTMLElement;
  readonly hasFocus: boolean;
  /** 生成并提交事务（既驱动状态，也驱动视图重绘） */
  dispatch(...specs: readonly TransactionSpec[]): void;
  /** 把已算出的事务交给视图（`dispatch` 为受控模式时用它） */
  update(transactions: readonly Transaction[]): void;
  setState(state: EditorState): void;
  focus(): void;
  destroy(): void;
  requestMeasure(): void;
  coordsAtPos(pos: number): { left: number; right: number; top: number; bottom: number } | null;
  posAtCoords(coords: { x: number; y: number }): number | null;
}

export interface EditorViewStatic {
  new (config: EditorViewConfig): EditorView;
  /** 状态更新监听（状态栏 F10 全字段由它驱动，设计 §4.1.1） */
  readonly updateListener: StateFacet<(update: ViewUpdate) => void>;
  /** 自动换行扩展（`EditorView.lineWrapping`） */
  readonly lineWrapping: Extension;
  /** 可编辑分片 */
  readonly editable: StateFacet<boolean>;
  readonly contentAttributes: StateFacet<Record<string, string>>;
  readonly editorAttributes: StateFacet<Record<string, string>>;
  /** 主题：`EditorView.theme(spec, {dark})` */
  theme(spec: Record<string, unknown>, options?: { dark?: boolean }): Extension;
  baseTheme(spec: Record<string, unknown>): Extension;
  /** DOM 事件处理：`EditorView.domEventHandlers({ copy: (e, view) => … })`（矩形选择复制口径，设计 §6.1.2） */
  domEventHandlers(
    handlers: Record<string, (event: Event, view: EditorView) => boolean | void>,
  ): Extension;
}

/* ============================ 快捷键 / 优先级 ============================ */

/** 单条键绑定（`@codemirror/view` 的 keymap） */
export interface KeyBinding {
  key: string;
  mac?: string;
  run?: (view: EditorView) => boolean;
  shift?: (view: EditorView) => boolean;
  preventDefault?: boolean;
  scope?: string;
  any?: (view: EditorView, event: KeyboardEvent) => boolean;
}

/** 扩展优先级（`@codemirror/state` 的 `Prec`，设计 §4.1.1「`Prec.high` 顺序」） */
export interface PrecApi {
  highest(ext: Extension): Extension;
  high(ext: Extension): Extension;
  default(ext: Extension): Extension;
  low(ext: Extension): Extension;
  lowest(ext: Extension): Extension;
}

/* ============================ 语言入口 ============================ */

/** 语言入口：返回一段扩展（`LanguageSupport` 或 `StreamLanguage` 定义），按需调用 */
export type LanguageLoader = () => Extension;

/**
 * 语言入口表：key = `scratch-types.ts` 的 `language` id（含别名）。
 * 纯文本（`text` / `txt` / `csv` / `tsv`）返回**空扩展数组**，宿主不必判空。
 */
export interface JFLoveCmLanguages {
  json: LanguageLoader;
  jsonc: LanguageLoader;
  markdown: LanguageLoader;
  sql: LanguageLoader;
  xml: LanguageLoader;
  svg: LanguageLoader;
  html: LanguageLoader;
  yaml: LanguageLoader;
  yml: LanguageLoader;
  toml: LanguageLoader;
  ini: LanguageLoader;
  conf: LanguageLoader;
  properties: LanguageLoader;
  css: LanguageLoader;
  python: LanguageLoader;
  javascript: LanguageLoader;
  jsx: LanguageLoader;
  typescript: LanguageLoader;
  tsx: LanguageLoader;
  java: LanguageLoader;
  cpp: LanguageLoader;
  php: LanguageLoader;
  rust: LanguageLoader;
  go: LanguageLoader;
  shell: LanguageLoader;
  sh: LanguageLoader;
  bat: LanguageLoader;
  powershell: LanguageLoader;
  ps1: LanguageLoader;
  diff: LanguageLoader;
  patch: LanguageLoader;
  text: LanguageLoader;
  txt: LanguageLoader;
  csv: LanguageLoader;
  tsv: LanguageLoader;
  /** 语言 id 是运行期数据，允许下标访问（未命中时宿主必须自行兜底为 `text`） */
  [languageId: string]: LanguageLoader;
}

/* ============================ 格式化器 ============================ */

/** `sql-formatter@15` 的选项（只列宿主会用到的） */
export interface SqlFormatOptions {
  language?: string;
  tabWidth?: number;
  useTabs?: boolean;
  keywordCase?: 'preserve' | 'upper' | 'lower';
  identifierCase?: 'preserve' | 'upper' | 'lower';
  dataTypeCase?: 'preserve' | 'upper' | 'lower';
  functionCase?: 'preserve' | 'upper' | 'lower';
  indentStyle?: 'standard' | 'tabularLeft' | 'tabularRight';
  logicalOperatorNewline?: 'before' | 'after';
  expressionWidth?: number;
  linesBetweenQueries?: number | 'preserve';
  denseOperators?: boolean;
  newlineBeforeSemicolon?: boolean;
}

/** `xml-formatter@3` 的选项（只列宿主会用到的） */
export interface XmlFormatOptions {
  indentation?: string;
  collapseContent?: boolean;
  lineSeparator?: string;
  throwOnFailure?: boolean;
  filter?: (node: unknown) => boolean;
}

export interface JFLoveCmFormatters {
  /** sql-formatter 的 `format`（同时以 `sqlFormat` 暴露别名） */
  sql(text: string, options?: SqlFormatOptions): string;
  sqlFormat(text: string, options?: SqlFormatOptions): string;
  /** `supportedDialects`：方言下拉用 */
  sqlDialects: readonly string[];
  /** xml-formatter 的默认导出（同时以 `xmlFormat` 暴露别名） */
  xml(text: string, options?: XmlFormatOptions): string;
  xmlFormat(text: string, options?: XmlFormatOptions): string;
}

/* ============================ diff ============================ */

export interface MergeEditorConfig {
  doc?: string | Text;
  extensions?: Extension | readonly Extension[];
  selection?: EditorSelection | { anchor: number; head?: number };
}

export interface MergeViewConfig {
  a: MergeEditorConfig | EditorState;
  b: MergeEditorConfig | EditorState;
  parent?: HTMLElement;
  orientation?: 'a-b' | 'b-a';
  highlightChanges?: boolean;
  gutter?: boolean;
  collapseUnchanged?: { margin?: number; minSize?: number };
  diffConfig?: unknown;
}

/** `@codemirror/merge` 的 `MergeView`（左右两个 `EditorView`，设计 §6.1.3） */
export interface MergeView {
  readonly a: EditorView;
  readonly b: EditorView;
  readonly dom: HTMLElement;
  destroy(): void;
  reconfigure(config: MergeViewConfig): void;
}

export interface MergeViewStatic {
  new (config: MergeViewConfig): MergeView;
}

/* ============================ 命令 / 搜索 / 语言 命名空间 ============================ */

/** CM6 命名空间逃生口：未在上面列出的小众成员从这里取（返回类型未知，需自行收窄） */
export interface CodeMirrorNamespace {
  readonly [member: string]: unknown;
}

/** `@codemirror/commands`（历史 / 默认键位） */
export interface CommandsNamespace extends CodeMirrorNamespace {
  readonly defaultKeymap: readonly KeyBinding[];
  readonly standardKeymap: readonly KeyBinding[];
  readonly historyKeymap: readonly KeyBinding[];
  readonly emacsStyleKeymap: readonly KeyBinding[];
  readonly history: (config?: unknown) => Extension;
  readonly undo: (view: EditorView) => boolean;
  readonly redo: (view: EditorView) => boolean;
  readonly undoDepth: (state: EditorState) => number;
  readonly indentWithTab: KeyBinding;
  /** 注释切换（类型表 `commentToggle` 能力） */
  readonly toggleComment: (view: EditorView) => boolean;
  readonly lineComment: (view: EditorView) => boolean;
  readonly blockComment: (view: EditorView) => boolean;
  readonly indentMore: (view: EditorView) => boolean;
  readonly indentLess: (view: EditorView) => boolean;
  readonly cursorLineUp: (view: EditorView) => boolean;
  readonly cursorLineDown: (view: EditorView) => boolean;
}

/** `@codemirror/search`（查找替换面板） */
export interface SearchNamespace extends CodeMirrorNamespace {
  readonly searchKeymap: readonly KeyBinding[];
  readonly openSearchPanel: (view: EditorView) => boolean;
  readonly closeSearchPanel: (view: EditorView) => boolean;
  readonly findNext: (view: EditorView) => boolean;
  readonly findPrevious: (view: EditorView) => boolean;
  readonly replaceNext: (view: EditorView) => boolean;
  readonly replaceAll: (view: EditorView) => boolean;
  readonly search: (config?: unknown) => Extension;
  readonly highlightSelectionMatches: (config?: unknown) => Extension;
}

/** `@codemirror/language`（折叠 / 括号匹配 / 缩进 / 高亮） */
export interface LanguageNamespace extends CodeMirrorNamespace {
  readonly foldGutter: (config?: unknown) => Extension;
  readonly foldKeymap: readonly KeyBinding[];
  readonly codeFolding: (config?: unknown) => Extension;
  readonly foldAll: (view: EditorView) => boolean;
  readonly unfoldAll: (view: EditorView) => boolean;
  readonly bracketMatching: (config?: unknown) => Extension;
  readonly indentOnInput: () => Extension;
  readonly indentUnit: StateFacet<string>;
  readonly syntaxHighlighting: (highlighter: unknown, options?: unknown) => Extension;
  readonly defaultHighlightStyle: unknown;
  readonly StreamLanguage: { define(spec: unknown): Extension };
}

/** `@codemirror/autocomplete`（补全） */
export interface AutocompleteNamespace extends CodeMirrorNamespace {
  readonly autocompletion: (config?: unknown) => Extension;
  readonly closeBrackets: () => Extension;
  readonly closeBracketsKeymap: readonly KeyBinding[];
  readonly completionKeymap: readonly KeyBinding[];
}

/** 原始模块命名空间集合（同时挂到 `window.cmModules`，设计 §3.1 第 21 行） */
export interface JFLoveCmModules {
  state: CodeMirrorNamespace;
  view: CodeMirrorNamespace;
  commands: CommandsNamespace;
  search: SearchNamespace;
  language: LanguageNamespace;
  autocomplete: AutocompleteNamespace;
  merge: CodeMirrorNamespace;
  /** `@codemirror/legacy-modes` 的**原始**模式对象（需经 `StreamLanguage.define()` 包装） */
  legacyModes: {
    toml: unknown;
    properties: unknown;
    diff: unknown;
    shell: unknown;
    powerShell: unknown;
    readonly [mode: string]: unknown;
  };
}

/* ============================ 顶层 API ============================ */

/** `extensions.*`：常用扩展的快捷入口（等价于从 `modules.*` 自取） */
export interface JFLoveCmExtensions {
  /** 矩形选择（**`@codemirror/view` 的内置导出，不是 `EditorView.rectangularSelection`**，设计 §6.1.2） */
  rectangularSelection: (config?: { eventFilter?: (event: MouseEvent) => boolean }) => Extension;
  drawSelection: (config?: unknown) => Extension;
  highlightActiveLine: () => Extension;
  highlightActiveLineGutter: () => Extension;
  highlightSpecialChars: () => Extension;
  lineNumbers: () => Extension;
  dropCursor: (config?: unknown) => Extension;
  crosshairCursor: (config?: unknown) => Extension;
  scrollPastEnd: () => Extension;
  placeholder: (content: string) => Extension;
  /**
   * 键位分片（`@codemirror/view` 的 `keymap`）。
   *
   * **写入侧**（宿主）就是普通数组：`keymap.of([...bindings])`。
   *
   * ⚠ **读取侧的真实形状是"数组的数组"**（2026-10-14 用真包实测，审查 M3 的副产物）：
   * `Facet.define()` 没有 combine 函数时，facet 值就是**各扩展输入值的数组**，
   * 而每个输入值本身又是 `KeyBinding[]` ⇒ `state.facet(keymap)` 实际得到
   * `readonly (readonly KeyBinding[])[]`，**外层数组按扩展优先级排序**（`Prec.highest` 在前）。
   * 读键位表必须先按该形状收窄再 `flat()`（见
   * `components/scratch/__tests__/scratch-block-select-keymap.test.ts`）。
   * 一个类型无法同时表达两侧，这里以**写入侧**为准（宿主代码量更大）。
   */
  keymap: StateFacet<readonly KeyBinding[]>;
  highlightSelectionMatches: (config?: unknown) => Extension;
  bracketMatching: (config?: unknown) => Extension;
  foldGutter: (config?: unknown) => Extension;
  indentOnInput: () => Extension;
  syntaxHighlighting: (highlighter: unknown, options?: unknown) => Extension;
  defaultHighlightStyle: unknown;
  StreamLanguage: { define(spec: unknown): Extension };
  /** `@codemirror/state` 的 `Prec` */
  Prec: PrecApi;
}

/** 内核实际版本（打包时由构建脚本写入；与 `editor.version.json` 同源） */
export interface JFLoveCmVersion {
  /** `@codemirror/view` 版本（内核/UI 层，手写类型的同步基准） */
  codemirror: string;
  merge: string;
  sqlFormatter: string;
  xmlFormatter: string;
}

/**
 * `window.__JFLOVE_CM__` 的完整形状。
 * 由 `jflove-desktop/scripts/build-codemirror-bundle.mjs` 的 IIFE 副作用挂载。
 */
export interface JFLoveCmApi {
  readonly version: JFLoveCmVersion;
  readonly EditorState: EditorStateStatic;
  readonly EditorView: EditorViewStatic;
  /** `@codemirror/merge` 的 `MergeView`（diff 视图用） */
  readonly MergeView: MergeViewStatic;
  /** `@codemirror/merge` 的原始命名空间（`merge.MergeView` 亦可用） */
  readonly merge: CodeMirrorNamespace;
  readonly languages: JFLoveCmLanguages;
  /** `languages` 的可用 id 列表（便于自检 / 下拉） */
  readonly languageIds: readonly string[];
  readonly extensions: JFLoveCmExtensions;
  readonly formatters: JFLoveCmFormatters;
  readonly modules: JFLoveCmModules;
}

declare global {
  interface Window {
    /** 由 `public/vendor/editor.bundle.js` 挂载（懒加载，见 `utils/scratch/load-editor-bundle.ts`） */
    __JFLOVE_CM__?: JFLoveCmApi;
    /** 原始 CM6 模块命名空间（同 `__JFLOVE_CM__.modules`） */
    cmModules?: JFLoveCmModules;
  }
}

export {};

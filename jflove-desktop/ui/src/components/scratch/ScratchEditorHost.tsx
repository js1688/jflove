/**
 * CM6 编辑器**实例宿主**（设计 v1.6.0 §4.1.1 / §4.1.3 / §6.1.2 / §6.1.3 / §14.2 步骤 7）
 *
 * ## 职责（只做这四件事）
 *
 * 1. **懒挂载**：`pane.mounted === true` **且**宿主矩形进入视口（`IntersectionObserver`）时，
 *    才 `await loadEditorBundle()`（`window.__JFLOVE_CM__`）并建 `EditorView`；
 *    未满足条件时只渲染骨架，内存占用 = 一个 React 空壳（§4.1.3，AC-60）。
 * 2. **生命周期**：`view.destroy()` 只在组件卸载 / `pane.id` 变化时调用（**不泄漏**）；
 *    可见性翻转**不**拆实例（否则滚动一下就把撤销历史丢了）。
 * 3. **能力接线**：
 *    - 语言注入（`config/scratch-types.ts` 的 `language` id；**未知语言回落 `text`**）；
 *    - 行号 / 自动换行 / 字号 / 只读（`pane.readOnly`：大文件降级与 `.diff` 都走它）；
 *    - 块选择 `rectangularSelection({ eventFilter })`，修饰键由
 *      `shortcut-store.blockSelectModifier` 派生；**复制口径自实现**（`block-select.ts`，AC-11）；
 *    - `updateListener` → `markDirty` + 状态统计广播（F10 全字段，§4.1.4）；
 *    - 正文读取器注册（`registerScratchContentAccessor`，草稿 / 自动保存读它）。
 * 4. **会话注册表**（本文件导出）：把活着的实例暴露给工具栏 / 状态栏 / diff 视图
 *    （`getScratchEditorSession` / `subscribeScratchEditorStats` / `useScratchEditorStats`）。
 *    —— store **不持有正文**（§4.1.2），所以跨组件取正文必须经这张表。
 *
 * ## 使用约定（与同事的接口）
 *
 * ```tsx
 * // components/scratch/ScratchPane.tsx 里：一个窗格**只挂一个**宿主
 * <ScratchEditorHost pane={pane} />
 * ```
 *
 * ⚠ **不要再套第二层**：`MarkdownSplitView` 的 split 态内部已经自带宿主，
 * 若外面同时挂了宿主，一个窗格会出现两个 CM6 实例（内容不同步）。
 *
 * ## 安全（AGENTS.md §9.6 / §9.14 / R-1）
 *
 * - **不打印文档内容与路径**：错误文案只有"内核加载失败"这类与内容无关的说明；
 * - **不碰 localStorage**、不发网络请求、不读文件系统（本文件连 `scratchService` 都不 import）；
 * - CM6 内核**只从运行期离线包取**（`utils/scratch/load-editor-bundle.ts`），
 *   源码里**没有任何** `import '@codemirror/...'`（§6.1.1）。
 */

import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { selectBlockSelectModifier, useShortcutStore } from '../../stores/shortcut-store';
import { registerScratchContentAccessor, takeInitialText, useScratchStore } from '../../stores/scratch-store';
import { useThemeStore } from '../../stores/theme-store';
import type { EditorView, Extension, JFLoveCmApi, ViewUpdate } from '../../types/codemirror-bundle';
import type { PaneCaret, ScratchPane } from '../../types/scratch';
import { blockSelectCopy, isBlockSelection } from '../../utils/scratch/block-select';
import { loadEditorBundle } from '../../utils/scratch/load-editor-bundle';

/* ============================================================================
 * 会话注册表（导出给工具栏 / 状态栏 / diff 视图）
 * ========================================================================== */

/** 状态栏口径（§4.1.4：行:列 / 选中字符数 / 总字符数，**全部按码点**） */
export interface ScratchEditorStats {
  /** 1 基行号 */
  line: number;
  /** 1 基列号（按**码点**计数） */
  col: number;
  /** 选中字符数（`[...selectedText].length`，无选中 = 0） */
  selectedChars: number;
  /** 总字符数（`[...doc].length`，**含换行符**，U-14 定稿） */
  totalChars: number;
}

/** 一次挂载期内的编辑器会话（宿主卸载即注销） */
export interface ScratchEditorSession {
  paneId: string;
  /** 原始 CM6 视图（工具栏要"从离线包取能力"时用 `api`） */
  view: EditorView;
  /** 该实例对应的离线包 API（`formatters.*` / `modules.search` 在这里取） */
  api: JFLoveCmApi;
  /** 实时正文（store 不缓存正文 ⇒ 这是跨组件取正文的唯一入口） */
  getText(): string;
  getStats(): ScratchEditorStats;
  /** 1 基光标（草稿还原口径） */
  getCaret(): PaneCaret;
  getScrollTop(): number;
  /** 整体替换正文（工具箱纯函数变换后回写；会触发 `markDirty`） */
  setText(next: string): void;
  focus(): void;
  /** 打开 CM6 查找面板（工具箱第 1 项） */
  openSearchPanel(): boolean;
  /** 跳到 1 基行号并选中该行（工具箱第 1 项"跳转行"） */
  revealLine(line: number): boolean;
}

const SESSIONS = new Map<string, ScratchEditorSession>();
const STATS_LISTENERS = new Map<string, Set<(stats: ScratchEditorStats) => void>>();

/** 取某个窗格的编辑器会话；未挂载（懒挂载未触发 / 已卸载）返回 `null` */
export function getScratchEditorSession(paneId: string): ScratchEditorSession | null {
  return SESSIONS.get(paneId) ?? null;
}

/** 当前活着实例的窗格 id 列表（diff 的"另一窗格"来源用它） */
export function listScratchEditorPaneIds(): string[] {
  return Array.from(SESSIONS.keys());
}

/** 已加载的离线包 API（未加载返回 `null`，**不触发加载**） */
export function getLoadedEditorApi(): JFLoveCmApi | null {
  const api = typeof window === 'undefined' ? undefined : window.__JFLOVE_CM__;
  return api && typeof api.EditorView === 'function' ? api : null;
}

/**
 * 按 §4.1.4 口径算状态栏全字段（纯读，不改状态）。
 *
 * - 列 = 光标位于该行的第几个**码点**（1 基）；
 * - 选中字符数 = 各非空 range 文本按码点计数之和；
 * - 总字符数 = 整篇文档按码点计数（**含换行符**）。
 */
export function computeEditorStats(view: EditorView): ScratchEditorStats {
  const state = view.state;
  const head = state.selection.main.head;
  const lineInfo = state.doc.lineAt(head);
  const col = [...lineInfo.text.slice(0, Math.max(0, head - lineInfo.from))].length + 1;
  let selected = '';
  for (const range of state.selection.ranges) {
    if (range.empty || range.from === range.to) continue;
    selected += state.sliceDoc(Math.min(range.from, range.to), Math.max(range.from, range.to));
  }
  return {
    line: lineInfo.number,
    col,
    selectedChars: [...selected].length,
    totalChars: [...state.doc.toString()].length,
  };
}

/** 1 基光标（草稿还原口径） */
export function caretOfView(view: EditorView): PaneCaret {
  const stats = computeEditorStats(view);
  return { line: stats.line, col: stats.col };
}

/** 广播某窗格的最新统计（订阅者：状态栏） */
function emitStats(paneId: string): void {
  const listeners = STATS_LISTENERS.get(paneId);
  const session = SESSIONS.get(paneId);
  if (!listeners || listeners.size === 0 || !session) return;
  const stats = session.getStats();
  listeners.forEach((listener) => listener(stats));
}

/**
 * 订阅某窗格的编辑器统计（**订阅时立即回调一次当前值**）。
 *
 * 返回取消订阅函数；窗格尚未挂载时订阅也是安全的（拿不到值就不回调）。
 */
export function subscribeScratchEditorStats(
  paneId: string,
  listener: (stats: ScratchEditorStats) => void,
): () => void {
  let set = STATS_LISTENERS.get(paneId);
  if (!set) {
    set = new Set();
    STATS_LISTENERS.set(paneId, set);
  }
  set.add(listener);
  const session = SESSIONS.get(paneId);
  if (session) listener(session.getStats());
  return () => {
    const current = STATS_LISTENERS.get(paneId);
    if (!current) return;
    current.delete(listener);
    if (current.size === 0) STATS_LISTENERS.delete(paneId);
  };
}

/**
 * React Hook：订阅某窗格的编辑器统计（状态栏 `ScratchPaneStatusBar` 直接用这个）。
 *
 * 未挂载窗格返回 `null` ⇒ 状态栏显示骨架而不是 0（避免"看起来在读空文档"）。
 */
export function useScratchEditorStats(paneId: string): ScratchEditorStats | null {
  const [stats, setStats] = useState<ScratchEditorStats | null>(
    () => SESSIONS.get(paneId)?.getStats() ?? null,
  );
  useEffect(() => {
    setStats(SESSIONS.get(paneId)?.getStats() ?? null);
    return subscribeScratchEditorStats(paneId, setStats);
  }, [paneId]);
  return stats;
}

/* ============================================================================
 * 内部工具
 * ========================================================================== */

/** `@codemirror/state` 的 `Compartment` 实例（手写 .d.ts 未给签名，这里收窄到用得到的两个方法） */
interface CompartmentInstance {
  of(extension: Extension): Extension;
  reconfigure(extension: Extension): unknown;
}

interface CompartmentStatic {
  new (): CompartmentInstance;
}

/** 从 `modules.state` 取 `Compartment`（CM6 一定有；取不到时退化为"不重配"，不影响首建） */
function getCompartment(api: JFLoveCmApi): CompartmentStatic | null {
  const candidate = (api.modules.state as { Compartment?: unknown }).Compartment;
  return typeof candidate === 'function' ? (candidate as CompartmentStatic) : null;
}

/** 语言 id → 扩展；**未知语言回落 `text`**（AC-51：纯文本打开、可手动切语言） */
function languageExtensionFor(api: JFLoveCmApi, language: string): Extension {
  const loader = api.languages[language];
  if (typeof loader !== 'function') return api.languages.text();
  try {
    return loader();
  } catch {
    return api.languages.text();
  }
}

/**
 * 块选择的修饰键过滤器（§6.1.2）。
 *
 * 同一个 `eventFilter` **只认一个修饰键**：改 `Ctrl` 后 `Alt` 自动退化为普通（流式）选择，
 * 天然满足 AC-12，不需要另写"排除"逻辑。
 */
function blockSelectFilter(modifier: string): (event: MouseEvent) => boolean {
  return modifier === 'Ctrl'
    ? (event: MouseEvent): boolean => event.ctrlKey
    : (event: MouseEvent): boolean => event.altKey;
}

/** 编辑器主题（字号 + 等宽字体 + 高度撑满；**不新建 css 文件**，走 CM6 的 theme 扩展） */
function editorThemeFor(api: JFLoveCmApi, fontSize: number, dark: boolean): Extension {
  return api.EditorView.theme(
    {
      '&': { fontSize: `${fontSize}px`, height: '100%', backgroundColor: 'transparent' },
      '.cm-scroller': {
        fontFamily:
          'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
        lineHeight: '1.5',
      },
      '.cm-content': { caretColor: 'currentColor' },
      '&.cm-focused': { outline: 'none' },
      '.cm-gutters': { backgroundColor: 'transparent', border: 'none' },
    },
    { dark },
  );
}

/** 矩形选择的复制处理器（AC-11 的宿主侧接线；口径本体在 `blockSelectCopy`） */
function copyHandlerFor(api: JFLoveCmApi): Extension {
  return api.extensions.Prec.high(
    api.EditorView.domEventHandlers({
      copy: (event: Event, view: EditorView): boolean => {
        const { doc, selection } = view.state;
        if (!isBlockSelection(doc, selection.ranges)) return false;
        const text = blockSelectCopy(doc, selection.ranges);
        if (!text) return false;
        const clipboard = event as ClipboardEvent;
        if (!clipboard.clipboardData) return false;
        clipboard.clipboardData.setData('text/plain', text);
        return true;
      },
    }),
  );
}

/** 组装基础扩展（不含 compartment 管理的那些） */
function baseExtensions(api: JFLoveCmApi, onUpdate: (update: ViewUpdate) => void): Extension[] {
  return [
    api.extensions.highlightActiveLineGutter(),
    api.extensions.highlightSpecialChars(),
    api.extensions.drawSelection(),
    api.extensions.dropCursor(),
    api.extensions.crosshairCursor(),
    api.extensions.highlightActiveLine(),
    api.extensions.highlightSelectionMatches(),
    api.modules.commands.history(),
    api.EditorState.allowMultipleSelections.of(true),
    api.EditorState.tabSize.of(2),
    api.modules.language.indentUnit.of('  '),
    api.extensions.indentOnInput(),
    api.extensions.syntaxHighlighting(api.extensions.defaultHighlightStyle, { fallback: true }),
    api.extensions.bracketMatching(),
    api.extensions.foldGutter(),
    api.modules.search.search({ top: true }),
    api.modules.autocomplete.closeBrackets(),
    api.modules.autocomplete.autocompletion(),
    api.extensions.keymap.of([
      ...api.modules.commands.defaultKeymap,
      ...api.modules.commands.historyKeymap,
      ...api.modules.search.searchKeymap,
      ...api.modules.language.foldKeymap,
      ...api.modules.autocomplete.closeBracketsKeymap,
      ...api.modules.autocomplete.completionKeymap,
      api.modules.commands.indentWithTab,
    ]),
    api.EditorView.updateListener.of(onUpdate),
  ];
}

/** 建会话对象（宿主卸载时从注册表删除） */
function createSession(paneId: string, view: EditorView, api: JFLoveCmApi): ScratchEditorSession {
  return {
    paneId,
    view,
    api,
    getText: () => view.state.doc.toString(),
    getStats: () => computeEditorStats(view),
    getCaret: () => caretOfView(view),
    getScrollTop: () => view.scrollDOM.scrollTop,
    setText: (next: string) => {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: next } });
    },
    focus: () => view.focus(),
    openSearchPanel: () => {
      const open = api.modules.search.openSearchPanel;
      return typeof open === 'function' ? open(view) : false;
    },
    revealLine: (line: number) => {
      const target = Math.min(Math.max(1, Math.trunc(line)), view.state.doc.lines);
      const info = view.state.doc.line(target);
      view.dispatch({ selection: { anchor: info.from, head: info.to }, scrollIntoView: true });
      view.focus();
      return true;
    },
  };
}

/** 视口相交（懒挂载条件①）；`IntersectionObserver` 不可用时视为可见（宁可早建也不留空壳） */
function useElementVisible(ref: RefObject<HTMLElement | null>, enabled: boolean): boolean {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!enabled) {
      setVisible(false);
      return;
    }
    const element = ref.current;
    if (!element || typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setVisible(true);
      },
      { rootMargin: '160px' },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, enabled]);
  return visible;
}

/* ============================================================================
 * 组件
 * ========================================================================== */

/** 宿主 props：`pane` 是同事（`ScratchPane`）挂载本组件的**唯一约定** */
export interface ScratchEditorHostProps {
  /** 窗格状态（来自 `scratch-store`；正文**不在**其中） */
  pane: ScratchPane;
  /** 可选：附加类名（外壳布局微调用） */
  className?: string;
}

type HostStatus = 'idle' | 'loading' | 'ready' | 'error';

/** compartment 集合（重配时用；`null` = 该 compartment 不可用，跳过重配） */
interface HostCompartments {
  language: CompartmentInstance | null;
  readOnly: CompartmentInstance | null;
  lineNumbers: CompartmentInstance | null;
  wrap: CompartmentInstance | null;
  theme: CompartmentInstance | null;
  blockSelect: CompartmentInstance | null;
}

/**
 * CM6 实例宿主。
 *
 * @example
 * // 由同事的 `ScratchPane` 挂载（**只挂一个**）
 * <ScratchEditorHost pane={pane} />
 */
export function ScratchEditorHost({ pane, className }: ScratchEditorHostProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const apiRef = useRef<JFLoveCmApi | null>(null);
  const compartmentsRef = useRef<HostCompartments | null>(null);
  const creatingRef = useRef(false);
  const destroyedRef = useRef(false);
  const releaseAccessorRef = useRef<(() => void) | null>(null);
  const initialTextRef = useRef('');

  const [status, setStatus] = useState<HostStatus>('idle');
  const [errorText, setErrorText] = useState<string | null>(null);

  // 每次渲染刷新 ref：create 逻辑是异步的，必须读"最新"的窗格状态，而不是首渲染闭包里的值
  const paneRef = useRef(pane);
  paneRef.current = pane;
  const markDirty = useScratchStore((state) => state.markDirty);
  const markDirtyRef = useRef(markDirty);
  markDirtyRef.current = markDirty;
  const modifier = useShortcutStore(selectBlockSelectModifier);
  const modifierRef = useRef(modifier);
  modifierRef.current = modifier;
  const dark = useThemeStore((state) => state.resolved === 'dark');
  const darkRef = useRef(dark);
  darkRef.current = dark;

  const visible = useElementVisible(containerRef, pane.mounted);
  const canCreate = pane.mounted && visible;

  /* ── 生命周期：只在 `pane.id` 变化 / 组件卸载时销毁实例（可见性翻转**不**拆实例） ── */
  useEffect(() => {
    const paneId = pane.id;
    destroyedRef.current = false;
    // 未挂载窗格的初始正文由 store 暂存，这里**取走即删**（全生命周期只交付一次）
    initialTextRef.current = takeInitialText(paneId) ?? '';
    return () => {
      destroyedRef.current = true;
      const view = viewRef.current;
      viewRef.current = null;
      apiRef.current = null;
      compartmentsRef.current = null;
      if (releaseAccessorRef.current) {
        releaseAccessorRef.current();
        releaseAccessorRef.current = null;
      }
      SESSIONS.delete(paneId);
      STATS_LISTENERS.delete(paneId);
      if (view) view.destroy();
    };
  }, [pane.id]);

  /* ── 懒挂载：条件满足时建实例（幂等；本 effect **不做** destroy） ── */
  useEffect(() => {
    if (!canCreate) return;
    if (viewRef.current || creatingRef.current) return;
    const container = containerRef.current;
    if (!container) return;

    creatingRef.current = true;
    setStatus('loading');

    void (async () => {
      try {
        const api = await loadEditorBundle();
        if (destroyedRef.current || viewRef.current) return;

        const current = paneRef.current;
        const Compartment = getCompartment(api);
        const compartments: HostCompartments = {
          language: Compartment ? new Compartment() : null,
          readOnly: Compartment ? new Compartment() : null,
          lineNumbers: Compartment ? new Compartment() : null,
          wrap: Compartment ? new Compartment() : null,
          theme: Compartment ? new Compartment() : null,
          blockSelect: Compartment ? new Compartment() : null,
        };

        const extensions: Extension[] = [copyHandlerFor(api)];
        for (const extension of baseExtensions(api, (update: ViewUpdate) => {
          if (update.docChanged) markDirtyRef.current(current.id, true);
          if (update.docChanged || update.selectionSet) emitStats(current.id);
        })) {
          extensions.push(extension);
        }

        // ① 语言（compartment：手动切语言即时生效）
        const languageExtensions = languageExtensionFor(api, current.language);
        extensions.push(compartments.language ? compartments.language.of(languageExtensions) : languageExtensions);

        // ② 只读（大文件降级 / `.diff` 强制只读）+ 可编辑性
        const readOnlyExtensions: Extension[] = [
          api.EditorState.readOnly.of(current.readOnly),
          api.EditorView.editable.of(!current.readOnly),
        ];
        extensions.push(
          compartments.readOnly ? compartments.readOnly.of(readOnlyExtensions) : readOnlyExtensions,
        );

        // ③ 行号（compartment：工具栏可开关）
        const gutterExtensions: Extension = current.showLineNumbers ? api.extensions.lineNumbers() : [];
        extensions.push(
          compartments.lineNumbers ? compartments.lineNumbers.of(gutterExtensions) : gutterExtensions,
        );

        // ④ 自动换行（compartment）
        const wrapExtensions: Extension = current.wordWrap ? api.EditorView.lineWrapping : [];
        extensions.push(compartments.wrap ? compartments.wrap.of(wrapExtensions) : wrapExtensions);

        // ⑤ 字号 / 主题（compartment）
        const themeExtensions = editorThemeFor(api, current.fontSize, darkRef.current);
        extensions.push(compartments.theme ? compartments.theme.of(themeExtensions) : themeExtensions);

        // ⑥ 块选择（compartment：修饰键变化时重配）
        const blockSelectExtensions = api.extensions.rectangularSelection({
          eventFilter: blockSelectFilter(modifierRef.current),
        });
        extensions.push(
          compartments.blockSelect
            ? compartments.blockSelect.of(blockSelectExtensions)
            : blockSelectExtensions,
        );

        const state = api.EditorState.create({ doc: initialTextRef.current, extensions });
        const view = new api.EditorView({ state, parent: container });

        viewRef.current = view;
        apiRef.current = api;
        compartmentsRef.current = compartments;

        // 光标与滚动还原（草稿 §6.3.2 的 1 基 行/列）
        const doc = view.state.doc;
        const lineNo = Math.min(Math.max(1, current.caret.line), doc.lines);
        const lineInfo = doc.line(lineNo);
        const anchor = Math.min(lineInfo.from + Math.max(0, current.caret.col - 1), lineInfo.to);
        view.dispatch({ selection: { anchor } });
        if (current.scrollTop > 0) view.scrollDOM.scrollTop = current.scrollTop;

        // 注册：① 会话表（工具栏 / 状态栏 / diff）② 正文读取器（草稿与自动保存）
        SESSIONS.set(current.id, createSession(current.id, view, api));
        releaseAccessorRef.current = registerScratchContentAccessor(current.id, () => ({
          text: view.state.doc.toString(),
          caret: caretOfView(view),
          scrollTop: view.scrollDOM.scrollTop,
        }));

        setStatus('ready');
        emitStats(current.id);
      } catch {
        // 与内容无关的失败说明（不打印任何文档内容 / 路径）
        setErrorText('编辑器内核加载失败：离线包 vendor/editor.bundle.js 缺失或损坏。');
        setStatus('error');
      } finally {
        creatingRef.current = false;
      }
    })();
  }, [canCreate, pane.id]);

  /* ── 属性变化 → compartment 重配（语言 / 只读 / 行号 / 换行 / 字号 / 块选择修饰键） ── */
  useEffect(() => {
    const view = viewRef.current;
    const api = apiRef.current;
    const compartments = compartmentsRef.current;
    if (!view || !api || !compartments) return;

    const effects: unknown[] = [];
    if (compartments.language) {
      effects.push(compartments.language.reconfigure(languageExtensionFor(api, pane.language)));
    }
    if (compartments.readOnly) {
      effects.push(
        compartments.readOnly.reconfigure([
          api.EditorState.readOnly.of(pane.readOnly),
          api.EditorView.editable.of(!pane.readOnly),
        ]),
      );
    }
    if (compartments.lineNumbers) {
      effects.push(
        compartments.lineNumbers.reconfigure(pane.showLineNumbers ? api.extensions.lineNumbers() : []),
      );
    }
    if (compartments.wrap) {
      effects.push(compartments.wrap.reconfigure(pane.wordWrap ? api.EditorView.lineWrapping : []));
    }
    if (compartments.theme) {
      effects.push(compartments.theme.reconfigure(editorThemeFor(api, pane.fontSize, dark)));
    }
    if (compartments.blockSelect) {
      effects.push(
        compartments.blockSelect.reconfigure(
          api.extensions.rectangularSelection({ eventFilter: blockSelectFilter(modifier) }),
        ),
      );
    }
    if (effects.length > 0) view.dispatch({ effects });
  }, [pane.id, pane.language, pane.readOnly, pane.showLineNumbers, pane.wordWrap, pane.fontSize, dark, modifier]);

  const wrapperClass = ['relative h-full w-full overflow-hidden', className]
    .filter((item): item is string => Boolean(item))
    .join(' ');

  return (
    <div className={wrapperClass}>
      {/* CM6 容器的**子节点全部由 CM6 自己管理**：React 不往里塞任何东西（避免节点打架） */}
      <div ref={containerRef} className="h-full w-full overflow-hidden" />
      {status === 'error' ? (
        <div
          role="alert"
          className="absolute inset-0 flex items-center justify-center bg-amber-500/10 p-4 text-center text-xs text-amber-600"
        >
          {errorText}
        </div>
      ) : null}
      {status === 'idle' || status === 'loading' ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs opacity-50">
          {pane.mounted ? '正在加载编辑器内核…' : '窗格未挂载'}
        </div>
      ) : null}
    </div>
  );
}

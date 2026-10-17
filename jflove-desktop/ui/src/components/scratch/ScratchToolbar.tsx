/**
 * 编辑面板工具栏：**语言切换 + 视图切换 + 工具箱 12 项 + 字号/行号/自动换行**
 * （设计 v1.6.1 §4.2 组件树 / §4.1.6 工具箱 / §6.1.3 四类视图）
 *
 * ## 分层：本文件只做"派发"
 *
 * - **纯逻辑**在 `utils/scratch/toolbox.ts`（12 项文本处理，全部纯函数，可单测）；
 * - **正文读写**经 `ScratchEditorHost` 导出的会话表
 *   （`getScratchEditorSession(tabId)`）—— store 不持有正文（§4.1.2），
 *   所以这里是"取正文 → 纯函数变换 → 整体回写"的唯一通路；
 * - **视图切换**走 `scratch-store.setViewMode`（能力表校验，不可用则不动，§4.1.2）。
 *
 * ## props 口径（v1.6.1 变更）
 *
 * v1.6.0 由 `ScratchPane` 把整个 `pane` 传进来；v1.6.1 组件族改为**单面板**，
 * 只收 `tabId`，标签状态（语言 / 视图 / 字号 / 行号 / 换行 / 只读 / 挂载）由本组件
 * 自己从 store 订阅 —— 面板可能同时存在多个（非激活项 `hidden`），逐层传对象会失效订阅。
 *
 * ## 只读标签页的硬约束（AC-19 / AC-52）
 *
 * `tab.readOnly === true` 时：所有会改正文的按钮**禁用**（大文件降级与 `.diff` 都走这条），
 * 视图切换与字号/行号/自动换行仍可用（它们不改内容）。
 *
 * ## diff 的落点说明
 *
 * `viewMode` 只有 `edit/preview/split/table/tree`（`types/scratch.ts`），**没有 `diff`**
 * —— 所以工具箱第 11 项用**覆盖层**呈现 `DiffView`：本组件渲染一个
 * `absolute inset-0` 的容器，依赖编辑面板外壳（`EditorPanel`）是定位祖先。
 *
 * ## 安全（AGENTS.md §9.14 / R-1）
 *
 * 不 `console.*` 文档内容与路径（`no-console` 只允许 warn/error，本文件一条都不打）；
 * 不使用 localStorage；所有文件 I/O 都经 `scratchService` 的白名单桥。
 *
 * ## v1.6.1 本轮新增的三件事（设计 §15.2 / §15.3 / §15.4）
 *
 * 1. **AC-74⑦ / B1**：语言显示名改用 `config/scratch-types.ts` 的 `languageLabel`（按 `ext` 优先），
 *    修掉"`.txt` 显示成 CSV"的误标；**SQL 方言下拉**显示名改用 `sqlDialectLabel`（中文 / 专有名词 + `（id）`）。
 * 2. **AC-75② / 闭环 BL-51**：消费 `stores/scratch-ui-store.ts` 的一次性面板请求
 *    （`Ctrl+K` ⇒ 开工具箱弹框；`Ctrl+Shift+D` ⇒ 开 diff 覆盖层）。
 * 3. **AC-76**：工具箱**10 项选完即关**、`sql`/`font` 保持打开，并补 `Esc` + 点弹框外部关闭；
 *    三条关闭路径的**唯一副作用**都是 `setMenuOpen(false)`（不碰正文）。
 *
 * ## v1.6.2 本轮的四处改动（需求 F11 / F12 / F14 / F15）
 *
 * 1. **修「点了完全没反应」**（AC-80 / DS-49）：关框委托从 `onPointerUpCapture` 搬到容器的
 *    `onClick`（**冒泡**）。真机探针 `poc/editor-probes/r21-pointerup-close-semantics.py` 证实：
 *    在 `pointerup` 监听里卸载按钮 ⇒ Chromium **不再派发**这次 `click` ⇒ 被点按钮的 `onClick`
 *    永不执行（受害面 = `sql`/`font` 之外的 10 项、约 29 个按钮）。搬成 `click` 冒泡后，
 *    被点按钮的 `onClick` 必然**先**执行、随后才关框（同一真机探针的 `click-bubble` 模式）。
 * 2. **动作结果可见**（AC-82 / DS-50）：正文类动作写回时把**首个差异位置**滚进视口并定位光标，
 *    并给中文结果文案（含行数 / 字符数）—— 治"作用文字不明显"。
 * 3. **覆盖层铺满编辑面板**（AC-83 / DS-51）：根元素**不再是定位祖先**（`relative` 下移到内容层，
 *    工具箱弹框仍在内容层里定位），于是 diff / 跨标签搜索两个覆盖层（挂根元素下）的包含块
 *    回到 `EditorPanel` 根 ⇒ 不再只有一行高。
 * 4. **工具箱项快捷键**（AC-88 ~ AC-91 / DS-53）：消费 `panelRequest('toolbox-item', tabId, itemId)`，
 *    执行该项的**首选动作**（`TOOLBOX_PRIMARY_ACTION`，与弹框按钮**同一个动作函数**）；
 *    弹框里每项显示自己的当前键位。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { LANGUAGES, languageLabel, sqlDialectLabel } from '../../config/scratch-types';
import { TOOLBOX_ITEM_SHORTCUT, formatShortcutForDisplay } from '../../config/shortcuts';
import { useShortcutStore } from '../../stores/shortcut-store';
import {
  capabilitiesForTab,
  isViewModeAllowed,
  useScratchStore,
} from '../../stores/scratch-store';
import { consumePanelRequest, useScratchUiStore } from '../../stores/scratch-ui-store';
import type { ScratchViewMode } from '../../types/scratch';
import {
  DEFAULT_SQL_FORMAT_OPTIONS,
  DEFAULT_XML_FORMAT_OPTIONS,
  TOOLBOX_ITEMS,
  TOOLBOX_PRIMARY_ACTION,
  checkSqlFormattable,
  checkXmlFormattable,
  clampFontSize,
  decodeBase64,
  decodeUrlComponent,
  dedupeLines,
  encodeBase64,
  encodeUrlComponent,
  escapeHtml,
  escapeJson,
  formatJson,
  minifyJson,
  removeEmptyLines,
  restoreBomForFormatted,
  sortLines,
  stepFontSize,
  stripBom,
  toCamelCase,
  toLowerCase,
  toSnakeCase,
  toUpperCase,
  toSqlFormatOptions,
  trimLines,
  unescapeHtml,
  unescapeJson,
  xmlSafeMinify,
} from '../../utils/scratch/toolbox';
import type { ToolboxItemId } from '../../utils/scratch/toolbox';
import { DiffView } from './views/DiffView';
import { CrossTabSearchPanel } from './CrossTabSearchPanel';
import { getScratchEditorSession } from './ScratchEditorHost';

/** 视图模式按钮（顺序即 UI 顺序；`edit` 恒可用） */
const VIEW_MODES: { mode: ScratchViewMode; label: string }[] = [
  { mode: 'edit', label: '编辑' },
  { mode: 'preview', label: '预览' },
  { mode: 'split', label: '分屏' },
  { mode: 'table', label: '表格' },
  { mode: 'tree', label: '树形' },
];

/**
 * SQL 关键字大小写三档（§4.1.6 第 9 项「关键字大小写」）
 *
 * （语言显示名 `languageLabel` 已迁到 `config/scratch-types.ts` —— 状态栏与工具栏共用同一份，
 *  且按 **ext 优先**修正了"`.txt` 显示成 CSV"的误标，见 AC-74⑦ / 台账 B1。）
 */
const SQL_KEYWORD_CASES: { value: 'upper' | 'lower' | 'preserve'; label: string }[] = [
  { value: 'upper', label: '大写' },
  { value: 'lower', label: '小写' },
  { value: 'preserve', label: '保持原样' },
];

export interface ScratchToolbarProps {
  /** 标签 id（工具栏**自己**从 store 订阅标签状态；正文经 `getScratchEditorSession` 取） */
  tabId: string;
  className?: string;
}

/**
 * 「选完即关」的**分组白名单**（AC-76 / 用户裁决 V-4，设计 §15.4.1 DS-46）。
 *
 * 语义 = **保持打开**的 id 集合（**白名单**，不是"关闭名单"）：
 *   - 只有需要**连续多次操作**的两组留在白名单里：`sql`（方言 / 关键字大小写 / 缩进宽度）
 *     与 `font`（字号 ± / 行号 / 自动换行）；
 *   - 其余 **10 项**选完即关；
 *   - **新增工具箱项时的默认行为 = 关闭**（白名单是"需要例外的少数"，安全默认）。
 *
 * ⚠ **按 `TOOLBOX_ITEMS[].id` 分组，不按按钮文本**：`escape` 一项就有 6 个按钮，
 * "是否关框"的归属是**工具箱项**而不是单个按钮 ⇒ 用容器委托 + `data-toolbox-item`，
 * 让归属成为**结构性事实**（新增按钮 / 新增项时自动继承正确行为）。
 * ⚠ 用 `Set` + 显式 id 而不是按控件类型判断：`sql` 组里点「美化」按钮按 AC-76② 也**保持打开**
 * （用户可能接着改配置让结果即时重跑，见 SQL 即时生效 effect）。
 */
const MENU_STAYS_OPEN: ReadonlySet<string> = new Set(['sql', 'font']);

/** 该工具箱项被选择后弹框是否保持打开（`false` = 选完即关） */
export function menuStaysOpenAfter(itemId: string): boolean {
  return MENU_STAYS_OPEN.has(itemId);
}

/**
 * 编辑面板工具栏。
 *
 * @example
 * // 由 `EditorPanel` 挂在面板顶部（工具栏之下是正文区，状态栏在底部）
 * <ScratchToolbar tabId={tab.id} />
 */
export function ScratchToolbar({ tabId, className }: ScratchToolbarProps) {
  const setLanguage = useScratchStore((state) => state.setLanguage);
  const setViewMode = useScratchStore((state) => state.setViewMode);
  const setFontSize = useScratchStore((state) => state.setFontSize);
  const toggleLineNumbers = useScratchStore((state) => state.toggleLineNumbers);
  const toggleWordWrap = useScratchStore((state) => state.toggleWordWrap);

  // 标签状态从 store 订阅（v1.6.0 是当 `pane` prop 传进来；面板数量由 1 个激活窗格变成 N 个，改由本组件自取）
  const tab = useScratchStore((state) => state.tabs.find((item) => item.id === tabId));
  // 工具箱项的当前键位（弹框里显示，AC-91③）—— 只订阅 bindings，改键后即时刷新
  const bindings = useShortcutStore((state) => state.bindings);

  const [menuOpen, setMenuOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [jumpLine, setJumpLine] = useState('1');
  const [diffOpen, setDiffOpen] = useState(false);
  const [diffText, setDiffText] = useState('');
  /** 跨标签页搜索 / 替换覆盖层（v1.6.2 · F15） */
  const [searchOpen, setSearchOpen] = useState(false);

  /**
   * 弹框与本组件的 DOM 锚点（AC-76 的"点外部关闭"判定用）。
   *
   * `menuRef` = 弹框本体；`triggerRef` = 「工具箱（12 项）」开关按钮 —— 落在开关上的
   * pointerdown **不算外部**，交给它自己的 `onClick` 做开/关切换（否则一按就"先关后开"）。
   */
  const menuRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  // 面板请求（`Ctrl+K` / `Ctrl+Shift+D` 的接管方投递；见下方消费 effect 与 stores/scratch-ui-store.ts）
  const panelRequest = useScratchUiStore((state) => state.panelRequest);

  // SQL 三项可配置（方言 / 关键字大小写 / 缩进宽度）——改配置**即时生效**（重跑格式化）
  const [sqlConfig, setSqlConfig] = useState({
    language: DEFAULT_SQL_FORMAT_OPTIONS.language ?? 'sql',
    keywordCase: 'upper' as 'upper' | 'lower' | 'preserve',
    tabWidth: DEFAULT_SQL_FORMAT_OPTIONS.tabWidth ?? 2,
  });
  const sqlAppliedRef = useRef(false);

  // 注意：Hook 必须**无条件**调用（`tab` 为 null 时也不能提前 return，否则违反 Hooks 规则）
  const capabilities = useMemo(() => (tab ? capabilitiesForTab(tab) : []), [tab]);
  const canEdit = tab ? !tab.readOnly && tab.mounted : false;

  /** 取正文（未挂载返回 null） */
  const currentText = (): string | null => getScratchEditorSession(tabId)?.getText() ?? null;

  /**
   * 结果文案（AC-82：**可见反馈**）。
   *
   * 唯一构造点：单位只有「行」与「字符」两种，数字来自与写回**同一次**变换的输入/输出 ⇒
   * 提示里的规模不可能与实际结果不一致。
   */
  const describeApplied = (label: string, unit: 'lines' | 'chars', text: string): string =>
    unit === 'lines'
      ? `已${label}（${text.split('\n').length} 行）`
      : `已${label}（${text.length} 字符）`;

  /**
   * 纯函数变换的统一派发（唯一回写通路）。
   *
   * v1.6.2（AC-82 第 3 条）两点变化：
   *   ① 写回时开 `revealFirstChange` ⇒ 变化处滚进视口（否则"作用文字不明显"仍在）；
   *   ② 结果**逐字符相同**时不再静默：给「已<动作>（无变化）」并**不写回**（不产生无意义历史）。
   */
  const runTransform = (
    transform: (input: string) => string,
    feedback: { label: string; unit: 'lines' | 'chars' },
  ): void => {
    if (!tab) return;
    if (tab.readOnly) {
      setMessage('只读标签页（大文件降级 / diff 预览）禁止修改内容');
      return;
    }
    const session = getScratchEditorSession(tabId);
    if (!session) {
      setMessage('标签页尚未挂载，无法处理正文');
      return;
    }
    const input = session.getText();
    try {
      const output = transform(input);
      if (output === input) {
        setMessage(`已${feedback.label}（无变化）`);
        return;
      }
      session.setText(output, { revealFirstChange: true });
      setMessage(describeApplied(feedback.label, feedback.unit, output));
    } catch (error) {
      setMessage(error instanceof Error && error.message ? error.message : '文本处理失败');
    }
  };

  /** 行操作的四项（首选动作 = 排序） */
  const applyLines = (transform: (input: string) => string, label: string): void =>
    runTransform(transform, { label, unit: 'lines' });

  /** 字号 +2（工具箱第 12 项的首选动作，与弹框「A+」同一实现） */
  const increaseFontSize = (): void => {
    if (!tab) return;
    setFontSize(tabId, stepFontSize(tab.fontSize, 2));
    setMessage(`已放大字号（${clampFontSize(stepFontSize(tab.fontSize, 2))}px）`);
  };

  /** SQL 美化（先纯预检，再用离线包的 sql-formatter） */
  const runSqlFormat = (): void => {
    if (!tab) return;
    if (tab.readOnly) {
      setMessage('只读标签页禁止修改内容');
      return;
    }
    const session = getScratchEditorSession(tabId);
    if (!session) {
      setMessage('标签页尚未挂载，无法美化 SQL');
      return;
    }
    const input = session.getText();
    const guard = checkSqlFormattable(input);
    if (!guard.ok) {
      setMessage(guard.reason ?? '当前内容不适合美化');
      return;
    }
    try {
      const formatted = session.api.formatters.sql(input, toSqlFormatOptions(sqlConfig));
      session.setText(formatted, { revealFirstChange: true });
      sqlAppliedRef.current = true;
      setMessage(describeApplied('美化 SQL', 'lines', formatted));
    } catch (error) {
      setMessage(error instanceof Error && error.message ? error.message : 'SQL 美化失败');
    }
  };

  /** XML 格式化（严格模式 + 补回 BOM；预检由纯函数负责） */
  const runXmlFormat = (): void => {
    if (!tab) return;
    if (tab.readOnly) {
      setMessage('只读标签页禁止修改内容');
      return;
    }
    const session = getScratchEditorSession(tabId);
    if (!session) {
      setMessage('标签页尚未挂载，无法格式化 XML');
      return;
    }
    const input = session.getText();
    const guard = checkXmlFormattable(input);
    if (!guard.ok) {
      setMessage(guard.reason ?? '当前内容不适合格式化');
      return;
    }
    try {
      const formatted = session.api.formatters.xml(stripBom(input), DEFAULT_XML_FORMAT_OPTIONS);
      const restored = restoreBomForFormatted(input, formatted);
      session.setText(restored, { revealFirstChange: true });
      setMessage(describeApplied('格式化 XML', 'lines', restored));
    } catch (error) {
      setMessage(error instanceof Error && error.message ? error.message : 'XML 格式化失败');
    }
  };

  /** XML 自研安全压缩（**不用** xml-formatter.minify，理由见 §4.1.6 第 10 项） */
  const runXmlMinify = (): void => {
    if (!tab) return;
    if (tab.readOnly) {
      setMessage('只读标签页禁止修改内容');
      return;
    }
    const session = getScratchEditorSession(tabId);
    if (!session) {
      setMessage('标签页尚未挂载，无法压缩 XML');
      return;
    }
    const result = xmlSafeMinify(session.getText());
    if (!result.ok || result.output === undefined) {
      setMessage(result.reason ?? '压缩被拒绝');
      return;
    }
    session.setText(result.output, { revealFirstChange: true });
    setMessage(describeApplied('安全压缩 XML', 'chars', result.output));
  };

  /**
   * 打开 diff 覆盖层（先把当前正文快照下来，避免与编辑热路径耦合）。
   *
   * `text` 可选：工具栏按钮与**键盘请求**共用这一条通路，避免"两处漏改"
   * （`setMenuOpen(false)` 这一点尤其容易漏 —— 设计 §15.3.6）。
   */
  const openDiff = (text?: string): void => {
    setDiffText(text ?? currentText() ?? '');
    setDiffOpen(true);
    setSearchOpen(false); // 两个覆盖层互斥（同一块矩形，不叠着）
    setMenuOpen(false);
  };

  /** 打开跨标签搜索覆盖层（v1.6.2 · F15；与 diff 互斥） */
  const openCrossTabSearch = (): void => {
    setSearchOpen(true);
    setDiffOpen(false);
    setMenuOpen(false);
  };

  /* SQL 配置改动 ⇒ 若上次动作是美化，则**立刻重跑**（§4.1.6 第 9 项"即时生效"） */
  useEffect(() => {
    if (!sqlAppliedRef.current) return;
    runSqlFormat();
    // 依赖只有三项配置：runSqlFormat 每次渲染都是新函数，放进依赖会死循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sqlConfig.language, sqlConfig.keywordCase, sqlConfig.tabWidth]);

  /**
   * 消费**一次性面板请求**（AC-75② / 闭环 BL-51；投递方 = `utils/scratch/shortcut-runtime.ts`）。
   *
   * - 放在 Hook 区、`if (!tab) return null` **之前**（否则标签不存在时请求会卡在 store 里）；
   * - `tabId` 必须与请求目标一致：**非激活面板也挂了工具栏**（多面板并存，非激活项带 `hidden`），
   *   只有目标面板该应答；不一致就让请求继续等（等目标面板挂载 / 激活）；
   * - `consumePanelRequest` 是幂等的：返回 `false` = 已被处理过（重复渲染）⇒ 直接返回，不执行；
   * - `toolbox` **只开不关**（不破坏既有 `menuOpen` 语义）；`diff` 的正文在**组件上下文**里取
   *   （store 不持有正文，§4.1.2），取不到就给中文提示 —— 提示本身也是可观测结果，不静默；
   * - v1.6.2 新增 `toolbox-item`（F14 / AC-89）：**执行该项的首选动作**，不开弹框
   *   （投递方 = 工具箱快捷键运行时；动作与弹框按钮同一个函数，见 `primaryActionFor`）。
   */
  useEffect(() => {
    if (!panelRequest || !tab || panelRequest.tabId !== tabId) return;
    if (!consumePanelRequest(panelRequest.nonce)) return;
    if (panelRequest.kind === 'toolbox') {
      setMenuOpen(true);
      return;
    }
    if (panelRequest.kind === 'toolbox-item') {
      if (panelRequest.itemId) executeToolboxItem(panelRequest.itemId);
      return;
    }
    const session = getScratchEditorSession(tabId);
    if (!session) {
      setMessage('标签页尚未挂载，无法打开对比');
      return;
    }
    openDiff(session.getText());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelRequest, tab, tabId]);

  /* ── AC-76：`Esc` 关闭弹框（照抄 `ScratchTabBar.tsx:173-186` 的捕获阶段写法）── */
  useEffect(() => {
    if (!menuOpen) return undefined; // 没开就不装监听（零常驻成本）
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation(); // 不让 Esc 继续流向 CM6 / 宿主
      setMenuOpen(false);
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [menuOpen]);

  /* ── AC-76：点弹框外部关闭（照抄 `ScratchTabBar.tsx:160-171` 的 pointerdown 捕获）── */
  useEffect(() => {
    if (!menuOpen) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      const node = event.target;
      if (!(node instanceof Node)) return;
      if (menuRef.current?.contains(node)) return; // 点在弹框内 ⇒ 由容器委托决定（见下方 onClick 委托）
      if (triggerRef.current?.contains(node)) return; // 点在「工具箱」开关上 ⇒ 交给它自己的 onClick 切换
      setMenuOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
  }, [menuOpen]);

  const searchPanel = (): void => {
    const session = getScratchEditorSession(tabId);
    if (!session || !session.openSearchPanel()) setMessage('查找面板不可用（标签页未挂载）');
    else setMessage(null);
  };

  const jumpToLine = (): void => {
    const session = getScratchEditorSession(tabId);
    if (!session) {
      setMessage('标签页尚未挂载，无法跳转行');
      return;
    }
    const line = Number.parseInt(jumpLine, 10);
    if (!Number.isFinite(line) || line < 1) {
      setMessage('跳转行号必须是 ≥ 1 的整数');
      return;
    }
    session.revealLine(line);
    setMessage(null);
  };

  /* ── 单项动作表（工具箱弹框按钮与工具箱快捷键**共用同一份实现** ⇒ AC-90 的防漂移手段） ── */
  const actions = {
    searchPanel,
    crossTabSearch: openCrossTabSearch,
    jumpToLine,
    sort: () => applyLines(sortLines, '排序'),
    dedupe: () => applyLines(dedupeLines, '去重'),
    removeEmpty: () => applyLines(removeEmptyLines, '去空行'),
    trim: () => applyLines(trimLines, '去首尾空格'),
    upper: () => runTransform(toUpperCase, { label: '转大写', unit: 'chars' }),
    lower: () => runTransform(toLowerCase, { label: '转小写', unit: 'chars' }),
    camel: () => runTransform(toCamelCase, { label: '转驼峰', unit: 'chars' }),
    snake: () => runTransform(toSnakeCase, { label: '转下划线', unit: 'chars' }),
    jsonEscape: () => runTransform(escapeJson, { label: 'JSON 转义', unit: 'chars' }),
    jsonUnescape: () => runTransform(unescapeJson, { label: 'JSON 反转义', unit: 'chars' }),
    htmlEscape: () => runTransform(escapeHtml, { label: 'HTML 转义', unit: 'chars' }),
    htmlUnescape: () => runTransform(unescapeHtml, { label: 'HTML 反转义', unit: 'chars' }),
    urlEncode: () => runTransform(encodeUrlComponent, { label: 'URL 编码', unit: 'chars' }),
    urlDecode: () => runTransform(decodeUrlComponent, { label: 'URL 解码', unit: 'chars' }),
    base64Encode: () => runTransform(encodeBase64, { label: 'Base64 编码', unit: 'chars' }),
    base64Decode: () => runTransform(decodeBase64, { label: 'Base64 解码', unit: 'chars' }),
    jsonFormat: () => runTransform(formatJson, { label: '格式化 JSON', unit: 'chars' }),
    jsonMinify: () => runTransform(minifyJson, { label: '压缩 JSON', unit: 'chars' }),
    jsonTree: () => setViewMode(tabId, 'tree'),
    viewEdit: () => setViewMode(tabId, 'edit'),
    viewPreview: () => setViewMode(tabId, 'preview'),
    viewSplit: () => setViewMode(tabId, 'split'),
    viewTable: () => setViewMode(tabId, 'table'),
    sqlFormat: runSqlFormat,
    xmlFormat: runXmlFormat,
    xmlMinify: runXmlMinify,
    diff: () => openDiff(),
    fontUp: increaseFontSize,
  };

  /**
   * 因**文档类型**而不可用时的中文原因（AC：禁用不得表现为"点了没反应"）。
   *
   * 判据强度：每一条非空、含"仅 … 可用"，且**不含**英文异常字样。
   */
  const viewDisabledHint = (mode: ScratchViewMode): string => {
    switch (mode) {
      case 'preview':
      case 'split':
        return '仅 Markdown 文档可用（当前文档类型不支持预览 / 分屏）';
      case 'table':
        return '仅 CSV / TSV 可用（当前文档类型不支持表格视图）';
      case 'tree':
        return '仅 JSON 文档可用（当前文档类型不支持树形视图）';
      default:
        return '当前文档类型不支持该视图';
    }
  };

  /**
   * 工具箱项 → **首选动作**（AC-90 / 设计 DS-53）。
   *
   * 值必须是 `TOOLBOX_PRIMARY_ACTION` 里那一项的按钮文案所对应的**同一个动作函数**
   * （不是另写一份实现）⇒"按键执行的动作"与"点按钮执行的动作"不可能漂移。
   * 用**函数声明**以便上面的消费 effect 直接引用（提升 + 无需改 Hook 顺序）。
   */
  function primaryActionFor(itemId: string): (() => void) | null {
    switch (itemId) {
      case 'search':
        return actions.searchPanel;
      case 'lines':
        return actions.sort;
      case 'case':
        return actions.upper;
      case 'escape':
        return actions.jsonEscape;
      case 'base64':
        return actions.base64Encode;
      case 'json':
        return actions.jsonFormat;
      case 'markdown':
        return actions.viewPreview;
      case 'csv':
        return actions.viewTable;
      case 'sql':
        return actions.sqlFormat;
      case 'xml':
        return actions.xmlFormat;
      case 'diff':
        return actions.diff;
      case 'font':
        return actions.fontUp;
      default:
        return null;
    }
  }

  /** 执行某一工具箱项的首选动作（快捷键通路；未挂载 / 只读的原因由动作自己给中文提示） */
  function executeToolboxItem(itemId: string): void {
    const action = primaryActionFor(itemId);
    if (!action) {
      setMessage('该工具箱项暂无可执行的动作');
      return;
    }
    action();
  }

  /** 该工具箱项当前键位的展示文案（AC-91③：弹框里让用户知道"这项有快捷键"） */
  function bindingHint(itemId: string): string {
    const shortcutId = TOOLBOX_ITEM_SHORTCUT[itemId as ToolboxItemId];
    if (!shortcutId) return '未绑定快捷键';
    const key = bindings[shortcutId];
    return key ? `快捷键 ${formatShortcutForDisplay(key)}` : '未绑定快捷键';
  }

  /** 每个工具箱项的动作按钮（顺序即设计 §4.1.6 的 1~12；**不含第 13 项**） */
  const actionsFor = (itemId: string) => {
    if (!tab) return null;
    const disabled = !canEdit;
    const btn = (label: string, onClick: () => void, extraDisabled = false, title?: string) => (
      <button
        key={label}
        type="button"
        title={title}
        disabled={disabled || extraDisabled}
        onClick={onClick}
        className="rounded px-1.5 py-0.5 hover:bg-black/5 disabled:opacity-40 dark:hover:bg-white/10"
      >
        {label}
      </button>
    );

    switch (itemId) {
      case 'search':
        return (
          <>
            {btn('查找 / 替换面板', actions.searchPanel, false, 'Ctrl+F 打开 CM6 查找面板（支持正则）')}
            {btn(
              '跨标签搜索 / 替换',
              actions.crossTabSearch,
              false,
              '在所有已打开的标签页之间搜索与替换（默认范围：当前标签页）',
            )}
            <span className="flex items-center gap-1">
              <input
                value={jumpLine}
                onChange={(event) => setJumpLine(event.target.value)}
                inputMode="numeric"
                aria-label="跳转到行号"
                className="w-14 rounded border border-black/15 bg-transparent px-1 py-0.5 dark:border-white/20"
              />
              {btn('跳转行', actions.jumpToLine, false, '按 1 基行号跳转')}
            </span>
          </>
        );
      case 'lines':
        return (
          <>
            {btn('排序', actions.sort, false, 'Unicode 码点升序，大小写敏感')}
            {btn('去重', actions.dedupe, false, '完全相等保留首次')}
            {btn('去空行', actions.removeEmpty, false, '只删空行，全空白行保留')}
            {btn('去首尾空格', actions.trim, false, '每行 trim()（含制表符）')}
          </>
        );
      case 'case':
        return (
          <>
            {btn('大写', actions.upper)}
            {btn('小写', actions.lower)}
            {btn('驼峰', actions.camel)}
            {btn('下划线', actions.snake)}
          </>
        );
      case 'escape':
        return (
          <>
            {btn('JSON 转义', actions.jsonEscape)}
            {btn('JSON 反转义', actions.jsonUnescape)}
            {btn('HTML 转义', actions.htmlEscape)}
            {btn('HTML 反转义', actions.htmlUnescape)}
            {btn('URL 编码', actions.urlEncode)}
            {btn('URL 解码', actions.urlDecode)}
          </>
        );
      case 'base64':
        return (
          <>
            {btn('Base64 编码', actions.base64Encode)}
            {btn('Base64 解码', actions.base64Decode)}
          </>
        );
      case 'json':
        return (
          <>
            {btn('格式化', actions.jsonFormat, false, '缩进 2 空格；非法 JSON 只提示')}
            {btn('压缩', actions.jsonMinify, false, '非法 JSON 只提示，原文不动')}
            {btn(
              '树形视图',
              actions.jsonTree,
              !isViewModeAllowed('tree', capabilities),
              isViewModeAllowed('tree', capabilities)
                ? '只读 JSON 树（不回写正文）'
                : viewDisabledHint('tree'),
            )}
          </>
        );
      case 'markdown':
        return (
          <>
            {btn('编辑', actions.viewEdit)}
            {btn(
              '预览',
              actions.viewPreview,
              !isViewModeAllowed('preview', capabilities),
              isViewModeAllowed('preview', capabilities) ? '只读渲染预览' : viewDisabledHint('preview'),
            )}
            {btn(
              '分屏',
              actions.viewSplit,
              !isViewModeAllowed('split', capabilities),
              isViewModeAllowed('split', capabilities) ? '左编辑右预览' : viewDisabledHint('split'),
            )}
          </>
        );
      case 'csv':
        return (
          <>
            {btn(
              '表格视图',
              actions.viewTable,
              !isViewModeAllowed('table', capabilities),
              isViewModeAllowed('table', capabilities)
                ? 'CSV / TSV 只读表格（自动识别分隔符）'
                : viewDisabledHint('table'),
            )}
          </>
        );
      case 'sql':
        return (
          <>
            {btn('美化', actions.sqlFormat, false, '先预检（DELIMITER / 命名参数 / 规模 / 嵌套深度）')}
            <label className="flex items-center gap-1 opacity-70">
              方言
              <select
                value={sqlConfig.language}
                onChange={(event) => setSqlConfig((prev) => ({ ...prev, language: event.target.value }))}
                className="rounded border border-black/15 bg-transparent px-1 py-0.5 dark:border-white/20"
              >
                {(getScratchEditorSession(tabId)?.api.formatters.sqlDialects ?? [
                  DEFAULT_SQL_FORMAT_OPTIONS.language ?? 'sql',
                ]).map((dialect) => (
                  <option key={dialect} value={dialect}>
                    {/* AC-74⑥：显示名 = 中文名 / 专有名词 + （原始 id）；`value` 仍是原始 id ⇒
                        格式化行为零变化（`toSqlFormatOptions` 逐字不变）。映射表见
                        `config/scratch-types.ts` 的 `sqlDialectLabel`（覆盖面 = 运行时全集 21 项）。 */}
                    {sqlDialectLabel(dialect)}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1 opacity-70">
              关键字
              <select
                value={sqlConfig.keywordCase}
                onChange={(event) =>
                  setSqlConfig((prev) => ({
                    ...prev,
                    keywordCase: event.target.value as 'upper' | 'lower' | 'preserve',
                  }))
                }
                className="rounded border border-black/15 bg-transparent px-1 py-0.5 dark:border-white/20"
              >
                {SQL_KEYWORD_CASES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1 opacity-70">
              缩进
              <input
                type="number"
                min={0}
                max={8}
                value={sqlConfig.tabWidth}
                onChange={(event) =>
                  setSqlConfig((prev) => ({
                    ...prev,
                    tabWidth: Number.parseInt(event.target.value, 10) || 0,
                  }))
                }
                className="w-12 rounded border border-black/15 bg-transparent px-1 py-0.5 dark:border-white/20"
              />
            </label>
          </>
        );
      case 'xml':
        return (
          <>
            {btn('格式化', actions.xmlFormat, false, '严格模式；非法 XML 只提示，不改原文')}
            {btn('安全压缩', actions.xmlMinify, false, '自研压缩：不动文本节点内空白')}
          </>
        );
      case 'diff':
        return (
          <>
            {btn(
              '打开对比',
              actions.diff,
              false,
              '第二段来源：剪贴板 / 另一标签页 / 本地文件；覆盖层铺满编辑面板',
            )}
          </>
        );
      case 'font':
        return (
          <>
            {btn('A−', () => setFontSize(tabId, stepFontSize(tab.fontSize, -2)), false, '缩小字号')}
            <span className="opacity-60">{clampFontSize(tab.fontSize)}px</span>
            {btn('A+', actions.fontUp, false, '放大字号')}
            {btn(tab.showLineNumbers ? '行号 ✓' : '行号', () => toggleLineNumbers(tabId))}
            {btn(tab.wordWrap ? '自动换行 ✓' : '自动换行', () => toggleWordWrap(tabId))}
          </>
        );
      default:
        return null;
    }
  };

  // 标签不存在（面板刚被关闭）时**不能提前 return** —— 上面已无条件调用全部 Hook，这里才安全
  if (!tab) return null;

  return (
    /* ⚠ v1.6.2（DS-51 / AC-83）：**根元素刻意不带 `relative`** —— diff 与跨标签搜索两个
       覆盖层（`absolute inset-0`）挂在它下面，定位祖先必须是 `EditorPanel` 根（`relative`）；
       v1.6.1 把 `relative` 放在这里，导致覆盖层只有工具栏一行高（"对比完全没效果"）。
       工具箱弹框需要自己的定位基准 ⇒ 下面这层"内容层"单独带 `relative`。 */
    <div
      className={`flex flex-wrap items-center gap-1 border-b border-black/10 px-2 py-1 text-xs dark:border-white/10 ${className ?? ''}`}
    >
      <div className="relative flex min-w-0 flex-1 flex-wrap items-center gap-1">
      {/* 语言手动切换 + 恢复自动（AC-51：未知语言按 text 打开、可手动切） */}
      <label className="flex items-center gap-1">
        <span className="opacity-60">语言</span>
        <select
          value={tab.language}
          onChange={(event) => setLanguage(tabId, event.target.value)}
          className="rounded border border-black/15 bg-transparent px-1 py-0.5 dark:border-white/20"
        >
          {tab.language !== tab.detected ? (
            <option value={tab.detected}>恢复自动（{languageLabel(tab.detected)}）</option>
          ) : null}
          {LANGUAGES.map((language) => (
            <option key={language} value={language}>
              {languageLabel(language)}
            </option>
          ))}
        </select>
      </label>

      <span className="mx-1 h-4 w-px bg-black/10 dark:bg-white/15" />

      {/* 视图模式（能力表校验：不可用的按钮禁用，§4.1.2） */}
      {VIEW_MODES.map((item) => {
        const allowed = isViewModeAllowed(item.mode, capabilities);
        return (
          <button
            key={item.mode}
            type="button"
            disabled={!allowed}
            onClick={() => setViewMode(tabId, item.mode)}
            className={`rounded px-1.5 py-0.5 disabled:opacity-30 ${
              tab.viewMode === item.mode
                ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400'
                : 'hover:bg-black/5 dark:hover:bg-white/10'
            }`}
          >
            {item.label}
          </button>
        );
      })}

      <span className="mx-1 h-4 w-px bg-black/10 dark:bg-white/15" />

      <button
        ref={triggerRef}
        type="button"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}
        className="rounded px-2 py-0.5 hover:bg-black/5 dark:hover:bg-white/10"
      >
        工具箱（{TOOLBOX_ITEMS.length} 项）{menuOpen ? ' ▴' : ' ▾'}
      </button>

      {tab.readOnly ? <span className="opacity-60">只读</span> : null}
      {message ? (
        <span role="status" className="text-amber-600">
          {message}
        </span>
      ) : null}

      {/* 工具箱面板：12 项逐项渲染（顺序取 TOOLBOX_ITEMS，UI 不自造第 13 项）。
          AC-76 的"选完即关"用**容器一次性委托**实现（**不**逐个改 `actionsFor` 里几十个按钮）：
          捕获阶段从事件目标向上找最近的 `[data-toolbox-item]` ⇒ 得到归属项 id。 */}
      {menuOpen ? (
        <div
          ref={menuRef}
          data-toolbox-menu="true"
          className="absolute top-full left-0 z-40 mt-1 max-h-[70vh] w-full min-w-[420px] overflow-auto rounded border border-black/10 bg-white p-2 text-xs shadow-lg dark:border-white/15 dark:bg-neutral-900"
          onClick={(event) => {
            const target = event.target as HTMLElement | null;
            const owner = target?.closest?.<HTMLElement>('[data-toolbox-item]');
            const itemId = owner?.dataset.toolboxItem;
            if (!itemId) return;
            if (menuStaysOpenAfter(itemId)) return; // sql / font：保持打开（连续操作型）
            // ⚠ 只在"这一项真的执行了动作"时才关（v1.6.2 DS-49：**冒泡**阶段 ⇒ 目标按钮的
            //    `onClick` 已经执行完毕才轮到这里；改用 `pointerup` 会在 click 之前卸载按钮、
            //    把这次点击整个吞掉，见文件头与 `poc/editor-probes/r21-pointerup-close-semantics.py`）：
            //    ① 只对**可用的 `<button>`** 生效，避免点到 `<input>`/`<select>`（跳转行输入框、
            //    方言下拉）时误关；`disabled` 的按钮也要放行（用户没获得任何结果，不该丢上下文）。
            if (!(target instanceof HTMLButtonElement) || target.disabled) return;
            setMenuOpen(false);
          }}
        >
          <ul className="flex flex-col gap-1">
            {TOOLBOX_ITEMS.map((item) => (
              <li
                key={item.id}
                data-toolbox-item={item.id}
                className="flex flex-wrap items-center gap-1 border-b border-black/5 pb-1 last:border-b-0 dark:border-white/10"
              >
                <span className="w-4 text-right opacity-40">{item.index}</span>
                <span className="min-w-[9rem] font-medium" title={item.hint}>
                  {item.label}
                </span>
                <span className="flex flex-wrap items-center gap-1">{actionsFor(item.id)}</span>
                {/* AC-91③：让用户看得见"这项有快捷键"，并用 `TOOLBOX_PRIMARY_ACTION`
                    （唯一真相表）说明**这条键会执行哪一项动作**（键位取生效值，改键后即时刷新；
                    `data-toolbox-key` 是用例的观测点）。 */}
                <span
                  data-toolbox-key={item.id}
                  title={`${bindingHint(item.id)}：执行「${TOOLBOX_PRIMARY_ACTION[item.id]}」`}
                  className="ml-auto shrink-0 text-[10.5px] opacity-50"
                >
                  {bindingHint(item.id)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      </div>

      {/* 第 11 项：diff 覆盖层。v1.6.2（DS-51）：它现在挂**根元素**下、根元素是 static
          ⇒ 包含块 = 最近的定位祖先 = `EditorPanel` 根（`relative`）⇒ 铺满整个编辑面板。
          `DiffView` 的 `pane` prop 值类型已在 v1.6.1 由 `ScratchPane` 换成 `ScratchTab`
          （与 `ScratchEditorHost` 同一口径），直接投喂本面板的标签对象即可，不再需要类型断言。 */}
      {diffOpen ? (
        <div className="absolute inset-0 z-30" data-editor-overlay="diff">
          <DiffView pane={tab} text={diffText} onClose={() => setDiffOpen(false)} />
        </div>
      ) : null}

      {/* 跨标签页搜索 / 替换覆盖层（v1.6.2 · F15 / DS-54）：与 diff 同一挂载手法（互斥） */}
      {searchOpen ? (
        <div className="absolute inset-0 z-30" data-editor-overlay="search-across-tabs">
          <CrossTabSearchPanel tabId={tabId} onClose={() => setSearchOpen(false)} />
        </div>
      ) : null}
    </div>
  );
}

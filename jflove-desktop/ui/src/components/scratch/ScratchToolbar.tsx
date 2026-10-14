/**
 * 窗格工具栏：**语言切换 + 视图切换 + 工具箱 12 项 + 字号/行号/自动换行**
 * （设计 v1.6.0 §4.1.1 `ScratchPaneToolbar` / §4.1.6 工具箱 / §6.1.3 四类视图）
 *
 * ## 分层：本文件只做"派发"
 *
 * - **纯逻辑**在 `utils/scratch/toolbox.ts`（12 项文本处理，全部纯函数，可单测）；
 * - **正文读写**经 `ScratchEditorHost` 导出的会话表
 *   （`getScratchEditorSession(pane.id)`）—— store 不持有正文（§4.1.2），
 *   所以这里是"取正文 → 纯函数变换 → 整体回写"的唯一通路；
 * - **视图切换**走 `scratch-store.setViewMode`（能力表校验，不可用则不动，§4.1.2）。
 *
 * ## 只读窗格的硬约束（AC-19 / AC-52）
 *
 * `pane.readOnly === true` 时：所有会改正文的按钮**禁用**（大文件降级与 `.diff` 都走这条），
 * 视图切换与字号/行号/自动换行仍可用（它们不改内容）。
 *
 * ## diff 的落点说明
 *
 * `viewMode` 只有 `edit/preview/split/table/tree`（`types/scratch.ts`），**没有 `diff`**
 * —— 所以工具箱第 11 项用**覆盖层**呈现 `DiffView`：本组件渲染一个
 * `absolute inset-0` 的容器，依赖窗格外壳（同事的 `ScratchPane`）是定位祖先。
 *
 * ## 安全（AGENTS.md §9.14 / R-1）
 *
 * 不 `console.*` 文档内容与路径（`no-console` 只允许 warn/error，本文件一条都不打）；
 * 不使用 localStorage；所有文件 I/O 都经 `scratchService` 的白名单桥。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { EXTENSIONS, LANGUAGES } from '../../config/scratch-types';
import {
  capabilitiesForPane,
  isViewModeAllowed,
  useScratchStore,
} from '../../stores/scratch-store';
import type { ScratchPane, ScratchViewMode } from '../../types/scratch';
import {
  DEFAULT_SQL_FORMAT_OPTIONS,
  DEFAULT_XML_FORMAT_OPTIONS,
  TOOLBOX_ITEMS,
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
import { DiffView } from './views/DiffView';
import { getScratchEditorSession } from './ScratchEditorHost';

/** 视图模式按钮（顺序即 UI 顺序；`edit` 恒可用） */
const VIEW_MODES: { mode: ScratchViewMode; label: string }[] = [
  { mode: 'edit', label: '编辑' },
  { mode: 'preview', label: '预览' },
  { mode: 'split', label: '分屏' },
  { mode: 'table', label: '表格' },
  { mode: 'tree', label: '树形' },
];

/** 语言 id → 中文/英文显示名（取类型表里第一个用该语言的扩展名标签） */
function languageLabel(language: string): string {
  return EXTENSIONS.find((entry) => entry.language === language)?.label ?? language;
}

/** SQL 关键字的三个大小写档位（§4.1.6 第 9 项"关键字大小写"） */
const SQL_KEYWORD_CASES: { value: 'upper' | 'lower' | 'preserve'; label: string }[] = [
  { value: 'upper', label: '大写' },
  { value: 'lower', label: '小写' },
  { value: 'preserve', label: '保持原样' },
];

export interface ScratchToolbarProps {
  /** 窗格状态（来自 `scratch-store`） */
  pane: ScratchPane;
  className?: string;
}

/**
 * 窗格工具栏。
 *
 * @example
 * // 由同事的 `ScratchPane` 挂在标题栏下方
 * <ScratchToolbar pane={pane} />
 */
export function ScratchToolbar({ pane, className }: ScratchToolbarProps) {
  const setLanguage = useScratchStore((state) => state.setLanguage);
  const setViewMode = useScratchStore((state) => state.setViewMode);
  const setFontSize = useScratchStore((state) => state.setFontSize);
  const toggleLineNumbers = useScratchStore((state) => state.toggleLineNumbers);
  const toggleWordWrap = useScratchStore((state) => state.toggleWordWrap);

  const [menuOpen, setMenuOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [jumpLine, setJumpLine] = useState('1');
  const [diffOpen, setDiffOpen] = useState(false);
  const [diffText, setDiffText] = useState('');

  // SQL 三项可配置（方言 / 关键字大小写 / 缩进宽度）——改配置**即时生效**（重跑格式化）
  const [sqlConfig, setSqlConfig] = useState({
    language: DEFAULT_SQL_FORMAT_OPTIONS.language ?? 'sql',
    keywordCase: 'upper' as 'upper' | 'lower' | 'preserve',
    tabWidth: DEFAULT_SQL_FORMAT_OPTIONS.tabWidth ?? 2,
  });
  const sqlAppliedRef = useRef(false);

  const capabilities = useMemo(() => capabilitiesForPane(pane), [pane]);
  const canEdit = !pane.readOnly && pane.mounted;

  /** 取正文（未挂载返回 null） */
  const currentText = (): string | null => getScratchEditorSession(pane.id)?.getText() ?? null;

  /** 纯函数变换的统一派发（唯一回写通路） */
  const runTransform = (transform: (input: string) => string): void => {
    if (pane.readOnly) {
      setMessage('只读窗格（大文件降级 / diff 预览）禁止修改内容');
      return;
    }
    const session = getScratchEditorSession(pane.id);
    if (!session) {
      setMessage('窗格尚未挂载，无法处理正文');
      return;
    }
    const input = session.getText();
    try {
      session.setText(transform(input));
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error && error.message ? error.message : '文本处理失败');
    }
  };

  /** SQL 美化（先纯预检，再用离线包的 sql-formatter） */
  const runSqlFormat = (): void => {
    if (pane.readOnly) {
      setMessage('只读窗格禁止修改内容');
      return;
    }
    const session = getScratchEditorSession(pane.id);
    if (!session) {
      setMessage('窗格尚未挂载，无法美化 SQL');
      return;
    }
    const input = session.getText();
    const guard = checkSqlFormattable(input);
    if (!guard.ok) {
      setMessage(guard.reason ?? '当前内容不适合美化');
      return;
    }
    try {
      session.setText(session.api.formatters.sql(input, toSqlFormatOptions(sqlConfig)));
      sqlAppliedRef.current = true;
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error && error.message ? error.message : 'SQL 美化失败');
    }
  };

  /** XML 格式化（严格模式 + 补回 BOM；预检由纯函数负责） */
  const runXmlFormat = (): void => {
    if (pane.readOnly) {
      setMessage('只读窗格禁止修改内容');
      return;
    }
    const session = getScratchEditorSession(pane.id);
    if (!session) {
      setMessage('窗格尚未挂载，无法格式化 XML');
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
      session.setText(restoreBomForFormatted(input, formatted));
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error && error.message ? error.message : 'XML 格式化失败');
    }
  };

  /** XML 自研安全压缩（**不用** xml-formatter.minify，理由见 §4.1.6 第 10 项） */
  const runXmlMinify = (): void => {
    if (pane.readOnly) {
      setMessage('只读窗格禁止修改内容');
      return;
    }
    const session = getScratchEditorSession(pane.id);
    if (!session) {
      setMessage('窗格尚未挂载，无法压缩 XML');
      return;
    }
    const result = xmlSafeMinify(session.getText());
    if (!result.ok || result.output === undefined) {
      setMessage(result.reason ?? '压缩被拒绝');
      return;
    }
    session.setText(result.output);
    setMessage(null);
  };

  /** 打开 diff 覆盖层（先把当前正文快照下来，避免与编辑热路径耦合） */
  const openDiff = (): void => {
    setDiffText(currentText() ?? '');
    setDiffOpen(true);
    setMenuOpen(false);
  };

  /* SQL 配置改动 ⇒ 若上次动作是美化，则**立刻重跑**（§4.1.6 第 9 项"即时生效"） */
  useEffect(() => {
    if (!sqlAppliedRef.current) return;
    runSqlFormat();
    // 依赖只有三项配置：runSqlFormat 每次渲染都是新函数，放进依赖会死循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sqlConfig.language, sqlConfig.keywordCase, sqlConfig.tabWidth]);

  const searchPanel = (): void => {
    const session = getScratchEditorSession(pane.id);
    if (!session || !session.openSearchPanel()) setMessage('查找面板不可用（窗格未挂载）');
    else setMessage(null);
  };

  const jumpToLine = (): void => {
    const session = getScratchEditorSession(pane.id);
    if (!session) {
      setMessage('窗格尚未挂载，无法跳转行');
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

  /** 每个工具箱项的动作按钮（顺序即设计 §4.1.6 的 1~12；**不含第 13 项**） */
  const actionsFor = (itemId: string) => {
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
            {btn('查找 / 替换面板', searchPanel, false, 'Ctrl+F 打开 CM6 查找面板（支持正则）')}
            <span className="flex items-center gap-1">
              <input
                value={jumpLine}
                onChange={(event) => setJumpLine(event.target.value)}
                inputMode="numeric"
                aria-label="跳转到行号"
                className="w-14 rounded border border-black/15 bg-transparent px-1 py-0.5 dark:border-white/20"
              />
              {btn('跳转行', jumpToLine, false, '按 1 基行号跳转')}
            </span>
          </>
        );
      case 'lines':
        return (
          <>
            {btn('排序', () => runTransform(sortLines), false, 'Unicode 码点升序，大小写敏感')}
            {btn('去重', () => runTransform(dedupeLines), false, '完全相等保留首次')}
            {btn('去空行', () => runTransform(removeEmptyLines), false, '只删空行，全空白行保留')}
            {btn('去首尾空格', () => runTransform(trimLines), false, '每行 trim()（含制表符）')}
          </>
        );
      case 'case':
        return (
          <>
            {btn('大写', () => runTransform(toUpperCase))}
            {btn('小写', () => runTransform(toLowerCase))}
            {btn('驼峰', () => runTransform(toCamelCase))}
            {btn('下划线', () => runTransform(toSnakeCase))}
          </>
        );
      case 'escape':
        return (
          <>
            {btn('JSON 转义', () => runTransform(escapeJson))}
            {btn('JSON 反转义', () => runTransform(unescapeJson))}
            {btn('HTML 转义', () => runTransform(escapeHtml))}
            {btn('HTML 反转义', () => runTransform(unescapeHtml))}
            {btn('URL 编码', () => runTransform(encodeUrlComponent))}
            {btn('URL 解码', () => runTransform(decodeUrlComponent))}
          </>
        );
      case 'base64':
        return (
          <>
            {btn('Base64 编码', () => runTransform(encodeBase64))}
            {btn('Base64 解码', () => runTransform(decodeBase64))}
          </>
        );
      case 'json':
        return (
          <>
            {btn('格式化', () => runTransform(formatJson), false, '缩进 2 空格；非法 JSON 只提示')}
            {btn('压缩', () => runTransform(minifyJson), false, '非法 JSON 只提示，原文不动')}
            {btn('树形视图', () => setViewMode(pane.id, 'tree'), !isViewModeAllowed('tree', capabilities))}
          </>
        );
      case 'markdown':
        return (
          <>
            {btn('编辑', () => setViewMode(pane.id, 'edit'))}
            {btn('预览', () => setViewMode(pane.id, 'preview'), !isViewModeAllowed('preview', capabilities))}
            {btn('分屏', () => setViewMode(pane.id, 'split'), !isViewModeAllowed('split', capabilities))}
          </>
        );
      case 'csv':
        return (
          <>
            {btn(
              '表格视图',
              () => setViewMode(pane.id, 'table'),
              !isViewModeAllowed('table', capabilities),
              'CSV / TSV 只读表格（自动识别分隔符）',
            )}
          </>
        );
      case 'sql':
        return (
          <>
            {btn('美化', runSqlFormat, false, '先预检（DELIMITER / 命名参数 / 规模 / 嵌套深度）')}
            <label className="flex items-center gap-1 opacity-70">
              方言
              <select
                value={sqlConfig.language}
                onChange={(event) => setSqlConfig((prev) => ({ ...prev, language: event.target.value }))}
                className="rounded border border-black/15 bg-transparent px-1 py-0.5 dark:border-white/20"
              >
                {(getScratchEditorSession(pane.id)?.api.formatters.sqlDialects ?? [
                  DEFAULT_SQL_FORMAT_OPTIONS.language ?? 'sql',
                ]).map((dialect) => (
                  <option key={dialect} value={dialect}>
                    {dialect}
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
            {btn('格式化', runXmlFormat, false, '严格模式；非法 XML 只提示，不改原文')}
            {btn('安全压缩', runXmlMinify, false, '自研压缩：不动文本节点内空白')}
          </>
        );
      case 'diff':
        return <>{btn('打开对比', openDiff, false, '第二段来源：剪贴板 / 另一窗格 / 本地文件')}</>;
      case 'font':
        return (
          <>
            {btn('A−', () => setFontSize(pane.id, stepFontSize(pane.fontSize, -2)), false, '缩小字号')}
            <span className="opacity-60">{clampFontSize(pane.fontSize)}px</span>
            {btn('A+', () => setFontSize(pane.id, stepFontSize(pane.fontSize, 2)), false, '放大字号')}
            {btn(pane.showLineNumbers ? '行号 ✓' : '行号', () => toggleLineNumbers(pane.id))}
            {btn(pane.wordWrap ? '自动换行 ✓' : '自动换行', () => toggleWordWrap(pane.id))}
          </>
        );
      default:
        return null;
    }
  };

  return (
    <div
      className={`relative flex flex-wrap items-center gap-1 border-b border-black/10 px-2 py-1 text-xs dark:border-white/10 ${className ?? ''}`}
    >
      {/* 语言手动切换 + 恢复自动（AC-51：未知语言按 text 打开、可手动切） */}
      <label className="flex items-center gap-1">
        <span className="opacity-60">语言</span>
        <select
          value={pane.language}
          onChange={(event) => setLanguage(pane.id, event.target.value)}
          className="rounded border border-black/15 bg-transparent px-1 py-0.5 dark:border-white/20"
        >
          {pane.language !== pane.detected ? (
            <option value={pane.detected}>恢复自动（{languageLabel(pane.detected)}）</option>
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
            onClick={() => setViewMode(pane.id, item.mode)}
            className={`rounded px-1.5 py-0.5 disabled:opacity-30 ${
              pane.viewMode === item.mode
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
        type="button"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}
        className="rounded px-2 py-0.5 hover:bg-black/5 dark:hover:bg-white/10"
      >
        工具箱（{TOOLBOX_ITEMS.length} 项）{menuOpen ? ' ▴' : ' ▾'}
      </button>

      {pane.readOnly ? <span className="opacity-60">只读</span> : null}
      {message ? (
        <span role="status" className="text-amber-600">
          {message}
        </span>
      ) : null}

      {/* 工具箱面板：12 项逐项渲染（顺序取 TOOLBOX_ITEMS，UI 不自造第 13 项） */}
      {menuOpen ? (
        <div className="absolute top-full left-0 z-40 mt-1 max-h-[70vh] w-full min-w-[420px] overflow-auto rounded border border-black/10 bg-white p-2 text-xs shadow-lg dark:border-white/15 dark:bg-neutral-900">
          <ul className="flex flex-col gap-1">
            {TOOLBOX_ITEMS.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-1 border-b border-black/5 pb-1 last:border-b-0 dark:border-white/10">
                <span className="w-4 text-right opacity-40">{item.index}</span>
                <span className="min-w-[9rem] font-medium" title={item.hint}>
                  {item.label}
                </span>
                <span className="flex flex-wrap items-center gap-1">{actionsFor(item.id)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 第 11 项：diff 覆盖层（依赖窗格外壳是定位祖先，见文件头说明） */}
      {diffOpen ? (
        <div className="absolute inset-0 z-30">
          <DiffView pane={pane} text={diffText} onClose={() => setDiffOpen(false)} />
        </div>
      ) : null}
    </div>
  );
}

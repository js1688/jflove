/**
 * 跨标签页搜索 / 替换面板（v1.6.2 · 需求 F15 / 设计 DS-54）
 *
 * ## 范围口径（需求 D37 / AC-92）
 *
 * - **默认「当前标签页」**（用户裁决："默认还是当前输入框内"）；
 * - 切到「所有已打开的标签页」⇒ 只在**已挂载**的标签页之间搜索；
 * - **不读磁盘、不搜未打开的文件、不新增桥方法** ⇒ 本文件**不 import** `scratchService`，
 *   正文一律经 `getScratchEditorSession(id).getText()`（内存里的正文，零 I/O）。
 * - 未挂载的标签页**不得静默漏掉** ⇒ 面板顶部明确写出"有 N 个标签页尚未挂载，未参与搜索"。
 *
 * ## 交互口径
 *
 * - 「查找」按钮或输入框回车触发搜索（不做输入即搜：跨 10 个标签页的实时搜索没有必要）；
 * - 结果按标签页分组，点击某条命中 ⇒ 切到该标签页 + 滚动并**选中**命中文本（AC-93）；
 * - 「替换当前」作用于当前选中的那条命中；「全部替换」走**就地二次确认条**
 *   （不用原生 / 嵌套对话框：既避免模态套模态，也让自动化判据能读计数，AC-95①）；
 * - 替换文本**按字面插入**（不展开 `$1` 反向引用）—— 这一条**写在界面上**（不静默，见 `search-across-tabs.ts` 文件头）。
 *
 * ## 安全（AGENTS.md §9.6 / §9.14）
 *
 * 不打印文档内容与路径；只把**行号与行文本**显示在结果列表（那是用户自己要看的东西，
 * 不进日志）；不访问网络、不写文件、不用 localStorage。
 */

import { useCallback, useMemo, useState } from 'react';
import { useScratchStore } from '../../stores/scratch-store';
import {
  MAX_HITS_PER_TAB,
  MAX_HITS_TOTAL,
  buildSearchPattern,
  patternMatchesEmpty,
  replaceAllLiteral,
  searchTabs,
} from '../../utils/scratch/search-across-tabs';
import type { CrossTabSearchResult, SearchableTab } from '../../utils/scratch/search-across-tabs';
import { getScratchEditorSession } from './ScratchEditorHost';

/** 搜索范围（默认「当前标签页」= 用户裁决的默认口径） */
type SearchScope = 'tab' | 'all';

export interface CrossTabSearchPanelProps {
  /** 打开本面板时所在的标签页（范围 = 「当前标签页」时就是它） */
  tabId: string;
  onClose: () => void;
  className?: string;
}

/** 结果列表里的一条（命中 + 所属标签页，供点击跳转用） */
interface FlatHit {
  key: string;
  tabId: string;
  title: string;
  from: number;
  to: number;
  line: number;
  lineText: string;
}

export function CrossTabSearchPanel({ tabId, onClose, className }: CrossTabSearchPanelProps) {
  const tabs = useScratchStore((state) => state.tabs);
  const setActiveTab = useScratchStore((state) => state.setActiveTab);

  const [scope, setScope] = useState<SearchScope>('tab');
  const [query, setQuery] = useState('');
  const [replacement, setReplacement] = useState('');
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [regex, setRegex] = useState(false);
  const [wholeWord, setWholeWord] = useState(false);

  const [result, setResult] = useState<CrossTabSearchResult | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);

  /**
   * 本次范围内的标签页（**只取元数据，不取正文**）。
   *
   * ⚠ 正文**绝不能**在这里缓存：正文活在 CM6 会话里，store 的 `tabs` 变化与"正文变了"无关
   * （替换后 `tabs` 一个字段都不变）⇒ 把 `getText()` 的结果塞进 `useMemo` 会让**替换后重搜
   * 读到旧文本**（开发期实测：替换完仍显示"2 处命中"）。正文一律在 `collectSearchable()`
   * 里**现取**（只在用户点「查找」时发生，代价可控）。
   */
  const candidateTabs = useMemo(() => {
    const picked = scope === 'tab' ? tabs.filter((item) => item.id === tabId) : tabs;
    return picked.map((item) => ({ tabId: item.id, title: item.title }));
  }, [scope, tabs, tabId]);

  /** 已挂载的那些（未挂载标签页没有正文可取） */
  const mountedTabs = useMemo(
    () => candidateTabs.filter((item) => getScratchEditorSession(item.tabId) !== null),
    [candidateTabs],
  );

  /** 未挂载的标签页数量（只统计本次范围里的，措辞才不会误导） */
  const unmountedCount = candidateTabs.length - mountedTabs.length;

  /** 现取正文（搜索 / 替换的唯一入口；**不缓存**，理由见 `candidateTabs` 的说明） */
  const collectSearchable = useCallback((): SearchableTab[] => {
    const collected: SearchableTab[] = [];
    for (const item of mountedTabs) {
      const session = getScratchEditorSession(item.tabId);
      if (session) collected.push({ tabId: item.tabId, title: item.title, text: session.getText() });
    }
    return collected;
  }, [mountedTabs]);

  const flatHits = useMemo<FlatHit[]>(() => {
    if (!result) return [];
    return result.groups.flatMap((group) =>
      group.hits.map((hit) => ({
        key: `${group.tabId}:${hit.from}`,
        tabId: group.tabId,
        title: group.title,
        from: hit.from,
        to: hit.to,
        line: hit.line,
        lineText: hit.lineText,
      })),
    );
  }, [result]);

  const activeHit = useMemo(
    () => flatHits.find((hit) => hit.key === activeKey) ?? null,
    [activeKey, flatHits],
  );

  /** 当前查询对应的正则（`null` = 空查询 / 非法正则） */
  const buildPattern = useCallback(() => {
    if (!query) {
      setMessage('请输入要查找的内容');
      return null;
    }
    const pattern = buildSearchPattern(query, { regex, caseSensitive, wholeWord });
    if (!pattern) {
      setMessage('正则表达式无效，请检查后重试');
      return null;
    }
    if (patternMatchesEmpty(pattern)) {
      setMessage('查找内容不能匹配空字符串（否则替换会在每个字符之间插入内容）');
      return null;
    }
    return pattern;
  }, [caseSensitive, query, regex, wholeWord]);

  /** 执行一次搜索（结果按标签条顺序分组） */
  const runSearch = useCallback((): CrossTabSearchResult | null => {
    const pattern = buildPattern();
    if (!pattern) {
      setResult(null);
      return null;
    }
    const next = searchTabs(collectSearchable(), pattern);
    setResult(next);
    setActiveKey(next.groups[0]?.hits[0] ? `${next.groups[0].tabId}:${next.groups[0].hits[0].from}` : null);
    setConfirmAll(false);
    if (next.total === 0) setMessage('未找到匹配内容');
    else if (next.truncated) setMessage(`命中过多，仅显示前 ${next.total} 处（上限 ${MAX_HITS_TOTAL}）`);
    else setMessage(null);
    return next;
  }, [buildPattern, collectSearchable]);

  /** 点击结果 ⇒ 切标签页 + 滚动并选中命中文本（AC-93） */
  const jumpTo = useCallback(
    (hit: FlatHit) => {
      setActiveKey(hit.key);
      setActiveTab(hit.tabId);
      getScratchEditorSession(hit.tabId)?.revealRange(hit.from, hit.to);
    },
    [setActiveTab],
  );

  /** 替换当前选中的那一条命中（精确按区间替换，**不用全局 replace**，避免误伤其它命中） */
  const replaceCurrent = useCallback(() => {
    if (!activeHit) {
      setMessage('请先在结果里选中一条命中');
      return;
    }
    const session = getScratchEditorSession(activeHit.tabId);
    if (!session) {
      setMessage('该标签页尚未挂载，无法替换');
      return;
    }
    const text = session.getText();
    const from = Math.max(0, Math.min(activeHit.from, text.length));
    const to = Math.max(from, Math.min(activeHit.to, text.length));

    /**
     * ⚠ **先复核命中还成立**（审查期回修 · 中等）：命中偏移是**搜索那一刻**的快照；
     * 用户若在搜索之后编辑过正文（或用别的标签页改过），偏移就可能指到别处 ——
     * 直接切片会把 `replacement` 写到**错误的字符区间**。复核不通过时**不猜**：
     * 重新搜索并让用户重选（中文提示，不静默）。
     */
    const pattern = buildPattern();
    if (!pattern) return;
    pattern.lastIndex = from;
    const matched = pattern.exec(text);
    if (!matched || matched.index !== from || matched[0].length !== to - from) {
      // ⚠ 顺序：先 `runSearch()`（它会在命中数变化时**覆盖**提示文案），再落我们的提示
      runSearch();
      setMessage('命中内容已变化，已重新搜索，请重新选择一条命中再替换');
      return;
    }

    session.setText(`${text.slice(0, from)}${replacement}${text.slice(to)}`, {
      revealFirstChange: true,
    });
    runSearch();
    setMessage('已替换 1 处');
  }, [activeHit, buildPattern, replacement, runSearch]);

  /** 全部替换（作用范围 = 当前选定范围；作用对象 = 结果里出现过的标签页） */
  const replaceEverywhere = useCallback(() => {
    const pattern = buildPattern();
    if (!pattern) return;
    const targets = result?.groups.map((group) => group.tabId) ?? [];
    if (targets.length === 0) {
      setMessage('没有可替换的命中');
      setConfirmAll(false);
      return;
    }
    let replaced = 0;
    let touched = 0;
    for (const targetId of targets) {
      const session = getScratchEditorSession(targetId);
      if (!session) continue;
      const current = session.getText();
      const outcome = replaceAllLiteral(current, pattern, replacement);
      if (outcome.count === 0) continue;
      session.setText(outcome.text);
      replaced += outcome.count;
      touched += 1;
    }
    setConfirmAll(false);
    runSearch();
    setMessage(
      replaced > 0
        ? `已替换 ${replaced} 处（涉及 ${touched} 个标签页；改动需保存后落盘）`
        : '没有可替换的命中',
    );
  }, [buildPattern, replacement, result, runSearch]);

  const scopeLabel = scope === 'tab' ? '当前标签页' : '所有已打开的标签页';

  return (
    <div className={`flex h-full min-h-0 flex-col bg-white/95 dark:bg-neutral-900/95 ${className ?? ''}`}>
      <div className="flex flex-wrap items-center gap-2 border-b border-black/10 px-2 py-1 text-xs dark:border-white/10">
        <label className="flex items-center gap-1">
          <span className="opacity-70">搜索范围</span>
          <select
            value={scope}
            onChange={(event) => {
              setScope(event.target.value as SearchScope);
              setResult(null);
              setActiveKey(null);
              setConfirmAll(false);
              setMessage(null);
            }}
            aria-label="搜索范围"
            className="rounded border border-black/15 bg-transparent px-1 py-0.5 dark:border-white/20"
          >
            <option value="tab">当前标签页</option>
            <option value="all">所有已打开的标签页</option>
          </select>
        </label>

        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              runSearch();
            }
          }}
          placeholder="查找内容"
          aria-label="查找内容"
          className="w-40 rounded border border-black/15 bg-transparent px-1.5 py-0.5 dark:border-white/20"
        />
        <input
          value={replacement}
          onChange={(event) => setReplacement(event.target.value)}
          placeholder="替换为"
          aria-label="替换为"
          className="w-40 rounded border border-black/15 bg-transparent px-1.5 py-0.5 dark:border-white/20"
        />

        <label className="flex items-center gap-1 opacity-80">
          <input type="checkbox" checked={caseSensitive} onChange={(e) => setCaseSensitive(e.target.checked)} />
          区分大小写
        </label>
        <label className="flex items-center gap-1 opacity-80">
          <input type="checkbox" checked={regex} onChange={(e) => setRegex(e.target.checked)} />
          正则
        </label>
        <label className="flex items-center gap-1 opacity-80">
          <input type="checkbox" checked={wholeWord} onChange={(e) => setWholeWord(e.target.checked)} />
          整词
        </label>

        <button
          type="button"
          onClick={() => runSearch()}
          className="rounded px-2 py-0.5 hover:bg-black/5 dark:hover:bg-white/10"
        >
          查找
        </button>
        <button
          type="button"
          onClick={replaceCurrent}
          className="rounded px-2 py-0.5 hover:bg-black/5 dark:hover:bg-white/10"
        >
          替换当前
        </button>
        <button
          type="button"
          onClick={() => (confirmAll ? replaceEverywhere() : setConfirmAll(true))}
          disabled={!result || result.total === 0}
          className="rounded px-2 py-0.5 hover:bg-black/5 disabled:opacity-40 dark:hover:bg-white/10"
        >
          {confirmAll ? '确认全部替换' : '全部替换'}
        </button>

        <span className="ml-auto flex items-center gap-2 opacity-70">
          <span>
            范围：{scopeLabel}（{mountedTabs.length} 个）
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-0.5 hover:bg-black/5 dark:hover:bg-white/10"
          >
            关闭
          </button>
        </span>
      </div>

      {/* 未挂载标签页的明示（AC-94：不得静默漏掉） */}
      {unmountedCount > 0 ? (
        <div className="border-b border-black/10 px-2 py-1 text-xs text-amber-600 dark:border-white/10">
          有 {unmountedCount} 个标签页尚未挂载，未参与搜索（切到该标签页后即可被搜到）
        </div>
      ) : null}

      {confirmAll && result ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-black/10 px-2 py-1 text-xs dark:border-white/10">
          <span className="text-amber-600">
            将替换 {result.total} 处、涉及 {result.groups.length} 个标签页（替换文本按字面插入）
          </span>
          <button
            type="button"
            onClick={() => setConfirmAll(false)}
            className="rounded px-2 py-0.5 hover:bg-black/5 dark:hover:bg-white/10"
          >
            取消
          </button>
        </div>
      ) : null}

      {message ? (
        <div role="status" className="border-b border-black/10 px-2 py-1 text-xs text-amber-600 dark:border-white/10">
          {message}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto px-2 py-1 text-xs">
        {flatHits.length === 0 ? (
          <div className="py-2 opacity-60">
            选择范围并输入查找内容后点「查找」；命中会在这里按标签页分组列出（最多每页 {MAX_HITS_PER_TAB} 处）
          </div>
        ) : (
          result?.groups.map((group) => (
            <div key={group.tabId} className="mb-2">
              <div className="font-medium opacity-80">
                {group.title}（{group.hits.length} 处）
              </div>
              <ul>
                {group.hits.map((hit) => {
                  const key = `${group.tabId}:${hit.from}`;
                  return (
                    <li key={key}>
                      <button
                        type="button"
                        onClick={() =>
                          jumpTo({
                            key,
                            tabId: group.tabId,
                            title: group.title,
                            from: hit.from,
                            to: hit.to,
                            line: hit.line,
                            lineText: hit.lineText,
                          })
                        }
                        className={`w-full rounded px-1 py-0.5 text-left hover:bg-black/5 dark:hover:bg-white/10 ${
                          key === activeKey ? 'bg-sky-500/15' : ''
                        }`}
                      >
                        <span className="tabular opacity-60">{hit.line}:</span> {hit.lineText}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
      </div>

      {result ? (
        <div className="border-t border-black/10 px-2 py-1 text-[11px] opacity-70 dark:border-white/10">
          共 {result.total} 处命中，涉及 {result.groups.length} 个标签页
        </div>
      ) : null}
    </div>
  );
}

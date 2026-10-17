/**
 * 跨标签页搜索 / 替换的**纯逻辑**（v1.6.2 · 需求 F15 / 设计 DS-54）
 *
 * ## 为什么是一个叶子模块
 *
 * 跨标签搜索只依赖"已打开标签页的正文"（由 `ScratchEditorHost` 的会话表提供），
 * **不读磁盘、不碰桥、不碰 DOM** ⇒ 全部逻辑可以做成纯函数，于是：
 *   - 用例不需要渲染任何组件即可逐条判定（AC-92 / AC-95 的计数与行号判据）；
 *   - AC-96"零新增能力面"变成结构性事实（本文件连 `scratchService` 都不 import）。
 *
 * ## 硬口径（改动前先读）
 *
 * 1. **替换按字面插入**：`replaceAllLiteral` 用回调形式替换，**不展开 `$1` 之类的反向引用**
 *    与 `$&`/`$$` 转义 —— 这样"计数"与"替换"同源（都由回调产生），不可能出现
 *    "说替换了 3 处、实际替换了 2 处"的口径分裂。界面上必须写明这一条（不静默）。
 * 2. **零长度匹配必须手动前进** `lastIndex`，否则 `/(?:)/g` 这类模式会让循环永不结束。
 * 3. 偏移口径 = **UTF-16 码元**，与 CM6 文档位置一致（`revealRange` 直接吃这个值）。
 */

/** 搜索选项（与 CM6 查找面板同一组语义） */
export interface SearchOptions {
  /** 按正则解释查询串（为假时按**字面量**，需转义） */
  regex: boolean;
  caseSensitive: boolean;
  wholeWord: boolean;
}

/** 一条命中 */
export interface TextHit {
  /** 命中起点（UTF-16 码元偏移，含） */
  from: number;
  /** 命中终点（不含） */
  to: number;
  /** 1 基行号 */
  line: number;
  /** 该行原文（结果列表摘要用） */
  lineText: string;
}

/** 一个标签页的命中集 */
export interface TabHits {
  tabId: string;
  title: string;
  hits: TextHit[];
}

/** 可被搜索的标签页（调用方负责从会话表取正文；未挂载的标签页**不得**进入本数组） */
export interface SearchableTab {
  tabId: string;
  title: string;
  text: string;
}

/** 单个标签页的命中上限（超出只提示"命中过多"，不静默截断成"就这么点"） */
export const MAX_HITS_PER_TAB = 500;

/** 一次跨标签搜索的总命中上限 */
export const MAX_HITS_TOTAL = 2000;

/** 正则元字符转义（`regex: false` 时把查询串当字面量） */
export function escapeRegExp(raw: string): string {
  return raw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * 构造搜索用正则；**非法正则返回 `null`**（调用方据此给中文提示，不得抛异常给界面）。
 *
 * 注意：永远带 `g` 标志 —— `findHits` 与 `replaceAllLiteral` 都靠 `lastIndex` 逐条前进。
 */
export function buildSearchPattern(query: string, options: SearchOptions): RegExp | null {
  if (!query) return null;
  let source = options.regex ? query : escapeRegExp(query);
  if (options.wholeWord) source = `\\b(?:${source})\\b`;
  const flags = options.caseSensitive ? 'g' : 'gi';
  try {
    return new RegExp(source, flags);
  } catch {
    return null;
  }
}

/** 各行的起始偏移表（`[0, 第一行之后的下标, …]`）——一次线性扫描，供行号查询二分 */
function lineStarts(text: string): number[] {
  const starts: number[] = [0];
  for (let i = 0; i < text.length; i += 1) {
    if (text.charCodeAt(i) === 10 /* \n */) starts.push(i + 1);
  }
  return starts;
}

/** 1 基行号（`starts` 来自 `lineStarts`，二分查找） */
function lineNumberAt(starts: readonly number[], offset: number): number {
  let low = 0;
  let high = starts.length - 1;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (starts[mid] <= offset) low = mid;
    else high = mid - 1;
  }
  return low + 1;
}

/** 命中所在行的原文（不含换行符） */
function lineTextOf(text: string, starts: readonly number[], line: number): string {
  const start = starts[line - 1] ?? 0;
  const end = line < starts.length ? starts[line] - 1 : text.length;
  return text.slice(start, Math.max(start, end));
}

/**
 * 在**一段正文**里找出全部命中（最多 `limit` 条）。
 *
 * @param pattern 必须带 `g`（由 `buildSearchPattern` 保证）
 * @returns 命中的偏移与行信息；调用方不需要关心 `lastIndex`（函数出口会复位）
 */
export function findHits(
  text: string,
  pattern: RegExp,
  limit: number = MAX_HITS_PER_TAB,
): TextHit[] {
  const hits: TextHit[] = [];
  if (!text) return hits;
  const starts = lineStarts(text);
  pattern.lastIndex = 0;
  let match = pattern.exec(text);
  while (match !== null) {
    if (hits.length >= limit) break;
    const from = match.index;
    const to = from + match[0].length;
    const line = lineNumberAt(starts, from);
    hits.push({ from, to, line, lineText: lineTextOf(text, starts, line) });
    // 零长度匹配：手动前进，否则 exec 会原地打转（正则 `/(?:)/g` 必然命中）
    if (match[0].length === 0) pattern.lastIndex += 1;
    match = pattern.exec(text);
  }
  pattern.lastIndex = 0;
  return hits;
}

/**
 * 字面量替换**全部**命中（不展开反向引用，见文件头口径 1）。
 *
 * @returns `text` = 替换后的正文；`count` = 实际替换处数（与替换**同源**，不可能不一致）
 */
export function replaceAllLiteral(
  text: string,
  pattern: RegExp,
  replacement: string,
): { text: string; count: number } {
  let count = 0;
  pattern.lastIndex = 0;
  const next = text.replace(pattern, () => {
    count += 1;
    return replacement;
  });
  pattern.lastIndex = 0;
  return { text: next, count };
}

/**
 * 该正则是否会命中**空串**（`/(?:)/g`、`/a?/g` 这类）。
 *
 * 用途：替换前必须挡掉"空匹配" —— 否则"全部替换"会在每个字符之间插入替换文本，
 * 既不是用户想要的、计数也没有意义。挡掉时给中文提示（不得静默）。
 */
export function patternMatchesEmpty(pattern: RegExp): boolean {
  pattern.lastIndex = 0;
  const match = pattern.exec('');
  pattern.lastIndex = 0;
  return match !== null && match[0].length === 0;
}

/** 一次跨标签搜索的汇总 */export interface CrossTabSearchResult {
  groups: TabHits[];
  /** 所有标签页的命中总数（**不含**被上限截断的部分） */
  total: number;
  /** 是否触达了任一上限（触达时界面必须提示"命中过多"） */
  truncated: boolean;
  /** 参与搜索的标签页数（已挂载的那些） */
  searchedTabs: number;
}

/**
 * 跨多个标签页搜索（按传入顺序分组，**不重排** ⇒ 结果列表顺序 = 标签条顺序）。
 *
 * 上限口径：单标签页 `MAX_HITS_PER_TAB`；总体 `MAX_HITS_TOTAL`（触顶即停止并置 `truncated`）。
 */
export function searchTabs(
  tabs: readonly SearchableTab[],
  pattern: RegExp,
  limitPerTab: number = MAX_HITS_PER_TAB,
  limitTotal: number = MAX_HITS_TOTAL,
): CrossTabSearchResult {
  const groups: TabHits[] = [];
  let total = 0;
  let truncated = false;
  for (const tab of tabs) {
    const remaining = limitTotal - total;
    if (remaining <= 0) {
      truncated = true;
      break;
    }
    const effective = Math.min(limitPerTab, remaining);
    // 多取一条用于**精确**判断"是否还有更多命中"：取到 effective+1 条 ⇒ 确实被截断
    // （只比较 `hits.length === effective` 会把"恰好这么多"误报成截断）
    const probed = findHits(tab.text, pattern, effective + 1);
    if (probed.length > effective) truncated = true;
    const hits = probed.slice(0, effective);
    if (hits.length > 0) {
      groups.push({ tabId: tab.tabId, title: tab.title, hits });
      total += hits.length;
    }
  }
  return { groups, total, truncated, searchedTabs: tabs.length };
}

/**
 * 编辑面板状态栏（设计 v1.6.1 §4.1.4 的 F10 全字段 / AC-62）
 *
 * v1.6.1 相对 v1.6.0（`ScratchPaneStatusBar`，已删除）**唯一的变化是宿主位置与入参**：
 * 从「浮动窗格底部」挪到「编辑面板底部」，props 由 `{ pane: ScratchPane }` 改为 `{ tabId: string }`，
 * 标签与实时量都在组件内部取。**状态栏口径逐字不变**（下表）。
 *
 * ## 逐字实现 §4.1.4 的口径
 *
 * | 字段 | 口径 |
 * | --- | --- |
 * | `行:列` | **1 基**。列 = 光标所在**字符**在该行的序号（按**码点**，不按 UTF-16 code unit） |
 * | 选中字符数 | `[...selectedText].length`（无选中 = 0） |
 * | 总字符数 | `[...doc.toString()].length` —— **含换行符**（`"abc\nDE"` = 6，U-14 定稿），文案注明 |
 * | 编码 / 换行符 / 语言 | 直接取标签（语言随手动切换变化） |
 * | 只读标记 | `readOnly === true` 时显示 |
 * | 脏标记 | `dirty` |
 *
 * ## 实时数值从哪来
 *
 * 前三项是 **CM6 运行时量**（`EditorState.selection` + `doc.length`），不由 store 持有。
 * `ScratchEditorHost.tsx` 已实现这条广播通道：
 *
 * ```
 * updateListener → emitStats(tabId) → subscribeScratchEditorStats / useScratchEditorStats
 * ```
 *
 * 因此本组件直接调 `useScratchEditorStats(tabId)`（v1.6.0 是由 `ScratchPane` 取好再当
 * `stats` prop 传进来 —— 面板数量从「1 个激活窗格」变成「N 个已挂载面板」后，
 * 让状态栏自己订阅更省一次透传，输出口径完全一致）。
 * 未挂载 / 尚未上报时 `stats === null`，前三项**降级**为：`行:列` 取 `tab.caret`
 * （store 缓存的还原值），选中 / 总数显示 `—`（宁缺毋假，绝不编造数字）。
 *
 * > 口径提醒：「列」必须按**码点**（`[...prefix].length + 1`），总数**含换行符** ——
 * > 这两条由宿主 `computeEditorStats` 负责；本组件只展示，**不得再做任何"再算一遍"的加工**。
 */

import { languageLabel, resolveTypeForPath } from '../../config/scratch-types';
import { useScratchStore } from '../../stores/scratch-store';
import type { TextEncoding, LineEnding } from '../../types/scratch';
import { useScratchEditorStats } from './ScratchEditorHost';
import type { ScratchEditorStats } from './ScratchEditorHost';

/** CM6 运行时量（由宿主上报；全部是**数字**，不含正文与路径）。 */
export type TabEditorStats = ScratchEditorStats;

/** 编码 → 界面显示（逐项对齐 `TextEncoding`，新增取值时必须同步这里） */
const ENCODING_LABEL: Record<TextEncoding, string> = {
  'utf-8': 'UTF-8',
  'utf-8-bom': 'UTF-8 BOM',
  gbk: 'GBK',
  'utf-16le': 'UTF-16 LE',
  'utf-16be': 'UTF-16 BE',
};

/** 换行符 → 界面显示（§4.1.4 要求显示 LF / CRLF） */
const LINE_ENDING_LABEL: Record<LineEnding, string> = {
  lf: 'LF',
  crlf: 'CRLF',
};

export interface ScratchEditorStatusBarProps {
  /** 所属标签 id（编码 / 换行符 / 语言 / 只读 / 脏标记从 store 查；实时量从宿主广播订阅） */
  tabId: string;
}

/**
 * 状态栏的**语言显示名**（AC-74⑦ / 台账 B1 的误标修正）。
 *
 * 口径（与设计 §15.2.5 的"推荐做法"一致）：
 *   - 有 `path` ⇒ 调既有的 `resolveTypeForPath(path).label` —— 显示名与类型表**天然同源**，
 *     **不**在本组件里自己 `split('.')`（那会重复 `resolveTypeForPath` 的规范化逻辑）；
 *   - 草稿形态（`path === null`）⇒ 回退 `languageLabel(language)`。
 *
 * 修掉的缺陷：改造前这里**直接渲染语言 id**（`{tab.language}` ⇒ 用户看到 `text` / `json`
 * 这类英文 id）；而工具栏的旧 `languageLabel(language)` 又按"首个匹配 TYPES 项"取值，
 * 使 `language:'text'` 的**全部 8 个扩展名**（txt/csv/tsv/log/toml/ini/conf/properties）
 * 都显示「CSV」，且 `.csv` 与 `.tsv` 无法区分。现在 `.txt` ⇒「纯文本 (txt)」、
 * `.csv` ⇒「CSV」、`.tsv` ⇒「TSV」，三者互不相同。
 */
function statusLanguageLabel(language: string, path: string | null): string {
  if (path) return resolveTypeForPath(path).label;
  return languageLabel(language);
}

/**
 * 编辑面板状态栏（F10 全字段）。
 *
 * 布局：左侧 `行:列` + 选中 + 总数；右侧 编码 / 换行符 / 语言 / 只读 / 脏标记。
 * 标签不存在（渲染期间被关闭）时返回 `null`，不编造任何字段。
 */
export function ScratchEditorStatusBar({ tabId }: ScratchEditorStatusBarProps) {
  const tab = useScratchStore((state) => state.tabs.find((item) => item.id === tabId));
  // 宿主上报的实时量；`null` = 未挂载 / 尚未上报 ⇒ 前三项降级显示（不编造数字）
  const stats = useScratchEditorStats(tabId);

  if (!tab) return null;

  // 未上报时降级：行:列 取 store 的 caret（1 基还原值），选中/总数显示占位符
  const line = stats ? stats.line : tab.caret.line;
  const col = stats ? stats.col : tab.caret.col;

  return (
    <div className="scratch-status" role="status" aria-label="编辑器状态">
      <span className="scratch-status-item" title="光标位置（1 基，列按码点计）">
        {line}:{col}
      </span>
      <span className="scratch-status-item" title="选中的字符数（按码点）">
        选中 {stats ? stats.selectedChars : '—'} 字符
      </span>
      <span className="scratch-status-item" title="文档总字符数（含换行符）">
        总 {stats ? stats.totalChars : '—'} 字符（含换行符）
      </span>
      <span className="scratch-status-grow" />
      <span className="scratch-status-item" title="文本编码">
        {ENCODING_LABEL[tab.encoding]}
      </span>
      <span className="scratch-status-item" title="换行符">
        {LINE_ENDING_LABEL[tab.lineEnding]}
      </span>
      <span className="scratch-status-item" title="语言（可手动切换）">
        {statusLanguageLabel(tab.language, tab.path)}
      </span>
      {tab.readOnly && (
        <span className="scratch-status-item scratch-status-readonly" title="只读标签页（不写回文件）">
          只读
        </span>
      )}
      <span
        className={
          tab.dirty
            ? 'scratch-status-item scratch-status-dirty'
            : 'scratch-status-item scratch-status-clean'
        }
        title={tab.dirty ? '有未保存的修改' : '无未保存修改'}
      >
        {tab.dirty ? '● 未保存' : '已保存'}
      </span>
    </div>
  );
}

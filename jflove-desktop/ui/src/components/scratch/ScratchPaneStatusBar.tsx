/**
 * 窗格状态栏（设计 v1.6.0 §4.1.1 / §4.1.4 的 F10 全字段 / AC-62）
 *
 * ## 逐字实现 §4.1.4 的口径
 *
 * | 字段 | 口径 |
 * | --- | --- |
 * | `行:列` | **1 基**。列 = 光标所在**字符**在该行的序号（按**码点**，不按 UTF-16 code unit） |
 * | 选中字符数 | `[...selectedText].length`（无选中 = 0） |
 * | 总字符数 | `[...doc.toString()].length` —— **含换行符**（`"abc\nDE"` = 6，U-14 定稿），文案注明 |
 * | 编码 / 换行符 / 语言 | 直接取 `pane`（语言随手动切换变化） |
 * | 只读标记 | `pane.readOnly === true` 时显示 |
 * | 脏标记 | `pane.dirty` |
 *
 * ## 实时数值从哪来（**已接线**）
 *
 * 前三项是 **CM6 运行时量**（`EditorState.selection` + `doc.length`），不由 store 持有。
 * 同事交付的 `ScratchEditorHost.tsx` 已实现这条广播通道：
 *
 * ```
 * updateListener → emitStats(paneId) → subscribeScratchEditorStats / useScratchEditorStats
 * ```
 *
 * 因此 `ScratchPane` 调 `useScratchEditorStats(pane.id)`，把结果作为 **`stats` prop** 传进来；
 * 本组件保持**纯展示**：不订阅、不持有全局注册表，给定 `pane` + `stats` 即可断言输出。
 * 未挂载 / 尚未上报时 `stats === null`，前三项**降级**为：`行:列` 取 `pane.caret`
 * （store 缓存的还原值），选中 / 总数显示 `—`（宁缺毋假，绝不编造数字）。
 *
 * > 口径提醒：「列」必须按**码点**（`[...prefix].length + 1`），总数**含换行符** ——
 * > 这两条由宿主 `computeEditorStats` 负责；本组件只展示，**不得再做任何"再算一遍"的加工**。
 *
 * ## 安全（AGENTS.md §9.14 / 设计 §7.4）
 *
 * 只接收**数字**与 `pane` 元数据（编码 / 换行符 / 语言 / 脏标记），**不接收正文**、
 * 不打印任何日志、DOM 里不出现路径与内容片段。
 */

import type { ScratchPane } from '../../types/scratch';

/** CM6 运行时量（由宿主上报；全部是**数字**，不含正文与路径） */
export interface PaneEditorStats {
  /** 1 基行号 */
  line: number;
  /** 1 基列号（**按码点**计，§4.1.4） */
  col: number;
  /** 选中字符数（按码点；无选中 = 0） */
  selectedChars: number;
  /** 总字符数（按码点，**含换行符**） */
  totalChars: number;
}

/** 编码 → 界面显示（逐项对齐 `TextEncoding`，新增取值时必须同步这里） */
const ENCODING_LABEL: Record<ScratchPane['encoding'], string> = {
  'utf-8': 'UTF-8',
  'utf-8-bom': 'UTF-8 BOM',
  gbk: 'GBK',
  'utf-16le': 'UTF-16 LE',
  'utf-16be': 'UTF-16 BE',
};

/** 换行符 → 界面显示（§4.1.4 要求显示 LF / CRLF） */
const LINE_ENDING_LABEL: Record<ScratchPane['lineEnding'], string> = {
  lf: 'LF',
  crlf: 'CRLF',
};

export interface ScratchPaneStatusBarProps {
  /** 所属窗格（编码 / 换行符 / 语言 / 只读 / 脏标记的唯一来源） */
  pane: ScratchPane;
  /** 宿主上报的实时量；`null` / 缺省 = 未挂载（降级显示，不编造数字） */
  stats?: PaneEditorStats | null;
}

/**
 * 窗格状态栏（F10 全字段）。
 *
 * 布局：左侧 `行:列` + 选中 + 总数；右侧 编码 / 换行符 / 语言 / 只读 / 脏标记。
 */
export function ScratchPaneStatusBar({ pane, stats = null }: ScratchPaneStatusBarProps) {
  // 未上报时降级：行:列 取 store 的 caret（1 基还原值），选中/总数显示占位符
  const line = stats ? stats.line : pane.caret.line;
  const col = stats ? stats.col : pane.caret.col;

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
        {ENCODING_LABEL[pane.encoding]}
      </span>
      <span className="scratch-status-item" title="换行符">
        {LINE_ENDING_LABEL[pane.lineEnding]}
      </span>
      <span className="scratch-status-item" title="语言（可手动切换）">
        {pane.language}
      </span>
      {pane.readOnly && (
        <span className="scratch-status-item scratch-status-readonly" title="只读窗格（不写回文件）">
          只读
        </span>
      )}
      <span
        className={
          pane.dirty
            ? 'scratch-status-item scratch-status-dirty'
            : 'scratch-status-item scratch-status-clean'
        }
        title={pane.dirty ? '有未保存的修改' : '无未保存修改'}
      >
        {pane.dirty ? '● 未保存' : '已保存'}
      </span>
    </div>
  );
}

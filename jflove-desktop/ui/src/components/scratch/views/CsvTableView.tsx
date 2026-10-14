/**
 * CSV / TSV 表格视图（**只读、不回写**）—— 设计 v1.6.0 §4.1.1 / §6.1.3 / §4.1.6 第 8 项
 *
 * ## 口径
 *
 * - 解析：`text.split(/\r?\n/)` 思路 + **首行分隔符投票**（`,` vs `\t`），实现见
 *   `utils/scratch/toolbox.ts` 的 `parseCsvTable`（纯函数，可单测）；
 * - 列对齐：所有行补齐到最大列数（`alignTableRows`）；
 * - **只读**：本组件没有 `onChange` / 不回写原文（AC-35：表格视图永不改文档）；
 * - 分隔符可**手动覆盖**（自动 / 逗号 / 制表符），覆盖只影响本视图的渲染，不动文档。
 *
 * ## 性能护栏
 *
 * 最多渲染 `MAX_ROWS` 行，超出只显示前若干行 + 一行说明（避免超大 CSV 一次挂出几十万 DOM）。
 *
 * ## 安全（AGENTS.md §9.14）
 *
 * 不 `console.*` 任何单元格内容；错误提示只说"第几行第几列字段数不一致"这类结构性信息。
 */

import { useMemo, useState } from 'react';
import type { CsvDelimiter } from '../../../utils/scratch/toolbox';
import { alignTableRows, detectDelimiter, parseDelimited } from '../../../utils/scratch/toolbox';

/** 单表最多渲染的行数（护栏） */
const MAX_ROWS = 5000;

export interface CsvTableViewProps {
  /** 文档正文（只读） */
  text: string;
  /** 外部指定的分隔符；不传则按首行投票自动识别 */
  delimiter?: CsvDelimiter;
  className?: string;
}

/** 分隔符展示名 */
function delimiterLabel(delimiter: CsvDelimiter): string {
  return delimiter === '\t' ? '制表符 (\\t)' : '逗号 (,)';
}

/**
 * CSV / TSV 表格（只读）。
 *
 * 注意：**列对齐只发生在渲染副本上**，原文一个字符都不动（AC-35）。
 */
export function CsvTableView({ text, delimiter, className }: CsvTableViewProps) {
  const [override, setOverride] = useState<CsvDelimiter | 'auto'>('auto');
  const effective: CsvDelimiter = override === 'auto' ? (delimiter ?? detectDelimiter(text)) : override;

  const rows = useMemo(() => alignTableRows(parseDelimited(text, effective)), [text, effective]);
  const header = rows[0] ?? [];
  const columnCount = header.length;
  const bodyRows = rows.slice(1);
  const shownRows = bodyRows.slice(0, MAX_ROWS);
  const hiddenRows = bodyRows.length - shownRows.length;

  return (
    <div className={`flex h-full min-h-0 flex-col ${className ?? ''}`}>
      {/* 视图自身的状态条：分隔符来源 + 规模 + 手动覆盖（只影响本视图） */}
      <div className="flex flex-wrap items-center gap-2 border-b border-black/10 px-2 py-1 text-xs dark:border-white/10">
        <span className="opacity-70">
          分隔符：{delimiterLabel(effective)}（{override === 'auto' ? '首行投票' : '手动'}）
        </span>
        <span className="opacity-50">
          {rows.length} 行 × {columnCount} 列
        </span>
        <span className="ml-auto flex items-center gap-1">
          {(['auto', ',', '\t'] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setOverride(option)}
              className={`rounded px-1.5 py-0.5 ${
                override === option
                  ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400'
                  : 'opacity-60 hover:opacity-100'
              }`}
            >
              {option === 'auto' ? '自动' : option === ',' ? '逗号' : '制表符'}
            </button>
          ))}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {rows.length === 0 ? (
          <div className="p-3 text-xs opacity-60">空内容，没有可显示的表格</div>
        ) : (
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 z-10 bg-black/5 backdrop-blur dark:bg-white/10">
              <tr>
                <th className="border border-black/10 px-2 py-1 text-right font-normal opacity-50 dark:border-white/10">
                  #
                </th>
                {header.map((cell, index) => (
                  <th
                    key={index}
                    className="border border-black/10 px-2 py-1 text-left font-semibold whitespace-nowrap dark:border-white/10"
                  >
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shownRows.map((row, rowIndex) => (
                <tr key={rowIndex} className="odd:bg-black/[0.02] dark:odd:bg-white/[0.03]">
                  <td className="border border-black/10 px-2 py-1 text-right opacity-40 dark:border-white/10">
                    {rowIndex + 1}
                  </td>
                  {row.map((cell, cellIndex) => (
                    <td
                      key={cellIndex}
                      className="max-w-[32rem] truncate border border-black/10 px-2 py-1 align-top dark:border-white/10"
                      title={cell.length > 80 ? cell : undefined}
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {hiddenRows > 0 ? (
          <div className="p-2 text-center text-xs opacity-60">
            仅显示前 {MAX_ROWS} 行，还有 {hiddenRows} 行未渲染（表格视图只读，不修改原文）
          </div>
        ) : null}
      </div>
    </div>
  );
}

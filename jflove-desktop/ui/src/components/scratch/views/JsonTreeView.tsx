/**
 * JSON 树视图（**只读**，可折叠）—— 设计 v1.6.0 §4.1.1 / §6.1.3 / §4.1.6 第 6 项
 *
 * ## 口径
 *
 * - 自绘递归组件（`<details>` / `<summary>`），不引入任何 JSON 树库；
 * - `JSON.parse` 失败时**只提示，原文逐字符不变**（AC-31：非法 JSON 只提示、不改原文）——
 *   本组件**不接收**任何回写回调，从类型上就不可能有"顺手修复原文"的路径；
 * - 只读：不提供编辑、不提供"应用修改"，也没有任何 `onChange`。
 *
 * ## 性能护栏
 *
 * 单个容器最多渲染 `MAX_CHILDREN` 个子节点（超出只显示"还有 N 项"），
 * 避免打开几 MB 的数组时一次挂出几十万个 DOM 节点（NFR：首次呼出 ≤ 1.5 s）。
 *
 * ## 安全（AGENTS.md §9.14）
 *
 * 解析失败的错误文案来自 `toolbox.ts` 的 `tryParseJson`，**只描述位置**，
 * 不回显文档内容；本组件也不 `console.*` 任何内容。
 */

import { useMemo } from 'react';
import { tryParseJson } from '../../../utils/scratch/toolbox';

/** 单个容器最多渲染的子节点数（护栏，超出折叠成一行说明） */
const MAX_CHILDREN = 500;

export interface JsonTreeViewProps {
  /** 待解析的文档正文（**不修改**） */
  text: string;
  /** 默认展开的层数（默认 2；更深的层折叠，避免大树一次全开） */
  defaultExpandDepth?: number;
  className?: string;
}

/** 值的类型名（用于 `<summary>` 上的 `{…}` / `[…]` 提示） */
function kindOf(value: unknown): 'object' | 'array' | 'string' | 'number' | 'boolean' | 'null' {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  return typeof value as 'object' | 'string' | 'number' | 'boolean';
}

/** 叶子值的展示文本（字符串加引号；长字符串截断显示） */
function leafText(value: unknown): string {
  if (typeof value === 'string') {
    const shown = value.length > 200 ? `${value.slice(0, 200)}…` : value;
    return `"${shown}"`;
  }
  if (value === null) return 'null';
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return String(value);
}

/** 单个子节点（key 是对象的键名或数组下标，仅用于显示） */
function JsonNode({
  label,
  value,
  depth,
  defaultExpandDepth,
  isLast,
}: {
  label: string;
  value: unknown;
  depth: number;
  defaultExpandDepth: number;
  isLast: boolean;
}) {
  const kind = kindOf(value);

  if (kind !== 'object' && kind !== 'array') {
    return (
      <div className="pl-4 font-mono text-xs leading-5">
        <span className="text-sky-600 dark:text-sky-400">{label}</span>
        <span className="opacity-60">: </span>
        <span className="break-all">{leafText(value)}</span>
        {isLast ? null : <span className="opacity-60">,</span>}
      </div>
    );
  }

  const entries: [string, unknown][] =
    kind === 'array'
      ? (value as unknown[]).map((item, index) => [String(index), item])
      : Object.entries(value as Record<string, unknown>);
  const shown = entries.slice(0, MAX_CHILDREN);
  const hidden = entries.length - shown.length;
  const summaryText = kind === 'array' ? `[…] ${entries.length} 项` : `{…} ${entries.length} 项`;

  return (
    <details
      className="pl-4 font-mono text-xs leading-5"
      open={depth < defaultExpandDepth}
    >
      <summary className="cursor-pointer select-none">
        <span className="text-sky-600 dark:text-sky-400">{label}</span>
        <span className="opacity-60">: </span>
        <span className="opacity-70">{summaryText}</span>
        {isLast ? null : <span className="opacity-60">,</span>}
      </summary>
      {shown.map(([key, item], index) => (
        <JsonNode
          key={`${depth}-${key}`}
          label={kind === 'array' ? key : `"${key}"`}
          value={item}
          depth={depth + 1}
          defaultExpandDepth={defaultExpandDepth}
          isLast={index === shown.length - 1 && hidden === 0}
        />
      ))}
      {hidden > 0 ? (
        <div className="pl-4 text-xs opacity-60">… 还有 {hidden} 项未显示（单容器上限 {MAX_CHILDREN}）</div>
      ) : null}
    </details>
  );
}

/**
 * JSON 树（只读）。
 *
 * 解析失败 ⇒ 顶部只显示一行提示（`JSON 解析失败（第 N 个字符附近）`），**不渲染任何内容**，
 * 也**不提供**任何"修复原文"的按钮（AC-31）。
 */
export function JsonTreeView({ text, defaultExpandDepth = 2, className }: JsonTreeViewProps) {
  const parsed = useMemo(() => tryParseJson(text), [text]);

  if (!parsed.ok) {
    return (
      <div
        role="status"
        className={`flex h-full items-center justify-center p-4 text-center text-xs text-amber-600 ${className ?? ''}`}
      >
        {parsed.error}（原文未改动）
      </div>
    );
  }

  const kind = kindOf(parsed.value);
  if (kind !== 'object' && kind !== 'array') {
    // 顶层是标量：树没有意义，直接按值显示（仍然是只读视图）
    return (
      <div className={`h-full overflow-auto p-3 font-mono text-xs ${className ?? ''}`}>
        {leafText(parsed.value)}
      </div>
    );
  }

  return (
    <div className={`h-full overflow-auto p-3 ${className ?? ''}`}>
      <JsonNode
        label="根"
        value={parsed.value}
        depth={0}
        defaultExpandDepth={defaultExpandDepth}
        isLast
      />
    </div>
  );
}

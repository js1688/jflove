/**
 * 两段文本对比（diff）—— 设计 v1.6.0 §4.1.1 / §6.1.3 / §4.1.6 第 11 项
 *
 * ## 口径
 *
 * - 用 `@codemirror/merge` 的 `MergeView`（行级 diff），**两侧都是只读**
 *   （`EditorState.readOnly.of(true)`，§6.1.3 表格）；
 * - 第二段来源**三选一**：剪贴板 / 另一标签页 / 本地文件（`editor.readTextOnly`）；
 * - 第一段 = 当前标签页正文（由调用方传入，通常是会话表的 `getText()`）；
 * - 本地文件读取走 `scratchService.readTextOnly`（Python 侧读盘 + 类型校验），
 *   单条失败**不抛**，返回 `ok:false` + 文案 ⇒ 这里只显示文案（**不含完整路径**）。
 *
 * ## 安全（AGENTS.md §9.6 / §9.14 / R-1）
 *
 * - 渲染层**没有文件系统出口**：本组件不 import 任何 `fs`/`path`，只用 `scratchService` 的白名单桥；
 * - 剪贴板读取是**用户主动点击**才发生（不轮询、不后台读）；
 * - 不 `console.*` 任何路径或内容；错误文案只透传 Python 侧已脱敏的 `message`。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { scratchService } from '../../../services/scratch-service';
import { useScratchStore } from '../../../stores/scratch-store';
import type { MergeView } from '../../../types/codemirror-bundle';
import type { ScratchTab } from '../../../types/scratch';
import { loadEditorBundle } from '../../../utils/scratch/load-editor-bundle';
import { installEditorPhrases } from '../../../utils/scratch/editor-phrases';
import { getScratchEditorSession } from '../ScratchEditorHost';

/** 第二段文本的来源 */
type DiffSource = 'clipboard' | 'pane' | 'file';

export interface DiffViewProps {
  /** 当前标签页（第一段文本所属；也用于列出"另一标签页"候选） */
  pane: ScratchTab;
  /** 第一段文本（当前标签页实时正文） */
  text: string;
  /** 关闭回调（diff 以覆盖层形式出现时用；不传则不显示关闭按钮） */
  onClose?: () => void;
  className?: string;
}

/** 来源按钮的定义（顺序即 UI 顺序） */
const SOURCE_LABELS: { id: DiffSource; label: string; hint: string }[] = [
  { id: 'clipboard', label: '剪贴板', hint: '读取系统剪贴板作为第二段' },
  { id: 'pane', label: '另一标签页', hint: '选一个已挂载标签页的实时正文' },
  { id: 'file', label: '本地文件', hint: '通过原生对话框只读读取一个本地文件' },
];

/**
 * 「剪贴板来源不可用」的**唯一中文文案**（需求 v1.6.1 §0.5.4 **C-1①** / 设计 §15.8.3）。
 *
 * ## 三条硬性口径（写在这里，改文案时不得破坏）
 *
 * 1. **C-1①**：只放中文提示，**绝不**把 `NotAllowedError` / `SecurityError` 之类的
 *    `error.message` 原文（英文）写到界面上 —— 那是渲染层实现细节，对用户零收益；
 * 2. **C-1②**：命中本文案的路径**只** `setMessage(...)` 然后 `return`，
 *    **不得** `setSecond('')` / `setSecond(null)` ⇒ 第二段文本与已呈现的对比结果**逐字符不变**；
 * 3. **C-1③**：文案里必须点名至少一个**可改用的替代来源**，且用词与 `SOURCE_LABELS`
 *    的 `label` **逐字一致**（否则用户在界面上找不到对应按钮）。
 *
 * ## 为什么不"开启权限让来源可用"（设计 §15.8.2 裁决 = (B) 降级）
 *
 * 开 `JavascriptCanAccessClipboard` 会让**任何**渲染层脚本（含第三方依赖）能静默读系统剪贴板，
 * 为修一个只读来源而扩大**整层**能力面，违反 `AGENTS.md §9.6` 的能力面最小化 ⇒ 明确不做。
 *
 * ## 为什么 try/catch 永远不删（设计 §15.8.4 末段）
 *
 * 即便将来真机取证显示该来源在当前平台**实测可用**，这段防御性分支也**不删**：
 * 权限可能被未来的系统策略关闭，保留它成本为零；此时用例需标注为"防御性分支"。
 */
const CLIPBOARD_UNAVAILABLE_HINT =
  '当前环境不允许读取系统剪贴板，请改用「另一标签页」或「本地文件」作为第二段来源。';

/**
 * 两段文本对比视图。
 *
 * 只读性：两侧都带 `EditorState.readOnly.of(true)`，且本组件不提供任何回写入口。
 */
export function DiffView({ pane, text, onClose, className }: DiffViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mergeRef = useRef<MergeView | null>(null);

  const [source, setSource] = useState<DiffSource | null>(null);
  const [second, setSecond] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [paneChoice, setPaneChoice] = useState<string>('');

  /** 可选的"另一标签页"（排除自己；只列**已挂载**的实例，未挂载标签页没有正文可取） */
  const tabs = useScratchStore((state) => state.tabs);
  const candidates = useMemo(
    () => tabs.filter((item) => item.id !== pane.id && getScratchEditorSession(item.id) !== null),
    [tabs, pane.id],
  );

  /* ── 取第二段文本（三条来源） ── */
  async function pickSecond(nextSource: DiffSource, paneId?: string): Promise<void> {
    setSource(nextSource);
    setBusy(true);
    setMessage(null);
    try {
      if (nextSource === 'clipboard') {
        if (!navigator.clipboard || typeof navigator.clipboard.readText !== 'function') {
          // C-1①：中文提示（旧文案收敛到唯一常量）；C-1②：**不改**第二段、**不清**结果
          setMessage(CLIPBOARD_UNAVAILABLE_HINT);
          return;
        }
        let content = '';
        try {
          content = await navigator.clipboard.readText();
        } catch {
          // ★ C-1① 的核心落点：**吞掉**原始异常（不把 `NotAllowedError` / `SecurityError`
          //   之类原文抛到界面），只给中文提示 + 替代来源（C-1③）；
          //   **不** `setSecond('')` —— 失败绝不能顺带把第二段清空（C-1②）
          setMessage(CLIPBOARD_UNAVAILABLE_HINT);
          return;
        }
        if (!content) {
          setMessage('剪贴板为空'); // 保留既有语义（空剪贴板 ≠ 不可用）
          return;
        }
        setSecond(content);
        return;
      }
      if (nextSource === 'pane') {
        const target = paneId ?? paneChoice;
        const session = target ? getScratchEditorSession(target) : null;
        if (!session) {
          setMessage('选中的标签页尚未挂载，没有可用正文');
          return;
        }
        setSecond(session.getText());
        return;
      }
      // 本地文件：原生对话框（只读用途）→ 类型校验 + 读取全在 Python 侧
      const path = await scratchService.pickReadOnlyFile();
      if (!path) {
        setMessage('已取消选择文件');
        return;
      }
      const item = await scratchService.readTextOnly(path);
      if (!item.ok) {
        // Python 侧文案已脱敏（不含完整路径）
        setMessage(item.message || '该文件无法作为文本读取');
        return;
      }
      setSecond(item.text);
    } catch {
      // 兜的只剩"未知异常"：`pane` 来源不会抛，`file` 来源的失败已在上面按 `item.ok` 处理。
      // 设计 §15.8.3 明确**收窄**：不把 `error.message` 上屏（可能是桥/内核的实现细节）。
      setMessage('读取第二段文本失败，请重试或改用其它来源');
    } finally {
      setBusy(false);
    }
  }

  /* ── MergeView 生命周期：两侧只读；第二段变化时重建（diff 视图本身不参与编辑热路径） ── */
  useEffect(() => {
    const container = containerRef.current;
    if (!container || second === null) return;
    let destroyed = false;
    let created: MergeView | null = null;

    void (async () => {
      try {
        const api = await loadEditorBundle();
        if (destroyed) return;
        // 中文短语（AC-74⑤：`$ unchanged lines` 等）：`config.a` / `config.b` 会被 CM6 建成
        // **两个各自独立**的 `EditorState`（M15 实测）⇒ `phrases` facet **不共享**，
        // 必须**两侧各装一份**（合并视图的折叠提示只装一侧会在另一侧回落成英文，设计 §15.2.3）。
        const phrases = installEditorPhrases(api);
        const config = {
          a: { doc: text, extensions: [api.EditorState.readOnly.of(true), ...phrases] },
          b: { doc: second, extensions: [api.EditorState.readOnly.of(true), ...phrases] },
          parent: container,
          highlightChanges: true,
          gutter: true,
          collapseUnchanged: { margin: 3, minSize: 4 },
        };
        const existing = mergeRef.current;
        if (existing) {
          existing.reconfigure(config);
          return;
        }
        created = new api.MergeView(config);
        mergeRef.current = created;
      } catch {
        if (!destroyed) setMessage('对比视图初始化失败（编辑器内核或 diff 模块不可用）');
      }
    })();

    return () => {
      destroyed = true;
      const view = created ?? mergeRef.current;
      if (view) {
        view.destroy();
        if (mergeRef.current === view) mergeRef.current = null;
      }
    };
  }, [second, text]);

  /* ── 组件卸载：确保实例销毁（不泄漏） ── */
  useEffect(
    () => () => {
      const view = mergeRef.current;
      mergeRef.current = null;
      if (view) view.destroy();
    },
    [],
  );

  const hasResult = second !== null;

  return (
    <div className={`flex h-full min-h-0 flex-col bg-white/95 dark:bg-neutral-900/95 ${className ?? ''}`}>
      <div className="flex flex-wrap items-center gap-2 border-b border-black/10 px-2 py-1 text-xs dark:border-white/10">
        <span className="opacity-70">第二段来源：</span>
        {SOURCE_LABELS.map((item) => (
          <button
            key={item.id}
            type="button"
            title={item.hint}
            disabled={busy}
            onClick={() => {
              if (item.id === 'pane') {
                setSource('pane');
                return; // 先选标签页再取文本（下一段 UI）
              }
              void pickSecond(item.id);
            }}
            className={`rounded px-2 py-0.5 disabled:opacity-40 ${
              source === item.id ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400' : 'hover:bg-black/5 dark:hover:bg-white/10'
            }`}
          >
            {item.label}
          </button>
        ))}

        {source === 'pane' ? (
          <span className="flex items-center gap-1">
            <select
              value={paneChoice}
              onChange={(event) => {
                setPaneChoice(event.target.value);
                void pickSecond('pane', event.target.value);
              }}
              className="rounded border border-black/15 bg-transparent px-1 py-0.5 dark:border-white/20"
            >
              <option value="">选择标签页…</option>
              {candidates.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </span>
        ) : null}

        <span className="ml-auto flex items-center gap-2 opacity-60">
          {busy ? <span>读取中…</span> : null}
          {hasResult ? <span>已载入第二段（{second.length} 字符）</span> : null}
          {onClose ? (
            <button type="button" onClick={onClose} className="rounded px-2 py-0.5 hover:bg-black/5 dark:hover:bg-white/10">
              关闭对比
            </button>
          ) : null}
        </span>
      </div>

      {message ? (
        <div role="status" className="border-b border-black/10 px-2 py-1 text-xs text-amber-600 dark:border-white/10">
          {message}
        </div>
      ) : null}

      <div className="relative min-h-0 flex-1">
        <div ref={containerRef} className="h-full w-full overflow-hidden" />
        {hasResult ? null : (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs opacity-60">
            选一种来源载入第二段文本后开始对比（两侧均只读）
          </div>
        )}
      </div>
    </div>
  );
}

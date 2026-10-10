import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router';
import { useNoteStore } from '../stores/note-store';
import { PageHeader } from '../components/PageHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { MarkdownRenderer } from '../components/markdown';
import { Badge, Button, Icon, IconButton, Segmented, toast, type IconName } from '../components/ui';
import { AUTO_SAVE_INTERVAL_MS } from '../config/constants';
import { planInsertion } from '../utils/markdown-insert';

type ViewMode = 'edit' | 'preview' | 'split';

/**
 * 由笔记内容判定进入页面时的**初始**视图模式（v1.5.1 #4-b，AC-9 / AC-10）。
 *
 * 为什么用内容判定而不是"新建标记"：新建与打开既有笔记**共用** `notes/:noteId` 路由
 * （新建是先建一个空文件再跳转），没有可区分的路由标记。
 *
 * - 有内容 → `'preview'`：已有笔记以阅读为主（用户反馈"编写的概率比较小"）；
 * - 空 / 纯空白 → `'edit'`：新建的目的就是写。
 *
 * 抽成**纯函数并 export**：默认模式判定是本版本的核心行为，抽出来才能被单测直接覆盖
 * （`.claude/skills/LESSONS.md` L25）。
 */
export function resolveInitialViewMode(content: string): ViewMode {
  return content.trim() !== '' ? 'preview' : 'edit';
}

/** Markdown 工具栏按钮（v1.5.0：图标由字符/emoji 改为矢量图标，需求 AC-5） */
interface ToolbarButton {
  label: string;
  icon: IconName;
  /**
   * 插入的 Markdown 语法，`|` 表示**插入后光标的位置**。
   * ⚠ 这个占位符约定**只对工具栏按钮成立**（调用时显式传 `useCursorMarker = true`）；
   * 图表模板一律原样插入 —— 否则模板里的 mermaid 语法 `||--o{` 会被吃掉一根竖线。
   */
  insert: string;
}

const TOOLBAR_BUTTONS: ToolbarButton[] = [
  { label: '加粗', icon: 'bold', insert: '**|**' },
  { label: '斜体', icon: 'italic', insert: '*|*' },
  { label: '删除线', icon: 'strike', insert: '~~|~~' },
  { label: '一级标题', icon: 'h1', insert: '\n# |\n' },
  { label: '二级标题', icon: 'h2', insert: '\n## |\n' },
  { label: '三级标题', icon: 'h3', insert: '\n### |\n' },
  { label: '无序列表', icon: 'listUl', insert: '\n- |\n' },
  { label: '有序列表', icon: 'listOl', insert: '\n1. |\n' },
  { label: '引用', icon: 'quote', insert: '\n> |\n' },
  { label: '行内代码', icon: 'inlineCode', insert: '`|`' },
  { label: '链接', icon: 'link', insert: '[|](url)' },
  { label: '图片', icon: 'imageInsert', insert: '![|](图片地址)' },
];

/** Mermaid 图表模板（需求 AC-15：编辑器提供「插入图表」入口与类型模板） */
const DIAGRAM_TEMPLATES: { label: string; body: string }[] = [
  {
    label: '流程图',
    body: `
\`\`\`mermaid
flowchart LR
    A[开始] --> B{条件判断}
    B -- 是 --> C[分支一]
    B -- 否 --> D[分支二]
\`\`\`
`,
  },
  {
    label: '时序图',
    body: `
\`\`\`mermaid
sequenceDiagram
    participant C as 客户端
    participant S as 服务端
    C->>S: 请求
    S-->>C: 响应
    Note over C,S: 说明
\`\`\`
`,
  },
  {
    label: '状态图',
    body: `
\`\`\`mermaid
stateDiagram-v2
    [*] --> 待处理
    待处理 --> 处理中: 开始
    处理中 --> 完成: 成功
    处理中 --> 失败: 出错
    完成 --> [*]
\`\`\`
`,
  },
  {
    label: '甘特图',
    body: `
\`\`\`mermaid
gantt
    title 项目排期
    dateFormat YYYY-MM-DD
    section 阶段一
    需求梳理 :done, a1, 2026-01-01, 5d
    方案设计 :active, a2, after a1, 6d
\`\`\`
`,
  },
  {
    label: '饼图',
    body: `
\`\`\`mermaid
pie showData
    title 构成占比
    "A" : 45
    "B" : 30
    "C" : 25
\`\`\`
`,
  },
  {
    label: '类图',
    body: `
\`\`\`mermaid
classDiagram
    class 用户 {
      +int id
      +string name
      +登录()
    }
    用户 --> 会话 : 拥有
\`\`\`
`,
  },
  {
    // v1.5.0：三端模板统一 —— 移动端已含 ER 图，这里补齐
    // （与 note_page.py / diagram_templates.dart 逐字一致）
    label: 'ER 图',
    body: `
\`\`\`mermaid
erDiagram
    用户 ||--o{ 会话 : 拥有
    用户 {
      int id
      string name
    }
\`\`\`
`,
  },
];

/** 笔记编辑页 */
export function NoteEditPage() {
  const { noteId } = useParams<{ noteId: string }>();
  const navigate = useNavigate();
  const store = useNoteStore();

  // v1.5.1 #4-b：初值由 `'split'` 改为 `'edit'` —— 加载期间不闪一下空预览；
  // 加载成功后由 `resolveInitialViewMode` 按内容改写为 `'preview'`（有内容时）。
  const [viewMode, setViewMode] = useState<ViewMode>('edit');
  const [showDiscardDialog, setShowDiscardDialog] = useState(false);
  const [showDiagramMenu, setShowDiagramMenu] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<string | null>(null);
  // v1.5.1 #4-a（AC-8）：大纲栏折叠状态，仅组件内存（**不写 localStorage**），下次进入回到默认展开
  const [outlineOpen, setOutlineOpen] = useState(true);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** v1.5.1 #4-b：已完成"内容驱动"默认模式判定的文件名（同一文件名只判定一次） */
  const modeResolvedForRef = useRef<string | null>(null);
  /**
   * v1.5.1 #4-b（AC-11）：**手动切换过模式的那篇笔记**的文件名。
   * ⚠ 必须按 filename 记账，不能用一个全局布尔值（Phase 5 审查 M-4）：
   * 全局布尔的语义是"本组件实例内只要手动切过一次就永久生效"，于是之后打开**任何**有内容的
   * 笔记都会停在 `edit`，违背 AC-9「有内容 → 预览」。按 filename 记账后：同一篇笔记内手动切换
   * 依旧不再被自动改写（AC-11），切到另一篇笔记则重新按内容判定一次（AC-9 / AC-10）。
   */
  const userSwitchedModeForRef = useRef<string | null>(null);

  const filename = noteId ? decodeURIComponent(noteId) : null;

  // 加载笔记（v1.5.1 #4-b：加载成功后按内容判定一次默认模式）
  useEffect(() => {
    if (!filename) return;
    useNoteStore
      .getState()
      .loadNote(filename)
      .then(() => {
        // **本篇笔记**手动切换过模式（AC-11），或该文件名已判定过 → 不覆盖当前模式。
        // 判定只发生在加载成功这一刻，自动保存/内容变化都不会再改写它。
        if (userSwitchedModeForRef.current === filename) return;
        if (modeResolvedForRef.current === filename) return;
        modeResolvedForRef.current = filename;
        // 必须取 store 的**实时**内容：闭包里的 store 是本次渲染的快照，可能还是上一篇笔记的
        setViewMode(resolveInitialViewMode(useNoteStore.getState().currentContent));
      })
      .catch(() => {
        navigate('/notes', { replace: true });
      });
  }, [filename, navigate]);

  // 自动保存
  useEffect(() => {
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);

    if (store.isModified) {
      autoSaveTimerRef.current = setTimeout(() => {
        // 自动保存失败不阻塞用户，静默记录；用户可手动保存覆盖
        useNoteStore.getState().saveNote().catch(() => {});
      }, AUTO_SAVE_INTERVAL_MS);
    }

    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [store.currentContent, store.isModified]);

  // 离开确认
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (store.isModified) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [store.isModified]);

  const handleBack = useCallback(() => {
    if (store.isModified) {
      setShowDiscardDialog(true);
      setPendingNavigation('/notes');
    } else {
      navigate('/notes');
    }
  }, [store.isModified, navigate]);

  const handleSave = useCallback(async () => {
    try {
      await store.saveNote();
      toast.success('笔记已保存');
    } catch (e) {
      toast.error('保存失败', e instanceof Error ? e.message : undefined);
    }
  }, [store]);

  const handleDiscard = () => {
    store.discardChanges();
    setShowDiscardDialog(false);
    if (pendingNavigation) navigate(pendingNavigation);
  };

  /**
   * 工具栏 / 图表模板插入 Markdown（在光标处插入）。
   *
   * ⚠ `useCursorMarker` **默认 false**：只有工具栏按钮（加粗、斜体、链接…）才显式打开它，
   * 因为它表示"把插入文本里的第一个 `|` 当作光标落点"。
   * 图表模板正文里可能**合法地**出现 `|`（mermaid 的 ER 关系语法就是 `||--o{`）——
   * 早期实现无条件解析占位符，把 ER 模板的第一根竖线吃掉了，插入即报语法错误。
   * 详见 `utils/markdown-insert.ts` 的说明。
   */
  const insertMarkdown = useCallback(
    (template: string, useCursorMarker = false) => {
      const textarea = textareaRef.current;
      if (!textarea) return;

      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const selected = store.currentContent.slice(start, end);
      const before = store.currentContent.slice(0, start);
      const after = store.currentContent.slice(end);

      const plan = planInsertion(template, selected, useCursorMarker);
      store.setContent(before + plan.text + after);

      // 恢复光标：工具栏按钮落在"被包住的内容之后"；图表模板落在插入内容之后
      requestAnimationFrame(() => {
        const cursorPos = before.length + plan.cursorOffset;
        textarea.focus();
        textarea.setSelectionRange(cursorPos, cursorPos);
      });
    },
    [store],
  );

  /**
   * 跳转到第 n 张图表的源码位置（AC-12 的「跳到源码」）。
   * 做法：数 mermaid 围栏出现的位置，定位到目标围栏所在行，把光标移过去并滚动到可见。
   */
  const jumpToDiagramSource = useCallback(
    (targetIndex: number) => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      // 编辑模式下才能定位光标
      if (viewMode === 'preview') setViewMode('split');

      const lines = store.currentContent.split('\n');
      let seen = -1;
      let targetLine = 0;
      for (let i = 0; i < lines.length; i++) {
        if (/^\s*```+\s*mermaid\s*$/i.test(lines[i])) {
          seen++;
          if (seen === targetIndex) {
            targetLine = i;
            break;
          }
        }
      }
      const offset = lines.slice(0, targetLine).reduce((acc, l) => acc + l.length + 1, 0);

      requestAnimationFrame(() => {
        textarea.focus();
        textarea.setSelectionRange(offset, offset);
        // 粗略滚动到目标行（按行高估算）
        const lineHeight = 24;
        textarea.scrollTop = Math.max(1, targetLine * lineHeight - textarea.clientHeight / 3);
      });
    },
    [store.currentContent, viewMode],
  );

  /** 大纲（预览模式下用于快速跳转；<=3 级与 v1.4.2 行为一致） */
  const headings = useMemo(
    () =>
      store.currentContent
        .split('\n')
        .filter((line) => /^#{1,3}\s/.test(line))
        .map((line) => {
          const level = line.match(/^(#{1,3})/)![1].length;
          return { level, text: line.replace(/^#{1,3}\s/, '') };
        }),
    [store.currentContent],
  );

  /** 大纲点击跳转到预览区对应标题（对标桌面端 _on_outline_clicked） */
  const handleOutlineClick = (text: string) => {
    const preview = document.querySelector('.markdown-body');
    const nodes = preview?.querySelectorAll('h1, h2, h3');
    for (const h of nodes ?? []) {
      if ((h.textContent || '').trim() === text) {
        h.scrollIntoView({ behavior: 'smooth', block: 'start' });
        break;
      }
    }
  };

  if (!filename) return null;

  const showEditor = viewMode === 'edit' || viewMode === 'split';
  const showPreview = viewMode === 'preview' || viewMode === 'split';

  /**
   * v1.5.1 #4-a（AC-6 / AC-7）：大纲栏的可用条件 —— 预览 / 分屏 + 笔记含标题。
   * 编辑模式不显示：大纲跳转依赖预览区 DOM（`document.querySelector('.markdown-body')`），
   * 编辑模式下预览区不存在，展示一个点了没反应的栏不如不展示。
   * 加载中（`store.isLoading`）也不显示，避免拿上一篇笔记的标题闪一下。
   */
  const outlineAvailable =
    !store.isLoading && (viewMode === 'preview' || viewMode === 'split') && headings.length > 0;
  /** 是否真的把大纲栏渲染出来（可用 + 用户未折叠） */
  const showOutline = outlineAvailable && outlineOpen;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={filename}
        subtitle="笔记管理 / Markdown"
        onBack={handleBack}
        actions={
          <div className="flex items-center gap-2">
            <Segmented
              value={viewMode}
              onChange={(mode) => {
                // v1.5.1 #4-b（AC-11）：按 filename 记账 —— **本篇笔记**不再被自动改写；
                // 切到另一篇笔记时重新按内容判定（M-4 修法）
                userSwitchedModeForRef.current = filename;
                setViewMode(mode);
              }}
              options={[
                { value: 'edit', label: '编辑', icon: 'edit' },
                { value: 'split', label: '分屏', icon: 'columns' },
                { value: 'preview', label: '预览', icon: 'view' },
              ]}
            />
            {/* v1.5.1 #4-a（AC-8）：大纲栏折叠开关（仅在"有标题的预览/分屏"下出现，
                这样按钮不会变成一个点了没反应的死开关）；折叠时按钮呈激活态 `data-on` */}
            {outlineAvailable && (
              <IconButton
                icon="list"
                label={outlineOpen ? '折叠大纲' : '展开大纲'}
                size="sm"
                data-on={!outlineOpen}
                aria-expanded={outlineOpen}
                onClick={() => setOutlineOpen((v) => !v)}
              />
            )}
            {store.isModified ? (
              <Badge tone="warning" icon="warning">
                未保存
              </Badge>
            ) : (
              <Badge tone="success" icon="checked">
                已保存
              </Badge>
            )}
            <Button
              variant={store.isModified ? 'primary' : 'secondary'}
              size="sm"
              icon="save"
              onClick={handleSave}
              disabled={!store.isModified}
            >
              保存
            </Button>
          </div>
        }
      />

      {store.isLoading && <LoadingSpinner text="加载笔记…" />}

      {!store.isLoading && (
        <>
          {/* Markdown 工具栏（编辑/分屏模式） */}
          {showEditor && (
            <div className="relative flex shrink-0 flex-wrap items-center gap-1 border-b border-line-subtle bg-surface px-3 py-2">
              {TOOLBAR_BUTTONS.map((btn) => (
                <button
                  key={btn.label}
                  type="button"
                  onClick={() => insertMarkdown(btn.insert, true)}
                  title={btn.label}
                  aria-label={btn.label}
                  className="tb"
                >
                  <Icon name={btn.icon} size="sm" />
                </button>
              ))}

              <span className="mx-1.5 h-[18px] w-px bg-line" />

              {/* 插入图表（AC-15） */}
              <button
                type="button"
                onClick={() => setShowDiagramMenu((v) => !v)}
                title="插入 Mermaid 图表"
                aria-label="插入 Mermaid 图表"
                aria-expanded={showDiagramMenu}
                className="tb"
                data-on={showDiagramMenu}
              >
                <Icon name="diagram" size="sm" />
              </button>

              {showDiagramMenu && (
                <div
                  className="card absolute top-[46px] left-3 z-30 w-[300px] p-3"
                  style={{ boxShadow: 'var(--e4)' }}
                >
                  <div className="mb-2 flex items-center gap-2">
                    <Icon name="diagram" className="text-brand-500" />
                    <span className="text-[13px] font-semibold">插入 Mermaid 图表</span>
                    <div className="grow" />
                    <IconButton
                      icon="close"
                      label="关闭"
                      size="sm"
                      onClick={() => setShowDiagramMenu(false)}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {DIAGRAM_TEMPLATES.map((tpl) => (
                      <button
                        key={tpl.label}
                        type="button"
                        className="btn btn-secondary btn-sm justify-start"
                        onClick={() => {
                          insertMarkdown(tpl.body);
                          setShowDiagramMenu(false);
                        }}
                      >
                        {tpl.label}
                      </button>
                    ))}
                  </div>
                  <p className="mt-2.5 mb-0 text-[11.5px] leading-relaxed text-subtle">
                    插入后编辑区可继续修改源码，预览区会自动渲染成图；语法有误时只影响该图表。
                  </p>
                </div>
              )}
            </div>
          )}

          {/* 编辑 / 预览 */}
          <div className={`flex min-h-0 flex-1 ${showEditor && showPreview ? 'divide-x divide-line-subtle' : ''}`}>
            {showEditor && (
              <div className={showPreview ? 'w-1/2' : 'w-full'}>
                <textarea
                  ref={textareaRef}
                  value={store.currentContent}
                  onChange={(e) => store.setContent(e.target.value)}
                  onKeyDown={(e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                      e.preventDefault();
                      handleSave();
                    }
                  }}
                  className="code-editor h-full w-full resize-none bg-surface p-4 text-[13px] leading-[1.85] outline-none"
                  placeholder="开始编写 Markdown…&#10;&#10;支持代码块（```python）与 Mermaid 图表（```mermaid）"
                  spellCheck={false}
                />
              </div>
            )}

            {showPreview && (
              <div
                className={`${showPreview && showEditor ? 'w-1/2' : 'w-full'} overflow-y-auto overflow-x-hidden bg-canvas`}
              >
                {store.currentContent.trim() ? (
                  <MarkdownRenderer
                    content={store.currentContent}
                    className="mx-auto max-w-[880px] px-8 pt-6 pb-24"
                    onJumpToDiagramSource={jumpToDiagramSource}
                  />
                ) : (
                  <div className="grid h-full place-items-center text-[13px] text-subtle">
                    暂无内容，切换到编辑模式开始编写
                  </div>
                )}
              </div>
            )}

            {/* 大纲（v1.5.1 #4-a：由**底部折叠区**改为**右侧可折叠竖栏**，与预览区并列；
                条目样式与跳转逻辑沿用原实现，不新增标题解析规则、不做滚动联动高亮） */}
            {showOutline && (
              <aside className="w-56 shrink-0 overflow-y-auto border-l border-line-subtle bg-surface">
                <div className="px-4 py-2 text-[13px] text-muted">
                  大纲（{headings.length} 个标题）
                </div>
                <div className="space-y-1 px-4 pb-3">
                  {headings.map((h, i) => (
                    <div
                      key={i}
                      onClick={() => handleOutlineClick(h.text)}
                      className="cursor-pointer text-[13px] text-muted transition-colors hover:text-fg-brand"
                      style={{ paddingLeft: `${(h.level - 1) * 16}px` }}
                    >
                      {h.text}
                    </div>
                  ))}
                </div>
              </aside>
            )}
          </div>
        </>
      )}

      {/* 放弃修改确认 */}
      {showDiscardDialog && (
        <ConfirmDialog
          title="未保存的修改"
          message="当前笔记有未保存的修改，确定要放弃吗？"
          confirmLabel="放弃"
          danger
          onConfirm={handleDiscard}
          onCancel={() => setShowDiscardDialog(false)}
        />
      )}
    </div>
  );
}

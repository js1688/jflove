import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router';
import { useNoteStore } from '../stores/note-store';
import { PageHeader } from '../components/PageHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { AUTO_SAVE_INTERVAL_MS } from '../config/constants';

type ViewMode = 'edit' | 'preview' | 'split';

/** Markdown 工具栏按钮 */
interface ToolbarButton {
  label: string;
  icon: string;
  insert: string; // 插入的 Markdown 语法，| 表示光标位置
}

const TOOLBAR_BUTTONS: ToolbarButton[] = [
  { label: '加粗', icon: '𝐁', insert: '**|**' },
  { label: '斜体', icon: '𝘐', insert: '*|*' },
  { label: '删除线', icon: 'S̶', insert: '~~|~~' },
  { label: 'H1', icon: 'H1', insert: '\n# |\n' },
  { label: 'H2', icon: 'H2', insert: '\n## |\n' },
  { label: 'H3', icon: 'H3', insert: '\n### |\n' },
  { label: '无序列表', icon: '•', insert: '\n- |\n' },
  { label: '有序列表', icon: '1.', insert: '\n1. |\n' },
  { label: '链接', icon: '🔗', insert: '[|](url)' },
  { label: '图片', icon: '🖼️', insert: '![|](图片地址)' },
  { label: '代码块', icon: '⟨⟩', insert: '\n```\n|\n```\n' },
  { label: '引用', icon: '❝', insert: '\n> |\n' },
  { label: '分割线', icon: '—', insert: '\n---\n' },
];

/** 笔记编辑页 */
export function NoteEditPage() {
  const { noteId } = useParams<{ noteId: string }>();
  const navigate = useNavigate();
  const store = useNoteStore();

  const [viewMode, setViewMode] = useState<ViewMode>('edit');
  const [showDiscardDialog, setShowDiscardDialog] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<string | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const filename = noteId ? decodeURIComponent(noteId) : null;

  // 加载笔记
  useEffect(() => {
    if (!filename) return;
    useNoteStore.getState().loadNote(filename).catch(() => {
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
    await store.saveNote();
  }, [store]);

  const handleDiscard = () => {
    store.discardChanges();
    setShowDiscardDialog(false);
    if (pendingNavigation) navigate(pendingNavigation);
  };

  /** 工具栏插入 Markdown */
  const insertMarkdown = (insert: string) => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selected = store.currentContent.slice(start, end);
    const before = store.currentContent.slice(0, start);
    const after = store.currentContent.slice(end);

    let replacement = insert.replace('|', selected);
    if (!insert.includes('|')) {
      replacement = insert;
    }

    const newContent = before + replacement + after;
    store.setContent(newContent);

    // 恢复光标位置
    requestAnimationFrame(() => {
      const cursorPos = before.length + replacement.length;
      textarea.focus();
      textarea.setSelectionRange(cursorPos, cursorPos);
    });
  };

  /** 生成大纲 */
  const headings = store.currentContent
    .split('\n')
    .filter(line => /^#{1,3}\s/.test(line))
    .map(line => {
      const level = line.match(/^(#{1,3})/)![1].length;
      const text = line.replace(/^#{1,3}\s/, '');
      return { level, text };
    });

  if (!filename) return null;

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title={filename}
        onBack={handleBack}
        actions={
          <div className="flex items-center gap-2">
            {/* 视图切换 */}
            <div className="flex border border-gray-200 rounded-lg overflow-hidden">
              {(['edit', 'preview', 'split'] as ViewMode[]).map(mode => (
                <button
                  key={mode}
                  onClick={() => setViewMode(mode)}
                  className={`px-2 py-1 text-xs ${
                    viewMode === mode
                      ? 'bg-indigo-600 text-white'
                      : 'text-gray-500 hover:bg-gray-50'
                  }`}
                >
                  {mode === 'edit' ? '编辑' : mode === 'preview' ? '预览' : '分屏'}
                </button>
              ))}
            </div>
            {/* 保存按钮 */}
            <button
              onClick={handleSave}
              className={`px-3 py-1.5 text-xs rounded-lg ${
                store.isModified
                  ? 'bg-amber-500 text-white hover:bg-amber-600'
                  : 'bg-gray-100 text-gray-400'
              }`}
            >
              保存{store.isModified ? ' ●' : ''}
            </button>
          </div>
        }
      />

      {store.isLoading && <LoadingSpinner text="加载笔记…" />}

      {!store.isLoading && (
        <>
          {/* Markdown 工具栏（编辑/分屏模式） */}
          {(viewMode === 'edit' || viewMode === 'split') && (
            <div className="flex flex-wrap gap-1 px-3 py-2 border-b border-gray-100 bg-white overflow-x-auto">
              {TOOLBAR_BUTTONS.map(btn => (
                <button
                  key={btn.label}
                  onClick={() => insertMarkdown(btn.insert)}
                  title={btn.label}
                  className="px-2 py-1 text-xs text-gray-500 hover:bg-gray-100 rounded transition-colors whitespace-nowrap"
                >
                  {btn.icon}
                </button>
              ))}
            </div>
          )}

          {/* 编辑/预览区域 */}
          <div className={`flex-1 flex ${viewMode === 'split' ? 'divide-x divide-gray-200' : ''}`}>
            {/* 编辑区 */}
            {(viewMode === 'edit' || viewMode === 'split') && (
              <div className={viewMode === 'split' ? 'w-1/2' : 'w-full'}>
                <textarea
                  ref={textareaRef}
                  value={store.currentContent}
                  onChange={e => store.setContent(e.target.value)}
                  className="w-full h-full p-4 text-sm font-mono resize-none focus:outline-none bg-white"
                  placeholder="开始编写 Markdown…"
                  spellCheck={false}
                />
              </div>
            )}

            {/* 预览区 */}
            {(viewMode === 'preview' || viewMode === 'split') && (
              <div className={`${viewMode === 'split' ? 'w-1/2' : 'w-full'} p-4 overflow-y-auto`}>
                <div
                  className="markdown-body"
                  dangerouslySetInnerHTML={{ __html: simpleMarkdownRender(store.currentContent) }}
                />
              </div>
            )}
          </div>

          {/* 大纲（预览模式） */}
          {viewMode === 'preview' && headings.length > 0 && (
            <details className="border-t border-gray-200 bg-white">
              <summary className="px-4 py-2 text-sm text-gray-500 cursor-pointer hover:bg-gray-50">
                大纲（{headings.length} 个标题）
              </summary>
              <div className="px-4 pb-2 space-y-1 max-h-48 overflow-y-auto">
                {headings.map((h, i) => (
                  <div
                    key={i}
                    className="text-sm text-gray-600 hover:text-indigo-600 cursor-pointer"
                    style={{ paddingLeft: `${(h.level - 1) * 16}px` }}
                  >
                    {h.text}
                  </div>
                ))}
              </div>
            </details>
          )}
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

/** 简单 Markdown 渲染 */
function simpleMarkdownRender(content: string): string {
  return content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<h3 class="text-lg font-semibold mt-4 mb-2">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="text-xl font-semibold mt-5 mb-3">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="text-2xl font-bold mt-6 mb-4">$1</h1>')
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/~~(.+?)~~/g, '<del>$1</del>')
    .replace(/`([^`]+)`/g, '<code class="bg-gray-100 px-1 rounded text-sm">$1</code>')
    .replace(/^> (.+)$/gm, '<blockquote class="border-l-4 border-gray-300 pl-4 italic text-gray-600">$1</blockquote>')
    .replace(/^- (.+)$/gm, '<li class="ml-4 list-disc">$1</li>')
    .replace(/^\d+\. (.+)$/gm, '<li class="ml-4 list-decimal">$1</li>')
    .replace(/^---$/gm, '<hr class="my-4 border-gray-200" />')
    .replace(/\n{2,}/g, '<br/><br/>')
    .replace(/\n/g, '<br/>');
}

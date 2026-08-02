import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useNoteStore } from '../stores/note-store';
import { PageHeader } from '../components/PageHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { EmptyState } from '../components/EmptyState';

/** 笔记列表页 */
export function NoteListPage() {
  const navigate = useNavigate();
  const store = useNoteStore();

  const [showNewDialog, setShowNewDialog] = useState(false);
  const [newName, setNewName] = useState('');
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  useEffect(() => {
    // 使用 getState 避免把 store 整体引用加入依赖
    useNoteStore.getState().loadNotes().catch(() => {});
  }, []);

  const filtered = store.getFilteredNotes();

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    const filename = name.endsWith('.md') ? name : `${name}.md`;
    try {
      await store.createNote(filename);
      setNewName('');
      setShowNewDialog(false);
    } catch {
      // error handled by store
    }
  };

  const handleRename = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    const newFilename = renameValue.endsWith('.md') ? renameValue : `${renameValue}.md`;
    try {
      await store.renameNote(renameTarget, newFilename);
      setRenameTarget(null);
    } catch { /* handled */ }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await store.deleteNote(deleteTarget);
      setDeleteTarget(null);
    } catch { /* handled */ }
  };

  return (
    <div>
      <PageHeader
        title="笔记管理"
        actions={
          <button
            onClick={() => setShowNewDialog(true)}
            className="px-3 py-1.5 text-xs bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
          >
            ＋ 新建
          </button>
        }
      />

      {/* 搜索框 */}
      <div className="px-4 py-3">
        <input
          type="text"
          placeholder="搜索笔记…"
          value={store.searchQuery}
          onChange={e => store.setSearchQuery(e.target.value)}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
      </div>

      {/* 笔记列表 */}
      <div className="px-2">
        {store.notesLoading && <LoadingSpinner />}

        {!store.notesLoading && filtered.length === 0 && (
          <EmptyState
            icon="📝"
            title={store.searchQuery ? '未找到匹配的笔记' : '暂无笔记'}
            description={store.searchQuery ? undefined : '点击右上角「新建」创建第一篇笔记'}
          />
        )}

        {!store.notesLoading && filtered.map(note => (
          <div
            key={note.filename}
            className="flex items-center gap-3 px-4 py-3 hover:bg-gray-50 rounded-lg cursor-pointer border-b border-gray-50 group"
          >
            <span className="text-xl">📝</span>
            <div
              className="flex-1 min-w-0"
              onClick={() => navigate(`/notes/${encodeURIComponent(note.filename)}`)}
            >
              <div className="text-sm text-gray-800 truncate">{note.filename}</div>
              <div className="text-xs text-gray-400">
                {formatSize(note.size)} · {formatDate(note.modified_at)}
              </div>
            </div>
            <div className="hidden group-hover:flex items-center gap-1">
              <button
                onClick={(e) => { e.stopPropagation(); setRenameTarget(note.filename); setRenameValue(note.filename); }}
                className="px-2 py-1 text-xs text-gray-400 hover:text-indigo-600"
              >
                重命名
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); setDeleteTarget(note.filename); }}
                className="px-2 py-1 text-xs text-gray-400 hover:text-red-500"
              >
                删除
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* 新建对话框 */}
      {showNewDialog && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h3 className="font-semibold text-gray-800 mb-3">新建笔记</h3>
            <input
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
              placeholder="笔记名称（自动补 .md）"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 mb-4"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => { setShowNewDialog(false); setNewName(''); }}
                className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
              >
                取消
              </button>
              <button
                onClick={handleCreate}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
              >
                创建
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 重命名 */}
      {renameTarget && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h3 className="font-semibold text-gray-800 mb-3">重命名</h3>
            <input
              type="text"
              value={renameValue}
              onChange={e => setRenameValue(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleRename()}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 mb-4"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setRenameTarget(null)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">取消</button>
              <button onClick={handleRename} className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">确认</button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认 */}
      {deleteTarget && (
        <ConfirmDialog
          title="确认删除"
          message={`确定要删除「${deleteTarget}」吗？此操作不可撤销。`}
          confirmLabel="删除"
          danger
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatDate(ts: number): string {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

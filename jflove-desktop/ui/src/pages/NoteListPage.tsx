import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useNoteStore } from '../stores/note-store';
import { PageHeader } from '../components/PageHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Button, EmptyState, Icon, IconButton, Input, ListSkeleton, Modal } from '../components/ui';
import { formatSize, formatTime } from '../utils/format';

/**
 * 笔记列表页
 *
 * v1.5.0：emoji 📝/＋ 换矢量图标；搜索框与列表行套设计令牌；
 * 加载态改骨架屏；悬停操作改图标按钮（需求 AC-4/5/6）。
 */
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
        subtitle={`共 ${store.notes.length} 篇`}
        actions={
          <Button variant="primary" size="sm" icon="plus" onClick={() => setShowNewDialog(true)}>
            新建
          </Button>
        }
      />

      {/* 搜索框 */}
      <div className="px-4 py-3">
        <Input
          icon="search"
          type="text"
          placeholder="搜索笔记…"
          value={store.searchQuery}
          onChange={e => store.setSearchQuery(e.target.value)}
        />
      </div>

      {/* 笔记列表 */}
      <div className="px-2">
        {store.notesLoading && <ListSkeleton rows={5} />}

        {!store.notesLoading && filtered.length === 0 && (
          <EmptyState
            icon="notes"
            title={store.searchQuery ? '未找到匹配的笔记' : '暂无笔记'}
            description={store.searchQuery ? undefined : '点击右上角「新建」创建第一篇笔记'}
          />
        )}

        {!store.notesLoading &&
          filtered.map((note) => (
            <div
              key={note.filename}
              onClick={() => navigate(`/notes/${encodeURIComponent(note.filename)}`)}
              className="group list-row cursor-pointer rounded-lg border-b border-line-subtle"
            >
              <span className="file-ico file-ico-doc">
                <Icon name="notes" size="lg" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium text-fg">{note.filename}</div>
                <div className="tabular text-[11.5px] text-subtle">
                  {formatSize(note.size)} · {formatTime(note.modified_at)}
                </div>
              </div>
              <div className="hidden items-center gap-0.5 group-hover:flex">
                <IconButton
                  icon="edit"
                  label="重命名"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setRenameTarget(note.filename);
                    setRenameValue(note.filename);
                  }}
                />
                <IconButton
                  icon="delete"
                  label="删除"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setDeleteTarget(note.filename);
                  }}
                />
              </div>
            </div>
          ))}
      </div>

      {/* 新建笔记 */}
      <Modal
        open={showNewDialog}
        title="新建笔记"
        icon="plus"
        width={420}
        onClose={() => {
          setShowNewDialog(false);
          setNewName('');
        }}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setShowNewDialog(false);
                setNewName('');
              }}
            >
              取消
            </Button>
            <Button variant="primary" icon="checked" onClick={handleCreate}>
              创建
            </Button>
          </>
        }
      >
        <input
          type="text"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
          placeholder="笔记名称（自动补 .md）"
          className="input"
          autoFocus
        />
      </Modal>

      {/* 重命名 */}
      <Modal
        open={Boolean(renameTarget)}
        title="重命名"
        icon="edit"
        width={420}
        onClose={() => setRenameTarget(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRenameTarget(null)}>
              取消
            </Button>
            <Button variant="primary" icon="checked" onClick={handleRename}>
              确认
            </Button>
          </>
        }
      >
        <input
          type="text"
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleRename()}
          className="input"
          autoFocus
        />
      </Modal>

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

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { diskService } from '../../services/disk-service';
import { PageHeader } from '../../components/PageHeader';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { LoadingSpinner } from '../../components/LoadingSpinner';
import { useIsPC } from '../../hooks/use-responsive';
import type { VirtualDisk } from '../../types/models';

/** 管理员 - 磁盘管理 */
export function AdminDisksPage() {
  const navigate = useNavigate();
  const isPC = useIsPC();
  const [disks, setDisks] = useState<VirtualDisk[]>([]);
  const [loading, setLoading] = useState(true);

  // 移动端三点菜单展开的磁盘 ID
  const [menuDiskId, setMenuDiskId] = useState<number | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createPath, setCreatePath] = useState('');

  const [editDisk, setEditDisk] = useState<VirtualDisk | null>(null);
  const [editName, setEditName] = useState('');
  const [editPath, setEditPath] = useState('');

  const [deleteDisk, setDeleteDisk] = useState<VirtualDisk | null>(null);

  const loadDisks = async () => {
    setLoading(true);
    try { setDisks(await diskService.listAllDisks()); } catch { /* 加载失败静默，界面显示空列表 */ }
    setLoading(false);
  };

  useEffect(() => { loadDisks(); }, []);

  const handleCreate = async () => {
    if (!createName.trim() || !createPath.trim()) return;
    await diskService.createDisk(createName.trim(), createPath.trim());
    setShowCreate(false); setCreateName(''); setCreatePath('');
    await loadDisks();
  };

  const handleEdit = async () => {
    if (!editDisk || !editName.trim() || !editPath.trim()) return;
    await diskService.updateDisk(editDisk.id, editName.trim(), editPath.trim());
    setEditDisk(null);
    await loadDisks();
  };

  const handleDelete = async () => {
    if (!deleteDisk) return;
    await diskService.deleteDisk(deleteDisk.id);
    setDeleteDisk(null);
    await loadDisks();
  };

  return (
    <div>
      <PageHeader
        title="磁盘管理"
        onBack={() => navigate(-1)}
        actions={
          <button
            onClick={() => setShowCreate(true)}
            className="px-3 py-1.5 text-xs bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
          >
            ＋ 添加磁盘
          </button>
        }
      />

      {loading && <LoadingSpinner />}

      {!loading && (
        isPC ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-left text-xs text-gray-400 uppercase">
                  <th className="px-4 py-3 font-medium">ID</th>
                  <th className="px-4 py-3 font-medium">名称</th>
                  <th className="px-4 py-3 font-medium">路径</th>
                  <th className="px-4 py-3 font-medium">创建时间</th>
                  <th className="px-4 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {disks.map(disk => (
                  <tr key={disk.id} className="border-b border-gray-50 hover:bg-gray-50">
                    <td className="px-4 py-3 text-gray-500">{disk.id}</td>
                    <td className="px-4 py-3 font-medium text-gray-800">{disk.name}</td>
                    <td className="px-4 py-3 text-gray-500 text-xs font-mono max-w-[200px] truncate">{disk.real_path || disk.path}</td>
                    <td className="px-4 py-3 text-gray-400 text-xs">
                      {disk.created_at ? new Date(disk.created_at).toLocaleString('zh-CN') : '-'}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1">
                        <button
                          onClick={() => { setEditDisk(disk); setEditName(disk.name); setEditPath(disk.real_path || disk.path); }}
                          className="px-2 py-1 text-xs text-indigo-600 hover:bg-indigo-50 rounded"
                        >
                          编辑
                        </button>
                        <button
                          onClick={() => setDeleteDisk(disk)}
                          className="px-2 py-1 text-xs text-red-500 hover:bg-red-50 rounded"
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {disks.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-400">暂无磁盘</td></tr>
                )}
              </tbody>
            </table>
          </div>
        ) : (
          /* 移动端对齐安卓 App：卡片 + ListTile（磁盘图标 + id.名称 + 路径 + 三点菜单） */
          <div className="p-3 space-y-2">
            {disks.map(disk => (
              <div key={disk.id} className="bg-white rounded-xl border border-gray-100 p-3 flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-indigo-100 text-indigo-600 flex items-center justify-center text-lg shrink-0">
                  💾
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-gray-800 text-sm truncate">{disk.id}. {disk.name}</div>
                  <div className="text-xs text-gray-400 mt-0.5 truncate font-mono">{disk.real_path || disk.path}</div>
                </div>
                {/* 三点操作菜单 */}
                <div className="relative shrink-0">
                  <button
                    onClick={() => setMenuDiskId(menuDiskId === disk.id ? null : disk.id)}
                    className="w-8 h-8 flex items-center justify-center text-gray-400 hover:text-gray-600 rounded-full hover:bg-gray-50"
                    aria-label="操作菜单"
                  >
                    ⋮
                  </button>
                  {menuDiskId === disk.id && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setMenuDiskId(null)} />
                      <div className="absolute right-0 top-9 z-20 w-36 bg-white rounded-lg shadow-lg border border-gray-100 py-1">
                        <button
                          onClick={() => { setMenuDiskId(null); setEditDisk(disk); setEditName(disk.name); setEditPath(disk.real_path || disk.path); }}
                          className="w-full px-3 py-2 text-left text-sm text-gray-600 hover:bg-gray-50"
                        >
                          编辑
                        </button>
                        <button
                          onClick={() => { setMenuDiskId(null); setDeleteDisk(disk); }}
                          className="w-full px-3 py-2 text-left text-sm text-red-500 hover:bg-red-50"
                        >
                          删除
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            ))}
            {disks.length === 0 && (
              <div className="py-8 text-center text-gray-400 text-sm">暂无磁盘</div>
            )}
          </div>
        )
      )}

      {/* 创建磁盘 */}
      {showCreate && <DiskFormDialog title="创建磁盘" name={createName} onNameChange={setCreateName} path={createPath} onPathChange={setCreatePath} onConfirm={handleCreate} onCancel={() => setShowCreate(false)} />}

      {/* 编辑磁盘 */}
      {editDisk && <DiskFormDialog title="编辑磁盘" name={editName} onNameChange={setEditName} path={editPath} onPathChange={setEditPath} onConfirm={handleEdit} onCancel={() => setEditDisk(null)} />}

      {/* 删除 */}
      {deleteDisk && (
        <ConfirmDialog title="确认删除" message={`确定要删除磁盘「${deleteDisk.name}」吗？磁盘内的所有文件将被删除。`} confirmLabel="删除" danger onConfirm={handleDelete} onCancel={() => setDeleteDisk(null)} />
      )}
    </div>
  );
}

function DiskFormDialog({
  title, name, onNameChange, path, onPathChange, onConfirm, onCancel,
}: {
  title: string; name: string; onNameChange: (v: string) => void; path: string; onPathChange: (v: string) => void;
  onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4">
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
        <h3 className="font-semibold text-gray-800 mb-4">{title}</h3>
        <input type="text" placeholder="磁盘名称" value={name}
          onChange={e => onNameChange(e.target.value)}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-indigo-500" />
        <input type="text" placeholder="磁盘路径" value={path}
          onChange={e => onPathChange(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && onConfirm()}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-indigo-500" />
        <div className="flex justify-end gap-2">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">取消</button>
          <button onClick={onConfirm} className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">确认</button>
        </div>
      </div>
    </div>
  );
}

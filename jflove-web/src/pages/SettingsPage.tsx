import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { useSettingsStore } from '../stores/settings-store';
import { useAuth } from '../hooks/use-auth';
import { serverHistoryService } from '../services/server-history-service';
import { diskService } from '../services/disk-service';
import { noteService } from '../services/note-service';
import { DirTreeModal } from '../components/DirTreeModal';
import { PageHeader } from '../components/PageHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { SecurityPage } from './SecurityPage';
import { useIsPC } from '../hooks/use-responsive';
import { APP_VERSION } from '../config/constants';
import { getTokenExpiresAt, effectiveExpireAt } from '../utils/session';
import type { VirtualDisk } from '../types/models';

/** 设置页 */
export function SettingsPage() {
  const navigate = useNavigate();
  const isPC = useIsPC();
  const { username, role, isAdmin, serverUrl } = useAuthStore();
  const { handleLogout } = useAuth();
  const settings = useSettingsStore();

  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [showDirBrowser, setShowDirBrowser] = useState(false);
  const [disks, setDisks] = useState<VirtualDisk[]>([]);
  const [selectedDiskId, setSelectedDiskId] = useState<number | null>(null);

  // 加载磁盘列表（用于友好展示笔记目录当前配置）
  useEffect(() => {
    diskService.listAllDisks().then(setDisks).catch(() => {});
    // 从后端加载笔记目录配置（users.notes_disk_id / notes_path，跨设备持久化）
    noteService.getNotesDiskConfig()
      .then(cfg => {
        if (cfg.disk_id != null) {
          settings.setNotesDiskId(cfg.disk_id);
          settings.setNotesPath(cfg.path || '');
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [serverUrlEdit, setServerUrlEdit] = useState(serverUrl);
  const [history] = useState(serverHistoryService.listHistory());
  // 服务端地址保存/重连状态
  const [savingServer, setSavingServer] = useState(false);
  const [serverMsg, setServerMsg] = useState<string | null>(null);
  // 移动端「修改服务器地址」底部弹窗
  const [showServerSheet, setShowServerSheet] = useState(false);

  // 保存服务端地址并重新密钥交换（需求 §2.7；对标桌面端：换地址后需重新登录）
  const handleSaveServerUrl = async () => {
    const url = serverUrlEdit.trim();
    if (!url || savingServer) return;
    setSavingServer(true);
    setServerMsg(null);
    try {
      await useAuthStore.getState().keyExchange(url);
      // 服务端地址已变化：旧 token/用户信息对新服务端无效，清除会话并提示重新登录
      useAuthStore.getState().logout();
      setServerMsg('服务端地址已更新，请重新登录');
      setShowServerSheet(false);
      navigate('/login', { replace: true });
    } catch (e) {
      setServerMsg(e instanceof Error ? e.message : '连接失败，请检查地址');
    } finally {
      setSavingServer(false);
    }
  };

  // 登录有效期信息（当前 token 过期时间 + 剩余时长）
  const tokenExpiresAt = getTokenExpiresAt();
  const effectiveExpire = effectiveExpireAt(tokenExpiresAt);
  const remainingSeconds = effectiveExpire
    ? Math.max(0, Math.floor(effectiveExpire - Date.now() / 1000))
    : 0;
  const remainingText = remainingSeconds > 0
    ? formatDuration(remainingSeconds)
    : '已过期';

  // 安全状态卡片（移动端在设置页内显示）
  const securitySection = !isPC ? (
    <div className="mb-4">
      <SecurityPage embedded />
    </div>
  ) : null;

  return (
    <div>
      <PageHeader title="设置" />

      <div className="p-4 space-y-4">
        {/* 安全状态（移动端） */}
        {securitySection}

        {/* 服务端地址 */}
        {/* 移动端对齐安卓 App：显示当前地址 + 「修改服务器地址」按钮，点击弹底部面板输入，避免行内输入溢出 */}
        <Section icon={isPC ? undefined : '🔌'} title="服务端">
          {isPC ? (
            <>
              <div className="flex gap-2">
                <input
                  type="text"
                  list="settings-server-history"
                  value={serverUrlEdit}
                  onChange={e => setServerUrlEdit(e.target.value)}
                  className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
                <button
                  onClick={handleSaveServerUrl}
                  disabled={savingServer || !serverUrlEdit.trim()}
                  className="px-3 py-2 text-xs bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 whitespace-nowrap"
                >
                  {savingServer ? '连接中…' : '保存并重新连接'}
                </button>
                <datalist id="settings-server-history">
                  {history.map(url => <option key={url} value={url} />)}
                </datalist>
              </div>
              {serverMsg && (
                <div className="mt-2 text-xs text-indigo-600">{serverMsg}</div>
              )}
            </>
          ) : (
            <>
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-gray-600 truncate flex-1">{serverUrl}</span>
                <button
                  onClick={() => setShowServerSheet(true)}
                  className="px-3 py-1.5 text-xs bg-indigo-50 text-indigo-600 rounded-lg hover:bg-indigo-100 whitespace-nowrap"
                >
                  修改服务器地址
                </button>
              </div>
              {serverMsg && (
                <div className="mt-2 text-xs text-indigo-600">{serverMsg}</div>
              )}
            </>
          )}
        </Section>

        {/* 笔记目录 */}
        <Section icon={isPC ? undefined : '📁'} title="笔记目录">
          <div className="text-xs text-gray-400 mb-2">
            {settings.notesDiskId
              ? (() => {
                  const d = disks.find(x => x.id === settings.notesDiskId);
                  return `当前：${d?.name || `磁盘 ${settings.notesDiskId}`} / ${settings.notesPath || '根目录'}`;
                })()
              : '未配置'}
          </div>
          <button
            onClick={async () => {
              try {
                const d = await diskService.listAllDisks();
                setDisks(d);
                setShowDirBrowser(true);
              } catch { /* ignore */ }
            }}
            className="px-3 py-1.5 text-xs border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            浏览选择
          </button>
        </Section>

        {/* 账号（含 token 过期时间，对标桌面端登录凭证信息） */}
        <Section icon={isPC ? undefined : '👤'} title="账号">
          <div className="text-sm text-gray-700 mb-1">
            {username} <span className="text-xs text-gray-400">({role === 'admin' ? '管理员' : '普通用户'})</span>
          </div>
          {effectiveExpire && (
            <div className="text-xs text-gray-400 mb-2">
              登录凭证过期时间：{new Date(effectiveExpire * 1000).toLocaleString('zh-CN')}（剩余 {remainingText}）
            </div>
          )}
          <button
            onClick={() => setShowLogoutConfirm(true)}
            className="px-4 py-2 text-sm text-red-500 border border-red-200 rounded-lg hover:bg-red-50"
          >
            退出登录
          </button>
        </Section>

        {/* 管理面板入口（仅移动端显示；PC 端侧边栏已有入口，避免重复） */}
        {/* 对齐移动端 Flutter：卡片式列表项（leading 图标 + 标题 + 右箭头），点击进入子页 */}
        {isAdmin && !isPC && (
          <Section icon="🛡️" title="管理面板">
            <div className="space-y-2">
              {[
                { path: '/admin/users', label: '用户管理', icon: '👤' },
                { path: '/admin/disks', label: '磁盘管理', icon: '💾' },
                { path: '/admin/permissions', label: '权限配置', icon: '🔑' },
              ].map(item => (
                <button
                  key={item.path}
                  onClick={() => navigate(item.path)}
                  className="w-full flex items-center gap-3 px-4 py-3 text-sm text-gray-600 bg-white rounded-lg border border-gray-100 hover:bg-gray-50"
                >
                  <span className="text-lg">{item.icon}</span>
                  <span className="flex-1 text-left">{item.label}</span>
                  <span className="text-gray-300">→</span>
                </button>
              ))}
            </div>
          </Section>
        )}

        {/* 关于 */}
        <Section icon={isPC ? undefined : 'ℹ️'} title="关于">
          <div className="text-sm text-gray-600">
            <div>JFLove v{APP_VERSION}</div>
            <div className="text-xs text-gray-400 mt-1">
              加密方案：X25519 ECDH + ChaCha20-Poly1305
            </div>
          </div>
        </Section>
      </div>

      {/* 退出登录确认 */}
      {showLogoutConfirm && (
        <ConfirmDialog
          title="退出登录"
          message="确定要退出登录吗？"
          confirmLabel="退出"
          danger
          onConfirm={handleLogout}
          onCancel={() => setShowLogoutConfirm(false)}
        />
      )}

      {/* 笔记目录浏览 */}
      {showDirBrowser && selectedDiskId === null && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h3 className="font-semibold text-gray-800 mb-3">选择磁盘</h3>
            <div className="space-y-2 mb-4 max-h-60 overflow-y-auto">
              {disks.map(disk => (
                <button
                  key={disk.id}
                  onClick={() => setSelectedDiskId(disk.id)}
                  className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50 rounded-lg border border-gray-100"
                >
                  💾 {disk.name}
                </button>
              ))}
              {disks.length === 0 && (
                <div className="text-center text-sm text-gray-400 py-4">暂无可用磁盘</div>
              )}
            </div>
            <button
              onClick={() => setShowDirBrowser(false)}
              className="w-full py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {showDirBrowser && selectedDiskId !== null && (
        <DirTreeModal
          diskId={selectedDiskId}
          diskName={disks.find(d => d.id === selectedDiskId)?.name || ''}
          onSelect={(path) => {
            settings.setNotesDiskId(selectedDiskId);
            settings.setNotesPath(path);
            // 同步到后端（users.notes_disk_id / notes_path），对标桌面端 set_notes_disk
            noteService.setNotesDiskConfig(selectedDiskId, path).catch(() => {});
            setSelectedDiskId(null);
            setShowDirBrowser(false);
          }}
          onClose={() => { setSelectedDiskId(null); setShowDirBrowser(false); }}
        />
      )}

      {/* 移动端：修改服务器地址底部面板（对齐安卓 App BottomSheet，避免行内输入溢出） */}
      {showServerSheet && (
        <div
          className="fixed inset-0 z-50 flex items-end bg-black/30"
          onClick={() => setShowServerSheet(false)}
        >
          <div
            className="w-full bg-white rounded-t-2xl p-4 pb-6 max-h-[80vh] overflow-y-auto"
            onClick={e => e.stopPropagation()}
          >
            <div className="text-sm font-semibold text-gray-800 mb-1">修改服务器地址</div>
            <div className="text-xs text-gray-400 mb-3">保存后需重新密钥交换并登录</div>
            <input
              type="text"
              list="settings-server-history-sheet"
              value={serverUrlEdit}
              onChange={e => setServerUrlEdit(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="http://localhost:8989"
            />
            <datalist id="settings-server-history-sheet">
              {history.map(url => <option key={url} value={url} />)}
            </datalist>
            {history.length > 0 && (
              <div className="mb-3 space-y-1">
                <div className="text-xs text-gray-400">历史地址</div>
                {history.map(url => (
                  <button
                    key={url}
                    onClick={() => setServerUrlEdit(url)}
                    className="block w-full text-left text-xs text-indigo-600 hover:text-indigo-800 truncate px-2 py-1.5 rounded hover:bg-indigo-50"
                  >
                    {url}
                  </button>
                ))}
              </div>
            )}
            <button
              onClick={handleSaveServerUrl}
              disabled={savingServer || !serverUrlEdit.trim()}
              className="w-full py-2.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {savingServer ? '连接中…' : '保存并重新连接'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** 将秒数格式化为可读时长（如 29 天 23 小时 59 分） */
function formatDuration(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d} 天 ${h} 小时`;
  if (h > 0) return `${h} 小时 ${m} 分`;
  return `${m} 分`;
}

/** 分组卡片（移动端对齐安卓 App：小图标 + 加粗标题 + 灰边框圆角卡片） */
function Section({ icon, title, children }: { icon?: string; title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-100">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold text-gray-700 mb-3">
        {icon && <span className="text-base leading-none text-indigo-500">{icon}</span>}
        {title}
      </h3>
      {children}
    </div>
  );
}

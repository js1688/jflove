import { useState } from 'react';
import { useNavigate } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { useSettingsStore } from '../stores/settings-store';
import { useAuth } from '../hooks/use-auth';
import { serverHistoryService } from '../services/server-history-service';
import { diskService } from '../services/disk-service';
import { DirTreeModal } from '../components/DirTreeModal';
import { PageHeader } from '../components/PageHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { SecurityPage } from './SecurityPage';
import { useIsPC } from '../hooks/use-responsive';
import { SESSION_TTL_OPTIONS, APP_VERSION } from '../config/constants';
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
  const [serverUrlEdit, setServerUrlEdit] = useState(serverUrl);
  const [history] = useState(serverHistoryService.listHistory());

  // 安全状态卡片（移动端在设置页内显示）
  const securitySection = !isPC ? (
    <div className="mb-4">
      <SecurityPage />
    </div>
  ) : null;

  return (
    <div>
      <PageHeader title="设置" />

      <div className="p-4 space-y-4">
        {/* 安全状态（移动端） */}
        {securitySection}

        {/* 服务端地址 */}
        <Section title="服务端">
          <div className="flex gap-2">
            <input
              type="text"
              list="settings-server-history"
              value={serverUrlEdit}
              onChange={e => setServerUrlEdit(e.target.value)}
              className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <datalist id="settings-server-history">
              {history.map(url => <option key={url} value={url} />)}
            </datalist>
          </div>
          {history.length > 0 && (
            <div className="mt-2 space-y-1 max-h-32 overflow-y-auto">
              {history.map(url => (
                <button
                  key={url}
                  onClick={() => setServerUrlEdit(url)}
                  className="block w-full text-left text-xs text-indigo-600 hover:text-indigo-800 truncate"
                >
                  {url}
                </button>
              ))}
            </div>
          )}
        </Section>

        {/* 笔记目录 */}
        <Section title="笔记目录">
          <div className="text-xs text-gray-400 mb-2">
            {settings.notesDiskId
              ? `当前：磁盘 ID ${settings.notesDiskId} / ${settings.notesPath || '根目录'}`
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

        {/* 登录有效期 */}
        <Section title="登录有效期">
          <select
            value={settings.localSessionMaxSeconds}
            onChange={e => settings.setLocalSessionMaxSeconds(Number(e.target.value))}
            className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            {SESSION_TTL_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </Section>

        {/* 账号 */}
        <Section title="账号">
          <div className="text-sm text-gray-700 mb-2">
            {username} <span className="text-xs text-gray-400">({role === 'admin' ? '管理员' : '普通用户'})</span>
          </div>
          <button
            onClick={() => setShowLogoutConfirm(true)}
            className="px-4 py-2 text-sm text-red-500 border border-red-200 rounded-lg hover:bg-red-50"
          >
            退出登录
          </button>
        </Section>

        {/* 管理面板入口（仅 admin） */}
        {isAdmin && (
          <Section title="管理面板">
            <div className="space-y-2">
              <button
                onClick={() => navigate('/admin/users')}
                className="w-full px-4 py-2 text-sm text-left text-gray-600 hover:bg-gray-50 rounded-lg border border-gray-100"
              >
                👤 用户管理
              </button>
              <button
                onClick={() => navigate('/admin/disks')}
                className="w-full px-4 py-2 text-sm text-left text-gray-600 hover:bg-gray-50 rounded-lg border border-gray-100"
              >
                💾 磁盘管理
              </button>
              <button
                onClick={() => navigate('/admin/permissions')}
                className="w-full px-4 py-2 text-sm text-left text-gray-600 hover:bg-gray-50 rounded-lg border border-gray-100"
              >
                🔑 权限配置
              </button>
            </div>
          </Section>
        )}

        {/* 关于 */}
        <Section title="关于">
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
            setSelectedDiskId(null);
            setShowDirBrowser(false);
          }}
          onClose={() => { setSelectedDiskId(null); setShowDirBrowser(false); }}
        />
      )}
    </div>
  );
}

/** 分组卡片 */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-100">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
        {title}
      </h3>
      {children}
    </div>
  );
}

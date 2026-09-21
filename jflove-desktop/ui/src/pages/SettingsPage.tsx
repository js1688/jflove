import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { useSettingsStore } from '../stores/settings-store';
import { useThemeStore, type ThemeMode } from '../stores/theme-store';
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
import { Button, Card, Icon, IconButton, Segmented, toast, type IconName } from '../components/ui';
import { ServerUrlInput } from '../components/desktop/ServerUrlInput';
import type { VirtualDisk } from '../types/models';

/**
 * 设置页
 *
 * v1.5.0：Section 的 `icon` 由 emoji 字符串改为语义 `IconName`（矢量图标）；
 * 手写弹窗换统一 Modal / Card，配色全部改语义令牌（暗色零改动）。
 * 功能项（版本号、服务端地址、笔记目录、账号与登录凭证、管理面板入口、关于）
 * 与 v1.4.2 逐项一致；移动端 / PC 分支（isPC）保留。
 */
export function SettingsPage() {
  const navigate = useNavigate();
  const isPC = useIsPC();
  const { username, role, isAdmin, serverUrl } = useAuthStore();
  const { handleLogout } = useAuth();
  const settings = useSettingsStore();
  const themeMode = useThemeStore(s => s.mode);
  const setThemeMode = useThemeStore(s => s.setMode);

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
      toast.success('服务端地址已更新', '请使用新服务端重新登录');
      setShowServerSheet(false);
      navigate('/login', { replace: true });
    } catch (e) {
      const msg = e instanceof Error ? e.message : '连接失败，请检查地址';
      setServerMsg(msg);
      toast.error('连接失败', msg);
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

  /** 管理面板入口（仅移动端显示；PC 端侧边栏已有入口，避免重复） */
  const adminEntries: { path: string; label: string; icon: IconName }[] = [
    { path: '/admin/users', label: '用户管理', icon: 'users' },
    { path: '/admin/disks', label: '磁盘管理', icon: 'disks' },
    { path: '/admin/permissions', label: '权限配置', icon: 'permissions' },
    { path: '/admin/system', label: '系统设置', icon: 'system' },
  ];

  return (
    <div>
      <PageHeader title="设置" />

      <div className="flex flex-col gap-4 p-4">
        {/* 安全状态（移动端） */}
        {securitySection}

        {/* 服务端地址 */}
        {/* 移动端对齐安卓 App：显示当前地址 + 「修改服务器地址」按钮，点击弹底部面板输入，避免行内输入溢出 */}
        <Section icon={isPC ? undefined : 'server'} title="服务端">
          {isPC ? (
            <>
              <div className="flex gap-2">
                {/*
                  桌面端改动：原生 `<datalist>` 的候选项弹层在 shadow DOM 里、
                  CSS 无法美化（用户反馈"下拉框有点原始"），改用自绘下拉面板。
                */}
                <ServerUrlInput
                  value={serverUrlEdit}
                  onChange={setServerUrlEdit}
                  suggestions={history}
                  className="flex-1"
                  onEnter={() => void handleSaveServerUrl()}
                />
                <Button
                  variant="primary"
                  loading={savingServer}
                  disabled={!serverUrlEdit.trim()}
                  onClick={() => void handleSaveServerUrl()}
                >
                  {savingServer ? '连接中…' : '保存并重新连接'}
                </Button>
              </div>
              {serverMsg && (
                <div className="mt-2 text-[11.5px] text-fg-brand">{serverMsg}</div>
              )}
            </>
          ) : (
            <>
              <div className="flex items-center justify-between gap-3">
                <span className="min-w-0 flex-1 truncate text-[13px] text-muted">{serverUrl}</span>
                <Button size="sm" icon="edit" onClick={() => setShowServerSheet(true)}>
                  修改服务器地址
                </Button>
              </div>
              {serverMsg && (
                <div className="mt-2 text-[11.5px] text-fg-brand">{serverMsg}</div>
              )}
            </>
          )}
        </Section>

        {/* 笔记目录 */}
        <Section icon={isPC ? undefined : 'folder'} title="笔记目录">
          <div className="mb-2 text-[11.5px] text-subtle">
            {settings.notesDiskId
              ? (() => {
                  const d = disks.find(x => x.id === settings.notesDiskId);
                  return `当前：${d?.name || `磁盘 ${settings.notesDiskId}`} / ${settings.notesPath || '根目录'}`;
                })()
              : '未配置'}
          </div>
          <Button
            size="sm"
            icon="folder"
            onClick={async () => {
              try {
                const d = await diskService.listAllDisks();
                setDisks(d);
                setShowDirBrowser(true);
              } catch { /* ignore */ }
            }}
          >
            浏览选择
          </Button>
        </Section>

        {/* 账号（含 token 过期时间，对标桌面端登录凭证信息） */}
        <Section icon={isPC ? undefined : 'user'} title="账号">
          <div className="mb-1 text-[13px] text-fg">
            {username}{' '}
            <span className="text-[11.5px] text-subtle">
              ({role === 'admin' ? '管理员' : '普通用户'})
            </span>
          </div>
          {effectiveExpire && (
            <div className="tabular mb-2 text-[11.5px] text-subtle">
              登录凭证过期时间：{new Date(effectiveExpire * 1000).toLocaleString('zh-CN')}（剩余 {remainingText}）
            </div>
          )}
          <Button
            variant="ghost"
            size="sm"
            icon="logout"
            className="btn-danger-ghost"
            onClick={() => setShowLogoutConfirm(true)}
          >
            退出登录
          </Button>
        </Section>

        {/* 管理面板入口（仅移动端显示；PC 端侧边栏已有入口，避免重复） */}
        {/* 对齐移动端 Flutter：卡片式列表项（leading 图标 + 标题 + 右箭头），点击进入子页 */}
        {isAdmin && !isPC && (
          <Section icon="security" title="管理面板">
            <div className="flex flex-col gap-2">
              {adminEntries.map(item => (
                <button
                  key={item.path}
                  type="button"
                  onClick={() => navigate(item.path)}
                  className="flex w-full items-center gap-3 rounded-lg border border-line-subtle bg-surface px-4 py-3 text-[13px] text-muted transition-colors hover:bg-hover"
                >
                  <Icon name={item.icon} size="sm" className="shrink-0 text-brand-500" />
                  <span className="flex-1 text-left">{item.label}</span>
                  <Icon name="next" size="sm" className="shrink-0 text-subtle" />
                </button>
              ))}
            </div>
          </Section>
        )}

        {/* 外观（v1.5.0 修复：主题切换从侧栏悬浮按钮移到这里，三端统一进设置页） */}
        <Section icon={isPC ? undefined : 'theme'} title="外观">
          <div className="text-[13px] text-muted">
            <div className="mb-2 text-[11.5px] text-subtle">
              跟随系统会随操作系统的亮/暗设置自动切换
            </div>
            <Segmented<ThemeMode>
              value={themeMode}
              onChange={(v) => setThemeMode(v)}
              options={[
                { value: 'system', label: '跟随系统', icon: 'themeSystem' },
                { value: 'light', label: '亮色', icon: 'themeLight' },
                { value: 'dark', label: '暗色', icon: 'theme' },
              ]}
            />
          </div>
        </Section>

        {/* 关于 */}
        <Section icon={isPC ? undefined : 'info'} title="关于">
          <div className="text-[13px] text-muted">
            <div>JFLove v{APP_VERSION}</div>
            <div className="mt-1 text-[11.5px] text-subtle">
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
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-overlay/40 p-4">
          <div className="card w-full max-w-sm p-6">
            <div className="mb-3 flex items-center gap-3">
              <Icon name="disks" className="text-brand-500" />
              <span className="text-[15px] font-semibold">选择磁盘</span>
              <div className="grow" />
              <IconButton icon="close" label="关闭" size="sm" onClick={() => setShowDirBrowser(false)} />
            </div>
            <div className="mb-4 max-h-60 space-y-2 overflow-y-auto">
              {disks.map(disk => (
                <button
                  key={disk.id}
                  type="button"
                  onClick={() => setSelectedDiskId(disk.id)}
                  className="flex w-full items-center gap-2.5 rounded-lg border border-line-subtle px-4 py-2 text-left text-[13px] transition-colors hover:bg-hover"
                >
                  <Icon name="disks" size="sm" className="shrink-0 text-brand-500" />
                  <span className="truncate">{disk.name}</span>
                </button>
              ))}
              {disks.length === 0 && (
                <div className="py-4 text-center text-[13px] text-subtle">暂无可用磁盘</div>
              )}
            </div>
            <Button variant="ghost" className="w-full" onClick={() => setShowDirBrowser(false)}>
              取消
            </Button>
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
          className="fixed inset-0 z-50 flex items-end bg-overlay/40"
          onClick={() => setShowServerSheet(false)}
        >
          <div
            className="card max-h-[80vh] w-full overflow-y-auto rounded-t-2xl rounded-b-none p-4 pb-6"
            onClick={e => e.stopPropagation()}
          >
            <div className="mb-1 text-[13px] font-semibold text-fg">修改服务器地址</div>
            <div className="mb-3 text-[11.5px] text-subtle">保存后需重新密钥交换并登录</div>
            {/* 同上：不使用原生 datalist（该分支在桌面端不可达，保持一致以免日后误用） */}
            <ServerUrlInput
              value={serverUrlEdit}
              onChange={setServerUrlEdit}
              suggestions={history}
              className="mb-3"
              placeholder="http://localhost:8989"
            />
            {history.length > 0 && (
              <div className="mb-3 space-y-1">
                <div className="text-[11.5px] text-subtle">历史地址</div>
                {history.map(url => (
                  <button
                    key={url}
                    type="button"
                    onClick={() => setServerUrlEdit(url)}
                    className="block w-full truncate rounded-sm px-2 py-1.5 text-left text-[11.5px] text-fg-brand transition-colors hover:bg-active"
                  >
                    {url}
                  </button>
                ))}
              </div>
            )}
            <Button
              variant="primary"
              className="w-full"
              loading={savingServer}
              disabled={!serverUrlEdit.trim()}
              onClick={() => void handleSaveServerUrl()}
            >
              {savingServer ? '连接中…' : '保存并重新连接'}
            </Button>
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
function Section({
  icon,
  title,
  children,
}: {
  /** 语义图标名（v1.5.0 起不再接受 emoji 字面量） */
  icon?: IconName;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <h3 className="mb-3 flex items-center gap-1.5 text-[13px] font-semibold text-fg">
        {icon && <Icon name={icon} size="sm" className="text-brand-500" />}
        {title}
      </h3>
      {children}
    </Card>
  );
}

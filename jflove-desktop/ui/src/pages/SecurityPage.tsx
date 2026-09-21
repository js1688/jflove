import { useEffect, useState } from 'react';
import { useAuthStore } from '../stores/auth-store';
import { authService } from '../services/auth-service';
import { getSessionId, getKeyExchangeTime, isEncrypted } from '../utils/session';
import { PageHeader } from '../components/PageHeader';
import { Badge, Button, Card, Icon, toast, type IconName } from '../components/ui';
import { formatElapsed } from '../utils/format';

/**
 * 安全状态页
 *
 * v1.5.0：✅/❌ emoji 换矢量图标徽标；卡片套令牌；刷新提示由手写 DOM
 * 改为全站统一 Toast（需求 AC-4/AC-5/AC-6）。
 */
export function SecurityPage({ embedded = false }: { embedded?: boolean }) {
  const { username, role } = useAuthStore();
  const [refreshing, setRefreshing] = useState(false);
  // 定时刷新：免登录恢复后会话为异步建立，5 秒轮询一次以反映最新状态
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick(n => n + 1), 5000);
    return () => clearInterval(timer);
  }, []);

  const sessionId = getSessionId();
  const keyExchangeTime = getKeyExchangeTime();
  const encrypted = isEncrypted();

  /** 会话已持续的时长（秒） */
  const durationSeconds = keyExchangeTime ? Date.now() / 1000 - keyExchangeTime : 0;
  const duration = keyExchangeTime ? formatElapsed(durationSeconds) : '—';

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await authService.refreshSessionKey();
      toast.success('会话密钥已刷新', '后续通信使用新密钥');
    } catch (e) {
      toast.error('刷新失败', e instanceof Error ? e.message : undefined);
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div>
      {/* 嵌入移动端设置页时使用 h3 标题，避免页面出现多个 h1（Bug#11） */}
      {embedded ? (
        <h3 className="mb-2 text-[15px] font-semibold">安全状态</h3>
      ) : (
        <PageHeader title="安全状态" subtitle="会话与加密通道" />
      )}
      <div className={embedded ? '' : 'p-4'}>
        <Card pad="none">
          <StatusRow
            label="会话状态"
            icon={encrypted ? 'locked' : 'warning'}
            value={
              <Badge tone={encrypted ? 'success' : 'danger'} icon={encrypted ? 'checked' : 'alert'}>
                {encrypted ? '已加密（ChaCha20-Poly1305）' : '未建立加密会话'}
              </Badge>
            }
          />
          <StatusRow
            label="Session ID"
            value={<span className="mono text-[12.5px]">{sessionId ? `${sessionId.slice(0, 8)}…` : '—'}</span>}
          />
          <StatusRow
            label="密钥交换时间"
            value={
              keyExchangeTime
                ? `${new Date(keyExchangeTime * 1000).toLocaleString('zh-CN')}（持续 ${duration}）`
                : '—'
            }
          />
          <StatusRow
            label="当前用户"
            value={`${username || '—'}${role === 'admin' ? '（管理员）' : ''}`}
          />
          <StatusRow label="加密算法" value="X25519 ECDH + HKDF-SHA256" />
          <StatusRow label="前向保密" value="已启用（临时密钥、私钥用完即销毁）" />
        </Card>

        <Button
          variant="primary"
          className="mt-4 w-full"
          icon="refresh"
          loading={refreshing}
          disabled={refreshing || !encrypted}
          onClick={handleRefresh}
        >
          {refreshing ? '刷新中…' : '刷新会话密钥'}
        </Button>

        <p className="mt-2 text-center text-[11.5px] text-subtle">
          刷新后原会话密钥立即失效，需重新进行密钥交换
        </p>
      </div>
    </div>
  );
}

function StatusRow({
  label,
  value,
  icon,
}: {
  label: string;
  value: React.ReactNode;
  icon?: IconName;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line-subtle px-4 py-3 last:border-b-0">
      <span className="flex shrink-0 items-center gap-2 text-[13px] text-muted">
        {icon && <Icon name={icon} size="sm" />}
        {label}
      </span>
      <span className="min-w-0 text-right text-[13px] break-words text-fg">{value}</span>
    </div>
  );
}

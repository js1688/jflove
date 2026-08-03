import { useEffect, useState } from 'react';
import { useAuthStore } from '../stores/auth-store';
import { authService } from '../services/auth-service';
import { getSessionId, getKeyExchangeTime, isEncrypted } from '../utils/session';
import { PageHeader } from '../components/PageHeader';

/** 安全状态页 */
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

  const duration = keyExchangeTime
    ? formatDuration(Date.now() / 1000 - keyExchangeTime)
    : '-';

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await authService.refreshSessionKey();
      // 刷新成功 toast 提示（对标桌面端 InfoBar.success）
      const toast = document.createElement('div');
      toast.className = 'fixed top-4 right-4 z-50 px-4 py-2 bg-green-50 border border-green-200 text-green-700 text-sm rounded-lg shadow';
      toast.textContent = '会话密钥已刷新，后续通信使用新密钥';
      document.body.appendChild(toast);
      setTimeout(() => toast.remove(), 3000);
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div>
      {/* 嵌入移动端设置页时使用 h3 标题，避免页面出现多个 h1（Bug#11） */}
      {embedded ? (
        <h3 className="text-base font-semibold text-gray-800 mb-2">安全状态</h3>
      ) : (
        <PageHeader title="安全状态" />
      )}
      <div className={embedded ? '' : 'p-4'}>
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 divide-y divide-gray-50">
          <StatusRow
            label="会话状态"
            value={encrypted ? '✅ 已加密（ChaCha20-Poly1305）' : '❌ 未建立加密会话'}
            valueClass={encrypted ? 'text-green-600' : 'text-red-500'}
          />
          <StatusRow
            label="Session ID"
            value={sessionId ? sessionId.slice(0, 8) + '…' : '-'}
          />
          <StatusRow
            label="密钥交换时间"
            value={keyExchangeTime
              ? `${new Date(keyExchangeTime * 1000).toLocaleString('zh-CN')}（已持续 ${duration}）`
              : '-'
            }
          />
          <StatusRow
            label="当前用户"
            value={`${username || '-'}${role === 'admin' ? '（管理员）' : ''}`}
          />
        </div>

        <button
          onClick={handleRefresh}
          disabled={refreshing || !encrypted}
          className="mt-4 w-full py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
        >
          {refreshing ? '刷新中…' : '刷新会话密钥'}
        </button>

        <p className="text-xs text-gray-400 mt-2 text-center">
          刷新后原会话密钥立即失效，需重新密钥交换
        </p>
      </div>
    </div>
  );
}

function StatusRow({
  label,
  value,
  valueClass = 'text-gray-700',
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="flex justify-between items-center px-4 py-3">
      <span className="text-sm text-gray-500">{label}</span>
      <span className={`text-sm ${valueClass}`}>{value}</span>
    </div>
  );
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return `${h} 小时 ${m} 分钟`;
}

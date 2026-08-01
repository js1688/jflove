import { useState } from 'react';
import { useAuthStore } from '../stores/auth-store';
import { authService } from '../services/auth-service';
import { getSessionId, getKeyExchangeTime, isEncrypted } from '../utils/session';
import { PageHeader } from '../components/PageHeader';

/** 安全状态页 */
export function SecurityPage() {
  const { username, role } = useAuthStore();
  const [refreshing, setRefreshing] = useState(false);

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
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div>
      <PageHeader title="安全状态" />
      <div className="p-4">
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

import { useState, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { authService } from '../services/auth-service';
import { serverHistoryService } from '../services/server-history-service';
import { SESSION_TTL_OPTIONS, SESSION_TTL_DEFAULT, APP_VERSION } from '../config/constants';
import { ErrorBanner } from '../components/ErrorBanner';

type Step = 'connect' | 'init-admin' | 'login';

/** 登录页 */
export function LoginPage() {
  const navigate = useNavigate();
  const { login, keyExchange, isLoggedIn } = useAuthStore();

  const [step, setStep] = useState<Step>('connect');
  const [serverUrl, setServerUrl] = useState(serverHistoryService.getDefault());
  const [serverHistory, setServerHistory] = useState<string[]>([]);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [ttlSeconds, setTtlSeconds] = useState(SESSION_TTL_DEFAULT);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 加载地址历史
  useEffect(() => {
    setServerHistory(serverHistoryService.listHistory());
  }, []);

  // 已登录则跳转首页
  useEffect(() => {
    if (isLoggedIn) navigate('/', { replace: true });
  }, [isLoggedIn, navigate]);

  // 密钥交换 + 检测管理员
  const handleConnect = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await keyExchange(serverUrl.trim());
      setServerHistory(serverHistoryService.listHistory());

      const adminExists = await authService.adminExists();
      if (adminExists) {
        setStep('login');
      } else {
        setStep('init-admin');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '连接失败，请检查服务器地址');
    } finally {
      setLoading(false);
    }
  }, [serverUrl, keyExchange]);

  // 初始化管理员
  const handleInitAdmin = useCallback(async () => {
    if (!username.trim() || !password) {
      setError('请填写用户名和密码');
      return;
    }
    if (password !== confirmPassword) {
      setError('两次输入的密码不一致');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await authService.initAdmin(username.trim(), password);
      // 初始化管理员成功后自动登录
      await login(username.trim(), password, ttlSeconds);
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建管理员失败');
    } finally {
      setLoading(false);
    }
  }, [username, password, confirmPassword, ttlSeconds, login]);

  // 登录
  const handleLogin = useCallback(async () => {
    if (!username.trim() || !password) {
      setError('请填写用户名和密码');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await login(username.trim(), password, ttlSeconds);
    } catch (e) {
      setError(e instanceof Error ? e.message : '登录失败');
    } finally {
      setLoading(false);
    }
  }, [username, password, ttlSeconds, login]);

  // 删除历史
  const deleteHistory = (url: string) => {
    serverHistoryService.delete(url);
    setServerHistory(serverHistoryService.listHistory());
  };

  return (
    <div className="bg-white rounded-2xl shadow-lg p-8">
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {/* Header */}
      <div className="text-center mb-6">
        <span className="text-3xl">🔐</span>
        <h1 className="text-xl font-bold text-gray-800 mt-2">JFLove</h1>
        <p className="text-xs text-gray-400 mt-1">
          所有通信均经过加密，连接后自动交换临时会话密钥。
        </p>
      </div>

      {/* Step: connect */}
      {step === 'connect' && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">服务端地址</label>
            <div className="flex gap-1">
              <input
                type="text"
                list="server-history-list"
                value={serverUrl}
                onChange={e => setServerUrl(e.target.value)}
                placeholder="http://localhost:8989"
                className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <datalist id="server-history-list">
                {serverHistory.map(url => (
                  <option key={url} value={url} />
                ))}
              </datalist>
            </div>
            {/* 历史记录 */}
            {serverHistory.length > 0 && (
              <div className="mt-2 space-y-1">
                {serverHistory.map(url => (
                  <div key={url} className="flex items-center gap-1 text-xs">
                    <button
                      onClick={() => setServerUrl(url)}
                      className="text-indigo-600 hover:text-indigo-800 truncate flex-1 text-left"
                    >
                      {url}
                    </button>
                    <button
                      onClick={() => deleteHistory(url)}
                      className="text-gray-300 hover:text-red-400"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <button
            onClick={handleConnect}
            disabled={loading || !serverUrl.trim()}
            className="w-full py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
          >
            {loading ? '连接中…' : '连接'}
          </button>
        </div>
      )}

      {/* Step: init-admin */}
      {step === 'init-admin' && (
        <div className="space-y-4">
          <div className="text-sm text-gray-500 bg-amber-50 border border-amber-200 rounded-lg p-3">
            系统尚未配置管理员，请创建管理员账号。
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">用户名</label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">密码</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">确认密码</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setStep('connect')}
              className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
            >
              返回
            </button>
            <button
              onClick={handleInitAdmin}
              disabled={loading}
              className="flex-1 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {loading ? '创建中…' : '创建管理员并登录'}
            </button>
          </div>
        </div>
      )}

      {/* Step: login */}
      {step === 'login' && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">用户名</label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleLogin()}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">密码</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleLogin()}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">登录有效期</label>
            <select
              value={ttlSeconds}
              onChange={e => setTtlSeconds(Number(e.target.value))}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
            >
              {SESSION_TTL_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
          <button
            onClick={handleLogin}
            disabled={loading}
            className="w-full py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {loading ? '登录中…' : '登录'}
          </button>
        </div>
      )}

      {/* 版本号 */}
      <div className="text-center mt-6">
        <span className="text-xs text-gray-300">v{APP_VERSION}</span>
      </div>
    </div>
  );
}

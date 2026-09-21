/**
 * 登录页（桌面端）
 *
 * 视觉与交互**逐字取自 Web 端** `jflove-web/src/pages/LoginPage.tsx`，
 * 只改了 3 处桌面端特有的语义（均已在下载代码中就地标注）：
 *   1. 桌面端不存在"同源"：服务端地址必须显式填写完整地址；
 *   2. 地址为空时直接禁用连接按钮（空串在桌面端不是合法输入）；
 *   3. 地址历史来自 Python（`%APPDATA%\JFLove\storage\server_history.json`）。
 */

import { useState, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { authService } from '../services/auth-service';
import { serverHistoryService } from '../services/server-history-service';
import { SESSION_TTL_OPTIONS, SESSION_TTL_DEFAULT, APP_VERSION } from '../config/constants';
import { ErrorBanner } from '../components/ErrorBanner';
import { Icon, Button } from '../components/ui';
import { ServerUrlInput } from '../components/desktop/ServerUrlInput';

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
      // 空字符串是合法输入（= 同源）：keyExchange 会把它规范成同源再握手
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
    <div className="card w-full p-8" style={{ boxShadow: 'var(--e4)' }}>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {/* Header */}
      <div className="mb-6 text-center">
        <img
          src="./logo-256.png"
          alt=""
          aria-hidden="true"
          className="mx-auto h-14 w-14 rounded-2xl object-cover"
        />
        <h1 className="grad-text mt-3 text-[22px] font-bold tracking-[-0.02em]">JFLove</h1>
        <p className="mt-1 text-[11.5px] text-subtle">
          所有通信均经过端到端加密，连接后自动交换临时会话密钥。
        </p>
      </div>

      {/* Step: connect */}
      {step === 'connect' && (
        <div className="flex flex-col gap-4">
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">服务端地址</label>
            {/*
              桌面端与 Web 端的两点差异：
              1. 用自绘下拉替代原生 `<datalist>`（后者的弹层在 shadow DOM 里、
                 CSS 无法美化，观感与设计系统不符）；
              2. 因此下方那条「历史记录」列表被下拉面板取代（含删除按钮），
                 避免同一份历史在页面上出现两次。
            */}
            <ServerUrlInput
              value={serverUrl}
              onChange={setServerUrl}
              suggestions={serverHistory}
              onDeleteSuggestion={deleteHistory}
              placeholder="例如 http://127.0.0.1:8989"
              onEnter={() => void handleConnect()}
            />
            {/* 桌面端与 Web 端的差异：桌面端没有"同源"这回事
                （页面是 file:// 加载的，没有站点根），必须填写完整地址。 */}
            <p className="mt-1.5 text-[11.5px] text-subtle">
              需填写完整的服务端地址（含协议与端口），例如 http://127.0.0.1:8989
            </p>
          </div>
          <Button
            variant="primary"
            className="w-full"
            icon="link"
            loading={loading}
            /* 桌面端与 Web 端的差异：空地址在桌面端无从连接，直接禁用 */
            disabled={loading || !serverUrl.trim()}
            onClick={handleConnect}
          >
            {loading ? '连接中…' : '连接服务端'}
          </Button>
        </div>
      )}

      {/* Step: init-admin */}
      {step === 'init-admin' && (
        <div className="flex flex-col gap-4">
          <div
            className="flex items-start gap-2 rounded-lg border p-3 text-[12.5px]"
            style={{
              backgroundColor: 'var(--warning-50)',
              borderColor: 'rgb(245 158 11 / 0.28)',
              color: 'var(--warning-700)',
            }}
          >
            <Icon name="warning" size="sm" className="mt-px shrink-0" />
            <span>系统尚未配置管理员，请创建管理员账号。</span>
          </div>
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">用户名</label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              className="input"
            />
          </div>
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">密码</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="input"
            />
          </div>
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">确认密码</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)}
              className="input"
            />
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" icon="back" onClick={() => setStep('connect')}>
              返回
            </Button>
            <Button
              variant="primary"
              className="flex-1"
              icon="userAdd"
              loading={loading}
              disabled={loading}
              onClick={handleInitAdmin}
            >
              {loading ? '创建中…' : '创建管理员并登录'}
            </Button>
          </div>
        </div>
      )}

      {/* Step: login */}
      {step === 'login' && (
        <div className="flex flex-col gap-4">
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">用户名</label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleLogin()}
              className="input"
            />
          </div>
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">密码</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleLogin()}
              className="input"
            />
          </div>
          <div>
            <label className="mb-1 block text-[13px] font-medium text-muted">登录有效期</label>
            <select
              value={ttlSeconds}
              onChange={e => setTtlSeconds(Number(e.target.value))}
              className="input bg-surface"
            >
              {SESSION_TTL_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
          <Button
            variant="primary"
            className="w-full"
            icon="logout"
            loading={loading}
            disabled={loading}
            onClick={handleLogin}
          >
            {loading ? '登录中…' : '登录'}
          </Button>
        </div>
      )}

      {/* 版本号 */}
      <div className="mt-6 text-center">
        <span className="tabular text-[11px] text-subtle">v{APP_VERSION}</span>
      </div>
    </div>
  );
}

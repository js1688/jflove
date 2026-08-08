import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { useAuthStore } from './stores/auth-store';
import { resyncSession } from './utils/http-client';
import './index.css';

const root = document.getElementById('root');
if (!root) throw new Error('未找到 #root 元素');

// 从 localStorage 恢复登录态（token / 用户信息；session_key 不持久化，
// 需要重新密钥交换，由 http-client 在 401 时自动触发）
useAuthStore.getState().initFromStorage();

// 免登录恢复后预建立加密会话（session_key 不持久化）。
// 复用 http-client 的单飞 resyncSession，与请求时的惰性建立共用同一锁，
// 避免并发触发两次密钥交换导致会话冲突。
if (useAuthStore.getState().isLoggedIn) {
  resyncSession().catch(() => {
    useAuthStore.getState().logout();
  });
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

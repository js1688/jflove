import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { useAuthStore } from './stores/auth-store';
import './index.css';

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('未找到 #root 元素');
const root: HTMLElement = rootElement;

/**
 * 启动引导（桌面端与 Web 端的差异点）
 *
 * Web 端：同步读 localStorage 恢复登录态 → 立即渲染 → 再异步补密钥交换，
 * 因此会先闪一下登录页再跳走。
 *
 * 桌面端：**先问 Python 有没有可恢复的会话**（`auth_service.try_restore_session()`
 * 会读 `%APPDATA%\JFLove\storage\session.json` 并顺手重建 ECDH 会话），
 * 拿到结果再挂载 React —— 免登录启动时不会闪登录页。
 *
 * 桥不可用（例如页面被浏览器直接打开）时不阻塞渲染，按未登录处理，
 * 登录页会给出明确提示。
 */
async function bootstrap(): Promise<void> {
  try {
    await useAuthStore.getState().bootstrap();
  } catch (e) {
    console.warn('[jflove] 启动引导失败，将按未登录状态渲染：', e);
  }

  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void bootstrap();

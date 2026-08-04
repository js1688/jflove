/**
 * Service Worker 流式代理（页面侧辅助）
 *
 * 对标桌面端 StreamProxy 的「注册 / 开流 / 关流」控制面。
 * 职责：
 *  - 注册 SW（dev: /src/sw/index.ts；prod: /sw.js）
 *  - 把加密会话（session_key / session_id / token / server_url）同步给 SW
 *  - 打开 / 关闭一次预览的流式 URL（token 不透明，URL 不含业务数据）
 *
 * 安全：session_key 以 base64 仅通过 postMessage 传入 SW 内存，不写入日志/DOM。
 * SW 仅安全上下文可用；非安全上下文 isStreamProxySupported() 返回 false，
 * 由页面回退 MSE / 完整下载。
 */

import { getSessionKey, getSessionId, getToken, getServerUrl } from './session';
import { arrayBufferToBase64 } from './crypto';

// ── 能力检测 ──

/** SW 是否可用（navigator.serviceWorker 仅在安全上下文存在） */
export function isStreamProxySupported(): boolean {
  return typeof navigator !== 'undefined' && 'serviceWorker' in navigator;
}

// ── 注册（单飞） ──

let registrationPromise: Promise<boolean> | null = null;

/**
 * 注册流式代理 SW。结果缓存，重复调用直接返回。
 *
 * dev / prod 均注册根路径 `/sw.js`：
 *  - prod：vite build 输出 dist/sw.js
 *  - dev：vite.config.ts 的 jflove-sw-dev 插件实时转换 src/sw/index.ts 并提供
 *    Service-Worker-Allowed: / 头（允许 scope 覆盖全站，拦截 /jflove-stream/*）
 */
export function registerStreamProxy(): Promise<boolean> {
  if (!isStreamProxySupported()) return Promise.resolve(false);
  if (registrationPromise) return registrationPromise;

  registrationPromise = navigator.serviceWorker
    .register('/sw.js', { type: 'module', scope: '/' })
    .then(() => true)
    .catch((e) => {
      // 注册失败（如 nginx 未配置 /sw.js）记为不可用，不阻塞页面
      console.warn('[stream-proxy] SW 注册失败:', e);
      return false;
    });
  return registrationPromise;
}

// ── 消息收发（MessageChannel 请求/应答） ──

let readyPromise: Promise<boolean> | null = null;

/** 确保 SW 已注册且已接管页面（controller 可用） */
async function ensureController(): Promise<boolean> {
  if (!isStreamProxySupported()) return false;
  if (!readyPromise) {
    readyPromise = (async () => {
      const registered = await registerStreamProxy();
      if (!registered) return false;
      await navigator.serviceWorker.ready;
      // 若页面已被 SW 控制则立即可用；否则等待 controllerchange
      if (navigator.serviceWorker.controller) return true;
      await new Promise<void>((resolve) => {
        const onChange = () => {
          navigator.serviceWorker.removeEventListener('controllerchange', onChange);
          resolve();
        };
        navigator.serviceWorker.addEventListener('controllerchange', onChange);
        // 兜底超时（10s），避免永久挂起
        setTimeout(() => {
          navigator.serviceWorker.removeEventListener('controllerchange', onChange);
          resolve();
        }, 10000);
      });
      return !!navigator.serviceWorker.controller;
    })();
  }
  return readyPromise;
}

/** 向 SW 发送消息并通过 MessageChannel 等待应答 */
function postMessageWithReply(
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const sw = navigator.serviceWorker.controller;
    if (!sw) {
      reject(new Error('SW 未就绪'));
      return;
    }
    const channel = new MessageChannel();
    const timeout = setTimeout(() => {
      channel.port1.close();
      reject(new Error('SW 消息超时'));
    }, 10000);
    channel.port1.onmessage = (e) => {
      clearTimeout(timeout);
      channel.port1.close();
      resolve((e.data || {}) as Record<string, unknown>);
    };
    sw.postMessage(payload, [channel.port2]);
  });
}

// ── 会话同步 ──

/**
 * 把当前加密会话同步给 SW（登录 / 密钥交换 / 免登录恢复后调用）。
 * SW 无 localStorage，只能通过 postMessage 获取密钥。
 */
export async function syncStreamSession(): Promise<void> {
  if (!isStreamProxySupported()) return;
  const sessionKey = getSessionKey();
  const sessionId = getSessionId();
  const token = getToken();
  if (!sessionKey || !sessionId || !token) return;

  const ready = await ensureController();
  if (!ready) return;
  try {
    await postMessageWithReply({
      type: 'sync-session',
      sessionKeyB64: arrayBufferToBase64(sessionKey),
      sessionId,
      token,
      serverUrl: getServerUrl(),
    });
  } catch {
    // 同步失败不阻断预览（SW 侧会以 503 提示重新打开）
  }
}

// ── 开流 / 关流 ──

/**
 * 为一次视频/音频预览打开流式 URL。
 * @returns 可播放的 blob 风格 URL（/jflove-stream/<token>），失败返回 null
 */
export async function openStreamUrl(
  diskId: number,
  path: string,
  filename: string,
): Promise<string | null> {
  if (!isStreamProxySupported()) return null;
  const ready = await ensureController();
  if (!ready) return null;

  try {
    await syncStreamSession();
    const resp = await postMessageWithReply({
      type: 'open-stream',
      diskId,
      path,
      filename,
    });
    const url = resp.url;
    return typeof url === 'string' && url ? url : null;
  } catch {
    return null;
  }
}

/** 关闭一次预览的流式 URL，释放 SW 内存中的 token */
export function closeStreamUrl(url: string): void {
  if (!isStreamProxySupported() || !navigator.serviceWorker.controller) return;
  try {
    navigator.serviceWorker.controller.postMessage({ type: 'close-stream', url });
  } catch {
    /* SW 已失效，忽略 */
  }
}

/** 登出时清空 SW 内存中的会话与密钥 */
export function clearStreamProxySession(): void {
  if (!isStreamProxySupported() || !navigator.serviceWorker.controller) return;
  try {
    navigator.serviceWorker.controller.postMessage({ type: 'clear-session' });
  } catch {
    /* SW 已失效，忽略 */
  }
}

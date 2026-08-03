/**
 * 加密 HTTP 客户端
 *
 * 对标桌面端 src/utils/http_client.py 和移动端 lib/utils/http_service.dart。
 * 所有加密 API 调用统一走此模块，自动处理：
 *   - 加密信封（请求体加密 + 响应体解密）
 *   - ECDH 静默续约（401 会话过期自动重新密钥交换 + 重发）
 *   - 明文白名单豁免
 *
 * services/ 和 UI 层禁止直接使用 fetch()。
 */

import { ECDH_401_PATTERNS, REQUEST_TIMEOUT_MS, STREAM_TIMEOUT_MS } from '../config/constants';
import { encryptEnvelope, decryptEnvelope, generateKeyPair, deriveSessionKey } from './crypto';
import {
  getSessionKey, setSessionKey,
  getSessionId, setSessionId,
  getToken,
  setKeyExchangeTime,
  getServerUrl,
  clearSession,
} from './session';
import { ApiError } from '../types/models';
import type { KeyExchangeResponse } from '../types/models';

// ── 单飞续约控制 ──

let resyncPromise: Promise<void> | null = null;

// ── 请求构建 ──

function baseUrl(): string {
  return getServerUrl();
}

// ── 加密请求 ──

async function encryptedRequest<T>(
  method: string,
  path: string,
  body: Record<string, unknown> = {},
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  // 已登录但加密会话未建立（如页面刷新后免登录恢复）：自动重新密钥交换
  if ((!getSessionKey() || !getSessionId()) && getToken()) {
    await resyncSession();
  }

  const sessionKey = getSessionKey();
  const sessionId = getSessionId();

  if (!sessionKey || !sessionId) {
    throw new ApiError(0, '未建立加密会话，请先进行密钥交换');
  }

  // 注入 token
  const token = getToken();
  if (token) {
    body.token = token;
  }

  const url = `${baseUrl()}${path}`;
  const bodyJson = new TextEncoder().encode(JSON.stringify(body));
  const { nonce, ciphertext } = encryptEnvelope(sessionKey, bodyJson);
  const envelope = JSON.stringify({ nonce, ciphertext });

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        'X-Session-ID': sessionId,
        ...extraHeaders,
      },
      // 所有方法（含 GET）都发送加密 body：后端通过 await request.body() 读取
      body: envelope,
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    // 401 自动续约
    if (response.status === 401) {
      return handle401AndRetry<T>(method, path, body, response, extraHeaders);
    }

    if (!response.ok) {
      // 405：请求方法不被该接口支持（服务端版本不兼容），给用户明确提示
      if (response.status === 405) {
        throw new ApiError(405, '服务端不支持该请求方式（405），请确认服务端版本与 Web 端兼容。');
      }
      const errorBody = await tryDecryptError(response, sessionKey);
      throw new ApiError(response.status, errorBody?.detail || `HTTP ${response.status}`);
    }

    const respJson: { nonce: string; ciphertext: string } = await response.json();
    const decrypted = decryptEnvelope(sessionKey, respJson.nonce, respJson.ciphertext);
    return JSON.parse(new TextDecoder().decode(decrypted)) as T;
  } catch (e) {
    clearTimeout(timeoutId);
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError(0, '请求超时');
    }
    throw new ApiError(0, `网络错误：${e instanceof Error ? e.message : String(e)}`);
  }
}

// ── 加密 GET（URL query 传信封） ───────────────────

/**
 * 加密 GET：浏览器禁止 GET/HEAD 携带 body（fetch/XHR 均丢弃），
 * 因此把加密信封放入 URL query（?nonce=...&ciphertext=...）发送。
 * 信封内容为 ChaCha20-Poly1305 密文，URL 中不泄漏明文业务参数；
 * 服务端 middleware 在请求体为空时从 query 读取信封（全局通用改动）。
 */
async function getQueryEncrypted<T>(
  path: string,
  body: Record<string, unknown> = {},
): Promise<T> {
  // 已登录但加密会话未建立（如页面刷新后免登录恢复）：自动重新密钥交换
  if ((!getSessionKey() || !getSessionId()) && getToken()) {
    await resyncSession();
  }

  const sessionKey = getSessionKey();
  const sessionId = getSessionId();
  if (!sessionKey || !sessionId) {
    throw new ApiError(0, '未建立加密会话，请先进行密钥交换');
  }

  // 注入 token
  const token = getToken();
  if (token) {
    body.token = token;
  }

  const url = `${baseUrl()}${path}`;
  const bodyJson = new TextEncoder().encode(JSON.stringify(body));
  const { nonce, ciphertext } = encryptEnvelope(sessionKey, bodyJson);
  const query = new URLSearchParams({ nonce, ciphertext }).toString();

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`${url}?${query}`, {
      method: 'GET',
      headers: {
        'X-Session-ID': sessionId,
      },
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    // 401 自动续约
    if (response.status === 401) {
      return handle401AndRetry<T>('GET', path, body, response);
    }

    if (!response.ok) {
      const errorBody = await tryDecryptError(response, sessionKey);
      throw new ApiError(response.status, errorBody?.detail || `HTTP ${response.status}`);
    }

    const respJson: { nonce: string; ciphertext: string } = await response.json();
    const decrypted = decryptEnvelope(sessionKey, respJson.nonce, respJson.ciphertext);
    return JSON.parse(new TextDecoder().decode(decrypted)) as T;
  } catch (e) {
    clearTimeout(timeoutId);
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError(0, '请求超时');
    }
    throw new ApiError(0, `网络错误：${e instanceof Error ? e.message : String(e)}`);
  }
}

// ── 401 处理 ──

async function handle401AndRetry<T>(
  method: string,
  path: string,
  body: Record<string, unknown>,
  response: Response,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  // 优先解密错误信封获取真实 detail（后端错误响应均为加密信封）
  const sessionKey = getSessionKey();
  const detail = sessionKey
    ? (await tryDecryptError(response, sessionKey))?.detail || ''
    : await extractErrorDetail(response);

  // 检查是否为 ECDH 会话过期类 401 → 静默续约后重发
  if (ECDH_401_PATTERNS.some(p => detail.includes(p))) {
    await resyncSession();
    if (method === 'GET') {
      // 浏览器 GET 无法携带 body，续约后仍需走 URL query 信封
      return getQueryEncrypted<T>(path, body);
    }
    return encryptedRequest<T>(method, path, body, extraHeaders);
  }

  // 登录接口的 401 是"用户名或密码错误"，不应清除会话密钥，直接抛错由 UI 展示
  if (path === '/api/v1/auth/login') {
    throw new ApiError(401, detail || '用户名或密码错误');
  }

  // 其余 JWT 过期类 401 — 清除会话，让 UI 层处理
  clearSession();
  throw new ApiError(401, detail);
}

async function extractErrorDetail(response: Response): Promise<string> {
  try {
    const json = await response.json() as { detail?: string };
    return json.detail || '';
  } catch {
    return '';
  }
}

async function tryDecryptError(
  response: Response,
  sessionKey: Uint8Array,
): Promise<{ detail: string } | null> {
  try {
    const json: { nonce?: string; ciphertext?: string } = await response.json();
    if (json.nonce && json.ciphertext) {
      const decrypted = decryptEnvelope(sessionKey, json.nonce, json.ciphertext);
      return JSON.parse(new TextDecoder().decode(decrypted)) as { detail: string };
    }
    return json as { detail: string };
  } catch {
    return null;
  }
}

// ── ECDH 静默续约 ──

/**
 * 重新密钥交换（单飞：并发调用只执行一次）。
 * 由 401 续约、请求前惰性建立、应用启动预建立（main.tsx）共用，避免并发冲突。
 */
export async function resyncSession(): Promise<void> {
  // 单飞续约：多个并发 401 只触发一次 key-exchange
  if (resyncPromise) {
    await resyncPromise;
    return;
  }

  resyncPromise = (async () => {
    try {
      const { publicKeyB64, privateKey } = await generateKeyPair();
      const resp = await fetch(`${baseUrl()}/api/v1/auth/key-exchange`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_public_key: publicKeyB64 }),
      });

      if (!resp.ok) {
        throw new Error(`密钥交换失败：HTTP ${resp.status}`);
      }

      const data: KeyExchangeResponse = await resp.json();
      const sessionKey = await deriveSessionKey(privateKey, data.server_public_key);

      setSessionKey(sessionKey);
      setSessionId(data.session_id);
      setKeyExchangeTime(Date.now() / 1000);
    } finally {
      resyncPromise = null;
    }
  })();

  await resyncPromise;
}

// ── 明文请求 ──

async function plainRequest<T>(
  method: string,
  path: string,
  body?: Record<string, unknown>,
): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const url = `${baseUrl()}${path}`;
    const options: RequestInit = {
      method,
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
    };
    if (body && method !== 'GET') {
      options.body = JSON.stringify(body);
    }

    const response = await fetch(url, options);
    clearTimeout(timeoutId);

    if (!response.ok) {
      throw new ApiError(response.status, `HTTP ${response.status}`);
    }

    return (await response.json()) as T;
  } catch (e) {
    clearTimeout(timeoutId);
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, `网络错误：${e instanceof Error ? e.message : String(e)}`);
  }
}

// ── 流式下载（GET + query 传信封，流式响应） ──

async function downloadStream(
  path: string,
  body: Record<string, unknown> = {},
): Promise<ReadableStream<Uint8Array>> {
  // 已登录但加密会话未建立（如页面刷新后免登录恢复）：自动重新密钥交换
  if ((!getSessionKey() || !getSessionId()) && getToken()) {
    await resyncSession();
  }

  const sessionKey = getSessionKey();
  const sessionId = getSessionId();

  if (!sessionKey || !sessionId) {
    throw new ApiError(0, '未建立加密会话');
  }

  const token = getToken();
  if (token) {
    body.token = token;
  }

  const url = `${baseUrl()}${path}`;
  const bodyJson = new TextEncoder().encode(JSON.stringify(body));
  const { nonce, ciphertext } = encryptEnvelope(sessionKey, bodyJson);
  const query = new URLSearchParams({ nonce, ciphertext }).toString();

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS);

  try {
    const response = await fetch(`${url}?${query}`, {
      method: 'GET',
      headers: {
        'X-Session-ID': sessionId,
      },
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!response.ok) {
      if (response.status === 401) {
        const errDetail = sessionKey
          ? (await tryDecryptError(response, sessionKey))?.detail || ''
          : await extractErrorDetail(response);
        if (ECDH_401_PATTERNS.some(p => errDetail.includes(p))) {
          await resyncSession();
          return downloadStream(path, body);
        }
      }
      throw new ApiError(response.status, `流式请求失败：HTTP ${response.status}`);
    }

    if (!response.body) {
      throw new ApiError(0, '响应体为空');
    }

    // 返回流式响应体，由调用方（stream-frame）逐帧解密
    return response.body;
  } catch (e) {
    clearTimeout(timeoutId);
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, `流式网络错误：${e instanceof Error ? e.message : String(e)}`);
  }
}

// ── 公开 API ──

export const httpClient = {
  /** 加密 POST */
  post: <T>(path: string, body?: Record<string, unknown>) =>
    encryptedRequest<T>('POST', path, body),

  /**
   * 加密 GET。
   * 浏览器禁止 GET 携带 body（fetch/XHR 均丢弃），故加密信封放入 URL query 发送
   * （?nonce=...&ciphertext=...），服务端 middleware 在请求体为空时从 query 读取，
   * 全局兼容，无需后端为各接口增加 POST 别名。
   */
  get: <T>(path: string, body?: Record<string, unknown>) =>
    getQueryEncrypted<T>(path, body),

  /** 加密 PUT */
  put: <T>(path: string, body?: Record<string, unknown>) =>
    encryptedRequest<T>('PUT', path, body),

  /** 加密 DELETE */
  delete: <T>(path: string, body?: Record<string, unknown>) =>
    encryptedRequest<T>('DELETE', path, body),

  /** 流式下载（ReadableStream，调用方负责逐帧解密） */
  downloadStream: (path: string, body?: Record<string, unknown>) =>
    downloadStream(path, body),

  // ── 明文请求（白名单接口） ──

  /** 明文 POST（key-exchange） */
  postPlain: <T>(path: string, body: Record<string, unknown>) =>
    plainRequest<T>('POST', path, body),

  /** 明文 GET（admin-exists / health） */
  getPlain: <T>(path: string) =>
    plainRequest<T>('GET', path),
};

/**
 * 桌面端加密 HTTP 客户端（桥转发版）
 *
 * 与 Web 端 `jflove-web/src/utils/http-client.ts` 的**公开 API 完全一致**
 * （`post` / `get` / `put` / `delete` / `postPlain` / `getPlain` / `resyncSession`），
 * 因此上层的 `services/*` 与页面代码可以逐字复用。但内部实现完全不同：
 *
 * | | Web 端 | 桌面端 |
 * | --- | --- | --- |
 * | 加密 | JS 里做 X25519 + ChaCha20-Poly1305 | Python 侧 `http_client.py` 做 |
 * | 传输 | `fetch()` | 无网络出口，经 QWebChannel 交给 Python |
 * | 会话密钥 | JS 内存 | 只在 Python，永不下发 |
 * | 401 续约 | JS 自己重试 | Python 的 `_send_with_auto_resync` 已处理 |
 *
 * 这样加密链路**只有一份实现**，不会出现第二份需要同步维护的密码学代码。
 */

import { ApiError } from '../types/models';
import { callBridge } from './desktop-bridge';
import { clearSession } from './session';

/** Python 侧 `api.request` 的返回结构 */
interface ApiResult {
  status: number;
  body: Record<string, unknown>;
}

/** 只需本地判断是否登录过，不参与任何加解密 */
const LOGIN_PATH = '/api/v1/auth/login';

/**
 * 发起一次请求：把 (method, path, body) 交给 Python，取回**已解密**的响应体。
 *
 * :param plain: 是否走明文白名单接口（key-exchange / admin-exists / health）
 */
async function send<T>(
  method: string,
  path: string,
  body?: Record<string, unknown>,
  plain = false,
): Promise<T> {
  let result: ApiResult;
  try {
    result = await callBridge<ApiResult>('api', 'request', {
      method,
      path,
      body: body ?? {},
      plain,
    });
  } catch (e) {
    throw new ApiError(0, `桥调用失败：${e instanceof Error ? e.message : String(e)}`);
  }

  const status = Number(result?.status ?? 0);
  const payload = (result?.body ?? {}) as Record<string, unknown>;

  if (status >= 400) {
    const detail =
      typeof payload.detail === 'string' ? payload.detail : `HTTP ${status}`;
    // JWT 过期：清掉本地渲染态，让路由守卫把用户送回登录页。
    // 登录接口本身的 401 是"用户名或密码错误"，不能据此清会话。
    if (status === 401 && path !== LOGIN_PATH) {
      clearSession();
    }
    throw new ApiError(status, detail);
  }

  return payload as T;
}

export const httpClient = {
  /** 加密 POST */
  post: <T>(path: string, body?: Record<string, unknown>) => send<T>('POST', path, body),

  /** 加密 GET（桌面端可以把 body 放在加密请求体里，不像浏览器只能塞 URL） */
  get: <T>(path: string, body?: Record<string, unknown>) => send<T>('GET', path, body),

  /** 加密 PUT */
  put: <T>(path: string, body?: Record<string, unknown>) => send<T>('PUT', path, body),

  /** 加密 DELETE */
  delete: <T>(path: string, body?: Record<string, unknown>) =>
    send<T>('DELETE', path, body),

  /** 明文 POST（key-exchange） */
  postPlain: <T>(path: string, body: Record<string, unknown>) =>
    send<T>('POST', path, body, true),

  /** 明文 GET（admin-exists / health） */
  getPlain: <T>(path: string) => send<T>('GET', path, undefined, true),

  /**
   * 流式下载（桌面端尚未接入）。
   *
   * 桌面端的媒体与文件预览走**原生解码浮层**（P0-4 / N12 / N14），
   * 不经由 renderer 拿字节流，所以这里刻意不实现，避免出现一条
   * "JS 直接取文件流"的旁路。
   */
  downloadStream: (_path: string, _body?: Record<string, unknown>) =>
    Promise.reject(
      new ApiError(0, '桌面端流式下载尚未接入：文件预览由 N12 / N14 节点实现'),
    ),
};

/**
 * 重新确认加密会话。
 *
 * 与 Web 端的差异：桌面端不重建密钥（那由 Python 负责），
 * 这里只是让 Python 确保会话可用。保留同名导出是为了让上层引导代码保持一致。
 */
export async function resyncSession(): Promise<void> {
  await callBridge('auth', 'ensure_session', {});
}

/**
 * Service Worker 流式代理（桌面端 StreamProxy 的 Web 版）
 *
 * 拦截 `/jflove-stream/<token>` 请求，解析 HTTP Range 头，向后端
 * `/api/v1/files/stream`（v2 帧协议）拉取加密帧流，逐帧解密后以
 * `206 Partial Content` + `Content-Range` 返回给 `<video>/<audio>`，
 * 由浏览器原生解码器接管，实现真「边下边播 + 拖动 seek」。
 *
 * 安全（加密宪法 §9）：
 *  - URL 仅含不透明一次性 token，不携带任何业务数据（§9.1.4）
 *  - session_key / JWT 仅存 SW 内存，禁止写入日志
 *  - 会话由页面通过 postMessage 同步（SW 无法访问 localStorage）
 *
 * 依赖：
 *  - 后端 /api/v1/files/stream 已支持 range_start / range_end（v1.1.0+）
 *  - SW 仅安全上下文可用（HTTPS / localhost）；非安全上下文由页面回退 MSE / 完整下载
 */

import { base64ToUint8Array } from '../utils/crypto';
import {
  openEncryptedStream,
  parseRangeHeader,
  type StreamMeta,
  type StreamSessionParams,
} from '../utils/stream-frame';

// ── SW 全局最小类型 ──
// 说明：ServiceWorkerGlobalScope / FetchEvent 等 SW 类型位于 TS 的 lib.webworker 中，
// 而本项目 lib 仅启用 DOM，故在此以最小接口自行声明（运行时由 SW 全局提供），
// 避免引入 webworker lib 与 DOM lib 的全局命名冲突（Request/Response 等重复声明）。

/** SW fetch 事件（仅声明用到的字段） */
interface StreamFetchEvent {
  request: Request;
  respondWith(response: Response | Promise<Response>): void;
}

/** SW message 事件（仅声明用到的字段） */
interface StreamMessageEvent {
  data: unknown;
  ports: readonly MessagePort[];
  source: unknown;
}

/** install / activate 生命周期事件 */
interface StreamLifecycleEvent {
  waitUntil(promise: Promise<unknown>): void;
}

/** 可 postMessage 的客户端（页面） */
interface StreamClientLike {
  postMessage(message: unknown): void;
}

/** 当前 SW 全局作用域（self）的最小类型 */
interface StreamGlobalScope {
  addEventListener(type: 'install', listener: (e: StreamLifecycleEvent) => void): void;
  addEventListener(type: 'activate', listener: (e: StreamLifecycleEvent) => void): void;
  addEventListener(type: 'fetch', listener: (e: StreamFetchEvent) => void): void;
  addEventListener(type: 'message', listener: (e: StreamMessageEvent) => void): void;
  skipWaiting(): void;
  clients: { claim(): Promise<void> };
}

/** 页面 → SW 的消息（宽松字段，按 type 分支取值） */
interface StreamClientMessage {
  type?: string;
  sessionKeyB64?: string;
  sessionId?: string;
  token?: string;
  serverUrl?: string;
  diskId?: number;
  path?: string;
  filename?: string;
  id?: string;
  url?: string;
}

declare const self: StreamGlobalScope;

/** 流式请求 URL 前缀（不含业务数据的 opaque token 后缀） */
const STREAM_PREFIX = '/jflove-stream/';

/** 一次预览的流目标 */
interface StreamTarget {
  diskId: number;
  path: string;
  filename: string;
}

// ── SW 内存态（页面刷新后清空，需重新同步会话） ──

/** 当前加密会话（session_key 以 base64 保存，页面 postMessage 同步） */
let session: {
  sessionKeyB64: string;
  sessionId: string;
  token: string;
  serverUrl: string;
} | null = null;

/** token → 流目标 映射（token 不透明，URL 不暴露业务数据） */
const streams = new Map<string, StreamTarget>();

/** token → 元数据缓存（避免每次 seek 重复拉 meta 帧） */
const metaCache = new Map<string, StreamMeta>();

// ── 生命周期：立即接管，无需刷新 ──

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// ── 消息处理：会话同步 / 开流 / 关流 ──

self.addEventListener('message', (event) => {
  const msg = (event.data ?? {}) as StreamClientMessage;
  if (typeof msg !== 'object') return;

  switch (msg.type) {
    case 'sync-session': {
      // 页面密钥交换/登录完成后同步会话（仅存内存）
      session = {
        sessionKeyB64: String(msg.sessionKeyB64 || ''),
        sessionId: String(msg.sessionId || ''),
        token: String(msg.token || ''),
        serverUrl: String(msg.serverUrl || ''),
      };
      metaCache.clear();
      reply(event, { type: 'session-synced' });
      break;
    }
    case 'clear-session': {
      // 登出时清空内存中的密钥与会话
      session = null;
      streams.clear();
      metaCache.clear();
      reply(event, { type: 'session-cleared' });
      break;
    }
    case 'open-stream': {
      // 生成一次性 token，登记流目标，返回可播放 URL
      const token = randomToken();
      streams.set(token, {
        diskId: Number(msg.diskId),
        path: String(msg.path || ''),
        filename: String(msg.filename || ''),
      });
      reply(event, { type: 'stream-opened', id: msg.id, url: `${STREAM_PREFIX}${token}` });
      break;
    }
    case 'close-stream': {
      // 释放 token，阻止后续拉流
      const url = String(msg.url || '');
      const token = url.startsWith(STREAM_PREFIX) ? url.slice(STREAM_PREFIX.length) : '';
      if (token) {
        streams.delete(token);
        metaCache.delete(token);
      }
      reply(event, { type: 'stream-closed' });
      break;
    }
    default:
      break;
  }
});

/** 通过 MessageChannel port 回执（页面侧 await 结果） */
function reply(event: StreamMessageEvent, payload: Record<string, unknown>): void {
  const port = event.ports && event.ports[0];
  if (port) {
    port.postMessage(payload);
  } else {
    // 兼容无 port 的 postMessage（降级：通过 controller 广播，页面按 id 匹配）
    const source = event.source as StreamClientLike | null;
    if (source) source.postMessage(payload);
  }
}

// ── fetch 拦截：仅处理 /jflove-stream/* ──

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (!url.pathname.startsWith(STREAM_PREFIX)) return;
  event.respondWith(handleStreamRequest(event.request, url));
});

async function handleStreamRequest(request: Request, url: URL): Promise<Response> {
  const token = url.pathname.slice(STREAM_PREFIX.length);
  const target = streams.get(token);
  if (!target) return textResponse(404, '无效的流式 token');
  if (!session) return textResponse(503, '加密会话未就绪，请重新打开预览');

  const sessionParams: StreamSessionParams = {
    sessionKey: base64ToUint8Array(session.sessionKeyB64),
    sessionId: session.sessionId,
    token: session.token,
    serverUrl: session.serverUrl,
  };

  // HEAD：媒体探测（文件大小 / 类型），供播放器初始化
  if (request.method === 'HEAD') {
    try {
      const meta = await loadMeta(token, sessionParams, target);
      return new Response(null, {
        status: 200,
        headers: {
          'Content-Type': meta.content_type,
          'Content-Length': String(meta.file_size),
          'Accept-Ranges': 'bytes',
        },
      });
    } catch (e) {
      return textResponse(500, `流式元数据获取失败：${errMsg(e)}`);
    }
  }

  // GET：按 Range 边下边播
  try {
    const meta = await loadMeta(token, sessionParams, target);
    const { start, end } = parseRangeHeader(request.headers.get('Range'), meta.file_size);
    if (start >= end) {
      // 超出文件范围的 Range，返回 416
      return new Response(null, {
        status: 416,
        headers: { 'Content-Range': `bytes */${meta.file_size}` },
      });
    }

    // 打开后端加密 Range 流（meta 帧后的数据帧逐帧解密）
    const abortController = new AbortController();
    const { frames } = await openEncryptedStream(
      sessionParams,
      target.diskId,
      target.path,
      target.filename,
      start,
      end,
      abortController.signal,
    );

    const contentLength = end - start;
    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        try {
          for await (const chunk of frames) {
            controller.enqueue(chunk);
          }
          controller.close();
        } catch (e) {
          controller.error(e);
        }
      },
      cancel() {
        // 播放器中断（seek / 关闭预览）时中止后端拉流
        abortController.abort();
        void frames.return?.(undefined);
      },
    });

    return new Response(stream, {
      status: 206,
      headers: {
        'Content-Type': meta.content_type,
        'Content-Length': String(contentLength),
        'Content-Range': `bytes ${start}-${end - 1}/${meta.file_size}`,
        'Accept-Ranges': 'bytes',
      },
    });
  } catch (e) {
    return textResponse(500, `流式播放失败：${errMsg(e)}`);
  }
}

/**
 * 读取并缓存文件元数据（file_size / content_type）。
 * 首次向后端发 range(0,0) 请求拿 meta 帧并缓存，后续 seek 直接复用。
 */
async function loadMeta(
  token: string,
  sessionParams: StreamSessionParams,
  target: StreamTarget,
): Promise<StreamMeta> {
  const cached = metaCache.get(token);
  if (cached) return cached;

  const { meta, frames } = await openEncryptedStream(
    sessionParams,
    target.diskId,
    target.path,
    target.filename,
    0,
    0,
  );
  // 排空生成器释放 reader（range(0,0) 通常无数据帧）
  while (!(await frames.next()).done) {
    /* drain */
  }
  metaCache.set(token, meta);
  return meta;
}

// ── 工具函数 ──

/** 生成一次性不透明 token（16 字节随机 hex） */
function randomToken(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

function textResponse(status: number, message: string): Response {
  return new Response(message, {
    status,
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export {};

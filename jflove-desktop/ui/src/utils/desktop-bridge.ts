/**
 * 桌面端桥客户端（QWebChannel）
 *
 * ## 安全定位（AGENTS.md §9.5.16）
 *
 * 桌面端所有 HTTP 只走 Python 侧 `src/utils/http_client.py`。
 * 因此 **renderer 没有任何网络出口** —— 本模块是页面获取数据的唯一通道。
 * `session_key` 与 JWT 只存在于 Python，永不下发到 JS。
 *
 * ## 协议
 *
 * ```
 * 下行 JS → Python：  bridge.request(requestId, service, method, paramsJson)   void
 * 上行 Python → JS：  responseReady(requestId, payloadJson)
 *                     eventPushed(topic, payloadJson)
 * payloadJson = {ok: true, result: ...} | {ok: false, error: "..."}
 * ```
 *
 * requestId 由 JS 生成（P0-2 已验证的设计）：`request` 是 void 槽，不需要回传值，
 * 也就没有「先收到响应、后拿到 id」的竞态。
 *
 * ## qwebchannel.js 从哪来
 *
 * 由 Python 外壳在 `DocumentCreation` 阶段注入（见 `src/ui/web_shell.py`），
 * 且 **必须指定 MainWorld**（默认的 ApplicationWorld 会让本模块看不到 `QWebChannel`）。
 */

interface BridgeObject {
  request(id: string, service: string, method: string, paramsJson: string): void;
  responseReady: { connect(cb: (id: string, payload: string) => void): void };
  eventPushed: { connect(cb: (topic: string, payload: string) => void): void };
}

interface QWebChannelCtor {
  new (
    transport: unknown,
    callback: (channel: { objects: Record<string, unknown> }) => void,
  ): unknown;
}

declare global {
  interface Window {
    qt?: { webChannelTransport?: unknown };
    QWebChannel?: QWebChannelCtor;
  }
}

interface PendingCall {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  at: number;
}

let bridgeObject: BridgeObject | null = null;
let channelObjects: Record<string, unknown> = {};
let readyPromise: Promise<void> | null = null;
let seq = 0;

const pending = new Map<string, PendingCall>();
const eventListeners = new Map<string, Set<(payload: unknown) => void>>();

/** 桥是否已握手完成 */
export function isBridgeReady(): boolean {
  return bridgeObject !== null;
}

/**
 * 取一个 QWebChannel 对象（如 Python 注册的 `shell`）。
 *
 * 未握手完成时返回 null —— 调用方必须自己处理"桥还没好"的情况，
 * 不要假定它一定存在（`npm run dev` 在浏览器里打开时就没有）。
 */
export function getChannelObject<T>(name: string): T | null {
  return (channelObjects[name] as T | undefined) ?? null;
}

/**
 * 等待桥就绪（幂等，只握手一次）。
 *
 * :returns: 就绪后 resolve；页面未被 Qt 外壳承载时 reject
 */
export function whenBridgeReady(): Promise<void> {
  if (readyPromise) return readyPromise;

  readyPromise = new Promise<void>((resolve, reject) => {
    const boot = () => {
      const Ctor = window.QWebChannel;
      const transport = window.qt?.webChannelTransport;

      if (!Ctor || !transport) {
        reject(
          new Error(
            '未检测到 QWebChannel：页面可能是在普通浏览器中打开的。' +
              '桌面端必须由 Qt 外壳（src/main.py）承载。',
          ),
        );
        return;
      }

      new Ctor(transport, (channel) => {
        channelObjects = channel.objects;
        const bridge = channel.objects.bridge as BridgeObject | undefined;
        if (!bridge) {
          reject(new Error('QWebChannel 中没有名为 bridge 的对象'));
          return;
        }
        bridgeObject = bridge;

        bridge.responseReady.connect((id: string, payloadJson: string) => {
          const entry = pending.get(id);
          if (!entry) return;
          pending.delete(id);
          let parsed: { ok?: boolean; result?: unknown; error?: string };
          try {
            parsed = JSON.parse(payloadJson);
          } catch {
            entry.reject(new Error(`桥响应不是合法 JSON：${payloadJson}`));
            return;
          }
          if (parsed.ok) {
            entry.resolve(parsed.result);
          } else {
            entry.reject(new Error(parsed.error || '桥调用失败'));
          }
        });

        bridge.eventPushed.connect((topic: string, payloadJson: string) => {
          const handlers = eventListeners.get(topic);
          if (!handlers || handlers.size === 0) return;
          let payload: unknown;
          try {
            payload = JSON.parse(payloadJson);
          } catch {
            payload = payloadJson;
          }
          handlers.forEach((handler) => handler(payload));
        });

        resolve();
      });
    };

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', boot);
    } else {
      boot();
    }
  });

  return readyPromise;
}

/**
 * 调用一个白名单方法。
 *
 * @param service 服务名（Python 侧白名单一级键）
 * @param method 方法名
 * @param params 参数对象（会序列化成 JSON）
 * @returns Python 侧处理函数的返回值
 */
export async function callBridge<T>(
  service: string,
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  await whenBridgeReady();
  const bridge = bridgeObject;
  if (!bridge) throw new Error('桥未就绪');

  const requestId = `r${++seq}-${Date.now()}`;
  return new Promise<T>((resolve, reject) => {
    pending.set(requestId, {
      resolve: resolve as (value: unknown) => void,
      reject,
      at: performance.now(),
    });
    bridge.request(requestId, service, method, JSON.stringify(params));
  });
}

/**
 * 订阅 Python 主动推送的事件。
 *
 * @param topic 事件主题（如 `transfer.progress`）
 * @param handler 处理函数
 * @returns 取消订阅函数
 */
export function onBridgeEvent(
  topic: string,
  handler: (payload: unknown) => void,
): () => void {
  let handlers = eventListeners.get(topic);
  if (!handlers) {
    handlers = new Set();
    eventListeners.set(topic, handlers);
  }
  handlers.add(handler);
  return () => {
    handlers?.delete(handler);
  };
}

/**
 * 调用 `shell` 对象上的一个方法（窗口/托盘控制）。
 *
 * 注意：`shell` 是**另一个** QWebChannel 对象，不走 `bridge` 的白名单分发，
 * 因此不能复用 `callBridge`。这里只做「等桥就绪 → 取对象 → 调用」。
 *
 * @returns 是否成功调用（桥未就绪或方法不存在时返回 false）
 */
export async function callShell(method: string, ...args: unknown[]): Promise<boolean> {
  await whenBridgeReady();
  const shell = getChannelObject<Record<string, ((...a: unknown[]) => unknown) | undefined>>(
    'shell',
  );
  const fn = shell?.[method];
  if (typeof fn !== 'function') return false;
  fn(...args);
  return true;
}

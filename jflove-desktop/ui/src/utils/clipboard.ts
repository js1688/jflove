/**
 * 剪贴板（终端用）
 *
 * ## 为什么不能依赖 `navigator.clipboard`
 *
 * QtWebEngine（Chromium）里 `navigator.clipboard.readText()` 会**永远 pending**
 * （既不 resolve 也不 reject；v1.10.8 真机发现，v1.10.22 复测 3 秒仍挂着）。
 * 终端的复制/粘贴因此改走 Python 桥（GUI 线程读写 `QGuiApplication.clipboard()`）。
 *
 * ## 桌面端桥优先（v1.10.22）
 *
 * 早期实现是「先试浏览器 API、超时再回退桥」。在 QtWebEngine 下这意味着
 * **每次读剪贴板都要先白等满 500ms**（`CLIPBOARD_TIMEOUT_MS`）——用户感受到的
 * 就是"每次粘贴都卡半秒"。桌面端桥一定可用且是同步 IPC，所以在桥就绪时
 * **直接走桥**；浏览器 API 只作为「没有桥」（如 `npm run dev` 在浏览器里打开）
 * 时的兜底，并记忆"这台宿主上它是否可用"，避免反复踩同一个超时。
 */
import { callBridge, isBridgeReady } from './desktop-bridge';

/** 浏览器剪贴板 API 的等待上限（毫秒）：超过就认为平台不支持，走桥兜底 */
const CLIPBOARD_TIMEOUT_MS = 500;

/**
 * 「超时」与会给出真实值的哨兵。
 *
 * 必须和 `null`（读失败）/ `''`（剪贴板确实为空）区分开：只有**超时**
 * 才说明"这个宿主上浏览器剪贴板 API 不可用"，可以据此停止后续重试。
 */
const TIMEOUT = Symbol('clipboard-timeout');

/**
 * 给 Promise 加超时。
 *
 * @param task 待等待的 Promise
 * @param ms 上限毫秒数
 * @returns 任务结果；超时返回 `TIMEOUT`；任务抛错返回 `null`
 */
async function withTimeout<T>(task: Promise<T>, ms: number): Promise<T | null | typeof TIMEOUT> {
  let timer = 0;
  const timeout = new Promise<typeof TIMEOUT>((resolve) => {
    timer = window.setTimeout(() => resolve(TIMEOUT), ms);
  });
  try {
    return await Promise.race([task.catch(() => null), timeout]);
  } finally {
    if (timer) window.clearTimeout(timer);
  }
}

/**
 * 把文本交给 Python 侧写一次 Qt 剪贴板（失败静默：它只是"再加一道保险"）。
 *
 * 为什么要双写：Wayland 下浏览器路径的 Promise 可能在合成器拒绝后仍然 resolve，
 * 于是文本框其实没拿到内容，而"粘贴"（Qt 侧读）就是空的。两边都写一遍最稳，
 * 反正同一份文本写两次没有副作用。
 */
async function writeThroughBridge(text: string): Promise<boolean> {
  try {
    await callBridge('local_term', 'clipboard_write', { text });
    return true;
  } catch {
    return false;
  }
}

/**
 * 写文本到剪贴板。
 *
 * **优先走 Python 桥（Qt 原生剪贴板）**，浏览器 API 只作兜底：
 * Wayland 上同一个剪贴板会有两个数据源（Chromium 与 Qt）——如果先写浏览器、
 * 再写 Qt，Chromium 那条异步源落地时会把 Qt 的选区顶掉；窗口没焦点时合成器
 * 又拒绝它，最终变成"复制成功但剪贴板是空的"（v1.10.8 真机实测）。
 * 只留一个数据源就没有这个竞态。
 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  // 与 `readClipboardText` 同构：桥就绪 ⇒ 先且只用桥。
  // 旧实现"先写桥、失败再试浏览器 API"在 QtWebEngine 上留了一条必然白等
  // `CLIPBOARD_TIMEOUT_MS` 的死路径（该宿主的浏览器剪贴板 API 是永久 pending 的）。
  const bridgeReady = isBridgeReady();
  if (bridgeReady && (await writeThroughBridge(text))) return true;
  // 桥不可用（或罕见地写失败）才试浏览器 API；`=== true` 保证 TIMEOUT / null 继续兜底
  const api = navigator.clipboard?.writeText?.(text);
  if (api) {
    const result = await withTimeout(api.then(() => true), CLIPBOARD_TIMEOUT_MS);
    if (result === true) return true;
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', 'readonly');
    area.style.position = 'fixed';
    area.style.left = '-9999px';
    area.style.top = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

/**
 * 浏览器剪贴板 API 在当前宿主里是否可用（探测一次，之后不再尝试）。
 *
 * ## 为什么要记这个状态（v1.10.22）
 *
 * QtWebEngine 里 `navigator.clipboard.readText()` **既不 resolve 也不 reject**
 * （实测 3 秒仍挂着），所以每次 `readClipboardText()` 都要先白等满
 * `CLIPBOARD_TIMEOUT_MS`（500ms）才回退到 Python 桥 —— 用户感受到的就是
 * **每次粘贴都卡半秒**。既然它在这台机器上永远不可用，就没有必要每次都重试：
 * 探测到一次超时就置位，后续直接走桥（桥是同步 IPC，通常 <10ms）。
 *
 * 不能一上来就写死"永不使用浏览器 API"：普通浏览器里它是可用的，
 * 而本模块同时被桌面端与其它渲染环境复用。
 */
let browserClipboardUsable: boolean | null = null;

/**
 * 读剪贴板文本。
 *
 * ## 顺序（v1.10.22 调整）
 *
 * **桌面端（桥可用）优先走桥**，浏览器 API 只在没有桥时兜底。
 *
 * 理由：QtWebEngine 里 `navigator.clipboard.readText()` 是**死的**
 * （实测永久 pending），而桥是同步 IPC、通常 <10ms。旧实现"先试浏览器 API
 * 再回退"意味着**第一次粘贴必然白等 500ms**，用户感受就是"粘贴卡"。
 * 桥在桌面端永远可用；只有在普通浏览器里（`npm run dev`）才没有桥。
 */
export async function readClipboardText(): Promise<string> {
  if (isBridgeReady()) {
    try {
      const result = await callBridge<{ text?: string }>('local_term', 'clipboard_read', {});
      return result?.text ?? '';
    } catch {
      // 桥调用失败时再退回浏览器 API，不要直接返回空
    }
  }
  if (browserClipboardUsable !== false) {
    const api = navigator.clipboard?.readText?.();
    if (api) {
      const text = await withTimeout(api, CLIPBOARD_TIMEOUT_MS);
      if (text === TIMEOUT) {
        browserClipboardUsable = false;
      } else if (text !== null) {
        browserClipboardUsable = true;
        return text; // 包含 ''（剪贴板确实为空）：这是可信答案
      }
    } else {
      browserClipboardUsable = false;
    }
  }
  try {
    const result = await callBridge<{ text?: string }>('local_term', 'clipboard_read', {});
    return result?.text ?? '';
  } catch {
    return '';
  }
}

/** 仅供单测：重置"浏览器剪贴板是否可用"的探测结果 */
export function __resetClipboardProbeForTest(): void {
  browserClipboardUsable = null;
}

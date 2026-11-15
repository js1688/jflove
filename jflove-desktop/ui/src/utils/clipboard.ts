/**
 * 剪贴板（终端用）
 *
 * 为什么不是直接 `navigator.clipboard`：QtWebEngine（Chromium）里
 * `navigator.clipboard.readText()` 会**永远 pending**（既不 resolve 也不 reject，
 * 实测 v1.10.8 真机），终端里"粘贴没反应"就是它把整条链路挂住了。
 * 所以两条路都带超时：浏览器 API 先试，超时/失败立刻退回 Python 桥
 * （GUI 线程读 `QGuiApplication.clipboard()`）。
 */
import { callBridge } from './desktop-bridge';

/** 浏览器剪贴板 API 的等待上限（毫秒）：超过就认为平台不支持，走桥兜底 */
const CLIPBOARD_TIMEOUT_MS = 500;

/**
 * 给 Promise 加超时：超时返回 null（不抛错）。
 *
 * @param task 待等待的 Promise
 * @param ms 上限毫秒数
 */
async function withTimeout<T>(task: Promise<T>, ms: number): Promise<T | null> {
  let timer = 0;
  const timeout = new Promise<null>((resolve) => {
    timer = window.setTimeout(() => resolve(null), ms);
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
  if (await writeThroughBridge(text)) return true;
  const api = navigator.clipboard?.writeText?.(text);
  if (api) {
    const result = await withTimeout(api.then(() => true), CLIPBOARD_TIMEOUT_MS);
    if (result) return true;
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

/** 读剪贴板文本：浏览器 API（带超时）→ Python 桥兜底（始终返回字符串） */
export async function readClipboardText(): Promise<string> {
  const api = navigator.clipboard?.readText?.();
  if (api) {
    const text = await withTimeout(api, CLIPBOARD_TIMEOUT_MS);
    if (text) return text;
  }
  try {
    const result = await callBridge<{ text?: string }>('local_term', 'clipboard_read', {});
    return result?.text ?? '';
  } catch {
    return '';
  }
}

#!/usr/bin/env node
// Windows VM 内 jflove 桌面端的 DOM 级自动化：经 CDP 驱动 QtWebEngine 渲染的 React 界面。
//
// 为什么能这么干：桌面端界面是 Web UI（React）跑在 QtWebEngine（Chromium）里，
// 启动时给出 QTWEBENGINE_REMOTE_DEBUGGING 就会暴露 DevTools 端口，宿主机用 CDP 就能
// 点击 / 输入 / 断言 / 截图，和 Web 端同一套打法，但验证的是真实的 Windows 打包产物。
//
// 用法：
//   node scripts/verify/windows_vm_ui.mjs --host 192.168.122.50 --shot /tmp/jf-win/ui.png
//   node scripts/verify/windows_vm_ui.mjs --host 192.168.122.50 --wait-text '连接服务端'
//   node scripts/verify/windows_vm_ui.mjs --host 192.168.122.50 \
//        --fill 'input' 'http://192.168.122.1:8989' --click-text '连接服务端'
//   node scripts/verify/windows_vm_ui.mjs --host ... --eval "document.title"
//
// 依赖：Node 22+（内置 fetch / WebSocket），无第三方包。

import fs from 'node:fs';
import path from 'node:path';

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
const has = (name) => process.argv.includes(`--${name}`);

const host = arg('host', '127.0.0.1');
const port = Number(arg('port', '9222'));
const shot = arg('shot', '');
const waitText = arg('wait-text', '');
const clickText = arg('click-text', '');
const fillSel = arg('fill-sel', '');
const fillVal = arg('fill-val', '');
const evalJs = arg('eval', '');
const timeoutMs = Number(arg('timeout', '30000'));
const verbose = has('verbose');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 列出 CDP 目标；QtWebEngine 的 DevTools 端点与 Chromium 兼容。 */
async function targets() {
  const res = await fetch(`http://${host}:${port}/json/list`, { signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`/json/list HTTP ${res.status}`);
  return res.json();
}

/** 极简 CDP 客户端：只依赖全局 WebSocket。 */
class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.id = 0;
    this.pending = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', (e) => reject(new Error(`ws error: ${e.message || e.type}`)));
    });
    this.ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
      if (verbose && msg.method) console.error(`[cdp] ${msg.method}`);
    });
  }

  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`${method} 超时`));
        }
      }, timeoutMs);
    });
  }

  /** 在页面里跑 JS 并取回结果（JSON 反序列化）。 */
  async evaluate(expr) {
    const r = await this.send('Runtime.evaluate', {
      expression: expr,
      returnByValue: true,
      awaitPromise: true,
    });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text || 'JS 异常');
    return r.result.value;
  }

  close() { try { this.ws.close(); } catch { /* 忽略 */ } }
}

/** 轮询直到页面里能查到指定文本。 */
async function waitForText(cdp, text, ms) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    const body = await cdp.evaluate('document.body ? document.body.innerText : ""');
    if (body && body.includes(text)) return true;
    await sleep(500);
  }
  return false;
}

/** 找到包含指定文本的最小可点击元素，返回其中心坐标。 */
const CENTER_JS = (text) => `(() => {
  const t = ${JSON.stringify(text)};
  // 优先真实可点控件（button/a），避免点到标题栏等同样含该文字的容器
  const pick = (sel) => [...document.querySelectorAll(sel)].filter((e) =>
      ((e.innerText || e.value || '').trim().includes(t)) && e.getBoundingClientRect().width > 0);
  let hits = pick('button,a,[role=button]');
  if (!hits.length) hits = pick('input');
  if (!hits.length) hits = [...document.querySelectorAll('div,span,label,li')].filter((e) =>
      ((e.innerText || '').trim() === t) && e.getBoundingClientRect().width > 0);
  if (!hits.length) return null;
  hits.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
  const r = hits[0].getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, tag: hits[0].tagName };
})()`;

async function clickAt(cdp, x, y) {
  // 走真实输入事件（mousePressed/Released），而不是 element.click()，更接近人点
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
}

async function main() {
  const list = await targets();
  const page = list.find((t) => t.type === 'page') || list[0];
  if (!page || !page.webSocketDebuggerUrl) throw new Error('没有可用的 CDP page target');
  if (verbose) console.error(`[cdp] attach ${page.url || page.title}`);

  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.ready;
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable').catch(() => {});
  const out = {};

  try {
    if (waitText) {
      const ok = await waitForText(cdp, waitText, timeoutMs);
      out.waitText = ok;
      if (!ok) throw new Error(`等不到文本：${waitText}`);
    }
    if (fillSel) {
      const focused = await cdp.evaluate(`(() => {
        const el = document.querySelector(${JSON.stringify(fillSel)});
        if (!el) return false;
        el.focus(); el.select && el.select();
        return true;
      })()`);
      if (!focused) throw new Error(`找不到输入框：${fillSel}`);
      await cdp.send('Input.insertText', { text: fillVal });
      out.filled = [fillSel, fillVal];
    }
    if (clickText) {
      const pos = await cdp.evaluate(CENTER_JS(clickText));
      if (!pos) throw new Error(`找不到可点击文本：${clickText}`);
      await clickAt(cdp, pos.x, pos.y);
      out.clicked = clickText;
    }
    if (evalJs) out.eval = await cdp.evaluate(evalJs);
    if (shot) {
      fs.mkdirSync(path.dirname(shot), { recursive: true });
      const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(shot, Buffer.from(r.data, 'base64'));
      out.shot = shot;
      out.shotBytes = Buffer.from(r.data, 'base64').length;
    }
    console.log(JSON.stringify(out));
  } finally {
    cdp.close();
  }
}

main().catch((e) => {
  console.error(`[windows_vm_ui] 失败：${e.message}`);
  process.exit(1);
});

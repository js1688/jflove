#!/usr/bin/env node
// Web 端实机验证：Edge 无头 + CDP 点击/执行 JS + 截图。
//
// 用法：
//   node scripts/verify/web_shot.mjs
//   node scripts/verify/web_shot.mjs --url http://127.0.0.1:3000/ --out /tmp/web.png
//   node scripts/verify/web_shot.mjs --click-text '连接服务端'
//   node scripts/verify/web_shot.mjs --width 390 --height 844 --mobile
//   node scripts/verify/web_shot.mjs --eval "document.querySelector('input').value='admin'"
//
// 依赖：Microsoft Edge（本机 /usr/bin/microsoft-edge，Chromium 内核）。
// 不依赖 Playwright；需要更复杂的交互时再引入 playwright-core（channel: 'msedge'）。

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
const has = (name) => process.argv.includes(`--${name}`);

const url = arg('url', 'http://127.0.0.1:3000/');
const out = arg('out', '/tmp/jf-web.png');
const width = Number(arg('width', '1440'));
const height = Number(arg('height', '900'));
const waitMs = Number(arg('wait', '6000'));
const clickText = arg('click-text', '');
const evalJs = arg('eval', '');
const mobile = has('mobile');
const port = Number(arg('port', '9222'));

const edge = arg('edge', 'microsoft-edge');
const profile = `/tmp/jf-edge-${port}-${Date.now()}`;
fs.mkdirSync(path.dirname(out), { recursive: true });

const child = spawn(edge, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  `--window-size=${width},${height}`, 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function httpJson(p) {
  const res = await fetch(`http://127.0.0.1:${port}${p}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

let ws;
try {
  let version;
  for (let i = 0; i < 60; i++) {
    try { version = await httpJson('/json/version'); break; } catch { await sleep(200); }
  }
  if (!version) throw new Error('Edge CDP 未就绪');

  const targets = await httpJson('/json');
  const page = targets.find((t) => t.type === 'page');
  if (!page) throw new Error('没有可用的 page target');

  ws = new WebSocket(page.webSocketDebuggerUrl);
  let seq = 0;
  const pending = new Map();
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  });
  await new Promise((res, rej) => {
    ws.addEventListener('open', res);
    ws.addEventListener('error', rej);
  });
  const send = (method, params = {}) => new Promise((res) => {
    const id = ++seq;
    pending.set(id, res);
    ws.send(JSON.stringify({ id, method, params }));
  });

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width, height, deviceScaleFactor: 1, mobile,
  });
  await send('Page.navigate', { url });
  await sleep(waitMs);

  if (clickText) {
    const expr = `(() => {
      const t = ${JSON.stringify(clickText)};
      const nodes = [...document.querySelectorAll('button, a, [role="button"]')];
      const el = nodes.find((n) => (n.textContent || '').trim().includes(t));
      if (!el) return 'not-found';
      el.click();
      return 'clicked';
    })()`;
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    console.log(`click "${clickText}":`, r.result?.result?.value);
    await sleep(2500);
  }

  if (evalJs) {
    const r = await send('Runtime.evaluate', { expression: evalJs, returnByValue: true });
    console.log('eval:', JSON.stringify(r.result?.result?.value ?? r.result));
    await sleep(1000);
  }

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  if (!shot.result?.data) throw new Error('截图失败');
  fs.writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
  console.log(`saved: ${out}`);
  process.exitCode = 0;
} catch (err) {
  console.error('[verify] web_shot 失败：', err.message);
  process.exitCode = 1;
} finally {
  try { ws?.close(); } catch { /* ignore */ }
  try { child.kill('SIGKILL'); } catch { /* ignore */ }
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ }
}

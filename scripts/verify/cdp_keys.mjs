#!/usr/bin/env node
// 通过 CDP 向当前焦点（xterm 的隐藏 textarea）注入按键，用于桌面端终端交互验证。
// 用法：node scripts/verify/cdp_keys.mjs --host 127.0.0.1 --port 9222 --text 'echo OK'
const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
};
const host = arg('host', '127.0.0.1');
const port = arg('port', '9222');
const text = arg('text', 'echo JFLove_VERIFY');
const pages = await (await fetch(`http://${host}:${port}/json`)).json();
const page = pages.find((p) => p.type === 'page') || pages[0];
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (pending.has(m.id)) {
    pending.get(m.id)(m);
    pending.delete(m.id);
  }
};
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) =>
  new Promise((r) => {
    const i = ++id;
    pending.set(i, r);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
const evalJs = async (expression) =>
  (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result
    ?.result?.value;
await evalJs("document.querySelector('.xterm-helper-textarea')?.focus()");
for (const ch of text) {
  await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, key: ch, unmodifiedText: ch });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
}
await send('Input.dispatchKeyEvent', { type: 'keyDown', text: '\r', key: 'Enter', unmodifiedText: '\r' });
await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter' });
await new Promise((r) => setTimeout(r, 1500));
const rows = await evalJs(
  "Array.from(document.querySelectorAll('.xterm-rows > div')).map(d=>d.textContent).filter(Boolean).slice(-6).join('\\n')",
);
console.log(JSON.stringify({ rows: rows || '' }));
ws.close();

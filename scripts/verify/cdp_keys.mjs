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
// 只挑**可见**终端的 textarea（多标签时隐藏标签也在 DOM 里）
await evalJs(
  "(() => { const t=[...document.querySelectorAll('.xterm-helper-textarea')].filter((el)=>el.offsetParent!==null)[0]; t && t.focus(); })()",
);
// 按键必须走 rawKeyDown → char → keyUp：
// 直接给 keyDown 带 text 会让 Chromium 再往 textarea 插一次字符，xterm 收到**两遍**
// （v1.10.8 排查时实测：`echo hello` 被敲成 `echo helloecho hello`）。
const CODE_OF = (ch) => {
  if (/[a-z]/i.test(ch)) return 'Key' + ch.toUpperCase();
  if (/[0-9]/.test(ch)) return 'Digit' + ch;
  if (ch === ' ') return 'Space';
  if (ch === '-') return 'Minus';
  if (ch === '.') return 'Period';
  if (ch === '/') return 'Slash';
  if (ch === '_') return 'Minus';
  return '';
};
for (const ch of text) {
  const code = CODE_OF(ch);
  await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: ch, code });
  await send('Input.dispatchKeyEvent', { type: 'char', text: ch, unmodifiedText: ch, key: ch, code });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch, code });
}
await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
await send('Input.dispatchKeyEvent', { type: 'char', text: '\r', unmodifiedText: '\r', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
await new Promise((r) => setTimeout(r, 1500));
const rows = await evalJs(
  "Array.from(document.querySelectorAll('.xterm-rows > div')).map(d=>d.textContent).filter(Boolean).slice(-6).join('\\n')",
);
console.log(JSON.stringify({ rows: rows || '' }));
ws.close();

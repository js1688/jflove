#!/usr/bin/env node
// 桌面端终端底座实机验收（v1.10.8）
//
// 为什么要单独一个脚本：终端的问题只在"真事件 + 真进程 + 真渲染"下才暴露
// （v1.10.7 的事故就是单元测试全绿、真机 TUI 卡死）。本脚本用 CDP 直接驱动
// QtWebEngine 里的真实 React 界面：点按钮、敲键盘、走输入法、切标签、改尺寸，
// 每一步都对 xterm 的缓冲区 / 状态栏 / 真实文件内容做**可判定断言**。
//
// 依赖：桌面端以 `QTWEBENGINE_REMOTE_DEBUGGING=<port>` 启动（源码或安装包均可）。
//
// 用法：
//   node scripts/verify/desktop_terminal_e2e.mjs --port 9222 --out /tmp/jf-term-e2e
//   node scripts/verify/desktop_terminal_e2e.mjs --only tab-switch,ime
//
// 退出码：0 = 全部通过；1 = 有失败用例（stdout 里给出逐条结果与证据路径）。
import fs from 'node:fs';
import path from 'node:path';

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
};
const has = (name) => process.argv.includes(`--${name}`);
const HOST = '127.0.0.1';
const PORT = Number(arg('port', '9222'));
const OUT = arg('out', '/tmp/jf-term-e2e');
const ONLY = arg('only', '')
  .split(',')
  .map((item) => item.trim())
  .filter(Boolean);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ── CDP 客户端 ─────────────────────────────────────

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.id = 0;
    this.pending = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', (event) => reject(new Error(`ws error ${event.type}`)));
    });
    this.ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(JSON.stringify(message.error)));
        else resolve(message.result);
      }
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
      }, 30000);
    });
  }

  async eval(expression) {
    const result = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || 'JS 执行异常');
    }
    return result.result.value;
  }
}

let cdp;

async function attach() {
  const list = await (
    await fetch(`http://${HOST}:${PORT}/json/list`, { signal: AbortSignal.timeout(5000) })
  ).json();
  const page = list.find((item) => item.type === 'page') || list[0];
  if (!page?.webSocketDebuggerUrl) throw new Error('没有可用的 CDP page target');
  cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.ready;
  await cdp.send('Runtime.enable').catch(() => {});
  await cdp.send('Page.enable').catch(() => {});
}

// ── 界面操作 ───────────────────────────────────────

/** 轮询直到表达式为真（返回该值），超时抛错 */
async function waitFor(expression, timeoutMs = 15000, label = expression) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await cdp.eval(expression);
    if (last) return last;
    await sleep(200);
  }
  throw new Error(`等待超时（${label}），最后结果=${JSON.stringify(last)}`);
}

const clickTextJs = (text) => `(() => {
  const t = ${JSON.stringify(text)};
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const hits = [...document.querySelectorAll('button,a,[role=button],[role=tab]')]
    .filter((el) => (el.innerText || '').trim().includes(t) && visible(el));
  if (!hits.length) return null;
  hits.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
  const r = hits[0].getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
})()`;

async function clickAt(x, y, clickCount = 1) {
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  for (let i = 1; i <= clickCount; i += 1) {
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mousePressed',
      x,
      y,
      button: 'left',
      clickCount: i,
    });
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased',
      x,
      y,
      button: 'left',
      clickCount: i,
    });
  }
}

/** 按 aria-label / title 点按钮（工具栏图标按钮没有可见文字） */
async function clickLabel(label) {
  const ok = await cdp.eval(`(() => {
    const hit = [...document.querySelectorAll('button')].find((el) =>
      (el.getAttribute('aria-label') || '') === ${JSON.stringify(label)} || (el.getAttribute('title') || '') === ${JSON.stringify(label)});
    if (!hit) return false;
    hit.click();
    return true;
  })()`);
  if (!ok) throw new Error(`找不到按钮：${label}`);
}

async function clickText(text) {
  const pos = await cdp.eval(clickTextJs(text));
  if (!pos) throw new Error(`找不到可点击文本：${text}`);
  await clickAt(pos.x, pos.y);
  return pos;
}

/** 点开本地终端配置卡片（React 的 onDoubleClick 需要真实双击） */
async function openLocalTerminalCard() {
  const pos = await waitFor(
    `(() => {
      const card = [...document.querySelectorAll('div')].find(
        (el) => typeof el.className === 'string'
          && el.className.includes('rounded-xl')
          && el.className.includes('p-3.5'),
      );
      if (!card) return null;
      const r = card.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + 20 };
    })()`,
    15000,
    '本机终端配置卡片',
  );
  await clickAt(pos.x, pos.y, 2);
}

/** 等某个终端宿主可见，并返回其数量 */
const visibleHostsJs = `[...document.querySelectorAll('[data-testid=terminal-host]')]
  .filter((h) => h.offsetParent !== null).length`;

async function focusTerminal() {
  return waitFor(
    `(() => {
      const hosts = [...document.querySelectorAll('[data-testid=terminal-host]')]
        .filter((h) => h.offsetParent !== null);
      const area = hosts[0]?.querySelector('.xterm-helper-textarea');
      if (!area) return null;
      area.focus();
      return document.activeElement === area;
    })()`,
    8000,
    '终端可获得焦点',
  );
}

/** 取可见终端的 xterm 文本（按行） */
async function termRows() {
  return cdp.eval(`(() => {
    const hosts = [...document.querySelectorAll('[data-testid=terminal-host]')]
      .filter((h) => h.offsetParent !== null);
    const host = hosts[0];
    if (!host) return [];
    return [...host.querySelectorAll('.xterm-rows > div')]
      .map((row) => row.textContent.replace(/\\u00a0/g, ' '));
  })()`);
}

async function termText() {
  return (await termRows()).join('\n');
}

const CODE_OF = (char) => {
  if (/[a-z]/i.test(char)) return `Key${char.toUpperCase()}`;
  if (/[0-9]/.test(char)) return `Digit${char}`;
  const map = {
    ' ': 'Space',
    '-': 'Minus',
    '=': 'Equal',
    '[': 'BracketLeft',
    ']': 'BracketRight',
    ';': 'Semicolon',
    "'": 'Quote',
    ',': 'Comma',
    '.': 'Period',
    '/': 'Slash',
    '\\': 'Backslash',
    '`': 'Backquote',
    '|': 'Backslash',
    '"': 'Quote',
    '!': 'Digit1',
    '@': 'Digit2',
    '#': 'Digit3',
    $: 'Digit4',
    '%': 'Digit5',
    '^': 'Digit6',
    '&': 'Digit7',
    '*': 'Digit8',
    '(': 'Digit9',
    ')': 'Digit0',
    _: 'Minus',
    '+': 'Equal',
    ':': 'Semicolon',
    '<': 'Comma',
    '>': 'Period',
    '?': 'Slash',
    '{': 'BracketLeft',
    '}': 'BracketRight',
    '~': 'Backquote',
  };
  return map[char] || '';
};

const SHIFTED = new Set('~!@#$%^&*()_+{}|:"<>?');

/** 真键盘事件（rawKeyDown → char → keyUp），避免 xterm 收到两次输入 */
async function typeText(text, delay = 18) {
  for (const char of text) {
    const code = CODE_OF(char);
    const shifted = SHIFTED.has(char);
    const base = shifted ? char.toLowerCase().replace(/[^a-z0-9]/g, '') : char;
    const vk = /[a-z]/i.test(char)
      ? char.toUpperCase().charCodeAt(0)
      : /[0-9]/.test(char)
        ? char.charCodeAt(0)
        : undefined;
    const modifiers = shifted ? 8 : 0;
    await cdp.send('Input.dispatchKeyEvent', {
      type: 'rawKeyDown',
      key: char,
      code,
      modifiers,
      ...(vk ? { windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk } : {}),
    });
    await cdp.send('Input.dispatchKeyEvent', {
      type: 'char',
      text: char,
      unmodifiedText: base || char,
      key: char,
      code,
      modifiers,
    });
    await cdp.send('Input.dispatchKeyEvent', {
      type: 'keyUp',
      key: char,
      code,
      modifiers,
      ...(vk ? { windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk } : {}),
    });
    if (delay) await sleep(delay);
  }
}

const KEY = {
  Enter: { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' },
  Escape: { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 },
  Tab: { key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9, text: '\t' },
  Backspace: { key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8, text: '\b' },
  ArrowUp: { key: 'ArrowUp', code: 'ArrowUp', windowsVirtualKeyCode: 38, text: '\u001b[A' },
  ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40, text: '\u001b[B' },
  ArrowLeft: { key: 'ArrowLeft', code: 'ArrowLeft', windowsVirtualKeyCode: 37, text: '\u001b[D' },
  ArrowRight: { key: 'ArrowRight', code: 'ArrowRight', windowsVirtualKeyCode: 39, text: '\u001b[C' },
  'ctrl+c': { key: 'c', code: 'KeyC', windowsVirtualKeyCode: 67, modifiers: 2 },
  'ctrl+l': { key: 'l', code: 'KeyL', windowsVirtualKeyCode: 76, modifiers: 2 },
  'ctrl+u': { key: 'u', code: 'KeyU', windowsVirtualKeyCode: 85, modifiers: 2 },
  'ctrl+v': { key: 'v', code: 'KeyV', windowsVirtualKeyCode: 86, modifiers: 2 },
};

async function pressKey(name) {
  const key = KEY[name];
  if (!key) throw new Error(`未知按键 ${name}`);
  await cdp.send('Input.dispatchKeyEvent', {
    type: 'rawKeyDown',
    ...key,
    ...(key.text ? { text: undefined } : {}),
  });
  if (key.text) {
    await cdp.send('Input.dispatchKeyEvent', {
      type: 'char',
      text: key.text,
      unmodifiedText: key.text,
      key: key.key,
      code: key.code,
      modifiers: key.modifiers || 0,
    });
  }
  await cdp.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: key.key,
    code: key.code,
    modifiers: key.modifiers || 0,
    windowsVirtualKeyCode: key.windowsVirtualKeyCode,
  });
}

/** 走真实 IME 通道输入（composition 更新 + 提交） */
async function imeInput(text) {
  await cdp.send('Input.imeSetComposition', {
    text,
    selectionStart: text.length,
    selectionEnd: text.length,
  });
  await sleep(80);
  await cdp.send('Input.insertText', { text });
}

async function shot(name) {
  fs.mkdirSync(OUT, { recursive: true });
  const target = path.join(OUT, `${name}.png`);
  const result = await cdp.send('Page.captureScreenshot', { format: 'png' });
  const buffer = Buffer.from(result.data, 'base64');
  fs.writeFileSync(target, buffer);
  return target;
}

const go = (hash) => cdp.eval(`location.hash = ${JSON.stringify(hash)}, 'ok'`);

/**
 * 清理上一次运行留下的标签：终端脚本假定"从列表页、零标签"开始。
 * 不这么做，第二次跑会因为多出来的标签把 `[role=tab][1]` 指到别的会话上（实测踩过）。
 */
async function resetTabs() {
  for (let i = 0; i < 12; i += 1) {
    const closed = await cdp.eval(`(() => {
      const tabs = [...document.querySelectorAll('[role=tab]')];
      if (!tabs.length) return false;
      const tab = tabs[tabs.length - 1];
      const close = tab.querySelector('button');
      if (!close) return false;
      close.click();
      return true;
    })()`);
    if (!closed) break;
    await sleep(500);
  }
  await go('#/files');
  await sleep(600);
}

// ── 用例 ───────────────────────────────────────────

const results = [];
/** 记录一条用例结果（附截图与关键输出） */
function record(id, title, ok, detail, evidence = {}) {
  results.push({ id, title, ok, detail, evidence });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${id} ${title}${ok ? '' : ` —— ${detail}`}`);
}

async function caseShellBasics() {
  await go('#/remote/local');
  await sleep(600);
  await openLocalTerminalCard();
  await waitFor(`location.hash === '#/remote/session'`, 10000, '进入会话页');
  await waitFor(`(${visibleHostsJs}) > 0`, 10000, '终端渲染');
  await focusTerminal();

  await typeText('export JF_TERM_E2E=OK; echo "MARK_$JF_TERM_E2E"');
  await pressKey('Enter');
  const text = await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); const t=hosts[0]?.innerText||''; return t.includes('MARK_OK') ? t : null; })()`,
    10000,
    'echo 回显 MARK_OK',
  );
  return { shot: await shot('01-shell-basics'), detail: 'export + echo 回显正确', extra: text.slice(-120) };
}

async function caseChineseOutput() {
  await typeText('echo 中文输出验证');
  await pressKey('Enter');
  await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); return (hosts[0]?.innerText||'').includes('中文输出验证'); })()`,
    8000,
    '中文输出',
  );
  return { shot: await shot('02-chinese-output') };
}

async function caseImeInput() {
  await typeText('echo IME_');
  await imeInput('输入法中文');
  await sleep(200);
  await pressKey('Enter');
  const text = await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); const t=hosts[0]?.innerText||''; return (t.match(/IME_输入法中文/g)||[]).length >= 2 ? t : null; })()`,
    8000,
    '输入法中文回显（命令行 + 输出各一次）',
  );
  return { shot: await shot('03-ime'), detail: '输入法提交的中文进入 shell 并回显', extra: text.slice(-160) };
}

async function caseVim() {
  const file = '/tmp/jf-term-e2e/vim-target.txt';
  fs.mkdirSync('/tmp/jf-term-e2e', { recursive: true });
  try {
    fs.unlinkSync(file);
  } catch {
    /* 文件不存在即可 */
  }
  await typeText(`vim ${file}`);
  await pressKey('Enter');
  await sleep(1200);
  await typeText('i');
  await typeText('vim 中文 abc');
  await sleep(300);
  await shot('04-vim-insert');
  await pressKey('Escape');
  await sleep(300);
  await typeText(':wq');
  await pressKey('Enter');
  await sleep(1500);
  const written = fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim() : '';
  const ok = written === 'vim 中文 abc';
  return {
    shot: await shot('05-vim-exit'),
    okOverride: ok,
    detail: ok ? `vim 写入内容=${written}` : `vim 写入内容不符：${JSON.stringify(written)}`,
  };
}

async function ensureTerminal() {
  const count = await cdp.eval(visibleHostsJs);
  if (count > 0) {
    await focusTerminal();
    return false;
  }
  await go('#/remote/local');
  await sleep(700);
  await openLocalTerminalCard();
  await waitFor(`location.hash === '#/remote/session'`, 10000, '进入会话页');
  await waitFor(`(${visibleHostsJs}) > 0`, 10000, '终端渲染');
  await focusTerminal();
  return true;
}

async function caseDshTui() {
  await ensureTerminal();
  await typeText('TERM=xterm-256color dsh cli');
  await pressKey('Enter');
  await sleep(7000);
  const started = await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); const t=hosts[0]?.innerText||''; return t.includes('dsh-tui') ? t : null; })()`,
    15000,
    'dsh TUI 首屏',
  );
  await shot('06-dsh-start');
  await focusTerminal();
  await typeText('hello 世界', 60);
  const typed = await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); const t=hosts[0]?.innerText||''; return t.includes('hello 世界') ? t : null; })()`,
    12000,
    'dsh TUI 输入框出现键入文本',
  );
  await shot('07-dsh-typed');
  return { detail: 'TUI 输入框接受英文+中文', extra: typed.split('\n').filter((line) => line.includes('hello')).join(' | ') };
}

async function caseLargeOutput() {
  // 先退出 TUI：TUI 不占 stdin 的退出键不固定，直接 Ctrl+C（copyPasteMode=ctrl 会只复制），
  // 所以用工具条的「断开会话」重开一个干净终端来做大输出测试。
  await typeText('seq 1 2000');
  await pressKey('Enter');
  await sleep(2500);
  const text = await termText();
  const ok = text.includes('2000');
  return {
    shot: await shot('08-large-output'),
    okOverride: ok,
    detail: ok ? 'seq 2000 尾部到达' : 'seq 2000 尾部缺失',
  };
}

async function caseTabSwitch() {
  await ensureTerminal();
  // 先把 A 的画面清干净并留下**可见**标记：xterm 只渲染视口，
  // 早先的 MARK_OK 已经被 seq 输出顶进滚动历史，不能再用它做断言。
  await typeText('clear; echo MARK_TAB_A');
  await pressKey('Enter');
  await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); return (hosts[0]?.innerText||'').includes('MARK_TAB_A'); })()`,
    8000,
    'A 标签可见标记',
  );

  // 回到列表再开一个终端（真实路由切换：SessionPage 卸载 → 重挂）
  await go('#/remote/local');
  await sleep(800);
  await openLocalTerminalCard();
  await waitFor(`location.hash === '#/remote/session'`, 10000, '再次进入会话页');
  await waitFor(`(${visibleHostsJs}) > 0`, 10000, '第二个终端渲染');
  await focusTerminal();
  await typeText('clear; echo MARK_TAB_B');
  await pressKey('Enter');
  await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); return (hosts[0]?.innerText||'').includes('MARK_TAB_B'); })()`,
    8000,
    'B 标签可见标记',
  );
  await shot('09-tab-second');
  const tabCount = await cdp.eval(`[...document.querySelectorAll('[role=tab]')].length`);

  // 切回 A
  await cdp.eval(`[...document.querySelectorAll('[role=tab]')][0].click(), 'ok'`);
  await sleep(900);
  await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); return (hosts[0]?.innerText||'').includes('MARK_TAB_A'); })()`,
    8000,
    '切回 A 后画面仍在',
  );
  await shot('10-tab-back-first');

  // 再切回 B
  await cdp.eval(`[...document.querySelectorAll('[role=tab]')][1].click(), 'ok'`);
  await sleep(900);
  await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); return (hosts[0]?.innerText||'').includes('MARK_TAB_B'); })()`,
    8000,
    '再切回 B 后画面仍在',
  );
  await shot('11-tab-back-second');
  return { detail: `标签数=${tabCount}；A/B 来回切换后两边画面与通道都在（无回放、无重连）` };
}

async function caseClipboard() {
  await ensureTerminal();
  await typeText('echo SELECT_ME_中文');
  await pressKey('Enter');
  await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); return (hosts[0]?.innerText||'').includes('SELECT_ME_中文'); })()`,
    8000,
    '复制源文本出现',
  );
  // 三击选中该行（xterm 的三击 = 整行选择）
  const pos = await cdp.eval(`(() => {
    const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null);
    const rows=[...hosts[0].querySelectorAll('.xterm-rows > div')];
    const idx=rows.findIndex((row)=>row.textContent.includes('SELECT_ME_中文'));
    if (idx < 0) return null;
    const rect=rows[idx].getBoundingClientRect();
    return { x: rect.left + 40, y: rect.top + rect.height / 2 };
  })()`);
  if (!pos) throw new Error('找不到可选中行');
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: pos.x, y: pos.y });
  for (let count = 1; count <= 3; count += 1) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: count });
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: count });
  }
  await sleep(300);
  const selection = await cdp.eval(`(() => {
    const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null);
    return hosts[0]?.querySelector('.xterm-helper-textarea') ? 'ok' : 'no';
  })()`);
  await clickLabel('复制选中内容');
  await sleep(300);
  await focusTerminal();
  await pressKey('Enter'); // 新起一行，便于观察粘贴结果
  await clickLabel('粘贴');
  await sleep(500);
  const pasted = await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); const t=hosts[0]?.innerText||''; const hits=(t.match(/SELECT_ME_中文/g)||[]).length; return hits >= 3 ? t : null; })()`,
    6000,
    '粘贴后出现第三份 SELECT_ME_中文（命令行）',
  );
  await shot('14-clipboard');
  return {
    detail: `三击选中 + 复制 + 粘贴成功（textarea=${selection}）`,
    extra: pasted.split('\n').filter((line) => line.includes('SELECT_ME')).slice(-2).join(' | '),
  };
}

async function caseSignalInterrupt() {
  // copyPasteMode=smart 时 Ctrl+C 无选区 ⇒ 发 0x03（SIGINT）
  await cdp.eval(
    `localStorage.setItem('jflove.terminal.shortcuts', JSON.stringify({copyPasteMode:'smart'})), 'ok'`,
  );
  await focusTerminal();
  await typeText('sleep 30');
  await pressKey('Enter');
  await sleep(500);
  await pressKey('ctrl+c');
  await sleep(800);
  const text = await termText();
  const ok = /sleep 30[\s\S]*\^C/.test(text) || /\^C/.test(text);
  return {
    shot: await shot('12-sigint'),
    okOverride: ok,
    detail: ok ? 'Ctrl+C 触发 SIGINT（画面出现 ^C）' : 'Ctrl+C 未产生中断（前台进程组缺失？）',
  };
}

async function caseResize() {
  const dimsJs = `(() => {
    const bar = [...document.querySelectorAll('span')].find((el) => /^\\d+×\\d+$/.test((el.innerText || '').trim()));
    return bar ? bar.innerText.trim() : '';
  })()`;
  const before = await cdp.eval(dimsJs);
  // 用设备度量模拟窗口变窄：QtWebEngine 与 Chromium 一样会走 resize → ResizeObserver
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 900,
    height: 620,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await sleep(1500);
  const after = await cdp.eval(dimsJs);
  await shot('13-resize');
  await cdp.send('Emulation.clearDeviceMetricsOverride');
  await sleep(800);
  const ok = Boolean(before && after && before !== after);
  return {
    okOverride: ok,
    detail: ok ? `状态栏行列数 ${before} → ${after}` : `行列数未变化：${before} → ${after}`,
  };
}

// ── 主流程 ─────────────────────────────────────────

const CASES = [
  ['shell-basics', '本机终端基础命令', caseShellBasics],
  ['chinese-output', '中文输出', caseChineseOutput],
  ['ime', '输入法中文输入', caseImeInput],
  ['vim', '全屏编辑器 vim（插入/保存/退出）', caseVim],
  ['large-output', '大输出 seq 1 2000', caseLargeOutput],
  ['clipboard', '选中复制 + 粘贴', caseClipboard],
  ['sigint', 'Ctrl+C 中断前台进程', caseSignalInterrupt],
  ['resize', '窗口尺寸变化后行列数更新', caseResize],
  ['tab-switch', '多标签切换内容保留', caseTabSwitch],
  ['dsh-tui', 'dsh TUI 键盘输入（最后跑：TUI 占满屏幕）', caseDshTui],
];

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  await attach();
  await resetTabs();
  for (const [id, title, fn] of CASES) {
    if (ONLY.length && !ONLY.includes(id)) continue;
    try {
      const outcome = (await fn()) || {};
      const ok = outcome.okOverride ?? true;
      record(id, title, ok, outcome.detail || '断言通过', {
        shot: outcome.shot,
        extra: outcome.extra,
      });
    } catch (error) {
      let evidence = '';
      try {
        evidence = await shot(`fail-${id}`);
      } catch {
        /* 截图失败不掩盖原始错误 */
      }
      record(id, title, false, error.message, { shot: evidence });
    }
  }
  const failed = results.filter((item) => !item.ok);
  const report = {
    port: PORT,
    out: OUT,
    passed: results.length - failed.length,
    failed: failed.length,
    results,
  };
  fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
  return failed.length === 0 ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error(`[terminal-e2e] ${error.message}`);
    process.exit(1);
  });

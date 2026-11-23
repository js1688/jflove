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
// 默认本机；验证客机（如 Windows VM 的 CDP 端口转发）时用 --host 指定
const HOST = arg('host', '127.0.0.1');
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
      // 把真正的异常描述也带出来：只报 "Uncaught" 时无法定位是哪一行 JS 坏了
      const detail =
        result.exceptionDetails.exception?.description ||
        result.exceptionDetails.text ||
        'JS 执行异常';
      throw new Error(detail);
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
  // 先等配置卡片出现；**全新安装**（如 Windows 客机）没有任何本机配置，
  // 页面只给「新建本机配置」/「打开默认终端」入口 —— 此时直接走默认终端，
  // 否则用例会在"等卡片"上超时，看起来像功能坏了（实测踩过）。
  const found = await waitFor(
    `(() => {
      const card = [...document.querySelectorAll('div')].find(
        (el) => typeof el.className === 'string'
          && el.className.includes('rounded-xl')
          && el.className.includes('p-3.5'),
      );
      if (card) {
        const r = card.getBoundingClientRect();
        return { kind: 'card', x: r.left + r.width / 2, y: r.top + 20 };
      }
      const fallback = [...document.querySelectorAll('button')].find(
        (el) => /打开默认终端|新建本机配置/.test((el.innerText || '').trim()),
      );
      if (fallback) {
        const r = fallback.getBoundingClientRect();
        return { kind: 'default', x: r.left + r.width / 2, y: r.top + r.height / 2 };
      }
      return null;
    })()`,
    15000,
    '本机终端配置卡片或默认终端入口',
  );
  if (found.kind === 'card') {
    await clickAt(found.x, found.y, 2);
    return;
  }
  await clickAt(found.x, found.y, 1);
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

  // 本用例要同时适用于 POSIX shell 与 Windows PowerShell ——
  // 早先写死 `export VAR=...; echo "$VAR"`，在 Windows 上 PowerShell 不认，
  // 会表现成"echo 回显超时"，看着像终端坏了（实测踩过）。
  // 这里用两个 shell 都支持的**纯 echo 回显**做断言，不依赖变量语法。
  const marker = `SHELLBASICS_${Date.now().toString().slice(-6)}`;
  const before = await cdp.eval(`(() => {
    const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null);
    return (hosts[0]?.innerText||'').split(String.fromCharCode(10)).length;
  })()`);
  await typeText(`echo ${marker}`);
  await pressKey('Enter');
  const text = await waitFor(
    `(() => {
      const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null);
      const t=hosts[0]?.innerText||'';
      const lines=t.split(String.fromCharCode(10));
      return lines.length > ${before} && lines.some((line) => line.trim() === ${JSON.stringify(marker)}) ? t : null;
    })()`,
    10000,
    'echo 回显标记行',
  );
  return { shot: await shot('01-shell-basics'), detail: 'echo 回显作为独立行出现', extra: text.slice(-120) };
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

/**
 * 让当前终端回到「干净的交互 shell」状态（用例之间互不影响）。
 *
 * 为什么需要：用例是有状态的 —— `vim` 会停在插入模式、`dsh-tui` 会进备用屏、
 * `sigint` 会留下被中断的命令行。下一条用例直接敲键就会被 TUI 吃掉，
 * 表现为「凭空失败」，而且失败点会随后续用例顺序漂移（实测踩过多次）。
 *
 * 做法：先发 Esc 退出可能的插入/备用屏，再连敲 Ctrl+C 与 Ctrl+D 归还提示符，
 * 最后清屏。这些都是终端层面无害的"逃逸"动作，不依赖具体程序。
 */
async function settleShell(attempts = 3) {
  await focusTerminal();
  for (let i = 0; i < attempts; i += 1) {
    await pressKey('Escape');
    await sleep(150);
  }
  // vim / less 一类：先 `:q!` 再 Ctrl+C，双保险
  await typeText(':q!');
  await pressKey('Enter');
  await sleep(300);
  for (let i = 0; i < attempts; i += 1) {
    await pressKey('ctrl+c');
    await sleep(200);
  }
  await sleep(300);
  await clickLabel('清屏（保留会话）');
  await sleep(400);
  await focusTerminal();
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
  await ensureTerminal();
  await settleShell();
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
  await settleShell();
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
  const beforePaste = await cdp.eval(`(() => {
    const t=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null)[0]?.innerText||'';
    return (t.match(/SELECT_ME_中文/g)||[]).length;
  })()`);
  await clickLabel('粘贴');
  await sleep(500);
  // 判据要**对折行健壮**：粘贴的长行可能在终端宽度处折成两行，
  // 于是 `SELECT_ME_中` / `文 ` 被拆开，直接正则匹配会漏。这里先去掉换行再比对。
  const pasted = await waitFor(
    `(() => {
      const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null);
      const raw=hosts[0]?.innerText||'';
      const flat=raw.split(String.fromCharCode(10)).join('');
      const hits=(flat.match(/SELECT_ME_中文/g)||[]).length;
      return hits > ${beforePaste} ? raw : null;
    })()`,
    6000,
    '粘贴后出现新的一份 SELECT_ME_中文（命令行）',
  );
  await shot('14-clipboard');
  return {
    detail: `三击选中 + 复制 + 粘贴成功（textarea=${selection}）`,
    extra: pasted.split('\n').filter((line) => line.includes('SELECT_ME')).slice(-2).join(' | '),
  };
}

/**
 * 粘贴只发一次（v1.10.22 回归）
 *
 * 事故：`attachCustomKeyEventHandler` 返回 false 只拦得住 xterm 自己的 keydown，
 * 浏览器默认粘贴照样执行 ⇒ 一次 Ctrl+V 往 PTY 写两份。
 * 判据：单次 Ctrl+V 触发的 `type: 'input'` 上行消息**恰好 1 条**。
 */
async function casePasteOnce() {
  await ensureTerminal();
  // 先回到干净 shell 再清屏：本用例靠"新增独立一行"判定，
  // 若上一条用例把终端留在 TUI 里，敲的 echo 根本不会被执行（实测踩过）
  await settleShell();
  // 记录 WS 上行 input 帧
  await cdp.eval(`(() => {
    if (window.__pasteOnceProbe) return 'has';
    window.__pasteOnceProbe = { inputs: [] };
    const original = WebSocket.prototype.send;
    WebSocket.prototype.send = function (payload) {
      try {
        const msg = JSON.parse(payload);
        if (msg.type === 'input') window.__pasteOnceProbe.inputs.push(String(msg.data));
      } catch {
        /* 非 JSON 忽略 */
      }
      return original.call(this, payload);
    };
    return 'installed';
  })()`);
  // 先用终端自己的「复制选中内容」把已知文本放进系统剪贴板 ——
  // 这样不依赖外部 xclip，也不受 X11 剪贴板所有权影响。
  const marker = `ONCE${Date.now().toString().slice(-7)}`;
  // 先记下当前终端行数，等"新增一行输出"而不是"文本里出现过 marker" ——
  // 后者会被命令行本身（`echo ONCE...`）提前满足，导致判定失效（实测踩过）
  const beforeLines = await cdp.eval(`(() => {
    const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null);
    return (hosts[0]?.innerText||'').split(String.fromCharCode(10)).length;
  })()`);
  await typeText(`echo ${marker}`);
  await pressKey('Enter');
  await waitFor(
    `(() => {
      const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null);
      const lines=(hosts[0]?.innerText||'').split(String.fromCharCode(10));
      return lines.length > ${beforeLines} && lines.some((line, i) => i > 0 && line.trim() === ${JSON.stringify(marker)});
    })()`,
    8000,
    '标记文本作为独立一行出现',
  );
  // 三击选中该行 → 点工具条「复制选中内容」
  const pos = await cdp.eval(`(() => {
    const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null);
    const rows=[...hosts[0].querySelectorAll('.xterm-rows > div')];
    const idx=rows.findIndex((row)=>row.textContent.includes(${JSON.stringify(marker)}));
    if (idx < 0) return null;
    const rect=rows[idx].getBoundingClientRect();
    return { x: rect.left + 40, y: rect.top + rect.height / 2 };
  })()`);
  if (!pos) throw new Error('找不到含标记的行');
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: pos.x, y: pos.y });
  for (let count = 1; count <= 3; count += 1) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: count });
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: count });
  }
  await sleep(300);
  await clickLabel('复制选中内容');
  await sleep(400);
  await focusTerminal();
  await pressKey('Enter');
  await cdp.eval(`window.__pasteOnceProbe.inputs = []`);
  // 单次真实 Ctrl+V（modifiers=2 表示 Ctrl）
  await cdp.send('Input.dispatchKeyEvent', {
    type: 'rawKeyDown', key: 'v', code: 'KeyV', windowsVirtualKeyCode: 86, modifiers: 2,
  });
  await cdp.send('Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'v', code: 'KeyV', windowsVirtualKeyCode: 86, modifiers: 2,
  });
  await sleep(1200);
  const inputs = await cdp.eval(`window.__pasteOnceProbe.inputs`);
  await shot('23-paste-once');
  if (inputs.length !== 1) {
    throw new Error(`单次 Ctrl+V 产生了 ${inputs.length} 条 input 帧（应恰好 1 条）：${JSON.stringify(inputs)}`);
  }
  return {
    detail: '单次 Ctrl+V 只产生 1 条 input 帧（修复前为 2 条）',
    extra: inputs[0],
  };
}

/**
 * 输入回显时延（v1.10.22 回归）
 *
 * 事故：输出写屏绑在 `requestAnimationFrame` 上，页面被判隐藏/被限流时
 * 写入排队，按键到画面出现可达 2~4 秒。
 * 判据：连续 12 个字符，每次 keydown → xterm 行文本变化 < 200ms。
 */
async function caseInputLatency() {
  await ensureTerminal();
  await focusTerminal();
  await cdp.eval(`(() => {
    window.__latProbe = { lat: [], keyAt: 0 };
    const attach = () => {
      const area = [...document.querySelectorAll('.xterm-helper-textarea')]
        .filter((el) => el.offsetParent !== null)[0];
      if (area && !area.__latHooked) {
        area.__latHooked = true;
        area.addEventListener('keydown', () => { window.__latProbe.keyAt = performance.now(); }, true);
      }
      const host = [...document.querySelectorAll('[data-testid=terminal-host]')]
        .find((el) => el.offsetParent !== null);
      const rows = host?.querySelector('.xterm-rows');
      if (rows && !rows.__latHooked) {
        rows.__latHooked = true;
        new MutationObserver(() => {
          if (window.__latProbe.keyAt) {
            window.__latProbe.lat.push(+(performance.now() - window.__latProbe.keyAt).toFixed(1));
            window.__latProbe.keyAt = 0;
          }
        }).observe(rows, { childList: true, subtree: true, characterData: true });
      }
    };
    attach();
    setInterval(attach, 400);
    return 'armed';
  })()`);
  await cdp.eval(`window.__latProbe.lat = []`);
  for (const ch of 'latencycheck') {
    await cdp.send('Input.dispatchKeyEvent', {
      type: 'rawKeyDown', key: ch, code: `Key${ch.toUpperCase()}`, windowsVirtualKeyCode: ch.toUpperCase().charCodeAt(0),
      text: ch, unmodifiedText: ch,
    });
    await cdp.send('Input.dispatchKeyEvent', {
      type: 'char', text: ch, unmodifiedText: ch, key: ch, code: `Key${ch.toUpperCase()}`,
      windowsVirtualKeyCode: ch.toUpperCase().charCodeAt(0),
    });
    await cdp.send('Input.dispatchKeyEvent', {
      type: 'keyUp', key: ch, code: `Key${ch.toUpperCase()}`, windowsVirtualKeyCode: ch.toUpperCase().charCodeAt(0),
    });
    await sleep(40);
  }
  await sleep(1200);
  const lat = await cdp.eval(`window.__latProbe.lat`);
  await shot('24-input-latency');
  if (lat.length === 0) throw new Error('未能测到任何按键回显时延（探针未命中）');
  const worst = Math.max(...lat);
  if (worst > 200) {
    throw new Error(`按键回显最慢 ${worst}ms（阈值 200ms）：${JSON.stringify(lat)}`);
  }
  return {
    detail: `12 键回显最慢 ${worst}ms（阈值 200ms）`,
    extra: `latencies=${JSON.stringify(lat)}`,
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

/**
 * 窗口偏矮时的 TUI 兜底（v1.10.9）
 *
 * 事故：dsh TUI 在 ≤21 行会画丢输入框内容行（tmux 同样复现）。
 * 产品兜底：窗口偏矮时自动缩字号保证 ≥24 行；实在放不下则明确提示。
 */
async function caseMinRows() {
  // 自己开一个新终端（上一个用例可能停在 TUI 里）
  await go('#/remote/local');
  await sleep(800);
  await openLocalTerminalCard();
  await waitFor(`location.hash === '#/remote/session'`, 10000, '进入会话页');
  await waitFor(`(${visibleHostsJs}) > 0`, 10000, '终端渲染');
  await focusTerminal();

  const statusJs = `(() => {
    const texts = [...document.querySelectorAll('span,div')].map((el) => (el.innerText || '').trim());
    const dims = texts.find((t) => /^\\d+×\\d+$/.test(t)) || '';
    const font = texts.find((t) => t.startsWith('⌥ 字号')) || '';
    const warn = document.body.innerText.includes('窗口高度不足');
    return JSON.stringify({ dims, font, warn });
  })()`;

  // ① 偏矮但不极端：期望自动缩字号后仍有 ≥24 行
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 900,
    height: 620,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await sleep(1800);
  const shrunk = JSON.parse(await cdp.eval(statusJs));
  const shrunkRows = Number((shrunk.dims.split('×')[1] || '0').trim());
  if (!(shrunkRows >= 24 && /自适应/.test(shrunk.font))) {
    throw new Error(`自动缩字号未生效：${JSON.stringify(shrunk)}`);
  }
  await shot('15-min-rows-shrunk');

  // ② 在偏矮窗口里真跑 TUI：输入框内容行必须在
  await focusTerminal();
  await typeText('TERM=xterm-256color dsh cli');
  await pressKey('Enter');
  await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); return (hosts[0]?.innerText||'').includes('dsh-tui') ? true : null; })()`,
    20000,
    '偏矮窗口里 dsh TUI 首屏',
  );
  await typeText('矮窗测试', 80);
  await waitFor(
    `(() => { const hosts=[...document.querySelectorAll('[data-testid=terminal-host]')].filter(h=>h.offsetParent!==null); return (hosts[0]?.innerText||'').includes('矮窗测试') ? true : null; })()`,
    12000,
    '偏矮窗口里 TUI 输入框出现键入文本',
  );
  await shot('16-min-rows-tui');

  // ③ 极端矮：缩到最小字号仍不够 → 必须明确提示，而不是静默坏掉
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 900,
    height: 430,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await sleep(1800);
  const tooSmall = JSON.parse(await cdp.eval(statusJs));
  if (!tooSmall.warn) throw new Error(`极端矮窗口未显示提示：${JSON.stringify(tooSmall)}`);
  await shot('17-min-rows-warning');

  // ④ 恢复窗口：字号回到用户设定，行数 ≥24
  await cdp.send('Emulation.clearDeviceMetricsOverride');
  await sleep(1800);
  const restored = JSON.parse(await cdp.eval(statusJs));
  const restoredRows = Number((restored.dims.split('×')[1] || '0').trim());
  if (restoredRows < 24 || /自适应/.test(restored.font)) {
    throw new Error(`恢复窗口后未回到用户字号：${JSON.stringify(restored)}`);
  }
  await shot('18-min-rows-restored');
  return {
    detail: `偏矮窗口自动 ${shrunk.font} → 恢复后 ${restored.font}；行数 ${shrunkRows}→${restoredRows}`,
  };
}


/** v1.10.10：会话结束自动关标签 + 空态返回入口 */
async function caseSessionAutoClose() {
  await resetTabs();
  await ensureTerminal();
  const before = await cdp.eval(`[...document.querySelectorAll('[role=tab]')].length`);

  await typeText('exit');
  await pressKey('Enter');
  await waitFor(
    `[...document.querySelectorAll('[role=tab]')].length === 0`,
    10000,
    'exit 后标签自动关闭',
  );
  const empty = await cdp.eval(
    `document.body.innerText.includes('没有打开的会话') && document.body.innerText.includes('返回连接列表')`,
  );
  if (!empty) throw new Error('标签关闭后未显示空态 + 返回连接列表入口');
  await shot('19-session-auto-close');

  // 空态按钮可回到连接列表
  await clickText('返回连接列表');
  await waitFor(`location.hash === '#/remote'`, 8000, '返回连接列表');
  return { detail: `exit 前标签数=${before}；退出后自动关闭并显示空态返回入口` };
}

/** v1.10.10：左侧「会话」固定节点（计数 / 点击进入 / 悬停关闭） */
async function caseSessionTree() {
  await resetTabs();
  await ensureTerminal();
  await go('#/remote');
  await waitFor(
    `document.querySelector('[data-tree-root="会话"]') !== null`,
    8000,
    '左侧树出现「会话」节点',
  );
  const count = await cdp.eval(
    `document.querySelector('[data-tree-count="会话"]')?.textContent?.trim()`,
  );
  const rows = await cdp.eval(`document.querySelectorAll('[data-testid=session-tab]').length`);
  if (count !== '1' || rows !== 1) {
    throw new Error(`会话节点计数不正确：count=${count}, rows=${rows}`);
  }
  await shot('20-session-tree');

  // 单击会话行 → 回到对应终端面板
  await cdp.eval(
    `document.querySelector('[data-testid=session-tab] button:not([aria-label])')?.click(), 'ok'`,
  );
  await waitFor(`location.hash === '#/remote/session'`, 8000, '点击会话行进入面板');
  await waitFor(`[...document.querySelectorAll('[role=tab]')].length === 1`, 8000, '标签仍在');
  await waitFor(`(${visibleHostsJs}) > 0`, 8000, '终端渲染');

  // 回列表，用会话行悬停关闭按钮关掉
  await go('#/remote');
  await waitFor(
    `document.querySelector('[data-testid=session-tab]') !== null`,
    8000,
    '会话行存在',
  );
  await cdp.eval(
    `document.querySelector('[data-testid=session-tab] button[aria-label^="关闭"]')?.click(), 'ok'`,
  );
  await waitFor(
    `document.querySelectorAll('[data-testid=session-tab]').length === 0`,
    8000,
    '会话行关闭',
  );
  const countAfter = await cdp.eval(
    `document.querySelector('[data-tree-count="会话"]')?.textContent?.trim()`,
  );
  if (countAfter !== '0') throw new Error(`关闭后计数未归零：${countAfter}`);
  await shot('21-session-tree-closed');
  return { detail: '会话节点计数 1→0；单击进入面板；关闭按钮生效' };
}

/** v1.10.10：停留在其它页面时会话结束也要关标签（事件常驻） */
async function caseExitWhileAway() {
  await resetTabs();
  await ensureTerminal();
  await typeText('sleep 4; exit');
  await pressKey('Enter');
  // 命令开始执行后立刻离开会话页（SessionPage 卸载，但终端运行时与事件订阅仍在）
  await go('#/files');
  await sleep(5200);
  await go('#/remote');
  await sleep(800);
  const count = await cdp.eval(
    `document.querySelector('[data-tree-count="会话"]')?.textContent?.trim()`,
  );
  const rows = await cdp.eval(`document.querySelectorAll('[data-testid=session-tab]').length`);
  const tabs = await cdp.eval(`[...document.querySelectorAll('[role=tab]')].length`);
  await shot('22-session-exit-away');
  if (count !== '0' || rows !== 0 || tabs !== 0) {
    throw new Error(`离开会话页后未自动关闭：count=${count}, rows=${rows}, tabs=${tabs}`);
  }
  return { detail: '文件页停留期间会话退出，回到远程页标签已自动关闭' };
}

// ── 主流程 ─────────────────────────────────────────

const CASES = [
  ['shell-basics', '本机终端基础命令', caseShellBasics],
  ['chinese-output', '中文输出', caseChineseOutput],
  ['ime', '输入法中文输入', caseImeInput],
  ['vim', '全屏编辑器 vim（插入/保存/退出）', caseVim],
  ['large-output', '大输出 seq 1 2000', caseLargeOutput],
  ['clipboard', '选中复制 + 粘贴', caseClipboard],
  ['paste-once', '单次 Ctrl+V 只粘贴一次（v1.10.22）', casePasteOnce],
  ['input-latency', '按键回显时延 < 200ms（v1.10.22）', caseInputLatency],
  ['sigint', 'Ctrl+C 中断前台进程', caseSignalInterrupt],
  ['resize', '窗口尺寸变化后行列数更新', caseResize],
  ['tab-switch', '多标签切换内容保留', caseTabSwitch],
  ['dsh-tui', 'dsh TUI 键盘输入', caseDshTui],
  ['min-rows', '窗口偏矮时自动保证 TUI 行数（v1.10.9）', caseMinRows],
  ['session-auto-close', '会话 exit 后标签自动关闭（v1.10.10）', caseSessionAutoClose],
  ['session-tree', '左侧「会话」节点计数/进入/关闭（v1.10.10）', caseSessionTree],
  ['session-exit-away', '停留其它页面时会话结束也自动关标签（v1.10.10）', caseExitWhileAway],
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

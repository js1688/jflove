#!/usr/bin/env node
// 桌面端 SFTP 面板「就绪后自动出目录」实机回归（v1.10.12）
//
// ## 这条脚本守的是什么
//
// 用户实测（2026-11-17）：SFTP 连上后标签已是绿点「已连接」，文件面板却常驻红字
// 「会话尚未建立」，目录不出来 —— 点「刷新」才有内容。
// 根因：标签在 `session_open` 返回时立刻建好并挂载 `SftpView`，那一刻 Python 侧还在后台
// 建链；旧实现挂载即 `sftp_list`，失败后**不重试**。
//
// 因此本脚本的判据**不看"最终有没有目录"，而是看时序**：
//   1. 会话在 `connecting` 期间**不得**出现「会话尚未建立」这类目录错误；
//   2. 全程**不点「刷新」**；
//   3. 就绪后目录**自动**出现（出现文件行的「改名/删除」动作即算）。
//
// ## 前置（脚本不自己准备环境）
//
// 1. 后端在跑，且目标连接已存在（源码运行即可）：
//      cd jflove-desktop && QTWEBENGINE_REMOTE_DEBUGGING=9222 QT_QPA_PLATFORM=offscreen \
//        venv-linux/bin/python src/main.py
// 2. 已登录，且「远程连接」页能看到名为 `--item` 的连接。
// 3. 想稳定观察 `connecting` 阶段：把该连接的 `host_key_fingerprint` 清空，
//    这样会话会停在「确认主机指纹」等待（本脚本会自动点「接受」）。
//
// 用法：
//   node scripts/verify/desktop_sftp_gate_e2e.mjs --port 9222 --item '本机测试SFTP'
//   node scripts/verify/desktop_sftp_gate_e2e.mjs --port 9222 --item X --out /tmp/jf-sftp-gate
//
// 退出码：0 = 通过；1 = 判据失败（stdout 给出逐条结果与证据路径）。
import fs from 'node:fs';
import path from 'node:path';

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
};
const HOST = arg('host', '127.0.0.1');
const PORT = Number(arg('port', '9222'));
const ITEM = arg('item', '');
const OUT = arg('out', '/tmp/jf-sftp-gate');
/** 等目录自动出现的最长时间（毫秒）；主机指纹确认 + 建链要留足余量 */
const TIMEOUT = Number(arg('timeout', '60000'));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

if (!ITEM) {
  console.error('缺少 --item（远程连接名称）');
  process.exit(2);
}

/** 极简 CDP 客户端（与 scripts/verify/windows_vm_ui.mjs 同一套打法） */
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

  async evaluate(expression) {
    const r = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text || 'JS 异常');
    return r.result.value;
  }

  close() {
    try {
      this.ws.close();
    } catch {
      /* 忽略 */
    }
  }
}

/** 找到文本对应的可点控件中心并走真实鼠标事件（element.click() 对 React 不稳） */
async function clickText(cdp, text, count = 1) {
  const center = await cdp.evaluate(`(() => {
    const t = ${JSON.stringify(text)};
    const hits = [...document.querySelectorAll('button,a,[role=button]')].filter((e) =>
      ((e.innerText || e.value || '').trim().includes(t)) && e.getBoundingClientRect().width > 0);
    if (!hits.length) return null;
    hits.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
    const r = hits[0].getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  })()`);
  if (!center) return false;
  await clickAt(cdp, center.x, center.y, count);
  return true;
}

/** 双击指定 title 含「名称 · 主机」的连接行 */
async function doubleClickItem(cdp, name) {
  const center = await cdp.evaluate(`(() => {
    const n = ${JSON.stringify(name)};
    const el = [...document.querySelectorAll('[title]')].find((e) =>
      (e.title || '').includes('·') && (e.innerText || '').includes(n)
      && e.getBoundingClientRect().width > 0);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  })()`);
  if (!center) return false;
  await clickAt(cdp, center.x, center.y, 2);
  return true;
}

async function clickAt(cdp, x, y, count = 1) {
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  for (let i = 1; i <= count; i += 1) {
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

const bodyText = (cdp) => cdp.evaluate('document.body ? document.body.innerText : ""');

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const list = await (
    await fetch(`http://${HOST}:${PORT}/json/list`, { signal: AbortSignal.timeout(5000) })
  ).json();
  const page = list.find((t) => t.type === 'page') || list[0];
  if (!page?.webSocketDebuggerUrl) throw new Error('没有可用的 CDP page target');

  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.ready;
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable').catch(() => {});

  const evidence = { item: ITEM, connectingSeen: false, badErrorSeen: false, timeline: [] };

  // ① 回到连接列表（已有标签时先断开，保证从"冷启动"开始观察）
  let body = await bodyText(cdp);
  if (body.includes('断开')) {
    await clickText(cdp, '断开');
    await sleep(1500);
    body = await bodyText(cdp);
  }
  if (body.includes('返回连接列表')) {
    await clickText(cdp, '返回连接列表');
    await sleep(1200);
  }
  if (!(await doubleClickItem(cdp, ITEM))) {
    throw new Error(`连接列表里找不到「${ITEM}」（确认已登录且已进「远程连接」页）`);
  }
  const openedAt = Date.now();
  evidence.timeline.push([0, 'double-click 打开会话']);

  // ② 每 100ms 采样：记录是否出现「连接中」占位、是否出现过目录错误、目录何时出现
  let entriesAt = null;
  let accepted = false;
  while (Date.now() - openedAt < TIMEOUT) {
    const text = await bodyText(cdp);
    const t = Date.now() - openedAt;
    if (text.includes('会话正在连接')) {
      if (!evidence.connectingSeen) evidence.timeline.push([t, '出现「会话正在连接」占位']);
      evidence.connectingSeen = true;
    }
    if (text.includes('会话尚未建立')) {
      if (!evidence.badErrorSeen) evidence.timeline.push([t, '❗出现「会话尚未建立」目录错误']);
      evidence.badErrorSeen = true;
    }
    if (!accepted && text.includes('确认主机指纹')) {
      accepted = (await clickText(cdp, '接受')) || accepted;
      evidence.timeline.push([t, `点「接受」主机指纹=${accepted}`]);
    }
    // 目录出现的判据：表头「修改时间」+ 至少一个真实时间单元格
    // （行内「改名/删除」是 hover 才显示的 `hidden` 元素，innerText 里看不到，不能当判据）
    if (text.includes('修改时间') && /\d{4}\/\d{1,2}\/\d{1,2} \d{1,2}:\d{2}/.test(text)) {
      entriesAt = t;
      evidence.timeline.push([t, '目录行自动出现（未点刷新）']);
      break;
    }
    await sleep(100);
  }

  const shotPath = path.join(OUT, 'sftp-gate.png');
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(shotPath, Buffer.from(shot.data, 'base64'));
  evidence.shot = shotPath;

  const finalText = await bodyText(cdp);
  evidence.entriesAutoLoaded = entriesAt !== null;
  evidence.entriesAfterMs = entriesAt;

  const pass = evidence.entriesAutoLoaded && !evidence.badErrorSeen;
  const result = { status: pass ? 'ok' : 'fail', ...evidence };
  console.log(JSON.stringify(result, null, 2));
  cdp.close();
  process.exit(pass ? 0 : 1);
}

main().catch((error) => {
  console.log(JSON.stringify({ status: 'fail', reason: String(error) }, null, 2));
  process.exit(1);
});

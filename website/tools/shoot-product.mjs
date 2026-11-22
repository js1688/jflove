#!/usr/bin/env node
// 真实产品截图器（仅供官网操作指引使用）
//
// 为什么单独写：官方 web_shot.mjs 每次调用都新建浏览器进程，登录态不保留；
// 本脚本在**同一个浏览器会话**里登录一次，然后逐路由截图。
//
// 依赖：Microsoft Edge（Chromium 内核）+ 一个把 /api 反代到后端的静态服务。
//
// 用法：
//   node website/tools/shoot-product.mjs --base http://127.0.0.1:8099 \
//        --user admin --pass xxx --out website/design/v2/screenshots/product

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const BASE = arg('base', 'http://127.0.0.1:8099');
const USER = arg('user', 'admin');
const PASS = arg('pass', '');
const OUT = arg('out', 'website/design/v2/screenshots/product');
const WIDTH = Number(arg('width', '1440'));
const HEIGHT = Number(arg('height', '900'));
const PORT = Number(arg('port', '9600'));

// 要截的页面：路径 → 文件名
const PAGES = [
    ['/login', 'login'],
    ['/files', 'files-disks'],
    ['/files/1', 'files-browser'],
    ['/notes', 'notes'],
    ['/transfer', 'transfer'],
    ['/offline', 'offline'],
    ['/security', 'security'],
    ['/settings', 'settings'],
    ['/admin/users', 'admin-users'],
    ['/admin/disks', 'admin-disks'],
    ['/admin/permissions', 'admin-permissions'],
    ['/admin/system', 'admin-system'],
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

const edge = process.env.EDGE_BIN || 'microsoft-edge';
const profile = `/tmp/jf-shoot-${PORT}-${Date.now()}`;
const child = spawn(edge, [
    '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    `--window-size=${WIDTH},${HEIGHT}`, 'about:blank',
], { stdio: 'ignore' });

let ws;
try {
    async function httpJson(p) {
        const res = await fetch(`http://127.0.0.1:${PORT}${p}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }

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
    const evaluate = async (expression) => {
        const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
        return r.result?.result?.value;
    };

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Emulation.setDeviceMetricsOverride', {
        width: WIDTH, height: HEIGHT, deviceScaleFactor: 1, mobile: false,
    });

    const shoot = async (name) => {
        const shot = await send('Page.captureScreenshot', { format: 'png' });
        if (!shot.result?.data) throw new Error(`截图失败：${name}`);
        const file = path.join(OUT, `${name}.png`);
        fs.writeFileSync(file, Buffer.from(shot.result.data, 'base64'));
        console.log(`saved: ${file}`);
    };

    // ── 第一步：登录页（未登录状态） ──
    await send('Page.navigate', { url: `${BASE}/login` });
    await sleep(3000);
    await shoot('login');

    // ── 第二步：走完登录流程 ──
    // 点「连接服务端」→ 等服务端给出会话 → 填账号密码 → 点登录
    const clickByText = async (text, tags = 'button, a, [role="button"]') => {
        const expr = `(() => {
            const t = ${JSON.stringify(text)};
            const nodes = [...document.querySelectorAll(${JSON.stringify(tags)})];
            const el = nodes.find((n) => (n.textContent || '').trim().includes(t));
            if (!el) return 'not-found';
            el.click();
            return 'clicked';
        })()`;
        const r = await evaluate(expr);
        console.log(`  click "${text}": ${r}`);
        return r;
    };

    const fill = async (selector, value) => {
        const expr = `(() => {
            const el = document.querySelector(${JSON.stringify(selector)});
            if (!el) return 'not-found';
            const proto = el.tagName === 'SELECT' ? HTMLSelectElement : HTMLInputElement;
            const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
            setter.call(el, ${JSON.stringify(value)});
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return 'ok';
        })()`;
        const r = await evaluate(expr);
        console.log(`  fill ${selector}: ${r}`);
    };

    console.log('开始登录流程…');
    await clickByText('连接服务端');
    await sleep(3000);

    // 登录表单：第一个 input 是用户名，第二个是密码
    const fillResult = await evaluate(`(() => {
        const inputs = [...document.querySelectorAll('input')];
        if (inputs.length < 2) return 'no-login-form:' + inputs.length;
        const setVal = (el, v) => {
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(el, v);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        };
        const typeAttr = (el) => (el.getAttribute('type') || 'text').toLowerCase();
        const pwdInput = inputs.find((el) => typeAttr(el) === 'password');
        const userInput = inputs.find((el) => typeAttr(el) !== 'password');
        if (!userInput || !pwdInput) return 'inputs-not-paired';
        setVal(userInput, ${JSON.stringify(USER)});
        setVal(pwdInput, ${JSON.stringify(PASS)});
        return 'filled';
    })()`);
    console.log('  填表:', fillResult);
    await sleep(500);

    // 点登录按钮（文案随版本可能为「登录」）
    for (const label of ['登录', '登 录', '立即登录']) {
        const r = await clickByText(label);
        if (r === 'clicked') break;
    }
    await sleep(4000);

    const afterLogin = await evaluate('location.pathname');
    console.log('  登录后路径:', afterLogin);
    if (!afterLogin || afterLogin === '/login') {
        const err = await evaluate(`(() => {
            const e = document.querySelector('[class*="error"], .text-danger, [role="alert"]');
            return e ? (e.textContent || '').trim().slice(0, 120) : 'no-error-node';
        })()`);
        throw new Error(`登录未成功，当前仍在 /login。页面错误：${err}`);
    }

    // ── 第三步：逐页截图 ──
    for (const [route, name] of PAGES) {
        if (route === '/login') continue;
        await send('Page.navigate', { url: `${BASE}${route}` });
        await sleep(2600);
        const where = await evaluate('location.pathname');
        if (where === '/login') throw new Error(`会话丢失，${route} 被重定向到登录页`);
        await shoot(name);
    }

    console.log('全部截图完成');
} catch (err) {
    console.error('[shoot-product] 失败：', err.message);
    process.exitCode = 1;
} finally {
    try { ws?.close(); } catch { /* ignore */ }
    try { child.kill('SIGKILL'); } catch { /* ignore */ }
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ }
}

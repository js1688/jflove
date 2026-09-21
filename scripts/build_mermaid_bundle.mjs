/**
 * 构建期产物：Mermaid 离线渲染包（三端共用同一份，保证渲染一致性）
 *
 * 背景：
 *   - jflove-web 用 Vite 打包，本来可以直接 `import mermaid`；但桌面端（Python）与
 *     移动端（Flutter）都没有 npm 运行时，必须预打包成自包含文件。
 *   - Mermaid v11 只发布 ESM（`dist/mermaid.esm.min.mjs`），在 `file://` 与 WebView
 *     `loadHtmlString` 场景下模块解析不可靠 —— 实测确认：打成 IIFE 后单文件即可离线渲染。
 *
 * 用法（工作目录必须是 jflove-web，以保证 esbuild 能解析到 node_modules）：
 *     cd jflove-web
 *     node ../scripts/build_mermaid_bundle.mjs
 *
 * 产物：
 *     jflove-web/public/vendor/mermaid.bundle.js         Web 端（静态资源，独立 chunk）
 *     jflove-desktop/resources/mermaid/mermaid.bundle.js 桌面端（离屏 QWebEnginePage 加载）
 *     jflove-app/assets/mermaid/mermaid.bundle.js        移动端（构建期内联进 HTML 模板）
 *     jflove-web/public/vendor/mermaid.version.json      版本 + 校验信息
 *
 * 版本锁定：设计文档 §4.1 规定三端必须使用同一大版本。脚本会校验
 * `node_modules/mermaid/package.json` 的版本与 LOCKED_MAJOR_MINOR 一致，不一致直接失败，
 * 防止某端误升级导致同一笔记在三端画出不同的图。
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..');
const WEB_DIR = join(REPO_ROOT, 'jflove-web');

/** 三端锁定的 Mermaid 版本（设计文档 §4.1） */
const LOCKED_MAJOR_MINOR = '11.16';

const TARGETS = [
  { name: 'web', out: join(WEB_DIR, 'public', 'vendor', 'mermaid.bundle.js') },
  { name: 'desktop', out: join(REPO_ROOT, 'jflove-desktop', 'resources', 'mermaid', 'mermaid.bundle.js') },
  { name: 'app', out: join(REPO_ROOT, 'jflove-app', 'assets', 'mermaid', 'mermaid.bundle.js') },
];

function fail(msg) {
  console.error(`\n[build_mermaid_bundle] 失败：${msg}\n`);
  process.exit(1);
}

/* ---------- 1. 校验运行位置与 Mermaid 版本 ---------- */
if (!existsSync(WEB_DIR)) fail(`找不到 jflove-web 目录：${WEB_DIR}`);

const mermaidPkgPath = join(WEB_DIR, 'node_modules', 'mermaid', 'package.json');
if (!existsSync(mermaidPkgPath)) {
  fail('未在 jflove-web/node_modules 下找到 mermaid。请先在 jflove-web 里执行 npm install。');
}

const mermaidVersion = JSON.parse(readFileSync(mermaidPkgPath, 'utf8')).version;
if (!mermaidVersion.startsWith(LOCKED_MAJOR_MINOR)) {
  fail(
    `Mermaid 版本不一致：已安装 ${mermaidVersion}，但设计文档 §4.1 锁定 ${LOCKED_MAJOR_MINOR}.x。\n` +
    `  三端必须使用同一版本，否则同一份笔记在不同端会渲染出不同的图（AC-9）。\n` +
    `  如确需升级，请先改设计文档 §4.1 与本脚本的 LOCKED_MAJOR_MINOR，再同步三端。`,
  );
}
console.log(`[build_mermaid_bundle] Mermaid 版本校验通过：${mermaidVersion}`);

/* ---------- 2. 调用 esbuild ----------
 * 走 esbuild 的 Node API（而不是 `npx esbuild` CLI）：
 *   - 不需要 shell，避开 Windows 上 spawn `.cmd` 的 EINVAL 与 DEP0190 警告；
 *   - 不需要每次联网拉 npx 包，离线可构建。
 * esbuild 由 jflove-web 的 devDependencies 提供（Web 构建本来就需要它）。
 */
let esbuild;
try {
  // 必须从 jflove-web 解析：本脚本位于仓库根 scripts/，根目录没有 node_modules。
  // （Web 端由 Vite 提供 esbuild；本脚本额外把它列为 devDependency 以便独立运行。）
  const require = createRequire(join(WEB_DIR, 'package.json'));
  esbuild = require('esbuild');
} catch (e) {
  fail(
    `未找到 esbuild（${e.message.split('\n')[0]}）。\n` +
    '  请在 jflove-web 里执行 `npm install`。',
  );
}
if (!esbuild || typeof esbuild.build !== 'function') {
  fail('esbuild 模块形态异常：缺少 build() 方法');
}

let bundle;
try {
  const result = await esbuild.build({
    stdin: {
      contents: 'import mermaid from "mermaid";\nwindow.mermaid = mermaid;\n',
      resolveDir: WEB_DIR,
      sourcefile: 'mermaid-bundle-entry.mjs',
      loader: 'js',
    },
    bundle: true,
    format: 'iife',
    minify: true,
    target: 'es2020',
    write: false,
    logLevel: 'warning',
  });
  bundle = Buffer.from(result.outputFiles[0].contents);
} catch (e) {
  fail(`esbuild 打包失败：${e.message}`);
}

if (!bundle || bundle.length === 0) fail('esbuild 未产出内容');
const sha256 = createHash('sha256').update(bundle).digest('hex');
const sizeKB = Math.round(bundle.length / 1024);

/* 基本正确性检查：IIFE 里必须真的挂上了 window.mermaid */
const head = bundle.subarray(0, 512).toString('utf8');
if (!head.includes('use strict') && !head.includes('(()=>')) {
  fail('产物不是预期的 IIFE 格式');
}

/* ---------- 3. 分发到三端 ---------- */
const written = [];
for (const t of TARGETS) {
  mkdirSync(dirname(t.out), { recursive: true });
  writeFileSync(t.out, bundle);
  written.push({ target: t.name, path: t.out, bytes: bundle.length });
}

/* ---------- 4. 写版本清单（供各端运行时校验） ---------- */
const manifest = {
  mermaidVersion,
  lockedMajorMinor: LOCKED_MAJOR_MINOR,
  sha256,
  bytes: bundle.length,
  generatedBy: 'scripts/build_mermaid_bundle.mjs',
  note: '三端共用同一份离线渲染包；改动 mermaid 版本必须同步设计文档 §4.1',
};
writeFileSync(
  join(WEB_DIR, 'public', 'vendor', 'mermaid.version.json'),
  JSON.stringify(manifest, null, 2) + '\n',
  'utf8',
);

/* ---------- 5. 完成 ---------- */
console.log(`[build_mermaid_bundle] 打包完成：${sizeKB} KB，sha256=${sha256.slice(0, 16)}…`);
for (const w of written) {
  console.log(`  → ${w.target.padEnd(8)} ${w.path.replace(REPO_ROOT + '\\', '').replace(REPO_ROOT + '/', '')}`);
}
console.log('[build_mermaid_bundle] 完成');

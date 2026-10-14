/**
 * 构建期产物：文本编辑器 CM6 离线分包（**只有桌面端消费**）
 *
 * 背景（设计文档 v1.6.0 §6.1.1）：
 *   - 编辑器内核通过**运行期注入** `<script src="./vendor/editor.bundle.js">` 加载，
 *     源码里没有任何 `import '@codemirror/...'` ⇒ CM6 的 20+ 个包**不进** `ui/package.json`
 *     （`npm ci` 不必拉它们；类型由手写 `ui/src/types/codemirror-bundle.d.ts` 承担）。
 *   - 必须是**单个 IIFE**：`file://` 下动态 `import()` 的路径解析在 QtWebEngine 里不可靠。
 *     产物副作用把整包挂到 `window.__JFLOVE_CM__`（结构化 API）与 `window.cmModules`
 *     （原始模块命名空间，宿主缺什么 helper 可直接取，无需重新打包）。
 *
 * 用法（**工作目录任意**：脚本内部全部基于自身路径解析，不依赖 cwd）：
 *     node jflove-desktop/scripts/build-codemirror-bundle.mjs          # 仓库根
 *     node scripts/build-codemirror-bundle.mjs                        # jflove-desktop
 *
 * 前置：依赖装在 `jflove-desktop/scripts/editor-bundle/node_modules`（版本在
 *       `scripts/editor-bundle/package.json` 里**精确钉死**）：
 *     cd jflove-desktop/scripts/editor-bundle && npm install
 *       esbuild 也从该目录解析（它是该 package.json 的 devDependency）。
 *
 * 产物（`ui/public/vendor/` 已被 `.gitignore:86` 忽略，不入库）：
 *     jflove-desktop/ui/public/vendor/editor.bundle.js        IIFE 内核
 *     jflove-desktop/ui/public/vendor/editor.version.json     版本 + sha256（构建期 import）
 *
 * 为什么是**本地脚本**而不是仓库根 `scripts/`：产物只有桌面端消费，与 mermaid 三端共用不同
 * （设计文档 §6.1.1 明确区隔，避免重蹈 `jflove-desktop/resources/` 失效路径的覆辙）。
 *
 * 版本锁：本脚本的 `LOCKED` 是唯一真相，与 `scripts/editor-bundle/package.json` 必须逐条相等；
 * 安装结果与 `LOCKED` 不一致（含补丁位）**直接失败**。改版本时必须同步三处：
 *   ① 本脚本 LOCKED  ② scripts/editor-bundle/package.json  ③ ui/src/types/codemirror-bundle.d.ts
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));            // jflove-desktop/scripts
const DESKTOP_DIR = resolve(HERE, '..');                          // jflove-desktop
const REPO_ROOT = resolve(DESKTOP_DIR, '..');                     // 仓库根
const DEPS_DIR = join(HERE, 'editor-bundle');                     // 构建依赖安装位置
const OUT_DIR = join(DESKTOP_DIR, 'ui', 'public', 'vendor');      // 唯一落点（桌面端消费）
const OUT_BUNDLE = join(OUT_DIR, 'editor.bundle.js');
const OUT_MANIFEST = join(OUT_DIR, 'editor.version.json');

/**
 * 显式钉死的依赖版本（唯一真相）。
 * 与 `scripts/editor-bundle/package.json` 完全一致；不一致时本脚本会失败。
 * 「codemirror」= `@codemirror/view`：内核/UI 那一层的版本，也是手写 .d.ts 的同步基准
 * （CM6 各包独立发版，不存在单一 "codemirror" 版本；`codemirror` 元包并未安装）。
 */
const LOCKED_CODEMIRROR_PACKAGE = '@codemirror/view';
const LOCKED = {
  '@codemirror/state': '6.7.6',
  '@codemirror/view': '6.43.14',
  '@codemirror/commands': '6.11.1',
  '@codemirror/search': '6.7.2',
  '@codemirror/language': '6.13.1',
  '@codemirror/autocomplete': '6.20.3',
  '@codemirror/merge': '6.12.2',
  '@codemirror/lang-json': '6.0.2',
  '@codemirror/lang-markdown': '6.5.2',
  '@codemirror/lang-sql': '6.10.0',
  '@codemirror/lang-xml': '6.1.0',
  '@codemirror/lang-yaml': '6.1.3',
  '@codemirror/lang-python': '6.2.1',
  '@codemirror/lang-javascript': '6.2.5',
  '@codemirror/lang-css': '6.3.1',
  '@codemirror/lang-html': '6.4.12',
  '@codemirror/lang-java': '6.0.2',
  '@codemirror/lang-cpp': '6.0.3',
  '@codemirror/lang-php': '6.0.2',
  '@codemirror/lang-rust': '6.0.2',
  '@codemirror/lang-go': '6.0.1',
  '@codemirror/legacy-modes': '6.5.5',
  'sql-formatter': '15.9.0',
  'xml-formatter': '3.7.0',
  esbuild: '0.28.2',
};

function fail(msg) {
  console.error(`\n[build-codemirror-bundle] 失败：${msg}\n`);
  process.exit(1);
}

/** 仓库根相对路径（只用于打印，跨平台统一正斜杠） */
function rel(p) {
  return relative(REPO_ROOT, p).replace(/\\/g, '/');
}

/* ---------- 1. 校验依赖位置与版本（严格相等，含补丁位） ---------- */
if (!existsSync(DEPS_DIR)) fail(`找不到构建依赖目录：${rel(DEPS_DIR)}`);
const depPkgPath = join(DEPS_DIR, 'package.json');
if (!existsSync(depPkgPath)) fail(`找不到 ${rel(depPkgPath)}`);

const installed = {};
for (const [name, expected] of Object.entries(LOCKED)) {
  const pkgJson = join(DEPS_DIR, 'node_modules', name, 'package.json');
  if (!existsSync(pkgJson)) {
    fail(
      `依赖未安装：${name}\n` +
      `  请先执行：cd ${rel(DEPS_DIR)} && npm install`,
    );
  }
  const actual = JSON.parse(readFileSync(pkgJson, 'utf8')).version;
  if (actual !== expected) {
    fail(
      `版本不一致：${name} 实际装到 ${actual}，本脚本锁定 ${expected}。\n` +
      '  本脚本显式钉死版本（设计文档 §6.1.1「版本锁」）。如确需升级，请同步修改三处：\n' +
      '    ① scripts/build-codemirror-bundle.mjs 的 LOCKED\n' +
      '    ② scripts/editor-bundle/package.json\n' +
      '    ③ ui/src/types/codemirror-bundle.d.ts（手写类型，版本变化时必须同步更新）',
    );
  }
  installed[name] = actual;
}
console.log(
  `[build-codemirror-bundle] 版本校验通过：内核 ${LOCKED_CODEMIRROR_PACKAGE}@` +
  `${installed[LOCKED_CODEMIRROR_PACKAGE]}，共 ${Object.keys(LOCKED).length} 个包（含 esbuild）`,
);

/* ---------- 2. 解析 esbuild（从 editor-bundle 目录，走 Node API 不走 npx CLI） ----------
 * 走 Node API 的理由与 mermaid 脚本一致：不需要 shell（避开 Windows spawn .cmd 的 EINVAL）、
 * 不需要联网拉 npx 包，离线可构建。
 */
let esbuild;
try {
  const require = createRequire(join(DEPS_DIR, 'package.json'));
  esbuild = require('esbuild');
} catch (e) {
  fail(
    `未找到 esbuild（${String(e.message).split('\n')[0]}）。\n` +
    `  请执行：cd ${rel(DEPS_DIR)} && npm install`,
  );
}
if (!esbuild || typeof esbuild.build !== 'function') fail('esbuild 模块形态异常：缺少 build() 方法');

/* ---------- 3. 打包入口（内联 stdin）：把整包挂到 window.__JFLOVE_CM__ ---------- */
/** 内核实际版本（注入进产物，供运行期与 editor.version.json 比对，只告警不阻断） */
const RUNTIME_VERSION = {
  codemirror: installed[LOCKED_CODEMIRROR_PACKAGE],
  merge: installed['@codemirror/merge'],
  sqlFormatter: installed['sql-formatter'],
  xmlFormatter: installed['xml-formatter'],
};

const ENTRY = `
import * as state from "@codemirror/state";
import * as view from "@codemirror/view";
import * as commands from "@codemirror/commands";
import * as search from "@codemirror/search";
import * as language from "@codemirror/language";
import * as autocomplete from "@codemirror/autocomplete";
import * as merge from "@codemirror/merge";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { sql } from "@codemirror/lang-sql";
import { xml } from "@codemirror/lang-xml";
import { yaml } from "@codemirror/lang-yaml";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { java } from "@codemirror/lang-java";
import { cpp } from "@codemirror/lang-cpp";
import { php } from "@codemirror/lang-php";
import { rust } from "@codemirror/lang-rust";
import { go } from "@codemirror/lang-go";
import { toml as legacyToml } from "@codemirror/legacy-modes/mode/toml";
import { properties as legacyProperties } from "@codemirror/legacy-modes/mode/properties";
import { diff as legacyDiff } from "@codemirror/legacy-modes/mode/diff";
import { shell as legacyShell } from "@codemirror/legacy-modes/mode/shell";
import { powerShell as legacyPowerShell } from "@codemirror/legacy-modes/mode/powershell";
import { format as formatSql, supportedDialects as sqlDialects } from "sql-formatter";
import formatXml from "xml-formatter";

/* 纯文本：不注入任何语言扩展（CM6 允许空扩展数组），宿主按 pane.language 取用时不会拿到 undefined */
var PLAIN = function () { return []; };

/* 遗留模式（CM5 移植）：design §6.1.1「补齐 toml / ini / properties / diff / shell / bat 等」。
   bat 上游没有专用模式（legacy-modes 无 batch），按最接近的 shell 模式处理并在此注明。 */
var stream = language.StreamLanguage.define;

var languages = {
  /* 首类语言包 */
  json: function () { return json(); },
  jsonc: function () { return json(); },
  markdown: function () { return markdown(); },
  sql: function () { return sql(); },
  xml: function () { return xml(); },
  html: function () { return html(); },
  yaml: function () { return yaml(); },
  css: function () { return css(); },
  python: function () { return python(); },
  java: function () { return java(); },
  cpp: function () { return cpp(); },
  php: function () { return php(); },
  rust: function () { return rust(); },
  go: function () { return go(); },
  javascript: function () { return javascript(); },
  jsx: function () { return javascript({ jsx: true }); },
  typescript: function () { return javascript({ typescript: true }); },
  tsx: function () { return javascript({ typescript: true, jsx: true }); },
  /* 遗留模式 */
  toml: function () { return stream(legacyToml); },
  ini: function () { return stream(legacyProperties); },
  conf: function () { return stream(legacyProperties); },
  properties: function () { return stream(legacyProperties); },
  diff: function () { return stream(legacyDiff); },
  shell: function () { return stream(legacyShell); },
  sh: function () { return stream(legacyShell); },
  bat: function () { return stream(legacyShell); },
  powershell: function () { return stream(legacyPowerShell); },
  ps1: function () { return stream(legacyPowerShell); },
  /* 别名与纯文本（design §5.3 的 language 列） */
  yml: function () { return yaml(); },
  svg: function () { return xml(); },
  patch: function () { return stream(legacyDiff); },
  text: PLAIN,
  txt: PLAIN,
  csv: PLAIN,
  tsv: PLAIN,
};

/* 常用扩展的快捷入口（宿主也可从 modules.* 里自取；consoles/搜索/折叠等） */
var extensions = {
  rectangularSelection: view.rectangularSelection,
  drawSelection: view.drawSelection,
  highlightActiveLine: view.highlightActiveLine,
  highlightActiveLineGutter: view.highlightActiveLineGutter,
  highlightSpecialChars: view.highlightSpecialChars,
  lineNumbers: view.lineNumbers,
  dropCursor: view.dropCursor,
  crosshairCursor: view.crosshairCursor,
  scrollPastEnd: view.scrollPastEnd,
  placeholder: view.placeholder,
  keymap: view.keymap,
  highlightSelectionMatches: search.highlightSelectionMatches,
  bracketMatching: language.bracketMatching,
  foldGutter: language.foldGutter,
  indentOnInput: language.indentOnInput,
  syntaxHighlighting: language.syntaxHighlighting,
  defaultHighlightStyle: language.defaultHighlightStyle,
  StreamLanguage: language.StreamLanguage,
  Prec: state.Prec,
};

var formatters = {
  sql: formatSql,
  sqlFormat: formatSql,
  sqlDialects: sqlDialects,
  xml: formatXml,
  xmlFormat: formatXml,
};

var modules = {
  state: state,
  view: view,
  commands: commands,
  search: search,
  language: language,
  autocomplete: autocomplete,
  merge: merge,
  legacyModes: {
    toml: legacyToml,
    properties: legacyProperties,
    diff: legacyDiff,
    shell: legacyShell,
    powerShell: legacyPowerShell,
  },
};

/* 内核实际版本：构建脚本注入（与 editor.version.json 同源），仅用于运行期一致性告警 */
var version = ${JSON.stringify(RUNTIME_VERSION)};

var api = {
  version: version,
  EditorState: state.EditorState,
  EditorView: view.EditorView,
  MergeView: merge.MergeView,
  merge: merge,
  languages: languages,
  languageIds: Object.keys(languages).sort(),
  extensions: extensions,
  formatters: formatters,
  modules: modules,
};

/* 副作用挂载：运行期消费的唯一契约（设计文档 §6.1.1「产出文件与形态」） */
window.__JFLOVE_CM__ = api;
/* 兼容设计文档 §3.1 第 21 行提到的 window.cmModules：原始模块命名空间逃生口 */
window.cmModules = modules;
`;

let bundle;
try {
  const result = await esbuild.build({
    stdin: {
      contents: ENTRY,
      resolveDir: DEPS_DIR,
      sourcefile: 'jflove-editor-bundle-entry.mjs',
      loader: 'js',
    },
    bundle: true,
    format: 'iife',
    minify: true,
    target: 'es2020',
    platform: 'browser',
    write: false,
    logLevel: 'warning',
  });
  bundle = Buffer.from(result.outputFiles[0].contents);
} catch (e) {
  fail(`esbuild 打包失败：${e.message}`);
}

if (!bundle || bundle.length === 0) fail('esbuild 未产出内容');

/* ---------- 4. 产物形态静态自检 ---------- */
const head = bundle.subarray(0, 512).toString('utf8');
if (!head.includes('use strict') && !head.includes('(()=>')) {
  fail('产物不是预期的 IIFE 格式（单文件 IIFE 是硬约束：file:// 下动态 import() 不可靠）');
}
if (!bundle.toString('utf8').includes('__JFLOVE_CM__')) {
  fail('产物中找不到 window.__JFLOVE_CM__ 挂载点（入口副作用丢失）');
}

/* ---------- 5. 产物运行期自检：伪 window 里求值，断言关键键都在 ---------- */
const REQUIRED_KEYS = ['version', 'EditorView', 'EditorState', 'MergeView', 'merge', 'languages', 'formatters', 'modules'];
/** 伪 window：CM6 顶层有 typeof document/navigator 守卫，缺失不会抛错 */
const fakeWindow = {};
try {
  const sandbox = {
    window: fakeWindow,
    document: undefined,
    navigator: { userAgent: 'node', vendor: '', platform: 'node' },
    console: { log() {}, warn() {}, error() {} },
  };
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  new vm.Script(bundle.toString('utf8'), { filename: 'editor.bundle.js' }).runInNewContext(sandbox, { timeout: 15000 });
} catch (e) {
  fail(`产物在伪 window 中求值失败：${e.message}`);
}
const api = fakeWindow.__JFLOVE_CM__;
if (!api) fail('产物求值后 window.__JFLOVE_CM__ 仍为空');
const missing = REQUIRED_KEYS.filter((k) => api[k] === undefined || api[k] === null);
if (missing.length) fail(`window.__JFLOVE_CM__ 缺少键：${missing.join(', ')}`);
if (typeof api.EditorView !== 'function' || typeof api.EditorState !== 'function') {
  fail('window.__JFLOVE_CM__ 的 EditorView / EditorState 不是构造函数');
}
if (typeof api.EditorView.rectangularSelection !== 'function' && typeof api.extensions.rectangularSelection !== 'function') {
  fail('矩形选择入口缺失（extensions.rectangularSelection 不是函数）');
}
if (api.version.codemirror !== installed[LOCKED_CODEMIRROR_PACKAGE]) {
  fail(`产物内注入的版本 ${api.version.codemirror} 与实际安装版本不一致`);
}
if (fakeWindow.cmModules !== api.modules) fail('window.cmModules 未按规定挂载');

/* ---------- 6. 落盘（唯一落点：ui/public/vendor/） ---------- */
const sha256 = createHash('sha256').update(bundle).digest('hex');
mkdirSync(OUT_DIR, { recursive: true });
writeFileSync(OUT_BUNDLE, bundle);

/* ---------- 7. 版本清单（构建期 import；字段照设计文档 §6.1.1 + 可复现用的 extras） ---------- */
const manifest = {
  codemirror: installed[LOCKED_CODEMIRROR_PACKAGE],
  merge: installed['@codemirror/merge'],
  sqlFormatter: installed['sql-formatter'],
  xmlFormatter: installed['xml-formatter'],
  sha256,
  bytes: bundle.length,
  /** 上面 `codemirror` 字段指的是哪个包（CM6 各包独立发版，无单一版本号） */
  codemirrorPackage: LOCKED_CODEMIRROR_PACKAGE,
  /** 全部钉死版本，便于复现与比对 */
  locked: installed,
  generatedBy: 'jflove-desktop/scripts/build-codemirror-bundle.mjs',
  note: '桌面端专用；CM6 不进 ui/package.json（design §6.1.1）。版本变化必须同步 ui/src/types/codemirror-bundle.d.ts',
};
writeFileSync(OUT_MANIFEST, JSON.stringify(manifest, null, 2) + '\n', 'utf8');

/* ---------- 8. 完成 ---------- */
const sizeKB = Math.round(bundle.length / 1024);
console.log(`[build-codemirror-bundle] 打包完成：${bundle.length} 字节（${sizeKB} KB），sha256=${sha256}`);
console.log(`  → ${rel(OUT_BUNDLE)}`);
console.log(`  → ${rel(OUT_MANIFEST)}`);
console.log('[build-codemirror-bundle] 完成');

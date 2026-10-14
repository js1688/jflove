/**
 * 构建期派生脚本：`ui/src/config/scratch-types.ts` 的 `TYPES` → `ui/public/file-assoc.manifest.json`
 *
 * 设计依据：v1.6.0 §5.1（单一真相机制 / AC-48a）与 §6.7.1（清单源）。本脚本是派生链的
 * **第一环**，也是唯一把 `.ts` 变成机器可读清单的地方：
 *
 *     ui/src/config/scratch-types.ts  ──►  file-assoc.manifest.json  ──►  file_assoc_service
 *     （唯一手写真相）       本脚本          （生成物，随 dist/ 进包）      + 打包资产渲染
 *
 * ## 为什么是「文本提取 + JSON.parse」而不是 import
 *
 * Node **不能**直接 `import` `.ts`。`scratch-types.ts` 的 `TYPES` 数组字面量因此被要求写成
 * **合法 JSON**（双引号键名、无尾逗号、无注释，见该文件头部说明），本脚本按
 * 「`export const TYPES` 之后第一个 `[` → 字符串感知地找到匹配的 `]`」切片，再交给
 * `JSON.parse`。这样不需要任何 TS 工具链（不引入 typescript / ts-node 依赖）。
 *
 * ## 用法（工作目录任意：全部基于脚本自身路径解析）
 *
 *     node jflove-desktop/scripts/gen_file_assoc_manifest.mjs              # 生成/覆盖 manifest
 *     node scripts/gen_file_assoc_manifest.mjs --check                     # 只比对不写（退出码 0/1）
 *     node scripts/gen_file_assoc_manifest.mjs --src <ts> --out <json>     # 夹具验证用（覆盖两端）
 *
 * ## 失败即响亮（非零退出）
 *
 * 提取失败 / 结构非法 / 与磁盘不一致 ⇒ 打印明确原因并以 **1** 退出。绝不产出"看起来成功"
 * 的半成品清单（派生链一旦静默漂移，注册项与设置页就会各说各话）。
 *
 * ## 反例禁止（§5.1）
 *
 * 任何 Python 文件 / `.iss` / `.desktop` / `.xml` **都不得**手打扩展名清单：新增格式只改
 * `scratch-types.ts` 一处，随后重跑本脚本 + `scripts/gen_file_assoc_assets.py`。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));              // jflove-desktop/scripts
const DESKTOP_DIR = resolve(HERE, '..');                            // jflove-desktop
const REPO_ROOT = resolve(DESKTOP_DIR, '..');                       // 仓库根

const DEFAULT_SRC = join(DESKTOP_DIR, 'ui', 'src', 'config', 'scratch-types.ts');
const DEFAULT_OUT = join(DESKTOP_DIR, 'ui', 'public', 'file-assoc.manifest.json');

/** manifest 版本（§5.1 固定为 1） */
const MANIFEST_VERSION = 1;
/** `generatedFrom` 的来源标注（与 `scratch-types.ts::MANIFEST_GENERATED_FROM` 逐字一致） */
const GENERATED_FROM = 'config/scratch-types.ts';
/** 扩展名条数上限：纯防御，解析错位时不至于把半个文件当清单 */
const MAX_ENTRIES = 512;

function fail(msg) {
  console.error(`\n[gen-file-assoc-manifest] 失败：${msg}\n`);
  process.exit(1);
}

/** 仓库根相对路径（只用于打印，跨平台统一正斜杠） */
function rel(p) {
  return relative(REPO_ROOT, p).replace(/\\/g, '/');
}

function usage() {
  console.log(`用法：node scripts/gen_file_assoc_manifest.mjs [选项]

  --src <path>   覆盖 TS 源文件（默认 jflove-desktop/ui/src/config/scratch-types.ts）
  --out <path>   覆盖 manifest 落点（默认 jflove-desktop/ui/public/file-assoc.manifest.json）
  --check        不写盘：重跑派生并与 --out 指向的文件逐字节比对（一致 exit 0 / 不一致 exit 1）
  -h, --help     显示本帮助

说明：本脚本只读 scratch-types.ts 的 TYPES 数组（合法 JSON 字面量），不 import .ts。`);
}

/* ---------- 1. 命令行 ---------- */
const argv = process.argv.slice(2);
let srcPath = DEFAULT_SRC;
let outPath = DEFAULT_OUT;
let checkOnly = false;

for (let i = 0; i < argv.length; i += 1) {
  const arg = argv[i];
  if (arg === '--src' || arg === '--out') {
    const value = argv[i + 1];
    if (value === undefined || value.startsWith('--')) fail(`${arg} 缺少参数值`);
    if (arg === '--src') srcPath = resolve(value); else outPath = resolve(value);
    i += 1;
  } else if (arg.startsWith('--src=')) {
    srcPath = resolve(arg.slice('--src='.length));
  } else if (arg.startsWith('--out=')) {
    outPath = resolve(arg.slice('--out='.length));
  } else if (arg === '--check') {
    checkOnly = true;
  } else if (arg === '-h' || arg === '--help') {
    usage();
    process.exit(0);
  } else {
    usage();
    fail(`未知参数 ${arg}`);
  }
}

/* ---------- 2. 文本提取：export const TYPES 之后第一个 [ → 匹配的 ] ---------- */

/**
 * 从 TS 源码里切出 `TYPES` 的数组字面量文本。
 *
 * 扫描器是**字符串感知**的：跳过 `"` / `'` / 反引号串（含 `\` 转义）与行注释 / 块注释，
 * 因此字面量内部即便出现方括号（如将来的正则或字符串）也不会破坏配平。
 *
 * @param {string} text TS 源码全文
 * @returns {string} 形如 `[ {...}, ... ]` 的字面量文本（含方括号）
 */
function extractTypesLiteral(text) {
  const declaration = /export\s+const\s+TYPES\b/.exec(text);
  if (declaration === null) {
    fail('在源文件里找不到 `export const TYPES` 声明（派生链的第一环断了）');
  }
  const eq = text.indexOf('=', declaration.index);
  if (eq < 0) fail('`export const TYPES` 之后找不到 `=`（声明写法被改动？）');
  const start = text.indexOf('[', eq);
  if (start < 0) fail('`export const TYPES` 之后找不到数组字面量的 `[`');

  let depth = 0;
  let quote = null;           // 当前所处字符串的定界符（null = 不在字符串里）
  let inLineComment = false;
  let inBlockComment = false;

  for (let i = start; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];

    if (inLineComment) {
      if (ch === '\n') inLineComment = false;
      continue;
    }
    if (inBlockComment) {
      if (ch === '*' && next === '/') { inBlockComment = false; i += 1; }
      continue;
    }
    if (quote !== null) {
      if (ch === '\\') { i += 1; continue; }        // 转义字符：连同下一字符一起跳过
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue; }
    if (ch === '/' && next === '/') { inLineComment = true; i += 1; continue; }
    if (ch === '/' && next === '*') { inBlockComment = true; i += 1; continue; }

    if (ch === '[') depth += 1;
    else if (ch === ']') {
      depth -= 1;
      if (depth === 0) return text.slice(start, i + 1);
      if (depth < 0) fail('数组字面量的方括号配平异常（多了 `]`）');
    }
  }
  fail('数组字面量未闭合：从 `[` 扫到文件末尾也没等到配平的 `]`');
  return '';
}

/* ---------- 3. 解析 + 结构校验（不合格就非零退出） ---------- */

/** 校验单条 `ext`（小写、无点、无通配） */
function assertExt(ext, index) {
  if (typeof ext !== 'string' || !/^[a-z0-9][a-z0-9-]*$/.test(ext)) {
    fail(`types[${index}].ext 非法：${JSON.stringify(ext)}（要求小写、不含点与通配符）`);
  }
}

/**
 * 解析并校验 TYPES 字面量，产出 §5.1 规定的 manifest 对象。
 *
 * @param {string} literal 数组字面量文本
 * @returns {{version: number, generatedFrom: string, types: object[]}}
 */
function buildManifest(literal) {
  let parsed;
  try {
    parsed = JSON.parse(literal);
  } catch (e) {
    fail(
      `TYPES 字面量不是合法 JSON：${e.message}\n` +
      '  TYPES 必须是纯 JSON 字面量（双引号键名、无尾逗号、无注释、无表达式）——\n' +
      '  这是 gen_file_assoc_manifest.mjs 文本派生（Node 不能 import .ts）的硬前提，\n' +
      '  详见 ui/src/config/scratch-types.ts 头部说明。',
    );
  }
  if (!Array.isArray(parsed)) fail('TYPES 不是数组');
  if (parsed.length === 0) fail('TYPES 为空数组（关联清单不能为空：注册项将无从派生）');
  if (parsed.length > MAX_ENTRIES) fail(`TYPES 条目数 ${parsed.length} 超过上限 ${MAX_ENTRIES}`);

  const seen = new Map();
  const types = parsed.map((raw, index) => {
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
      fail(`types[${index}] 不是对象`);
    }
    const keys = Object.keys(raw).sort().join(',');
    if (keys !== 'ext,label,language,mime') {
      fail(
        `types[${index}] 的字段是 {${keys}}，与 §5.1 规定的 {ext,label,language,mime} 不一致\n` +
        '  （每项只允许这四个字段；多余字段会让 manifest 与运行时 TYPES 悄悄分叉）',
      );
    }
    assertExt(raw.ext, index);
    if (seen.has(raw.ext)) fail(`扩展名重复：${raw.ext}（types[${index}] 与 types[${seen.get(raw.ext)}]）`);
    seen.set(raw.ext, index);
    if (typeof raw.label !== 'string' || raw.label === '') fail(`types[${index}].label 必须是非空字符串`);
    if (typeof raw.language !== 'string' || raw.language === '') {
      fail(`types[${index}].language 必须是非空字符串`);
    }
    if (!Array.isArray(raw.mime) || raw.mime.length === 0) {
      fail(`types[${index}].mime 必须是非空字符串数组（§5.1 的 mime 字段）`);
    }
    raw.mime.forEach((mime, mimeIndex) => {
      if (typeof mime !== 'string' || mime === '') {
        fail(`types[${index}].mime[${mimeIndex}] 必须是非空字符串`);
      }
    });
    // 字段顺序固定为 §5.1 的顺序，保证生成物逐字节可复现
    return { ext: raw.ext, label: raw.label, language: raw.language, mime: [...raw.mime] };
  });

  return { version: MANIFEST_VERSION, generatedFrom: GENERATED_FROM, types };
}

/* ---------- 4. 读源 → 派生 → 写 / 比对 ---------- */

/**
 * 渲染生成物文本：**每条扩展名一行**（与 `scratch-types.ts` 的排版一致，便于 review 与 diff）。
 *
 * 格式是生成物的内部约定，不构成第二份真相：`--check` 走同一个渲染函数，
 * Python 侧的 `--check` 也只拿本脚本的输出与磁盘比对（不在 Python 里复刻格式）。
 *
 * @param {{version: number, generatedFrom: string, types: object[]}} manifest manifest 对象
 * @returns {string} 以换行结尾的 JSON 文本
 */
function renderManifest(manifest) {
  const lines = [
    '{',
    `  "version": ${manifest.version},`,
    `  "generatedFrom": ${JSON.stringify(manifest.generatedFrom)},`,
    '  "types": [',
  ];
  manifest.types.forEach((entry, index) => {
    const mime = entry.mime.map((m) => JSON.stringify(m)).join(', ');
    const body = `    { "ext": ${JSON.stringify(entry.ext)}, "label": ${JSON.stringify(entry.label)}, `
      + `"language": ${JSON.stringify(entry.language)}, "mime": [${mime}] }`;
    lines.push(index + 1 < manifest.types.length ? `${body},` : body);
  });
  lines.push('  ]', '}');
  return `${lines.join('\n')}\n`;
}

if (!existsSync(srcPath)) fail(`找不到 TS 源文件：${rel(srcPath)}`);
const sourceText = readFileSync(srcPath, 'utf8');
const manifest = buildManifest(extractTypesLiteral(sourceText));
const rendered = renderManifest(manifest);

// 自检：渲染结果必须是合法 JSON 且与内存态语义一致（防"渲染函数写坏了还照写"）
try {
  const reparsed = JSON.parse(rendered);
  if (JSON.stringify(reparsed) !== JSON.stringify(manifest)) {
    fail('渲染结果与内存态 manifest 语义不一致（渲染函数有 bug）');
  }
} catch (e) {
  fail(`渲染结果不是合法 JSON：${e.message}`);
}

if (checkOnly) {
  if (!existsSync(outPath)) {
    fail(`manifest 不存在：${rel(outPath)}\n  请先运行：node scripts/gen_file_assoc_manifest.mjs`);
  }
  const onDisk = readFileSync(outPath, 'utf8');
  if (onDisk === rendered) {
    console.log(
      `[gen-file-assoc-manifest] 一致：${rel(outPath)}（${manifest.types.length} 条，`
      + `来源 ${rel(srcPath)}）`,
    );
    process.exit(0);
  }
  const onDiskLines = onDisk.split('\n');
  const renderedLines = rendered.split('\n');
  const diffs = [];
  const max = Math.max(onDiskLines.length, renderedLines.length);
  for (let i = 0; i < max && diffs.length < 20; i += 1) {
    if (onDiskLines[i] !== renderedLines[i]) {
      diffs.push(`  @@ 第 ${i + 1} 行\n  - 磁盘: ${onDiskLines[i] ?? '<无>'}\n  + 派生: ${renderedLines[i] ?? '<无>'}`);
    }
  }
  console.error(
    `\n[gen-file-assoc-manifest] 失败：${rel(outPath)} 与 ${rel(srcPath)} 的派生结果不一致\n`
    + diffs.join('\n')
    + `\n\n  修法：node scripts/gen_file_assoc_manifest.mjs（重新生成；不要手改生成物）\n`,
  );
  process.exit(1);
}

mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, rendered, 'utf8');
const extList = manifest.types.map((t) => t.ext).join(',');
console.log(
  `[gen-file-assoc-manifest] 已生成 ${rel(outPath)}：${manifest.types.length} 条`
  + `（version=${manifest.version}，generatedFrom=${manifest.generatedFrom}）`,
);
console.log(`  扩展名：${extList}`);

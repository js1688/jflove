/**
 * R-6 补充实测：字节级核对（BOM / 行分隔符）+ 压缩模式的文本节点损坏用例
 *
 * 主脚本 r6-format-libs.mjs 只看"字符级"结果，这里补三件事：
 *   1. BOM（U+FEFF / EF BB BF）在格式化后是否还在
 *   2. 默认 lineSeparator 到底是 \r\n 还是 \n（字节计数）
 *   3. 压缩（minify）在"文本节点与子元素相邻"时是否吃掉文本节点空白
 *      —— 这是 AC-33「压缩只删元素之间空白，不触碰文本节点内空白」的直接判据
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROBE_DIR = process.env.FMT_PROBE_DIR || path.join(os.tmpdir(), 'jflove-fmt-probe');
const require = createRequire(path.join(PROBE_DIR, '__probe_require__.cjs'));
const xmlFormat = require('xml-formatter');

const OUT_DIR = path.join(__dirname, 'r6-out');
fs.mkdirSync(OUT_DIR, { recursive: true });
const rows = [];

function save(id, input, output, ext = 'xml') {
  fs.writeFileSync(path.join(OUT_DIR, `${id}.in.${ext}`), input, 'utf8');
  fs.writeFileSync(path.join(OUT_DIR, `${id}.out.${ext}`), output, 'utf8');
}

function esc(s) {
  return JSON.stringify(s).slice(1, -1);
}

// ---------- 1. BOM ----------
{
  const input = '\uFEFF<?xml version="1.0" encoding="UTF-8"?><r><a>1</a></r>';
  const out = xmlFormat(input, { lineSeparator: '\n' });
  save('x8b-bom-bytes', input, out);
  rows.push({
    id: 'x8b-bom-bytes',
    title: 'BOM 是否保留（字符与字节双重核对）',
    input_has_uFEFF: input.charCodeAt(0) === 0xfeff,
    output_has_uFEFF: out.charCodeAt(0) === 0xfeff,
    output_first4_hex: Buffer.from(out, 'utf8').subarray(0, 4).toString('hex'),
    output_first4: esc(out.slice(0, 12)),
    verdict: out.charCodeAt(0) === 0xfeff ? 'BOM 保留' : 'BOM 被丢弃',
  });
}

// ---------- 2. 默认行分隔符 ----------
{
  const input = '<r><a>1</a><b>2</b></r>';
  const out = xmlFormat(input);
  const crlf = (out.match(/\r\n/g) || []).length;
  const lfTotal = (out.match(/\n/g) || []).length;
  rows.push({
    id: 'x10-default-linesep-bytes',
    title: '默认 lineSeparator 实测（\r\n 计数 vs 裸 \n 计数）',
    crlf_count: crlf,
    lf_count: lfTotal,
    bare_lf_count: lfTotal - crlf,
    output_hex: Buffer.from(out, 'utf8').subarray(0, 24).toString('hex'),
    verdict: crlf === lfTotal && crlf > 0 ? '默认是 \\r\\n（CRLF）' : '默认不是 CRLF',
  });
}

// ---------- 3. 压缩模式对文本节点空白的影响 ----------
const minifyCases = [
  {
    id: 'x6f-minify-mixed-inline',
    title: '压缩（不加 collapseContent）：文本节点与内联子元素相邻时空白是否被吃',
    input: '<r><p>This is <b>some</b> content.</p></r>',
    opts: {},
  },
  {
    id: 'x6g-minify-mixed-inline-collapse',
    title: '压缩 + collapseContent:true：同一输入',
    input: '<r><p>This is <b>some</b> content.</p></r>',
    opts: { collapseContent: true },
  },
  {
    id: 'x6h-minify-xmlspace',
    title: '压缩：xml:space="preserve" 是否保住文本节点空白',
    input: '<r><p xml:space="preserve">This is <b>some</b> content.</p></r>',
    opts: {},
  },
  {
    id: 'x6i-minify-space-textnode-ac33',
    title: 'AC-33 直接判据：压缩前后用标准解析器读文本节点值是否相同',
    input: '<root><a> </a><b>x</b><c>hello   world</c></root>',
    opts: { collapseContent: true },
  },
  {
    id: 'x6j-format-trailing-space',
    title: '格式化（非压缩）：纯文本元素的首尾空白是否被裁掉',
    input: '<r><v> 5 </v><w>keep  inner</w></r>',
    opts: { lineSeparator: '\n' },
    format: true,
  },
  {
    id: 'x6k-format-collapse-preserve',
    title: '格式化 + collapseContent:true：纯文本元素的首尾空白是否保住',
    input: '<r><v> 5 </v><w>keep  inner</w></r>',
    opts: { lineSeparator: '\n', collapseContent: true },
    format: true,
  },
];

for (const c of minifyCases) {
  const fn = c.format ? xmlFormat : xmlFormat.minify;
  let out = null;
  let err = null;
  try {
    out = fn(c.input, c.opts);
  } catch (e) {
    err = `${e.name}: ${e.message}`;
  }
  if (out !== null) save(c.id, c.input, out);
  c.output = out;
  c.error = err;
  rows.push({
    id: c.id,
    title: c.title,
    opts: c.opts,
    input: c.input,
    output: out,
    error: err,
  });
}

// ---------- 4. 逐用例判定：文本节点是否变化（用独立解析器交叉验证） ----------
// 不引入新依赖：用 Node 内置的 DOMParser 没有，改用简易正则取文本节点做比对（仅用于本探针）
function textNodes(xml) {
  // 去掉声明/注释/CDATA 后，取 ">...<" 之间的裸文本
  const stripped = xml
    .replace(/<\?[\s\S]*?\?>/g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<!\[CDATA\[[\s\S]*?\]\]>/g, '');
  const out = [];
  const re = />([^<]*)</g;
  let m;
  while ((m = re.exec(stripped)) !== null) out.push(m[1]);
  return out;
}

const judgement = [];
for (const c of minifyCases) {
  if (c.error || c.output === null) {
    judgement.push({ id: c.id, text_nodes_equal: null, note: c.error });
    continue;
  }
  const a = textNodes(c.input);
  const b = textNodes(c.output);
  // 比较"去掉纯空白差异后"的可见文本，同时单独报告空白敏感项
  const aVisible = a.map((s) => s.replace(/\s+/g, ' ').trim());
  const bVisible = b.map((s) => s.replace(/\s+/g, ' ').trim());
  judgement.push({
    id: c.id,
    in_text_nodes: a,
    out_text_nodes: b,
    visible_text_equal: JSON.stringify(aVisible) === JSON.stringify(bVisible),
    raw_text_nodes_equal: JSON.stringify(a) === JSON.stringify(b),
  });
}

const payload = { rows, judgement };
fs.writeFileSync(path.join(__dirname, 'r6-verify.json'), JSON.stringify(payload, null, 2), 'utf8');

console.log('=== 字节级核对 ===');
for (const r of rows.slice(0, 2)) console.log(JSON.stringify(r, null, 2));
console.log('\n=== 压缩/格式化文本节点用例 ===');
for (const r of rows.slice(2)) {
  console.log(`${r.id}  ${r.title}\n  in : ${JSON.stringify(r.input)}\n  out: ${r.error ? 'ERROR ' + r.error : JSON.stringify(r.output)}`);
}
console.log('\n=== 文本节点判定 ===');
for (const j of judgement) console.log(JSON.stringify(j));
console.log(`\n已写出：${path.join(__dirname, 'r6-verify.json')}`);

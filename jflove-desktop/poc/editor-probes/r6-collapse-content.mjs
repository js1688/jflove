/**
 * R-6 补充实测（六）：collapseContent 在多行文本上会"粘词"——最严重的一类静默损坏
 *
 * 起因：README 的 minify 示例（collapseContent:true）输出 `This is<b>some</b>content.`，
 * 而同一输入、不加 collapseContent 时空白是保留的。本脚本把这条差异刻画清楚。
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

// 多行（含换行+缩进的文本节点）—— 这是真实用户文件最常见的形态
const MULTILINE = [
  '<root>',
  '  <content>',
  '    <p>',
  '        This is <b>some</b> content.',
  '    </p>',
  '  </content>',
  '</root>',
].join('\n');

// 单行（文本节点内只有行内空白）
const SINGLELINE = '<root><p>This is <b>some</b> content.</p></root>';

const CASES = [
  ['c1-minify-multiline-default', 'multiline', 'minify', {}],
  ['c2-minify-multiline-collapse-true', 'multiline', 'minify', { collapseContent: true }],
  ['c3-minify-multiline-collapse-false', 'multiline', 'minify', { collapseContent: false }],
  ['c4-format-multiline-default', 'multiline', 'format', { lineSeparator: '\n' }],
  ['c5-format-multiline-collapse-true', 'multiline', 'format', { lineSeparator: '\n', collapseContent: true }],
  ['c6-minify-singleline-default', 'singleline', 'minify', {}],
  ['c7-minify-singleline-collapse-true', 'singleline', 'minify', { collapseContent: true }],
  ['c8-format-singleline-collapse-true', 'singleline', 'format', { lineSeparator: '\n', collapseContent: true }],
];

const rows = [];
for (const [id, which, mode, opts] of CASES) {
  const input = which === 'multiline' ? MULTILINE : SINGLELINE;
  const fn = mode === 'minify' ? xmlFormat.minify : xmlFormat;
  let out = null;
  let err = null;
  try {
    out = fn(input, opts);
  } catch (e) {
    err = `${e.name}: ${e.message}`;
  }
  if (out !== null) {
    fs.writeFileSync(path.join(OUT_DIR, `${id}.in.xml`), input, 'utf8');
    fs.writeFileSync(path.join(OUT_DIR, `${id}.out.xml`), out, 'utf8');
  }
  // 关键判据：把文本节点里的空白折叠成单个空格后，可见文字是否还等于原文
  const norm = (s) => s.replace(/<[^>]*>/g, (m) => m).replace(/>\s+</g, '><');
  const visible = (s) =>
    s
      .replace(/<[^>]*>/g, '\u0001')
      .split('\u0001')
      .map((t) => t.replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .join(' ');
  const glued = out !== null ? /This is<b>some<\/b>content\./.test(out) : false;
  rows.push({
    id,
    which,
    mode,
    opts,
    input,
    output: out,
    error: err,
    visible_in: visible(input),
    visible_out: out === null ? null : visible(out),
    visible_text_lost: out !== null && visible(input) !== visible(out),
    inline_space_removed_glued: glued,
  });
  console.log(
    `[${id}] ${which} ${mode} ${JSON.stringify(opts)}\n  out: ${err ? 'ERROR ' + err : JSON.stringify(out)}\n  可见文字: ${JSON.stringify(visible(input))} -> ${out === null ? '—' : JSON.stringify(visible(out))}  粘词=${glued}`
  );
}

fs.writeFileSync(path.join(__dirname, 'r6-collapse-content.json'), JSON.stringify({ rows }, null, 2), 'utf8');
console.log(`\n已写出：${path.join(__dirname, 'r6-collapse-content.json')}`);

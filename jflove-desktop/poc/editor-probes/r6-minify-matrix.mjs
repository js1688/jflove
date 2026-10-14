/**
 * R-6 补充实测（三）：压缩模式对"纯空白文本节点"的处置矩阵 + 推荐参数的端到端验证
 *
 * 背景：x6i 实测到 xmlFormat.minify('<root><a> </a>...</root>') 把 `<a> </a>` 吃成 `<a></a>`。
 * 本脚本穷举"能否用某个选项组合保住这个空格"，并给出 AC-33 的端到端判据结果。
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

function run(id, title, input, fn, opts) {
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
  rows.push({ id, title, mode: fn === xmlFormat.minify ? 'minify' : 'format', opts, input, output: out, error: err });
  console.log(
    `[${id}] ${fn === xmlFormat.minify ? 'minify' : 'format'} ${JSON.stringify(opts)}\n  in : ${JSON.stringify(input)}\n  out: ${err ? 'ERROR ' + err : JSON.stringify(out)}`
  );
  return out;
}

// ---------- 压缩模式矩阵：<a> </a> 这个"有意义的空格文本节点"能否保住 ----------
const SPACE_INPUT = '<root><a> </a><b>x</b></root>';
run('m1-minify-space-default', '压缩默认参数', SPACE_INPUT, xmlFormat.minify, {});
run('m2-minify-space-collapse-true', '压缩 + collapseContent:true', SPACE_INPUT, xmlFormat.minify, { collapseContent: true });
run('m3-minify-space-collapse-false', '压缩 + collapseContent:false', SPACE_INPUT, xmlFormat.minify, { collapseContent: false });
run('m4-minify-space-xmlspace-attr', '压缩 + 元素上 xml:space="preserve"', SPACE_INPUT.replace('<a>', '<a xml:space="preserve">'), xmlFormat.minify, {});
run('m5-minify-space-xmlspace-root', '压缩 + 根上 xml:space="preserve"', SPACE_INPUT.replace('<root>', '<root xml:space="preserve">'), xmlFormat.minify, {});
run('m6-minify-space-filter', '压缩 + filter 恒真（显式声明保留全部节点）', SPACE_INPUT, xmlFormat.minify, { filter: () => true });
run('m7-minify-two-spaces', '压缩：<a>  </a>（两个空格）', '<root><a>  </a><b>x</b></root>', xmlFormat.minify, {});
run('m8-minify-nbsp', '压缩：<a>&#160;</a>（不换行空格实体，非 ASCII 空白）', '<root><a>&#160;</a><b>x</b></root>', xmlFormat.minify, {});
run('m9-minify-space-in-mixed', '压缩：空格文本节点夹在元素之间 <b>y</b> <c>z</c>', '<root><b>y</b> <c>z</c></root>', xmlFormat.minify, {});
run('m10-minify-xmlspace-in-mixed', '压缩：xml:space="preserve" 能否保住 <b>y</b> <c>z</c> 之间的空格', '<root xml:space="preserve"><b>y</b> <c>z</c></root>', xmlFormat.minify, {});

// ---------- 推荐调用参数的端到端验证（AC-33 及其"不触碰文本节点空白"口径） ----------
// AC-33 原文样例 + 一个"有意义的空格文本节点" + 混合内容
const AC33_INPUT =
  '<?xml version="1.0" encoding="UTF-8"?>\n' +
  '<root><!--c--><a><![CDATA[ x < y ]]></a><br/><img src="x" />' +
  '<keep>hello   world</keep><sp> </sp><mix>This is <b>some</b> content.</mix></root>';

const RECOMMENDED = {
  indentation: '  ',
  lineSeparator: '\n',
  attributeQuotes: 'double',
  collapseContent: true,
  strictMode: true,
  throwOnFailure: true,
};

const fmtOut = run('v1-format-recommended', '推荐参数下的格式化（AC-33 样例）', AC33_INPUT, xmlFormat, RECOMMENDED);
const minOut = run('v2-minify-recommended', '推荐参数下的压缩（AC-33 样例）', AC33_INPUT, xmlFormat.minify, {
  collapseContent: true,
  strictMode: true,
  throwOnFailure: true,
});

// 文本节点比对（本探针自制；最终判据用 Python 的 xml.etree）
function textNodes(xml) {
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
const cmp = {
  input_text_nodes: textNodes(AC33_INPUT),
  format_text_nodes: fmtOut === null ? null : textNodes(fmtOut),
  minify_text_nodes: minOut === null ? null : textNodes(minOut),
  format_keeps_comment: fmtOut !== null && fmtOut.includes('<!--c-->'),
  format_keeps_cdata: fmtOut !== null && fmtOut.includes('<![CDATA[ x < y ]]>'),
  minify_keeps_comment: minOut !== null && minOut.includes('<!--c-->'),
  minify_keeps_cdata: minOut !== null && minOut.includes('<![CDATA[ x < y ]]>'),
  minify_keeps_space_textnode: minOut !== null && /<sp> <\/sp>/.test(minOut),
  format_keeps_space_textnode: fmtOut !== null && /<sp> <\/sp>/.test(fmtOut),
  format_keeps_double_space: fmtOut !== null && fmtOut.includes('hello   world'),
  minify_keeps_double_space: minOut !== null && minOut.includes('hello   world'),
  format_keeps_selfclosing: fmtOut !== null && fmtOut.includes('<br/>'),
  format_keeps_mixed_inline: fmtOut !== null && fmtOut.includes('This is <b>some</b> content.'),
};

fs.writeFileSync(path.join(__dirname, 'r6-minify-matrix.json'), JSON.stringify({ rows, recommended: RECOMMENDED, cmp }, null, 2), 'utf8');

console.log('\n=== 推荐参数下的关键判据 ===');
console.log(JSON.stringify(cmp, null, 2));
console.log(`\n已写出：${path.join(__dirname, 'r6-minify-matrix.json')}`);
console.log('待 Python 复核的产物：r6-out/v1-format-recommended.out.xml / v2-minify-recommended.out.xml');

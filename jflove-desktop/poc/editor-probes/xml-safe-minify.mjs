/**
 * R-6 替代方案验证：基于 xml-parser-xo 的"压缩前置预检 + 安全封装"
 *
 * 背景（实测结论）：xml-formatter@3.7.0 的 minify 会把"元素唯一内容为纯空白文本"的
 * 文本节点一并删掉（`<a> </a>` → `<a></a>`），而 AC-33 明确要求"压缩只删元素之间空白，
 * 不触碰文本节点内空白"。因此本文件给出一条**不改库、只加一层**的退路：
 *
 *   safeMinify(xml)
 *     ├─ 用 xml-parser-xo 解析出 AST（xml-formatter 自己的依赖，无新增第三方包）
 *     ├─ 找出"父元素除了这个纯空白文本节点之外没有任何子节点"的文本节点
 *     │    ⇒ 这类空白是**元素的内容**，删了就是改内容 ⇒ 判定"不安全"，拒绝压缩并给出位置
 *     └─ 没有这类节点 ⇒ 才交给 xmlFormat.minify（元素间的缩进空白照删）
 *
 * 运行：node jflove-desktop/poc/editor-probes/xml-safe-minify.mjs
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
const xo = require('xml-parser-xo');
const parseXml = xo.default || xo;

const WS = /^[ \t\r\n]*$/;

/** 递归收集"元素唯一内容 = 纯空白文本节点"的位置（AC-33 保护的类）
 *  xml:space="preserve" 的子树跳过（库本身会保住，不必拦）
 */
function findContentWhitespace(node, pathStr, out, inheritedPreserve = false) {
  if (!node || node.type !== 'Element') return;
  const preserve =
    inheritedPreserve || (node.attributes && node.attributes['xml:space'] === 'preserve');
  const kids = node.children || [];
  const meaningful = kids.filter((k) => !(k.type === 'Text' && WS.test(k.content)));
  if (!preserve && kids.length > 0 && meaningful.length === 0) {
    // 该元素的全部子节点都是纯空白文本 ⇒ 这些空白是元素内容，不是元素间的缩进
    for (const k of kids) {
      out.push({ path: pathStr, tag: node.name, content: JSON.stringify(k.content) });
    }
  }
  for (const k of kids) {
    if (k.type === 'Element') findContentWhitespace(k, `${pathStr}/${k.name}`, out, preserve);
  }
}

/** 安全压缩：不安全就拒绝，绝不静默改内容 */
function safeMinify(xml) {
  const ast = parseXml(xml);
  const warnings = [];
  findContentWhitespace(ast.root, `/${ast.root?.name ?? '?'}`, warnings);
  if (warnings.length > 0) {
    return {
      ok: false,
      output: null,
      warnings,
      reason: 'XML 含"元素内容为纯空白"的文本节点，压缩会改变文本节点',
    };
  }
  return {
    ok: true,
    // ⚠ 压缩**不要**传 collapseContent:true —— 实测（r6-collapse-content.mjs，用例 c2）它在
    //   多行文本上会"粘词"：`This is <b>some</b> content.` → `This is<b>some</b>content.`
    //   默认选项（c1）则安全保留行内空白。
    output: xmlFormat.minify(xml, { strictMode: true, throwOnFailure: true }),
    warnings: [],
  };
}

/** 安全格式化：collapseContent 保住已存在的文本节点，strictMode 拦住非法 XML；BOM 需调用方补回 */
function safeFormat(xml, { indentation = '  ', lineSeparator = '\n', blockOnUnsafe = true } = {}) {
  const hadBom = xml.charCodeAt(0) === 0xfeff;
  const body = hadBom ? xml.slice(1) : xml;
  const ast = parseXml(body);
  const warnings = [];
  findContentWhitespace(ast.root, `/${ast.root?.name ?? '?'}`, warnings);
  if (blockOnUnsafe && warnings.length > 0) {
    return { ok: false, output: null, warnings, reason: '格式化会改动"元素内容纯空白"的文本节点' };
  }
  const out = xmlFormat(body, {
    indentation,
    lineSeparator,
    attributeQuotes: 'double',
    collapseContent: true,
    strictMode: true,
    throwOnFailure: true,
  });
  return { ok: true, output: hadBom ? '\uFEFF' + out : out, bomRestored: hadBom, warnings };
}

// ---------------------------------------------------------------------------
const CASES = [
  { id: 'a1-ac33', title: 'AC-33 样例（含 <sp> </sp>）', input: '<root><!--c--><a><![CDATA[ x < y ]]></a><sp> </sp><b>hello   world</b></root>' },
  { id: 'a2-pretty', title: '正常缩进的 XML（应当允许压缩）', input: '<root>\n  <a>1</a>\n  <b>2</b>\n</root>' },
  { id: 'a3-mixed-inline', title: '混合内容 <b>y</b> <c>z</c>', input: '<root><b>y</b> <c>z</c></root>' },
  { id: 'a4-only-space', title: '<a> </a>（元素内容为单个空格）', input: '<root><a> </a></root>' },
  { id: 'a5-xmlspace', title: 'xml:space="preserve"', input: '<root xml:space="preserve"><a> </a></root>' },
  { id: 'a6-invalid', title: '非法 XML（缺闭合标签）', input: '<root><a>1</root>' },
  { id: 'a7-bom', title: '带 BOM 的 XML', input: '\uFEFF<?xml version="1.0" encoding="UTF-8"?><r><a>1</a></r>' },
  { id: 'a8-ac33-plain', title: 'AC-33 原文样例（无纯空白文本节点，应放行）', input: '<root><!--c--><a><![CDATA[ x < y ]]></a><b>hello   world</b></root>' },
  {
    id: 'a9-multiline-mixed',
    title: '多行混合内容（粘词对抗样本，压缩必须保留 This is / content. 的空格）',
    input: '<root>\n  <content>\n    <p>\n        This is <b>some</b> content.\n    </p>\n  </content>\n</root>',
  },
];

const rows = [];
for (const c of CASES) {
  let minify = null;
  let format = null;
  try {
    minify = safeMinify(c.input);
  } catch (e) {
    minify = { ok: false, output: null, error: `${e.name}: ${e.message}`, warnings: [] };
  }
  try {
    format = safeFormat(c.input);
  } catch (e) {
    format = { ok: false, output: null, error: `${e.name}: ${e.message}`, warnings: [] };
  }
  if (minify && minify.ok && minify.output) {
    fs.writeFileSync(path.join(__dirname, 'r6-out', `${c.id}.safemin.out.xml`), minify.output, 'utf8');
  }
  if (format && format.ok && format.output) {
    fs.writeFileSync(path.join(__dirname, 'r6-out', `${c.id}.safeformat.out.xml`), format.output, 'utf8');
  }
  rows.push({ id: c.id, title: c.title, input: c.input, minify, format });
  console.log(
    `[${c.id}] ${c.title}\n  minify : ${minify.ok ? JSON.stringify(minify.output) : '拒绝(' + (minify.error || minify.reason) + ') @ ' + JSON.stringify(minify.warnings)}\n  format : ${format.ok ? JSON.stringify(format.output) + (format.bomRestored ? '  (BOM 已补回)' : '') : '拒绝(' + (format.error || format.reason) + ') @ ' + JSON.stringify(format.warnings)}`
  );
}

fs.writeFileSync(path.join(__dirname, 'r6-safe-wrapper.json'), JSON.stringify(rows, null, 2), 'utf8');
console.log(`\n已写出：${path.join(__dirname, 'r6-safe-wrapper.json')}`);

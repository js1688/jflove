/**
 * R-6 补充实测（四）：filter 回调能否用来做"压缩前置预检"
 *
 * 目的：AC-33 要求"压缩只删元素间空白"。实测已确认库会删掉纯空白文本节点，
 * 那么能否在调用前用 filter 回调**发现**这类节点并中止（把"静默损坏"变成"显式告知"）？
 * 本脚本把 filter 收到的节点形状打印出来。
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

const INPUT = '<root>\n  <a> </a>\n  <b>y</b> <c>z</c>\n  <!--c--><d><![CDATA[q]]></d>\n</root>';

const seen = [];
const out = xmlFormat.minify(INPUT, {
  filter: (node) => {
    seen.push({
      type: node.type,
      name: node.name ?? null,
      keys: Object.keys(node),
      text: typeof node.content === 'string' ? node.content : null,
      attributes: node.attributes ?? null,
      // 只取可安全 JSON 化的字段
    });
    return true;
  },
});

// 用 filter 做"预检"的可行性：返回 false 会怎样？
const removed = xmlFormat.minify('<root><a>1</a><b>2</b></root>', {
  filter: (node) => node.type !== 'Text',
});

const payload = {
  input: INPUT,
  output: out,
  filter_calls: seen,
  filter_allows_text_node_type: seen.some((s) => s.type === 'Text'),
  whitespace_only_text_nodes: seen.filter((s) => s.type === 'Text' && s.text !== null && s.text.trim() === '').length,
  filter_returning_false_output: removed,
  verdict: '',
};
payload.verdict = payload.filter_allows_text_node_type
  ? 'filter 能枚举到 Text 节点 ⇒ 可在回调里做"含纯空白文本节点则中止"的前置预检'
  : 'filter 枚举不到 Text 节点 ⇒ 该预检方式不可行，需自研扫描';

fs.writeFileSync(path.join(__dirname, 'r6-filter-probe.json'), JSON.stringify(payload, null, 2), 'utf8');
console.log(JSON.stringify(payload, null, 2));
